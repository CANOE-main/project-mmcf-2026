"""
MMCF TF1, temporal representation: the TF3 reference run (coal phase-out + CER, perfect
foresight) under different sets of representative days.

    python tf1_rep_days.py prepare [--db COMPILED_DB]  # locally, inside project-mmcf-2026
    python tf1_rep_days.py list                        # runs and their Slurm resources
    python tf1_rep_days.py run tf1_selected12          # one run (what run_one.sh calls)
    python tf1_rep_days.py collect                     # runs/*/ -> results.csv
"""

import argparse
import calendar
import json
import os
import re
import resource
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import tomllib
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pandas as pd
from canoe.representative_periods.build import build_representative_periods
from canoe.representative_periods.clusterings import Clustering, TsamClustering, TsamCollection
from canoe.representative_periods.config import RepresentativePeriodsConfig
from canoe.temoa_protocol import CANOETemoaConfig, run_temoa
from loguru import logger

PKG_DIR = Path(__file__).resolve().parent
REPO_DIR = PKG_DIR.parent  # project-mmcf-2026, only needed by `prepare`
INPUT_DB = PKG_DIR / "db" / "tf1-ref.sqlite"  # full year, policies applied
CLUSTERINGS_DIR = PKG_DIR / "clusterings"  # precomputed rep day sets (tsam collections)
TIMESERIES_DIR = PKG_DIR / "assets" / "rep_periods_data" / "timeseries"
RUNS_DIR = PKG_DIR / "runs"
TRP_SRC_DB = Path("~/OneDrive/dev/canoe-data/processed/canoe-trp-v4.sqlite").expanduser()
WEATHER_YEAR = 2018  # year of the weather series and of the CODERS hourly demand


# ---------------------------------------------------------------------------
# Representative periods and Temoa configuration (same as tf3 / the toml)
# ---------------------------------------------------------------------------

def selected(n_periods: int) -> TsamClustering:
    """Our preferred method: configuration/canoe-full-pipeline.toml's clustering, n days."""
    return TsamClustering(
        data_dir=TIMESERIES_DIR,
        weather_year=WEATHER_YEAR,
        include=["*_temperature", "*_humidity", "*_wind_speed", "*_irradiance_surface"],
        pca_components=6,
        method="hierarchical",
        n_periods=n_periods,
    )


def precomputed(name: str, n_periods: int) -> TsamCollection:
    """A rep day set written by `prepare` into clusterings/<name>/."""
    return TsamCollection(collection=name, collections_dir=CLUSTERINGS_DIR, n_periods=n_periods)


DEMAND_PRESERVATION = "annual"
DSD_THRESHOLD = 0.01

TEMOA_CONFIG = CANOETemoaConfig(
    solver="gurobi",  # replaced per run with the thread count and log file
    scenario_mode="perfect_foresight",
    silent=False,
    save_duals=True,
    save_lp_file=False,
    extensions=["growth_rates", "carbon_markets"],  # carbon_markets needed by add_cer
)


# ---------------------------------------------------------------------------
# Experiment design (TF1 Experiment Design, Oct 16 2026, Test Design table)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Run:
    clustering: Clustering | None  # None: full year, no reduction
    mem: str  # Slurm --mem (Narval: base nodes 249G, large-memory nodes 2009G)
    time: str  # Slurm --time, kept at the top of Alliance's 3h/12h/24h/72h windows
    solver_options: dict[str, Any] | None = None  # on top of Temoa's Gurobi defaults


# Names without dashes: Temoa reserves them in scenario names
RUNS: dict[str, Run] = {
    # 1. 15th of each month, (near) equal weighting: each day goes to its month's 15th
    "tf1_midmonth12": Run(precomputed("midmonth", 12), "64G", "03:00:00"),
    # 2. Peak Canada demand day of each month (CODERS 2018), each day to its month's peak
    "tf1_peak12": Run(precomputed("peak", 12), "64G", "03:00:00"),
    # 3-7. Our preferred method
    "tf1_selected12": Run(selected(12), "64G", "03:00:00"),
    "tf1_selected24": Run(selected(24), "64G", "03:00:00"),
    "tf1_selected36": Run(selected(36), "125G", "12:00:00"),
    "tf1_selected60": Run(selected(60), "245G", "12:00:00"),
    "tf1_selected120": Run(selected(120), "245G", "24:00:00"),
    # 8. All 365 days in sequence
    "tf1_all365": Run(None, "1200G", "72:00:00"),
}


# ---------------------------------------------------------------------------
# prepare: input database and precomputed rep day sets (run locally)
# ---------------------------------------------------------------------------

def prepare(compiled_db: Path | None, trp_db: Path) -> None:
    # Policies live in project-mmcf-2026, not shipped to the HPC
    sys.path.insert(0, str(REPO_DIR / "mmcf_2026"))
    from policies.add_cer import add_cer
    from policies.add_coal_phaseout import add_coal_phaseout
    from policies.module_copy import run as module_cp_run

    ############
    # COMPILER #
    # ##########
    if compiled_db is None:
        from canoe.pipeline import CANOEPipelineConfig, run_compiler

        os.chdir(REPO_DIR)  # the toml's paths are relative to the repo
        with Path("configuration/canoe-full-pipeline.toml").open("rb") as f:
            compiler_config = CANOEPipelineConfig.model_validate(tomllib.load(f)).compiler
        assert compiler_config is not None
        run_compiler(compiler_config)
        compiled_db = REPO_DIR / compiler_config.base.db_output_dir
    else:
        logger.info(f"Skipping compiler, using {compiled_db}")

    INPUT_DB.parent.mkdir(parents=True, exist_ok=True)
    logger.info(f"Copying {compiled_db} -> {INPUT_DB}")
    shutil.copyfile(compiled_db, INPUT_DB)

    ###########################################
    # Post-process (same as tf3_myopic, ref) #
    # #########################################
    logger.info("Adding transportation")
    module_cp_run(str(trp_db), str(INPUT_DB), "TRP%", "data_set", ("T_*", "F_T_*"), version="003")
    with sqlite3.connect(INPUT_DB) as conn:
        conn.execute("DELETE FROM efficiency WHERE tech = 'E_T_ELC' AND data_id LIKE 'FUELHR%'")
    add_coal_phaseout(str(INPUT_DB))
    add_cer(str(INPUT_DB))

    ###############################
    # Rep day sets and their data #
    # #############################
    with sqlite3.connect(INPUT_DB) as conn:
        seasons = [s for (s,) in conn.execute("SELECT season FROM time_season ORDER BY sequence")]
    month = {s: day_date(s).month for s in seasons}

    # Run 1: the 15th of each month
    rep_of_month = {m: f"D{date(WEATHER_YEAR, m, 15).timetuple().tm_yday:03d}" for m in range(1, 13)}
    write_collection("midmonth", {s: rep_of_month[month[s]] for s in seasons},
                     "15th of each month; every day represented by its month's 15th")

    # Run 2: the day of each month with the highest hourly Canada demand
    from canoe.electricity.loaders import get_coders_provincial_demand

    hourly = get_coders_provincial_demand(WEATHER_YEAR).groupby("hour")["demand"].sum()
    daily_peak = hourly.groupby(hourly.index // 24).max()  # hours are local time per province
    daily_peak.index = [f"D{d + 1:03d}" for d in daily_peak.index]
    peak_of_month = daily_peak.groupby(daily_peak.index.map(month)).idxmax().to_dict()
    write_collection("peak", {s: peak_of_month[month[s]] for s in seasons},
                     f"Day of each month with the highest hourly Canada-wide demand (CODERS "
                     f"provincial demand {WEATHER_YEAR}, summed over provinces); every day "
                     "represented by its month's peak day")
    daily_peak.rename("canada_peak_mw").rename_axis("season").to_csv(CLUSTERINGS_DIR / "peak" / "daily_peak.csv")

    # Weather series for the selected runs
    logger.info(f"Copying weather series -> {TIMESERIES_DIR}")
    shutil.copytree(REPO_DIR / "assets/rep_periods_data/timeseries", TIMESERIES_DIR, dirs_exist_ok=True)
    logger.info(f"Ready: {INPUT_DB}, {CLUSTERINGS_DIR}, {TIMESERIES_DIR}")


def day_date(season: str) -> date:
    """Dnnn is day nnn of the year."""
    return date(WEATHER_YEAR, 1, 1) + timedelta(days=int(season[1:]) - 1)


def write_collection(name: str, representative_of: dict[str, str], description: str) -> None:
    folder = CLUSTERINGS_DIR / name
    folder.mkdir(parents=True, exist_ok=True)
    n = len(set(representative_of.values()))
    pd.DataFrame({"season": list(representative_of), str(n): list(representative_of.values())}).to_csv(
        folder / "assignments.csv", index=False
    )
    (folder / "clustering.toml").write_text(f'description = "{description}"\n')
    weights = pd.Series(representative_of).value_counts().sort_index()
    logger.info(f"{name}: {n} days, " + ", ".join(
        f"{s} ({calendar.month_abbr[day_date(s).month]} {day_date(s).day}, x{w})" for s, w in weights.items()
    ))


# ---------------------------------------------------------------------------
# run: rep periods -> Temoa, one run (one Slurm job)
# ---------------------------------------------------------------------------

def run(name: str, extra_solver_options: dict[str, Any], keep_db: bool) -> None:
    spec = RUNS[name]
    job_id = os.environ.get("SLURM_JOB_ID", "local")
    run_dir = RUNS_DIR / f"{name}_{job_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    # SQLite on Lustre is slow: work on the node-local disk, keep results in run_dir
    work_dir = Path(os.environ.get("SLURM_TMPDIR") or tempfile.mkdtemp())
    threads = int(os.environ.get("SLURM_CPUS_PER_TASK") or os.cpu_count() or 1)

    summary: dict[str, Any] = {
        "run": name, "job_id": job_id, "host": socket.gethostname(), "threads": threads,
        "mem_requested": spec.mem, "status": "started",
    }
    write_json(run_dir / "summary.json", summary)
    start = time.time()

    db = work_dir / f"{name}.sqlite"
    shutil.copyfile(INPUT_DB, db)

    ##########################
    # Representative periods #
    # ########################
    if spec.clustering is None:
        with sqlite3.connect(db) as conn:
            (days_per_period,) = conn.execute("SELECT COUNT(*) FROM time_season").fetchone()
        n_rep_days, time_sequencing = days_per_period, "consecutive_days"
    else:
        config = RepresentativePeriodsConfig(
            clustering=spec.clustering,
            demand_preservation=DEMAND_PRESERVATION,
            dsd_threshold=DSD_THRESHOLD,
        )
        # Days the rep days stand for: 363 for the weather series (Jan 2 - Dec 30), 365 for
        # the precomputed sets
        mapping = config.clustering.mapping()
        days_per_period = len(mapping.representative_of)
        n_rep_days = len(set(mapping.representative_of.values()))
        output = build_representative_periods(input_db=db, output_dir=work_dir / "reduced", config=config)
        db, time_sequencing = output.db_path, output.time_sequencing
        pd.Series(mapping.representative_of, name="representative").rename_axis("season").to_csv(
            run_dir / "rep_days.csv"
        )
    reduction_s = time.time() - start
    summary |= {"n_rep_days": n_rep_days, "days_per_period": days_per_period,
                "time_sequencing": time_sequencing, "reduction_s": round(reduction_s)}

    #########
    # TEMOA #
    # #######
    solver_options = {"Threads": threads, "LogFile": str(run_dir / "gurobi.log"),
                      **(spec.solver_options or {}), **extra_solver_options}
    temoa_config = TEMOA_CONFIG.model_copy(update={
        "scenario": name,
        "output_path": run_dir / "temoa-outputs",
        "time_sequencing": time_sequencing,
        "days_per_period": days_per_period,
        "solver": {"name": "gurobi", "options": solver_options},
    })
    summary |= {"solver_options": solver_options, "status": "solving"}
    write_json(run_dir / "summary.json", summary)
    try:
        run_temoa(db_path=db, config=temoa_config)
        summary["status"] = "finished"
    except Exception as e:
        summary["status"] = f"error: {e!r}"
        raise
    finally:
        summary |= {
            "temoa_s": round(time.time() - start - reduction_s),
            "total_s": round(time.time() - start),
            # Linux ru_maxrss is in KB; Gurobi may run in a child process
            "peak_rss_self_gb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**2, 2),
            "peak_rss_children_gb": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024**2, 2),
        }
        write_json(run_dir / "summary.json", summary)
        if keep_db:
            shutil.copyfile(db, run_dir / db.name)


def write_json(path: Path, data: dict[str, Any]) -> None:
    path.write_text(json.dumps(data, indent=2, default=str))


# ---------------------------------------------------------------------------
# collect: one row per run, for the TF1 computational statistics
# ---------------------------------------------------------------------------

GUROBI_PATTERNS = {
    "rows": r"Optimize a model with (\d+) rows",
    "columns": r"Optimize a model with \d+ rows, (\d+) columns",
    "nonzeros": r"Optimize a model with \d+ rows, \d+ columns and (\d+) nonzeros",
    "barrier_iterations": r"Barrier solved model in (\d+) iterations",
    "solve_s": r"(?:Barrier solved model|Solved) in \d+ iterations and ([\d.]+) seconds",
    "objective": r"Optimal objective\s+([-\d.e+]+)",
    "gurobi_status": r"(Optimal objective|Numerical trouble|Infeasible model|Time limit reached|"
                     r"Unbounded model|Infeasible or unbounded model|Sub-optimal termination)",
}


def collect() -> None:
    rows = []
    for summary_file in sorted(RUNS_DIR.glob("*/summary.json")):
        row = json.loads(summary_file.read_text())
        options = row.pop("solver_options", None) or {}
        row["extra_solver_options"] = ",".join(
            f"{k}={v}" for k, v in options.items() if k not in ("Threads", "LogFile")
        )
        log = summary_file.parent / "gurobi.log"
        text = log.read_text(errors="replace") if log.exists() else ""
        for key, pattern in GUROBI_PATTERNS.items():
            found = re.findall(pattern, text)
            row[key] = found[-1] if found else None  # last solve in the log
        row |= sacct(row["job_id"])
        rows.append(row)
    if not rows:
        print(f"No runs in {RUNS_DIR}")
        return
    df = pd.DataFrame(rows)
    df.to_csv(PKG_DIR / "results.csv", index=False)
    with pd.option_context("display.width", 250, "display.max_columns", None):
        print(df)
    print(f"\nWritten to {PKG_DIR / 'results.csv'}")


def sacct(job_id: str) -> dict[str, str | None]:
    """Slurm's view of the job (empty off the cluster)."""
    if shutil.which("sacct") is None or job_id == "local":
        return {}
    out = subprocess.run(["sacct", "-j", job_id, "-n", "-P", "--format=State,Elapsed,TotalCPU,MaxRSS"],
                         capture_output=True, text=True).stdout.splitlines()
    if not out:
        return {}
    state, elapsed, total_cpu, _ = out[0].split("|")
    maxrss = max((line.split("|")[3] for line in out if line.split("|")[3]), key=_bytes, default=None)
    return {"slurm_state": state, "slurm_elapsed": elapsed, "slurm_total_cpu": total_cpu,
            "slurm_maxrss": maxrss}


def _bytes(size: str) -> float:
    units = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}
    return float(size[:-1]) * units[size[-1]] if size[-1] in units else float(size)


# ---------------------------------------------------------------------------

def parse_option(text: str) -> tuple[str, Any]:
    key, value = text.split("=", 1)
    for cast in (int, float):
        try:
            return key, cast(value)
        except ValueError:
            pass
    return key, value


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare", help="build db/ and clusterings/ (locally, inside project-mmcf-2026)")
    p.add_argument("--db", type=Path, help="skip the compiler and start from this compiled database")
    p.add_argument("--trp-db", type=Path, default=TRP_SRC_DB)
    p = sub.add_parser("list", help="print 'name mem time' for each run (used by submit_all.sh)")
    p.add_argument("runs", nargs="*", help="default: all")
    p = sub.add_parser("run", help="one run")
    p.add_argument("name", choices=list(RUNS))
    p.add_argument("--solver-option", action="append", default=[], metavar="KEY=VALUE",
                   help="Gurobi option on top of the run's, repeatable (e.g. ScaleFlag=2)")
    p.add_argument("--no-keep-db", action="store_true", help="don't copy the solved database to runs/")
    sub.add_parser("collect", help="runs/*/ -> results.csv")
    args = parser.parse_args()

    match args.command:
        case "prepare":
            prepare(args.db, args.trp_db)
        case "list":
            unknown = set(args.runs) - set(RUNS)
            if unknown:
                parser.error(f"unknown runs {sorted(unknown)}; choose from {list(RUNS)}")
            for name, spec in RUNS.items():
                if not args.runs or name in args.runs:
                    print(name, spec.mem, spec.time)
        case "run":
            run(args.name, dict(parse_option(o) for o in args.solver_option), keep_db=not args.no_keep_db)
        case "collect":
            collect()

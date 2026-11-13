"""
Full-year validation of a TF1 run: the unsolved full-year database, with the run's
investments as minimum new capacity and its retirements fixed, solved over all 365 days.
What the model builds on top of the bounds (topup.csv) is what the run's plan was missing.

    python validation/validate.py list                     # solved runs to validate
    python validation/validate.py run runs/tf1_peak12_123/tf1_peak12_12d.sqlite
    python tf1_rep_days.py collect                         # validations land in runs/ too
"""

import argparse
import os
import resource
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

import pandas as pd
from canoe.temoa_protocol import run_temoa
from loguru import logger

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from tf1_rep_days import INPUT_DB, RUNS_DIR, TEMOA_CONFIG, parse_option, write_json  # noqa: E402

MEM, TIME = "1200G", "72:00:00"  # same as tf1_all365
ZERO_TOL = 1e-3  # capacities below this (absolute) are taken as zero
CLOSE_TOL = 1e-3  # relative: a run's value this close to an upper limit makes the limit an equality
NOTE = "TF1 validation"


# ---------------------------------------------------------------------------
# Fixing the run's decisions in the full-year database
# ---------------------------------------------------------------------------

def fix_decisions(solved_db: Path, db: Path) -> pd.DataFrame:
    """Write the bounds into `db`; returns the minimum new capacity per (region, tech, vintage)."""
    with sqlite3.connect(solved_db) as conn:
        scenarios = [s for (s,) in conn.execute("SELECT DISTINCT scenario FROM output_built_capacity")]
        if len(scenarios) != 1:
            raise ValueError(f"{solved_db}: expected one scenario, found {scenarios}")
        built = pd.read_sql("SELECT region, tech, vintage, capacity FROM output_built_capacity", conn)
        net = pd.read_sql("SELECT region, period, tech, capacity FROM output_net_capacity", conn)
    net = net.groupby(["region", "period", "tech"], as_index=False)["capacity"].sum()

    with sqlite3.connect(db) as conn:
        retirable = {t for (t,) in conn.execute("SELECT tech FROM technology WHERE retire = 1")}
        groups = pd.read_sql("SELECT group_name, tech FROM tech_group_member", conn)
        regions = [r for (r,) in conn.execute("SELECT region FROM region")]

        # Upper limits the run reaches become equalities (before adding our bounds)
        relax_close_limits(conn, "limit_new_capacity", "vintage", "new_cap",
                           built.rename(columns={"vintage": "key"}), groups, regions)
        relax_close_limits(conn, "limit_capacity", "period", "capacity",
                           net.rename(columns={"period": "key"}), groups, regions)

        # New capacity >= the run's; near-zero: no bound. Shaved by CLOSE_TOL so solver
        # noise in the run's solution can't make the bounds infeasible
        min_new = built[(built["capacity"] >= ZERO_TOL) & ~built["tech"].isin(retirable)].copy()
        min_new["new_cap"] = min_new["capacity"] * (1 - CLOSE_TOL)
        for r, t, v, c in min_new[["region", "tech", "vintage", "new_cap"]].itertuples(index=False):
            set_limit(conn, "limit_new_capacity", "vintage", "new_cap", r, t, v, "ge", c)

        # Retirable techs: capacity of every period fixed to the run's (within CLOSE_TOL),
        # so the validation retires what the run retired, no more and no less
        fixed = (
            net[net["tech"].isin(retirable)]
            .set_index(["region", "period", "tech"])["capacity"]
            .reindex(pd.MultiIndex.from_product(
                [regions, sorted(net["period"].unique()), sorted(retirable)],
                names=["region", "period", "tech"]), fill_value=0.0)
            .reset_index()
        )
        with_process = set(conn.execute("SELECT DISTINCT region, tech FROM efficiency"))
        fixed = fixed[[(r, t) in with_process for r, t in zip(fixed["region"], fixed["tech"])]]
        fixed.loc[fixed["capacity"] < ZERO_TOL, "capacity"] = 0.0
        for r, p, t, c in fixed[["region", "period", "tech", "capacity"]].itertuples(index=False):
            set_limit(conn, "limit_capacity", "period", "capacity", r, t, p, "le", c * (1 + CLOSE_TOL) + ZERO_TOL)
            if c > 0:
                set_limit(conn, "limit_capacity", "period", "capacity", r, t, p, "ge", c * (1 - CLOSE_TOL))

        # Results of the input database, if any
        for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' "
                                     "AND name LIKE 'output\\_%' ESCAPE '\\'").fetchall():
            conn.execute(f"DELETE FROM {table}")
    logger.info(f"{len(min_new)} minimum new capacities; capacity of {sorted(retirable)} fixed "
                f"in {len(fixed)} (region, period)")
    return min_new.rename(columns={"capacity": "run_capacity"})[["region", "tech", "vintage", "run_capacity", "new_cap"]]


def set_limit(conn: sqlite3.Connection, table: str, key: str, value: str,
              region: str, tech: str, key_value: int, op: str, limit: float) -> None:
    """
    Add a limit; one already on the same (region, tech, key, operator) is replaced if ours
    is tighter, kept otherwise (Temoa takes one value per index).
    """
    where = f"region = ? AND tech_or_group = ? AND {key} = ? AND operator = ?"
    params = (region, tech, int(key_value), op)
    existing = [v for (v,) in conn.execute(f"SELECT {value} FROM {table} WHERE {where}", params)]
    if existing:
        tighter = limit > max(existing) if op == "ge" else limit < min(existing)
        if not tighter:
            return
        conn.execute(f"DELETE FROM {table} WHERE {where}", params)
    conn.execute(f"INSERT INTO {table} (region, tech_or_group, {key}, operator, {value}, notes) "
                 "VALUES (?, ?, ?, ?, ?, ?)", (*params, limit, NOTE))


def relax_close_limits(conn: sqlite3.Connection, table: str, key: str, value: str,
                       run_values: pd.DataFrame, groups: pd.DataFrame, regions: list[str]) -> None:
    """
    Upper limits ('le') the run reaches (within CLOSE_TOL) become equalities at the limit:
    the bounds from the run could otherwise push past them by solver noise.
    `run_values` has region, key (vintage or period), tech, capacity.
    """
    limits = pd.read_sql(f"SELECT rowid, region, {key} AS key, tech_or_group, {value} AS limit_value "
                         f"FROM {table} WHERE operator = 'le'", conn)
    members = groups.groupby("group_name")["tech"].apply(set).to_dict()
    changed = []
    for row in limits.itertuples(index=False):
        techs = members.get(row.tech_or_group, {row.tech_or_group})
        limit_regions = regions if row.region == "global" else row.region.split("+")
        used = run_values.loc[
            run_values["tech"].isin(techs) & run_values["region"].isin(limit_regions)
            & (run_values["key"] == row.key), "capacity"
        ].sum()
        if used > 0 and used >= row.limit_value * (1 - CLOSE_TOL):
            changed.append(row)
            # Temoa's equality operator is 'e'. One already on the same index pins the value:
            # just drop the 'le'
            same = conn.execute(f"SELECT 1 FROM {table} WHERE region = ? AND tech_or_group = ? AND {key} = ? "
                                "AND operator = 'e'", (row.region, row.tech_or_group, row.key)).fetchone()
            if same:
                conn.execute(f"DELETE FROM {table} WHERE rowid = ?", (row.rowid,))
            else:
                conn.execute(f"UPDATE {table} SET operator = 'e', notes = ? WHERE rowid = ?",
                             (f"{NOTE}: le -> e, run at {used:.6g}", row.rowid))
    if changed:
        logger.info(f"{table}: {len(changed)} upper limits reached by the run made equalities: "
                    + ", ".join(f"{r.region}/{r.tech_or_group}/{r.key}" for r in changed[:20])
                    + (" ..." if len(changed) > 20 else ""))


# ---------------------------------------------------------------------------
# One validation (one Slurm job)
# ---------------------------------------------------------------------------

def run(solved_db: Path, extra_solver_options: dict[str, Any], keep_db: bool) -> None:
    source = solved_db.parent.name.rsplit("_", 1)[0]  # runs/<run>_<jobid>/
    name = f"val_{source}"
    job_id = os.environ.get("SLURM_JOB_ID", "local")
    run_dir = RUNS_DIR / f"{name}_{job_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    work_dir = Path(os.environ.get("SLURM_TMPDIR") or tempfile.mkdtemp())
    threads = int(os.environ.get("SLURM_CPUS_PER_TASK") or os.cpu_count() or 1)

    summary: dict[str, Any] = {"run": name, "validates": str(solved_db), "job_id": job_id,
                               "threads": threads, "mem_requested": MEM, "status": "started"}
    write_json(run_dir / "summary.json", summary)
    start = time.time()

    db = work_dir / f"{name}.sqlite"
    shutil.copyfile(INPUT_DB, db)
    min_new = fix_decisions(solved_db, db)
    min_new.to_csv(run_dir / "min_new_capacity.csv", index=False)
    with sqlite3.connect(db) as conn:
        (days,) = conn.execute("SELECT COUNT(*) FROM time_season").fetchone()
    summary |= {"n_rep_days": days, "days_per_period": days, "time_sequencing": "consecutive_days",
                "reduction_s": round(time.time() - start)}

    solver_options = {"Threads": threads, "LogFile": str(run_dir / "gurobi.log"), **extra_solver_options}
    temoa_config = TEMOA_CONFIG.model_copy(update={
        "scenario": name,
        "output_path": run_dir / "temoa-outputs",
        "time_sequencing": "consecutive_days",
        "days_per_period": days,
        "solver": {"name": "gurobi", "options": solver_options},
    })
    summary |= {"solver_options": solver_options, "status": "solving"}
    write_json(run_dir / "summary.json", summary)
    try:
        run_temoa(db_path=db, config=temoa_config)
        summary["status"] = "finished"
        write_topup(db, min_new, run_dir / "topup.csv")
    except Exception as e:
        summary["status"] = f"error: {e!r}"
        raise
    finally:
        summary |= {
            "temoa_s": round(time.time() - start - summary["reduction_s"]),
            "total_s": round(time.time() - start),
            "peak_rss_self_gb": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024**2, 2),
            "peak_rss_children_gb": round(resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss / 1024**2, 2),
        }
        write_json(run_dir / "summary.json", summary)
        if keep_db:
            shutil.copyfile(db, run_dir / db.name)


def write_topup(db: Path, min_new: pd.DataFrame, path: Path) -> None:
    """New capacity built beyond the run's: what its plan was missing for the full year."""
    with sqlite3.connect(db) as conn:
        built = pd.read_sql("SELECT region, sector, tech, vintage, capacity, units FROM output_built_capacity", conn)
    df = built.merge(min_new, on=["region", "tech", "vintage"], how="left").fillna({"run_capacity": 0.0})
    # Against the run's own (unshaved) capacity; within the tolerances it is solver noise
    df["topup"] = df["capacity"] - df["run_capacity"]
    noise = ZERO_TOL + 2 * CLOSE_TOL * df["run_capacity"]
    df[df["topup"] > noise].drop(columns="new_cap").to_csv(path, index=False)
    logger.info(f"Top-up written to {path}")


def solved_runs() -> list[Path]:
    """Solved databases of the reduced runs (tf1_all365 needs no validation)."""
    return sorted(p for p in RUNS_DIR.glob("tf1_*/tf1_*_*d.sqlite"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="print 'solved_db mem time' per solved reduced run (used by submit_validate.sh)")
    p = sub.add_parser("run", help="one validation")
    p.add_argument("solved_db", type=Path)
    p.add_argument("--solver-option", action="append", default=[], metavar="KEY=VALUE")
    p.add_argument("--no-keep-db", action="store_true")
    args = parser.parse_args()

    match args.command:
        case "list":
            for path in solved_runs():
                print(path, MEM, TIME)
        case "run":
            run(args.solved_db.resolve(), dict(parse_option(o) for o in args.solver_option),
                keep_db=not args.no_keep_db)

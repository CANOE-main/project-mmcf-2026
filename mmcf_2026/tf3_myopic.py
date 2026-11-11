import argparse
import itertools
import shutil
import sqlite3
import tomllib
from pathlib import Path
from typing import Literal

from canoe.pipeline import CANOEPipelineConfig, run_compiler
from canoe.representative_periods.build import build_representative_periods
from canoe.temoa_protocol import CANOETemoaConfig, run_temoa
from loguru import logger
from policies.add_cer import add_cer
from policies.add_coal_phaseout import add_coal_phaseout
from policies.add_early_load_growth import add_early_load_growth
from policies.add_late_techno_miracle import add_late_techno_miracle
from policies.module_copy import run as module_cp_run

CONFIG_FILE = Path("configuration/canoe-full-pipeline.toml")
WORKING_DIR = Path("canoe-pwd")  # freshly compiled databases only
EXPERIMENT_DIR = WORKING_DIR / "tf3"  # post-processed and optimized databases
TRP_SRC_DB = Path("~/OneDrive/dev/canoe-data/processed/canoe-trp-v4.sqlite").expanduser()
N_REP_PERIODS = 16  # overrides the toml's rep periods config

Scenario = Literal["ref", "elg", "ltm"]
SCENARIOS: tuple[Scenario, ...] = ("ref", "elg", "ltm")


# ---------------------------------------------------------------------------
# Temoa configuration (in-line, not from the toml), one per foresight
# ---------------------------------------------------------------------------

TEMOA_CONFIG = CANOETemoaConfig(
    solver="gurobi",
    scenario_mode="perfect_foresight",
    silent=False,
    save_duals=True,
    save_lp_file=False,
    extensions=["growth_rates", "carbon_markets"],  # carbon_markets needed by add_cer
)

FORESIGHTS: dict[str, CANOETemoaConfig] = {
    "pf": TEMOA_CONFIG,
    # Sees only the current period
    "myopic1": TEMOA_CONFIG.model_copy(
        update={"scenario_mode": "myopic", "myopic": {"view_depth": 1, "step_size": 1}}
    ),
    # Sees two periods, moves forward one at a time
    "myopic2": TEMOA_CONFIG.model_copy(
        update={"scenario_mode": "myopic", "myopic": {"view_depth": 2, "step_size": 1}}
    ),
}


# ---------------------------------------------------------------------------
# Pipeline: compiler -> post-processing -> rep periods -> Temoa
# ---------------------------------------------------------------------------

def tf3_run(
    existing_db: Path | None = None,
    scenario_name: str | None = None,
    scenario: Scenario = "ref",
    temoa_config: CANOETemoaConfig = TEMOA_CONFIG,
) -> Path:
    """Run one experiment; returns the compiled database used, to reuse in later runs."""
    with CONFIG_FILE.open("rb") as f:
        pipeline_config = CANOEPipelineConfig.model_validate(tomllib.load(f))

    ############
    # COMPILER #
    # ##########
    compiler_config = pipeline_config.compiler
    assert compiler_config is not None
    compiled_db = Path(compiler_config.base.db_output_dir)

    if existing_db is None:
        run_compiler(compiler_config)
        existing_db = compiled_db
    else:
        logger.info(f"Skipping compiler, using {existing_db}")

    # The compiled db stays clean in WORKING_DIR, the experiment works on a copy
    EXPERIMENT_DIR.mkdir(parents=True, exist_ok=True)
    db = EXPERIMENT_DIR / f"{scenario_name or existing_db.stem}.sqlite"
    if existing_db.resolve() == db.resolve():
        raise ValueError(f"--db must differ from the experiment database ({db})")
    logger.info(f"Copying {existing_db} -> {db}")
    shutil.copyfile(existing_db, db)

    ################
    # Post-process #
    # ##############
    logger.info("Adding transportation")
    module_cp_run(str(TRP_SRC_DB), str(db), "TRP%", "data_set", ("T_*", "F_T_*"), version="003")
    # The copy sweeps in the TRP db's own E_T_ELC (outputs T_elc), duplicating the compiler's.
    # Keep the compiler's: myopic mode fails on the duplicate efficiency rows
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM efficiency WHERE tech = 'E_T_ELC' AND data_id LIKE 'FUELHR%'")

    add_coal_phaseout(str(db))
    add_cer(str(db))

    # Scenario
    if scenario == "elg":
        add_early_load_growth(str(db))
    elif scenario == "ltm":
        add_late_techno_miracle(str(db))

    ####################################
    # Representative periods and TEMOA #
    # ##################################
    rep_periods_config = pipeline_config.representative_periods_config
    assert rep_periods_config is not None
    rep_periods_config.clustering.n_periods = N_REP_PERIODS
    rep_period_output = build_representative_periods(
        input_db=db,
        output_dir=EXPERIMENT_DIR,
        config=rep_periods_config,
    )
    run_temoa(db_path=rep_period_output.db_path, config=temoa_config)
    return existing_db


# ---------------------------------------------------------------------------
# Experiment design: every scenario under every foresight
# ---------------------------------------------------------------------------

def main(
    existing_db: Path | None = None,
    foresights: list[str] | None = None,
    scenarios: list[Scenario] | None = None,
) -> None:
    foresights = foresights or list(FORESIGHTS)
    scenarios = scenarios or list(SCENARIOS)
    for foresight, scenario in itertools.product(foresights, scenarios):
        name = f"tf3_{foresight}_{scenario}"  # no dashes: Temoa reserves them in scenario names
        logger.info(f"=== {name} ===")
        temoa_config = FORESIGHTS[foresight].model_copy(update={"scenario": name})
        # Compile once at most, later runs start from the compiled db
        existing_db = tf3_run(existing_db=existing_db, scenario_name=name, scenario=scenario, temoa_config=temoa_config)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, help="skip the compiler and start from this compiled database")
    parser.add_argument("--foresight", nargs="+", choices=list(FORESIGHTS), help="default: all")
    parser.add_argument("--scenario", nargs="+", choices=list(SCENARIOS), help="default: all")
    args = parser.parse_args()
    main(existing_db=args.db, foresights=args.foresight, scenarios=args.scenario)

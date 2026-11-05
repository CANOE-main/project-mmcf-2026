import argparse
import shutil
import tomllib
from pathlib import Path

import pandas as pd
from canoe.common.projections import CSVSeriesSource
from canoe.pipeline import CANOECompilerConfig, CANOEPipelineConfig, run_compiler
from canoe.representative_periods.build import build_representative_periods
from canoe.temoa_protocol import run_temoa
from loguru import logger
from scripts.add_building_codes import add_building_codes
from scripts.add_candu_forced_refurb import add_candu_forced_refurb
from scripts.add_carbon_price import add_carbon_price
from scripts.add_cer import add_cer
from scripts.add_coal_phaseout import add_coal_phaseout
from scripts.add_cogen_flex import add_cogen_flex
from scripts.add_data_centres import add_data_centres
from scripts.add_itcs import add_itcs
from scripts.add_methane import add_methane
from scripts.add_net_zero_cap import add_net_zero_cap
from scripts.add_qc_wci import add_qc_wci
from scripts.add_uc import add_uc
from scripts.module_copy import run as module_cp_run
from scripts.temoa_to_iamc import run as run_to_iamc

CONFIG_FILE = Path("configuration/canoe-full-pipeline.toml")
WORKING_DIR = Path("canoe-pwd")
TRP_SRC_DB = Path("~/OneDrive/dev/canoe-data/processed/canoe-trp-v4.sqlite").expanduser()
NET_ZERO_RESIDUAL_CO2E: float | None = None  # kt CO2e in the last period; EF2026 residual, TBD
QC_WCI_PRICE: float | None = None  # 2030 WCI price in 2020 C$/t (constant real), TBD
# Economy-wide carbon price in 2020 C$/t, constant or {year: price} (read at period end,
# interpolated). Quebec is excluded when QC_WCI_PRICE is set. None = no global price.
GLOBAL_CARBON_PRICE: float | dict[int, float] | None = 10_000
# Unit commitment with the policies: adds the UC data (add_uc) and enables Temoa's
# unit_commitment extension. Both go together, the extension needs add_uc's tables.
UNIT_COMMITMENT = False


# ---------------------------------------------------------------------------
# Changes from the default pipeline
# ---------------------------------------------------------------------------

def modify_compiler_config(compiler_config: CANOECompilerConfig) -> None:
    # Decided we won't include the CEF2026 prices
    # CEF 2026 oil and gas prices take priority over the configured sources
    # oil_gas_prices = CSVPriceSource(path=Path("data/cef2026_fuel_prices.csv"))
    # compiler_config.fuel.price_sources = [oil_gas_prices] + compiler_config.fuel.price_sources

    # CEF 2026 GDP, set per sector since sectors copy base.gdp when the config is loaded
    cef_gdp = CSVSeriesSource(path=Path("data/cef2026_gdp.csv"))
    for sector_config in compiler_config.sectors.values():
        if hasattr(sector_config, "gdp"):
            sector_config.gdp = cef_gdp


# ---------------------------------------------------------------------------
# Default pipeline: compiler -> post-processing -> rep periods -> Temoa
# ---------------------------------------------------------------------------

def main(
    existing_db: Path | None = None,
    nz_policies: bool = True,
    carbon_price: bool = True,
    scenario_name: str | None = None,
) -> None:
    with CONFIG_FILE.open("rb") as f:
        pipeline_config = CANOEPipelineConfig.model_validate(tomllib.load(f))

    ############
    # COMPILER #
    # ##########
    compiler_config = pipeline_config.compiler
    assert compiler_config is not None
    db = Path(compiler_config.base.db_output_dir)

    if existing_db is None:
        modify_compiler_config(compiler_config)
        run_compiler(compiler_config)
        if scenario_name:
            # Keep the compiler output clean for other scenarios (--db), work on a copy
            existing_db, db = db, db.with_stem(scenario_name)
            logger.info(f"Copying compiler output {existing_db} -> {db}")
            shutil.copyfile(existing_db, db)
    else:
        # Post-processing edits the database in place, so work on a copy
        if scenario_name:
            db = db.with_stem(scenario_name)
        if existing_db.resolve() == db.resolve():
            raise ValueError(f"--db must differ from the compiler output path ({db})")
        logger.info(f"Skipping compiler, copying {existing_db} -> {db}")
        shutil.copyfile(existing_db, db)

    ##########################################
    # Post-process (add TRP, scenarios, etc) #
    # ########################################
    logger.info("Adding transportation")
    trp_src_db = TRP_SRC_DB
    module_cp_run(str(trp_src_db), str(db), "TRP%", "data_set", ("T_*", "F_T_*"), version="003")

    # Post-hoc policies (Net-Zero)
    if carbon_price and GLOBAL_CARBON_PRICE is not None:
        add_carbon_price(str(db), GLOBAL_CARBON_PRICE, exclude=() if QC_WCI_PRICE is None else ('QC',))
    if nz_policies:
        add_building_codes(str(db))
        add_coal_phaseout(str(db))
        add_cogen_flex(str(db))
        add_candu_forced_refurb(str(db))
        if UNIT_COMMITMENT:
            add_uc(str(db), pd.read_csv("data/unit_commitment_data.csv"))
        add_cer(str(db))
        add_data_centres(str(db))
        add_itcs(str(db))
        add_methane(str(db))
        if QC_WCI_PRICE is None:
            logger.warning("QC_WCI_PRICE not set, running without Quebec carbon pricing")
        else:
            add_qc_wci(str(db), QC_WCI_PRICE)
        if NET_ZERO_RESIDUAL_CO2E is None:
            logger.warning("NET_ZERO_RESIDUAL_CO2E not set, running without the net-zero endpoint")
        else:
            add_net_zero_cap(str(db), NET_ZERO_RESIDUAL_CO2E)

    ####################################
    # Representative periods and TEMOA #
    # ##################################
    rep_periods_config = pipeline_config.representative_periods_config
    assert rep_periods_config is not None
    rep_period_output = build_representative_periods(
        input_db=db,
        output_dir=WORKING_DIR,
        config=rep_periods_config,
    )

    temoa_config = pipeline_config.temoa_config
    assert temoa_config is not None
    # The policy extensions follow nz_policies whatever the config says: without the
    # policies their tables (add_cer, add_uc) are missing and Temoa stops to ask
    policy_extensions = {"carbon_markets", "unit_commitment"}
    extensions = [e for e in temoa_config.extensions if e not in policy_extensions]
    if nz_policies:
        extensions.append("carbon_markets")
        if UNIT_COMMITMENT:
            extensions.append("unit_commitment")
    temoa_config.extensions = extensions
    run_temoa(db_path=rep_period_output.db_path, config=temoa_config)

    # Convert results to target iamc format for IDEA
    run_to_iamc(
        [("TF2-Electrification", rep_period_output.db_path)],
        output_dir=temoa_config.output_path,
    )



if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, help="skip the compiler and start from this compiled database")
    parser.add_argument("--nz-policies", action="store_true")
    parser.add_argument("--carbon-price", action="store_true", help="apply GLOBAL_CARBON_PRICE")
    parser.add_argument("--scenario-name", type=str, help="name of the scenario database (compiler output kept clean)")
    args = parser.parse_args()
    main(
        existing_db=args.db,
        nz_policies=args.nz_policies,
        carbon_price=args.carbon_price,
        scenario_name=args.scenario_name,
    )

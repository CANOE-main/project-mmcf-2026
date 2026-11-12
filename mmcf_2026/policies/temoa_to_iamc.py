"""Convert Temoa (v4 schema) results into the MMCF R3 TF2 IAMC reporting template.

Every quantity is aggregated over season and time of day, so the output does not depend on the number
of representative days. Periods are modelled at their end year, so period p is reported as the year
that follows it (2045 -> 2050).

Usage:
    python temoa_to_iamc.py --run TF2-Run1=canoe-net-zero_4d.sqlite [--run TF2-Run2=other.sqlite ...]
"""
import argparse
import json
import re
import shutil
import sqlite3
from pathlib import Path
from typing import NamedTuple

import openpyxl
import pandas as pd

TEMPLATE = Path(__file__).with_name("MMCF_R3_TF2_Reporting_Template_v3_21Oct2026.xlsx")
COLUMNS = ["model", "scenario", "region", "variable", "unit", "time", "value", "note"]

CANADA = "Canada"
REGIONS = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
    "NLLAB": "Newfoundland and Labrador", "NS": "Nova Scotia", "ON": "Ontario",
    "PEI": "Prince Edward Island", "QC": "Quebec", "SK": "Saskatchewan",
}  # Inter-regional exchange regions (e.g. "AB-BC") are not mapped and are dropped.

# Temoa sector -> template sector, for both final energy and combustion emissions.
END_USE_SECTORS = {
    "residential": "Buildings|Residential", "commercial": "Buildings|Commercial",
    "industry": "Industry", "agriculture": "Industry", "transportation": "Transportation",
}
EMISSION_SECTORS = {
    "residential": "Buildings", "commercial": "Buildings", "transportation": "Transportation",
    "industry": "Industry", "fuel": "Industry", "electricity": "Electricity",
}
OTHER_EMISSION_SECTORS = ("agriculture",)  # Reported as Emissions|GHG Total|Other
FUGITIVE_TECH = "OG_CH4"

# Delivered fuel commodities are "<sector letter>_<fuel>", e.g. R_ng; the fuel part maps to a template fuel.
FUEL_TYPES = {
    "elc": "Electricity",
    "ng": "Gas", "cng": "Gas", "lng": "Gas",
    "oil": "Oil", "lpg": "Oil", "dsl": "Oil", "gsl": "Oil", "hfo": "Oil", "mdo": "Oil", "jtf": "Oil",
    "ngl": "Oil", "pck": "Oil",
    "coal": "Coal", "coke": "Coal",
    "wood": "Biomass Solids",
    "eth": "Biomass Liquids", "rdsl": "Biomass Liquids", "spk": "Biomass Liquids",  # SPK is HEFA (bio-based)
    "h2": "Hydrogen", "h2_700": "Hydrogen",
}
DELIVERY_TECHS = ("F_*", "E_*")  # CANOE fuel and electricity delivery technologies, e.g. F_R_NG, E_R_ELC
HEATING = {"R_D_SPH": "Buildings|Residential", "C_D_SPH": "Buildings|Commercial"}

# Patterns of technologies whose output to the grid is not generation: T&D, interties, imports, storage.
NON_GENERATION = ("E_ELC_*", "E_INT*", "E_BAT*", "E_PUMP*")

VEHICLE_RULES = [("PHEV", "PHEV"), ("FCHEV|FCEV", "FCEV"), ("HEV", "HEV"), ("BEV", "BEV"), ("", "ICE")]
HEATING_RULES = [
    (r"WOOD|OTH|-ELC-|-OIL-", "Other"), ("NG_HP", "Gas Furnace or Boiler"), ("HP", "Heat Pump"),
    ("ELE?C", "Electric Resistance"), ("NG", "Gas Furnace or Boiler"), ("OIL", "Oil Furnace or Boiler"),
    ("", "Other"),
]
# Technology pattern -> (segment, ordered class rules where the first match wins, basis note).
SEGMENTS = [
    (r"^T_LDV_(C|LTP)_", "Transportation|Passenger", VEHICLE_RULES, "Number of vehicles"),
    (r"^T_(LDV_LTF|MDV_T|HDV_T)_", "Transportation|Freight", VEHICLE_RULES, "Number of vehicles"),
    (r"^R_SPH", "Buildings|Residential|Space Heating", HEATING_RULES, "Number of units"),
    (r"^C_SPH", "Buildings|Commercial|Space Heating", HEATING_RULES, "Heating capacity (PJ/y), not units"),
]

# Template cost line -> (representative technology, unit noun). Database costs are in M$ per k units.
COSTS = {
    "Buildings|Residential|Space Heating|Heat Pump": ("R_SPHC_AIR_HP-TYP-NEW", "unit"),
    "Buildings|Residential|Space Heating|Gas Furnace": ("R_SPH_NG_FRN-TYP-NEW", "unit"),
    "Transportation|Passenger|Light Duty|BEV": ("T_LDV_C_BEV300", "vehicle"),
    "Transportation|Passenger|Light Duty|ICE": ("T_LDV_C_GSL", "vehicle"),
    "Transportation|Freight|Heavy Duty|BEV": ("T_HDV_T_BEV", "vehicle"),
    "Transportation|Freight|Heavy Duty|ICE": ("T_HDV_T_DSL", "vehicle"),
}
PRICES = {"Residential|Electricity": "R_elc", "Residential|Gas": "R_ng"}
ACTIVITY = {"p": ("Passenger", "million pkm"), "t": ("Freight", "million tkm")}  # DB demands are in billions
GJ_PER_PJ = 1e6


def rows(df, variable, unit, note=None):
    return df.assign(variable=variable, unit=unit, note=note)[["region", "time", "variable", "unit", "value", "note"]]


def total(df, *by):
    return df.groupby(["region", "time", *by], as_index=False).value.sum()


def add_canada(df, how="sum"):
    keys = [c for c in df.columns if c not in ("region", "value")]
    national = df.groupby(keys, as_index=False).value.agg(how).assign(region=CANADA)
    return pd.concat([df, national], ignore_index=True)


def fuel_type(commodity):
    try:
        return FUEL_TYPES[commodity.split("_", 1)[1]]
    except KeyError:
        raise SystemExit(f"Delivered fuel {commodity!r} has no entry in FUEL_TYPES") from None


def classify(tech):
    for pattern, segment, rules, note in SEGMENTS:
        if re.search(pattern, tech):
            return segment, next(label for p, label in rules if re.search(p, tech)), note
    return None, None, None


def glob_any(column, patterns):
    return "(" + " OR ".join(f"{column} GLOB '{p}'" for p in patterns) + ")"


class Results:
    def __init__(self, path, scenario=None):
        self.con = sqlite3.connect(f"file:{Path(path).resolve().as_posix()}?mode=ro", uri=True)
        found = [r[0] for r in self.con.execute("SELECT DISTINCT scenario FROM output_objective")]
        if scenario is None and len(found) != 1:
            raise SystemExit(f"{path}: specify the database scenario, one of {found}")
        self.scenario = scenario or found[0]
        periods = [r[0] for r in self.con.execute("SELECT period FROM time_period WHERE flag = 'f' ORDER BY period")]
        self.year = dict(zip(periods, periods[1:]))  # period -> reported (end) year; the last period is a boundary
        self.first_period = periods[0]
        self.discount_rate = self.con.execute(
            "SELECT value FROM metadata_real WHERE element = 'global_discount_rate'").fetchone()[0]

    def query(self, sql, **params):
        params = {k: json.dumps(v) if isinstance(v, (list, tuple, dict)) else v for k, v in params.items()}
        df = pd.read_sql_query(sql, self.con, params={"scenario": self.scenario, **params})
        df["region"] = df.region.map(REGIONS)
        df["time"] = df.time.map(self.year)
        return df.dropna(subset=["region", "time"])

    def discount_factor(self, period):
        """Sum of discount factors over the period's years; Temoa duals are in these discounted terms."""
        start = period - self.first_period
        return sum((1 + self.discount_rate) ** -(start + k) for k in range(1, self.year[period] - period + 1))

    def duals(self, constraint, commodity):
        """Dual values of a balance constraint for one commodity, with the index split into columns."""
        found = self.con.execute(
            "SELECT constraint_name, dual FROM output_dual_variable WHERE scenario = ? AND constraint_name GLOB ?",
            (self.scenario, f"{constraint}[[]*,{commodity}]")).fetchall()
        index = [name[name.index("[") + 1:-1].split(",")[:-1] + [dual] for name, dual in found]
        keys = ["region", "period", "season", "tod"][:len(index[0]) - 1] if index else []
        return pd.DataFrame(index, columns=[*keys, "dual"]).astype({"period": int} if index else {})


def final_energy(res):
    # Consumption of delivered fuels (supply can exceed it), plus a zero row for every represented path
    # so that represented fuels report 0 rather than blank.
    delivered = add_canada(res.query(f"""
        WITH fuel AS (
            SELECT DISTINCT e.output_comm AS commodity, t.sector FROM efficiency AS e JOIN technology AS t USING (tech)
            WHERE t.sector IN (SELECT value FROM json_each(:sectors)) AND {glob_any('e.tech', DELIVERY_TECHS)}
        )
        SELECT region, time, fuel.sector, commodity, SUM(value) AS value FROM (
            SELECT region, period AS time, input_comm AS commodity, flow AS value
            FROM output_flow_in WHERE scenario = :scenario
            UNION ALL
            SELECT e.region, p.period, e.output_comm, 0 FROM efficiency AS e, time_period AS p WHERE p.flag = 'f'
        ) JOIN fuel USING (commodity)
        GROUP BY region, time, fuel.sector, commodity""", sectors=list(END_USE_SECTORS)))
    use = total(delivered.assign(sector=delivered.sector.map(END_USE_SECTORS),
                                 fuel=delivered.commodity.map(fuel_type)), "sector", "fuel")

    heating = add_canada(res.query("""
        SELECT region, time, commodity, demand, SUM(value) AS value FROM (
            SELECT region, period AS time, input_comm AS commodity, output_comm AS demand, flow AS value
            FROM output_flow_in WHERE scenario = :scenario
            UNION ALL
            SELECT e.region, p.period, e.input_comm, e.output_comm, 0 FROM efficiency AS e, time_period AS p
            WHERE p.flag = 'f'
        )
        WHERE demand IN (SELECT value FROM json_each(:demands))
        GROUP BY region, time, commodity, demand""", demands=list(HEATING)))
    heating = total(heating.assign(sector=heating.demand.map(HEATING), fuel=heating.commodity.map(fuel_type)),
                    "sector", "fuel")

    sectors = total(use, "sector")
    buildings = use[use.sector.str.startswith("Buildings")]
    by_fuel, energy = total(buildings, "fuel"), total(use, "fuel")
    return [
        rows(use, "Use Final|Sector|" + use.sector + "|" + use.fuel, "PJ"),
        rows(sectors, "Use Final|Sector|" + sectors.sector, "PJ"),
        rows(total(sectors), "Use Final", "PJ"),
        rows(heating, "Use Final|Sector|" + heating.sector + "|Heating|" + heating.fuel, "PJ"),
        rows(by_fuel, "Use Final|Sector|Buildings|" + by_fuel.fuel, "PJ"),
        rows(total(buildings), "Use Final|Sector|Buildings", "PJ"),
        rows(energy, "Use Final|Energy|" + energy.fuel, "PJ"),
    ]


def emissions(res):
    df = add_canada(res.query("""
        SELECT region, period AS time,
               CASE WHEN emission < 0 THEN 'CCS' WHEN tech = :fugitive THEN 'Fugitive' ELSE sector END AS source,
               SUM(emission) / 1000 AS value
        FROM output_emission
        WHERE scenario = :scenario AND emis_comm = 'co2e'
        GROUP BY region, period, source""", fugitive=FUGITIVE_TECH))
    gross = df[df.source != "CCS"]
    other = gross[gross.source.isin(OTHER_EMISSION_SECTORS)]
    combustion = gross[~gross.source.isin(["Fugitive", *OTHER_EMISSION_SECTORS])]
    combustion = total(combustion.assign(sector=combustion.source.map(EMISSION_SECTORS)), "sector")
    unit = "Mt CO2-equiv"
    return [
        rows(total(gross), "Emissions|GHG Total", unit, "Gross: excludes captured CO2"),
        rows(combustion, "Emissions|GHG Total|Combustion|Sector|" + combustion.sector, unit),
        rows(total(gross[gross.source == "Fugitive"]), "Emissions|GHG Total|Fugitive", unit),
        rows(total(other), "Emissions|GHG Total|Other", unit, "Agriculture"),
        rows(total(df[df.source == "CCS"]), "Carbon Sequestration|CCS", "Mt CO2", "Negative: stored"),
    ]


def technology_shares(res):
    built = res.query("""
        SELECT region, vintage AS time, tech, SUM(capacity) AS value FROM output_built_capacity
        WHERE scenario = :scenario GROUP BY region, vintage, tech""")
    stock = res.query("""
        SELECT region, period AS time, tech, SUM(capacity) AS value FROM output_net_capacity
        WHERE scenario = :scenario GROUP BY region, period, tech""")

    frames = []
    for capacity, measure in ((built, "Sales Share"), (stock, "Stock Share")):
        classes = pd.DataFrame([classify(t) for t in capacity.tech], columns=["segment", "kind", "note"],
                               index=capacity.index)
        capacity = add_canada(capacity.join(classes).dropna(subset=["segment"]).drop(columns="tech"))
        capacity = total(capacity, "segment", "kind", "note")
        segment_total = capacity.groupby(["region", "time", "segment"]).value.transform("sum")
        share = capacity.assign(value=100 * capacity.value / segment_total).dropna(subset=["value"])
        frames.append(rows(share, "Energy Service|" + share.segment + "|" + share.kind + "|" + measure, "%",
                           share.note))
    return frames


def capital_costs(res, currency):
    techs = {tech: name for name, (tech, _) in COSTS.items()}
    df = add_canada(res.query("""
        SELECT region, vintage AS time, tech, cost AS value FROM cost_invest
        WHERE tech IN (SELECT value FROM json_each(:techs))""", techs=list(techs)), how="mean")
    df = df.assign(name=df.tech.map(techs), value=df.value * 1000 * currency.factor)  # M$/k units -> $/unit
    unit = f"CAD${currency.year}/" + df.name.map(lambda n: COSTS[n][1])
    return [rows(df, "Cost|Capital|" + df.name, unit, "As used in the run; " + df.tech)]


def prices(res, currency):
    """Undiscounted duals of delivered-fuel balances in $/GJ, weighted by consumption."""
    frames = []
    for name, commodity in PRICES.items():
        duals = res.duals("commodity_balance_constraint", commodity)
        if duals.empty:
            duals = res.duals("annual_commodity_balance_constraint", commodity)
        if duals.empty:
            continue
        keys = [c for c in duals.columns if c != "dual"]
        flows = pd.read_sql_query(f"""
            SELECT {', '.join(keys)}, SUM(flow) AS flow FROM output_flow_in
            WHERE scenario = ? AND input_comm = ? GROUP BY {', '.join(keys)}""",
                                  res.con, params=(res.scenario, commodity))
        df = duals.merge(flows, on=keys).astype({"period": int})
        df = df[df.period.isin(res.year) & df.region.isin(REGIONS)]
        df = df.assign(region=df.region.map(REGIONS), time=df.period.map(res.year),
                       cost=df.dual / df.period.map(res.discount_factor) * df.flow)
        sums = df.groupby(["region", "time"], as_index=False)[["cost", "flow"]].sum()
        sums = pd.concat([sums, sums.groupby("time", as_index=False)[["cost", "flow"]].sum().assign(region=CANADA)])
        price = sums.assign(value=sums.cost / sums.flow * currency.factor)
        frames.append(rows(price, "Price|Use Final|" + name, f"CAD${currency.year}/GJ",
                           "Marginal cost from model duals; no carbon price applied"))
    return frames


def carbon_price(res, currency):
    """National shadow price of the emissions cap in $/t (M$/kt); zero if nothing prices carbon."""
    found = res.con.execute(
        "SELECT constraint_name, dual FROM output_dual_variable WHERE scenario = ? AND constraint_name GLOB ?",
        (res.scenario, "limit_emission_constraint[[]*,co2e,*]")).fetchall()
    priced = res.con.execute("SELECT COUNT(*) FROM cost_emission").fetchone()[0]
    if found:
        periods = [int(name[name.index("[") + 1:-1].split(",")[1]) for name, _ in found]
        value = {p: abs(dual) / res.discount_factor(p) * currency.factor for p, (_, dual) in zip(periods, found)
                 if p in res.year}
        note = "Shadow price of the emissions cap"
    elif priced:
        return []
    else:
        value = {p: 0.0 for p in res.year}
        note = "No emissions cap or carbon price in the run"
    df = pd.DataFrame({"region": CANADA, "time": [res.year[p] for p in value], "value": list(value.values())})
    return [rows(df, "Price|Carbon", f"CAD${currency.year}/tCO2e", note)]


def electricity_and_activity(res):
    generation = add_canada(res.query(f"""
        SELECT region, period AS time, SUM(flow) AS value FROM output_flow_out
        WHERE scenario = :scenario AND output_comm IN ('E_elc_tx', 'E_elc_dx')
          AND NOT {glob_any('tech', NON_GENERATION)}
        GROUP BY region, period"""))
    activity = add_canada(res.query("""
        SELECT region, period AS time, substr(output_comm, 5, 1) AS kind, SUM(flow) * 1000 AS value
        FROM output_flow_out
        WHERE scenario = :scenario AND (output_comm GLOB 'T_D_pkm*' OR output_comm GLOB 'T_D_tkm*')
        GROUP BY region, period, kind"""))
    mode = activity.kind.map(lambda k: ACTIVITY[k][0])
    unit = activity.kind.map(lambda k: ACTIVITY[k][1])
    return [
        rows(generation, "Production Secondary|Electricity", "PJ", "Excludes imports and storage discharge"),
        rows(activity, "Energy Service|Transportation|" + mode + "|Value", unit),
    ]


def convert(res, model, scenario, currency):
    frames = [*final_energy(res), *emissions(res), *technology_shares(res), *prices(res, currency),
              *carbon_price(res, currency),
              *capital_costs(res, currency), *electricity_and_activity(res)]
    iamc = pd.concat(frames, ignore_index=True).assign(model=model, scenario=scenario)
    iamc["time"] = iamc.time.astype(int)
    return iamc[COLUMNS].sort_values(COLUMNS[:6], ignore_index=True)


def validate(iamc, template):
    """Check names and units against the template's Variables tab; list core lines not reported."""
    spec = pd.read_excel(template, sheet_name="Variables").set_index("Variable")
    reported = iamc.drop_duplicates("variable").set_index("variable").unit
    unknown = reported.index.difference(spec.index)
    if not unknown.empty:
        raise SystemExit("Variables not in the template:\n  " + "\n  ".join(unknown))
    same_unit = reported.str.replace(r"\d{4}", "", regex=True) == spec.Unit[reported.index].str.replace(
        r"\d{4}", "", regex=True)
    for variable in reported.index[~same_unit]:
        print(f"Unit differs: {variable}: {reported[variable]} vs template {spec.Unit[variable]}")
    core = spec.index[spec.Priority.str.startswith("Core")]
    # "Core (one of three)": capital, levelized or ratio; EMH derives the ratio from capital costs.
    alternative = spec.Priority.str.contains("one of three")
    group = spec.index.to_series().str.replace(r"^Cost\|(Capital|Levelized)\|", "", regex=True)
    covered = set(group[spec.index.isin(reported.index)])
    core = core[~alternative[core] | (~group[core].isin(covered) & ~core.str.startswith("Cost|Ratio"))]
    missing = core.difference(reported.index)
    print(f"{len(core) - len(missing)} of {len(core)} core variables reported; left blank:")
    print("  " + "\n  ".join(missing))


def write_template(iamc, template, path):
    shutil.copyfile(template, path)
    book = openpyxl.load_workbook(path)
    sheet = book["data"]
    header = [cell.value for cell in sheet[1]]
    for record in iamc.assign(definition=None).to_dict("records"):
        sheet.append([record[column] for column in header])
    book.save(path)


class Currency(NamedTuple):
    year: int
    factor: float


def run_spec(text):
    scenario, _, path = text.partition("=")
    if not path:
        raise argparse.ArgumentTypeError("expected SCENARIO=DATABASE, e.g. TF2-Run1=run1.sqlite")
    return scenario, Path(path)


def run(runs, output_dir, model="CANOE", stage="prelim", template=TEMPLATE, db_scenario=None,
        currency=Currency(2020, 1.0)):
    iamc = pd.concat([convert(Results(db, db_scenario), model, scenario, currency) for scenario, db in runs],
                     ignore_index=True)
    validate(iamc, template)

    stem = f"MMCF_R3_TF2_{model}_{stage}"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    iamc.drop(columns="note").to_csv(output_dir / f"{stem}.csv", index=False)
    write_template(iamc, template, output_dir / f"{stem}.xlsx")
    print(f"Wrote {len(iamc):,} rows, {iamc.variable.nunique()} variables to {output_dir / stem}.csv/.xlsx")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--run", type=run_spec, action="append", required=True, metavar="SCENARIO=DATABASE",
                        help="Template scenario name and its results database; repeat for each run")
    parser.add_argument("--model", default="CANOE")
    parser.add_argument("--stage", default="prelim", choices=["prelim", "final"])
    parser.add_argument("--template", type=Path, default=TEMPLATE)
    parser.add_argument("--output-dir", type=Path, default=Path.cwd())
    parser.add_argument("--db-scenario", help="Scenario inside the databases (needed if one holds several)")
    parser.add_argument("--dollar-year", type=int, default=2020, help="Dollar year reported for costs and prices")
    parser.add_argument("--cpi-factor", type=float, default=1.0,
                        help="Multiplier from the database dollar year (2020) to --dollar-year")
    args = parser.parse_args()

    run(args.run, args.output_dir, args.model, args.stage, args.template, args.db_scenario,
        Currency(args.dollar_year, args.cpi_factor))


if __name__ == "__main__":
    main()

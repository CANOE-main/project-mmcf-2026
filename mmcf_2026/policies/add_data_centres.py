"""
Add the data centre load (MMCF TF2): 1.5 GW by 2030 and 3.5 GW by 2050, commercial sector.
CANOE grows commercial demand from GDP rather than following the EF2026 demand path, so
the load is added on top as a new electric-only demand with a flat profile (no
demand_specific_distribution rows, so Temoa spreads it by segment fraction).
"""

import numpy as np
from canoe.common import atomic_transaction

DEFAULT_LOAD_GW = {2030: 1.5, 2050: 3.5}


def add_data_centres(
    db: str,
    load_gw: dict[int, float] | None = None,
    period_step: int = 5,
    base_period: int = 2025,
    demand_comm: str = 'C_D_DC',
    tech: str = 'C_DC',
    input_comm: str = 'C_elc',
    weight_tech: str = 'C_OTH',
    weight_demand: str = 'C_D_OTH',
    pj_per_gw_year: float = 31.536,
) -> None:
    """
    Add a national data centre load, split across provinces by base-year commercial
    electricity use (`weight_demand` times the electricity share of `weight_tech`).

    :param db: Path to the SQLite database.
    :param load_gw: Average national load (GW) by year, interpolated linearly and held flat outside.
    :param period_step: Period length; each period reads the load at its end year (period_end).
    :param base_period: Period whose demand and input split weight the provinces.
    :param demand_comm: New demand commodity.
    :param tech: New tech serving it.
    :param input_comm: Electricity commodity the tech consumes.
    :param weight_tech: Tech whose electricity input share weights the provinces.
    :param weight_demand: Demand served by `weight_tech`.
    :param pj_per_gw_year: Energy of 1 GW over a year, in PJ.
    """
    if load_gw is None:
        load_gw = DEFAULT_LOAD_GW
    years, loads = zip(*sorted(load_gw.items()))

    with atomic_transaction(db) as conn:
        curs = conn.cursor()

        weights = dict(curs.execute(
            """
            SELECT d.region, d.demand * s.proportion
            FROM demand d
            JOIN limit_tech_input_split_annual s
                ON s.region = d.region AND s.period = d.period
            WHERE d.commodity = ? AND d.period = ? AND s.tech = ? AND s.input_comm = ?
            """,
            (weight_demand, base_period, weight_tech, input_comm),
        ).fetchall())
        total_weight = sum(weights.values())
        periods = [r[0] for r in curs.execute("SELECT period FROM time_period WHERE flag = 'f' ORDER BY period")][:-1]

        curs.execute("REPLACE INTO commodity_label(commodity) VALUES(?)", (demand_comm,))
        curs.execute(
            """
            REPLACE INTO commodity(name, flag, description, units, data_id)
            VALUES(?, 'd', 'demand for commercial `data centre` energy', 'PJ', 'COMHR003')
            """,
            (demand_comm,),
        )
        curs.execute("REPLACE INTO technology_label(tech) VALUES(?)", (tech,))
        curs.execute(
            """
            REPLACE INTO technology(tech, flag, sector, unlim_cap, annual, description, data_id)
            VALUES(?, 'p', 'commercial', 1, 1, 'data centres, electricity for Commercial sector', 'COMHR003')
            """,
            (tech,),
        )

        for region, weight in weights.items():
            data_id = f'COMHR{region}003'
            curs.execute(
                """
                REPLACE INTO efficiency(region, input_comm, tech, vintage, output_comm, efficiency, data_id)
                VALUES(?, ?, ?, ?, ?, 1, ?)
                """,
                (region, input_comm, tech, base_period, demand_comm, data_id),
            )
            for period in periods:
                national_gw = float(np.interp(period + period_step, years, loads))
                demand = national_gw * pj_per_gw_year * weight / total_weight
                curs.execute(
                    """
                    REPLACE INTO demand(region, period, commodity, demand, units, notes, data_id)
                    VALUES(?, ?, ?, ?, 'PJ', ?, ?)
                    """,
                    (
                        region, period, demand_comm, demand,
                        f'MMCF TF2 data centre load, {national_gw:.2f} GW national, '
                        f'{weight / total_weight:.1%} by base-year commercial electricity',
                        data_id,
                    ),
                )
        conn.commit()

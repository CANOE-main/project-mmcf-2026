"""
Early Load Growth (MMCF TF3 ELG): extra national baseload demand on top of the reference,
reaching 10 GW by 2035 and held there (the reference growth rate resumes after
2035, so the gap stays ~10 GW through 2050). Added as a new industrial electric-only
demand with a flat profile (no demand_specific_distribution rows, so Temoa spreads it by
segment fraction), like add_data_centres.
"""

import numpy as np
from canoe.common import atomic_transaction

# Read at period end (period 2025 = 2026-2030 reads 2030): 3 GW in 2025, the full 10 GW
# from 2030 on
DEFAULT_LOAD_GW = {2025: 0.0, 2030: 3.0, 2035: 10.0}


def add_early_load_growth(
    db: str,
    load_gw: dict[int, float] | None = None,
    shares: dict[str, float] | None = None,
    period_step: int = 5,
    base_period: int = 2025,
    demand_comm: str = 'I_D_ELG',
    tech: str = 'I_ELG',
    input_comm: str = 'I_elc',
    pj_per_gw_year: float = 31.536,
) -> None:
    """
    Add the ELG load, split across provinces by `shares`, or by base-year industrial
    electricity use if not given.

    :param db: Path to the SQLite database.
    :param load_gw: Extra average national load (GW) by year, interpolated linearly and held flat outside.
    :param shares: Provincial shares of the load (normalized); None = base-year industrial electricity use.
    :param period_step: Period length; each period reads the load at its end year (period_end).
    :param base_period: Period whose industrial electricity use weights the provinces.
    :param demand_comm: New demand commodity.
    :param tech: New tech serving it.
    :param input_comm: Electricity commodity the tech consumes.
    :param pj_per_gw_year: Energy of 1 GW over a year, in PJ.
    """
    if load_gw is None:
        load_gw = DEFAULT_LOAD_GW
    years, loads = zip(*sorted(load_gw.items()))

    with atomic_transaction(db) as conn:
        curs = conn.cursor()

        if shares is None:
            # Industrial electricity use: each demand times the electricity input share of its tech
            shares = dict(curs.execute(
                """
                SELECT s.region, SUM(d.demand * s.proportion)
                FROM limit_tech_input_split_annual s
                JOIN (SELECT DISTINCT region, tech, input_comm, output_comm FROM efficiency) e
                    ON e.region = s.region AND e.tech = s.tech AND e.input_comm = s.input_comm
                JOIN demand d
                    ON d.region = s.region AND d.period = s.period AND d.commodity = e.output_comm
                WHERE s.input_comm = ? AND s.period = ?
                GROUP BY s.region
                """,
                (input_comm, base_period),
            ).fetchall())
        total_share = sum(shares.values())
        periods = [r[0] for r in curs.execute("SELECT period FROM time_period WHERE flag = 'f' ORDER BY period")][:-1]

        curs.execute("REPLACE INTO commodity_label(commodity) VALUES(?)", (demand_comm,))
        curs.execute(
            """
            REPLACE INTO commodity(name, flag, description, units, data_id)
            VALUES(?, 'd', 'demand for early load growth baseload electricity (MMCF TF3 ELG)', 'PJ', 'INDHR003')
            """,
            (demand_comm,),
        )
        curs.execute("REPLACE INTO technology_label(tech) VALUES(?)", (tech,))
        curs.execute(
            """
            REPLACE INTO technology(tech, flag, sector, unlim_cap, annual, description, data_id)
            VALUES(?, 'p', 'industry', 1, 1, 'early load growth baseload, electricity for Industry sector', 'INDHR003')
            """,
            (tech,),
        )

        for region, share in shares.items():
            data_id = f'INDHR{region}003'
            curs.execute(
                """
                REPLACE INTO efficiency(region, input_comm, tech, vintage, output_comm, efficiency, data_id)
                VALUES(?, ?, ?, ?, ?, 1, ?)
                """,
                (region, input_comm, tech, base_period, demand_comm, data_id),
            )
            for period in periods:
                national_gw = float(np.interp(period + period_step, years, loads))
                demand = national_gw * pj_per_gw_year * share / total_share
                curs.execute(
                    """
                    REPLACE INTO demand(region, period, commodity, demand, units, notes, data_id)
                    VALUES(?, ?, ?, ?, 'PJ', ?, ?)
                    """,
                    (
                        region, period, demand_comm, demand,
                        f'MMCF TF3 ELG load, {national_gw:.2f} GW national, {share / total_share:.1%} of it',
                        data_id,
                    ),
                )
        conn.commit()

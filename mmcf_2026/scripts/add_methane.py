"""
Add the oil and gas methane regulations: a fixed dummy emitter for oil and gas methane,
cut 75% below 2012 by 2030 (0.8 Mt CH4, 22.4 Mt CO2e at AR5 GWP 28). Under period_end
every period stands for 2030 or later, so every period gets the 2030 value. It is fixed,
so it only matters for emissions accounting and the net-zero cap.
"""

from canoe.common import atomic_transaction


def add_methane(
    db: str,
    ch4_kt: float = 800,
    co2e_kt: float = 22400,
    region: str = 'AB',
    source_comm: str = 'OG_ethos',
    demand_comm: str = 'OG_D_CH4',
    tech: str = 'OG_CH4',
    vintage: int = 2025,
    data_id: str = 'FUELHRAB003',
    sector_data_id: str = 'FUELHR003',
) -> None:
    """
    Add a dummy tech meeting a 1 PJ dummy demand, with ch4/co2e emission factors equal
    to the yearly oil and gas methane emissions.

    :param db: Path to the SQLite database.
    :param ch4_kt: Yearly oil and gas methane, kt CH4.
    :param co2e_kt: The same in kt CO2e.
    :param region: Region the emissions are placed in.
    :param source_comm: Dummy source commodity.
    :param demand_comm: Dummy demand commodity.
    :param tech: Dummy emitter tech.
    :param vintage: Vintage of the emitter (its default lifetime covers the horizon).
    :param data_id: data_id of the regional rows.
    :param sector_data_id: data_id of the commodity and technology definitions.
    """
    with atomic_transaction(db) as conn:
        curs = conn.cursor()
        periods = [r[0] for r in curs.execute("SELECT period FROM time_period WHERE flag = 'f' ORDER BY period")][:-1]

        for comm, flag, description in (
            (source_comm, 's', 'dummy input - oil and gas methane'),
            (demand_comm, 'd', 'dummy demand - oil and gas methane'),
        ):
            curs.execute("REPLACE INTO commodity_label(commodity) VALUES(?)", (comm,))
            curs.execute(
                "REPLACE INTO commodity(name, flag, description, units, data_id) VALUES(?, ?, ?, 'PJ', ?)",
                (comm, flag, description, sector_data_id),
            )
        curs.execute("REPLACE INTO technology_label(tech) VALUES(?)", (tech,))
        curs.execute(
            """
            REPLACE INTO technology(tech, flag, sector, unlim_cap, annual, description, data_id)
            VALUES(?, 'p', 'fuel', 1, 1, 'dummy oil and gas methane emitter (methane regulations)', ?)
            """,
            (tech, sector_data_id),
        )
        curs.execute(
            """
            REPLACE INTO efficiency(region, input_comm, tech, vintage, output_comm, efficiency, data_id)
            VALUES(?, ?, ?, ?, ?, 1, ?)
            """,
            (region, source_comm, tech, vintage, demand_comm, data_id),
        )
        for emis_comm, value, units in (('ch4', ch4_kt, 'kt/PJ'), ('co2e', co2e_kt, 'ktCO2e/PJ')):
            curs.execute(
                """
                REPLACE INTO emission_activity(region, emis_comm, input_comm, tech, vintage, output_comm, activity, units, notes, data_id)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, 'oil and gas methane, 75% below 2012 by 2030', ?)
                """,
                (region, emis_comm, source_comm, tech, vintage, demand_comm, value, units, data_id),
            )
        for period in periods:
            curs.execute(
                """
                REPLACE INTO demand(region, period, commodity, demand, units, notes, data_id)
                VALUES(?, ?, ?, 1, 'PJ', 'dummy demand for the oil and gas methane emitter', ?)
                """,
                (region, period, demand_comm, data_id),
            )
        conn.commit()

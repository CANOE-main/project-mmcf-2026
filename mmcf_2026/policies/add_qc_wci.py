"""
Add Quebec carbon pricing (MMCF TF2): the WCI cap-and-trade continues past 2030 with
the 2030 price held constant in real terms through 2050. WCI covers fuel distributors,
so the price is applied to all Quebec CO2e. Under period_end every period stands for
2030 or later, so the same price applies to every period.
"""

from canoe.common import atomic_transaction


def add_qc_wci(
    db: str,
    price: float,
    region: str = 'QC',
    emis_comm: str = 'co2e',
    notes: str = 'Quebec WCI, 2030 price held constant (real)',
    data_id: str = 'ELCHRQC003',
) -> None:
    """
    Price Quebec emissions in every model period.

    :param db: Path to the SQLite database.
    :param price: Carbon price in model dollars (2020 C$) per tonne, already converted.
    :param region: Region priced.
    :param emis_comm: Emission commodity priced.
    :param notes: Notes stored on the cost_emission rows.
    :param data_id: data_id stored on the cost_emission rows.
    """
    with atomic_transaction(db) as conn:
        curs = conn.cursor()
        periods = [r[0] for r in curs.execute("SELECT period FROM time_period WHERE flag = 'f' ORDER BY period")][:-1]
        for period in periods:
            curs.execute(
                """
                REPLACE INTO cost_emission(region, period, emis_comm, cost, units, notes, data_id)
                VALUES(?, ?, ?, ?, 'M$/kt', ?, ?)
                """,
                (region, period, emis_comm, price / 1000, notes, data_id),  # $/t to M$/kt
            )
        conn.commit()

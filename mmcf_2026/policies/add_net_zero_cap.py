"""
Add the net-zero endpoint: a cap on national emissions in the last model period only
(MMCF TF2: emissions trajectory endogenous, only the 2050 endpoint is set).
CANOE has no negative-emission technologies, so the cap is the residual emissions of
the EF2026 Canada Net-zero scenario for the sectors CANOE covers, not zero.
"""

from canoe.common import atomic_transaction


def add_net_zero_cap(
    db: str,
    residual_emissions: float,
    period: int = 2045,
    emis_comm: str = 'co2e',
    region: str = 'global',
    units: str = 'ktCO2e',
    notes: str = 'Net-zero endpoint: EF2026 Canada Net-zero residual emissions',
    data_id: str = 'ELCHR003',
) -> None:
    """
    Cap emissions in the last model period.

    :param db: Path to the SQLite database.
    :param residual_emissions: Cap on annual emissions, in `units`.
    :param period: Period capped (the last model period, representing 2050).
    :param emis_comm: Emission commodity capped.
    :param region: Region of the cap ('global' applies system-wide).
    :param units: Units of `residual_emissions`.
    :param notes: Notes stored on the limit_emission row.
    :param data_id: data_id stored on the limit_emission row.
    """
    with atomic_transaction(db) as conn:
        conn.execute(
            """
            REPLACE INTO
            limit_emission(region, period, emis_comm, operator, value, units, notes, data_id)
            VALUES(?, ?, ?, 'le', ?, ?, ?, ?)
            """,
            (region, period, emis_comm, residual_emissions, units, notes, data_id)
        )
        conn.commit()

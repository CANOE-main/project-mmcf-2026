"""
Add coal power phaseout by 2030
"""

import pandas as pd
from canoe.common import atomic_transaction


def add_coal_phaseout(
    db: str,
    tech: str = 'E_COAL-EXS',
    periods: range = range(2030, 2046, 5),
    region: str = 'global',
    notes: str = 'Coal power phaseout',
    data_id: str = 'ELCHR003',
) -> None:
    """
    Add coal power phaseout.

    :param db: Path to the SQLite database.
    :param tech: Technology to phase out (also made retirable).
    :param periods: Periods where the tech's activity is limited to 0.
    :param region: Region of the activity limit ('global' applies system-wide).
    :param notes: Notes stored on the limit_activity rows.
    :param data_id: data_id stored on the limit_activity rows.
    """
    with atomic_transaction(db) as conn:
        curs = conn.cursor()

        curs.execute(
            """
            UPDATE technology SET retire = 1 WHERE tech == ?
            """,
            (tech,)
        )
        for period in periods:
            curs.execute(
                """
                REPLACE INTO
                limit_capacity(region, period, tech_or_group, operator, capacity, units, notes, data_id)
                VALUES(?, ?, ?, 'le', 0, 'PJ', ?, ?)
                """,
                (region, period, tech, notes, data_id)
            )
        conn.commit()


if __name__ == "__main__":
    add_coal_phaseout("canoe-pwd/canoe.sqlite")

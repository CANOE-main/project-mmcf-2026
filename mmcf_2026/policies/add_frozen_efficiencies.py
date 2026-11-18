"""
Hold end-use equipment efficiencies at their first model vintage (MMCF TF2 data book,
3 Nov 2026: "equipment efficiencies are held at 2024 values this round", no efficiency
learning). Every efficiency row of a future vintage is set to the value of the earliest
future vintage (2025, the closest to 2024) of the same region, input, tech and output.

Only end-use sectors are touched (transportation, residential, commercial by default).
Electricity techs are left alone: their vintage differences (CCS retrofits, new gas
plants) are not end-use equipment learning.
"""

from collections.abc import Sequence

from canoe.common import atomic_transaction
from loguru import logger

DEFAULT_PATTERNS = ('T_*', 'R_*', 'C_*')


def add_frozen_efficiencies(
    db: str,
    patterns: Sequence[str] = DEFAULT_PATTERNS,
    marker: str = 'efficiency held at first model vintage',
) -> None:
    """
    Set each future-vintage efficiency to that of the earliest future vintage.

    :param db: Path to the SQLite database.
    :param patterns: Tech GLOB patterns frozen.
    :param marker: Text appended to the notes of the changed rows.
    """
    with atomic_transaction(db) as conn:
        curs = conn.cursor()
        first_period = curs.execute("SELECT MIN(period) FROM time_period WHERE flag = 'f'").fetchone()[0]
        changed = 0
        for pattern in patterns:
            curs.execute(
                """
                UPDATE efficiency AS e
                SET efficiency = f.efficiency,
                    notes = COALESCE(e.notes || '; ', '') || ?
                FROM (
                    SELECT region, input_comm, tech, output_comm, efficiency
                    FROM efficiency AS x
                    WHERE tech GLOB ? AND vintage = (
                        SELECT MIN(vintage) FROM efficiency AS y
                        WHERE y.region = x.region AND y.input_comm = x.input_comm
                          AND y.tech = x.tech AND y.output_comm = x.output_comm AND y.vintage >= ?
                    )
                ) AS f
                WHERE e.tech GLOB ? AND e.vintage >= ?
                  AND e.region = f.region AND e.input_comm = f.input_comm
                  AND e.tech = f.tech AND e.output_comm = f.output_comm
                  AND e.efficiency != f.efficiency
                """,
                (marker, pattern, first_period, pattern, first_period),
            )
            changed += curs.rowcount
        logger.info(f"Efficiencies held at the {first_period} vintage: {changed} rows changed")
        conn.commit()

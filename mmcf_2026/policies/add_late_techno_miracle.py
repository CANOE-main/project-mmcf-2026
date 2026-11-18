"""
Late Techno-Miracle (MMCF TF3 LTM): an unexpected breakthrough in 2035 makes solar PV and
batteries progressively cheaper, reaching `final_factor` x the reference investment cost
by 2050. The factor is 1 up to 2035 and falls linearly to 2050; each vintage takes the
factor of its period end year (period_end, like the demands), so vintage 2045 gets the
2050 factor. Only investment costs are scaled.
"""

import numpy as np
from canoe.common import atomic_transaction
from loguru import logger

# LIKE patterns of the techs made cheaper
DEFAULT_TECH_PATTERNS = ('E_SOL_PV-NEW%', 'E_BAT_%-NEW')


def add_late_techno_miracle(
    db: str,
    final_factor: float = 0.45,
    tech_patterns: tuple[str, ...] = DEFAULT_TECH_PATTERNS,
    breakthrough_year: int = 2035,
    final_year: int = 2050,
    period_step: int = 5,
) -> None:
    """
    Scale the investment costs of the LTM techs.

    :param db: Path to the SQLite database.
    :param final_factor: Cost multiplier reached in `final_year` (under 0.5 per the TF3 design).
    :param tech_patterns: LIKE patterns of the techs scaled.
    :param breakthrough_year: Year of the breakthrough; factor is 1 up to it.
    :param final_year: Year the factor reaches `final_factor`, held after.
    :param period_step: Period length; each vintage reads the factor at its end year.
    """
    with atomic_transaction(db) as conn:
        curs = conn.cursor()
        tech_filter = ' OR '.join('tech LIKE ?' for _ in tech_patterns)
        vintages = [r[0] for r in curs.execute(
            f"SELECT DISTINCT vintage FROM cost_invest WHERE ({tech_filter}) ORDER BY vintage",
            tech_patterns,
        )]
        for vintage in vintages:
            factor = float(np.interp(vintage + period_step, (breakthrough_year, final_year), (1.0, final_factor)))
            if factor == 1.0:
                continue
            n = curs.execute(
                f"""
                UPDATE cost_invest
                SET cost = cost * ?,
                    notes = COALESCE(notes || '; ', '') || ?
                WHERE vintage = ? AND ({tech_filter})
                """,
                (factor, f'MMCF TF3 LTM: x{factor:.3f}', vintage, *tech_patterns),
            ).rowcount
            logger.info(f"LTM: vintage {vintage} investment costs x{factor:.3f} ({n} rows)")
        conn.commit()

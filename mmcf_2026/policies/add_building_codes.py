"""
Add building codes
- No new gas space heating capacity from 2035: not implemented, excluded by the MMCF TF2 study design
- Shell efficiency: space heating and cooling demand falls linearly from 2025 to 2050
  (residential 50%, commercial 43%, CER Canada Net-zero)
"""

from collections.abc import Mapping

from canoe.common import atomic_transaction

DEFAULT_BASE_YEAR = 2025
DEFAULT_END_YEAR = 2050
DEFAULT_MARKER = "building code shell efficiency"
DEFAULT_HEATING_COOLING_DEMANDS = {
    "R_D_SPH": 0.50,
    "R_D_SPC": 0.50,
    "C_D_SPH": 0.43,
    "C_D_SPC": 0.43,
}


def add_building_codes(
    db: str,
    demand_improvements: Mapping[str, float] | None = None,
    base_year: int = DEFAULT_BASE_YEAR,
    end_year: int = DEFAULT_END_YEAR,
    marker: str = DEFAULT_MARKER,
    period_offset: int = 5,
) -> None:
    """
    Apply building-code shell-efficiency reductions to demand in a Canoe database.

    Each period is read at ``year = period + period_offset`` (CANOE's period_end
    convention, so the last period stands for ``end_year``), and its demand is scaled by
    ``1 - improvement * (year - base_year) / (end_year - base_year)``, i.e. a linear
    decline reaching ``improvement`` at ``end_year``.

    Rows already tagged with ``marker`` in their notes are skipped, so the
    function is safe to run more than once on the same database.

    :param db: Path to the SQLite database.
    :param demand_improvements: Mapping of demand commodity -> fractional reduction
        at ``end_year``. Defaults to ``DEFAULT_HEATING_COOLING_DEMANDS``.
    :param base_year: Year with no reduction applied.
    :param end_year: Year at which the full reduction is reached.
    :param marker: Text appended to ``notes`` to tag modified rows.
    :param period_offset: Years from a period's label to the year its demand stands for
        (5 for period_end, 0 for period_start).
    """
    if demand_improvements is None:
        demand_improvements = DEFAULT_HEATING_COOLING_DEMANDS
    if end_year == base_year:
        raise ValueError("end_year must differ from base_year")

    with atomic_transaction(db) as conn:
        curs = conn.cursor()

        for commodity, improvement in demand_improvements.items():
            for (period,) in curs.execute(
                "SELECT DISTINCT period FROM demand WHERE commodity = ? AND period + ? > ?",
                (commodity, period_offset, base_year),
            ).fetchall():
                year = min(period + period_offset, end_year)
                factor = 1 - improvement * (year - base_year) / (end_year - base_year)
                curs.execute(
                    """
                    UPDATE demand
                    SET demand = demand * ?, notes = COALESCE(notes || '; ', '') || ?
                    WHERE commodity = ? AND period = ? AND COALESCE(notes, '') NOT LIKE ?
                    """,
                    (factor, marker, commodity, period, f"%{marker}%"),
                )
        conn.commit()


if __name__ == "__main__":
    add_building_codes("C:\\Users\\David\\Downloads\\canoe-3.1-4-all-high_16d.sqlite")

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
) -> None:
    """
    Apply building-code shell-efficiency reductions to demand in a Canoe database.

    For each commodity, demand in periods after ``base_year`` is scaled by
    ``1 - improvement * (period - base_year) / (end_year - base_year)``, i.e. a
    linear decline reaching ``improvement`` at ``end_year``.

    Rows already tagged with ``marker`` in their notes are skipped, so the
    function is safe to run more than once on the same database.

    :param db: Path to the SQLite database.
    :param demand_improvements: Mapping of demand commodity -> fractional reduction
        at ``end_year``. Defaults to ``DEFAULT_HEATING_COOLING_DEMANDS``.
    :param base_year: Year with no reduction applied.
    :param end_year: Year at which the full reduction is reached.
    :param marker: Text appended to ``notes`` to tag modified rows.
    """
    if demand_improvements is None:
        demand_improvements = DEFAULT_HEATING_COOLING_DEMANDS
    if end_year == base_year:
        raise ValueError("end_year must differ from base_year")

    with atomic_transaction(db) as conn:
        curs = conn.cursor()

        for commodity, improvement in demand_improvements.items():
            for (period,) in curs.execute(
                "SELECT DISTINCT period FROM demand WHERE commodity = ? AND period > ?",
                (commodity, base_year),
            ).fetchall():
                factor = 1 - improvement * (period - base_year) / (end_year - base_year)
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

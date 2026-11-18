"""
Add the Clean Economy Investment Tax Credits as reductions to investment cost.
- Clean Technology ITC: 30% to 2033 (15% in 2034, then 0)
- Clean Electricity ITC: 15% to 2034, not stackable with Clean Technology
Applied to vintages 2025 and 2030 (builds 2025-2034). Checked against EF2026 Appendix 1.
"""

from canoe.common import atomic_transaction

# (name, rate, tech GLOB patterns)
DEFAULT_CREDITS = [
    ('Clean Technology ITC', 0.30, [
        'E_SOL_PV-NEW-*',
        'E_WND_ON-NEW-*',
        'E_NUC_SMR-NEW',
        'E_BIO_M-NEW',
        'E_BAT_*-NEW',
        'C_SPHC_HP_*',  # business-use heat pumps; residential not eligible
    ]),
    ('Clean Electricity ITC', 0.15, [
        'E_NG_CCS-NEW',
        'E_NG_CCS_RFIT_*',
        'E_NUC_PWR-NEW',
    ]),
]
DEFAULT_VINTAGES = (2025, 2030)


def add_itcs(
    db: str,
    credits: list[tuple[str, float, list[str]]] | None = None,
    vintages: tuple[int, ...] = DEFAULT_VINTAGES,
) -> None:
    """
    Reduce cost_invest by each credit's rate for the matching techs and vintages.
    Rows already noted with the credit's name are skipped, so it is safe to rerun.

    :param db: Path to the SQLite database.
    :param credits: (name, rate, tech GLOB patterns) per credit; a tech should match one credit only.
    :param vintages: Vintages that receive the credits.
    """
    if credits is None:
        credits = DEFAULT_CREDITS

    with atomic_transaction(db) as conn:
        curs = conn.cursor()
        for name, rate, patterns in credits:
            for pattern in patterns:
                for vintage in vintages:
                    curs.execute(
                        """
                        UPDATE cost_invest
                        SET cost = cost * ?, notes = COALESCE(notes || '; ', '') || ?
                        WHERE tech GLOB ? AND vintage = ? AND COALESCE(notes, '') NOT LIKE ?
                        """,
                        (1 - rate, f'{name} {rate:.0%}', pattern, vintage, f'%{name}%'),
                    )
        conn.commit()

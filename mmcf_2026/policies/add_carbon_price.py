"""
Add an economy-wide carbon price: `cost_emission` on co2e in every region and model
period. The price is either a constant or a {year: price} trajectory, read at each
period's end year (period_end) and interpolated linearly, held flat outside the years
given. Temoa does no dollar-year conversion, so prices go in already in 2020 C$/t.

Regions in `exclude` are left unpriced. Use it for Quebec when the WCI price is applied
(`add_qc_wci`); otherwise Quebec would pay both, because the two use different data_ids
and so both rows are kept.
"""

from collections.abc import Iterable, Mapping

import numpy as np
from canoe.common import atomic_transaction


def add_carbon_price(
    db: str,
    price: float | Mapping[int, float],
    exclude: Iterable[str] = (),
    emis_comm: str = 'co2e',
    period_offset: int = 5,
    notes: str = 'Economy-wide carbon price',
) -> None:
    """
    Price emissions in every region and model period.

    :param db: Path to the SQLite database.
    :param price: 2020 C$ per tonne, constant or by year (interpolated, flat outside).
    :param exclude: Regions left unpriced (e.g. 'QC' when the WCI price is applied).
    :param emis_comm: Emission commodity priced.
    :param period_offset: Years from a period's label to the year it stands for
        (5 for period_end, 0 for period_start).
    :param notes: Notes stored on the cost_emission rows.
    """
    if isinstance(price, Mapping):
        years, prices = zip(*sorted(price.items()))
    else:
        years, prices = (0,), (float(price),)
    excluded = set(exclude)

    with atomic_transaction(db) as conn:
        curs = conn.cursor()
        regions = [r[0] for r in curs.execute("SELECT region FROM region ORDER BY region")]
        periods = [r[0] for r in curs.execute("SELECT period FROM time_period WHERE flag = 'f' ORDER BY period")][:-1]
        for region in regions:
            if region in excluded:
                continue
            for period in periods:
                value = float(np.interp(period + period_offset, years, prices))
                curs.execute(
                    """
                    REPLACE INTO cost_emission(region, period, emis_comm, cost, units, notes, data_id)
                    VALUES(?, ?, ?, ?, 'M$/kt', ?, ?)
                    """,
                    (region, period, emis_comm, value / 1000,  # $/t to M$/kt
                     f'{notes}, {value:.2f} 2020 C$/t', f'ELCHR{region}003'),
                )
        conn.commit()

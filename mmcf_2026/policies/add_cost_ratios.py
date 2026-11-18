"""
Apply the MMCF TF2 harmonized capital cost ratios (data book 3 Nov 2026, tab 2a): the
conventional technology keeps CANOE's own investment cost, and the emerging one is set to
that cost times the ratio. Run 1 = conservative, Run 2 = accelerated; the reference run
applies nothing (CANOE's own costs).

Mapping decisions (record in the Phase 0 inventory):
- B1 heat pump ratio: residential air-source heat pump vs gas furnace (the pair itself),
  and residential water heating (heat pump vs gas). Not commercial space heating: CANOE
  costs commercial heat pumps per PJ/y as a combined heating and cooling device (already
  cheaper than furnace + rooftop AC), so the per-home Appendix 2 ratio against a
  heating-only furnace does not carry over; it would cut the heat pump cost ~75%, below
  the rooftop AC. Geo-exchange heat pumps are not mapped.
- T1 light-duty ratio: cars, light passenger trucks and light freight trucks. CANOE has
  four BEV ranges; all are scaled by the factor that puts BEV300 at gasoline x ratio, so
  the spread between ranges is kept. Motorcycles and buses are not mapped.
- T2 freight ratio: heavy-duty trucks (Class 8 proxy).
- PHEV, HEV and FCEV costs are unchanged.
- Each vintage takes the ratio of its period end year (period_end, like the demands and
  prices), interpolated linearly between the tab 2a years.
- Regions without the conventional tech (no gas furnaces in NLLAB and PEI) use the mean
  conventional cost of the same vintage across the regions that have it.

Run before `add_itcs`: costs are recomputed from the conventional cost, so running this
after the ITCs would undo the credits on the emerging techs.
"""

from typing import Literal

import numpy as np
from canoe.common import atomic_transaction
from loguru import logger

RATIO_YEARS = (2025, 2030, 2035, 2040, 2045, 2050)
# Tab 2a, emerging / conventional capital cost
RATIOS = {
    'run1': {
        'B1': (2.36043839490896, 2.21159625243062, 2.13717518119144, 2.06275410995227, 1.9883330387131, 1.91391196747393),
        'T1': (1.25523931078494, 1.15292916400766, 1.13033822590938, 1.1077472878111, 1.08515634971283, 1.06256541161455),
        'T2': (2.27078664054167, 1.72829640795519, 1.56444601290351, 1.40059561785184, 1.23674522280016, 1.07289482774848),
    },
    'run2': {
        'B1': (2.36043839490896, 2.16669612869012, 2.06575923634435, 1.96482234399859, 1.86388545165282, 1.76294855930705),
        'T1': (1.25523931078494, 1.0506445437141, 1.00545628589662, 0.960268028079132, 0.915079770261646, 0.869891512444161),
        'T2': (1.93016864446042, 1.46905194676191, 1.32977911096799, 1.19050627517406, 1.05123343938014, 0.911960603586212),
    },
}

# (pair, conventional tech, reference emerging tech, emerging techs scaled with it)
MAPPING = [
    ('B1', 'R_SPH_NG_FRN-TYP-NEW', 'R_SPHC_AIR_HP-TYP-NEW', ['R_SPHC_AIR_HP-TYP-NEW']),
    ('B1', 'R_WAH_NG-TYP-NEW', 'R_WAH_HP-TYP-NEW', ['R_WAH_HP-TYP-NEW']),
    *[
        ('T1', f'T_LDV_{cls}_GSL', f'T_LDV_{cls}_BEV300', [f'T_LDV_{cls}_BEV{r}' for r in (150, 200, 300, 400)])
        for cls in ('C', 'LTP', 'LTF')
    ],
    ('T2', 'T_HDV_T_DSL', 'T_HDV_T_BEV', ['T_HDV_T_BEV']),
]


def add_cost_ratios(
    db: str,
    run: Literal['run1', 'run2'],
    period_offset: int = 5,
    marker: str = 'MMCF TF2 cost ratio',
) -> None:
    """
    Set the emerging techs' cost_invest to the conventional cost times the run's ratio.

    :param db: Path to the SQLite database.
    :param run: 'run1' (conservative) or 'run2' (accelerated).
    :param period_offset: Years from a vintage to the year whose ratio it takes
        (5 for period_end, 0 for the vintage year itself).
    :param marker: Text written to the notes of the updated rows.
    """
    ratios = RATIOS[run]

    with atomic_transaction(db) as conn:
        curs = conn.cursor()
        regions = [r[0] for r in curs.execute("SELECT region FROM region ORDER BY region")]

        def costs(tech: str) -> dict[tuple[str, int], tuple[float, str]]:
            return {
                (region, vintage): (cost, units)
                for region, vintage, cost, units in curs.execute(
                    "SELECT region, vintage, cost, units FROM cost_invest WHERE tech = ?", (tech,)
                )
            }

        for pair, conventional, reference, emerging in MAPPING:
            conv = costs(conventional)
            ref = costs(reference)
            if not conv or not ref:
                logger.warning(f"{run} {pair}: no cost_invest for {conventional} or {reference}, skipped")
                continue
            for region in regions:
                for vintage in sorted({v for r, v in ref if r == region}):
                    ref_cost, ref_units = ref[(region, vintage)]
                    if (region, vintage) in conv:
                        conv_cost, conv_units = conv[(region, vintage)]
                        basis = conventional
                    else:
                        same_vintage = [c for (r, v), c in conv.items() if v == vintage]
                        if not same_vintage:
                            logger.warning(f"{run} {pair}: no {conventional} cost for vintage {vintage}, {region} skipped")
                            continue
                        conv_cost = float(np.mean([c for c, _ in same_vintage]))
                        conv_units = same_vintage[0][1]
                        basis = f'{conventional} (mean of other regions)'
                    if conv_units != ref_units:
                        raise ValueError(
                            f"{pair} {region} {vintage}: {conventional} in {conv_units!r}, {reference} in {ref_units!r}"
                        )
                    ratio = float(np.interp(vintage + period_offset, RATIO_YEARS, ratios[pair]))
                    factor = conv_cost * ratio / ref_cost  # same factor for every emerging tech of the pair
                    note = f'{marker} {run} {pair} {ratio:.3f} x {basis}'
                    for tech in emerging:
                        # Rerunning recomputes the same cost (factor 1) and keeps the first note
                        curs.execute(
                            """
                            UPDATE cost_invest
                            SET cost = cost * ?,
                                notes = CASE WHEN COALESCE(notes, '') LIKE ? THEN notes
                                             ELSE COALESCE(notes || '; ', '') || ? END
                            WHERE tech = ? AND region = ? AND vintage = ?
                            """,
                            (factor, f'%{marker}%', note, tech, region, vintage),
                        )
        conn.commit()

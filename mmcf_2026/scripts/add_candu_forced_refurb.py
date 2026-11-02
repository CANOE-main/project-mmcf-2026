"""
Add nuclear refurbishment
Using darlington refurb costs per unit capacity distributed over 5 years
Darlington costs were 2016-2026 so we just say roughly 2020 dollars
Only applies to Ontario in the end
"""

from canoe.common import atomic_transaction

DEFAULT_TECH = "E_NUC_CANDU-EXS"
DEFAULT_REGION = "ON"
DEFAULT_ORIGINAL_LIFE = 60
DEFAULT_LIFE_EXTENSION = 30
DEFAULT_OUTAGE_YEARS = 3
DEFAULT_PERIOD_LENGTH = 5
DEFAULT_FIRST_PERIOD = 2025
DEFAULT_LAST_PERIOD = 2045
DEFAULT_BASE_FIXED_COST = 177.983
DEFAULT_REFURB_FIXED_COST = 728.929385
DEFAULT_PJ_PER_CAPACITY_YEAR = 31.536  # 1 GW running for a full year = 31.536 PJ
DEFAULT_NOTES = "some units offline for refurbishment"
DEFAULT_DATA_ID = "ELCHRON003"


def add_candu_forced_refurb(
    db: str,
    tech: str = DEFAULT_TECH,
    region: str = DEFAULT_REGION,
    original_life: int = DEFAULT_ORIGINAL_LIFE,
    life_extension: int = DEFAULT_LIFE_EXTENSION,
    outage_years: float = DEFAULT_OUTAGE_YEARS,
    period_length: int = DEFAULT_PERIOD_LENGTH,
    first_period: int = DEFAULT_FIRST_PERIOD,
    last_period: int = DEFAULT_LAST_PERIOD,
    base_fixed_cost: float = DEFAULT_BASE_FIXED_COST,
    refurb_fixed_cost: float = DEFAULT_REFURB_FIXED_COST,
    pj_per_capacity_year: float = DEFAULT_PJ_PER_CAPACITY_YEAR,
    notes: str = DEFAULT_NOTES,
    data_id: str = DEFAULT_DATA_ID,
) -> None:
    """
    Force a mid-life refurbishment of existing CANDU reactors.

    1. Extends the tech lifetime to ``original_life + life_extension``.
    2. Extends ``cost_fixed`` / ``cost_variable`` rows of each vintage out to
       ``last_period`` by copying that vintage's latest existing row.
    3. In the period where a vintage reaches ``original_life``, sets its fixed
       cost to ``base_fixed_cost + refurb_fixed_cost`` (applies in all regions).
    4. In ``region``, caps the tech's activity in each refurbishment period to
       account for the retiring units being offline for ``outage_years`` of the
       ``period_length``-year period.

    :param db: Path to the SQLite database.
    :param tech: Technology to refurbish.
    :param region: Region where the outage activity limit is applied.
    :param original_life: Age (years) at which refurbishment happens.
    :param life_extension: Years of life added by the refurbishment.
    :param outage_years: Years each unit is offline while being refurbished.
    :param period_length: Length (years) of each model period.
    :param first_period: First period to extend costs into / check for refurbs.
    :param last_period: Last period (inclusive) to extend costs into / check for refurbs.
    :param base_fixed_cost: Normal fixed cost per unit capacity.
    :param refurb_fixed_cost: Extra fixed cost per unit capacity during the refurb period.
    :param pj_per_capacity_year: Activity (PJ) produced by one unit of capacity in a year.
    :param notes: Notes stored on the limit_activity rows.
    :param data_id: data_id stored on the limit_activity rows.
    """
    new_lifetime = original_life + life_extension
    periods = range(first_period, last_period + 1, period_length)

    with atomic_transaction(db) as conn:
        curs = conn.cursor()

        curs.execute("UPDATE lifetime_tech SET lifetime = ? WHERE tech = ?", (new_lifetime, tech))

        # Extend fixed and variable costs by copying each vintage's last existing row
        # (done before the refurb cost update so the copies carry the normal cost)
        for table in ("cost_fixed", "cost_variable"):
            cols = [r[1] for r in curs.execute(f"PRAGMA table_info({table})")]
            select_cols = ", ".join("?" if c == "period" else c for c in cols)
            rows = curs.execute(
                f"""
                SELECT region, vintage, max(period)
                FROM {table}
                WHERE tech = ?
                GROUP BY region, vintage
                """,
                (tech,),
            ).fetchall()
            for row_region, vintage, vintage_last_period in rows:
                for period in periods:
                    if period <= vintage_last_period or period > vintage + new_lifetime:
                        continue
                    curs.execute(
                        f"""
                        INSERT OR IGNORE INTO {table}({", ".join(cols)})
                        SELECT {select_cols} FROM {table}
                        WHERE tech = ? AND region = ? AND vintage = ? AND period = ?
                        """,
                        (period, tech, row_region, vintage, vintage_last_period),
                    )

        curs.execute(
            "UPDATE cost_fixed SET cost = ? WHERE tech = ? AND period = vintage + ?",
            (base_fixed_cost + refurb_fixed_cost, tech, original_life),
        )

        total_capacity = curs.execute(
            "SELECT sum(capacity) FROM existing_capacity WHERE region = ? AND tech = ?",
            (region, tech),
        ).fetchone()[0]
        for period in periods:
            refurbishing = curs.execute(
                """
                SELECT sum(capacity) FROM existing_capacity
                WHERE region = ? AND tech = ? AND vintage + ? = ?
                """,
                (region, tech, original_life, period),
            ).fetchone()[0]
            if not refurbishing:
                continue
            # Allowed generation with the refurbishing units offline for outage_years of the period
            available = total_capacity - refurbishing * outage_years / period_length
            activity_lim = available * pj_per_capacity_year
            curs.execute(
                """
                REPLACE INTO limit_activity
                    (region, period, tech_or_group, operator, activity, units, notes, data_id)
                VALUES (?, ?, ?, 'le', ?, 'PJ', ?, ?)
                """,
                (region, period, tech, activity_lim, notes, data_id),
            )
        conn.commit()


if __name__ == "__main__":
    add_candu_forced_refurb("canoe-pwd/canoe.sqlite")

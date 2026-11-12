# Notes: post-compile scripts

## add_cer.py
- Needs the `idelder/temoa@mmcf_2026` fork with `"carbon_markets"` in `extensions` (currently only `growth_rates`).
- Biomass (`E_BIO_*`) gets CER emissions (`F_gbio`/`F_sbio` have co2 factors). Exclude it?
- CER table rows have no `units`.
- New `emission_activity` rows have NULL `data_id`, so reruns append duplicates. Run once per DB.

## add_candu_forced_refurb.py
- Outage cap assumes 100% availability. It probably won't bind at CF ≈ 0.9 (2035/2040 need CF > ~0.91). Consider scaling by CF.
- Refurb fixed cost applies in all regions, not just `region` (fine while only ON hits age 60 in the horizon).
- Lifetime extension also applies to NB (no effect before 2050).
- Refurb only triggers when vintage + 60 is a model period.
- `REPLACE INTO limit_activity` overwrites any existing limit with the same key.

## add_building_codes.py
- The 2035 gas space-heating ban in the docstring isn't implemented.

## add_uc.py
- Enable it: add `"unit_commitment"` to `extensions` in the Temoa config (currently only `growth_rates`).
- `unit_capacity` in the CSV is in MW (CODERS) but labelled GW. Harmless while every row has `linearized=1`; wrong if any row is set to 0.
- Not rerunnable: the inserts fail on the primary key if the rows already exist.

## add_coal_phaseout.py
- Only limits `E_COAL-EXS`; `E_COAL_CCS-EXS` (SK) keeps running.

## General
- Temoa's last `time_period` (2050) is the horizon end, not a period, so `last_period=2045` is correct.
- FKs are enforced: any new `data_id` must exist in `data_set`.
- Temoa comes from `idelder/temoa@mmcf_2026` via `override-dependencies` in `pyproject.toml` (branch, not commit: `uv lock --upgrade-package temoa` picks up new commits). It reports 4.0.0a2, older than CANOE's pinned 4.0.1.

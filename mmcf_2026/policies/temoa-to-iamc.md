## Temoa to IAMC conversion

`temoa_to_iamc.py` converts Temoa (v4 database schema) results into the MMCF Round 3 Task Force 2 reporting template
(`MMCF_R3_TF2_Reporting_Template_v3_21Oct2026.xlsx`). It writes a CSV for IDEA and a filled copy of the template workbook
for submission.

### Usage

```bash
python temoa_to_iamc.py --run TF2-Run1=run1.sqlite --run TF2-Run2=run2.sqlite --run TF2-Reference=ref.sqlite
```

| Argument        | Default                    | Purpose                                                                       |
|-----------------|----------------------------|-------------------------------------------------------------------------------|
| `--run`         | required, repeatable       | `SCENARIO=DATABASE`: template scenario name and its Temoa results database    |
| `--model`       | `CANOE`                    | Model name written to the `model` column and the file name                    |
| `--stage`       | `prelim`                   | `prelim` or `final`, used in the file name                                    |
| `--template`    | template next to script    | Reporting template; its Variables tab is used for validation                  |
| `--output-dir`  | current directory          | Where `MMCF_R3_TF2_<model>_<stage>.csv` and `.xlsx` are written               |
| `--db-scenario` | the single DB scenario     | Scenario inside the databases; needed only if a database holds several        |
| `--dollar-year` | `2020`                     | Dollar year written in cost and price units                                   |
| `--cpi-factor`  | `1.0`                      | Multiplier from the database dollar year (2020) to `--dollar-year`            |

For CAD$2025, pass `--dollar-year 2025 --cpi-factor <factor>` with the 2020→2025 factor from tab 6 of the data book.

The CSV has the IAMC columns `model, scenario, region, variable, unit, time, value` (see [IAMC Format](iamc-format.md)).
The workbook is a copy of the template with the `data` sheet filled, including a `note` per row.

### Method

**Aggregation.** All results are aggregated in SQL over season and time of day before reaching pandas. The output
therefore does not depend on the number of representative days. Databases are opened read-only.

**Reporting years.** CANOE models each period at its end year (`demand_projection_point = "period_end"`). Each period is
therefore reported as the following year in `time_period`, so periods 2025–2045 become 2030–2050. 2025 is not reported.
Vintages (new capacity, investment costs) are shifted the same way.

**Regions.** The ten database regions map to template names. Inter-regional exchange regions such as `AB-BC` are
dropped. `Canada` is the sum of the provinces, with these exceptions:

- Costs are the mean of the provinces.
- Prices are averaged across provinces, weighted by consumption.
- Shares are computed from the summed capacity.

**Final energy.** Final energy is consumption (`output_flow_in`) of delivered fuels. Delivered fuels are the outputs of
the CANOE delivery technologies (`F_*`, `E_*`) in end-use sectors, for example `F_R_NG → R_ng` and `E_R_ELC → R_elc`.

- **Consumption, not supply.** Supply of a delivered fuel can exceed consumption, because the balance constraint is an
  inequality: in 2025, 613 PJ of `R_elc` was delivered but 576 PJ consumed.
- **Classification.** The delivery technology's sector gives the template sector. The commodity suffix gives the fuel
  (`FUEL_TYPES`). An unmapped suffix stops the script, so a new fuel is never dropped silently.
- **Blank vs zero.** Every fuel path in the `efficiency` table gets a zero row. A fuel the model represents but does not
  use (e.g. transportation hydrogen) is reported as 0. Unrepresented fuels stay blank.
- **Totals.** Sector totals, `Use Final`, the `Use Final|Energy|*` lines and the buildings aggregates are sums of the fuel
  lines, so the template's sum checks hold by construction.
- **Space heating by fuel.** The same consumption, restricted to the `R_D_SPH` and `C_D_SPH` demands.

**Emissions.** `co2e` from `output_emission`, kt → Mt.

- `Emissions|GHG Total` is gross: positive emissions only.
- Negative emissions (CCS technologies) are reported as `Carbon Sequestration|CCS`, so net = gross + CCS.
- Combustion emissions are reported by the emitting technology's sector.
- `OG_CH4` is reported as Fugitive.

**Technology shares.** Technology names are classified by regular expression into a segment (passenger, freight,
residential heating, commercial heating) and a technology class.

- Sales shares use new capacity by vintage; stock shares use net capacity.
- Each segment adds to 100%.
- The `note` column records the basis of each share.

**Prices.** `Price|Use Final|Residential|Electricity` and `|Gas` are the duals of the `R_elc` and `R_ng` balance
constraints (objective in M$, flows in PJ, so $/GJ).

- **Undiscounting.** Temoa duals are in discounted objective terms. The script divides each dual by the period's discount
  factor $\sum_{k=1}^{L}(1+r)^{-(p-p_0+k)}$, where:
    - $r$ is `global_discount_rate`,
    - $p_0$ is the first future period,
    - $L$ is the period length.
- **Check.** For ON in 2025 this reproduces the input cost of gas exactly ($45.70 / 4.580 = 9.98$ $/GJ $=$ `F_IMP_NG` +
  `F_R_NG`).
- **Annual price.** Time-slice duals (electricity) are averaged weighting by consumption in each slice.
- **Requirement.** Prices need `save_duals = true` in the Temoa run.

**Capital costs.** `cost_invest` for one representative technology per template line, M$ per thousand units → $ per unit.

**Other.**

- `Production Secondary|Electricity` is the output to the grid of generating technologies. It excludes
  transmission/distribution, interties, US imports and storage discharge.
- Passenger and freight activity is consumption of the transport demand commodities, billions → millions of pkm/tkm.

**Validation.** Before writing, every reported variable is checked against the template's Variables tab:

- An unknown name stops the script.
- Unit differences other than the dollar year are printed.
- Core variables not reported are listed.

### Assumptions

- **Agriculture** is reported inside Industry.
- **Upstream emissions.** Fuel-supply emissions (the `fuel` sector, e.g. emission factors on `F_IMP_*`) are reported as
  Industry combustion. Only `OG_CH4` is Fugitive.
- **Fuel groupings:**
    - Oil: LPG, petroleum coke, natural gas liquids, heavy fuel oil, marine diesel and jet fuel.
    - Coal: coke.
    - Biomass Solids: wood.
    - Biomass Liquids: ethanol, renewable diesel and SPK (HEFA, which is bio-based).
    - Gas: CNG and LNG.
- **Passenger** shares cover cars and passenger light trucks. **Freight** shares cover light, medium and heavy freight
  trucks.
- **Vehicle classes:**
    - PHEV includes plug-in hybrids.
    - FCEV includes fuel-cell hybrids.
    - HEV is a gasoline or diesel hybrid.
    - ICE is gasoline, diesel and CNG.
- **Heating classes:**
    - Heat Pump is air- and ground-source heat pumps.
    - Electric Resistance includes electric boilers.
    - Gas Furnace or Boiler includes gas heat pumps.
    - Other is dual-fuel existing systems, wood and LPG.
- **Commercial heating shares** are on a capacity basis (PJ/y), because commercial technologies are not in units.
- **Cost lines** use `R_SPHC_AIR_HP-TYP-NEW`, `R_SPH_NG_FRN-TYP-NEW`, `T_LDV_C_BEV300`, `T_LDV_C_GSL`, `T_HDV_T_BEV` and
  `T_HDV_T_DSL`.
- **No carbon price.** The run applies no explicit carbon price, so prices include none.

### Not reported

Lines the database cannot support are left blank, which the template reads as "not represented":

- `Price|Carbon` and `Price|Carbon|OBPS`. There is no emission price or cap; the CER cap-and-trade on electricity is not
  an economy-wide carbon price.
- LULUCF, direct air capture, industrial-process emissions and `Emissions|GHG Total|Other`.
- Non-energy use and chemical feedstock lines. Feedstocks sit inside the `I_CHEM` industry demand and cannot be split.
- Industry `Heat` and `Other`, transportation `Pipeline`.
- Floor space and peak demand.

### Extending

The mappings (`END_USE_SECTORS`, `EMISSION_SECTORS`, `FUEL_TYPES`, `SEGMENTS`, `COSTS`, `PRICES`) are module constants
at the top of the script. To report a new template variable, add or adjust a mapping and, if needed, one small function
that returns IAMC rows. Validation rejects any name that is not in the template.

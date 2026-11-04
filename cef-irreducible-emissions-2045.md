# CEF Net-Zero: irreducible emissions in 2045 ("Closest to Net-zero" floor)

**Floor: ~505 Mt CO2e in 2045** (with NZ policies, incl. O&G methane). Without policies (no `OG_CH4`): **~483 Mt**.
Any 2045 cap below this is infeasible in the current configuration; use ~510 Mt for solver headroom.

## Irreducible emissions, 2045 (Mt CO2e, `nz-price-pol`)

Upstream emissions (`F_IMP_*`, a fixed factor per PJ of fuel supplied) are assigned to the sector that uses the fuel.

| Source | Mt | Why it can't be reduced |
|---|---|---|
| Industry fuel use | 314.1 | Fixed fuel-consumption techs (`F_I_*`), no alternatives; includes wood 61.7 |
| Agriculture fuel use | 27.0 | Fixed fuel-consumption techs (`F_A_*`), no alternatives |
| O&G methane (`OG_CH4`) | 22.4 | Fixed exogenous demand (`OG_D_CH4`) |
| Upstream NG | 30.3 | All supplies fixed users (industry, cogen, agriculture, commercial) |
| Upstream diesel/gasoline | 20.1 | Supplies industry, agriculture, off-road |
| Upstream pet. coke, coke, NGL, HFO | 17.6 | Supplies industry |
| Aviation (combustion + upstream) | 29.0 | Best option is a 50% SAF blend (`T_jtf_spk50`), already fully used |
| Off-road, marine, biofuel blends | 19.9 | Single diesel tech (`T_OFF`); marine options all fossil; fixed blend shares |
| Electricity: gas cogen | 9.1 | Existing `E_NG_CG-EXS` output held at 0.95–1× historical (`limit_activity`) |
| Commercial gas (`C_OTH`) | 2.5 | Only tech for `C_D_OTH`; fuel split fixed (`limit_tech_input_split_annual`) |
| Road vehicles (combustion + upstream) | 14.3 | Existing (≤2020) fossil stock: `retire=0`, utilization ≥95% (`limit_annual_capacity_factor`) |
| Residential | 0.1 | Negligible |
| **Total** | **≈506** | Remaining flexibility ≈1 Mt (5% bands on cogen and vehicle utilization) |

## How we got here

1. Ran the four TF2 scenarios (`mmcf_2026/run_tf2_experiments.sh`): no price or $10,000/t economy-wide carbon price, each with and without the NZ policies.
2. The $10k/t price is far above any abatement cost, so every remaining emission is either impossible to cut or forced by a constraint. The 2045 totals level off at 484 Mt (no policies) and 506 Mt (with policies), down from 708 Mt without a price.
3. Compared emissions by tech with and without the price. Industry, agriculture and O&G methane don't change at all.
4. For what remained, traced each fuel to the techs using it (`output_flow_in`) and checked the alternatives (`efficiency`) and binding settings (`limit_activity`, `limit_annual_capacity_factor`, `limit_tech_input_split_annual`, `technology.retire`). Every remaining source has no alternative or is held in place by a constraint.

## Caveats

- **Wood (61.7 Mt):** counted as emitting. If treated as biogenic, the floor drops to ~443 Mt.
- **SAF upstream:** `F_IMP_SPK` (18.3 kt/PJ) is about the same as fossil jet fuel (`F_IMP_JTF`, 18.4 kt/PJ); probably a placeholder.
- **2025:** not fixed to the base year. The price cuts 2025 by ~145 Mt; the cap should use the base-year value (~695 Mt).
- **Other periods:** only 2045 was broken down. The price-run floors for 2025–2040 (571 / 541 / 517 / 505 Mt) are likely locked in the same way but weren't checked.

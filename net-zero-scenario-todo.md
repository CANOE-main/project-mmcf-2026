# Net-zero scenario (MMCF TF2 reference run) TODO

Reference run = Run 1 common configuration (EF2026 Canada Net-zero) with CANOE's own cost ratios.

## Done
- [x] CEF 2026 fuel prices (provincial delivered)
- [x] CEF 2026 GDP
- [x] Coal phase-out (`add_coal_phaseout`)
- [x] CER table + `carbon_markets` extension enabled
- [x] Unit commitment + extension enabled
- [x] Building codes shell efficiency (fixed: demand names are uppercase, it was a no-op before); read at period end
- [x] Temoa fork `idelder/temoa@mmcf_2026`
- [x] Net-zero endpoint (`add_net_zero_cap`), `NET_ZERO_CAP_MT` = 505 x 1.10 Mt CO2e
- [x] End-use efficiencies held at the 2025 vintage (`add_frozen_efficiencies`, all runs)
- [x] Harmonized cost ratios for Run 1 / Run 2 (`add_cost_ratios`, `--cost-ratios run1|run2`)
- [x] Submission runs script (`mmcf_2026/run_tf2_runs.sh`): reference, Run 1, Run 2
- [x] Data centre load (`add_data_centres`): new electric-only demand `C_D_DC`, flat profile
- [x] Clean Economy ITCs (`add_itcs`), rates and eligibility checked against EF2026 Appendix 1
- [x] Methane regulations (`add_methane`): fixed dummy emitter `OG_CH4`, path checked against EF2026 Appendix 1
- [x] Quebec WCI (`add_qc_wci`), skipped until `QC_WCI_PRICE` is set

## To do
- [ ] Set `QC_WCI_PRICE` (2030 WCI price, converted to 2020 C$/t)

## Inventory only (no model change)
- Population: StatCan provincial kept instead of the national common input (resolution)
- Industrial carbon pricing / aggregate cost of carbon: not applied. Industry fuel use is fixed (one tech per demand, fixed input shares), so a price changes nothing; pricing upstream supply (F_IMP_*) would act economy-wide
- Excluded mandates (gas heating ban 2035, ZEV mandates, HDV ZEV by 2050, EVAS): none present in the model
- Oil heating ban in new buildings from 2030: not modelled, negligible
- HFC regulations: not modelled
- Clean Fuel Regulations: not modelled this round (see decisions), although the data book lists them as included
- Clean Hydrogen, CCUS and Clean Tech Manufacturing ITCs: not applied
- No ITC on on-road EVs (Clean Technology ITC covers non-road ZEVs only)
- Methane regulations: fixed emissions, no abatement choice in the model
- Benchmark oil/gas prices: not used, only delivered prices
- LDV operating cost ratio (0.395): not forced, energy prices are endogenous; report CANOE's implied ratio at base-year prices
- Base year vs StatCan RESD: to confirm
- Carbon price treatment (on top / backstop): to record
- 2050 reporting from the 2045 period: to state

## Decisions
- Efficiencies: data book asks for 2024 values, no learning. Every future vintage of `T_`, `R_` and `C_` techs takes its 2025-vintage efficiency (the first model vintage, closest to 2024), in all three runs. Electricity techs untouched (their vintage differences are CCS retrofits and new plants, not end-use learning)
- Net-zero endpoint: cap national co2e in the 2045 period, not zero (CANOE has no negative-emission techs). Cap = 505 Mt, the 2045 minimum found with a very high carbon price (policies on), plus 10% slack so the cap is not the only feasible solution and the cost ratios can still move the result: 555.5 Mt. Closest feasible run, to compare with the EF2026 residual in the inventory. Same cap in the reference run, Run 1 and Run 2; global carbon price off
- Harmonized cost ratios (`add_cost_ratios`, data book 3 Nov tab 2a): conventional keeps CANOE's cost, emerging = conventional x ratio. Applied before the ITCs. Mapping:
  - B1 heat pump: residential air-source heat pump / gas furnace; also residential water heating (heat pump / gas). Geo-exchange heat pumps not mapped
  - Commercial space heating: not mapped (deviation from the study design's suggested mapping, which it leaves to each team). CANOE costs commercial heat pumps per PJ/y as a combined heating and cooling device, already cheaper than furnace + rooftop AC (13.2 vs 1.5 + 17.2, ON 2025); the per-home Appendix 2 ratio against a heating-only furnace would cut it ~75%, below the rooftop AC alone. Commercial heating therefore does not respond between Run 1 and Run 2 (say so in the Run 2 note)
  - T1 light-duty: cars, light passenger trucks, light freight trucks. All four BEV ranges scaled by the factor that puts BEV300 at gasoline x ratio. Motorcycles and buses not mapped
  - T2 freight: heavy-duty trucks `T_HDV_T_*`
  - PHEV, HEV, FCEV unchanged
  - Each vintage takes the ratio of its period end year (vintage 2025 -> 2030 ratio), linear between tab 2a years
  - NLLAB and PEI have no gas furnaces: the conventional cost is the mean of the other regions for that vintage
- Industrial carbon price: skipped (see inventory)
- Clean Fuel Regulations: not applied for now. EF2026 Table A1.1 target, for reference if added later: 12 gCO2e/MJ below 2016 levels by 2030, then a further 1.5 gCO2e/MJ decrease between 2030 and 2050 (open question then: average across fuels or per fuel)
- Building codes: read at period end, like the demands they scale
- LDV operating cost ratio: record, don't force
- Data centres: new electric-only demand; split by base-year commercial electricity (`C_OTH` demand x electricity share, ~ON 35%, QC 24%, AB 16%, BC 12%); read at period end (2025 period = 1.5 GW ... 2045 period = 3.5 GW)
- Quebec WCI: `cost_emission` on all QC co2e (WCI covers fuel distributors, so QC transport and buildings respond), 2030 price held constant in real terms in every period (all periods stand for 2030+ under period end). Temoa does no dollar-year conversion, so the price goes in already in 2020 C$
- ITCs, as reductions to `cost_invest` on vintages 2025 and 2030 (builds 2025-2034), none from 2035. Rates and eligibility are from the federal legislation, checked against EF2026 Appendix 1 (4 Nov 2026):
  - Clean Technology 30% (legislated 30% to 2033, 15% in 2034, then 0; the 2034 year is ignored): solar `E_SOL_PV-NEW-*`, onshore wind `E_WND_ON-NEW-*`, SMR `E_NUC_SMR-NEW`, biomass `E_BIO_M-NEW` (assumes it is waste biomass), batteries `E_BAT_*-NEW`, commercial heat pumps `C_SPHC_HP_*` (business use; residential not eligible)
  - Clean Electricity 15% (to 2034; not stackable, so techs eligible for both take Clean Technology): NG+CCS `E_NG_CCS-NEW` and retrofits `E_NG_CCS_RFIT_*`, large nuclear `E_NUC_PWR-NEW`
  - EVs: no ITC. Clean Technology covers non-road ZEVs only, and it would shift the BEV/ICE ratio TF2 compares (record in inventory)
- Methane: dummy emitter in AB, 0.8 Mt CH4 / 22.4 Mt CO2e (AR5 GWP 28) in every period. Oil and gas methane cut 75% below 2012 by 2030 (all provinces assumed to meet it); the 2025 value (1.044 Mt CH4 / 29.2 Mt CO2e) is unused because every period stands for 2030+ under period end

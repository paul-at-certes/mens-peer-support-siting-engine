# Occupational Risk Layer — Claude Code Handoff Spec

**Project:** Andy's Man Club geospatial targeting engine
**Layer:** Male occupational suicide risk index (area-level, England & Wales)
**Status:** Ready to implement. All source URLs verified August 2026.

---

## 1. Objective

Produce an LSOA-level (fallback: MSOA) index estimating the *expected relative suicide risk of an area's male working population given its occupational mix*, for use alongside the existing deprivation (IMD) and isolation layers. Output must be a normalised score joinable to the engine's existing geography spine.

---

## 2. Data inputs

| # | Dataset | Source | Role |
|---|---------|--------|------|
| 1 | Suicide by occupation: England, main data tables (2011–2015) | https://www.ons.gov.uk/peoplepopulationandcommunity/birthsdeathsandmarriages/deaths/datasets/suicidebyoccupationenglandmaindatatables | Male SMRs by SOC 2010 at major / sub-major / minor / unit level. **This is the only official SMR-based risk source — the 2016–2020 update was cancelled.** |
| 2 | Census 2021 occupation shares (SOC 2020), small-area | Nomis — dataset **TS063 (Occupation)** for major-group shares at OA/LSOA; ONS "Create a custom dataset" for 2-/3-digit SOC at coarser geography. **VERIFY** the finest SOC digit level actually published per geography before locking the design. | Denominator: male workforce composition per area. |
| 3 | SOC 2010 → SOC 2020 correspondence | ONS SOC 2020 publication (Volume 1/2 includes the relationship to SOC 2010): https://www.ons.gov.uk/methodology/classificationsandstandards/standardoccupationalclassificationsoc/soc2020 | Bridge the SMR vintage (SOC 2010) onto census shares (SOC 2020). |
| 4 | ONS ad hoc counts: suicide by occupation 2011–2021; construction workers 2011–2024 | https://www.ons.gov.uk/peoplepopulationandcommunity/birthsdeathsandmarriages/deaths/adhocs/15043suicidebyoccupationenglandandwales2011to2021registrations and https://www.ons.gov.uk/peoplepopulationandcommunity/birthsdeathsandmarriages/deaths/adhocs/3370suicidedeathsamongconstructionworkersinenglandandwalesdeathsregisteredbetween2011and2024 | Trend context only. **Counts must never be converted to risk weights** (ONS explicit caveat: counts reflect population structure, not risk). |
| 5 | Hare, Lawani & McEwen (2024), *Suicides among construction occupations in the UK*, JEPPM 14(2) | https://researchonline.gcu.ac.uk/ws/portalfiles/portal/82283223/82274781.pdf | Optional trend uplift for construction (rates rose 2015–2021, ~3× non-construction; unskilled ~7× managers). |

---

## 3. Risk weight vector (from input 1)

Parse the male SMR sheets of the ONS workbook. Build `weights.csv`:

```
soc2010_code, soc_level, label, smr, lcl_95, ucl_95, deaths
```

Filtering rules (mirror ONS's own commentary standards):
- Keep a weight only if **deaths ≥ 50** and the **95% CI excludes 100**; otherwise fall back to the parent group's SMR.
- Work at the finest SOC level the census shares support (likely sub-major, 2-digit). Where a high-risk minor/unit group sits inside a lower-risk sub-major (e.g. roofers 2.7× inside skilled construction 1.6×), it is acceptable to use the sub-major weight — document the attenuation.

Reference values to validate the parse against (male, England 2011–2015, SMR/100):

- Elementary construction **3.7** · elementary trades ~**3.0** · roofers/tilers/slaters **2.7** · elementary process plant **2.6**
- Building finishing trades ~**2.0** (plasterers, painters & decorators individually >2.0) · gardeners ~**2.0** · elementary agriculture ~**2.0** · care workers/home carers ~**2.0**
- Fork-lift drivers **1.85** · agricultural & related trades **1.7** · skilled construction & building trades **1.6**
- Elementary occupations (major) **1.44** · skilled trades (major) **1.35** · van drivers **1.25** · culture/media/sport **1.2** · LGV drivers **1.2**
- Process/plant/machine operatives **1.08**
- Low anchors: male health professionals **0.84** (doctors **0.63**), science/eng/tech professionals ~**0.5**, managers/directors/senior officials ~**0.5** (corporate managers/directors **0.28**), bus & coach drivers **0.68**
- Note: **farmers were NOT statistically elevated** in 2011–2015 (agricultural *trades* were, driven partly by firearm access, 12.6% of deaths vs 1.7% nationally). Do not hand-add a farmer uplift.

---

## 4. Method

1. **Map** SOC 2010 weights → SOC 2020 codes via the correspondence table. Where a SOC 2020 code maps to multiple SOC 2010 codes, take the employment-weighted mean SMR (or simple mean if employment splits unavailable — flag which).
2. **Fetch** male occupation shares per LSOA (or MSOA if the SOC digit level forces it) from Census 2021.
3. **Score** each area:
   `occ_risk_raw = Σ_g ( male_share_g × smr_g / 100 )`
   i.e. the expected relative risk of the area's male workforce. Areas dominated by elementary construction, skilled trades, agriculture trades, vehicle-adjacent driving and care work will score high; professional/managerial areas low.
4. **Normalise** to z-scores and percentiles across all areas: `occ_risk_z`, `occ_risk_pctl`.
5. **Deprivation adjustment (important):** the ONS literature (Agerbo et al. 2007, cited in the ONS release) shows the occupational disparity shrinks considerably after controlling for income/employment — this layer is partially collinear with IMD. Produce **both**:
   - `occ_risk_z` (raw), and
   - `occ_risk_resid` = residual from regressing `occ_risk_z` on the IMD score, so the composite index can use the orthogonal component and avoid double-counting deprivation.
6. **Optional age interaction:** multiply or report alongside the male 45–64 population share (peak male rate is 50–54 at ~26.8/100,000). Keep as a separate column, not baked into the occupation score.

---

## 5. Outputs

- `occupational_risk_lsoa.parquet` (or GeoPackage joined to the engine's LSOA boundaries):
  `lsoa21cd, occ_risk_raw, occ_risk_z, occ_risk_pctl, occ_risk_resid, top_contributing_soc_groups (JSON, top 3 with shares), male_45_64_share`
- `weights.csv` + a `WEIGHTS_PROVENANCE.md` recording workbook sheet names, filter rules applied, and any parent-group fallbacks used.

---

## 6. Validation checks

- Spot-check: areas with high construction/agricultural-trades employment should rank in the top deciles; commuter-professional areas near the bottom.
- Regional sanity: aggregated scores should be broadly consistent with the North East and North West having the highest male suicide rates and London the lowest.
- Sensitivity run swapping CI-filtered weights for point estimates; report rank correlation.
- Confirm `occ_risk_resid` vs IMD correlation is ~0 and inspect the areas where raw and residual scores diverge most (expected: rural agricultural areas that are not income-deprived, and affluent districts with large trades workforces — these are exactly where this layer adds value over IMD).

## 7. Known limitations (carry into the engine's methodology notes)

- SMRs are 2011–2015 vintage (deaths registered), England only, ages 20–64, SOC 2010; ~3 in 10 suicide records had no occupation recorded; occupation is informant-reported at death registration and may reflect lifetime rather than current occupation.
- Construction risk has **risen** since the SMR vintage (GCU 2024) — if a trend uplift is applied to construction weights, document it as an assumption, not ONS data.
- Ecological layer: it scores the occupational mix of areas, not individuals, and cannot establish causation. Selection effects (higher-risk men sorting into certain trades) are part of what it captures — which is fine for *targeting support provision*, the engine's purpose.

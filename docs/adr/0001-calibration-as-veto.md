# ADR 0001 — Calibration checks the weights; it does not set them

**Status:** accepted · **Date:** 2026-08-31 · **Supersedes:** the weighting half of `docs/design.md` §4

## Context

`CLAUDE.md`'s hard constraint sets up a two-level design: calibrate at Local
Authority level, where suicide data exists, then apply the learned weights to
small areas, where it does not. The first build followed that literally — a
Poisson/negative-binomial fit of pooled male suicide counts on the three
aggregated proxies, with the fitted coefficients promoted to the scoring
weights.

Measured against the England & Wales data, that does not hold up.

**The model does not identify the weights.** At LA level the proxies are
mutually collinear — deprivation correlates 0.72 with isolation and 0.63 with
occupation. Deprivation therefore **cannot be shown to be positive once the
other two are in the model** — its multivariable coefficient is negative in every
specification tested (rate ratio 0.93 as shipped) while it is positively
associated on its own (RR 1.11). That is a collinearity artefact, not
epidemiology, and it zeroes deprivation under a coefficients-as-weights rule.

*How far that negative sign goes, measured 2026-09-01.* `deprivation_proxy` is
the only proxy entering the LA fit on a within-nation rescaled axis: it averages
0.499 in both nations by construction, while Wales' pooled male rate is 28%
higher than England's (88.8 vs 69.4 per 100k). The pooled fit is thus asked to
explain Welsh excess deaths with a variable flattened at the border, which is the
specification where the negative coefficient is largest and its interval clears
zero.

| specification | deprivation coef | 95% CI | p |
|---|---|---|---|
| pooled E+W (as shipped) | −0.045 | [−0.088, −0.001] | 0.044 |
| England only (n=309) | −0.048 | [−0.097, +0.001] | 0.056 |
| pooled + Wales dummy | −0.035 | [−0.080, +0.010] | 0.126 |

The sign is stable across all three; the significance is not. So this repo claims
only what survives all three — deprivation cannot be shown to be positive in the
multivariable fit — and not the stronger "significantly protective", which holds
in the pooled specification and marginally there. Either reading supports the
same conclusion, and neither changes a weight or a veto: the veto is univariate,
where all three proxies are firmly positive.

**The choice was being made on evidence that does not support it.** Ranking
35,672 LSOAs under each defensible weighting and comparing top-20 shortlists:

| | per-capita top-20 overlap | top-100 |
|---|---|---|
| multivariable vs univariate | 12/20 | 72/100 |
| multivariable vs composite | 8/20 | 61/100 |
| univariate vs composite | 16/20 | 87/100 |
| **equal weights** vs multivariable | 10/20 | 60/100 |
| **equal weights** vs univariate | 16/20 | 70/100 |

Equal weights — no calibration at all — disagrees with each fitted scheme about
as much as the fitted schemes disagree with each other. The fit was not buying
information. But the choice still moved up to 12 of the top 20 areas, so it was
a consequential decision resting on non-identifying evidence.

The original build resolved this by shipping `weighting_scheme: univariate`:
three separate single-predictor GLMs. That keeps all three proxies positive, but
it is not the model `design.md` specifies, it double-counts the correlated
economic proxies, and — most importantly — it still presents a chosen number as
a fitted one.

## Decision

`need_index` is a **transparent allocation index**, not a calibrated surrogate
for suicide risk. This matches `CLAUDE.md`'s own guardrail — *"Latent need, not
prediction… the tool allocates resource; it does not forecast deaths"* — which
the coefficients-as-weights design was quietly exceeding.

1. **Weights are a declared prior** in `config.yaml` (`scoring.component_weights`,
   0.40 / 0.35 / 0.25), applied to within-nation percentile ranks. Because both
   the weights and the components live on a common 0–1 scale, the unit mismatch
   in the old design — per-1-SD coefficients applied to percentile ranks —
   disappears rather than being papered over.
2. **Calibration is a veto.** `calibrate.py` fits the LA model and flags any
   declared weight the data contradicts. It never supplies one.
3. **The veto is tested on the univariate fits.** Under collinearity a partial
   coefficient does not answer "is this proxy associated with the outcome",
   which is the question the veto asks. Severities: `contradicted` (CI entirely
   below zero), `unsupported` (CI spans zero and the declared weight exceeds
   `unsupported_weight_floor`), `collinearity` (informational).
4. **Calibration is non-blocking.** The outcome source is England-only; Wales
   must stay rankable without it. `weights.json` is a diagnostic nothing reads.
5. **Three sensitivity axes, and failure is loud but never fatal.** Named
   alternatives, the CI envelope, and the supply constants. Below threshold the
   run is flagged on the console, the map face and the PDF — and still produces
   the shortlist.

## What this changed in the numbers

**The Poisson offset was wrong, and it mattered.** The fit paired an age-10+
numerator (Fingertips 41001) with a male 16–64 denominator. That ratio varies
with local age structure, which correlates with deprivation. Using the outcome
dataset's own denominator:

| | before | after |
|---|---|---|
| dispersion | 3.26 | 2.66 |
| AIC | 2167.0 | 2116.4 |
| isolation univariate CI | [−0.012, 0.066] — spans zero | [1.031, 1.106] as RR — excludes 1 |

The bug was suppressing the isolation signal. With it fixed, all three proxies
are positively and significantly associated at LA level and the veto **passes**
with only a collinearity note on deprivation.

**The composite scheme merged the wrong pair.** It combined deprivation +
occupation (r=0.63) and left isolation standing alone, when deprivation +
isolation (r=0.72) is the tighter pair. Corrected.

## Consequences

**The headline claim weakens, deliberately.** "Calibrated at LA level" becomes
"sanity-checked at LA level". `CLAUDE.md` and `design.md` §4 are amended to
match. This is the honest description of what 292 LAs and three collinear
proxies support.

**The tool currently reports itself as unstable, and that is working.** On the
England & Wales run:

| axis | worst overlap | threshold | |
|---|---|---|---|
| weighting scheme | 0.46 (multivariable) | 0.70 | **WARN** |
| CI envelope | 0.84 (mean of 200 draws) | 0.70 | ok |
| supply constants | 0.74 (over a 5×3 sweep) | 0.70 | ok |

Thresholds are set from what the number means for the decision — a run that
would send you to 30%+ different places is not carrying the weight a ranking
implies — **not** tuned until the current data passes. The multivariable outlier
is annotated as discarding an evidenced proxy rather than silently excluded.

Two results worth keeping in view. The supply surface, which gates the shortlist
hardest (most of the per-capita top 100 sits in the bottom decile of supply), is
the **most** stable axis — the hand-set travel/catchment constants are not
secretly driving the answer. And the weighting, which the original design spent
its rigour on, is the least stable. That is the opposite of the expected shape,
and it is why the shortlist is presented as a starting point for local judgement.

## Alternatives rejected

- **Keep fitting, pick a scheme by rule.** Still presents a chosen number as a
  fitted one, and the rule would itself be the unevidenced choice.
- **Perturb a stated band around the prior** instead of the fitted CIs. The band
  would be another invented constant; the CIs are at least empirical.
- **Add a fourth proxy** to break the collinearity. With 292 LAs and three
  already-entangled predictors this makes identification worse, not better.
- **Hard-fail on veto.** An allocation index that refuses to produce a shortlist
  because deprivation came out collinear is less useful than one that produces
  it with the caveat attached.

## Addendum, 2026-08-31 — the outcome dataset

Resolved, though not the way this ADR first assumed. Three findings:

**The ONS *Suicides by local authority* workbook cannot be used.** It was
downloaded and read: it is **persons-only**. Neither count table carries a sex
breakdown, so it cannot answer a question about men.

**Nomis `NM_161_1` can.** *Mortality statistics: underlying cause, sex and age*
exposes cause x sex x age x LA for England and Wales over the same API the
occupation and isolation adapters already use — no manual download. It now
supplies the outcome: male, ICD-10 X60-X84 + Y10-Y34, 5-year pooled, geography
`TYPE434`, **331 LAs (309 England, 22 Wales)**, up from 292 England-only.

**Working age is not obtainable at LA level, and this was measured, not assumed.**
Nomis zeroes any cell below 5. At LA x cause x 5-year-age-band x year granularity
almost every cell is below 5 — across 33,900 cells the extract contains no value
of 1, 2, 3 or 4 anywhere:

| request | share of published national total recovered |
|---|---|
| male 15-64, X60-X84 + Y10-Y34 | **~48%** |
| male all ages, X60-X84 + Y10-Y34 | **96.6%** |
| male all ages, X60-X84 only | 99.5% |

A working-age series that silently loses half its deaths — disproportionately in
small local authorities, which is exactly where the regression is most fragile —
is worse for calibration than a nearly complete all-ages one. So the outcome is
**male all ages**, with male all-ages population as the offset, keeping numerator
and denominator matched.

The coefficients barely moved, which is reassuring about the whole design:

| | Fingertips, 292 LAs | Nomis, 331 LAs |
|---|---|---|
| deprivation RR (univariate) | 1.107 | 1.102 |
| occupation RR | 1.136 | 1.136 |
| isolation RR | 1.068 | 1.069 |
| veto | pass (collinearity note) | pass (collinearity note) |

**Wales now has a real suicide signal** for the first time — 22 distinct LA values
across 1,917 LSOAs, mean pooled rate 89.0 per 100k against England's 69.8. It was
previously carrying the neutral 0.5 fallback.

## Addendum, 2026-08-31 — the stability verdict was measuring the wrong thing

The run was reporting UNSTABLE on the weighting axis (0.46 against a 0.70
threshold). Both halves of that were wrong.

**The metric was misread.** `sensitivity.py` reported **Jaccard**, which for two
equal-sized sets is `overlap / (2 - overlap)`. 63 of 100 areas shared reads as
0.46. The threshold was set at 0.70 while being described as *"a run that would
send you to 30%+ different places"* — but Jaccard 0.70 demands **82%** agreement.
The bar was 12 points stricter than intended, by accident.

**The claim was never checked, and is false.** Under both worst-case schemes,
**every one of the declared top-20 stays inside the top 100** — median rank 20
(multivariable) and 14 (composite), worst individual rank 55 and 70. Nobody drops
out of contention. "Would send you to substantially different places" was not
true of this data.

**Set membership is the wrong test anyway.** An area at rank 101 versus rank 99
flips shortlist membership on a rounding error while changing no decision. So the
verdict now measures **displacement**: of the top `decision_n` (20) — the areas
you would actually act on — what share stays inside `contention_band` (100) under
every alternative configuration? Overlap is still reported, as a share rather
than a Jaccard, but it does not gate. On the current data all three axes hold
100% of the decision set and the verdict is **stable**.

**The residual uncertainty is one-dimensional.** Overlap against the declared
weights as occupation's weight varies: 0.20 -> 75%, 0.35 -> 92%, 0.45 -> 93%,
0.55 -> 84%, 0.62 -> 71%, 0.70 -> 61%. The fitted schemes push occupation to
~0.62 because collinearity dumps deprivation's shared variance into it. So the
open question is not "which scheme" but **is high-risk-occupation share measuring
need, or measuring 'working-class male area'?** That is a domain judgement; no
further fitting will settle it, and a better outcome dataset did not move it.

**Output is now banded, not ranked** (`fact_tier.parquet`). Across the 20
configurations tested: **47 areas** are inside the top 100 under *every* one
(shortlist tier), **133** reach it under *some* (in contention). The evidence
separates the tiers; within a tier it does not separate the areas, so the map and
the PDF present a tier as jointly prioritised and leave the choice within it to
local judgement — which is the brief's human-in-the-loop guardrail made real
rather than asserted.

## Still open

- **Working-age suicide counts at LA level.** Blocked by disclosure control, not
  by data access. An ONS bespoke tabulation is the only route; the all-ages
  outcome is broader than the working-age population the proxies describe.
- **~3.4% of deaths lost** to the below-5 rule, falling disproportionately in
  small local authorities.
- **How much weight occupation should carry** — the one dimension the shortlist
  is genuinely sensitive to, and a domain question rather than a modelling one.
- **Public transport** is unmodelled; travel time is car-only. Since 2026-08-31
  the supply surface uses real OSRM road driving times rather than straight-line
  distance (median nearest group 13.8 min against the stub's 10.2). Notably the
  stub was wrong in *both* directions — its flat 40 km/h ignores motorways, so it
  over-stated the worst journeys (90th percentile 41.4 min against 35.1). The
  switch moved 4 of the per-capita top 20, all of which stayed inside the new top
  100. Public transport at session times has since been measured and deliberately
  left unscored — see [ADR 0002](0002-public-transport-feasibility-spike.md).
  It remains the real gap for a population that disproportionately lacks cars.

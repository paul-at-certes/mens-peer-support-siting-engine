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
occupation. Deprivation therefore comes out *significantly protective* in the
multivariable fit (rate ratio 0.93, CI excluding 1) while being positively
associated on its own (RR 1.11). That is a collinearity artefact, not
epidemiology, and it zeroes deprivation under a coefficients-as-weights rule.

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

## Still open

- **ONS registrations** (male, working-age, 5-year pooled, England **and**
  Wales) should replace Fingertips 41001 as the outcome. Manual download; until
  then the check is England-only, age 10+, 3-year pooled, and Welsh areas are
  ranked on their proxies with a neutral suicide term.
- **Public transport** is unmodelled; travel time is car-only.

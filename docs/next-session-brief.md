# Next session — where to pick up

**Written 2026-08-31, rewritten 2026-09-01.** State of `main` after PR #5 merged,
plus the concordance work on `backtest-concordance-with-existing-groups` (PR #6).
Everything here was verified against the real England & Wales run, not inferred.

---

## Where things stand

v1's definition of done was already met before this session. This session added
the validation the repo could not previously do: **does the surface agree with
where AMC already opened groups?** It does, about neighbourhoods; it does not,
about towns. See [ADR 0003](adr/0003-concordance-with-existing-groups.md).

Current figures, so you don't re-derive them:

| | |
|---|---|
| areas / LAs | 35,672 LSOAs, 331 local authorities |
| existing groups | 354 harvested, 292 inside the England & Wales surface |
| flagged blind spots | 285 (0 reach the per-capita shortlist under any configuration; **1** reaches the reach shortlist under some) |
| veto status | `collinearity` — deprivation cannot be shown positive in the multivariable fit |
| routing | OSRM default, median nearest group 13.8 min |
| stability | STABLE on all three axes |
| concordance | within-LA 64.1st percentile (null 50, p < 5e-05); between-LA p = 0.21 once region is held constant |
| tests | 127 |

**Nothing in the model moved.** No weight, veto, rank, tier or output changed —
`weights.json`, `sensitivity.json` and `fact_score.parquet` are untouched, so no
re-run of `calibrate.py`, `sensitivity.py` or the pipeline is needed. The
concordance work is a spike, documentation, and caveat copy — no scored column
moved.

---

## 1. What was measured, and what it means

`spikes/group_need_concordance.py` places all 354 AMC groups in a small area by
postcode (ONS Postcode Directory, May 2026) and asks four questions. Full detail
and the raw tables are in ADR 0003; the short version:

- **Within a local authority, the surface and AMC agree.** A venue's own small
  area averages the 64.1st percentile of `need_index` among its LA's areas
  (null 50, p < 5e-05, 20,000 draws). 71.2% are above their LA's midpoint. This
  never compares one LA with another, so founder geography cannot explain it.
- **Between local authorities, the agreement is entirely regional.** The
  national result (+0.064, p < 5e-05) collapses to **p = 0.21** when the
  permutation is restricted to within-region. AMC's heartland is saturated —
  North East 12/12 LAs, Yorkshire and The Humber 21/21 — and where there *is* a
  choice the direction is mixed, negative in Wales, the South East and the North
  West. **The between-town judgement is untested**, and that is the judgement a
  national shortlist is being asked for.
- **A venue's own area over-states its catchment by 10 points.** Venue areas
  average the 68.0th national need percentile; the areas they are nearest to
  (median catchment 54 areas) average the 57.7th. Now a stated limitation in
  `design.md` §8: a rank names a pocket, a group serves a catchment.
- **All three components are corroborated**, in almost the inverse of their
  declared weights — isolation 68.0, deprivation 62.2, occupation 57.5. Left
  alone deliberately: ADR 0001 forbids a fitted quantity setting a declared
  prior, and this is one. It is a question for the face-validity conversation.

### The trap, and why the numbers use `need_index`
`priority_score` subtracts a supply surface built **from these same groups**.
The artefact is large — the same 292 venue areas sit at the 68th percentile of
`need_index` and the **25th** of `priority_score`. Everything reported uses
`need_index` and its components only.

### Two assignment findings worth remembering
- **Nearest population-weighted centroid agrees with the postcode only 47.9% of
  the time.** It is the obvious shortcut for placing a point in an LSOA and it
  does not work at neighbourhood grain. Nothing in `src/` does this (the
  pipeline uses group coordinates for *distance* only) — keep it that way.
- **Six group postcodes are hand-entered without their space** (`SS155NX`).
  An exact join drops them silently, and a dropped group is indistinguishable
  from a Scottish one. `normalise_postcode` handles it; recovered 2 E&W groups.
  It deliberately refuses to mangle a truncated entry into a plausible match.

---

## 2. Settled — do not re-litigate

- Weights are a **declared prior**; calibration vetoes, never supplies (ADR 0001).
  **This now explicitly covers the concordance ranking in finding 4** — it is a
  fitted quantity wearing a different hat.
- No small-area suicide rate, ever. Outcome stays at LA grain.
- Within-nation normalisation for deprivation.
- `no_car_share`, RUC21 and the blind-spot flag are **descriptive**, attached
  after `apply_weights` returns, outside `prepare_components`. Keep them there.
- Public transport is measured and deliberately unscored (ADR 0002).
- Suicide counts are male **all ages**, not working age — measured, not assumed.
- The deprivation coefficient's sign is stable and its significance is not; the
  repo claims only the former.
- Concordance is **not** the back-test. `design.md` §7 check 4 needs opening
  dates and attendance figures. Do not let the two blur in any copy.
- The concordance spike **stays a spike**: it is not a pipeline step and
  `python -m src.pipeline` does not run it. It *is* on the app face and in the
  PDF, read live from `concordance.json` when that file exists and reported as
  "not run" when it does not (ADR 0003 decision 5, amended the same day). No
  figure is typed into a page. Do not reinstate the ban on app copy.
- Both halves of the concordance finding go together. Copy that reports the
  64th-percentile result without the untested between-town half is true
  sentence by sentence and misleading overall; `tests/test_concordance_caveats.py`
  exists to stop it.

---

## 3. Start here

Nothing is blocked. Three candidates, in the order I'd take them:

0. **Nothing is half-done.** The app, the PDF and the guide all carry the
   concordance finding, both halves of it. The three below are new work.

1. **The face-validity conversation (`design.md` §7 check 1).** This is now the
   highest-value move and ADR 0003 sharpened what it is *for*: the between-town
   judgement is the part the data cannot check, and the top of the shortlist is
   mostly towns AMC is not in. Take the PDF (`python -m src.report`), the top 20,
   and finding 4's specific question — the weighting leans hardest on
   deprivation, AMC's own choices lean hardest on isolation. Who is right?
2. **Ask AMC for opening dates.** The single piece of non-open data that would
   most improve the tool: it turns concordance into a real back-test, closing
   the last open check in §7. Worth asking for even if the answer is slow.
3. **An occupation-led view.** 285 areas carry at least national-average
   occupational risk while `need_index` puts them in its bottom half; the
   weighting outvotes occupation roughly two to one, so they structurally
   cannot surface. Today that is a flag saying "we know". The remoteness view
   is the precedent for re-ranking a subset without re-scoring it. Finding 4
   makes this more interesting, not less: occupation is corroborated, just least.

Scotland/NI remain correctly stubbed. 61 of the 354 groups are already up there,
so the data to check concordance in Scotland exists the moment the surface does.

---

## 4. Running it

```bash
.venv/bin/python -m pytest -q                        # 127 tests, no network
.venv/bin/python -m src.pipeline                     # needs OSRM up, or set provider: haversine
.venv/bin/python -m src.report                       # PDF -> data/output/
.venv/bin/python spikes/group_need_concordance.py    # concordance; first run fetches ONSPD (~4 min)
.venv/bin/streamlit run app/streamlit_app.py
```

OSRM (only needed on the first run after geography or provision changes — the
matrix is cached):

```bash
docker start amc-osrm    # or the full docker run in README, "Real routing (OSRM)"
```

Set `mode: synthetic` in `config.yaml` for an instant, fully offline run.

# Next session — where to pick up

**Written 2026-08-31, updated 2026-09-01.** State of `main` after PR #4 merged,
plus the one follow-up finding on `count-declined-harvest-requests` (PR #5).
Everything here was verified against the real England & Wales run, not inferred.

---

## Where things stand

Everything on the previous brief's list is closed. Both open judgement calls were
decided (documentation-side, in both cases), and all six unfixed review findings
are fixed. The branch's own review found one more, also fixed. 99 tests pass, up
from 90.

Current figures, so you don't re-derive them:

| | |
|---|---|
| areas / LAs | 35,672 LSOAs, 331 local authorities |
| flagged blind spots | 285 (0 reach the per-capita shortlist under any configuration; **1** reaches the reach shortlist under some) |
| veto status | `collinearity` — deprivation cannot be shown positive in the multivariable fit |
| routing | OSRM default, median nearest group 13.8 min |
| stability | STABLE on all three axes |
| tests | 99 |

Unchanged by this session: every weight, every veto, every rank. Nothing in
`weights.json`, `sensitivity.json` or `fact_score.parquet` moved, so no re-run of
`calibrate.py` or `sensitivity.py` was needed. `occupation_diagnostic.json` and
the PDF were regenerated (the diagnostic gained one field; the PDF was rebuilt to
exercise the changed `report.py` signature).

---

## 1. What was decided, and what it cost

### 1a. The "significantly protective" claim — softened, not refitted

**Decision: soften.** The claim held only in the specification that pools
England and Wales on a within-nation rescaled deprivation axis, and marginally
there (p=0.044). The repo now claims what survives all three specifications:
*deprivation cannot be shown to be positive once the other two are in the model.*
The sign is stable everywhere; the significance is not.

The refit table and the reason (`deprivation_proxy` averages 0.499 in both
nations by construction while Wales' pooled male rate is 28% higher, so the
pooled fit explains Welsh excess deaths with a variable flattened at the border)
are now recorded in `docs/adr/0001-calibration-as-veto.md`, so the softening is
evidenced rather than merely quieter. `CLAUDE.md`, `README.md` and
`src/calibrate.py` carry the softened wording.

The alternative — adding a Wales dummy to the fit — was not taken. It would have
changed a diagnostic and forced a `sensitivity.py` re-run, to reach the same
conclusion by a longer route: the argument for the declared-prior architecture
rests on non-identification, which every specification agrees on.

### 1b. `outvoted_note` no longer generalises from ten areas

**Decision: reframe.** `occupation_diagnostic.run` now records `n_examined`
alongside the ranks, and `caveats.outvoted_note` reads it: *"The ten clearest
cases ... Those ten sit around 11,763rd of 35,672 here ... — where those
particular areas land, rather than a measured claim about every place like
them."* The general claim about the class is left to the blind-spot flag in the
same paragraph, which tests all 35,672 areas.

Widening the residual set was the alternative. It was not taken because a wider
arbitrary set is still an arbitrary set: the flag is the part entitled to speak
for the class, and it already does.

A diagnostic written before `n_examined` existed degrades to "The clearest cases
... Those few sit around", never to an invented number. Both paths are tested.

---

## 2. Review findings — all fixed

- **`src/fetch.py`** — `nomis_csv_all` no longer caches an empty frame; it warns
  and returns, so the caller's own empty check fires on a fresh miss instead of
  on a poisoned cache. `cached_csv` and `arcgis_count` (both dead) are gone.
- **`src/ingest/provision.py`** — `_harvest` counts failed grid requests, prints
  the count and the first five with their coordinates, and raises
  `MissingSourceError` above `HARVEST_MAX_FAILURE_RATE` (5%) **without writing
  the cache**. The 5% bar is set from the grid's own overlap: a 25-mile radius on
  a ~20–33km step means an isolated miss is covered by its neighbours, so a rate
  above a few percent means failures are clustered, and a clustered gap is a
  region of groups that silently vanished. Also: `open_status` now falls back to
  a Series, not a bare `"OPEN"` string that would hit `.map` and raise.
- **`src/ingest/provision.py`, second pass** — found reviewing the above. The
  failure count only saw requests that *raised*. A 200 whose body parses as JSON
  but is not a list of stores — `{"success": false}`, an error object, a
  rate-limit notice — fell through the `isinstance` check uncounted, so the site
  could decline every request in valid JSON while the harvest reported 0.0%
  failed and cached a fraction of the groups. Same silent understatement of
  provision, through the one door the guard left open. Now counted, with the
  first 80 characters of the response in the printed reason.
- **`src/report.py`** — `_remote_and_blind_spot` is down from 17 positional
  parameters to 11; the six reportlab names are re-imported inside the function
  (the caller has already run `_require_reportlab()`, so it is a dict lookup).
  The style objects still come in as arguments — they are built once and must be
  the same objects.
- **`docs/design.md`** — §2 and §3 no longer state the superseded design. §2
  reads "Check at LA level" with an amendment box pointing at ADR 0001; §3 now
  says three weighted components, not four, explains that the suicide signal is
  **not** a component and is male **all ages**, and describes the shipped
  SMR-weighted occupation proxy rather than the pre-SMR one.
- **Both spec files** moved to `docs/` (per `CLAUDE.md`'s structure) and their
  status lines now say **built and shipped**. In-code references updated.

New tests: `tests/test_silent_data_loss.py` (7) covers the two silent-failure
paths, the declined-but-parseable response, and the missing-column fallback;
`tests/test_occupation_diagnostic.py` gained 2 for the reframed caveat.

---

## 3. Settled — do not re-litigate

- Weights are a **declared prior**; calibration vetoes, never supplies (ADR 0001).
- No small-area suicide rate, ever. Outcome stays at LA grain.
- Within-nation normalisation for deprivation.
- `no_car_share`, RUC21 and the blind-spot flag are **descriptive**, attached
  after `apply_weights` returns, outside `prepare_components`. Keep them there.
- Public transport is measured and deliberately unscored (ADR 0002).
- Suicide counts are male **all ages**, not working age — measured, not assumed.
- The deprivation coefficient's sign is stable and its significance is not; the
  repo claims only the former (§1a). The stronger claim is not to be reinstated
  without the nation-adjusted refit that would justify it.

---

## 4. Start here

Nothing is outstanding. PR #4 — the softened claim, the reframed caveat, the
review fixes — is merged. PR #5 is the one finding its own review turned up
(§2, last bullet); once that lands, §1 and §2 are history rather than work, and
the next session starts from whatever you want to build.

Nothing here needs a re-run: no weight, veto or rank has moved since the figures
in the table above were measured.

---

## 5. Running it

```bash
.venv/bin/python -m pytest -q            # 98 tests, no network
.venv/bin/python -m src.pipeline         # needs OSRM up, or set provider: haversine
.venv/bin/python -m src.report           # PDF -> data/output/
.venv/bin/streamlit run app/streamlit_app.py
```

OSRM (only needed on the first run after geography or provision changes — the
matrix is cached):

```bash
docker start amc-osrm    # or the full docker run in README, "Real routing (OSRM)"
```

Set `mode: synthetic` in `config.yaml` for an instant, fully offline run.

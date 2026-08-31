# `spikes/` — throwaway research code

Nothing in here is imported by `src/` or `app/`, and nothing here runs as part
of `python -m src.pipeline`. These scripts exist so that a claim made in an ADR
can be re-derived rather than taken on trust.

A spike graduates by being **deleted** and replaced with a real module under
`src/`, or by being recorded as a dead end in the ADR that commissioned it.

| Spike | Commissioned by | Question | Verdict |
|---|---|---|---|
| `pt_evening_access.py` | `docs/adr/0002-public-transport-feasibility-spike.md` | Can a man get to a Monday-evening group by bus and back? | Data exists; not yet fit to score. See ADR 0002. |

## `pt_evening_access.py`

```bash
# the headline run — national feed, urban vs rural contrast
python spikes/pt_evening_access.py --las "Nottingham|Boston|Newark and Sherwood|Mansfield"

# does the answer survive changing what counts as an acceptable journey?
python spikes/pt_evening_access.py --las "Nottingham|Boston" --sweep

# faster iteration on one BODS region (WARNING: truncates cross-boundary journeys)
python spikes/pt_evening_access.py --feed east_midlands --las Nottingham
```

The first run downloads the ~1.4GB national GTFS from the Bus Open Data Service
and caches it under `data/raw/real/gtfs/` (git-ignored). No API key is needed.

It answers a **round trip**, not a travel time: can you arrive by 19:00 having
left home no earlier than 17:30, *and* get home after the 21:00 finish? The last
bus home is usually what actually binds, and a one-way number scores an area as
well-served when nobody can get back.

Method is a Connection Scan over the timetable, two scans **per group** —
backward from the group arriving 19:00, forward from it leaving 21:00 — with
origins joining on by a walk to nearby stops. Cost grows with the ~354 groups,
not the 35,672 small areas, which is the only shape that scales.

Read the limitations in the module docstring before quoting any number from it.
The important ones: BODS is a **bus** feed and GB rail is effectively absent, so
rural feasibility is understated; and walk access is straight-line, so
walkability is overstated.

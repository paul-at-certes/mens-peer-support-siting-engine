"""The occupational blind spot — naming what the ranking cannot see.

    DESCRIPTIVE ONLY. The flag is derived FROM a settled score and never feeds
    one. ``flag()`` is called in score.py after apply_weights() has run, in the
    same place and for the same reason as ``no_car_share``: outside
    prepare_components(), so no weighting scheme, tier or sensitivity draw can
    reach it. The arrow points one way — the flag reads need_index; need_index
    has never heard of the flag.

WHAT IT MARKS

An area whose men do work carrying at least the national-average male suicide
risk, and which the composite need index nonetheless places in its bottom half.
That is the blind spot described in occupational-risk-layer-spec.md 11.1: with
deprivation at 0.40 and isolation at 0.25 against occupation's 0.35, the two of
them outvote occupation nearly two to one, and areas that score high on
occupation alone tend to score low on both. Occupation cannot carry an area on
its own by construction, whatever the work there is.

The flag is keyed off the FACTOR PROFILE, not off geography. It is mostly remote
(the largest single council is Powys) but it also catches outer-London
neighbourhoods with large skilled-construction workforces and low measured
income deprivation — Brent and Harrow both appear. "Remote" is a legible label
for the view in ingest/remoteness.py; this is a different question and gets a
different name.

THE THRESHOLD, AND WHY IT IS THIS ONE

CLAUDE.md requires a threshold set from what the number means for the decision,
never tuned until the data passes. Both halves are read off quantities that
already mean something; neither was moved after seeing the count.

*Occupation index >= 1.00.* occupation_proxy is a sum of male occupational
shares weighted by the measured male suicide SMR of each group, so 1.00 is not a
percentile or a cut — it is the identity value of the index. An area at 1.00 has
an occupational mix carrying exactly the average male suicide risk for men in
work in England and Wales, an average that includes every desk job in the
country. Above it, more. That is the weakest claim worth flagging on, and it
cannot be nudged: there is nowhere for it to go.

Two things about it are worth stating. It lands at the 80th percentile
nationally, which is also the bar report.py already uses to call a factor "high"
in the plain-English reasoning it prints for every shortlisted area, so the flag
and the breakdown table beside it use the word "high" to mean the same thing. And
it is deliberately an ABSOLUTE cut, not a within-nation percentile. CLAUDE.md's
within-nation rule exists because IMD, WIMD and SIMD are not comparable across
borders; this index is, being one census and one national SMR schedule. Wales
genuinely has more of these areas — 41% of Welsh LSOAs sit above the national
average against 18% of English ones — and a within-nation percentile would hide
that behind the border rather than report it.

*need_index < 0.50.* The index runs 0 to 1 and is built from percentile ranks,
so its midpoint is 0.5 by construction and its median is 0.4986 on this run. This
is the ranking's own verdict, in its own units: below-average need. It replaces
the separate "deprivation low AND isolation low" cuts an earlier exploration
used, because those were two more invented constants standing in for the thing
they were trying to detect. This measures the mechanism directly — whatever
combination of the other factors did the outvoting. In practice it selects the
profile 11.1 describes anyway: flagged areas sit at a median 23rd percentile on
deprivation and 16th on isolation.

WHAT THE FLAG DOES NOT SAY

It does not say a group should open there. It says the need index is blind
there, which is a statement about this tool, not about the place. Supply is a
separate and entirely legitimate reason for a low rank, and some flagged areas
are well served already — which is why the flag is reported wherever an area
ranks and is never combined with the ranking into a score.

Run standalone with:  python -m src.blindspot
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd

from .config import Config, load_config

# The identity value of the SMR-weighted occupational composition index: an
# occupational mix carrying the national-average male suicide risk. See above.
OCCUPATION_INDEX_FLOOR = 1.00

# The midpoint of an index built from percentile ranks. See above.
NEED_INDEX_CEILING = 0.50

THRESHOLD_STATEMENT = (
    f"occupation_proxy >= {OCCUPATION_INDEX_FLOOR:.2f} (an occupational mix carrying at "
    f"least the national-average male suicide risk — the identity value of the index, "
    f"not a percentile) AND need_index < {NEED_INDEX_CEILING:.2f} (the midpoint of an "
    f"index built from percentile ranks — the ranking's own verdict of below-average "
    f"need). Neither number was chosen after seeing the count."
)


def flag(occupation_proxy, need_index) -> pd.Series:
    """The occupational blind spot, elementwise. Pure: no config, no I/O, no rank.

    High occupational risk in absolute terms, and a composite index that still
    places the area in its bottom half.
    """
    occ = pd.Series(np.asarray(occupation_proxy, dtype="float64"))
    need = pd.Series(np.asarray(need_index, dtype="float64"))
    return ((occ >= OCCUPATION_INDEX_FLOOR) & (need < NEED_INDEX_CEILING)).to_numpy()


def summarise(df: pd.DataFrame) -> dict:
    """Describe the flagged set, given a scored frame carrying the flag column."""
    n = len(df)
    m = df[df["occupation_blind_spot"]]
    out: dict = {
        "threshold": {
            "occupation_index_floor": OCCUPATION_INDEX_FLOOR,
            "need_index_ceiling": NEED_INDEX_CEILING,
            "statement": THRESHOLD_STATEMENT,
        },
        "n_areas": int(n),
        "n_flagged": int(len(m)),
        "share_flagged": round(len(m) / max(n, 1), 5),
        "descriptive_only": ("derived from a settled score; never enters need_index, "
                             "supply_index, priority_score, the tiers or the "
                             "sensitivity draws"),
    }
    if not len(m):
        return out
    out["ranking"] = {
        "median_rank": int(m["rank"].median()),
        "best_rank": int(m["rank"].min()),
        "n_inside_top_100": int((m["rank"] <= 100).sum()),
        # The claim the flag makes about itself: it is not restating the
        # shortlist. If flagged areas were already being surfaced there would be
        # nothing to name.
        "n_in_shortlist_tier": (int((m.get("tier") == "shortlist").sum())
                                if "tier" in m.columns else None),
    }
    out["profile"] = {
        "median_male_working_age_pop": int(m["male_working_age_pop"].median()),
        "median_travel_minutes": round(float(m["travel_minutes"].median()), 1),
        "median_supply_index": round(float(m["supply_index"].median()), 3),
        "all_areas_median_supply_index": round(float(df["supply_index"].median()), 3),
    }
    if "is_remote" in m.columns:
        out["profile"]["share_remote"] = round(float(m["is_remote"].mean()), 4)
    out["by_nation"] = {str(k): int(v) for k, v in m["nation"].value_counts().items()}
    out["top_councils"] = [{"la_name": str(k), "n": int(v)}
                           for k, v in m["la_name"].value_counts().head(10).items()]
    return out


def run(cfg: Config | None = None) -> dict:
    """Summarise the flag from fact_score.parquet, print it, record it.

    Non-blocking in the pipeline: it reports on a ranking that is already
    finished, so a failure here must not cost anyone the shortlist.
    """
    cfg = cfg or load_config()
    df = pd.read_parquet(cfg.path("fact_score"))
    if "occupation_blind_spot" not in df.columns:
        raise ValueError("blindspot: fact_score.parquet carries no occupation_blind_spot "
                         "column. Re-run score.py.")
    tier_path = cfg.path("fact_tier")
    if tier_path.exists():
        df = df.merge(pd.read_parquet(tier_path)[["area_code", "tier"]],
                      on="area_code", how="left")
    report = summarise(df)

    print("\n[blind-spot] ===== areas the need index cannot see =====")
    print(f"  rule: {THRESHOLD_STATEMENT}")
    print(f"  flagged: {report['n_flagged']:,} of {report['n_areas']:,} areas "
          f"({report['share_flagged']:.2%})")
    if report["n_flagged"]:
        r, p = report["ranking"], report["profile"]
        print(f"  they rank at a median of {r['median_rank']:,}, best {r['best_rank']:,}; "
              f"{r['n_inside_top_100']} inside the top 100")
        print(f"  median nearest group {p['median_travel_minutes']:.0f} min, supply "
              f"{p['median_supply_index']:.2f} against {p['all_areas_median_supply_index']:.2f} "
              f"for all areas"
              + (f"; {p['share_remote']:.0%} remote" if "share_remote" in p else ""))
        print("  concentrated in: " + ", ".join(
            f"{c['la_name']} {c['n']}" for c in report["top_councils"][:8]))
        print(f"  by nation: " + ", ".join(f"{k} {v}" for k, v in report["by_nation"].items()))
    print("  -> descriptive only; it changes no rank.")

    out = cfg.path("blind_spot")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2))
    print(f"  -> {out}\n")
    return report


if __name__ == "__main__":
    run()

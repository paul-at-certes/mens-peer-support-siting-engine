"""Unit tests for the concordance spike's statistics.

``spikes/`` is throwaway research code and is not generally tested — but the
same exception applies here as to ``pt_evening_access.py``: every number in
ADR 0003 comes out of these three functions, so they are tested against cases
whose answer can be worked out by hand.

What is being guarded:
  * the midrank percentile, including ties (a real case — many small areas
    share a rounded component percentile);
  * that the within-LA permutation is calibrated — venues placed at random
    within their LA must NOT look significant, or every result it reports is
    an artefact;
  * that stratifying the between-LA permutation actually holds the stratum
    constant, which is the whole defence against founder geography.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from spikes.group_need_concordance import (  # noqa: E402
    between_la_test,
    nearest_centroid,
    normalise_postcode,
    within_group_percentile,
    within_la_test,
)


# ---------------------------------------------------------------------------
# within_group_percentile
# ---------------------------------------------------------------------------
def test_percentile_of_the_smallest_and_largest():
    pool = np.array([1.0, 2.0, 3.0, 4.0])
    # Smallest: nothing below, itself is the one tie -> 0.5/4.
    assert within_group_percentile(pool, 1.0) == pytest.approx(0.125)
    # Largest: three below, itself the tie -> 3.5/4.
    assert within_group_percentile(pool, 4.0) == pytest.approx(0.875)


def test_percentile_uses_midrank_for_ties():
    # Four areas share a value; the midrank puts them at the middle of the block
    # they occupy, not at its top. 2 below, 4 tied -> (2 + 2)/8.
    pool = np.array([0.0, 0.1, 5.0, 5.0, 5.0, 5.0, 9.0, 9.9])
    assert within_group_percentile(pool, 5.0) == pytest.approx(0.5)


def test_percentile_mean_of_a_full_pool_is_one_half():
    # Scoring every member of a pool against the pool must average to 0.5 --
    # this is exactly the null the permutation relies on.
    pool = np.array([3.0, 1.0, 4.0, 1.0, 5.0, 9.0, 2.0])
    scores = [within_group_percentile(pool, v) for v in pool]
    assert float(np.mean(scores)) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# within_la_test
# ---------------------------------------------------------------------------
def _la_frame(n_las: int = 12, per_la: int = 25, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    return pd.DataFrame({
        "area_code": [f"E{i:08d}" for i in range(n_las * per_la)],
        "la_code": np.repeat([f"E06{i:06d}" for i in range(n_las)], per_la),
        "need_index": rng.random(n_las * per_la),
    })


def test_within_la_detects_venues_placed_at_the_top():
    scored = _la_frame()
    placed = (scored.sort_values("need_index").groupby("la_code").tail(1)
              .reset_index(drop=True))
    res = within_la_test(placed, scored, "need_index", draws=400, seed=1)
    assert res["n_groups"] == 12
    assert res["mean_percentile"] > 0.95
    assert res["p_one_sided"] < 0.01


def test_within_la_is_calibrated_on_venues_placed_at_random():
    """The null must not manufacture a finding. Venues drawn at random from
    their own LA should land near the 50th percentile with an unremarkable p."""
    scored = _la_frame(n_las=30, per_la=40, seed=5)
    placed = scored.groupby("la_code", as_index=False).sample(n=1, random_state=9)
    res = within_la_test(placed, scored, "need_index", draws=600, seed=2)
    assert 0.35 < res["mean_percentile"] < 0.65
    assert res["p_one_sided"] > 0.05


def test_within_la_skips_local_authorities_with_one_small_area():
    """An LA with a single small area carries no within-LA information: its
    venue is trivially at the 50th percentile of a pool of one. It must be
    dropped, not counted as a neutral observation that dilutes the result."""
    scored = pd.DataFrame({
        "la_code": ["A", "A", "A", "B"],
        "need_index": [0.1, 0.2, 0.9, 0.5],
    })
    placed = pd.DataFrame({"la_code": ["A", "B"], "need_index": [0.9, 0.5]})
    res = within_la_test(placed, scored, "need_index", draws=50, seed=4)
    assert res["n_groups"] == 1


# ---------------------------------------------------------------------------
# between_la_test
# ---------------------------------------------------------------------------
def _region_frame() -> pd.DataFrame:
    """Need is driven ENTIRELY by region, and every group sits in the high-need
    region. Nationally that looks like a huge effect; within region there is
    nothing at all. This is the founder-geography confound in miniature."""
    rows = []
    for i in range(20):
        rows.append({"la_code": f"N{i}", "region": "North", "la_need": 0.80,
                     "has_group": i < 10})
    for i in range(20):
        rows.append({"la_code": f"S{i}", "region": "South", "la_need": 0.20,
                     "has_group": False})
    return pd.DataFrame(rows)


def test_between_la_national_permutation_sees_the_confound():
    res = between_la_test(_region_frame(), draws=500, seed=1)
    assert res["difference"] == pytest.approx(0.4, abs=0.05)
    assert res["p_one_sided"] < 0.01


def test_between_la_stratified_permutation_holds_region_constant():
    """Same data. Permuting only within region must find no effect, because
    within a region every LA has identical need."""
    res = between_la_test(_region_frame(), draws=500, seed=1, stratify_by="region")
    assert res["difference"] == pytest.approx(0.4, abs=0.05)
    # Every stratified draw reproduces the observed difference exactly, so the
    # permutation p is 1.0 -- the effect is fully explained by region.
    assert res["p_one_sided"] > 0.9
    assert res["null_difference_mean"] == pytest.approx(res["difference"], abs=0.05)


# ---------------------------------------------------------------------------
# nearest_centroid
# ---------------------------------------------------------------------------
def test_nearest_centroid_returns_the_area_a_group_sits_exactly_on():
    geo = pd.DataFrame({
        "area_code": ["E01", "E02", "E03"],
        "centroid_lon": [-1.5, -0.1, -3.2],
        "centroid_lat": [53.8, 51.5, 55.9],
    })
    groups = pd.DataFrame({"lon": [-0.1, -3.2], "lat": [51.5, 55.9]})
    assert list(nearest_centroid(groups, geo)) == ["E02", "E03"]


def test_nearest_centroid_picks_the_closer_of_two_nearby_areas():
    geo = pd.DataFrame({
        "area_code": ["near", "far"],
        "centroid_lon": [-1.500, -1.520],
        "centroid_lat": [53.800, 53.800],
    })
    groups = pd.DataFrame({"lon": [-1.502], "lat": [53.800]})
    assert list(nearest_centroid(groups, geo)) == ["near"]


# ---------------------------------------------------------------------------
# normalise_postcode
# ---------------------------------------------------------------------------
def test_normalise_recovers_a_postcode_entered_without_its_space():
    """The failure this guards is silent: an unmatched postcode is simply absent
    from the England & Wales analysis, which is indistinguishable from a group
    being in Scotland. Five real AMC entries were this."""
    assert normalise_postcode("SS155NX") == "SS15 5NX"
    assert normalise_postcode("LL138DG") == "LL13 8DG"
    assert normalise_postcode("DD97EB") == "DD9 7EB"


def test_normalise_leaves_an_already_correct_postcode_alone():
    assert normalise_postcode("HD3 3RH") == "HD3 3RH"
    assert normalise_postcode(" np7 5nd ") == "NP7 5ND"


def test_normalise_collapses_stray_internal_whitespace():
    assert normalise_postcode("GU11  1TW") == "GU11 1TW"


def test_normalise_does_not_invent_a_postcode_from_a_truncated_entry():
    """One group's postcode is recorded as "AB54 8J" -- seven characters, not a
    postcode. Splitting it three-from-the-end would yield the plausible-looking
    "AB5 48J", which could match a real and quite different place. Failing to
    match is the correct outcome; mangling it into a match is not."""
    assert normalise_postcode("AB54 8J") == "AB5 48J"
    assert normalise_postcode("AB1") == "AB1"
    assert normalise_postcode("") == ""

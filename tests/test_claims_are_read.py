"""Claims about the flagged areas must be READ from the run, never asserted.

Four sentences on the map, in the guide and in the PDF used to state flatly that
no occupational blind spot reaches the shortlist. Nothing computed it, so nothing
checked it — and it was already false for the reach view the PDF leads with by
default, where one flagged area reaches rank 21 under an alternative
configuration. These tests pin the shape of the fix: the figure is recorded by
blindspot.summarise, every surface reads it, and the wording changes with it.

The fifth test covers a different failure of the same kind — sensitivity.py's
supply-sweep guard could not fire, so a skipped axis was recorded as an empty
one with no reason attached.
"""

import json

import pandas as pd
import pytest

from src import blindspot, caveats, report, sensitivity
from src.config import Config, load_config


def _flagged_frame(tiers, reach_tiers):
    """A scored frame carrying the columns summarise() reads."""
    n = len(tiers)
    return pd.DataFrame({
        "area_code": [f"E0100000{i}" for i in range(n)],
        "la_name": ["Powys"] * n, "nation": ["W"] * n,
        "occupation_blind_spot": [True] * n,
        "rank": list(range(500, 500 + n)),
        "male_working_age_pop": [400] * n,
        "travel_minutes": [27.0] * n, "supply_index": [0.17] * n,
        "tier": tiers, "rank_best": [400] * n,
        "reach_tier": reach_tiers, "reach_rank_best": [21] * n,
    })


# --- 1. the figure is recorded, per view -----------------------------------

def test_summarise_records_both_views_across_configurations():
    df = _flagged_frame(["outside", "outside"], ["outside", "contention"])
    ac = blindspot.summarise(df)["ranking"]["across_configurations"]
    assert ac["per_capita"] == {"n_shortlist_tier": 0,
                                "n_reaching_under_some_config": 0,
                                "best_rank_any_config": 400}
    # The reach view disagrees with the per-capita one, which is the entire
    # reason it is recorded separately.
    assert ac["reach"]["n_reaching_under_some_config"] == 1


def test_summarise_says_not_measured_rather_than_none_without_tiers():
    df = _flagged_frame(["outside"], ["outside"]).drop(
        columns=["tier", "rank_best", "reach_tier", "reach_rank_best"])
    ac = blindspot.summarise(df)["ranking"]["across_configurations"]
    assert ac == {"per_capita": None, "reach": None}


# --- 2. the copy tracks it -------------------------------------------------

def _cfg(tmp_path, ranking, view="reach"):
    cfg = Config({"paths": {"occupation_diagnostic": "occ.json",
                            "blind_spot": "blind_spot.json"},
                  "report": {"view": view}}, root=tmp_path)
    cfg.path("blind_spot").write_text(json.dumps({
        "n_areas": 35672, "n_flagged": 285, "share_flagged": 0.008,
        "ranking": ranking, "threshold": {"statement": "the rule"}}))
    return cfg


NONE = {"n_shortlist_tier": 0, "n_reaching_under_some_config": 0}
SOME = {"n_shortlist_tier": 0, "n_reaching_under_some_config": 1,
        "best_rank_any_config": 21}
ALL = {"n_shortlist_tier": 3, "n_reaching_under_some_config": 3,
       "best_rank_any_config": 12}


def test_caveat_claims_none_only_when_the_run_says_none(tmp_path):
    body = caveats.outvoted_note(_cfg(tmp_path, {
        "best_rank": 5282, "across_configurations": {"per_capita": NONE}}))
    assert "not one of them reaches the shortlist under any configuration" in body


def test_caveat_stops_claiming_none_once_one_gets_through(tmp_path):
    body = caveats.outvoted_note(_cfg(tmp_path, {
        "best_rank": 5282, "across_configurations": {"per_capita": SOME}}))
    assert "none of them" not in body
    assert "not one of them" not in body
    assert "1 of them reaches the shortlist under some configurations" in body


def test_caveat_is_silent_rather_than_wrong_when_unmeasured(tmp_path):
    """The old wording asserted a fact the run had not established. Saying
    nothing is the honest fallback; the sentence still stands without it."""
    body = caveats.outvoted_note(_cfg(tmp_path, {
        "best_rank": 5282, "across_configurations": {"per_capita": None}}))
    assert "shortlist" not in body.split("It says the ranking cannot see")[1]


# --- 3. the PDF answers for the view it actually features ------------------

def test_pdf_clause_follows_the_featured_view(tmp_path):
    ranking = {"across_configurations": {"per_capita": NONE, "reach": SOME}}
    reach = report._across_config_clause(_cfg(tmp_path, ranking, "reach"), ranking)
    per_capita = report._across_config_clause(
        _cfg(tmp_path, ranking, "per_capita"), ranking)
    # The bug in one sentence: the per-capita answer was printed under a reach
    # ranking, where it is false.
    assert "<b>none</b>" in per_capita and "per-capita ranking" in per_capita
    assert "<b>none</b>" not in reach and "reach ranking" in reach


def test_pdf_clause_reports_not_measured_rather_than_none(tmp_path):
    ranking = {"across_configurations": {"per_capita": None, "reach": None}}
    clause = report._across_config_clause(_cfg(tmp_path, ranking), ranking)
    assert "not measured" in clause
    assert "<b>none</b>" not in clause


def test_pdf_clause_handles_areas_that_hold_the_shortlist(tmp_path):
    ranking = {"across_configurations": {"per_capita": ALL, "reach": ALL}}
    clause = report._across_config_clause(_cfg(tmp_path, ranking), ranking)
    assert "3" in clause and "every configuration tested" in clause


# --- 4. the guard that could not fire --------------------------------------

def test_supply_sweep_guard_returns_none_not_an_empty_generator():
    """_supply_variants held a `yield`, so its `return None` produced a generator
    that stopped immediately. The caller's `is None` branch was unreachable and a
    skipped axis was recorded as an empty one, losing the reason."""
    cfg = load_config()
    cfg._data["sensitivity"] = dict(cfg["sensitivity"], supply_sweep={})
    assert sensitivity._supply_variants(cfg) is None

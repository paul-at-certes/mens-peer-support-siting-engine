"""The factor-independence diagnostic: is a factor really deprivation in a hat?"""

import numpy as np
import pytest

from src.occupation_diagnostic import INDEPENDENT_SHARE_FLOOR, _fit


def test_a_factor_that_is_deprivation_restated_has_no_independent_share():
    """The failure the diagnostic exists to catch: if a factor is a linear
    restatement of deprivation, all of its variance is explained and none of it
    is new information, however large a weight it carries."""
    dep = np.linspace(0, 1, 200)
    twin = 0.3 + 0.5 * dep                      # same fact, different scale
    resid, r2 = _fit(twin, dep)
    assert r2 == pytest.approx(1.0)
    assert (1.0 - r2) < INDEPENDENT_SHARE_FLOOR
    assert np.allclose(resid, 0.0, atol=1e-9)


def test_an_unrelated_factor_is_entirely_independent():
    rng = np.random.default_rng(7)
    dep = rng.random(500)
    other = rng.random(500)
    _, r2 = _fit(other, dep)
    assert (1.0 - r2) > INDEPENDENT_SHARE_FLOOR


def test_residual_is_orthogonal_to_what_it_was_regressed_on():
    """By construction. If this drifts the fit is wrong and every independence
    number downstream is meaningless."""
    rng = np.random.default_rng(11)
    dep = rng.random(400)
    y = 0.6 * dep + 0.4 * rng.random(400)
    resid, _ = _fit(y, dep)
    assert np.corrcoef(resid, dep)[0, 1] == pytest.approx(0.0, abs=1e-9)


def test_partial_overlap_splits_variance_sensibly():
    dep = np.linspace(0, 1, 300)
    y = dep + np.tile([0.0, 0.35], 150)         # half signal, half something else
    _, r2 = _fit(y, dep)
    assert 0.0 < r2 < 1.0


def test_flat_factor_does_not_divide_by_zero():
    dep = np.linspace(0, 1, 50)
    resid, r2 = _fit(np.full(50, 0.5), dep)
    assert r2 == 0.0
    assert np.isfinite(resid).all()


# --- the map-face caveat ---------------------------------------------------
import json as _json

from src.config import Config
from src.caveats import _ordinal, outvoted_note


def test_ordinals_read_as_ranks():
    assert _ordinal(1) == "1st"
    assert _ordinal(2) == "2nd"
    assert _ordinal(3) == "3rd"
    assert _ordinal(4) == "4th"
    # the teens are the trap: 11/12/13 take 'th', not 'st/nd/rd'
    assert _ordinal(11) == "11th"
    assert _ordinal(12) == "12th"
    assert _ordinal(13) == "13th"
    assert _ordinal(21) == "21st"
    assert _ordinal(5203) == "5,203rd"
    assert _ordinal(11763) == "11,763rd"


def _cfg(tmp_path):
    return Config({"paths": {"occupation_diagnostic": "occupation_diagnostic.json",
                             "blind_spot": "blind_spot.json"}},
                  root=tmp_path)


def test_caveat_states_the_limit_even_with_no_diagnostic_file(tmp_path):
    """The claim is structural, so it must survive the diagnostic being absent —
    a missing file must never mean a silently missing caveat."""
    body = outvoted_note(_cfg(tmp_path))
    assert "will not appear near the top" in body
    assert "looking for separately" in body


def test_caveat_quotes_the_diagnostic_when_it_exists(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.path("occupation_diagnostic").write_text(_json.dumps({"outvoted": {
        "n_areas": 35672, "median_rank": 11763, "best_rank": 5203,
        "example_las": ["Powys", "Richmondshire", "Eden"],
    }}))
    body = outvoted_note(cfg)
    assert "Powys, Richmondshire, Eden" in body
    assert "11,763rd" in body and "5,203rd" in body
    assert "11,763th" not in body


# --- what those ranks are a claim ABOUT ------------------------------------
# The median and best rank come from the handful of areas with the largest
# occupation residual. Printed bare, they read as a fact about the whole class
# of outvoted places. The general claim belongs to the blind-spot flag, which
# tests every area; this paragraph must say which areas it is describing.

def test_caveat_says_how_many_areas_the_ranks_describe(tmp_path):
    cfg = _cfg(tmp_path)
    cfg.path("occupation_diagnostic").write_text(_json.dumps({"outvoted": {
        "n_areas": 35672, "median_rank": 11763, "best_rank": 5203, "n_examined": 10,
        "example_las": ["Powys", "Richmondshire", "Eden"],
    }}))
    body = outvoted_note(cfg)
    assert "ten clearest cases" in body
    assert "Those ten sit around" in body
    # And it must disclaim the generalisation it used to invite.
    assert "rather than a measured claim about every place like them" in body


def test_caveat_names_no_count_it_was_not_given(tmp_path):
    """Diagnostics written before the count existed must degrade to a vaguer
    sentence, never to a made-up number."""
    cfg = _cfg(tmp_path)
    cfg.path("occupation_diagnostic").write_text(_json.dumps({"outvoted": {
        "n_areas": 35672, "median_rank": 11763, "best_rank": 5203,
        "example_las": ["Powys"],
    }}))
    body = outvoted_note(cfg)
    assert "Those few sit around" in body
    assert "clearest cases" in body
    assert "rather than a measured claim about every place like them" in body

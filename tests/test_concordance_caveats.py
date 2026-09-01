"""The concordance copy on the map face and in the PDF.

Two things are being guarded. First, that a build WITHOUT the diagnostic says so
rather than falling silent — an unrun check is worth stating, and silence reads
as a check that passed. Second, and the reason this file exists at all: that the
uncomfortable half of ADR 0003 survives into the copy. The finding is that the
ranking is corroborated about which part of a town and unproven about which
town, and a shortlist is mostly a list of towns. Copy that reported only the
64th-percentile half would be true sentence by sentence and misleading overall.
"""

from __future__ import annotations

import json

import pytest
import yaml

from src.caveats import (assurance_notes, catchment_note, concordance_note,
                         data_caveats)
from src.config import Config


def _cfg(tmp_path, concordance: dict | None) -> Config:
    """A config whose paths point at tmp_path, optionally with the diagnostic."""
    base = yaml.safe_load(open("config.yaml"))
    base["paths"] = dict(base["paths"])
    base["paths"]["concordance"] = "concordance.json"
    if concordance is not None:
        (tmp_path / "concordance.json").write_text(json.dumps(concordance))
    return Config(base, root=tmp_path)


_MEASURED = {
    "inputs": {"groups_assigned": 292},
    "a_between_la": {
        "national": {"n_las_with_group": 151, "p_one_sided": 5e-05},
        "within_region": {"p_one_sided": 0.20809},
    },
    "b_within_la": {"mean_percentile": 0.6411, "p_one_sided": 5e-05},
    "c_venue_vs_catchment": {
        "venue_mean_national_percentile": 0.6801,
        "catchment_mean_national_percentile": 0.5773,
    },
}


# ---------------------------------------------------------------------------
# concordance_note
# ---------------------------------------------------------------------------
def test_reports_both_halves_when_the_between_town_check_fails(tmp_path):
    note = concordance_note(_cfg(tmp_path, _MEASURED))
    assert "292 groups" in note
    assert "64th rung" in note          # the half that passed
    assert "could not be shown" in note  # the half that did not
    assert "unproven about which town" in note


def test_never_reports_the_flattering_half_alone(tmp_path):
    """The failure mode this guards is a true-but-misleading note."""
    note = concordance_note(_cfg(tmp_path, _MEASURED))
    passed_at = note.index("64th rung")
    assert note.index("could not be shown") > passed_at, "the caveat must follow the claim"
    assert "needing local judgement" in note


def test_says_the_supply_side_is_deliberately_excluded(tmp_path):
    note = concordance_note(_cfg(tmp_path, _MEASURED))
    assert "need side only" in note
    assert "homework" in note


def test_claims_the_between_town_half_only_when_it_holds(tmp_path):
    """If a future harvest makes the within-region test significant, the copy
    must switch to claiming it — and must stop saying it could not be shown."""
    held = json.loads(json.dumps(_MEASURED))
    held["a_between_la"]["within_region"]["p_one_sided"] = 0.004
    note = concordance_note(_cfg(tmp_path, held))
    assert "could not be shown" not in note
    assert "compared only with themselves" in note


def test_says_so_plainly_when_the_check_has_not_run(tmp_path):
    note = concordance_note(_cfg(tmp_path, None))
    assert "Not run" in note
    assert "unproven about which town" not in note


def test_degrades_rather_than_raising_on_a_diagnostic_it_cannot_read(tmp_path):
    """A truncated or half-written JSON file must not take down the map."""
    (tmp_path / "concordance.json").write_text("{not json")
    base = yaml.safe_load(open("config.yaml"))
    base["paths"] = dict(base["paths"], concordance="concordance.json")
    assert "Not run" in concordance_note(Config(base, root=tmp_path))


def test_degrades_when_the_diagnostic_ran_but_reported_nothing_usable(tmp_path):
    note = concordance_note(_cfg(tmp_path, {"inputs": {"groups_assigned": 292}}))
    assert "did not report a usable result" in note


def test_a_config_predating_the_diagnostic_still_renders(tmp_path):
    """_diagnostic_path returns None for a config with no such key at all."""
    base = yaml.safe_load(open("config.yaml"))
    base["paths"] = {k: v for k, v in base["paths"].items() if k != "concordance"}
    assert "Not run" in concordance_note(Config(base, root=tmp_path))


# ---------------------------------------------------------------------------
# catchment_note
# ---------------------------------------------------------------------------
def test_catchment_note_states_the_limitation_without_the_diagnostic(tmp_path):
    """The qualitative half follows from the grain of the ranking, not from a
    measurement, so it must be stated whether or not the check has run."""
    note = catchment_note(_cfg(tmp_path, None))
    assert "concentration here" in note
    assert "%" not in note, "no figure may be invented when nothing was measured"


def test_catchment_note_adds_the_measured_figures_when_they_exist(tmp_path):
    note = catchment_note(_cfg(tmp_path, _MEASURED))
    assert "68%" in note and "58%" in note
    assert "concentration here" in note


# ---------------------------------------------------------------------------
# both surfaces
# ---------------------------------------------------------------------------
def test_both_notes_reach_the_map_face_and_the_pdf(tmp_path):
    """caveats.py is the single source for both renderers, so appearing in
    these two lists is what puts the finding on both surfaces."""
    cfg = _cfg(tmp_path, _MEASURED)
    assert any("pocket" in c["label"] for c in data_caveats(cfg))
    labels = [n["label"] for n in assurance_notes(cfg)]
    assert "Checked against groups that already exist" in labels


@pytest.mark.parametrize("payload", [None, _MEASURED])
def test_every_caveat_stays_markup_free(tmp_path, payload):
    """Each renderer bolds labels in its own dialect; stray markup would show
    through as literal asterisks in the PDF."""
    cfg = _cfg(tmp_path, payload)
    for entry in data_caveats(cfg) + assurance_notes(cfg):
        assert "**" not in entry["body"]
        assert "\n" not in entry["body"]


def test_a_missing_sensitivity_file_does_not_swallow_later_notes(tmp_path):
    """Regression. assurance_notes used to `return notes` as soon as the
    sensitivity diagnostic was absent, so anything appended after that point
    vanished from a build that had not run it — silently, and only on the builds
    least able to spare an assurance. Both branches must carry every note."""
    cfg = _cfg(tmp_path, _MEASURED)          # tmp_path has no sensitivity.json
    labels = [n["label"] for n in assurance_notes(cfg)]
    assert "Stability check" in labels
    assert "Checked against groups that already exist" in labels

"""The remoteness view and the occupational blind-spot flag.

Four things are worth testing here, and the second is the one that matters.

1. The flag's threshold logic, including both boundaries. The threshold is a
   claim about meaning (rural-lens-spec.md 5, src/blindspot.py) and a test is
   the only thing that stops it drifting into a tuned number later.
2. **Neither addition can reach a score.** Both are descriptive, and the whole
   design rests on that: remoteness is deliberately kept out of need_index
   because the supply surface already rewards distance, and scoring it would
   double-count. The test does not read the code for reassurance — it changes
   the remoteness input and asserts every score is byte-identical.
3. The synthetic path still runs end to end with no network.
4. The RUC join covers every area in the spine, with no nulls, and fails loudly
   rather than quietly under-covering.
"""

import json

import numpy as np
import pandas as pd
import pytest

from src import blindspot, caveats, pipeline, score
from src.config import load_config
from src.blindspot import (NEED_INDEX_CEILING, OCCUPATION_INDEX_FLOOR, flag)
from src.ingest import remoteness
from tests.test_pipeline import _temp_cfg


# --- 1. the threshold ------------------------------------------------------

def test_flag_requires_both_conditions():
    occ = [1.20, 1.20, 0.80, 0.80]
    need = [0.30, 0.70, 0.30, 0.70]
    assert list(flag(occ, need)) == [True, False, False, False]


def test_flag_boundaries_are_where_the_reasoning_puts_them():
    """>= on the occupation index, < on need. Exactly at 1.00 an area's mix of
    jobs already carries the national-average risk, so it counts; exactly at
    0.50 the index is not below its own midpoint, so it does not."""
    eps = 1e-9
    assert flag([OCCUPATION_INDEX_FLOOR], [NEED_INDEX_CEILING - eps])[0]
    assert not flag([OCCUPATION_INDEX_FLOOR - eps], [NEED_INDEX_CEILING - eps])[0]
    assert not flag([OCCUPATION_INDEX_FLOOR], [NEED_INDEX_CEILING])[0]


def test_flag_thresholds_are_the_declared_ones():
    """The two numbers are defended in src/blindspot.py on what they mean: 1.00
    is the identity value of an SMR-weighted composition index, 0.50 the
    midpoint of an index built from percentile ranks. Changing either is a
    decision, not a tweak."""
    assert OCCUPATION_INDEX_FLOOR == 1.00
    assert NEED_INDEX_CEILING == 0.50


def test_flag_is_pure_and_elementwise():
    n = 50
    rng = np.random.default_rng(0)
    occ, need = rng.uniform(0.6, 1.4, n), rng.uniform(0, 1, n)
    out = flag(occ, need)
    assert out.shape == (n,)
    assert out.dtype == bool
    # Same inputs, same answer — no state, no config, no ranking.
    assert (out == flag(occ, need)).all()
    # Elementwise: each area's answer depends only on its own two numbers.
    assert (out == np.array([flag([o], [d])[0] for o, d in zip(occ, need)])).all()


# --- 2. neither can reach a score ------------------------------------------

def test_descriptive_columns_are_absent_from_the_scoring_frame(tmp_path):
    """prepare_components() is what every weighting scheme, tier and sensitivity
    draw is scored off. If remoteness or the flag appear in it, they are one
    careless line away from a rank."""
    cfg = _temp_cfg(tmp_path)
    pipeline.run(cfg)

    prepared = score.prepare_components(cfg)
    for col in ("ruc21_code", "ruc21_label", "is_remote", "occupation_blind_spot"):
        assert col not in prepared.columns


def test_changing_remoteness_changes_no_score(tmp_path):
    """The guarantee, tested rather than asserted: invert every area's
    remoteness, re-score, and every number that ranks must be untouched."""
    cfg = _temp_cfg(tmp_path)
    pipeline.run(cfg)
    before = pd.read_parquet(cfg.path("fact_score")).set_index("area_code")

    ruc_path = cfg.path("interim") / "fact_remoteness.parquet"
    ruc = pd.read_parquet(ruc_path)
    ruc["is_remote"] = ~ruc["is_remote"].astype(bool)
    ruc.to_parquet(ruc_path, index=False)

    after = score.run(cfg).set_index("area_code")
    for col in ("need_index", "supply_index", "priority_score", "reach_score",
                "rank", "rank_reach", "percentile"):
        pd.testing.assert_series_equal(before[col], after.loc[before.index, col])
    # ...and the descriptive column really did change, so the test could fail.
    assert not (before["is_remote"] == after.loc[before.index, "is_remote"]).all()


def test_flag_is_derived_from_the_score_not_an_input_to_it(tmp_path):
    cfg = _temp_cfg(tmp_path)
    pipeline.run(cfg)
    df = pd.read_parquet(cfg.path("fact_score"))
    occ = pd.read_parquet(cfg.path("interim") / "fact_occupation.parquet")
    merged = df.merge(occ[["area_code", "occupation_proxy"]], on="area_code")
    expected = flag(merged["occupation_proxy"], merged["need_index"])
    assert (merged["occupation_blind_spot"].to_numpy() == expected).all()


# --- 3. the synthetic path -------------------------------------------------

def test_synthetic_run_carries_both_additions(tmp_path):
    cfg = _temp_cfg(tmp_path)
    pipeline.run(cfg)

    ruc = pd.read_parquet(cfg.path("interim") / "fact_remoteness.parquet")
    assert len(ruc) == 25 * 5
    assert set(ruc["ruc21_code"]) <= set(remoteness.EXPECTED_CLASSES)
    assert ruc["is_remote"].dtype == bool

    df = pd.read_parquet(cfg.path("fact_score"))
    for col in ("ruc21_code", "ruc21_label", "is_remote", "occupation_blind_spot"):
        assert col in df.columns
    assert df["ruc21_code"].notna().all()
    assert df["occupation_blind_spot"].notna().all()
    # is_remote must mean the same thing as the published codes say it does.
    assert (df["is_remote"].astype(bool)
            == df["ruc21_code"].isin(remoteness.REMOTE_CODES)).all()

    report = blindspot.run(cfg)
    assert report["n_areas"] == 25 * 5
    assert 0 <= report["n_flagged"] <= report["n_areas"]
    assert cfg.path("blind_spot").exists()


# --- 4. the join -----------------------------------------------------------

def test_ruc_join_covers_every_area_with_no_nulls(tmp_path):
    cfg = _temp_cfg(tmp_path)
    pipeline.run(cfg)

    spine = pd.read_parquet(cfg.path("interim") / "dim_geography.parquet")
    ruc = pd.read_parquet(cfg.path("interim") / "fact_remoteness.parquet")
    assert set(spine["area_code"]) == set(ruc["area_code"])
    assert ruc["area_code"].is_unique
    assert ruc[["ruc21_code", "ruc21_name", "is_remote"]].notna().all().all()


def test_partial_ruc_coverage_fails_loudly(tmp_path):
    """An area with no class would read as 'not remote', which is a claim we
    would have no evidence for. RUC21 is published on the same LSOA 2021
    geography as the spine, so a gap means a vintage mismatch — fail, don't
    quietly shrink the view."""
    cfg = _temp_cfg(tmp_path)
    pipeline.run(cfg)

    raw = cfg.path("synthetic_raw") / "remoteness.csv"
    df = pd.read_csv(raw)
    df.iloc[1:].to_csv(raw, index=False)          # drop one area
    with pytest.raises(ValueError, match="no RUC21 class"):
        remoteness.run(cfg)


def test_unrecognised_class_code_fails_loudly():
    """is_remote is keyed off the code. If the classification is revised, the
    remote/not-remote split silently changes meaning unless we stop."""
    df = pd.DataFrame({"area_code": ["E01000001"], "ruc21_code": ["RX9"],
                       "ruc21_name": ["Something new"], "urban_rural_flag": ["Rural"]})
    with pytest.raises(ValueError, match="unrecognised RUC21 class"):
        remoteness._check_extract(df.reindex(range(remoteness.EXPECTED_AREAS),
                                             method="ffill"))


# --- 5. the caveat that became false ---------------------------------------
# "What this list will not show you" ended '...This list will not surface them.'
# The flag makes that false. rural-lens-spec.md 5.4 requires it rewritten, not
# appended to, and this project has shipped stale caveat copy twice already.

def _copy_cfg(tmp_path):
    from src.config import Config
    return Config({"paths": {"occupation_diagnostic": "occupation_diagnostic.json",
                             "blind_spot": "blind_spot.json"},
                   "vintages": {"remoteness": "RUC21 test vintage"}},
                  root=tmp_path)


def _write_flag(cfg, n_flagged=285):
    cfg.path("blind_spot").write_text(json.dumps({
        "n_areas": 35672, "n_flagged": n_flagged,
        "share_flagged": n_flagged / 35672,
        "ranking": {"median_rank": 10833, "best_rank": 5282, "n_inside_top_100": 0},
        "threshold": {"statement": "the rule"},
    }))


def test_caveat_drops_the_false_claim_once_the_flag_exists(tmp_path):
    cfg = _copy_cfg(tmp_path)
    _write_flag(cfg)
    body = caveats.outvoted_note(cfg)
    assert "will not surface them" not in body
    assert "looking for separately" not in body
    # The structural claim it opens with must survive the rewrite.
    assert "will not appear near the top" in body
    # And it must say what now happens instead, with the count.
    assert "285 of 35,672" in body
    assert "5,282nd" in body


def test_caveat_keeps_the_old_ending_when_the_flag_did_not_run(tmp_path):
    """The old wording is not wrong — it was true before the flag and is true
    again on a run that has no flag. The claim tracks reality either way."""
    body = caveats.outvoted_note(_copy_cfg(tmp_path))
    assert "will not surface them" in body


def test_caveat_says_so_when_nothing_is_flagged(tmp_path):
    cfg = _copy_cfg(tmp_path)
    _write_flag(cfg, n_flagged=0)
    body = caveats.outvoted_note(cfg)
    assert "no area met the test" in body
    assert "will not surface them" not in body


def test_remoteness_note_states_what_the_view_is_not(tmp_path):
    """The three things a reader has to be told or the view misleads: it
    re-ranks rather than re-scores, the cut is remoteness and not rurality, and
    a weekly group may not be viable there (spec 5.5)."""
    body = caveats.remoteness_note(_copy_cfg(tmp_path), median_male_pop=454)
    assert "does not re-score" in body
    assert "not on whether it is rural" in body
    assert "may simply not be viable" in body
    assert "454" in body


def test_remoteness_note_omits_the_population_when_there_is_none(tmp_path):
    body = caveats.remoteness_note(_copy_cfg(tmp_path))
    assert "median of about" not in body
    assert "may simply not be viable" in body


def test_blind_spot_note_never_reads_as_a_recommendation(tmp_path):
    cfg = _copy_cfg(tmp_path)
    marked = caveats.blind_spot_note(cfg, True)
    assert "does not by itself say a group should open here" in marked
    assert caveats.blind_spot_note(cfg, False)
    assert caveats.blind_spot_note(cfg, None) == ""


def test_remoteness_appears_in_the_shared_caveat_list(tmp_path):
    """Both surfaces read data_caveats(), so a note that is not in the list
    cannot reach the map or the PDF. Run against the shipped config, because a
    vintage missing from config.yaml is exactly the failure being guarded."""
    cfg = load_config()
    cfg.root = tmp_path                # no diagnostics here: the copy must still build
    labels = [c["label"] for c in caveats.data_caveats(cfg)]
    assert "Remoteness" in labels
    assert "What this list will not show you" in labels
    body = next(c["body"] for c in caveats.data_caveats(cfg) if c["label"] == "Remoteness")
    assert "RUC21" in body and "never scored" in body

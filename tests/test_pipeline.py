"""End-to-end smoke test on the synthetic fixture, in an isolated temp repo."""

import json

import pandas as pd
import pytest

from src.config import Config, load_config
from src import pipeline


def _temp_cfg(tmp_path) -> Config:
    cfg = load_config()
    cfg._data["mode"] = "synthetic"   # never hit the network in tests
    # Pin the dependency-free travel provider so the smoke test stays hermetic
    # regardless of the provider configured for production (e.g. osrm).
    cfg._data["accessibility"]["provider"] = "haversine"
    # Redirect every path under a temp dir so the test never touches data/.
    cfg._data["paths"] = {
        "raw": "raw", "synthetic_raw": "raw/synthetic",
        "interim": "interim", "output": "output",
        "weights": "output/weights.json",
        "fact_score": "output/fact_score.parquet",
        "scored_geojson": "output/fact_score.geojson",
        "sensitivity": "output/sensitivity.json",
        "fact_tier": "output/fact_tier.parquet",
    }
    cfg.root = tmp_path
    # Smaller fixture keeps the test fast but big enough to fit the GLM.
    cfg._data["synthetic"]["n_las"] = 25
    cfg._data["synthetic"]["lsoas_per_la"] = 5
    cfg._data["sensitivity"]["n_draws"] = 30   # keep the test fast
    return cfg


def test_pipeline_end_to_end(tmp_path):
    cfg = _temp_cfg(tmp_path)
    pipeline.run(cfg)

    score = pd.read_parquet(cfg.path("fact_score"))
    assert len(score) == 25 * 5
    # Core score columns exist and are in range.
    assert score["need_index"].between(0, 1).all()
    assert score["supply_index"].between(0, 1).all()
    assert (score["priority_score"] >= 0).all()
    # Every score is explainable.
    assert score["factor_breakdown"].notna().all()
    fb = json.loads(score["factor_breakdown"].iloc[0])
    assert {"components", "suicide_signal", "need_index",
            "supply_index", "priority_score"} <= set(fb)

    # The calibration diagnostic carries the fit, the declared weights it was
    # checking, and the veto verdict — but never the scoring weights themselves.
    weights = json.loads(cfg.path("weights").read_text())
    for comp in ("deprivation", "occupation", "isolation"):
        c = weights["components"][comp]
        assert "rate_ratio" in c and "ci_low" in c
        assert "weight" not in c, "the diagnostic must not look like a weight source"
    assert weights["family"] in ("poisson", "negbin")
    assert weights["declared_weights"] == cfg["scoring"]["component_weights"]
    assert weights["veto"]["status"] in (
        "pass", "collinearity", "unsupported", "contradicted")
    assert set(weights["schemes"]) == {"multivariable", "univariate", "composite"}
    for sc in weights["schemes"].values():
        assert abs(sum(sc.values()) - 1.0) < 1e-6


def test_scoring_weights_come_from_config(tmp_path):
    """The declared prior in config.yaml is the only source of scoring weights."""
    cfg = _temp_cfg(tmp_path)
    cfg._data["scoring"]["component_weights"] = {
        "deprivation": 0.7, "occupation": 0.2, "isolation": 0.1}
    pipeline.run(cfg)

    score = pd.read_parquet(cfg.path("fact_score"))
    fb = json.loads(score["factor_breakdown"].iloc[0])
    # Breakdown weights are the declared ones, renormalised by the suicide term,
    # so a reader can see the four shares add up.
    total = 1.0 + cfg["scoring"]["suicide_signal_weight"]
    assert abs(fb["components"]["deprivation"]["weight"] - 0.7 / total) < 1e-3
    assert abs(fb["suicide_signal"]["weight"] - 0.10 / total) < 1e-3
    shares = [fb["components"][c]["weight"] for c in
              ("deprivation", "occupation", "isolation")] + [fb["suicide_signal"]["weight"]]
    assert abs(sum(shares) - 1.0) < 1e-3
    # Each contribution is that share applied to the area's percentile.
    dep = fb["components"]["deprivation"]
    assert abs(dep["contribution"] - dep["weight"] * dep["percentile"]) < 1e-3
    assert "declared prior" in fb["weight_basis"]


def test_scoring_survives_calibration_failure(tmp_path):
    """Calibration is a check, not a prerequisite: the outcome needs a live fetch,
    and a nation without a suicide source must stay rankable."""
    cfg = _temp_cfg(tmp_path)

    def _boom(_cfg):
        raise RuntimeError("no outcome data")

    from src import calibrate
    original = calibrate.run
    calibrate.run = _boom
    try:
        pipeline.run(cfg)
    finally:
        calibrate.run = original

    score = pd.read_parquet(cfg.path("fact_score"))
    assert len(score) == 25 * 5
    assert score["priority_score"].notna().all()
    assert not cfg.path("weights").exists(), "no diagnostic should have been written"
    # Sensitivity still reports the axis that does not need the fit.
    sens = json.loads(cfg.path("sensitivity").read_text())
    assert "skipped" in sens["envelope"]
    assert "equal" in sens["alternatives"]


def test_sensitivity_outputs(tmp_path):
    """All three axes report, and the stability verdict is derived from them."""
    cfg = _temp_cfg(tmp_path)
    pipeline.run(cfg)
    sens = json.loads(cfg.path("sensitivity").read_text())

    assert sens["declared_weights"] == cfg["scoring"]["component_weights"]
    # 1) named alternatives, including the no-calibration-needed baseline.
    assert {"equal", "multivariable", "univariate", "composite"} <= set(sens["alternatives"])
    for a in sens["alternatives"].values():
        assert 0.0 <= a["overlap"] <= 1.0
        assert 0.0 <= a["displacement"]["held"] <= 1.0
    # 2) CI envelope.
    assert all(0.0 <= r <= 1.0 for r in sens["area_robustness"].values())
    assert 0.0 <= sens["envelope"]["mean_retention"] <= 1.0
    # 3) supply sweep — the shipped configuration is its own reference.
    shipped = [v for v in sens["supply"].values() if v["is_shipped"]]
    assert len(shipped) == 1
    assert shipped[0]["overlap"] == 1.0 and shipped[0]["displacement"]["held"] == 1.0

    st = sens["stability"]
    assert st["status"] in ("stable", "unstable")
    assert set(st["checks"]) == {"schemes", "envelope", "supply"}
    # Displacement gates the verdict; overlap is reported but must not.
    assert st["unstable_axes"] == [k for k, c in st["checks"].items() if not c["passes"]]
    for c in st["checks"].values():
        assert c["passes"] == (c["worst_held"] >= c["threshold_held"])


def test_overlap_share_is_not_jaccard(tmp_path):
    """The two metrics were conflated once, which set the bar 12 points too high.

    For equal-sized sets Jaccard = overlap / (2 - overlap), so they can only agree
    at 0 and 1. Both are reported; the share is the one plain language means.
    """
    cfg = _temp_cfg(tmp_path)
    pipeline.run(cfg)
    sens = json.loads(cfg.path("sensitivity").read_text())
    n = sens["shortlist_n"]
    for a in sens["alternatives"].values():
        assert a["overlap"] == pytest.approx(a["shared"] / n)
        expected = a["overlap"] / (2 - a["overlap"])
        assert a["jaccard"] == pytest.approx(expected, abs=1e-3)
        assert a["jaccard"] <= a["overlap"] + 1e-9


def test_tiers_band_the_output(tmp_path):
    """Tiers express what the evidence separates: membership, not order."""
    cfg = _temp_cfg(tmp_path)
    pipeline.run(cfg)
    sens = json.loads(cfg.path("sensitivity").read_text())
    tiers = pd.read_parquet(cfg.path("fact_tier"))
    score = pd.read_parquet(cfg.path("fact_score"))

    assert set(tiers["area_code"]) == set(score["area_code"])
    assert set(tiers["tier"]) <= {"shortlist", "contention", "outside"}
    assert (tiers["rank_best"] <= tiers["rank_worst"]).all()
    assert (tiers["rank_best"] <= tiers["rank_declared"]).all()
    assert (tiers["rank_declared"] <= tiers["rank_worst"]).all()

    n = sens["shortlist_n"]
    # Both views are tiered, and independently: reach multiplies priority by
    # population, so a per-capita tier says nothing about the reach ranking.
    for prefix in ("", "reach_"):
        assert set(tiers[f"{prefix}tier"]) <= {"shortlist", "contention", "outside"}
        assert (tiers[f"{prefix}rank_best"] <= tiers[f"{prefix}rank_worst"]).all()
        assert (tiers[f"{prefix}rank_best"] <= tiers[f"{prefix}rank_declared"]).all()
        assert (tiers[f"{prefix}rank_declared"] <= tiers[f"{prefix}rank_worst"]).all()
        band = tiers[tiers[f"{prefix}tier"] == "shortlist"]
        assert (band[f"{prefix}rank_worst"] <= n).all()
    assert sens["tiers"]["reach_counts"]["shortlist"] == int(
        (tiers["reach_tier"] == "shortlist").sum())
    # The declared reach ranking must match fact_score's own reach rank.
    merged = tiers.merge(score[["area_code", "rank_reach"]], on="area_code")
    assert (merged["reach_rank_declared"] == merged["rank_reach"]).all()

    shortlist = tiers[tiers.tier == "shortlist"]
    contention = tiers[tiers.tier == "contention"]
    outside = tiers[tiers.tier == "outside"]
    # Definitions hold exactly.
    assert (shortlist["rank_worst"] <= n).all()
    assert (contention["rank_best"] <= n).all() and (contention["rank_worst"] > n).all()
    assert (outside["rank_best"] > n).all()
    assert sens["tiers"]["counts"]["shortlist"] == len(shortlist)


def test_no_individual_records_in_outputs(tmp_path):
    """Guardrail: outputs are aggregate. Smallest grain is the small area, and
    population denominators are never ~1 (which would imply person-level rows)."""
    cfg = _temp_cfg(tmp_path)
    pipeline.run(cfg)
    score = pd.read_parquet(cfg.path("fact_score"))
    assert (score["male_working_age_pop"] > 100).all()

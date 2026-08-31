"""End-to-end smoke test on the synthetic fixture, in an isolated temp repo."""

import json

import pandas as pd

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

    # Weights were learned and persisted with confidence intervals + all schemes.
    weights = json.loads(cfg.path("weights").read_text())
    for comp in ("deprivation", "occupation", "isolation"):
        c = weights["components"][comp]
        assert "rate_ratio" in c and "ci_low" in c and "weight" in c
    assert weights["family"] in ("poisson", "negbin")
    assert set(weights["schemes"]) == {"multivariable", "univariate", "composite"}
    # Each scheme's weights sum to ~1.
    for sc in weights["schemes"].values():
        assert abs(sum(sc.values()) - 1.0) < 1e-6


def test_sensitivity_outputs(tmp_path):
    cfg = _temp_cfg(tmp_path)
    pipeline.run(cfg)
    sens = json.loads(cfg.path("sensitivity").read_text())
    assert sens["active_scheme"] == cfg["scoring"]["weighting_scheme"]
    # The active scheme compared to itself is identical.
    active = sens["scheme_comparison"][sens["active_scheme"]]
    assert active["topN_jaccard_vs_active"] == 1.0
    assert active["spearman_vs_active"] == 1.0
    # Robustness in [0,1] and retention reported.
    assert all(0.0 <= r <= 1.0 for r in sens["area_robustness"].values())
    assert 0.0 <= sens["perturbation"]["mean_retention"] <= 1.0


def test_no_individual_records_in_outputs(tmp_path):
    """Guardrail: outputs are aggregate. Smallest grain is the small area, and
    population denominators are never ~1 (which would imply person-level rows)."""
    cfg = _temp_cfg(tmp_path)
    pipeline.run(cfg)
    score = pd.read_parquet(cfg.path("fact_score"))
    assert (score["male_working_age_pop"] > 100).all()

"""End-to-end pipeline. Run with:  python -m src.pipeline

Reads from data/raw/ (or generates the synthetic fixture when mode==synthetic),
writes versioned Parquet through data/interim/ to data/output/fact_score.parquet,
and persists the calibration weights. No hidden state — every step reads/writes
files on disk.
"""

from __future__ import annotations

from .config import Config, load_config
from . import geography, calibrate, accessibility, score, sensitivity
from .ingest import deprivation, occupation, isolation, suicide_la, provision
from .synthetic import generate


def _ensure_synthetic(cfg: Config) -> None:
    """In synthetic mode, generate the raw fixture if it isn't already there."""
    out_dir = cfg.path("synthetic_raw")
    marker = out_dir / "geography.csv"
    if not marker.exists():
        print(f"[pipeline] generating synthetic fixture in {out_dir} ...")
        generate(cfg, out_dir)
    else:
        print(f"[pipeline] using existing synthetic fixture in {out_dir}")


def run(cfg: Config | None = None) -> None:
    cfg = cfg or load_config()
    print(f"[pipeline] mode={cfg.mode}  nations={cfg.nations}")

    if cfg.mode == "synthetic":
        _ensure_synthetic(cfg)

    # 1) Spine + population
    spine = geography.run(cfg)

    # 2) Risk proxies
    deprivation.run(cfg)
    occupation.run(cfg)
    isolation.run(cfg)

    # 3) Suicide signal + LA-level calibration -> learned weights
    suicide_la.run(cfg)
    calibrate.run(cfg)

    # 4) Provision + accessibility (supply surface)
    prov = provision.run(cfg)
    accessibility.run(cfg, spine["dim_geography"], prov)

    # 5) Score + factor breakdown + two views
    score.run(cfg)

    # 6) Sensitivity analysis — is the shortlist robust to the weighting?
    sensitivity.run(cfg)

    print("\n[pipeline] done. Outputs:")
    print(f"   {cfg.path('fact_score')}")
    print(f"   {cfg.path('scored_geojson')}")
    print(f"   {cfg.path('weights')}")
    print(f"   {cfg.path('sensitivity')}")
    print("\n   Launch the map with:  streamlit run app/streamlit_app.py")


if __name__ == "__main__":
    run()

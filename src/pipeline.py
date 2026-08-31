"""End-to-end pipeline. Run with:  python -m src.pipeline

Reads from data/raw/ (or generates the synthetic fixture when mode==synthetic),
writes versioned Parquet through data/interim/ to data/output/fact_score.parquet.
No hidden state — every step reads/writes files on disk.

Scoring weights are declared in config.yaml, so calibration is a CHECK on them
rather than a prerequisite (docs/adr/0001-calibration-as-veto.md). It runs
non-blocking: the outcome dataset is England-only, and Wales must still be
rankable when it is missing.
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


def _try(label: str, fn, *args):
    """Run a non-blocking step. A failure here degrades the run, never ends it —
    scoring depends on the declared weights, not on this."""
    try:
        return fn(*args)
    except Exception as exc:
        print(f"\n[pipeline] WARNING: {label} step failed ({type(exc).__name__}: {exc}).")
        print(f"[pipeline] Continuing — scoring uses the declared weights in config.yaml.")
        print(f"[pipeline] The {label} check will be reported as 'not run'.\n")
        return None


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

    # 3) Suicide signal + LA-level check on the declared weights (non-blocking)
    _try("suicide signal", suicide_la.run, cfg)
    _try("calibration", calibrate.run, cfg)

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
    print(f"   {cfg.path('weights')}       (calibration diagnostic)")
    print(f"   {cfg.path('sensitivity')}   (three-axis stability report)")
    print("\n   Launch the map with:  streamlit run app/streamlit_app.py")


if __name__ == "__main__":
    run()

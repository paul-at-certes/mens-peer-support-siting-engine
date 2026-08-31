"""End-to-end pipeline. Run with:  python -m src.pipeline

Reads from data/raw/ (or generates the synthetic fixture when mode==synthetic),
writes versioned Parquet through data/interim/ to data/output/fact_score.parquet.
No hidden state — every step reads/writes files on disk.

Scoring weights are declared in config.yaml, so calibration is a CHECK on them
rather than a prerequisite (docs/adr/0001-calibration-as-veto.md). It runs
non-blocking: the outcome requires a live fetch and covers England and Wales
only, so a network failure or a nation without a suicide source must degrade the
run, not end it.
"""

from __future__ import annotations

from .config import Config, load_config
from . import geography, calibrate, accessibility, score, sensitivity
from .ingest import (car_access, deprivation, occupation, isolation, provision,
                     suicide_la)
from .synthetic import generate


def _ensure_synthetic(cfg: Config) -> None:
    """In synthetic mode, generate the raw fixture if it isn't already there."""
    out_dir = cfg.path("synthetic_raw")
    # Every file the generator writes, so a fixture left over from before a new
    # source was added is regenerated rather than silently short one table.
    expected = ["geography.csv", "population.csv", "deprivation.csv", "occupation.csv",
                "isolation.csv", "car_access.csv", "suicide_la.csv", "provision.csv"]
    missing = [f for f in expected if not (out_dir / f).exists()]
    if missing:
        print(f"[pipeline] generating synthetic fixture in {out_dir} "
              f"(missing: {', '.join(missing)}) ...")
        generate(cfg, out_dir)
    else:
        print(f"[pipeline] using existing synthetic fixture in {out_dir}")


def _try(label: str, fn, *args):
    """Run a non-blocking step. A failure here degrades the run, never ends it —
    nothing in the ranking depends on these steps. They check the declared
    weights or add descriptive context; scoring uses config.yaml either way."""
    try:
        return fn(*args)
    except Exception as exc:
        print(f"\n[pipeline] WARNING: {label} step failed ({type(exc).__name__}: {exc}).")
        print(f"[pipeline] Continuing — scoring uses the declared weights in config.yaml.")
        print(f"[pipeline] The {label} step will be reported as 'not run'.\n")
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

    # 2b) Car or van availability — descriptive context, never scored. It flags
    #     where the car-only travel times most overstate access. Non-blocking
    #     for the same reason as the checks below: it changes no number in the
    #     ranking, so a failed fetch must degrade the run rather than end it.
    _try("car access", car_access.run, cfg)

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

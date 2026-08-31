"""Ingest existing provision (group locations) -> dim_provision.

Real source: Andy's Man Club group finder, which is backed by a WP Store Locator
AJAX endpoint (admin-ajax.php?action=store_search) returning JSON records with
coordinates. The endpoint caps each query at ~25 miles / 50 results, so the full
national list is harvested by tiling the UK with a grid of search points and
deduping by group id (see ``_harvest``). The harvest is cached to
``data/raw/amc_groups.json`` and refreshed only when you re-run it (provision
changes rarely).
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ..config import Config
from ..fetch import get
from ..io_utils import read_csv, require_file, validate_columns

REQUIRED = ["group_id", "lon", "lat"]

WPSL_URL = "https://andysmanclub.co.uk/wp-admin/admin-ajax.php"
# Cached harvest lives at the top level of data/raw/ (vintage-documented there).
HARVEST_NAME = "amc_groups.json"


def _raw_path(cfg: Config) -> Path:
    base = cfg.path("synthetic_raw") if cfg.mode == "synthetic" else cfg.path("real_raw")
    return base / "provision.csv"


def _harvest(cache: Path) -> Path:
    """Tile the UK with search points and dedupe by group id. Reproducible
    refresh path; only runs if the cached harvest is absent."""
    print("[provision] harvesting AMC group finder (WP Store Locator grid) ...")
    seen: dict[str, dict] = {}
    lat = 49.9
    while lat <= 59.0:
        lon = -8.2
        while lon <= 1.8:
            try:
                rows = get(WPSL_URL, params={
                    "action": "store_search", "lat": round(lat, 3), "lng": round(lon, 3),
                    "max_results": 50, "radius": 25,
                }).json()
            except Exception:
                rows = []
            if isinstance(rows, list):
                for r in rows:
                    if "id" in r and r.get("lat") and r.get("lng"):
                        seen[str(r["id"])] = r
            lon += 0.45
        lat += 0.30
    records = list(seen.values())
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(records))
    print(f"[provision] harvested {len(records)} unique groups")
    return cache


def _build_real(cfg: Config, dest: Path) -> Path:
    if dest.exists():
        return dest
    # Prefer the top-level cached harvest; fall back to (re)harvesting.
    cache = cfg.path("raw") / HARVEST_NAME
    if not cache.exists():
        _harvest(cache)
    records = json.loads(cache.read_text())
    df = pd.DataFrame(records)
    out = pd.DataFrame({
        "group_id": df["id"].astype(str),
        "org": "AMC",
        "name": df.get("name", df.get("store")),
        "lon": pd.to_numeric(df["lng"], errors="coerce"),
        "lat": pd.to_numeric(df["lat"], errors="coerce"),
        "status": df.get("open_status", "OPEN").map(
            lambda s: "active" if str(s).upper() == "OPEN" else "inactive"),
        "postcode": df.get("postcode"),
    }).dropna(subset=["lon", "lat"])
    dest.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(dest, index=False)
    return dest


def run(cfg: Config) -> pd.DataFrame:
    path = _raw_path(cfg)
    if cfg.mode == "real":
        _build_real(cfg, path)
    src = require_file(path, "provision (existing peer-support group locations)",
                       "AMC group finder (WP Store Locator endpoint)")
    df = validate_columns(read_csv(src), REQUIRED, "provision")
    if "status" in df.columns:
        df = df[df["status"].fillna("active") == "active"].copy()
    out = cfg.path("interim") / "dim_provision.parquet"
    df.to_parquet(out, index=False)
    print(f"[provision] {len(df)} active groups -> {out.name}")
    return df

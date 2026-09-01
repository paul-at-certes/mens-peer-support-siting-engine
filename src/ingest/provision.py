"""Ingest existing provision (group locations) -> dim_provision.

Real source: Andy's Man Club group finder, which is backed by a WP Store Locator
AJAX endpoint (admin-ajax.php?action=store_search) returning JSON records with
coordinates. The endpoint caps each query at ~25 miles / 50 results, so the full
national list is harvested by tiling the UK with a grid of search points and
deduping by group id (see ``_harvest``). The harvest is cached to
``data/raw/amc_groups.json`` and refreshed only when you re-run it (provision
changes rarely).

BE A GOOD GUEST. The grid is 713 requests against a small charity's WordPress
site. Two rules follow, and both are enforced below rather than left to good
intentions:

  * The harvest NEVER runs on its own. A missing cache fails loudly, the way a
    missing manual download does, and tells you to run it deliberately::

        python -m src.ingest.provision

    This matters because data/raw/ is gitignored, so every fresh clone starts
    without the cache. An automatic harvest would mean every person who ever
    clones this repo silently re-scrapes andysmanclub.co.uk.
  * Requests are spaced by HARVEST_DELAY_SECONDS. At 0.5s the full grid takes
    about six minutes, which is a fine price for a once-a-year refresh.

Their robots.txt (checked 2026-08-31) disallows /wp-json/ and /?rest_route=,
not the admin-ajax.php endpoint used here, so this is within what they permit —
but they have signalled they would rather not serve bulk API traffic, and the
politeness above is the least we owe them. If you are running this in earnest,
tell them: they are the obvious beneficiary of the tool.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pandas as pd

from ..config import Config
from ..fetch import get
from ..io_utils import MissingSourceError, read_csv, require_file, validate_columns

REQUIRED = ["group_id", "lon", "lat"]

WPSL_URL = "https://andysmanclub.co.uk/wp-admin/admin-ajax.php"
# Cached harvest lives at the top level of data/raw/ (vintage-documented there).
HARVEST_NAME = "amc_groups.json"
# Pause between grid requests. 713 requests x 0.5s is about six minutes for a
# refresh you need roughly never, against a charity's site. See the module
# docstring.
HARVEST_DELAY_SECONDS = 0.5
# Share of grid requests allowed to fail before the harvest is refused. The grid
# overlaps heavily (25-mile radius on a ~20-33km step), so one isolated miss is
# usually covered by its neighbours; a rate above this means failures are
# clustered, and a clustered gap is a region of groups that silently went
# missing. Missing groups make areas look underserved, which moves the
# shortlist, so this is the one place a network error could change a number
# without saying so.
HARVEST_MAX_FAILURE_RATE = 0.05


def _raw_path(cfg: Config) -> Path:
    base = cfg.path("synthetic_raw") if cfg.mode == "synthetic" else cfg.path("real_raw")
    return base / "provision.csv"


def _harvest(cache: Path, delay: float = HARVEST_DELAY_SECONDS) -> Path:
    """Tile the UK with search points and dedupe by group id.

    Deliberate, never automatic — call it from ``python -m src.ingest.provision``.
    ``delay`` spaces the requests; do not set it to 0 against the live site.
    """
    print(f"[provision] harvesting AMC group finder (WP Store Locator grid), "
          f"{delay}s between requests. This takes a few minutes by design ...")
    seen: dict[str, dict] = {}
    attempted = 0
    failed: list[tuple[float, float, str]] = []
    lat = 49.9
    while lat <= 59.0:
        lon = -8.2
        while lon <= 1.8:
            attempted += 1
            try:
                rows = get(WPSL_URL, params={
                    "action": "store_search", "lat": round(lat, 3), "lng": round(lon, 3),
                    "max_results": 50, "radius": 25,
                }).json()
            except Exception as exc:  # noqa: BLE001
                failed.append((round(lat, 3), round(lon, 3), f"{type(exc).__name__}: {exc}"))
                rows = []
            if isinstance(rows, list):
                for r in rows:
                    if "id" in r and r.get("lat") and r.get("lng"):
                        seen[str(r["id"])] = r
            time.sleep(delay)
            lon += 0.45
        lat += 0.30
    # Say what did not come back, always -- a partial harvest is otherwise
    # indistinguishable from a country with fewer groups in it.
    rate = len(failed) / attempted if attempted else 0.0
    print(f"[provision] {attempted} grid requests, {len(failed)} failed ({rate:.1%})")
    for lat_f, lon_f, why in failed[:5]:
        print(f"[provision]   failed at {lat_f}, {lon_f}: {why}")
    if len(failed) > 5:
        print(f"[provision]   ... and {len(failed) - 5} more")
    if rate > HARVEST_MAX_FAILURE_RATE:
        raise MissingSourceError(
            f"Harvest incomplete: {len(failed)} of {attempted} grid requests failed "
            f"({rate:.1%}, limit {HARVEST_MAX_FAILURE_RATE:.0%}).\n"
            f"  Nothing has been cached. A partial harvest would understate provision,\n"
            f"  which makes areas look underserved and moves the shortlist.\n"
            f"  Check the site is up and re-run:  python -m src.ingest.provision")

    records = list(seen.values())
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(records))
    print(f"[provision] harvested {len(records)} unique groups")
    return cache


def _build_real(cfg: Config, dest: Path) -> Path:
    if dest.exists():
        return dest
    # The cached harvest is the source. A miss is a loud failure, not a silent
    # re-scrape of a charity's website — see the module docstring.
    cache = cfg.path("raw") / HARVEST_NAME
    if not cache.exists():
        raise MissingSourceError(
            f"No cached AMC group harvest at {cache}.\n"
            f"  This is not fetched automatically: it is 713 requests against a\n"
            f"  small charity's website, so it only runs when you ask for it.\n"
            f"  Run it once (takes a few minutes, by design):\n"
            f"    python -m src.ingest.provision\n"
            f"  Or set mode: synthetic in config.yaml to run on the fixture.")
    records = json.loads(cache.read_text())
    df = pd.DataFrame(records)
    out = pd.DataFrame({
        "group_id": df["id"].astype(str),
        "org": "AMC",
        "name": df.get("name", df.get("store")),
        "lon": pd.to_numeric(df["lng"], errors="coerce"),
        "lat": pd.to_numeric(df["lat"], errors="coerce"),
        # Series fallback, not a bare "OPEN": df.get returns the default
        # verbatim, so a str default would hit .map and raise AttributeError on
        # the one run where the harvest stops carrying the column.
        "status": df.get("open_status", pd.Series("OPEN", index=df.index)).map(
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


def main() -> None:
    """Deliberate re-harvest: ``python -m src.ingest.provision``.

    Kept out of the pipeline on purpose. Delete the cache first to force a
    genuine refresh; otherwise this reports what is already there and stops.
    """
    from ..config import load_config

    cfg = load_config()
    cache = cfg.path("raw") / HARVEST_NAME
    if cache.exists():
        print(f"[provision] harvest already cached at {cache} "
              f"({len(json.loads(cache.read_text()))} groups).")
        print("[provision] Delete that file first if you want a fresh harvest.")
        return
    _harvest(cache)


if __name__ == "__main__":
    main()

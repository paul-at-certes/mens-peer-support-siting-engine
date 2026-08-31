"""HTTP fetch + cache helpers for the real-data adapters.

Every automatable source is downloaded once and CACHED under data/raw/real/ so
re-runs are reproducible and don't re-hit the APIs (which also keeps us polite).
Two paginators cover the two API styles we use:

  * ArcGIS REST FeatureServer  (ONS Open Geography Portal)  -> arcgis_query_all
  * Nomis REST .data.csv        (Census 2021 tables)         -> nomis_csv_all

Network failures fail loudly — we never silently proceed on partial data.
"""

from __future__ import annotations

import io
import time
from pathlib import Path

import pandas as pd
import requests

USER_AGENT = "mens-peer-support-siting-engine/0.1 (public-health resource allocation)"
_DEFAULT_TIMEOUT = 60


def _session() -> requests.Session:
    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})
    return s


def get(url: str, *, params: dict | None = None, timeout: int = _DEFAULT_TIMEOUT,
        retries: int = 3, backoff: float = 2.0) -> requests.Response:
    """GET with retry/backoff. Raises on final failure."""
    sess = _session()
    last: Exception | None = None
    for attempt in range(retries):
        try:
            r = sess.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r
        except Exception as exc:  # noqa: BLE001
            last = exc
            if attempt < retries - 1:
                time.sleep(backoff * (attempt + 1))
    raise RuntimeError(f"GET failed after {retries} attempts: {url}\n  {last}")


def download_to(url: str, dest: Path, *, params: dict | None = None,
                force: bool = False) -> Path:
    """Download ``url`` to ``dest`` (cached: skip if present unless force)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and not force:
        return dest
    r = get(url, params=params)
    dest.write_bytes(r.content)
    return dest


def cached_csv(url: str, dest: Path, *, params: dict | None = None,
               force: bool = False, **read_csv_kwargs) -> pd.DataFrame:
    """Download a CSV (cached) and return it as a DataFrame."""
    download_to(url, dest, params=params, force=force)
    return pd.read_csv(dest, **read_csv_kwargs)


# ---------------------------------------------------------------------------
# ArcGIS REST FeatureServer paginator (ONS Open Geography Portal)
# ---------------------------------------------------------------------------
def arcgis_query_all(layer_url: str, *, where: str = "1=1", out_fields: str = "*",
                     order_by: str | None = None, page_size: int = 1000,
                     return_geometry: bool = False, out_sr: int = 4326,
                     extra: dict | None = None) -> pd.DataFrame:
    """Page through an ArcGIS FeatureServer/MapServer layer and return all
    attribute rows as a DataFrame.

    ``layer_url`` is the layer endpoint WITHOUT the trailing /query, e.g.
    ".../FeatureServer/0". Pages via resultOffset/resultRecordCount. An
    ``order_by`` field is REQUIRED for stable, non-overlapping pages — these
    services do not guarantee ordering otherwise. ``page_size`` is capped by the
    server's maxRecordCount; the loop advances by the actual rows returned, so a
    server-side truncation (exceededTransferLimit) is handled correctly.
    """
    query_url = layer_url.rstrip("/") + "/query"
    base = {
        "where": where,
        "outFields": out_fields,
        "returnGeometry": str(return_geometry).lower(),
        "outSR": out_sr,
        "f": "json",
    }
    if order_by:
        base["orderByFields"] = order_by
    if extra:
        base.update(extra)

    rows: list[dict] = []
    offset = 0
    while True:
        params = dict(base, resultOffset=offset, resultRecordCount=page_size)
        data = get(query_url, params=params).json()
        if "error" in data:
            raise RuntimeError(f"ArcGIS error from {query_url}: {data['error']}")
        feats = data.get("features", [])
        if not feats:
            break
        for ft in feats:
            attrs = dict(ft.get("attributes", {}))
            if return_geometry and "geometry" in ft:
                attrs["_geometry"] = ft["geometry"]
            rows.append(attrs)
        if not data.get("exceededTransferLimit") and len(feats) < page_size:
            break
        offset += len(feats)
    return pd.DataFrame(rows)


def arcgis_count(layer_url: str, where: str = "1=1") -> int:
    query_url = layer_url.rstrip("/") + "/query"
    data = get(query_url, params={"where": where, "returnCountOnly": "true",
                                  "f": "json"}).json()
    return int(data.get("count", 0))


# ---------------------------------------------------------------------------
# Nomis .data.csv paginator (Census 2021 tables)
# ---------------------------------------------------------------------------
def nomis_csv_all(dataset_url: str, params: dict, *, page_size: int = 25000,
                  cache_path: Path | None = None, force: bool = False) -> pd.DataFrame:
    """Page through a Nomis ``.data.csv`` endpoint via RecordLimit/RecordOffset.

    ``dataset_url`` is e.g.
      https://www.nomisweb.co.uk/api/v01/dataset/NM_2021_1.data.csv
    ``params`` carries the geography/measures/dimension selection. Result is
    cached to ``cache_path`` when provided.
    """
    if cache_path and cache_path.exists() and not force:
        return pd.read_csv(cache_path)

    frames: list[pd.DataFrame] = []
    offset = 0
    while True:
        page_params = dict(params, RecordLimit=page_size, RecordOffset=offset)
        r = get(dataset_url, params=page_params)
        chunk = pd.read_csv(io.StringIO(r.text))
        if len(chunk) == 0:
            break
        frames.append(chunk)
        if len(chunk) < page_size:
            break
        offset += len(chunk)

    df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if cache_path is not None:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(cache_path, index=False)
    return df

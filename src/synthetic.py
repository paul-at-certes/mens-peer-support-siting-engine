"""Synthetic fixture generator — the walking-skeleton stand-in for real data.

Produces fake "raw" CSV files under ``data/raw/synthetic/`` that mirror the
*shape* of the real source files, so the ingest modules can read them through
exactly the same code path they will use for the real downloads. This keeps the
synthetic and real flows identical: ingest reads raw -> validates -> writes
interim, regardless of where the raw file came from.

Everything is aggregate and fictional. No individual-level record is produced.

The generator builds a hidden latent "need" field per area, then derives every
proxy from it (plus noise) so that:
  * the proxies are correlated with each other (as in reality), and
  * the LA-aggregated proxies genuinely predict the synthetic suicide counts,
    so calibrate.py recovers a meaningful, signed relationship.
The latent field is never written out — only the observable proxies are.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

# Rough England & Wales bounding box (lon/lat) for plausible-looking centroids.
_LON_MIN, _LON_MAX = -4.5, 1.5
_LAT_MIN, _LAT_MAX = 50.5, 54.5


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def generate(cfg, out_dir: Path) -> dict[str, Path]:
    """Generate the synthetic raw files. Returns a map of name -> path written."""
    s = cfg["synthetic"]
    rng = np.random.default_rng(s["seed"])
    n_las = int(s["n_las"])
    per_la = int(s["lsoas_per_la"])
    n_groups = int(s["n_groups"])
    nations = cfg.nations

    out_dir.mkdir(parents=True, exist_ok=True)

    # --- Local authorities -------------------------------------------------
    la_lon = rng.uniform(_LON_MIN, _LON_MAX, n_las)
    la_lat = rng.uniform(_LAT_MIN, _LAT_MAX, n_las)
    # Assign nations: Wales to the western edge, England otherwise (purely
    # cosmetic — keeps the within-nation normalisation demo meaningful).
    la_nation = np.where((la_lon < -3.0) & ("W" in nations), "W", "E")
    if "W" not in nations:
        la_nation[:] = "E"
    la_codes = [f"E0900000{i:02d}" if nat == "E" else f"W0600000{i:02d}"
                for i, nat in enumerate(la_nation)]
    la_names = [f"LA {i:02d} ({nat})" for i, nat in enumerate(la_nation)]
    # A per-LA latent shift so LAs differ systematically.
    la_latent = rng.normal(0, 1.0, n_las)

    geo_rows, pop_rows = [], []
    dep_rows, occ_rows, iso_rows = [], [], []

    area_counter = 0
    # Accumulators for LA-level suicide generation.
    la_latent_popw = np.zeros(n_las)
    la_pop = np.zeros(n_las)

    for li in range(n_las):
        for _ in range(per_la):
            nat = la_nation[li]
            code_prefix = "E0100" if nat == "E" else "W0100"
            area_code = f"{code_prefix}{area_counter:05d}"
            # Small areas scatter ~5km around their LA centroid.
            lon = la_lon[li] + rng.normal(0, 0.05)
            lat = la_lat[li] + rng.normal(0, 0.05)

            # Latent need for this small area (hidden, never written out).
            latent = la_latent[li] + rng.normal(0, 0.8)

            # Population (male working-age 16-64 is the denominator we care about).
            total_pop = int(rng.uniform(1200, 1900))
            male_pop = int(total_pop * rng.uniform(0.48, 0.51))
            male_wa_pop = int(male_pop * rng.uniform(0.60, 0.68))

            geo_rows.append({
                "area_code": area_code,
                "area_name": f"Area {area_counter:05d}",
                "la_code": la_codes[li],
                "la_name": la_names[li],
                "region": f"Region {li % 9}",
                "nation": nat,
                "centroid_lon": round(float(lon), 5),
                "centroid_lat": round(float(lat), 5),
            })
            pop_rows.append({
                "area_code": area_code,
                "total_pop": total_pop,
                "male_pop": male_pop,
                "male_working_age_pop": male_wa_pop,
                "year": 2022,
            })

            # --- Proxies derived from latent + noise -----------------------
            # Deprivation income/employment domains (0..1, higher = more deprived).
            income = float(np.clip(_sigmoid(0.9 * latent + rng.normal(0, 0.4)), 0.01, 0.99))
            employment = float(np.clip(_sigmoid(0.8 * latent + rng.normal(0, 0.4)), 0.01, 0.99))
            dep_rows.append({
                "area_code": area_code,
                "income_domain": round(income, 4),
                "employment_domain": round(employment, 4),
            })

            # High-risk male occupation share (0..~0.45).
            occ_pct = float(np.clip(0.12 + 0.10 * latent + rng.normal(0, 0.03), 0.0, 0.5))
            occ_rows.append({
                "area_code": area_code,
                "male_high_risk_occ_pct": round(occ_pct, 4),
                "male_high_risk_occ_count": int(round(occ_pct * male_wa_pop)),
            })

            # Isolation proxies. (one_person_household_pct mirrors the real
            # build: Census 2021 has no sex-broken living-alone at LSOA, so the
            # one-person-household share stands in — see ingest/isolation.py.)
            single_sep = float(np.clip(0.28 + 0.07 * latent + rng.normal(0, 0.03), 0.0, 0.8))
            one_person = float(np.clip(0.15 + 0.06 * latent + rng.normal(0, 0.03), 0.0, 0.7))
            iso_rows.append({
                "area_code": area_code,
                "male_single_separated_pct": round(single_sep, 4),
                "one_person_household_pct": round(one_person, 4),
            })

            la_latent_popw[li] += latent * male_wa_pop
            la_pop[li] += male_wa_pop
            area_counter += 1

    # --- LA-level suicide counts ------------------------------------------
    # Generate from the population-weighted latent mean so the aggregated
    # proxies (which track latent) predict the counts. Rate ~ 15 per 100k base.
    la_latent_mean = la_latent_popw / np.maximum(la_pop, 1)
    base_rate = 15.0 / 100_000  # per person-year, male working age, order of magnitude
    pool_years = int(s["suicide_pool_years"])
    # exp(beta * latent): a ~0.45 log-rate increase per 1 SD of latent need.
    lam = la_pop * pool_years * base_rate * np.exp(0.45 * la_latent_mean)
    # Negative-binomial draw to introduce realistic over-dispersion.
    nb_r = 8.0
    p = nb_r / (nb_r + np.maximum(lam, 1e-9))
    deaths = rng.negative_binomial(nb_r, p)

    suicide_rows = []
    for li in range(n_las):
        suicide_rows.append({
            "la_code": la_codes[li],
            "la_name": la_names[li],
            "nation": la_nation[li],
            "sex": "M",
            "age_band": "16-64",
            "years_pooled": f"{2022 - pool_years + 1}-2022",
            "deaths": int(deaths[li]),
            "population": int(la_pop[li]),
        })

    # --- Existing provision (group points) --------------------------------
    g_lon = rng.uniform(_LON_MIN, _LON_MAX, n_groups)
    g_lat = rng.uniform(_LAT_MIN, _LAT_MAX, n_groups)
    provision_rows = [{
        "group_id": f"G{gi:04d}",
        "org": rng.choice(["AMC", "ManKind", "Other"]),
        "name": f"Group {gi:04d}",
        "lon": round(float(g_lon[gi]), 5),
        "lat": round(float(g_lat[gi]), 5),
        "status": "active",
        "start_date": "2021-01-01",
        "type": "peer-support",
    } for gi in range(n_groups)]

    # --- Write all files ---------------------------------------------------
    writes = {
        "geography": (pd.DataFrame(geo_rows), "geography.csv"),
        "population": (pd.DataFrame(pop_rows), "population.csv"),
        "deprivation": (pd.DataFrame(dep_rows), "deprivation.csv"),
        "occupation": (pd.DataFrame(occ_rows), "occupation.csv"),
        "isolation": (pd.DataFrame(iso_rows), "isolation.csv"),
        "suicide_la": (pd.DataFrame(suicide_rows), "suicide_la.csv"),
        "provision": (pd.DataFrame(provision_rows), "provision.csv"),
    }
    paths: dict[str, Path] = {}
    for name, (df, fname) in writes.items():
        fp = out_dir / fname
        df.to_csv(fp, index=False)
        paths[name] = fp
    return paths

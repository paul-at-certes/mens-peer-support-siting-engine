"""Ingest occupation -> fact_occupation (keyed to area_code).

Male occupational COMPOSITION, weighted by observed male suicide SMRs.

    occupation_proxy = Sum_g ( male_share_g x smr_g / 100 )

over the 26 SOC-2020 **sub-major** groups. This is a latent-need COMPOSITION
INDEX, not a rate and not a prediction: SMRs are indirectly standardised, so the
weighted sum cannot be read as a rate.

Why sub-major matters: at SOC **major**-group resolution, elementary trades
(SMR 292) and elementary administration (103) are the same bucket, and skilled
construction (163) shares a bucket with textiles and food prep. The major-group
proxy used until now could not tell them apart.

WHAT IS PUBLISHED (all measured against the live APIs, 2026-08-31)

    route                              coverage
    LSOA  sub-major x sex                32%   <- disclosure-blocked, unusable
    LSOA  major x sex                   100%
    MSOA  sub-major x sex               100%
    MSOA  minor x sex                     0%   <- blocked

Sex-crossed sub-major occupation does not exist at LSOA: ONS blocks ~68% of
areas. So this module uses a HYBRID, taking each piece from the grain where it
is actually published:

  * **LSOA** male shares by major group (exact, every area)      - Nomis RM107
  * **MSOA** male composition WITHIN each major group            - ONS custom API

    occupation_proxy(lsoa) = Sum_M  major_share(lsoa, M) x composite_smr(msoa, M)

where composite_smr(msoa, M) is that MSOA's male sub-major mix within major
group M, weighted by SMR. Between-area variation in "how many men do skilled
trades" stays at LSOA; only "which skilled trades" is smoothed to the MSOA
(~4.9 LSOAs). Both inputs are male, so no sex-composition is ever assumed.

Weights come from ONS *Suicide by occupation: England, main data tables*
(2011-2015), Table 3 - male deaths, SMR and 95% CIs by SOC-2010 sub-major group,
ages 20-64. England only; the 2016-2020 update was cancelled.

Caveats carried downstream (see src/caveats.py):
  * SMRs are 2011-2015, England-only, SOC 2010, applied to 2021 SOC-2020
    composition, and applied to Wales for want of a Welsh alternative.
  * Within-major composition is MSOA-smoothed (above).
  * RESIDENCE-based (where high-risk workers live, not where they work).
  * Denominator is males IN EMPLOYMENT. Non-employment reaches need_index via
    the IMD employment domain; folding it in here would double-count it.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd

from ..config import Config
from ..fetch import download_to, get, nomis_csv_all
from ..io_utils import read_csv, require_file, validate_columns

REQUIRED = ["area_code", "occupation_proxy"]

# --- Source 1: ONS suicide-by-occupation SMRs (direct file URL, cached) ------
SMR_URL = ("https://www.ons.gov.uk/file?uri=/peoplepopulationandcommunity/"
           "birthsdeathsandmarriages/deaths/datasets/"
           "suicidebyoccupationenglandmaindatatables/2011to2015/"
           "dataforthecommentary.xls")
SMR_SHEET = "Table 3"          # sub-major groups, males and females, 20-64
SMR_HEADER_ROW = 5

# Weight rule (see occupational-risk-layer-spec.md 4.3). A CI spanning 100 means
# "no evidence this group differs from the working-age male average", so the
# group takes the NEUTRAL weight. It deliberately does NOT fall back to the
# parent major group: major group 9 is 144 almost entirely because of group 91
# (292), so a parent fallback would assign elevated risk to the 1.2m men in
# group 92 (a precisely-estimated 103) on the strength of a different occupation.
MIN_DEATHS = 50
NEUTRAL_SMR = 100.0

# --- Source 2: LSOA male shares by SOC major group (Nomis RM107) ------------
NOMIS_OCC_URL = "https://www.nomisweb.co.uk/api/v01/dataset/NM_2207_1.data.csv"
MAJOR_GROUPS = list(range(1, 10))      # C2021_OCC_10 codes 1-9 = major groups

# --- Source 3: MSOA male sub-major composition (ONS custom dataset API) -----
# NB the API has NO category-level filtering: only `area-type=<type>,<codes>`
# for areas and `dimensions=` for which variables to cross. Passing
# `occupation_current_27a=1` is silently IGNORED and returns the unfiltered
# total, so the chunking must be by AREA over the full cross-tab.
ONS_BASE = "https://api.beta.ons.gov.uk/v1/population-types/UR"
ONS_OBS = f"{ONS_BASE}/census-observations"
OCC_DIM = "occupation_current_27a"     # 26 sub-major groups + "Does not apply"
NOT_EMPLOYED_ID = "-8"
MALE = "2"
AREA_CHUNK = 400                       # 500 works, 1000 returns HTTP 520

# SOC-2020 sub-major labels, carried into the factor breakdown so the map can
# name the occupations driving an area's score rather than showing a percentile.
SUBMAJOR_LABELS = {
    "11": "Corporate managers and directors",
    "12": "Other managers and proprietors",
    "21": "Science, research, engineering and technology professionals",
    "22": "Health professionals",
    "23": "Teaching and educational professionals",
    "24": "Business, media and public service professionals",
    "31": "Science, engineering and technology associate professionals",
    "32": "Health and social care associate professionals",
    "33": "Protective service occupations",
    "34": "Culture, media and sports occupations",
    "35": "Business and public service associate professionals",
    "41": "Administrative occupations",
    "42": "Secretarial and related occupations",
    "51": "Skilled agricultural and related trades",
    "52": "Skilled metal, electrical and electronic trades",
    "53": "Skilled construction and building trades",
    "54": "Textiles, printing and other skilled trades",
    "61": "Caring personal service occupations",
    "62": "Leisure, travel and related personal service occupations",
    "63": "Community and civil enforcement occupations",
    "71": "Sales occupations",
    "72": "Customer service occupations",
    "81": "Process, plant and machine operatives",
    "82": "Transport and mobile machine drivers and operatives",
    "91": "Elementary trades and related occupations",
    "92": "Elementary administration and service occupations",
}


def _raw_path(cfg: Config) -> Path:
    base = cfg.path("synthetic_raw") if cfg.mode == "synthetic" else cfg.path("real_raw")
    return base / ("occupation.csv" if cfg.mode == "synthetic"
                   else "occupation_smr_weighted.csv")


# ---------------------------------------------------------------------------
# Weights
# ---------------------------------------------------------------------------
def apply_weight_rule(deaths: int, smr: float, lcl: float, ucl: float) -> tuple[float, bool]:
    """The weight for one SOC group, and whether it was neutralised.

    A CI that includes 100 means "no evidence this group differs from the
    working-age male average", so the group takes the neutral weight. It
    deliberately does NOT fall back to the parent major group; see the module
    header for why that would misprice group 92. An inclusive bound (group 81's
    100-117) counts as spanning.
    """
    excludes_100 = (ucl < NEUTRAL_SMR) or (lcl > NEUTRAL_SMR)
    keep = deaths >= MIN_DEATHS and excludes_100
    return (smr if keep else NEUTRAL_SMR), not keep


def composite_smr(sub: pd.DataFrame, w: pd.Series) -> tuple[pd.DataFrame, dict]:
    """Per-area composite SMR for each major group, from its sub-major male mix.

    ``sub`` is male counts by sub-major group (columns are SOC codes such as
    '53'); ``w`` their weights. Returns (composite by major group, within-major
    share frames). An area with no men at all in a major group has no local mix,
    so it falls back to the NATIONAL within-major composition rather than being
    dropped.
    """
    nat = sub.sum(axis=0)
    composite, within = {}, {}
    for M in (str(i) for i in MAJOR_GROUPS):
        cols = [s for s in sub.columns if s.startswith(M)]
        if not cols:
            continue
        tot = sub[cols].sum(axis=1)
        wi = sub[cols].div(tot.where(tot > 0), axis=0)
        nat_mix = nat[cols] / nat[cols].sum() if nat[cols].sum() else nat[cols]
        wi = wi.fillna(pd.Series(nat_mix))
        within[M] = wi
        composite[M] = (wi * w[cols]).sum(axis=1)
    return pd.DataFrame(composite), within


def smr_weights(cfg: Config) -> pd.DataFrame:
    """Parse Table 3 into per-SOC-sub-major male weights, applying the rule above.

    Returns columns: soc, label, deaths, smr, lcl_95, ucl_95, weight, neutralised.
    """
    dest = cfg.path("real_raw") / "suicide_by_occupation_smr.xls"
    if not dest.exists():
        print("[occupation] fetching ONS suicide-by-occupation SMRs ...")
        download_to(SMR_URL, dest)
    require_file(dest, "suicide by occupation SMRs (ONS, 2011-2015)", SMR_URL)

    raw = pd.read_excel(dest, sheet_name=SMR_SHEET, header=None)
    rows = []
    for _, r in raw.iloc[SMR_HEADER_ROW + 1:].iterrows():
        soc = str(r[0]).strip()
        if not re.fullmatch(r"\d{2}", soc):
            continue                        # notes block / blank rows
        try:
            deaths, smr = int(r[2]), float(r[3])
            lcl, ucl = float(r[4]), float(r[5])
        except (TypeError, ValueError):
            continue                        # suppressed ('z') or non-numeric
        weight, neutralised = apply_weight_rule(deaths, smr, lcl, ucl)
        rows.append({
            "soc": soc, "label": str(r[1]).strip(), "deaths": deaths,
            "smr": smr, "lcl_95": lcl, "ucl_95": ucl,
            "weight": weight, "neutralised": neutralised,
        })
    w = pd.DataFrame(rows)
    if len(w) < 20:
        raise ValueError(f"SMR parse found only {len(w)} sub-major groups in "
                         f"'{SMR_SHEET}' — the workbook layout has changed.")
    out = cfg.path("output") / "occupation_weights.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    w.to_csv(out, index=False)
    n_neutral = int(w["neutralised"].sum())
    print(f"[occupation] {len(w)} SOC sub-major weights parsed "
          f"({n_neutral} neutralised, spread {w.weight.min():.0f}-{w.weight.max():.0f}) "
          f"-> {out.name}")
    return w


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------
def _ons_areas(area_type: str) -> pd.DataFrame:
    """All area codes + labels for one ONS area-type (paged)."""
    out, off = [], 0
    while True:
        d = get(f"{ONS_BASE}/area-types/{area_type}/areas",
                params={"limit": 1000, "offset": off}, timeout=120).json()
        items = d.get("items") or []
        out += [(i["id"], i["label"]) for i in items]
        off += len(items)
        if not items or off >= d.get("total_count", 0):
            break
    return pd.DataFrame(out, columns=["code", "label"])


def _lsoa_to_msoa(cfg: Config) -> pd.Series:
    """LSOA 2021 -> MSOA 2021, derived from ONS area labels and validated.

    ONS names 2021 small areas so that an LSOA label is its MSOA label plus a
    trailing letter ('City of London 001A' -> 'City of London 001'). There is no
    England-AND-Wales LSOA21->MSOA21 lookup published on the Geo Portal (the
    OA21...LSOA21_MSOA21 lookups are England-only), so the mapping is derived
    and then checked: every LSOA must match, and every MSOA must be hit. If
    either check fails we raise rather than score on a silently partial join.
    """
    cache = cfg.path("real_raw") / "lsoa21_to_msoa21.csv"
    if cache.exists():
        return pd.read_csv(cache).set_index("area_code")["msoa_code"]
    print("[occupation] deriving LSOA21 -> MSOA21 lookup from ONS area labels ...")
    lsoa, msoa = _ons_areas("lsoa"), _ons_areas("msoa")
    lsoa["msoa_label"] = lsoa.label.str.replace(r"[A-Z]$", "", regex=True).str.strip()
    m = lsoa.merge(msoa.rename(columns={"code": "msoa_code", "label": "msoa_label"}),
                   on="msoa_label", how="left")
    unmatched = int(m.msoa_code.isna().sum())
    if unmatched or m.msoa_code.nunique() != len(msoa):
        raise ValueError(
            f"LSOA21->MSOA21 derivation failed: {unmatched} unmatched LSOAs, "
            f"{m.msoa_code.nunique()} of {len(msoa)} MSOAs hit. The ONS area "
            f"naming convention has changed — fetch a published lookup instead.")
    out = m[["code", "msoa_code"]].rename(columns={"code": "area_code"})
    cache.parent.mkdir(parents=True, exist_ok=True)
    out.to_csv(cache, index=False)
    print(f"[occupation] {len(out):,} LSOAs -> {out.msoa_code.nunique():,} MSOAs (validated)")
    return out.set_index("area_code")["msoa_code"]


def _lsoa_major_male(cfg: Config) -> pd.DataFrame:
    """Male counts per LSOA by SOC-2020 major group (Nomis RM107). One request."""
    cache = cfg.path("real_raw") / "occupation_major_male_lsoa.csv"
    if cache.exists():
        return pd.read_csv(cache, dtype={"area_code": str}).set_index("area_code")
    print("[occupation] fetching LSOA male shares by SOC major group (Nomis RM107) ...")
    raw = nomis_csv_all(NOMIS_OCC_URL, {
        "geography": "TYPE151",
        "c2021_occ_10": ",".join(str(c) for c in MAJOR_GROUPS),
        "c_sex": MALE,
        "measures": "20100",
        "select": "GEOGRAPHY_CODE,C2021_OCC_10,OBS_VALUE",
    }, cache_path=cfg.path("real_raw") / "occupation_major_raw.csv")
    wide = (raw.rename(columns={"GEOGRAPHY_CODE": "area_code", "OBS_VALUE": "value"})
               .pivot_table(index="area_code", columns="C2021_OCC_10",
                            values="value", aggfunc="sum")
               .fillna(0.0))
    wide.columns = [str(c) for c in wide.columns]
    wide.to_csv(cache)
    return wide


def _msoa_submajor_male(cfg: Config) -> tuple[pd.DataFrame, pd.Series]:
    """Male counts per MSOA by SOC-2020 sub-major group, plus the not-employed
    count. Chunked by area over the full occupation x sex cross-tab."""
    cache = cfg.path("real_raw") / "occupation_submajor_male_msoa.csv"
    if cache.exists():
        d = pd.read_csv(cache, dtype={"msoa_code": str}).set_index("msoa_code")
        d.columns = [str(c) for c in d.columns]
        return d.drop(columns=[NOT_EMPLOYED_ID]), d[NOT_EMPLOYED_ID]
    codes = _ons_areas("msoa")["code"].tolist()
    chunks = [codes[i:i + AREA_CHUNK] for i in range(0, len(codes), AREA_CHUNK)]
    print(f"[occupation] fetching MSOA male sub-major composition "
          f"(ONS custom API, {len(chunks)} chunks of {AREA_CHUNK}) ...")
    recs, blocked = [], 0
    for i, ch in enumerate(chunks, 1):
        d = get(ONS_OBS, params={"area-type": "msoa," + ",".join(ch),
                                 "dimensions": f"{OCC_DIM},sex"}, timeout=300).json()
        if d.get("errors") is not None:
            raise RuntimeError(f"ONS API refused MSOA chunk {i}: {d['errors']}")
        blocked += int(d.get("blocked_areas") or 0)
        for o in d.get("observations", []):
            dm = {x["dimension_id"]: x for x in o["dimensions"]}
            if dm["sex"]["option_id"] != MALE:
                continue
            opt = dm[OCC_DIM]
            soc = (opt["option"][:2] if opt["option_id"] != NOT_EMPLOYED_ID
                   else NOT_EMPLOYED_ID)
            recs.append((dm["msoa"]["option_id"], soc, o["observation"]))
        print(f"   [{i:>2}/{len(chunks)}] {len(recs):,} male rows")
    wide = (pd.DataFrame(recs, columns=["msoa_code", "soc", "value"])
              .pivot_table(index="msoa_code", columns="soc", values="value",
                           aggfunc="sum").fillna(0.0))
    if blocked:
        print(f"[occupation] WARNING {blocked} MSOAs disclosure-blocked")
    wide.to_csv(cache)
    return wide.drop(columns=[NOT_EMPLOYED_ID]), wide[NOT_EMPLOYED_ID]


def _build_real(cfg: Config, dest: Path) -> Path:
    if dest.exists():
        return dest
    weights = smr_weights(cfg).set_index("soc")["weight"]
    major = _lsoa_major_male(cfg)
    sub, not_emp = _msoa_submajor_male(cfg)
    l2m = _lsoa_to_msoa(cfg)

    # SOC 2020 adds group 63, absent from the SOC-2010 SMR table; it takes the
    # neutral weight by the same rule as any group the data cannot distinguish
    # from average, so no special case is needed.
    w = pd.Series({s: float(weights.get(s, NEUTRAL_SMR)) for s in sub.columns})
    missing = sorted(s for s in sub.columns if s not in weights.index)
    if missing:
        print(f"[occupation] SOC {missing} absent from the SOC-2010 SMR table "
              f"-> neutral weight {NEUTRAL_SMR:.0f}")

    # --- within-major male composition per MSOA -> a composite SMR per major --
    comp, within = composite_smr(sub, w)                # index msoa, cols major

    # --- apply to LSOA major shares -----------------------------------------
    major = major.reindex(columns=[str(i) for i in MAJOR_GROUPS], fill_value=0.0)
    employed = major.sum(axis=1)
    major_share = major.div(employed.where(employed > 0), axis=0)

    msoa_of = l2m.reindex(major.index)
    comp_l = comp.reindex(msoa_of.to_numpy())           # MSOA composite per LSOA
    comp_l.index = major.index
    proxy = (major_share * comp_l / 100.0).sum(axis=1)

    df = pd.DataFrame({
        "area_code": major.index,
        "occupation_proxy": proxy.to_numpy(),
        "male_in_employment": employed.to_numpy(),
    })
    ne = not_emp.reindex(msoa_of.to_numpy()).to_numpy()
    msoa_emp = sub.sum(axis=1).reindex(msoa_of.to_numpy()).to_numpy()
    df["male_not_employed_share"] = ne / (ne + msoa_emp)

    # --- top 3 contributing sub-major groups, so score.py can explain WHY ----
    contrib = {}
    for M, wi in within.items():
        wl = wi.reindex(msoa_of.to_numpy())
        wl.index = major.index
        for s in wl.columns:
            contrib[s] = major_share[M] * wl[s] * (w[s] / 100.0)
    C = pd.DataFrame(contrib)
    order = C.to_numpy().argsort(axis=1)[:, ::-1][:, :3]
    socs = C.columns.to_numpy()
    cvals = C.to_numpy()
    df["occupation_top_groups"] = [
        json.dumps([{"soc": str(socs[j]),
                     "label": SUBMAJOR_LABELS.get(str(socs[j]), str(socs[j])),
                     "smr": round(float(w[socs[j]]), 0),
                     "contribution": round(float(cvals[i, j]), 4)}
                    for j in order[i]])
        for i in range(len(C))]

    df.loc[employed.to_numpy() <= 0, "occupation_proxy"] = pd.NA
    dest.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(dest, index=False)
    return dest


# ---------------------------------------------------------------------------
def run(cfg: Config) -> pd.DataFrame:
    path = _raw_path(cfg)
    if cfg.mode == "real":
        _build_real(cfg, path)
    src = require_file(path, "occupation (Census 2021 SOC-2020 sub-major, SMR-weighted)",
                       "ONS custom dataset API + ONS suicide-by-occupation SMRs")
    df = read_csv(src)
    # The synthetic fixture predates the SMR weighting and carries the old
    # major-group share; treat it as the proxy so mode: synthetic still runs
    # end-to-end with no network.
    if "occupation_proxy" not in df.columns and "male_high_risk_occ_pct" in df.columns:
        df = df.rename(columns={"male_high_risk_occ_pct": "occupation_proxy"})
    df = validate_columns(df, REQUIRED, "occupation")
    keep = [c for c in ["area_code", "occupation_proxy", "male_in_employment",
                        "male_not_employed_share", "occupation_top_groups"]
            if c in df.columns]
    df = df[keep].copy()
    out = cfg.path("interim") / "fact_occupation.parquet"
    df.to_parquet(out, index=False)
    print(f"[occupation] {len(df)} areas -> {out.name}")
    return df

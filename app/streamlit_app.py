"""Streamlit map — ranked priority surface with both views + per-area breakdown.

Run with:  streamlit run app/streamlit_app.py

Shows the priority surface, a toggle between the per-capita and reach views, an
existing-group overlay, and a per-area factor breakdown. Data vintages and the
key caveats are surfaced on the map face per the design's honesty guardrails.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import pydeck as pdk
import streamlit as st

# Make the repo root importable so `src` resolves when run via streamlit.
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.caveats import assurance_notes, data_caveats  # noqa: E402

st.set_page_config(page_title="Men's Peer-Support Siting Engine", layout="wide")

cfg = load_config()


# Tier labels. The evidence separates the tiers; within a tier it does not
# separate the areas (see sensitivity.py).
TIER_LABEL = {"shortlist": "① Shortlist", "contention": "② In contention",
              "outside": "③ Outside"}


@st.cache_data
def load_scores(path_str: str) -> pd.DataFrame:
    return pd.read_parquet(path_str)


@st.cache_data
def load_tiers(path_str: str) -> pd.DataFrame:
    p = Path(path_str)
    return (pd.read_parquet(p) if p.exists()
            else pd.DataFrame(columns=["area_code", "tier", "rank_best", "rank_worst"]))


@st.cache_data
def load_groups(path_str: str) -> pd.DataFrame:
    p = Path(path_str)
    return pd.read_parquet(p) if p.exists() else pd.DataFrame(columns=["group_id", "lon", "lat"])


@st.cache_data
def load_json(path_str: str) -> dict:
    p = Path(path_str)
    return json.loads(p.read_text()) if p.exists() else {}


score_path = cfg.path("fact_score")
if not score_path.exists():
    st.error("No fact_score.parquet found. Run the pipeline first:\n\n"
             "`python -m src.pipeline`")
    st.stop()

df = load_scores(str(score_path)).copy()
tiers = load_tiers(str(cfg.path("fact_tier")))
if len(tiers):
    df = df.merge(tiers[["area_code", "tier", "rank_best", "rank_worst"]],
                  on="area_code", how="left")
    df["tier_label"] = df["tier"].map(TIER_LABEL).fillna("③ Outside")
groups = load_groups(str(cfg.path("interim") / "dim_provision.parquet"))
weights_meta = load_json(str(cfg.path("weights")))
sens = load_json(str(cfg.path("sensitivity")))

robustness = sens.get("area_robustness", {})

# --- Header -----------------------------------------------------------------
st.title("Men's Peer-Support Siting Engine")
st.caption(
    "A **resource-allocation** tool on **aggregate open data**. It ranks areas by "
    "*latent unmet need* for a men's peer-support group — it does **not** predict "
    "deaths or score individuals. The output is a **shortlist for local judgement**, "
    "not an automated siting decision."
)
_dw = cfg["scoring"]["component_weights"]
_veto_status = (weights_meta.get("veto", {}) or {}).get("status")
_veto_label = {"pass": "✅ passed", "collinearity": "✅ passed (collinearity noted)",
               "unsupported": "⚠️ flagged", "contradicted": "🚩 flagged"}.get(
    _veto_status, "not run")
st.caption(
    f"Declared weights: **{_dw['deprivation']:.2f}** deprivation · "
    f"**{_dw['occupation']:.2f}** occupation · **{_dw['isolation']:.2f}** isolation "
    f"(stated in `config.yaml`, not fitted). LA-level veto check: **{_veto_label}**"
    + (f" on {weights_meta['n_las']} LAs ({weights_meta.get('family','?')} model)."
       if weights_meta else ".")
    + " See the weighting & robustness panel below."
)

# --- Sidebar: view + filters ------------------------------------------------
view = st.sidebar.radio(
    "View",
    ["Per-capita (acute pockets)", "Reach (most men reached)"],
    help="Per-capita ranks priority_score. Reach ranks priority_score × "
         "male working-age population.",
)
score_col = "priority_score" if view.startswith("Per-capita") else "reach_score"
rank_col = "rank" if view.startswith("Per-capita") else "rank_reach"

nations = sorted(df["nation"].unique())
chosen = st.sidebar.multiselect("Nation", nations, default=nations)
show_groups = st.sidebar.checkbox("Show existing groups", value=True)
top_n = st.sidebar.slider("Highlight top N", 5, min(100, len(df)), 20)

view_df = df[df["nation"].isin(chosen)].copy()

# Normalise the active score to 0..1 for colour scaling.
smin, smax = view_df[score_col].min(), view_df[score_col].max()
span = (smax - smin) or 1.0
view_df["_norm"] = (view_df[score_col] - smin) / span
# Red = high priority, fading to pale. RGBA.
view_df["_r"] = (200 + 55 * view_df["_norm"]).astype(int)
view_df["_g"] = (210 * (1 - view_df["_norm"])).astype(int)
view_df["_b"] = (210 * (1 - view_df["_norm"])).astype(int)
view_df["_radius"] = (1500 + 6000 * view_df["_norm"]).astype(int)

# --- Map --------------------------------------------------------------------
left, right = st.columns([3, 2])

with left:
    st.subheader(f"Priority surface — {view}")
    layers = [
        pdk.Layer(
            "ScatterplotLayer",
            data=view_df,
            get_position="[centroid_lon, centroid_lat]",
            get_fill_color="[_r, _g, _b, 170]",
            get_radius="_radius",
            pickable=True,
        )
    ]
    if show_groups and len(groups):
        layers.append(pdk.Layer(
            "ScatterplotLayer",
            data=groups,
            get_position="[lon, lat]",
            get_fill_color="[30, 90, 200, 230]",
            get_radius=2200,
            pickable=True,
        ))
    view_state = pdk.ViewState(
        latitude=float(view_df["centroid_lat"].mean()),
        longitude=float(view_df["centroid_lon"].mean()),
        zoom=6,
    )
    tooltip = {"text": "{area_code}\n" + score_col + ": {" + score_col + "}\n"
                       "need: {need_index}  supply: {supply_index}"}
    st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=view_state,
                             tooltip=tooltip, map_style=None))
    st.caption("🔴 areas = priority (darker/larger = higher).  🔵 = existing groups. "
               "Hover for figures.")

# --- Ranked table + per-area breakdown -------------------------------------
with right:
    st.subheader(f"Top {top_n} shortlist")
    ranked = view_df.sort_values(score_col, ascending=False).head(top_n)
    rank_col = "rank" if score_col == "priority_score" else "rank_reach"
    tbl_cols = [rank_col, "area_code", "nation"]
    if "tier" in ranked.columns:
        tbl_cols.append("tier_label")
    tbl_cols += ["need_index", "supply_index", score_col]
    st.dataframe(
        ranked[tbl_cols].rename(columns={score_col: "score", "tier_label": "tier"}),
        hide_index=True, use_container_width=True,
    )
    if "tier" in ranked.columns:
        st.caption(
            "**Tier, not rank.** ① areas sit inside the top "
            f"{sens.get('shortlist_n', 100)} under *every* configuration tested; ② reach "
            "it under *some*. The evidence separates the tiers; within a tier it does "
            "not separate the areas, so treat them as jointly prioritised and let local "
            "judgement decide."
        )

    st.subheader("Per-area factor breakdown")
    pick = st.selectbox("Area", ranked["area_code"].tolist())
    if pick:
        row = df[df["area_code"] == pick].iloc[0]
        fb = json.loads(row["factor_breakdown"])
        tier_note = (f" · **{TIER_LABEL.get(row['tier'], '—')}** "
                     f"(rank {int(row['rank_best'])}–{int(row['rank_worst'])} across "
                     f"configurations)" if "tier" in row and pd.notna(row.get("tier")) else "")
        st.markdown(f"**{pick}** — {row['area_name']} ({row['nation']}), "
                    f"LA: {row['la_name']}{tier_note}")
        c1, c2, c3 = st.columns(3)
        c1.metric("need_index", f"{fb['need_index']:.3f}")
        c2.metric("supply_index", f"{fb['supply_index']:.3f}")
        c3.metric("priority_score", f"{fb['priority_score']:.3f}")

        rows = []
        for name, c in fb["components"].items():
            rows.append({"factor": name, "percentile": c["percentile"],
                         "weight": c["weight"], "contribution": c["contribution"]})
        s = fb["suicide_signal"]
        rows.append({"factor": "suicide_signal (LA)", "percentile": s["percentile"],
                     "weight": s["weight"], "contribution": s["contribution"]})
        st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        st.caption(f"Nearest group: {row['travel_minutes']} min · "
                   f"groups within catchment: {row['groups_within_catchment']}")
        if pick in robustness:
            ret = robustness[pick]
            tag = "✅ robust" if ret >= 0.8 else ("⚠️ moderate" if ret >= 0.5 else "🚩 low-confidence")
            st.caption(f"Shortlist robustness: stays in the top-{sens.get('shortlist_n')} "
                       f"in **{ret:.0%}** of weight perturbations — {tag}.")

# --- Weighting & robustness panel ------------------------------------------
st.divider()

_assurance = assurance_notes(cfg)
_stability = next((n for n in _assurance if n["label"] == "Stability check"), None)
if _stability and _stability["body"].startswith("UNSTABLE"):
    st.warning(f"**Stability check — {_stability['body']}**")

if sens:
    with st.expander("Weighting & robustness (how much do our choices matter?)",
                     expanded=False):
        for note in _assurance:
            st.markdown(f"**{note['label']}.** {note['body']}")

        st.markdown("---")
        st.markdown(
            f"Three things are varied, one at a time. The test that matters is "
            f"**displacement**: of the top **{sens.get('decision_n')}** areas — the ones "
            f"you would actually act on — how many stay inside the top "
            f"**{sens.get('contention_band')}**? Set membership is a poor test, because "
            f"an area at rank 101 versus 99 flips in and out of a shortlist without "
            f"changing any decision. Overlap is shown too, as a **share** of the "
            f"top-{sens['shortlist_n']}."
        )

        alts = sens.get("alternatives", {})
        if alts:
            st.markdown("**1. Alternative weightings**")
            st.dataframe(pd.DataFrame([
                {"weighting": k,
                 "dep/occ/iso": "/".join(f"{alts[k]['weights'][c]:.2f}"
                                         for c in ("deprivation", "occupation", "isolation")),
                 "decision set held": f"{alts[k]['displacement']['held']:.0%}",
                 "worst rank": alts[k]["displacement"]["worst_rank"],
                 "shortlist overlap": f"{alts[k]['overlap']:.0%}",
                 "Spearman": alts[k]["spearman"],
                 "note": ("discards " + ", ".join(alts[k]["discards_evidenced"])
                          if alts[k].get("discards_evidenced") else "")}
                for k in alts]), hide_index=True, use_container_width=True)
            st.caption(
                "A weighting that discards a proxy the LA fit finds significantly "
                "associated with suicide is dropping evidence rather than weighing it — "
                "it is usually the outlier here."
            )

        env = sens.get("envelope", {})
        if "mean_held" in env:
            st.markdown("**2. Weights moving within what the data supports**")
            st.markdown(
                f"- Decision set held: mean **{env['mean_held']:.0%}** (min "
                f"{env['min_held']:.0%}) over {env['n_draws']} draws from the "
                f"{env['basis']}.\n"
                f"- Mean shortlist overlap **{env['mean_overlap']:.0%}**.\n"
                f"- Average retention **{env['mean_retention']:.0%}**; "
                f"**{env['n_low_confidence']}** area(s) below 50% retention.\n"
                f"- Median rank shift **{env['median_rank_shift']:.0f}** places "
                f"(90th percentile {env['p90_rank_shift']:.0f})."
            )

        sup = sens.get("supply", {})
        if sup and "skipped" not in sup:
            st.markdown("**3. Travel-time and catchment constants**")
            st.dataframe(pd.DataFrame([
                {"configuration": k + (" (shipped)" if v["is_shipped"] else ""),
                 "decision set held": f"{v['displacement']['held']:.0%}",
                 "worst rank": v["displacement"]["worst_rank"],
                 "shortlist overlap": f"{v['overlap']:.0%}",
                 "Spearman": v["spearman"]}
                for k, v in sorted(sup.items(), key=lambda kv: kv[1]["overlap"])
            ]), hide_index=True, use_container_width=True)
            st.caption(
                "The supply surface gates the shortlist hard — most of the top 100 sits "
                "in the bottom decile of supply — so these two hand-set constants get "
                "the same scrutiny as any weight."
            )

# --- Caveats / vintages on the face ----------------------------------------
# Copy comes from src/caveats.py so this and the PDF report cannot drift apart.
st.divider()
with st.expander("Data vintages & caveats (read me)", expanded=False):
    st.markdown("\n".join(f"- **{c['label']}:** {c['body']}" for c in data_caveats(cfg)))

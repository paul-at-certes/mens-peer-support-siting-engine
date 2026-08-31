"""Priority surface — ranked map with both views + per-area breakdown.

Reached from app/streamlit_app.py; run that, not this file.

Shows the priority surface, a toggle between the per-capita and reach views, an
existing-group overlay, and a per-area factor breakdown. Data vintages and the
key caveats are surfaced on the map face per the design's honesty guardrails.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import pydeck as pdk
import streamlit as st

from src.config import load_config
from src.caveats import assurance_notes, data_caveats

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
    df = df.merge(tiers, on="area_code", how="left")
groups = load_groups(str(cfg.path("interim") / "dim_provision.parquet"))
weights_meta = load_json(str(cfg.path("weights")))
sens = load_json(str(cfg.path("sensitivity")))

robustness = sens.get("area_robustness", {})
area_names = dict(zip(df["area_code"], df["area_name"]))

# --- Header -----------------------------------------------------------------
st.title("Men's Peer-Support Siting Engine")
st.caption(
    "A resource-allocation tool built on aggregate open data. It ranks areas by "
    "likely unmet need for a men's peer-support group. It does not predict deaths "
    "or score individuals, and its output is a shortlist for local judgement "
    "rather than an automated siting decision."
)
_dw = cfg["scoring"]["component_weights"]
_veto_status = (weights_meta.get("veto", {}) or {}).get("status")
_veto_label = {"pass": "passed",
               "collinearity": "passed, with a note about overlapping factors",
               "unsupported": "flagged a weight the data cannot evidence",
               "contradicted": "flagged a weight the data contradicts"}.get(
    _veto_status, "did not run")
st.caption(
    f"Declared weights: {_dw['deprivation']:.2f} deprivation, "
    f"{_dw['occupation']:.2f} occupation, {_dw['isolation']:.2f} isolation, "
    f"stated in `config.yaml` rather than fitted. "
    + (f"Checked against {weights_meta['n_las']} council areas, where the veto "
       f"{_veto_label}. " if weights_meta else f"The veto check {_veto_label}. ")
    + "See the weighting and robustness panel below."
)
st.page_link("views/guide.py", icon=":material/menu_book:",
             label="New here? The beginner's guide explains what this is, where the "
                   "data comes from and how the tiers work.")

# --- Sidebar: view + filters ------------------------------------------------
view = st.sidebar.radio(
    "View",
    ["Per-capita (acute pockets)", "Reach (most men reached)"],
    help="Per-capita ranks priority_score. Reach ranks priority_score × "
         "male working-age population.",
)
score_col = "priority_score" if view.startswith("Per-capita") else "reach_score"
# Tiers are computed per view: a tier derived from per-capita ranks says nothing
# about the reach ranking, which multiplies by population. Select the matching
# set so the table can never label a reach row with a per-capita tier.
_tier_prefix = "" if score_col == "priority_score" else "reach_"
if f"{_tier_prefix}tier" in df.columns:
    for col in ("tier", "rank_best", "rank_worst"):
        df[col] = df[f"{_tier_prefix}{col}"]
    df["tier_label"] = df["tier"].map(TIER_LABEL).fillna("③ Outside")
rank_col = "rank" if view.startswith("Per-capita") else "rank_reach"

nations = sorted(df["nation"].unique())
chosen = st.sidebar.multiselect("Nation", nations, default=nations)
show_groups = st.sidebar.checkbox("Show existing groups", value=True)
top_n = st.sidebar.slider("Table: top N", 5, min(100, len(df)), 20)

view_df = df[df["nation"].isin(chosen)].copy()

# --- Which areas go on the map ---------------------------------------------
# All 35k small areas at once is slow to render and unreadable: the decision-
# relevant areas are a few hundred, and plotting the rest buries them. Tier is
# the natural filter because it is already the honest statement of what the
# evidence separates (see sensitivity.py).
if "tier" in view_df.columns:
    n_short = int((view_df["tier"] == "shortlist").sum())
    n_cont = int((view_df["tier"] == "contention").sum())
    TIER_SCOPES = {
        f"① Shortlist ({n_short:,})": ["shortlist"],
        f"① + ② In contention ({n_short + n_cont:,})": ["shortlist", "contention"],
        f"All areas ({len(view_df):,})": ["shortlist", "contention", "outside"],
    }
    scope_label = st.sidebar.radio(
        "Map: areas shown", list(TIER_SCOPES), index=1,
        help="① sits inside the top {n} under EVERY configuration tested; ② under "
             "SOME. Colour is scaled against all areas in the chosen nation(s), so "
             "it means the same thing whichever scope you pick.".format(
                 n=sens.get("shortlist_n", 100)),
    )
    map_df_filter = view_df["tier"].isin(TIER_SCOPES[scope_label])
    scope_note = scope_label
else:
    cap = st.sidebar.slider("Map: areas shown (top N by score)", 50,
                            min(5000, len(view_df)), min(500, len(view_df)), step=50)
    map_df_filter = view_df[score_col].rank(ascending=False, method="min") <= cap
    scope_note = f"top {cap:,} by score (run the pipeline for tier filtering)"

# Normalise the active score to 0..1 for colour scaling. Computed on ALL areas in
# the chosen nation(s), never on the map subset — otherwise filtering to the top
# 54 would repaint the weakest of them pale, as though it were low priority.
smin, smax = view_df[score_col].min(), view_df[score_col].max()
span = (smax - smin) or 1.0
view_df["_norm"] = (view_df[score_col] - smin) / span
# Red = high priority, fading to pale. RGBA.
view_df["_r"] = (200 + 55 * view_df["_norm"]).astype(int)
view_df["_g"] = (210 * (1 - view_df["_norm"])).astype(int)
view_df["_b"] = (210 * (1 - view_df["_norm"])).astype(int)
view_df["_radius"] = (1500 + 6000 * view_df["_norm"]).astype(int)

# Hover text. pydeck interpolates raw values into the tooltip template and has no
# number formatting, so the displayed strings are precomputed here — otherwise a
# score renders as 0.9440000000000001. Both layers carry the same two columns so
# a single deck-level template serves area markers and group markers alike.
score_name = "priority" if score_col == "priority_score" else "reach"
view_df["_tip_title"] = view_df["area_code"] + ": " + view_df["area_name"].astype(str)
view_df["_tip_body"] = (
    score_name + ": " + view_df[score_col].map("{:,.2f}".format)
    + "\nneed: " + view_df["need_index"].map("{:.2f}".format)
    + "   supply: " + view_df["supply_index"].map("{:.2f}".format)
)
map_df = view_df[map_df_filter]

# --- Shortlist selection ----------------------------------------------------
# Streamlit renders top to bottom, so the table below cannot reach back up to the
# map. Its selection is read out of session state here instead: selecting a row
# triggers a rerun, and on that rerun the state is set before the map is built.
ranked = view_df.sort_values(score_col, ascending=False).head(top_n)
rank_col = "rank" if score_col == "priority_score" else "rank_reach"

SHORTLIST_KEY = "shortlist_table"
_sel = st.session_state.get(SHORTLIST_KEY) or {}
_rows = (_sel.get("selection") or {}).get("rows") or []
# A stale row index can outlive the rows it pointed at — changing the nation
# filter, the view or top N reshapes `ranked` without clearing the selection.
selected_code = (ranked["area_code"].iloc[_rows[0]]
                 if _rows and _rows[0] < len(ranked) else None)

# --- Map (full width) -------------------------------------------------------
st.subheader(f"Priority surface: {view}")
layers = [
    pdk.Layer(
        "ScatterplotLayer",
        data=map_df,
        get_position="[centroid_lon, centroid_lat]",
        get_fill_color="[_r, _g, _b, 170]",
        get_radius="_radius",
        pickable=True,
    )
]
if show_groups and len(groups):
    group_pts = groups.copy()
    group_pts["_tip_title"] = (group_pts["name"].astype(str) + " ("
                               + group_pts["org"].astype(str) + ")")
    group_pts["_tip_body"] = ("Existing group, " + group_pts["status"].astype(str)
                              + "\n" + group_pts["postcode"].astype(str))
    layers.append(pdk.Layer(
        "ScatterplotLayer",
        data=group_pts,
        get_position="[lon, lat]",
        get_fill_color="[30, 90, 200, 230]",
        get_radius=2200,
        pickable=True,
    ))
selected_row = (view_df[view_df["area_code"] == selected_code]
                if selected_code is not None else view_df.iloc[0:0])
if len(selected_row):
    # Drawn from view_df, not map_df, so a selected area still shows even when
    # the tier scope would otherwise hide it. A hollow ring rather than a fill,
    # so the area's own priority colour stays readable underneath.
    layers.append(pdk.Layer(
        "ScatterplotLayer",
        data=selected_row,
        get_position="[centroid_lon, centroid_lat]",
        stroked=True,
        filled=False,
        get_line_color=[250, 204, 21, 255],
        line_width_min_pixels=4,
        get_radius=7000,
        radius_min_pixels=14,
        radius_max_pixels=48,
        pickable=False,
    ))

if len(selected_row):
    centre, zoom = selected_row, 10        # jump to what was just selected
else:
    centre, zoom = (map_df if len(map_df) else view_df), 6
view_state = pdk.ViewState(
    latitude=float(centre["centroid_lat"].mean()),
    longitude=float(centre["centroid_lon"].mean()),
    zoom=zoom,
)
tooltip = {"text": "{_tip_title}\n{_tip_body}"}
st.pydeck_chart(pdk.Deck(layers=layers, initial_view_state=view_state,
                         tooltip=tooltip, map_style=None), height=560)
_sel_note = ""
if selected_code is not None:
    _hidden = ("" if selected_code in set(map_df["area_code"])
               else ", which sits outside the current scope but is shown anyway")
    _sel_note = (f" Ringed in yellow: {selected_code}, "
                 f"{area_names.get(selected_code, '')}, selected in the "
                 f"shortlist{_hidden}.")
st.caption(
    f"Showing {len(map_df):,} of {len(view_df):,} areas ({scope_note}). "
    "Red marks priority, darker and larger meaning higher, scaled against all "
    "areas in the chosen nation or nations. Blue marks existing groups. Hover "
    f"over either for figures.{_sel_note}"
)
if not len(map_df):
    st.info("No areas match this scope. Widen the nation or map filter.")

# --- Ranked table | per-area breakdown, side by side ------------------------
st.divider()
table_col, detail_col = st.columns([3, 2])

with table_col:
    st.subheader(f"Top {top_n} shortlist")
    tbl_cols = [rank_col, "area_code", "area_name", "nation"]
    if "tier" in ranked.columns:
        tbl_cols.append("tier_label")
    tbl_cols += ["need_index", "supply_index", score_col]
    st.dataframe(
        ranked[tbl_cols].rename(columns={score_col: "score", "tier_label": "tier",
                                         "area_name": "area"}),
        hide_index=True, use_container_width=True,
        key=SHORTLIST_KEY, on_select="rerun", selection_mode="single-row",
        column_config={
            "need_index": st.column_config.NumberColumn("need", format="%.2f"),
            "supply_index": st.column_config.NumberColumn("supply", format="%.2f"),
            "score": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.caption("Select a row to ring that area on the map and load its breakdown.")
    if "tier" in ranked.columns:
        _view_word = "per-capita" if score_col == "priority_score" else "reach"
        st.caption(
            f"Tiers, not ranks, computed on the {_view_word} ranking shown here. "
            f"① areas sit inside the top {sens.get('shortlist_n', 100)} under every "
            "configuration tested, ② under some of them. The evidence separates the "
            "tiers but not the areas within one, so treat a tier as jointly "
            "prioritised and let local judgement decide between them."
        )

with detail_col:
    st.subheader("Per-area factor breakdown")
    _codes = ranked["area_code"].tolist()
    pick = st.selectbox(
        "Area", _codes,
        index=_codes.index(selected_code) if selected_code in _codes else 0,
        format_func=lambda c: f"{c}: {area_names.get(c, '')}",
    )
    if pick:
        row = df[df["area_code"] == pick].iloc[0]
        fb = json.loads(row["factor_breakdown"])
        if "tier" in row and pd.notna(row.get("tier")):
            _lo, _hi = int(row["rank_best"]), int(row["rank_worst"])
            _span = (f"ranked {_lo} in every configuration tested" if _lo == _hi
                     else f"ranked between {_lo} and {_hi} across the configurations tested")
            tier_note = f" {TIER_LABEL.get(row['tier'], '')}, {_span}."
        else:
            tier_note = ""
        st.markdown(f"**{pick}**, {row['area_name']} ({row['nation']}). "
                    f"Council area: {row['la_name']}.{tier_note}")
        c1, c2, c3 = st.columns(3)
        c1.metric("need", f"{fb['need_index']:.2f}")
        c2.metric("supply", f"{fb['supply_index']:.2f}")
        c3.metric("priority", f"{fb['priority_score']:.2f}")

        rows = []
        for name, c in fb["components"].items():
            rows.append({"factor": name, "percentile": c["percentile"],
                         "weight": c["weight"], "contribution": c["contribution"]})
        sig = fb["suicide_signal"]
        rows.append({"factor": "suicide_signal (LA)", "percentile": sig["percentile"],
                     "weight": sig["weight"], "contribution": sig["contribution"]})
        st.dataframe(
            pd.DataFrame(rows), hide_index=True, use_container_width=True,
            column_config={
                "percentile": st.column_config.NumberColumn(format="%.2f"),
                "weight": st.column_config.NumberColumn(format="%.2f"),
                "contribution": st.column_config.NumberColumn(format="%.2f"),
            },
        )
        st.caption(f"Nearest group {row['travel_minutes']:.0f} minutes away. "
                   f"Groups within the catchment: {row['groups_within_catchment']}.")
        if pick in robustness:
            ret = robustness[pick]
            tag = "robust" if ret >= 0.8 else ("moderate" if ret >= 0.5 else "low confidence")
            st.caption(f"Robustness: stays in the top {sens.get('shortlist_n')} in "
                       f"{ret:.0%} of weight perturbations, rated {tag}.")

# --- Weighting & robustness panel ------------------------------------------
st.divider()

_assurance = assurance_notes(cfg)
_stability = next((n for n in _assurance if n["label"] == "Stability check"), None)
if _stability and _stability["body"].startswith("UNSTABLE"):
    st.warning(f"Stability check: {_stability['body']}")

if sens:
    with st.expander("Weighting & robustness (how much do our choices matter?)",
                     expanded=False):
        for note in _assurance:
            st.markdown(f"**{note['label']}.** {note['body']}")

        st.markdown("---")
        st.markdown(
            f"Three things are varied, one at a time. The test that decides the "
            f"verdict is displacement: of the top {sens.get('decision_n')} areas, the "
            f"ones you would act on, how many stay inside the top "
            f"{sens.get('contention_band')}? Membership of a fixed list is a poor "
            f"test, because an area at rank 101 rather than 99 drops off it without "
            f"anything changing. Overlap is shown as well, as a share of the "
            f"top {sens['shortlist_n']}."
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
                "A weighting that drops a factor the council-level fit finds "
                "significantly associated with suicide is discarding evidence rather "
                "than weighing it. That is usually the outlier in this table."
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
                "The supply surface does most of the filtering: the great majority "
                "of the top 100 sits in the bottom tenth for access. These two "
                "hand-set constants therefore get the same scrutiny as any weight."
            )

# --- Caveats / vintages on the face ----------------------------------------
# Copy comes from src/caveats.py so this and the PDF report cannot drift apart.
st.divider()
with st.expander("Data vintages & caveats (read me)", expanded=False):
    st.markdown("\n".join(f"- **{c['label']}:** {c['body']}" for c in data_caveats(cfg)))

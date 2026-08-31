"""Shortlist PDF report — the top-N areas for a new peer-support group.

Run with:  python -m src.report

A *rendering* of what the pipeline already produced — it reads
``fact_score.parquet`` (+ ``weights.json`` and ``sensitivity.json``) and writes a
clear, shareable PDF to ``paths.report``. It runs NO new analysis: every number
is the exact score the app shows, so the printed shortlist and the interactive
map can never disagree.

The report features the REACH view by default (``report.view``): areas ranked by
``priority_score × male working-age population`` — "the most men reached per new
group" — which is the lens for a national where-to-open-next decision.

A closing section carries the two things that ranking cannot see: the remoteness
view (the remote classes ranked among themselves, on the same per-capita score)
and the occupational blind-spot flag. Both are re-presentations of columns
already in fact_score.parquet; neither runs any analysis and neither can reach a
score. Copy is shared with the map face through src/caveats.py.

reportlab is pure-Python and optional; install it with::

    pip install -e ".[report]"
"""

from __future__ import annotations

import json
import sys

import pandas as pd

from .caveats import (assurance_notes, blind_spot_definition, blind_spot_note,
                       data_caveats, remoteness_note)
from .config import Config, load_config

# Human-readable factor names (match the design note / app language).
FACTOR_LABEL = {
    "deprivation": "income & employment deprivation",
    "occupation": "high-risk male occupation share",
    "isolation": "male isolation",
}
NATION_NAME = {"E": "England", "W": "Wales", "S": "Scotland", "N": "Northern Ireland"}


def _require_reportlab():
    """Import reportlab lazily, failing loudly with an install hint (mirrors the
    'place file X here' pattern used for missing raw files in src/ingest/*)."""
    try:
        import reportlab  # noqa: F401
    except ModuleNotFoundError as e:  # pragma: no cover - trivial guard
        raise SystemExit(
            "The PDF report needs reportlab, which is an optional dependency.\n"
            "Install it with:  pip install -e \".[report]\"   (or: pip install reportlab)"
        ) from e


# --- Plain-English helpers --------------------------------------------------

def _band(pct: float) -> str:
    """Percentile -> qualitative wording."""
    if pct >= 0.95:
        return "very high"
    if pct >= 0.80:
        return "high"
    if pct >= 0.60:
        return "elevated"
    if pct >= 0.40:
        return "moderate"
    return "lower"


def _ord_pct(pct: float) -> str:
    """0.97 -> '97th percentile'."""
    n = int(round(pct * 100))
    n = min(max(n, 1), 100)
    if 10 <= n % 100 <= 20:
        suffix = "th"
    else:
        suffix = {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")
    return f"{n}{suffix} percentile"


def _join(parts: list[str]) -> str:
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


def _a(n: int) -> str:
    """'a' / 'an' for a spoken number (8, 11, 18, 80s start with a vowel sound)."""
    return "an" if n in (8, 11, 18) or 80 <= n <= 89 else "a"


def build_reasoning(row: pd.Series, fb: dict, catchment_minutes: int) -> str:
    """One plain-English sentence explaining why this area is on the shortlist,
    naming its dominant factors straight from the factor_breakdown JSON."""
    nation = NATION_NAME.get(row["nation"], row["nation"])
    comps = fb["components"]

    # Feature the factors that are most ELEVATED (by percentile — that's what
    # stands out to a reader), not by weighted contribution, which reflects the
    # weighting choice and can bury a genuinely high but lightly-weighted proxy.
    items = [(k, comps[k]["percentile"]) for k in ("deprivation", "occupation", "isolation")]
    items.sort(key=lambda x: x[1], reverse=True)
    chosen = [it for it in items if it[1] >= 0.60][:3]
    if len(chosen) < 2:
        chosen = items[:2]
    phrases = [f"{_band(p)} {FACTOR_LABEL[k]} ({_ord_pct(p)})" for k, p in chosen]
    driver = f"Driven by {_join(phrases)} within {nation}."

    # Optional: the wider LA suicide signal, if it is itself high.
    sig = fb.get("suicide_signal", {})
    sig_pct = sig.get("percentile")
    sig_clause = ""
    if sig_pct is not None and sig_pct >= 0.80 and sig.get("la_rate_per_100k") is not None:
        sig_clause = (f" The wider Local-Authority male suicide signal is also "
                      f"elevated ({_ord_pct(sig_pct)}).")

    # Supply / accessibility.
    trav = int(round(float(row["travel_minutes"])))
    within = int(row["groups_within_catchment"])
    if within == 0:
        supply = (f" No existing group lies within the {catchment_minutes}-minute "
                  f"catchment; the nearest is {_a(trav)} {trav}-minute drive.")
    else:
        grp, verb = ("group", "lies") if within == 1 else ("groups", "lie")
        supply = (f" {within} {grp} already {verb} within the {catchment_minutes}-minute "
                  f"catchment; the nearest is {_a(trav)} {trav}-minute drive.")

    # Reach.
    pop = int(round(float(row["male_working_age_pop"])))
    reach = f" A new group here would be within reach of ~{pop:,} working-age men."

    # The occupational blind spot, if this area carries it. Rare in the reach
    # view (the flag requires a below-average need index, and reach leaders sit
    # well above it) but stated wherever it is true.
    flag_clause = ""
    if bool(row.get("occupation_blind_spot", False)):
        flag_clause = (" Marked as an <b>occupational blind spot</b>: the work done here "
                       "carries at least the national-average male suicide risk, and this "
                       "ranking still scores the area below average need.")

    return driver + sig_clause + supply + reach + flag_clause


def _tier_cell(area_code: str, tiers, labels: dict, fallback: str,
               prefix: str = "") -> str:
    """Tier plus the rank range the area spans across tested configurations.

    ``prefix`` selects the tier set matching the featured view — tiers are
    computed per view, and a per-capita tier says nothing about the reach
    ranking this report features by default.
    """
    if len(tiers) and area_code in tiers.index:
        row = tiers.loc[area_code]
        return (f"{labels.get(row[f'{prefix}tier'], '—')}<br/>"
                f"<font size=6 color='#777777'>rank {int(row[f'{prefix}rank_best'])}–"
                f"{int(row[f'{prefix}rank_worst'])}</font>")
    return fallback


def robustness_cell(area_code: str, robustness: dict):
    """(label, is_present) for the robustness column, using the app's thresholds."""
    if area_code in robustness:
        r = float(robustness[area_code])
        if r >= 0.80:
            return f"Robust ({r:.0%})", True
        if r >= 0.50:
            return f"Moderate ({r:.0%})", True
        return f"Low ({r:.0%})", True
    return "—", False   # em dash


# --- PDF assembly -----------------------------------------------------------

def run(cfg: Config) -> "Path":  # type: ignore[name-defined]
    _require_reportlab()
    from reportlab.lib import colors
    from reportlab.lib.pagesizes import A4, landscape
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (KeepTogether, Paragraph, SimpleDocTemplate,
                                    Spacer, Table, TableStyle)

    rep = cfg.get("report", {}) or {}
    top_n = int(rep.get("top_n", 20))
    view = rep.get("view", "reach")
    rank_col = "rank_reach" if view == "reach" else "rank"
    catchment = int(cfg["accessibility"].get("catchment_minutes", 30))

    df = pd.read_parquet(cfg.path("fact_score"))
    weights_meta = json.loads(cfg.path("weights").read_text()) if cfg.path("weights").exists() else {}
    sens = json.loads(cfg.path("sensitivity").read_text()) if cfg.path("sensitivity").exists() else {}
    robustness = sens.get("area_robustness", {})
    tier_path = cfg.path("fact_tier")
    tiers = (pd.read_parquet(tier_path).set_index("area_code")
             if tier_path.exists() else pd.DataFrame())
    tier_prefix = "" if view == "per_capita" else "reach_"
    if len(tiers) and f"{tier_prefix}tier" not in tiers.columns:
        tier_prefix = ""      # tier file predates per-view tiers
    TIER_LABEL = {"shortlist": "Shortlist", "contention": "In contention",
                  "outside": "Outside"}

    top = df.sort_values(rank_col).head(top_n).reset_index(drop=True)

    # --- styles -------------------------------------------------------------
    styles = getSampleStyleSheet()
    NAVY = colors.HexColor("#1b2a4a")
    ACCENT = colors.HexColor("#b3122b")
    GREY = colors.HexColor("#555555")
    h1 = ParagraphStyle("h1", parent=styles["Title"], textColor=NAVY, fontSize=20,
                        spaceAfter=2)
    sub = ParagraphStyle("sub", parent=styles["Normal"], textColor=GREY, fontSize=9,
                         spaceAfter=10)
    box = ParagraphStyle("box", parent=styles["Normal"], fontSize=9.5, leading=13,
                         backColor=colors.HexColor("#eef1f6"), borderColor=NAVY,
                         borderWidth=0.6, borderPadding=8, spaceAfter=10)
    h2 = ParagraphStyle("h2", parent=styles["Heading2"], textColor=NAVY, fontSize=13,
                        spaceBefore=8, spaceAfter=6)
    cell = ParagraphStyle("cell", parent=styles["Normal"], fontSize=7.5, leading=9)
    cell_b = ParagraphStyle("cellb", parent=cell, fontName="Helvetica-Bold")
    reason_num = ParagraphStyle("rnum", parent=styles["Normal"], fontSize=9.5,
                                fontName="Helvetica-Bold", textColor=NAVY)
    reason = ParagraphStyle("reason", parent=styles["Normal"], fontSize=9, leading=12.5)
    foot = ParagraphStyle("foot", parent=styles["Normal"], fontSize=7.5, leading=10,
                          textColor=GREY)

    story = []

    # --- title + framing box ------------------------------------------------
    story.append(Paragraph(rep.get("title", "Shortlist for a New Group"), h1))
    dw = cfg["scoring"]["component_weights"]
    n_las = weights_meta.get("n_las")
    check = (f"checked against {n_las} local authorities" if n_las
             else "not checked — no LA outcome data in this build")
    view_label = "reach — most men reached per new group" if view == "reach" \
        else "per-capita — most acute unmet need"
    story.append(Paragraph(
        f"England &amp; Wales · ranked by <b>{view_label}</b> · declared weights "
        f"<b>{dw['deprivation']:.2f}/{dw['occupation']:.2f}/{dw['isolation']:.2f}</b> "
        f"(deprivation/occupation/isolation), {check}.",
        sub))
    story.append(Paragraph(
        "<b>What this is.</b> A prioritised shortlist of small areas (LSOAs) where the "
        "combination of latent need — deprivation, high-risk male occupations and "
        "isolation, informed by a Local-Authority suicide signal — and thin existing "
        "provision is greatest. <b>What this is not.</b> It does not predict deaths or "
        "score any individual; it allocates attention across areas. Treat it as a "
        "<b>starting shortlist for local judgement</b> — final siting depends on venue, "
        "volunteers and partner appetite, which no dataset captures.", box))

    # --- how to read --------------------------------------------------------
    story.append(Paragraph("How to read this", h2))
    story.append(Paragraph(
        "<b>Reach</b> ranks each area by its priority score multiplied by the local "
        "male 16–64 population, so larger areas with genuine need rise to the top — the "
        "goal being to reach the most men per new group. <b>Need index</b> (0–1) is the "
        "weighted blend of the risk proxies; <b>priority score</b> is that need after "
        "subtracting how well-served the area already is by road (nearer existing groups "
        "lower it). All proxies are ranked as <b>within-nation percentiles</b> — English "
        "and Welsh deprivation indices are not comparable across the border, so each "
        "nation is scored against itself. Scotland and Northern Ireland are out of scope "
        "for this version.", reason))
    story.append(Spacer(1, 4))

    # --- table --------------------------------------------------------------
    story.append(Paragraph(f"Top {len(top)} areas by reach", h2))
    header = ["#", "Area (LSOA)", "Local authority", "Region", "Male<br/>16–64",
              "Priority<br/>score", "Nearest<br/>group", "Tier"]
    data = [[Paragraph(f"<b>{h}</b>", cell) for h in header]]
    any_missing_rob = False
    for i, r in top.iterrows():
        rob_label, present = robustness_cell(r["area_code"], robustness)
        any_missing_rob = any_missing_rob or not present
        trav = int(round(float(r["travel_minutes"])))
        data.append([
            Paragraph(str(i + 1), cell_b),
            Paragraph(f"{r['area_name']}<br/><font size=6 color='#777777'>{r['area_code']}</font>", cell),
            Paragraph(str(r["la_name"]), cell),
            Paragraph(str(r["region"]), cell),
            Paragraph(f"{int(r['male_working_age_pop']):,}", cell),
            Paragraph(f"{r['priority_score']:.3f}", cell),
            Paragraph(f"{trav} min", cell),
            Paragraph(_tier_cell(r["area_code"], tiers, TIER_LABEL, rob_label,
                                 tier_prefix), cell),
        ])
    col_w = [8*mm, 45*mm, 34*mm, 42*mm, 16*mm, 17*mm, 16*mm, 24*mm]
    tbl = Table(data, colWidths=col_w, repeatRows=1)
    tbl.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), NAVY),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f4f8")]),
        ("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(tbl)

    # Robustness footnote (reach areas mostly fall outside the per-capita test).
    env = sens.get("envelope", {})
    if len(tiers):
        t = sens.get("tiers", {}) or {}
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            f"<b>Tier, not rank.</b> Tiers are computed on the <b>{view}</b> ranking, "
            f"the one featured here. “Shortlist” areas sit inside the top "
            f"{sens.get('shortlist_n', 100)} under <i>every</i> one of the "
            f"{t.get('n_configurations', '?')} configurations tested — alternative "
            "weightings, weights drawn from the range the Local-Authority fit supports, "
            "and alternative travel-time and catchment settings. “In contention” areas "
            "reach it under <i>some</i>. The rank range beneath each tier shows how far "
            "the area moved. The evidence separates the tiers; within a tier it does not "
            "separate the areas, so the numbered order here is presentational — treat a "
            "tier as jointly prioritised and let local judgement decide between them.",
            foot))
    elif any_missing_rob and "mean_retention" in env:
        story.append(Spacer(1, 4))
        story.append(Paragraph(
            "“Robustness” tests whether an area stays on the shortlist when the weights "
            f"are moved across the range the fit supports: mean retention "
            f"{env['mean_retention']:.0%}, with {env.get('n_low_confidence', 0)} "
            "low-confidence area(s).", foot))

    # --- per-area reasoning -------------------------------------------------
    story.append(Spacer(1, 8))
    story.append(Paragraph("Why each area — factor breakdown", h2))
    for i, r in top.iterrows():
        fb = json.loads(r["factor_breakdown"])
        text = build_reasoning(r, fb, catchment)
        block = [
            Paragraph(f"{i + 1}. {r['area_name']} — {r['la_name']} "
                      f"<font size=7 color='#777777'>({r['area_code']})</font>", reason_num),
            Paragraph(text, reason),
            Spacer(1, 5),
        ]
        story.append(KeepTogether(block))

    # --- what this ranking cannot see --------------------------------------
    # Two re-presentations of columns already in fact_score.parquet. No new
    # analysis: the remoteness view re-ranks a subset on the same per-capita
    # score, and the flag is a test applied to a finished need_index.
    _remote_and_blind_spot(cfg, df, story, top_n, h2, foot, reason_num, reason,
                           cell, cell_b, NAVY, colors, mm, Paragraph, Spacer,
                           Table, TableStyle)

    # --- how the weights were set, and did the checks pass? -----------------
    # Copy comes from src/caveats.py, shared with the Streamlit map face so the
    # two surfaces cannot drift apart.
    story.append(Spacer(1, 8))
    story.append(Paragraph("How to read this shortlist", h2))
    for note in assurance_notes(cfg):
        story.append(Paragraph(f"• <b>{note['label']}.</b> {note['body']}", foot))
        story.append(Spacer(1, 2))

    # --- caveats & vintages -------------------------------------------------
    story.append(Spacer(1, 6))
    story.append(Paragraph("Data vintages &amp; caveats", h2))
    for c in data_caveats(cfg):
        story.append(Paragraph(f"• <b>{c['label']}:</b> {c['body']}", foot))
        story.append(Spacer(1, 2))

    # --- build --------------------------------------------------------------
    out_path = cfg.path("report")
    doc = SimpleDocTemplate(
        str(out_path), pagesize=landscape(A4),
        leftMargin=14*mm, rightMargin=14*mm, topMargin=12*mm, bottomMargin=12*mm,
        title=rep.get("title", "Shortlist for a New Group"),
        author="Men's Peer-Support Siting Engine",
    )
    doc.build(story)
    return out_path


def _remote_and_blind_spot(cfg, df, story, top_n, h2, foot, reason_num, reason,
                           cell, cell_b, NAVY, colors, mm, Paragraph, Spacer,
                           Table, TableStyle) -> None:
    """The remoteness view and the blind-spot flag, as a closing PDF section.

    Both exist because the main ranking structurally cannot surface these areas
    — see rural-lens-spec.md. Both are descriptive: this function reads columns
    and prints them, and computes no score of any kind.
    """
    has_remote = "is_remote" in df.columns and df["is_remote"].notna().any()
    bs_path = cfg.path("blind_spot")
    bs = json.loads(bs_path.read_text()) if bs_path.exists() else {}
    if not has_remote and not bs:
        return

    story.append(Spacer(1, 10))
    story.append(Paragraph("What this ranking cannot see", h2))

    if has_remote:
        remote = df[df["is_remote"].fillna(False).astype(bool)]
        median_pop = float(remote["male_working_age_pop"].median()) if len(remote) else None
        story.append(Paragraph(f"<b>Remote areas, ranked among themselves.</b> "
                               f"{remoteness_note(cfg, median_pop)}", reason))
        story.append(Spacer(1, 5))

        top_remote = remote.sort_values("priority_score", ascending=False).head(top_n)
        header = ["#", "Area (LSOA)", "Local authority", "Rural-urban class",
                  "Male<br/>16–64", "Priority<br/>score", "Nearest<br/>group",
                  "National<br/>rank"]
        data = [[Paragraph(f"<b>{h}</b>", cell) for h in header]]
        for i, (_, r) in enumerate(top_remote.iterrows(), 1):
            data.append([
                Paragraph(str(i), cell_b),
                Paragraph(f"{r['area_name']}<br/><font size=6 color='#777777'>"
                          f"{r['area_code']}</font>", cell),
                Paragraph(str(r["la_name"]), cell),
                Paragraph(str(r.get("ruc21_label", "")), cell),
                Paragraph(f"{int(r['male_working_age_pop']):,}", cell),
                Paragraph(f"{r['priority_score']:.3f}", cell),
                Paragraph(f"{int(round(float(r['travel_minutes'])))} min", cell),
                Paragraph(f"{int(r['rank']):,}", cell),
            ])
        tbl = Table(data, colWidths=[8*mm, 45*mm, 34*mm, 38*mm, 16*mm, 17*mm, 16*mm,
                                     20*mm], repeatRows=1)
        tbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#f2f4f8")]),
            ("LINEBELOW", (0, 0), (-1, 0), 0.8, NAVY),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(tbl)
        story.append(Spacer(1, 3))
        story.append(Paragraph(
            f"Ranked among the {len(remote):,} remote areas only, on the same "
            f"per-capita score used elsewhere in this document. The national rank in "
            f"the last column is where the area sits on the full list of "
            f"{len(df):,} — the gap between the two is the point of the table.", foot))
        story.append(Spacer(1, 6))

        # The top of that table is dominated by remote URBAN areas, which the
        # main list already surfaces. Without this breakdown a reader would
        # conclude the view adds nothing; it is the smaller rural classes lower
        # down that the main ranking cannot reach.
        by_class = (remote.groupby("ruc21_label")
                    .agg(n=("area_code", "size"), best=("rank", "min"),
                         median=("rank", "median"),
                         flagged=("occupation_blind_spot", "sum"))
                    .sort_values("median"))
        cdata = [[Paragraph(f"<b>{h}</b>", cell) for h in
                  ["Rural-urban class", "Areas", "Best national rank",
                   "Median national rank", "Blind spots"]]]
        for label, r in by_class.iterrows():
            cdata.append([
                Paragraph(str(label), cell),
                Paragraph(f"{int(r['n']):,}", cell),
                Paragraph(f"{int(r['best']):,}", cell),
                Paragraph(f"{int(r['median']):,}", cell),
                Paragraph(f"{int(r['flagged']):,}", cell),
            ])
        ctbl = Table(cdata, colWidths=[45*mm, 20*mm, 32*mm, 34*mm, 22*mm], repeatRows=1)
        ctbl.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), NAVY),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1),
             [colors.white, colors.HexColor("#f2f4f8")]),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
            ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(ctbl)
        story.append(Spacer(1, 3))
        story.append(Paragraph(
            "Read the table above with this one. The remote classes are not one "
            "thing: remote <i>urban</i> areas carry the highest poverty of any class "
            "and the main ranking already surfaces them, which is why they fill the "
            "top of the list. The smaller rural classes sit far lower and carry "
            "almost all of the occupational blind spots — they are the reason this "
            "view exists, and they are further down the same table.", foot))
        story.append(Spacer(1, 8))

    if bs and bs.get("n_flagged"):
        rank, prof = bs.get("ranking", {}), bs.get("profile", {})
        councils = ", ".join(f"{c['la_name']} ({c['n']})"
                             for c in bs.get("top_councils", [])[:8])
        nations = ", ".join(f"{NATION_NAME.get(k, k)} {v}"
                            for k, v in (bs.get("by_nation") or {}).items())
        story.append(Paragraph(
            f"<b>The occupational blind spot.</b> {blind_spot_definition(cfg)}", reason))
        story.append(Spacer(1, 3))
        story.append(Paragraph(
            f"<b>{bs['n_flagged']:,}</b> of {bs['n_areas']:,} areas "
            f"({bs['share_flagged']:.1%}) meet that test. They rank at a median of "
            f"{rank.get('median_rank', 0):,}, the best of them "
            f"{rank.get('best_rank', 0):,}, and <b>none</b> reaches the top 100 under "
            f"any configuration tested — which is the whole reason for naming them "
            f"separately. The nearest group is a median of "
            f"{prof.get('median_travel_minutes', 0):.0f} minutes away, and they sit at "
            f"{prof.get('median_supply_index', 0):.2f} on the supply index against "
            f"{prof.get('all_areas_median_supply_index', 0):.2f} for all areas, so most "
            f"of them are genuinely thinly served as well as unseen. "
            f"By nation: {nations}. Most affected councils: {councils}.", reason))
        story.append(Spacer(1, 3))
        story.append(Paragraph(
            f"<b>Where the threshold came from.</b> {bs['threshold']['statement']} The "
            f"flag is derived from a finished score and never enters one: it cannot "
            f"move an area up or down this list.", foot))
    elif bs:
        story.append(Paragraph(
            "<b>The occupational blind spot.</b> Every area is tested for it, and on "
            "this run no area met the test.", reason))


def main() -> None:
    cfg = load_config()
    out = run(cfg)
    df = pd.read_parquet(cfg.path("fact_score"))
    rep = cfg.get("report", {}) or {}
    rank_col = "rank_reach" if rep.get("view", "reach") == "reach" else "rank"
    top = df.sort_values(rank_col).head(int(rep.get("top_n", 20)))
    print(f"Wrote {out}")
    print(f"Top {len(top)} areas ({rep.get('view', 'reach')} view):")
    for i, (_, r) in enumerate(top.iterrows(), 1):
        print(f"  {i:>2}. {r['area_name']} — {r['la_name']} ({r['region']})")


if __name__ == "__main__":
    sys.exit(main())

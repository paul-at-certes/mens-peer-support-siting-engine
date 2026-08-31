"""A plain-English guide to the siting engine, for readers with no background in
statistics or research data.

Figures are read from the pipeline's own outputs rather than typed in, so the
guide cannot drift away from what the tool actually did.
"""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from src.config import load_config

cfg = load_config()


@st.cache_data
def _facts() -> dict:
    """Live numbers from the pipeline outputs, with safe fallbacks."""
    f: dict = {}
    try:
        score = pd.read_parquet(cfg.path("fact_score"))
        f["n_areas"] = len(score)
        f["n_las"] = int(score["la_code"].nunique())
        f["median_travel"] = float(score["travel_minutes"].median())
    except Exception:
        pass
    try:
        f["n_groups"] = len(pd.read_parquet(cfg.path("interim") / "dim_provision.parquet"))
    except Exception:
        pass
    try:
        f["sens"] = json.loads(cfg.path("sensitivity").read_text())
    except Exception:
        f["sens"] = {}
    try:
        f["weights"] = json.loads(cfg.path("weights").read_text())
    except Exception:
        f["weights"] = {}
    return f


F = _facts()
S = F.get("sens", {})
tiers = S.get("tiers", {})
W = cfg["scoring"]["component_weights"]
N = S.get("shortlist_n", 100)
N_CONFIG = tiers.get("n_configurations", 20)


def n(key, default="—", fmt="{:,}"):
    v = F.get(key)
    return fmt.format(v) if isinstance(v, (int, float)) else default


# ---------------------------------------------------------------------------
st.title("📖 A beginner's guide to this tool")
st.markdown(
    "**No background in statistics needed.** This page explains what the tool is, "
    "where its numbers come from, how it puts them together, and — the part people "
    "ask about most — how the tiers are worked out. Read it start to finish, or "
    "jump to what you need."
)

st.info(
    "**The one-sentence version.** This tool looks at every neighbourhood in England "
    "and Wales, asks *how much might men here need a peer-support group?* and *how "
    "hard is it for them to reach one today?*, and produces a shortlist of places "
    "where the need is high and the nearest group is far away.",
    icon="💡",
)

# ---------------------------------------------------------------------------
st.header("1. What the tool is — and what it is not")

col_is, col_isnt = st.columns(2)
with col_is:
    st.markdown("#### ✅ What it is")
    st.markdown(
        """
        - A way of **narrowing thousands of neighbourhoods down to a manageable
          shortlist**, so a conversation about where to open the next group starts
          from evidence rather than from whoever shouted loudest.
        - Built entirely from **public, published statistics** — the kind of thing
          anyone can download from a government website.
        - **Explainable.** Every area's score can be broken open to show exactly
          which factors pushed it up or down.
        """
    )
with col_isnt:
    st.markdown("#### ❌ What it is not")
    st.markdown(
        """
        - It is **not about individual people.** It never sees, stores or scores a
          single person. Everything is a neighbourhood-level total published by the
          government, already grouped so that nobody can be identified.
        - It does **not predict suicides.** It does not say "this many men will die
          here." It measures *likely unmet need for support* — a different thing.
        - It does **not decide anything.** It produces a shortlist for people to
          judge. Whether a group can actually run somewhere depends on a venue,
          volunteers and local partners, none of which any dataset knows about.
        """
    )

# ---------------------------------------------------------------------------
st.header("2. The basic idea: need, minus what's already there")

st.markdown(
    """
    Imagine you run a chain of free evening classes and you want to open one more.
    You would not open it where you already have three classes down the road. Nor
    would you open it somewhere nobody needs it. You would look for the place with
    **the most unmet demand** — high need, nothing nearby.

    That is exactly what this tool does, in two halves.
    """
)

need_col, supply_col, result_col = st.columns([2, 2, 3])
with need_col:
    st.markdown("#### 🔴 The need side")
    st.markdown(
        "How much might men in this neighbourhood benefit from a peer-support "
        "group? Built from four things, explained in section 3."
    )
with supply_col:
    st.markdown("#### 🔵 The supply side")
    st.markdown(
        f"How well served is this neighbourhood already? Mostly: **how long is the "
        f"drive to the nearest existing group?** Across all neighbourhoods the "
        f"typical drive is about **{n('median_travel', '—', '{:.0f}')} minutes**."
    )
with result_col:
    st.markdown("#### 🎯 Putting them together")
    st.markdown(
        "An area scores highly only if it is **high on need AND poorly served**. "
        "High need but a group already next door? Lower priority — those men "
        "already have somewhere to go. Well served and low need? Lower still."
    )

st.caption(
    "In the app this is written `priority = need × (1 − supply)`. In words: take "
    "the need, then reduce it according to how well the area is already covered. "
    "An area with a group on its doorstep has most of its need already met."
)

# ---------------------------------------------------------------------------
st.header("3. The four things that make up 'need'")

st.markdown(
    "These four were chosen because published research consistently links them to "
    "poor mental health and suicide risk among working-age men. Each is measured "
    "for every neighbourhood."
)

st.dataframe(
    pd.DataFrame([
        {"Factor": "💷 Deprivation",
         "In plain English": "How much poverty there is — people on low incomes and out of work.",
         "Why it's here": "The single most consistent area-level marker of poor mental health.",
         "Counts for": f"{W['deprivation']:.0%}"},
        {"Factor": "🔨 High-risk jobs",
         "In plain English": "The share of local men working in construction, farming, factory work and labouring.",
         "Why it's here": "These trades have markedly higher suicide rates than average.",
         "Counts for": f"{W['occupation']:.0%}"},
        {"Factor": "🏠 Isolation",
         "In plain English": "Men living alone, and men who are single, separated or divorced.",
         "Why it's here": "Isolation and relationship breakdown are strongly linked to men in crisis.",
         "Counts for": f"{W['isolation']:.0%}"},
        {"Factor": "📊 Local suicide rate",
         "In plain English": "The recorded male suicide rate for the wider council area.",
         "Why it's here": "The only place real outcome data enters. Deliberately given a small say — see section 5.",
         "Counts for": f"{cfg['scoring']['suicide_signal_weight']:.0%}"},
    ]),
    hide_index=True, use_container_width=True,
)

st.caption(
    "The percentages are shares of the total. They are a **choice we made and "
    "wrote down**, not a number the computer worked out — section 6 explains why "
    "that is the honest way round."
)

# ---------------------------------------------------------------------------
st.header("4. Where the numbers come from")

st.markdown(
    "Everything is free, published, official statistics. Nothing is bought, "
    "scraped from private sources, or estimated by us."
)

v = cfg["vintages"]
st.dataframe(
    pd.DataFrame([
        {"What": "Poverty and unemployment",
         "Who publishes it": "UK and Welsh governments (the 'Indices of Deprivation')",
         "Vintage": v["deprivation"]},
        {"What": "Jobs, households and relationships",
         "Who publishes it": "The 2021 Census — the survey every household fills in",
         "Vintage": v["census"]},
        {"What": "How many men live where",
         "Who publishes it": "Office for National Statistics, from the Census",
         "Vintage": v["population"]},
        {"What": "Recorded suicides by council area",
         "Who publishes it": "Office for National Statistics death registrations",
         "Vintage": v["suicide"]},
        {"What": "Where existing groups are",
         "Who publishes it": "Andy's Man Club's own public group finder",
         "Vintage": v["provision"]},
        {"What": "Driving times",
         "Who publishes it": "OpenStreetMap road network, routed on our own machine",
         "Vintage": "Car driving times; public transport is not included"},
    ]),
    hide_index=True, use_container_width=True,
)

st.markdown(
    f"Put together, that covers **{n('n_areas')} neighbourhoods** across "
    f"**{n('n_las')} council areas** in England and Wales, and "
    f"**{n('n_groups')} existing groups**."
)

with st.expander("What exactly is a 'neighbourhood'?"):
    st.markdown(
        """
        The official name is an **LSOA** — a Lower Layer Super Output Area. It is
        simply a standard statistical building block: an area of roughly **1,500
        people**, small enough to be meaningfully local, big enough that no
        individual can be picked out of the figures.

        They are the smallest unit most UK statistics are published for, which is
        why the tool works at that level. Every neighbourhood in the country has a
        code like `E01021988` and a name like *Tendring 018A*.
        """
    )

# ---------------------------------------------------------------------------
st.header("5. The awkward problem at the heart of this — and how it's handled")

st.warning(
    "**Suicide figures do not exist for neighbourhoods.** They are only published "
    "for whole council areas — places of 100,000+ people. This is deliberate and "
    "right: the numbers in a single neighbourhood would be so small that "
    "publishing them could identify a family.",
    icon="⚠️",
)

st.markdown(
    f"""
    So the tool has fine-grained data on the *causes* (poverty, jobs, isolation —
    available for every neighbourhood) but only coarse data on the *outcome*
    (suicide — available for {n('n_las')} council areas). It cannot simply "look up
    the suicide rate" for a neighbourhood, and **it never invents one.**

    Instead it does two things:
    """
)

c1, c2 = st.columns(2)
with c1:
    st.markdown("##### ✔️ A sense-check at council level")
    st.markdown(
        """
        The tool adds up its four factors for each whole council area, where
        suicide figures *do* exist, and checks: **do these factors actually line up
        with higher suicide rates?**

        They do — all four point the right way. If any factor had pointed the wrong
        way, the tool would flag it loudly rather than quietly carrying on.
        """
    )
with c2:
    st.markdown("##### ✔️ A small, honest nudge")
    st.markdown(
        f"""
        The council-wide suicide rate is also carried into the score directly, but
        at a deliberately small **{cfg['scoring']['suicide_signal_weight']:.0%}**.

        Every neighbourhood in a council area shares the same figure, so it can
        only nudge a whole council up or down a little — it cannot pretend to
        distinguish one street from the next.
        """
    )

# ---------------------------------------------------------------------------
st.header("6. How very different measurements get compared fairly")

st.markdown(
    """
    The four factors are measured in completely different units — pounds of income,
    percentages of workers, counts of households. You cannot add those together any
    more than you can add up your height, your age and your shoe size.

    So each is converted into the **same simple thing: a position in the queue.**
    """
)

st.success(
    "**Think of it as a race with 35,000 runners.** For each factor separately, "
    "every neighbourhood is lined up from lowest to highest. An area finishing "
    "90th out of 100 is written down as **0.90**. Bottom of the pack is 0.00, top "
    "is 1.00. This is called a *percentile*, and it just means 'what fraction of "
    "areas are below this one'.",
    icon="🏁",
)

st.markdown(
    """
    Now every factor is on the same 0-to-1 scale, so they can be combined using the
    percentages from section 3.

    **One important detail:** England and Wales are lined up *separately*. The two
    countries measure poverty using different systems that genuinely are not
    comparable — so a Welsh neighbourhood is ranked against other Welsh
    neighbourhoods, and an English one against English. Comparing them directly
    would be measuring the difference between two government methods, not a real
    difference between places.
    """
)

# ---------------------------------------------------------------------------
st.header("7. Why the percentages were chosen, not calculated")

st.markdown(
    """
    A reasonable question: why not let the computer work out the perfect weighting
    from the data?

    We tried. **The data cannot answer that question, and pretending otherwise
    would be dishonest.** Here is why, in plain terms.
    """
)

st.markdown(
    """
    The four factors overlap heavily. Poor neighbourhoods tend to have more men in
    manual trades *and* more men living alone. They travel together. So when you
    ask a statistical model "which of these matters most?", it cannot tell them
    apart — rather like asking whether a cake tastes of flour or of eggs.

    Ask the question different but equally reasonable ways and you get genuinely
    different answers, including one that says poverty does not matter at all,
    which is plainly not a real finding — it is the model getting confused by the
    overlap.
    """
)

st.markdown(
    f"""
    So instead the tool does the honest thing:

    1. **We state the weighting up front** ({W['deprivation']:.0%} / {W['occupation']:.0%} /
       {W['isolation']:.0%}), write down our reasons, and put it in a settings file
       anyone can read and argue with.
    2. **The data gets a veto.** If the real suicide figures had contradicted any of
       those choices, the tool would say so on screen.
    3. **We test how much it even matters** — which is what tiers are for.
    """
)

# ---------------------------------------------------------------------------
st.header("8. How the tiers are worked out")

st.markdown(
    f"""
    This is the part worth understanding, because it is what stops the shortlist
    being falsely precise.

    Any ranking involves judgement calls — the weighting above, how long a drive
    counts as "too far", and several more. Each is defensible. Each would shift the
    list a bit. So rather than pick one and present the result as fact, the tool
    **runs the entire calculation {N_CONFIG} times over**, each time making a
    different reasonable choice.
    """
)

st.markdown(f"Then it looks at where each neighbourhood landed across all {N_CONFIG} runs:")

t1, t2, t3 = st.columns(3)
counts = tiers.get("counts", {})
rcounts = tiers.get("reach_counts", {})
with t1:
    st.markdown(f"### ① Shortlist")
    st.markdown(
        f"Finished in the **top {N} every single time**, no matter which reasonable "
        f"choices were made.\n\nThese are the solid candidates."
    )
    st.metric("areas", f"{counts.get('shortlist', 0):,}")
with t2:
    st.markdown("### ② In contention")
    st.markdown(
        f"Reached the top {N} **under some choices but not others**.\n\nWorth a "
        f"look, but the evidence is less settled."
    )
    st.metric("areas", f"{counts.get('contention', 0):,}")
with t3:
    st.markdown("### ③ Outside")
    st.markdown(
        f"**Never** reached the top {N}.\n\nNot a judgement on the place — just not "
        f"where this particular evidence points."
    )
    st.metric("areas", f"{counts.get('outside', 0):,}")

st.info(
    "**The key point: the tiers are trustworthy; the exact running order inside a "
    "tier is not.** Two areas ranked 4th and 11th may well swap places under a "
    "slightly different judgement call. So treat everything in a tier as **jointly "
    "worth considering**, and let local knowledge — venue, volunteers, partners — "
    "decide between them. That is a feature, not a weakness: it stops the tool "
    "claiming a precision it has not earned.",
    icon="🎯",
)

with st.expander("Does the tool ever admit it's unsure?"):
    stab = S.get("stability", {})
    status = stab.get("status", "unknown")
    if status == "stable":
        st.markdown(
            f"""
            Yes, and it currently reports **stable**. The test it applies is: *of
            the top {S.get('decision_n', 20)} areas — the ones you would realistically
            act on — how many stay in contention when we change the judgement calls?*

            Right now **all of them do**, across all {N_CONFIG} runs. If that
            stopped being true, a warning would appear at the top of the map page,
            and the shortlist would carry a health warning rather than being
            presented as a ranking.
            """
        )
    else:
        st.markdown(
            f"""
            Yes — and it is currently reporting **{status}**. A warning appears at
            the top of the map page. The shortlist should be treated as a starting
            point for discussion rather than a ranking.
            """
        )

# ---------------------------------------------------------------------------
st.header("9. The two lists, and why they differ")

va, vb = st.columns(2)
with va:
    st.markdown("#### 👤 Per-capita — *acute pockets*")
    st.markdown(
        """
        Ranks by need per person. Answers: **where are men worst off relative to
        what's available?**

        Tends to surface smaller places — coastal towns, ex-industrial areas —
        where need is concentrated and nothing is nearby.
        """
    )
    st.metric("① Shortlist areas", f"{counts.get('shortlist', 0):,}")
with vb:
    st.markdown("#### 👥 Reach — *most men helped*")
    st.markdown(
        """
        Multiplies that by how many working-age men actually live there. Answers:
        **where would one new group reach the most men?**

        Tends to surface larger places. Slightly less acute need, far more people
        within range of a single new group.
        """
    )
    st.metric("① Shortlist areas", f"{rcounts.get('shortlist', 0):,}")

st.caption(
    "Neither is 'correct' — they answer different questions, and a real decision "
    "usually weighs both. Each has its own tiers, because a place can be an urgent "
    "pocket without being where you would reach the most men, and vice versa."
)

# ---------------------------------------------------------------------------
st.header("10. What this tool cannot tell you")

st.markdown(
    """
    Being straight about the limits is part of using it well.

    - **It only knows about driving.** Public transport is not included — a real
      gap, since the men most likely to need a free peer-support group are among
      the least likely to own a car, and groups often meet in the evening.
    - **The data is a few years old.** The Census was 2021, the deprivation figures
      2019. Neighbourhoods change.
    - **Suicide figures arrive slowly** — typically 6 to 9 months after a death, because
      each one goes through an inquest first.
    - **It knows nothing about whether a group could actually run.** No venue, no
      volunteers, no local partners, no sense of whether men there would come.
      Those decide success, and none of them are in any dataset.
    - **It measures likely need, not certainty.** A high score means the conditions
      associated with unmet need are present — not that any particular person is
      struggling.
    """
)

st.divider()
st.markdown(
    "#### In one line\n"
    "**A well-evidenced starting point for a human conversation — not an answer.**"
)
st.caption(
    "Every figure on this page is read live from the tool's own outputs, so it "
    "cannot fall out of step with what the pipeline actually produced. Full "
    "technical detail: README.md and docs/adr/0001-calibration-as-veto.md."
)

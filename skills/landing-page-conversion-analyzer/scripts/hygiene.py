"""
Landing Page CVR Analyzer -- data hygiene audit and sensitivity report.

AUDIT, NEVER ALTER. This module reports what it does not understand and attaches
the dollars sitting behind each problem, so a data fix has a business case rather
than being one more ticket. It never merges, renames, strips or infers. The single
exception is config.source_groups, applied in loader.py, and only because a human
wrote it down.

Two outputs:
  hygiene_report()     -- what is wrong with the data and what it costs
  sensitivity_report() -- what this brand's volume can and cannot detect

The second one matters more than it looks. "No anomalies found" and "no anomalies
detectable" are different statements, and a tool that cannot tell them apart
teaches its users to trust a silence that means nothing.
"""

from __future__ import annotations

import re
from collections import defaultdict

import numpy as np
import pandas as pd

from engine import (DIM_COLUMNS, UNRESOLVED_SOURCES, DIRECT_SOURCES, mde,
                    two_proportion_p)
from loader import Finding

_SCHEME = re.compile(r"^[a-z]+://")
_SUBDOMAIN = re.compile(r"^(www|m|l|lm|mobile|amp|web)\.")
_TLD = re.compile(r"\.(com|net|org|io|co|ai|app|me|tv|ly|gg|it|de|fr|es|uk|ca|au)(\.[a-z]{2})?$")
_NONALNUM = re.compile(r"[^a-z0-9]")
_TEMPLATE = re.compile(r"(?:\{\{.*?\}\}|\{.*?\}|%%.*?%%|\[\[.*?\]\]|\$\{.*?\})")


def canonical(value: str) -> str:
    """
    A comparison key only. Never written back into the data.

    facebook.com -> facebook ; m.facebook.com -> facebook ; Facebook_Ads -> facebookads
    """
    v = str(value).strip().lower()
    v = _SCHEME.sub("", v)
    v = _SUBDOMAIN.sub("", v)
    v = _TLD.sub("", v)
    return _NONALNUM.sub("", v)


def _ratio(a: str, b: str) -> float:
    """Levenshtein similarity, 0..1. Small strings only; no external dependency."""
    if a == b:
        return 1.0
    la, lb = len(a), len(b)
    if not la or not lb:
        return 0.0
    prev = list(range(lb + 1))
    for i in range(1, la + 1):
        cur = [i] + [0] * lb
        for j in range(1, lb + 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1,
                         prev[j - 1] + (a[i - 1] != b[j - 1]))
        prev = cur
    return 1.0 - prev[lb] / max(la, lb)



def _distinct_variants(a: str, b: str) -> bool:
    """
    Same length, differing only in the final character: `partner-a` vs `partner-b`,
    `campaign1` vs `campaign2`.

    String similarity happily calls these aliases. They almost never are -- that
    trailing character is how humans enumerate genuinely different things. Excluded
    from the possible-alias pass, because a confident wrong suggestion costs more
    credibility than a missed one.

    Abbreviations that share no prefix -- `ig` for `instagram`, `gads` for `google`
    -- are the reverse case and are NOT detectable from the strings alone. They are
    surfaced instead through the source inventory, for a human to recognise.
    """
    if len(a) != len(b) or a == b:
        return False
    diffs = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]
    return len(diffs) == 1 and diffs[0] == len(a) - 1


# ---------------------------------------------------------------------------
# Alias clustering -- report, never merge
# ---------------------------------------------------------------------------

def cluster_sources(cube: pd.DataFrame, conv_step: str,
                    acknowledged: set | None = None) -> list:
    """
    Values that look like the same source, with the traffic and revenue in each
    fragment, so the team can see what the split tagging is costing them.

    Two confidence tiers:
      certain  -- identical once case, subdomain and TLD are removed
      possible -- close string match, or the same shape used across mediums

    Never merged. `utm_medium` and `utm_campaign` are in the contract precisely so
    a human can look at a fragment and decide where it belongs without guessing.
    """
    ack = acknowledged or set()
    agg = (cube.groupby("utm_source", observed=True)
                .agg(sessions=("sessions", "sum"),
                     conversions=(conv_step, "sum"),
                     revenue=("revenue", "sum"))
                .reset_index())
    agg = agg[~agg["utm_source"].astype(str).isin(ack)]

    buckets: dict[str, list] = defaultdict(list)
    for _, r in agg.iterrows():
        s = str(r["utm_source"])
        if s in UNRESOLVED_SOURCES or s in DIRECT_SOURCES:
            continue
        buckets[canonical(s)].append(r)

    clusters = []
    for key, members in buckets.items():
        if len(members) > 1:
            clusters.append(("certain", key, members))

    singles = {k: v[0] for k, v in buckets.items() if len(v) == 1 and len(k) >= 2}
    keys = sorted(singles)
    used = set()
    by_head = defaultdict(list)
    for k in keys:
        by_head[k[:1]].append(k)          # bucket by first char to keep this cheap
    for head, group in by_head.items():
        for i, a in enumerate(group):
            if a in used:
                continue
            near = []
            for b in group[i + 1:]:
                if b in used or _distinct_variants(a, b):
                    continue
                if a.startswith(b) or b.startswith(a) or _ratio(a, b) >= 0.82:
                    near.append(b)
            if near:
                members = [singles[a]] + [singles[b] for b in near]
                used.update([a] + near)
                clusters.append(("possible", a, members))

    out = []
    for confidence, key, members in clusters:
        members = sorted(members, key=lambda r: -r["sessions"])
        total_s = float(sum(m["sessions"] for m in members))
        total_r = float(sum(m["revenue"] for m in members))
        out.append({
            "confidence": confidence,
            "canonical_key": key,
            "fragments": [
                {"value": str(m["utm_source"]),
                 "sessions": int(m["sessions"]),
                 "conversions": int(m["conversions"]),
                 "revenue": round(float(m["revenue"]), 2)}
                for m in members
            ],
            "combined_sessions": int(total_s),
            "combined_revenue": round(total_r, 2),
            "largest_fragment_share": round(
                float(members[0]["sessions"]) / total_s, 3) if total_s else 0.0,
        })
    return sorted(out, key=lambda c: -c["combined_sessions"])



def abbreviation_candidates(cube: pd.DataFrame, conv_step: str, cfg: dict,
                            acknowledged: set | None = None) -> list:
    """
    Abbreviations that string similarity structurally cannot find.

    `fb` is not a prefix of `facebook` and shares almost no characters with it, so
    edit distance is useless here. What IS visible is behavioural: same initial
    letter, one value is abbreviation-length, and the two convert at practically
    the same rate.

    NOTE ON THE TEST -- "their rates are not significantly different" is the wrong
    criterion, and it is the obvious one to reach for. At 900k sessions per source a
    2.33% vs 2.51% gap is overwhelmingly significant while being practically
    identical; a significance gate rejects exactly the pairs it should catch. What
    is wanted is EQUIVALENCE -- are the rates inside a narrow relative band -- which
    is a different question with a different answer as N grows.

    Still never merged. Reported with the evidence attached so a person can confirm
    or reject in one glance, because two genuinely different partners can also look
    like this.
    """
    ack = acknowledged or set()
    floors = cfg.get("floors") or {}
    min_sess = max(floors.get("min_sessions_current", 300), 100)
    band = float((cfg.get("stats") or {}).get("abbreviation_rate_band", 0.20))

    g = (cube.groupby("utm_source", observed=True)
             .agg(sessions=("sessions", "sum"), conv=(conv_step, "sum"),
                  revenue=("revenue", "sum")).reset_index())
    g = g[(g["sessions"] >= min_sess) &
          (~g["utm_source"].astype(str).isin(ack | UNRESOLVED_SOURCES | DIRECT_SOURCES))]

    med = (cube.groupby("utm_source", observed=True)["utm_medium"]
               .agg(lambda x: sorted(set(str(v) for v in x))[:3]).to_dict())

    rows = g.to_dict("records")
    out, seen = [], set()
    for short in rows:
        sv = str(short["utm_source"])
        if not (2 <= len(sv) <= 4) or short["sessions"] <= 0:
            continue
        r_s = short["conv"] / short["sessions"]
        for long in rows:
            lv = str(long["utm_source"])
            if len(lv) < len(sv) + 3 or sv.lower()[0] != lv.lower()[0]:
                continue
            if canonical(sv) == canonical(lv) or long["sessions"] <= 0:
                continue                     # already a certain cluster
            if (sv, lv) in seen:
                continue
            r_l = long["conv"] / long["sessions"]
            if r_l <= 0:
                continue
            rel = abs(r_s - r_l) / r_l
            if rel > band:
                continue
            seen.add((sv, lv))
            m_s, m_l = med.get(short["utm_source"], []), med.get(long["utm_source"], [])
            same_medium = bool(set(m_s) & set(m_l))
            out.append({
                "confidence": "possible_abbreviation" if same_medium else "weak_abbreviation",
                "short": sv, "long": lv,
                "short_mediums": m_s, "long_mediums": m_l,
                "evidence": (
                    f"`{sv}` is abbreviation-length, shares an initial letter with "
                    f"`{lv}`, and converts at {r_s:.2%} against {r_l:.2%} -- a "
                    f"{rel:.0%} relative gap, inside the {band:.0%} equivalence band."
                    + ("" if same_medium else
                       f" Mediums differ ({'/'.join(m_s)} vs {'/'.join(m_l)}), which is "
                       f"either evidence they are different things or evidence the same "
                       f"thing is tagged two ways -- a person has to decide which.")
                ),
                "short_sessions": int(short["sessions"]),
                "long_sessions": int(long["sessions"]),
                "combined_sessions": int(short["sessions"] + long["sessions"]),
                "combined_revenue": round(float(short["revenue"] + long["revenue"]), 2),
            })
    return sorted(out, key=lambda c: (c["confidence"] != "possible_abbreviation",
                                      -c["combined_sessions"]))[:15]


def source_inventory(cube: pd.DataFrame, conv_step: str, cfg: dict,
                     top_n: int = 40) -> list:
    """
    Every material source with its medium, volume, rate and testability.

    This exists because string similarity cannot see that `ig` is Instagram or that
    `gads` is Google. A human reading this list side by side can, in about four
    seconds. The tool's job is to lay the evidence out, not to guess -- and then the
    grouping goes in config, written by a person.
    """
    floors = cfg.get("floors") or {}
    floor = floors.get("min_sessions_current", 300)
    g = (cube.groupby(["utm_source", "utm_medium"], observed=True)
             .agg(sessions=("sessions", "sum"), conv=(conv_step, "sum"),
                  revenue=("revenue", "sum"))
             .reset_index())
    g = g[g["sessions"] > 0].nlargest(top_n, "sessions")
    out = []
    for _, r in g.iterrows():
        out.append({
            "utm_source": str(r["utm_source"]),
            "utm_medium": str(r["utm_medium"]),
            "sessions": int(r["sessions"]),
            "cvr": round(float(r["conv"]) / float(r["sessions"]), 5) if r["sessions"] else 0.0,
            "revenue": round(float(r["revenue"]), 2),
            "testable_at_cell_level": bool(r["sessions"] >= floor),
        })
    return out

# ---------------------------------------------------------------------------
# Hygiene report
# ---------------------------------------------------------------------------

def hygiene_report(res, cfg: dict) -> dict:
    cube, conv = res.cube, res.conversion_step
    findings = list(res.findings)
    total_sessions = float(cube["sessions"].sum())

    # --- broken tags: template variables that never rendered ------------------
    for col in ("utm_source", "utm_medium", "utm_campaign"):
        vals = cube[col].astype(str)
        hit = vals.str.contains(_TEMPLATE, regex=True, na=False)
        if hit.any():
            sub = cube[hit]
            samples = sorted(set(sub[col].astype(str)))[:6]
            findings.append(Finding("unrendered_template_variable", "high",
                f"`{col}` contains unrendered template variables on "
                f"{int(sub['sessions'].sum()):,} sessions. A tag is shipping its own "
                f"placeholder instead of a value -- every session behind it is "
                f"unattributable and will stay unattributable until the tag is fixed.",
                {"examples": samples, "sessions": int(sub["sessions"].sum()),
                 "revenue": round(float(sub["revenue"].sum()), 2)}))

    # --- unresolved traffic ---------------------------------------------------
    unres = cube[cube["utm_source"].astype(str).isin(UNRESOLVED_SOURCES)]
    if len(unres):
        share = float(unres["sessions"].sum()) / total_sessions if total_sessions else 0
        if share > 0.02:
            findings.append(Finding("unresolved_traffic", "medium" if share < 0.10 else "high",
                f"{share:.1%} of sessions have no resolvable source. These are not direct "
                f"traffic -- they are traffic the analytics could not attribute, and they "
                f"dilute every source-level rate below.",
                {"share": round(share, 4),
                 "by_value": {str(k): int(v) for k, v in
                              unres.groupby("utm_source", observed=True)["sessions"]
                                   .sum().nlargest(5).items()}}))

    # --- source and medium sprawl --------------------------------------------
    n_src = cube["utm_source"].nunique()
    n_med = cube["utm_medium"].nunique()
    if n_med > 15:
        med = (cube.groupby("utm_medium", observed=True)["sessions"].sum()
                   .sort_values(ascending=False))
        tail = med[med < total_sessions * 0.001]
        findings.append(Finding("medium_sprawl", "medium",
            f"{n_med} distinct utm_medium values, {len(tail)} of them under 0.1% of "
            f"traffic each. `medium` is meant to be a small controlled vocabulary "
            f"(cpc, email, organic, social, referral). When it becomes free text, "
            f"channel rollups stop being possible for anyone, not just this tool.",
            {"distinct_mediums": int(n_med),
             "long_tail_values": [str(x) for x in tail.index[:15]]}))

    # --- alias fragmentation --------------------------------------------------
    ack = set(cfg.get("acknowledged_sources") or [])
    clusters = cluster_sources(cube, conv, ack)
    abbrevs = abbreviation_candidates(cube, conv, cfg, ack)
    if abbrevs:
        findings.append(Finding("possible_source_abbreviations", "high",
            f"{len(abbrevs)} short source value(s) behave identically to a longer source "
            f"on the same medium -- `" + "`, `".join(
                f"{a['short']}` ~ `{a['long']}" for a in abbrevs[:4]) + "`. String "
            f"similarity cannot detect these (an initialism shares no prefix with the "
            f"word), so they are inferred from behaviour and reported with the evidence. "
            f"Nothing has been merged. Confirm or reject each, then write the confirmed "
            f"ones into config.source_groups.",
            {"candidates": abbrevs}))
    material = [c for c in clusters
                if c["combined_sessions"] >= max(50, total_sessions * 0.001)]
    if material:
        findings.append(Finding("source_alias_fragmentation", "high",
            f"{len(material)} source(s) appear under multiple spellings, splitting "
            f"{sum(c['combined_sessions'] for c in material):,} sessions and "
            f"{sum(c['combined_revenue'] for c in material):,.0f} in revenue across "
            f"fragments. Nothing has been merged. Each fragment is tested on its own "
            f"volume, which is why several of them will fall below the floor and go "
            f"untested. Confirm the groupings and put them in config.source_groups, or "
            f"fix the tagging upstream.",
            {"clusters": material[:12]}))

    # --- landing page sprawl / query strings ---------------------------------
    lp = cube["landing_page"].astype(str)
    with_q = cube[lp.str.contains(r"\?", regex=True, na=False)]
    keep = set((cfg.get("landing_pages") or {}).get("keep_params") or [])
    known_tracking = set((cfg.get("landing_pages") or {}).get("known_tracking_params") or [])
    if len(with_q):
        params = defaultdict(int)
        for v, s in zip(with_q["landing_page"].astype(str), with_q["sessions"]):
            q = v.split("?", 1)[1] if "?" in v else ""
            for kv in q.split("&"):
                k = kv.split("=", 1)[0]
                if k:
                    params[k] += int(s)
        unknown = {k: v for k, v in params.items()
                   if k not in keep and k not in known_tracking}
        collapsed = with_q["landing_page"].astype(str).str.split("?").str[0].nunique()
        findings.append(Finding("landing_page_query_sprawl", "medium",
            f"{cube['landing_page'].nunique():,} distinct landing pages, of which "
            f"{len(with_q):,} rows carry a query string; those collapse to {collapsed:,} "
            f"paths if tracking parameters are dropped. Nothing has been collapsed -- "
            f"the analysis below runs on raw values, which fragments the volume. Confirm "
            f"the keep-list in config.landing_pages.keep_params.",
            {"distinct_pages": int(cube["landing_page"].nunique()),
             "would_collapse_to": int(collapsed),
             "unrecognised_params": dict(sorted(unknown.items(),
                                                key=lambda x: -x[1])[:15])}))

    # --- tracking break signals ----------------------------------------------
    findings.extend(tracking_break_dates(res, cfg)[1])

    # --- geography sanity -----------------------------------------------------
    shippable = set((cfg.get("business") or {}).get("shippable_countries") or [])
    if shippable:
        off = cube[~cube["country"].astype(str).isin(shippable | {"ZZ"})]
        if len(off):
            share = float(off["sessions"].sum()) / total_sessions
            findings.append(Finding("traffic_outside_served_markets", "low" if share < .05 else "medium",
                f"{share:.1%} of sessions come from countries outside the served market "
                f"list. This traffic cannot convert, and it drags every blended rate down. "
                f"Reported separately rather than counted as a conversion problem.",
                {"share": round(share, 4),
                 "top": {str(k): int(v) for k, v in
                         off.groupby("country", observed=True)["sessions"]
                            .sum().nlargest(8).items()}}))

    return {
        "findings": [f.to_dict() for f in findings],
        "alias_clusters": clusters[:25],
        "source_inventory": source_inventory(cube, conv, cfg),
        "abbreviation_candidates": abbrevs,
        "counts": {"distinct_landing_pages": int(cube["landing_page"].nunique()),
                   "distinct_sources": int(n_src),
                   "distinct_mediums": int(n_med),
                   "distinct_countries": int(cube["country"].nunique()),
                   "cube_rows": int(len(cube))},
    }


def tracking_break_dates(res, cfg: dict | None = None) -> tuple[set, list]:
    """
    Days where the conversion step collapsed while traffic held.

    Scans the ENTIRE loaded range, not just recent days. A tracking break inside
    the BASELINE window is more dangerous than one in the current window: it drags
    the baseline down, and every genuinely broken thing afterwards then looks
    normal by comparison. Detected days are excluded from every window.

    Returns (dates_to_exclude, findings).
    """
    out, bad = [], set()
    cube, conv = res.cube, res.conversion_step
    conv_cube = cube[cube["is_converting_page"]] if "is_converting_page" in cube else cube
    daily = (conv_cube.groupby("date", observed=True)
             .agg(sessions=("sessions", "sum"), conv=(conv, "sum"))
             .reset_index().sort_values("date"))
    daily = daily[daily["date"] <= res.complete_through]
    if len(daily) < 14:
        return bad, out

    daily["rate"] = daily["conv"] / daily["sessions"].replace(0, np.nan)
    med = float(daily["rate"].median())
    if not np.isfinite(med) or med <= 0:
        return bad, out

    for _, r in daily.iterrows():
        if r["sessions"] <= 0 or not np.isfinite(r["rate"]):
            continue
        if r["rate"] < med * 0.25:
            bad.add(pd.Timestamp(r["date"]))
            where = ("the baseline window -- it was dragging the comparison down and "
                     "masking real problems"
                     if pd.Timestamp(r["date"]) < res.complete_through - pd.Timedelta(days=7)
                     else "the current window")
            out.append(Finding("possible_tracking_break", "high",
                f"{r['date'].date()}: conversion rate {r['rate']:.3%} against a median of "
                f"{med:.3%}, on {int(r['sessions']):,} sessions. Traffic is intact and "
                f"conversions are not -- that pattern is a broken tag far more often than a "
                f"real conversion collapse. This day sits in {where}. Excluded from all "
                f"windows. Verify the conversion event fires before changing anything on "
                f"the site.",
                {"date": str(r["date"].date()), "rate": round(float(r["rate"]), 5),
                 "median_rate": round(med, 5), "sessions": int(r["sessions"])}))

    # A landing page that carried real traffic and then vanished entirely.
    recent = cube[cube["date"] > res.complete_through - pd.Timedelta(days=7)]
    prior = cube[(cube["date"] <= res.complete_through - pd.Timedelta(days=7)) &
                 (cube["date"] > res.complete_through - pd.Timedelta(days=35))]
    if len(prior) and len(recent):
        pr = prior.groupby("landing_page", observed=True)["sessions"].sum()
        rc = recent.groupby("landing_page", observed=True)["sessions"].sum()
        gone = pr[(pr >= 500) & (~pr.index.isin(rc[rc > 0].index))]
        if len(gone):
            out.append(Finding("landing_page_disappeared", "high",
                f"{len(gone)} landing page(s) carried meaningful traffic in the prior month "
                f"and none in the last 7 days. Either the page was removed, a redirect was "
                f"added, or the tag stopped firing on it.",
                {"pages": {str(k): int(v) for k, v in gone.nlargest(8).items()}}))
    return bad, out


def _tracking_breaks(res) -> list:
    """Back-compat shim -- findings only."""
    return tracking_break_dates(res)[1]


# ---------------------------------------------------------------------------
# Sensitivity -- what this brand can and cannot see
# ---------------------------------------------------------------------------

def sensitivity_report(res, cfg: dict, windows) -> dict:
    """
    For each scan level, the smallest CVR change that is detectable at this volume.

    This is the difference between "we found nothing" and "we cannot see anything",
    and it is the number that decides whether a silent morning is reassuring or
    meaningless. Reported per level, with a concrete recommendation when the
    current window is too short to support the depth being asked for.
    """
    cube, conv = res.cube, res.conversion_step
    floors = cfg.get("floors") or {}
    cur_days = int((cfg.get("windows") or {}).get("current_days", 7))
    base_days = int((cfg.get("windows") or {}).get("baseline_days", 28))

    # Sensitivity must describe what is actually ALERTABLE. Pages declared
    # non-converting are excluded from CVR alerting, so counting them here would
    # overstate coverage and understate the typical detectable effect.
    if "is_converting_page" in cube.columns:
        cube = cube[cube["is_converting_page"]]
    win = cube[(cube["date"] >= windows.current_start) &
               (cube["date"] <= windows.current_end)]
    site_sessions = float(win["sessions"].sum())
    site_conv = float(win[conv].sum())
    site_rate = site_conv / site_sessions if site_sessions else 0.0

    levels = {
        "site": [],
        "landing_page": ["landing_page"],
        "utm_source": ["utm_source"],
        "landing_page x utm_source": ["landing_page", "utm_source"],
        "landing_page x utm_source x utm_campaign":
            ["landing_page", "utm_source", "utm_campaign"],
    }

    rows = []
    for name, dims in levels.items():
        if not dims:
            a, r = mde(site_rate, site_sessions, site_sessions * base_days / cur_days)
            rows.append({"level": name, "cells_total": 1, "cells_testable": 1,
                         "sessions_covered_share": 1.0,
                         "median_mde_rel": round(r, 1), "best_mde_rel": round(r, 1)})
            continue
        g = win.groupby(dims, observed=True)["sessions"].sum()
        floor = floors.get("min_sessions_current", 300)
        testable = g[g >= floor]
        mdes = [mde(site_rate, float(s), float(s) * base_days / cur_days)[1]
                for s in testable.values] or [float("inf")]
        rows.append({
            "level": name,
            "cells_total": int(len(g)),
            "cells_testable": int(len(testable)),
            "sessions_covered_share": round(
                float(testable.sum()) / site_sessions, 3) if site_sessions else 0.0,
            "median_mde_rel": round(float(np.median(mdes)), 1),
            "best_mde_rel": round(float(np.min(mdes)), 1),
        })

    recs = []
    deep = [r for r in rows if r["level"].count("x") >= 1]
    if deep and all(r["cells_testable"] == 0 or r["median_mde_rel"] > 50 for r in deep):
        wider = cur_days * 4
        recs.append(
            f"At {site_sessions:,.0f} sessions in a {cur_days}-day window and a "
            f"{site_rate:.2%} conversion rate, multi-dimension slices cannot support "
            f"reliable detection -- the typical cell would need a >50% swing before it "
            f"registered. Widen the current window to ~{wider} days for this depth, or "
            f"run the deeper levels weekly and keep the daily run at landing-page and "
            f"source level."
        )
    thin = [r for r in rows if r["cells_total"] and r["cells_testable"] / r["cells_total"] < 0.05]
    for r in thin:
        recs.append(
            f"`{r['level']}`: only {r['cells_testable']} of {r['cells_total']:,} cells "
            f"clear the volume floor, though they carry "
            f"{r['sessions_covered_share']:.0%} of sessions. The remaining cells are "
            f"untested -- which is not the same as unchanged, and is reported as such."
        )

    return {
        "conversion_rate_observed": round(site_rate, 5),
        "current_window_sessions": int(site_sessions),
        "method": "two-proportion z, alpha=0.05 two-sided, power=0.80",
        "levels": rows,
        "recommendations": recs,
    }

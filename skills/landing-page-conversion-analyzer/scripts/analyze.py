#!/usr/bin/env python3
"""
Landing Page CVR Analyzer -- orchestration.

    python3 analyze.py --data sessions.parquet --config config.yaml \
                       --state state.json --out ./out [--mode auto]

Two modes:
  baseline  first run, or whenever state is absent. Validates, audits the data,
            computes per-slice baselines, and reports what this brand's volume can
            and cannot detect. Emits NO alerts -- there is nothing to compare to yet.
  scan      every run after that. Hierarchical scan, decomposition, gating, state
            diff, ranked output.

DESIGN RULE -- the model never reads the cube.
This script does all the scanning and emits ~15-25 candidate rows. Whether the
brand has 400 landing pages or 40,000, what reaches the language model is the same
small JSON. Token cost per run is flat in cardinality.

No CVR standards or benchmarks are baked in anywhere. Every judgement is relative
to this brand's own trailing history, which is what lets it run on messy data and
get sharper as the data gets cleaner.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import engine as E
from engine import (CellResult, benjamini_hochberg, decompose, build_windows,
                    apply_yoy_baseline, detect_regime_shift, locate_funnel_break,
                    mde, passes_effect_size, passes_floors, test_cell,
                    weighted_ols_slope)
from loader import ContractViolation, load
import hygiene as H
import restatement as R
import stacks as ST


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def read_config(path: str) -> dict:
    if not path or not os.path.exists(path):
        raise ContractViolation(f"config not found: {path}")
    text = open(path).read()
    if path.endswith((".yaml", ".yml")):
        try:
            import yaml
            return yaml.safe_load(text) or {}
        except ImportError:
            raise ContractViolation(
                "PyYAML is not installed and the config is YAML. Install pyyaml, or "
                "supply the same config as JSON."
            )
    return json.loads(text)


def read_state(path: str | None) -> dict | None:
    if not path or not os.path.exists(path):
        return None
    try:
        return json.load(open(path))
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Hierarchical scan
# ---------------------------------------------------------------------------

SCAN_LEVELS = [
    (1, ["landing_page"]),
    (1, ["utm_source"]),
    (1, ["utm_medium"]),
    (2, ["landing_page", "utm_source"]),
    (3, ["landing_page", "utm_source", "utm_campaign"]),
]


def _slice(cube: pd.DataFrame, start, end) -> pd.DataFrame:
    return cube[(cube["date"] >= start) & (cube["date"] <= end)]


def hierarchical_scan(res, cfg: dict, win) -> tuple[list, list, dict]:
    """
    Top-down scan. Deeper levels are only opened where the level above moved, or
    where the cell is large enough to matter on its own.

    The alternative -- testing every cell at every depth -- is both slower and
    statistically worse: thousands of extra comparisons buy nothing but false
    positives to correct away. Measured on a real account, floors plus hierarchy
    cut the tested set by ~99% while still covering ~60% of sessions.

    SCALE: each level groups the two windows ONCE and indexes into the groups.
    The obvious implementation -- a boolean mask over the whole frame per cell --
    is O(rows x cells) and turns a 90-second run into a 40-minute one on a large
    account. Group once, look up many.
    """
    cube = res.cube
    scan_cfg = cfg.get("scan") or {}
    floors = cfg.get("floors") or {}
    stats = cfg.get("stats") or {}
    drill_share = float(scan_cfg.get("drill_revenue_share", 0.01))
    max_depth = int(scan_cfg.get("max_depth", 3))

    # Non-converting pages stay in the totals but are never CVR-alerted on.
    testable = cube[cube["is_converting_page"]] if "is_converting_page" in cube else cube
    base_all = _slice(testable, win.baseline_start, win.baseline_end)
    cur_all = _slice(testable, win.current_start, win.current_end)
    base_all = base_all[~base_all["date"].isin(win.excluded_dates)]
    cur_all = cur_all[~cur_all["date"].isin(win.excluded_dates)]

    total_rev = float(cur_all["revenue"].sum()) or 1.0
    results: list[CellResult] = []
    untested: list[dict] = []
    flagged_parents: dict[int, set] = {1: set(), 2: set()}
    tested_count = 0

    for step in res.steps:
        # ---- level 0: the site itself ---------------------------------------
        site = test_cell(0, {"scope": "site"}, base_all, cur_all, step,
                         aov_source_step=res.conversion_step)
        if site:
            site.mix = decompose(base_all, cur_all, ["utm_source"], step).to_dict()
            site.funnel = locate_funnel_break(base_all, cur_all, res.steps)
            results.append(site)
            tested_count += 1

        if step not in res.alert_steps:
            continue

        # ---- levels 1..N ------------------------------------------------------
        for level, dims in SCAN_LEVELS:
            if level > max_depth:
                continue

            cur_agg = cur_all.groupby(dims, observed=True).agg(
                sessions=("sessions", "sum"), revenue=("revenue", "sum"))
            if cur_agg.empty:
                continue

            # Decide which keys are worth opening BEFORE materialising any group.
            keys = []
            for key, row in cur_agg.iterrows():
                keyt = key if isinstance(key, tuple) else (key,)
                if level >= 2:
                    parent = str(keyt[0])
                    big = float(row["revenue"]) / total_rev >= drill_share
                    if not big and parent not in flagged_parents.get(level - 1, set()):
                        continue
                if float(row["sessions"]) < floors.get("min_sessions_current", 300):
                    if float(row["revenue"]) >= floors.get("min_revenue_at_stake", 2500):
                        untested.append({
                            "level": level,
                            "dims": dict(zip(dims, [str(k) for k in keyt])),
                            "step": step,
                            "cur_sessions": int(row["sessions"]),
                            "cur_revenue": round(float(row["revenue"]), 2),
                            "reason": "below current-window volume floor",
                        })
                    continue
                keys.append(key)

            if not keys:
                continue

            cur_g = cur_all.groupby(dims, observed=True)
            base_g = base_all.groupby(dims, observed=True)
            base_keys = set(base_g.groups.keys())

            for key in keys:
                keyt = key if isinstance(key, tuple) else (key,)
                dimmap = dict(zip(dims, [str(k) for k in keyt]))
                if key not in base_keys:
                    continue                      # no history: nothing to compare to
                c_sub = cur_g.get_group(key)
                b_sub = base_g.get_group(key)

                cell = test_cell(level, dimmap, b_sub, c_sub, step,
                                 aov_source_step=res.conversion_step)
                if cell is None:
                    continue

                if not passes_floors(cell, floors):
                    # Untested is not unchanged. Material-but-untestable cells are
                    # surfaced separately so nobody reads silence as safety.
                    if float(cell.cur_revenue) >= floors.get("min_revenue_at_stake", 2500):
                        untested.append({
                            "level": level, "dims": dimmap, "step": step,
                            "cur_sessions": int(cell.cur_sessions),
                            "cur_revenue": round(float(cell.cur_revenue), 2),
                            "reason": "below baseline volume floor",
                            "mde_rel_if_tested": round(cell.mde_rel, 1),
                        })
                    continue

                tested_count += 1
                results.append(cell)
                if level in (1, 2) and passes_effect_size(cell, stats):
                    flagged_parents[level].add(dimmap.get("landing_page"))

    stats_out = {"cells_tested": tested_count,
                 "cells_untested_but_material": len(untested)}
    return results, untested, stats_out


# ---------------------------------------------------------------------------
# Gating
# ---------------------------------------------------------------------------

def _child_dim(dims: dict) -> str | None:
    """
    The dimension whose mix could be faking this cell's move.

    For a landing-page cell that is `utm_source`: an LP whose traffic swung towards
    a cheaper source shows a genuine within-LP rate drop that is really a source-mix
    effect. Blaming the page for it is the single most common way this class of tool
    misleads a team.
    """
    if "landing_page" in dims and "utm_source" not in dims:
        return "utm_source"
    if "utm_source" in dims and "landing_page" not in dims:
        return "landing_page"
    if "utm_medium" in dims and "utm_source" not in dims:
        return "utm_source"
    if "landing_page" in dims and "utm_source" in dims and "utm_campaign" not in dims:
        return "utm_campaign"
    if dims.get("scope") == "site":
        return "utm_source"
    return None


def _subset(df: pd.DataFrame, dims: dict) -> pd.DataFrame:
    for d, v in dims.items():
        if d in df.columns:
            df = df[df[d].astype(str) == v]
    return df


def gate(results: list, res, cfg: dict, win) -> list:
    """Floors, effect size, false-discovery correction, then mix decomposition."""
    stats = cfg.get("stats") or {}
    q = float(stats.get("fdr_q", 0.10))
    mix_thresh = float(stats.get("mix_dominance_threshold", 0.60))

    candidates = [c for c in results if passes_effect_size(c, stats)]
    if not candidates:
        return []

    flags = benjamini_hochberg([c.p for c in candidates], q)
    survivors = [c for c, ok in zip(candidates, flags) if ok]

    cube = res.cube
    base_all = _slice(cube, win.baseline_start, win.baseline_end)
    cur_all = _slice(cube, win.current_start, win.current_end)
    base_all = base_all[~base_all["date"].isin(win.excluded_dates)]
    cur_all = cur_all[~cur_all["date"].isin(win.excluded_dates)]

    for c in survivors:
        c.survived = True
        cell_dims = {k: v for k, v in c.dims.items() if k != "scope"}
        b_sub = _subset(base_all, cell_dims)
        c_sub = _subset(cur_all, cell_dims)

        # Two independent ways this move could be an illusion, tested separately.
        # Taking only one of them was a real bug: with `device` present, the device
        # decomposition always ran and the source decomposition never did, so
        # source-mix shifts inside a landing page were reported as page problems.
        decs = {}
        if res.optional_dims and c.level > 0:
            decs["|".join(res.optional_dims)] = decompose(
                b_sub, c_sub, list(res.optional_dims), c.step)
        child = _child_dim(c.dims)
        if child and child not in cell_dims:
            decs[child] = decompose(b_sub, c_sub, [child], c.step)

        if decs:
            worst_dim, worst = max(decs.items(), key=lambda kv: kv[1].mix_share)
            c.mix = {**worst.to_dict(), "decomposed_by": worst_dim,
                     "all_dimensions_tested": {
                         k: round(v.mix_share, 3) for k, v in decs.items()}}
            if worst.mix_dominated(mix_thresh):
                movers = ", ".join(
                    f"{m['component']} ({m['share_before']:.1%} -> {m['share_after']:.1%} "
                    f"of traffic, converts at {m['its_cvr']:.2%})"
                    for m in worst.top_mix_movers[:2]) or worst_dim
                c.notes.append(
                    f"NOT A CONVERSION PROBLEM. {worst.mix_share:.0%} of this move is a "
                    f"shift in {worst_dim} mix, not a rate changing within any group -- "
                    f"driven by {movers}. The page is converting as it always did; it is "
                    f"receiving different traffic. Do not send anyone to fix it."
                )

        if not c.funnel and len(res.steps) > 1:
            c.funnel = locate_funnel_break(b_sub, c_sub, res.steps)

        if win.baseline_mode == "yoy":
            c.notes.append("Compared to the same period last year, not the trailing "
                           "window -- the current window is a different traffic regime.")
    return survivors


def annotate_site(site, res, cfg, win) -> None:
    """
    The site row is not an alert, but its decomposition is the most important
    sentence in the whole report -- it decides whether anything below is a real
    conversion problem or the arithmetic of a traffic-mix change. It was being
    skipped because gate() only annotates cells that survived FDR.
    """
    if site is None or not site.mix:
        return
    mix_thresh = float((cfg.get("stats") or {}).get("mix_dominance_threshold", 0.60))
    share = site.mix.get("mix_share", 0.0)
    movers = site.mix.get("top_mix_movers") or []
    if share >= mix_thresh:
        who = ", ".join(
            f"{m['component']} ({m['share_before']:.1%} -> {m['share_after']:.1%} of "
            f"traffic, converts at {m['its_cvr']:.2%})" for m in movers[:2])
        site.notes.append(
            f"Site conversion rate moved {site.rel_change:+.1%}, but {share:.0%} of that "
            f"is traffic MIX, not conversion. {who}. Blended rate fell because the "
            f"traffic composition changed -- individual sources are converting as before. "
            f"Read every finding below in that light before anyone rebuilds a page."
        )
    else:
        site.notes.append(
            f"Site conversion rate moved {site.rel_change:+.1%}, and only {share:.0%} of "
            f"that is traffic mix -- the observed rate change is within the tested components, not a "
            f"composition effect."
        )




# ---------------------------------------------------------------------------
# Root-cause attribution -- one problem should produce one finding
# ---------------------------------------------------------------------------

def attribute_root_causes(survivors: list, res, cfg: dict, win,
                          max_roots: int = 4, max_considered: int = 250) -> list:
    """
    Collapse the many findings a single broken thing produces into one.

    THE PROBLEM. When one cell genuinely breaks, every aggregate containing it moves
    too. A conversion break in one US region produced 25 separate notifications in a
    test run: the region, each source within it, and -- because that region is a
    large share of the business -- most landing pages and traffic sources at site
    level, none of which had anything wrong with them. Twenty-five alerts, one
    cause. A team that receives that once mutes the tool, and they are right to.

    THE METHOD is what an analyst does by hand: take California out, and ask whether
    Google still looks broken. Peel the strongest finding, re-test everything else
    with that cell removed, and anything that no longer clears the effect-size bar
    was never its own problem. Repeat for genuinely independent causes.

    CHOOSING THE ROOT. Not by dollars -- a diluted symptom inside a huge aggregate
    carries more revenue than the concentrated cause that created it. The root has
    the largest RATE move, where the damage is undiluted. Among comparable rate
    moves the broadest wins, because `US / US-CA` explains `US / US-CA / google`
    and not the reverse.

    SCALE. Findings are grouped by their dimension signature once per peel and
    looked up, rather than masking the whole frame per finding. The obvious version
    is O(roots x findings x rows) and does not finish on a real account.

    Nothing is deleted. Explained findings stay in the output, tiered down to
    context and labelled with what accounts for them.
    """
    stats = cfg.get("stats") or {}
    if len(survivors) < 2:
        return survivors

    cube = res.cube
    base_all = _slice(cube, win.baseline_start, win.baseline_end)
    cur_all = _slice(cube, win.current_start, win.current_end)
    base_all = base_all[~base_all["date"].isin(win.excluded_dates)]
    cur_all = cur_all[~cur_all["date"].isin(win.excluded_dates)]

    def drop(df, dim_sets):
        for dims in dim_sets:
            mask = np.ones(len(df), dtype=bool)
            for d, v in dims.items():
                if d in df.columns:
                    mask &= (df[d].astype(str) == v).to_numpy()
            df = df[~mask]
        return df

    ranked = sorted(survivors, key=lambda c: -abs(c.revenue_at_stake))
    considered = ranked[:max_considered]
    untouched = ranked[max_considered:]

    remaining = list(considered)
    roots, peeled = [], []

    for _ in range(max_roots):
        pool = [c for c in remaining if c.level > 0]
        if not pool:
            break

        # CHOOSING THE ROOT is the whole difficulty, and the obvious heuristics all
        # fail. Largest dollars picks a diluted symptom sitting inside a huge
        # aggregate. Largest rate move picks the narrowest cell -- if a market is
        # down 30% across the board, one landing page inside it will always be down
        # 43% by chance, and peeling that explains nothing. Broadest-first picks a
        # site-wide traffic source that is itself a symptom of one region breaking.
        #
        # So score against the actual objective: how many OTHER findings does
        # peeling this one account for? Approximated by containment -- how many
        # candidates sit inside this cell's dimensions. A market contains its
        # regions, sources and pages; one page inside that market contains almost
        # nothing. Candidacy is limited to cells whose rate move is near the
        # strongest, which keeps diluted aggregates out of the running.
        strongest = max(abs(c.rel_change) for c in pool)
        near = [c for c in pool if abs(c.rel_change) >= strongest * 0.60]

        def contains(outer, inner) -> bool:
            od = {k: v for k, v in outer.dims.items() if k != "scope"}
            idict = {k: v for k, v in inner.dims.items() if k != "scope"}
            return outer is not inner and bool(od) and all(
                idict.get(k) == v for k, v in od.items())

        def score(c):
            return (sum(1 for o in pool if contains(c, o)),
                    abs(c.rel_change) * c.cur_sessions)

        root = max(near, key=score)
        roots.append(root)
        remaining.remove(root)
        peeled.append({k: v for k, v in root.dims.items() if k != "scope"})

        b2, c2 = drop(base_all, peeled), drop(cur_all, peeled)

        # Group once per distinct dimension signature, then look up.
        sigs = {}
        for c in remaining:
            cd = tuple(sorted(k for k in c.dims if k != "scope"))
            sigs.setdefault(cd, [])
            sigs[cd].append(c)

        survives = []
        for sig, group in sigs.items():
            if not sig:
                survives.extend(group)
                continue
            dims = list(sig)
            try:
                bg = b2.groupby(dims, observed=True)
                cg = c2.groupby(dims, observed=True)
                bk, ck = set(bg.groups.keys()), set(cg.groups.keys())
            except Exception:
                survives.extend(group)
                continue
            for c in group:
                key = tuple(c.dims[d] for d in dims)
                key = key[0] if len(key) == 1 else key
                if key not in bk or key not in ck:
                    cell = None
                else:
                    cell = test_cell(c.level, c.dims, bg.get_group(key),
                                     cg.get_group(key), c.step,
                                     aov_source_step=res.conversion_step)
                if cell is None or not passes_effect_size(cell, stats):
                    c.explained_by = root.fingerprint()
                    label = " / ".join(str(v) for v in root.dims.values())
                    after = cell.rel_change if cell else 0.0
                    c.notes.insert(0,
                        f"EXPLAINED BY `{label}`. With that cell removed this moves "
                        f"{after:+.1%} instead of {c.rel_change:+.1%} -- below the "
                        f"reporting threshold. Nothing is separately wrong here; it is "
                        f"the same problem through a wider lens. Fix the root and this "
                        f"resolves with it.")
                    c.tier = "context"
                    c.tier_reason = "Explained by another finding; kept for traceability."
                else:
                    c.rel_change_ex_roots = round(cell.rel_change, 4)
                    survives.append(c)
        remaining = survives
        if not remaining:
            break

    # FINAL CONSISTENCY PASS. Root selection is greedy, so a finding can be tested
    # in an early round -- before its own parent has been peeled -- survive, and be
    # reported as an independent problem when it is nothing of the kind. Re-test
    # every survivor once against the fully peeled data so the verdict does not
    # depend on the order roots happened to come out in.
    # `untouched` findings fell past the peel budget and were never tested. They
    # must still face the final pass -- otherwise the cheapest way for a symptom to
    # be reported as an independent problem is simply to rank low enough to be
    # skipped, which is precisely backwards.
    if peeled and (remaining or untouched):
        remaining = remaining + untouched
        untouched = []
        b2, c2 = drop(base_all, peeled), drop(cur_all, peeled)
        sigs = {}
        for c in remaining:
            sigs.setdefault(tuple(sorted(k for k in c.dims if k != "scope")), []).append(c)
        final = []
        for sig, group in sigs.items():
            if not sig:
                final.extend(group)
                continue
            dims = list(sig)
            try:
                bg, cg = b2.groupby(dims, observed=True), c2.groupby(dims, observed=True)
                bk, ck = set(bg.groups.keys()), set(cg.groups.keys())
            except Exception:
                final.extend(group)
                continue
            for c in group:
                key = tuple(c.dims[d] for d in dims)
                key = key[0] if len(key) == 1 else key
                cell = (test_cell(c.level, c.dims, bg.get_group(key), cg.get_group(key),
                                  c.step, aov_source_step=res.conversion_step)
                        if key in bk and key in ck else None)
                if cell is None or not passes_effect_size(cell, stats):
                    owner = min(roots, key=lambda r: (
                        0 if all(c.dims.get(k) == v for k, v in r.dims.items()
                                 if k != "scope") else 1, r.level))
                    c.explained_by = owner.fingerprint()
                    label = " / ".join(str(v) for v in owner.dims.values())
                    after = cell.rel_change if cell else 0.0
                    c.notes.insert(0,
                        f"EXPLAINED BY `{label}`. With the root cause(s) removed this "
                        f"moves {after:+.1%} instead of {c.rel_change:+.1%} -- below the "
                        f"reporting threshold. Not a separate problem; the same one "
                        f"through a different lens. It resolves when the root does.")
                    c.tier = "context"
                    c.tier_reason = "Explained by another finding; kept for traceability."
                else:
                    c.rel_change_ex_roots = round(cell.rel_change, 4)
                    final.append(c)
        remaining = final

    for r in roots:
        r.is_root = True
    return roots + remaining + [c for c in considered
                                if c not in roots and c not in remaining] + untouched


# ---------------------------------------------------------------------------
# Tiering -- evaluation and notification are different decisions
# ---------------------------------------------------------------------------

ALWAYS_NOTIFY = {"possible_tracking_break", "data_restated", "landing_page_disappeared",
                 "cardinality_overflow"}


def assign_tiers(findings: list, total_revenue: float, cfg: dict) -> None:
    """
    Sort findings into notify / flag / context.

    THE POINT: nothing is ever suppressed from the analysis or the artifact. Every
    cell is evaluated identically regardless of size, and every finding is written
    down. This decides only what is worth interrupting someone for.

    Keeping those two decisions separate matters because they answer to different
    people. Whether something is TRUE is a statistical question with one right
    answer. Whether it is worth a push notification is a business preference that
    belongs to the brand, changes by season, and differs between an in-house team
    and an agency watching twelve accounts. Baking a revenue floor into the scan --
    which is what the first version of this did -- silently answers the second
    question inside the first, and then a small market that is genuinely collapsing
    never appears anywhere at all.
    """
    n = (cfg.get("notification") or {})
    nt = n.get("notify") or {}
    ft = n.get("flag") or {}
    min_abs = float(nt.get("min_revenue_at_stake", 5000))
    min_share = float(nt.get("min_revenue_share", 0.02))
    require_both = bool(nt.get("require_both", True))
    flag_abs = float(ft.get("min_revenue_at_stake", 250))

    for c in findings:
        stake = abs(c.revenue_at_stake)
        share = (abs(c.cur_revenue) / total_revenue) if total_revenue else 0.0
        hits_abs = stake >= min_abs
        hits_share = share >= min_share
        qualifies = (hits_abs and hits_share) if require_both else (hits_abs or hits_share)

        if getattr(c, "explained_by", None):
            c.tier = "context"
            c.tier_reason = "Explained by another finding; kept for traceability."
        elif qualifies:
            c.tier = "notify"
            c.tier_reason = (
                f"${stake:,.0f} at stake and {share:.1%} of revenue -- clears both the "
                f"${min_abs:,.0f} and {min_share:.0%} notify bars."
                if require_both else
                f"${stake:,.0f} at stake, {share:.1%} of revenue."
            )
        elif stake >= flag_abs:
            c.tier = "flag"
            missed = ("revenue share" if hits_abs and not hits_share
                      else "dollars at stake" if hits_share and not hits_abs
                      else "both bars")
            c.tier_reason = (
                f"Real and measured, but below the notify bar on {missed} "
                f"(${stake:,.0f}, {share:.1%} of revenue). In the report, not pushed."
            )
        else:
            c.tier = "context"
            c.tier_reason = f"${stake:,.0f} at stake -- recorded, not surfaced."

# ---------------------------------------------------------------------------
# Drift -- the decline too slow to ever trip a weekly test
# ---------------------------------------------------------------------------

def detect_drift(res, cfg: dict, win) -> list:
    """
    A landing page losing 1% of its conversion rate a week never trips a
    week-over-week test and has lost a third of its performance by year end.
    Weighted regression over a long window catches what the anomaly scan cannot.
    """
    cube, conv = res.cube, res.conversion_step
    if "is_converting_page" in cube.columns:
        cube = cube[cube["is_converting_page"]]   # a blog post drifting is not news
    days = int((cfg.get("windows") or {}).get("drift_days", 90))
    floors = cfg.get("floors") or {}
    start = win.current_end - pd.Timedelta(days=days - 1)
    win_df = cube[(cube["date"] >= start) & (cube["date"] <= win.current_end)]
    if win_df.empty:
        return []

    out = []
    totals = win_df.groupby("landing_page", observed=True)["sessions"].sum()
    top = totals[totals >= floors.get("min_sessions_baseline", 1000) * 2].nlargest(40)
    for page in top.index:
        d = (win_df[win_df["landing_page"] == page]
             .groupby("date", observed=True)
             .agg(sessions=("sessions", "sum"), conv=(conv, "sum"))
             .reset_index().sort_values("date"))
        if len(d) < 21:
            continue
        d["rate"] = d["conv"] / d["sessions"].replace(0, np.nan)
        d = d.dropna(subset=["rate"])
        if len(d) < 21:
            continue
        x = (d["date"] - d["date"].min()).dt.days.to_numpy(float)
        slope, p = weighted_ols_slope(x, d["rate"].to_numpy(float),
                                      d["sessions"].to_numpy(float))
        mean_rate = float(np.average(d["rate"], weights=d["sessions"]))
        if mean_rate <= 0:
            continue
        pct_per_30d = slope * 30 / mean_rate
        if p < 0.05 and abs(pct_per_30d) >= 0.05:
            out.append({
                "landing_page": str(page),
                "sessions_in_window": int(totals[page]),
                "mean_rate": round(mean_rate, 5),
                "change_per_30d_rel": round(float(pct_per_30d), 4),
                "projected_90d_rel": round(float(pct_per_30d * 3), 4),
                "p": round(float(p), 4),
                "direction": "declining" if slope < 0 else "improving",
            })
    return sorted(out, key=lambda r: r["change_per_30d_rel"])[:8]


# ---------------------------------------------------------------------------
# State diff -- news, not a standing list of the same problem
# ---------------------------------------------------------------------------

def diff_state(survivors: list, state: dict | None) -> tuple[list, list]:
    prior = {f["fingerprint"]: f for f in (state or {}).get("open_findings", [])}
    seen = set()
    for c in survivors:
        fp = c.fingerprint()
        seen.add(fp)
        if fp not in prior:
            c.status = "new"
        else:
            was = float(prior[fp].get("rel_change", 0.0))
            if abs(c.rel_change) >= abs(was) * 1.25:
                c.status = "worsened"
                c.notes.append(
                    f"Open since {prior[fp].get('first_seen','?')} and getting worse: "
                    f"{was:+.0%} then, {c.rel_change:+.0%} now."
                )
            else:
                c.status = "ongoing"
                c.notes.append(f"Already open since {prior[fp].get('first_seen','?')}. "
                               f"Unchanged in severity -- not new news.")
    resolved = [v for k, v in prior.items() if k not in seen]
    return survivors, resolved


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

def run_baseline(res, cfg: dict, win) -> dict:
    cube, conv = res.cube, res.conversion_step
    floors = cfg.get("floors") or {}
    hist = cube[cube["date"] <= res.complete_through]

    baselines = {}
    for name, dims in [("landing_page", ["landing_page"]),
                       ("utm_source", ["utm_source"]),
                       ("landing_page_x_utm_source", ["landing_page", "utm_source"])]:
        g = hist.groupby(dims, observed=True).agg(
            sessions=("sessions", "sum"), conv=(conv, "sum"), revenue=("revenue", "sum"))
        g = g[g["sessions"] >= floors.get("min_sessions_baseline", 1000)]
        if g.empty:
            baselines[name] = []
            continue
        daily = hist.groupby(dims + ["date"], observed=True).agg(
            s=("sessions", "sum"), c=(conv, "sum")).reset_index()
        daily["rate"] = daily["c"] / daily["s"].replace(0, np.nan)
        stats_by = daily.groupby(dims, observed=True)["rate"].agg(
            ["median", lambda x: x.quantile(.25), lambda x: x.quantile(.75), "std"])
        stats_by.columns = ["median", "p25", "p75", "volatility"]
        joined = g.join(stats_by, how="left")
        rows = []
        for key, r in joined.nlargest(60, "sessions").iterrows():
            keyt = key if isinstance(key, tuple) else (key,)
            rows.append({
                **dict(zip(dims, [str(k) for k in keyt])),
                "sessions": int(r["sessions"]),
                "conversions": int(r["conv"]),
                "cvr_pooled": round(float(r["conv"] / r["sessions"]), 5),
                "cvr_daily_median": round(float(r["median"]), 5) if pd.notna(r["median"]) else None,
                "cvr_p25": round(float(r["p25"]), 5) if pd.notna(r["p25"]) else None,
                "cvr_p75": round(float(r["p75"]), 5) if pd.notna(r["p75"]) else None,
                "volatility": round(float(r["volatility"]), 5) if pd.notna(r["volatility"]) else None,
                "revenue": round(float(r["revenue"]), 2),
            })
        baselines[name] = rows

    return {
        "mode": "baseline",
        "meta": res.meta,
        "data": res.summary(),
        "windows": win.to_dict(),
        "hygiene": H.hygiene_report(res, cfg),
        # The standing picture. Present on EVERY run, findings or not -- a page that
        # shows only anomalies looks broken on a healthy day.
        "channel_stack": ST.stack(res, cfg, win, "utm_medium"),
        "source_stack": ST.stack(res, cfg, win, "utm_source"),
        "page_stack": ST.stack(res, cfg, win, "landing_page"),
        "sensitivity": H.sensitivity_report(res, cfg, win),
        "baselines": baselines,
        "alerts": [],
        "note": ("Baseline run: no alerts by design. There is nothing to compare against "
                 "yet. Resolve the hygiene findings before trusting slice-level output, "
                 "and read the sensitivity table before reading silence as good news."),
    }


def run_scan(res, cfg: dict, win, state: dict | None) -> dict:
    t0 = time.time()
    results, untested, scan_stats = hierarchical_scan(res, cfg, win)
    survivors = gate(results, res, cfg, win)
    survivors, resolved = diff_state(survivors, state)

    floors = cfg.get("floors") or {}
    material = [c for c in survivors if c.level > 0]

    # ORDER MATTERS. Dedup to one finding per cell BEFORE attributing root causes.
    # The scan produces one CellResult per funnel step, so a single broken cell
    # arrives here three or four times over. Feeding those into the peel lets a cell
    # be "explained by itself" (its own purchase-step copy explaining its
    # add-to-cart copy) and loses the root flag when dedup later picks a different
    # copy. One row per cell first, then attribute.
    by_cell: dict = {}
    for c in material:
        key = (c.level, tuple(sorted((k, v) for k, v in c.dims.items())))
        keep = by_cell.get(key)
        if keep is None or abs(c.revenue_at_stake) > abs(keep.revenue_at_stake):
            by_cell[key] = c
    for c in material:
        key = (c.level, tuple(sorted((k, v) for k, v in c.dims.items())))
        keep = by_cell[key]
        if c is not keep:
            keep.notes.append(
                f"Also moved on `{c.step}` ({c.rel_change:+.1%}) -- same cell, same "
                f"underlying cause, reported once.")
    deduped = list(by_cell.values())

    # One problem, one finding: peel the strongest cause and demote what it explains.
    deduped = attribute_root_causes(deduped, res, cfg, win)

    cap = int((cfg.get("scan") or {}).get("top_n_findings", 20))
    cur_rev = float(_slice(res.cube, win.current_start, win.current_end)["revenue"].sum())
    assign_tiers(deduped, cur_rev, cfg)

    # Mix-dominated findings are NOT conversion problems and must never outrank real
    # ones. Ranking purely by dollars puts the biggest composition artifact at the
    # top of the page, and after a few of those nobody reads the report. Two lists,
    # two audiences: conversion is for whoever owns the site, mix for whoever owns
    # the traffic.
    mix_thresh = float((cfg.get("stats") or {}).get("mix_dominance_threshold", 0.60))
    real, mixed = [], []
    for c in deduped:
        share = (c.mix or {}).get("mix_share", 0.0)
        (mixed if share >= mix_thresh else real).append(c)

    order = {"notify": 0, "flag": 1, "context": 2}
    keyf = lambda c: (order[c.tier], not c.is_root, -abs(c.revenue_at_stake))
    real.sort(key=keyf)
    mixed.sort(key=keyf)
    top = [c for c in real if c.tier != "context"][:cap] + \
          [c for c in real if c.tier == "context"][:6]
    mix_top = [c for c in mixed if c.tier != "context"][:cap] + \
              [c for c in mixed if c.tier == "context"][:3]

    site = next((c for c in results
                 if c.level == 0 and c.step == res.conversion_step), None)
    annotate_site(site, res, cfg, win)
    breaks = [f.to_dict() for f in H.tracking_break_dates(res, cfg)[1]]
    restate_findings, restate_report = R.detect(res, state, cfg)

    seen_u, untested_dedup = set(), []
    for u in sorted(untested, key=lambda x: -x["cur_revenue"]):
        k = (u["level"], tuple(sorted(u["dims"].items())))
        if k in seen_u:
            continue
        seen_u.add(k)
        untested_dedup.append(u)
    untested = untested_dedup

    return {
        "mode": "scan",
        "meta": res.meta,
        "windows": win.to_dict(),
        "site": site.to_dict() if site else None,
        "alerts": [c.to_dict() for c in top],
        "mix_shifts": [c.to_dict() for c in mix_top],
        "resolved": resolved,
        "drift": detect_drift(res, cfg, win),
        "tracking_breaks": breaks,
        "untested_but_material": untested[:10],
        "scan_stats": {**scan_stats,
                       "candidates_after_effect_size": len(results),
                       "survived_fdr": len(survivors),
                       "reported": len(top),
                       "suppressed_below_revenue_floor": len(survivors) - len(material),
                       "deduped_to_one_per_cell": len(deduped),
                       "roots": len([c for c in real + mixed if getattr(c, "is_root", False)]),
                       "explained_by_a_root": len([c for c in real + mixed
                                                   if getattr(c, "explained_by", None)]),
                       "conversion_findings": len(real),
                       "mix_shift_findings": len(mixed),
                       "runtime_seconds": round(time.time() - t0, 2)},
        "channel_stack": ST.stack(res, cfg, win, "utm_medium"),
        "source_stack": ST.stack(res, cfg, win, "utm_source"),
        "page_stack": ST.stack(res, cfg, win, "landing_page"),
        "restatement": restate_report,
        "notification": {
            "should_notify": bool([c for c in top + mix_top if c.tier == "notify"])
                             or bool(breaks) or bool(restate_findings and
                                     any(f.code == "data_restated" for f in restate_findings)),
            "notify_count": len([c for c in top + mix_top if c.tier == "notify"]),
            "flag_count": len([c for c in top + mix_top if c.tier == "flag"]),
            "always_notify_triggered": sorted(
                {f.code for f in restate_findings if f.code in ALWAYS_NOTIFY}
                | {t["code"] for t in breaks if t["code"] in ALWAYS_NOTIFY}),
            "policy": (cfg.get("notification") or {}),
            "note": ("Everything was evaluated and everything is in this file. This block "
                     "decides only what is worth interrupting someone for."),
        },
        "data_findings": [f.to_dict() for f in res.findings]
                         + [f.to_dict() for f in restate_findings],
        "note": ("Nothing here is measured against an external benchmark. Every "
                 "comparison is this brand against its own history."),
    }


def build_state(res, cfg, win, out: dict, prior: dict | None) -> dict:
    now = datetime.now(timezone.utc).isoformat()
    prior_map = {f["fingerprint"]: f for f in (prior or {}).get("open_findings", [])}
    open_findings = []
    for a in out.get("alerts", []) + out.get("mix_shifts", []):
        fp = a["fingerprint"]
        open_findings.append({
            "fingerprint": fp,
            "first_seen": prior_map.get(fp, {}).get("first_seen", now),
            "last_seen": now,
            "rel_change": a["rel_change"],
            "revenue_at_stake": a["revenue_at_stake"],
            "dims": a["dims"], "step": a["step"], "level": a["level"],
        })
    return {
        "schema_version": 1,
        "updated_at": now,
        "last_run_mode": out["mode"],
        "complete_through": str(res.complete_through.date()),
        "windows": win.to_dict(),
        "conversion_step": res.conversion_step,
        "funnel": res.steps,
        "open_findings": open_findings,
        # Re-read next run and diffed, so a day that changes after being reported
        # on cannot pass unnoticed. Requires the runtime to RE-READ a trailing
        # window each run rather than appending -- see DATA_CONTRACT.
        "day_fingerprints": R.fingerprint_days(res),
        "baselines": out.get("baselines", (prior or {}).get("baselines", {})),
        "sensitivity": out.get("sensitivity", (prior or {}).get("sensitivity")),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Landing Page CVR Analyzer")
    ap.add_argument("--data", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--state", default=None)
    ap.add_argument("--meta", default=None, help="JSON file or inline JSON of the meta block")
    ap.add_argument("--out", default="./out")
    ap.add_argument("--mode", default="auto", choices=["auto", "baseline", "scan"])
    a = ap.parse_args()

    cfg = read_config(a.config)
    meta = {}
    if a.meta:
        meta = json.load(open(a.meta)) if os.path.exists(a.meta) else json.loads(a.meta)
    state = read_state(a.state)

    try:
        res = load(a.data, cfg, meta)
    except ContractViolation as e:
        os.makedirs(a.out, exist_ok=True)
        payload = {"status": "refused", "reason": str(e),
                   "note": ("The run stopped rather than producing a number that would be "
                            "confidently wrong. Fix the input and re-run.")}
        json.dump(payload, open(os.path.join(a.out, "findings.json"), "w"), indent=2)
        print(json.dumps(payload, indent=2))
        return 2

    # Days where the conversion tag was broken are excluded from every window --
    # a break sitting in the BASELINE quietly lowers the bar and hides everything
    # that goes wrong afterwards.
    break_dates, _ = H.tracking_break_dates(res, cfg)
    win = build_windows(cfg, res.complete_through,
                        set(res.incomplete_dates) | set(break_dates))
    shift = detect_regime_shift(res.daily, win)
    if shift:
        win = apply_yoy_baseline(win, shift)
        if res.cube["date"].min() > win.baseline_start:
            win = build_windows(cfg, res.complete_through,
                                set(res.incomplete_dates) | set(break_dates))
            win.notes.append(
                f"Regime shift detected ({shift}) but there is not enough history for a "
                f"year-on-year baseline. Falling back to the trailing window -- treat "
                f"every finding in this run as provisional."
            )

    mode = a.mode
    if mode == "auto":
        mode = "scan" if (state and state.get("baselines")) else "baseline"

    out = run_baseline(res, cfg, win) if mode == "baseline" else run_scan(res, cfg, win, state)

    # Mark BEFORE serialising -- otherwise the provisional notes never reach the file.
    R.mark_affected(out.get("alerts", []), out.get("restatement", {}))
    R.mark_affected(out.get("mix_shifts", []), out.get("restatement", {}))

    os.makedirs(a.out, exist_ok=True)
    json.dump(out, open(os.path.join(a.out, "findings.json"), "w"), indent=2, default=str)
    json.dump(build_state(res, cfg, win, out, state),
              open(os.path.join(a.out, "state.json"), "w"), indent=2, default=str)

    n = len(out.get("alerts", []))
    print(f"mode={mode}  alerts={n}  "
          f"cells_tested={out.get('scan_stats',{}).get('cells_tested','-')}  "
          f"-> {a.out}/findings.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

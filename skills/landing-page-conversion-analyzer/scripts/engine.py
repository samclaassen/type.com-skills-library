"""
Landing Page CVR Analyzer -- statistical core.

Statistics, windowing, funnel handling, decomposition, sensitivity. No I/O.
Imported by loader.py, hygiene.py and analyze.py.

Dependency-light on purpose: pandas + numpy only. The normal CDF and the
Benjamini-Hochberg procedure are implemented here rather than pulled from scipy,
so this survives being rebuilt inside another runtime.

SCALE NOTE -- this module only ever sees the aggregated cube, never raw rows.
Everything below operates on grouped frames whose row count is bounded by
cardinality, not by traffic. A brand doing 5M sessions/week and a brand doing
50k produce cubes of similar size; that is the whole point of the design.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Sequence

import numpy as np
import pandas as pd

DIM_COLUMNS = ["landing_page", "utm_source", "utm_medium", "utm_campaign", "country"]
OPTIONAL_DIMS = ["device", "new_vs_returning"]
BASE_MEASURES = ["sessions", "revenue"]

# Source values that are not really sources. Kept distinct on purpose -- they
# mean different things and collapsing them hides real problems. See DATA_CONTRACT §6.
UNRESOLVED_SOURCES = {"(not set)", "(data not available)", "", "(other)"}
DIRECT_SOURCES = {"(direct)", "(none)"}


# ---------------------------------------------------------------------------
# Statistics
# ---------------------------------------------------------------------------

def norm_sf(z: float) -> float:
    """Upper-tail probability of the standard normal. No scipy."""
    return 0.5 * math.erfc(z / math.sqrt(2.0))


def two_proportion_p(x1: float, n1: float, x2: float, n2: float) -> tuple[float, float]:
    """
    Two-sided two-proportion z-test. Arm 1 = current window, arm 2 = baseline.

    Both arms carry their real sample size, so a baseline built on thin volume
    widens the interval instead of being treated as ground truth.
    """
    if n1 <= 0 or n2 <= 0:
        return 0.0, 1.0
    p1 = x1 / n1
    pooled = (x1 + x2) / (n1 + n2)
    if pooled <= 0 or pooled >= 1:
        return 0.0, 1.0
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    if se == 0:
        return 0.0, 1.0
    z = (p1 - (x2 / n2)) / se
    return z, 2.0 * norm_sf(abs(z))


def benjamini_hochberg(pvals: Sequence[float], q: float) -> list[bool]:
    """
    Which hypotheses survive at false-discovery-rate q.

    Bonferroni is the wrong tool here: across thousands of cells it rejects
    everything real along with the noise. BH controls the *share* of findings
    that are false, which is the contract an operator can actually use --
    "roughly one in ten of these is noise" is usable; "we found nothing" is not.
    """
    m = len(pvals)
    if m == 0:
        return []
    order = np.argsort(pvals)
    survived = [False] * m
    max_k = 0
    for rank, idx in enumerate(order, start=1):
        if pvals[idx] <= q * rank / m:
            max_k = rank
    for rank, idx in enumerate(order, start=1):
        if rank <= max_k:
            survived[idx] = True
    return survived


# Two-sided alpha=0.05, power=0.80
_MDE_Z = 1.959964 + 0.841621


def mde(p: float, n_cur: float, n_base: float,
        z: float = _MDE_Z) -> tuple[float, float]:
    """
    Minimum detectable effect: the smallest CVR change this cell could reveal.

    Returns (absolute percentage points, relative %).

    This is the number that separates "nothing is wrong" from "nothing is
    visible", and almost no commercial tool reports it. A cell with 300 sessions
    at a 2.7% CVR cannot detect anything smaller than a ~108% relative move; a
    tool that scans it and stays quiet is not reassuring, it is blind.
    """
    if p <= 0 or p >= 1 or n_cur <= 0 or n_base <= 0:
        return float("inf"), float("inf")
    se = math.sqrt(p * (1 - p) * (1 / n_cur + 1 / n_base))
    a = z * se
    return a * 100.0, (a / p) * 100.0


def weighted_ols_slope(x: np.ndarray, y: np.ndarray, w: np.ndarray) -> tuple[float, float]:
    """
    Session-weighted linear slope of a daily rate, plus a two-sided p-value.

    Weighting matters: unweighted, a 40-session Sunday moves the line as much as
    a 40,000-session Monday.
    """
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    w = np.asarray(w, dtype=float)
    n = len(x)
    if n < 4 or w.sum() <= 0:
        return 0.0, 1.0
    xm = np.average(x, weights=w)
    ym = np.average(y, weights=w)
    sxx = float(np.sum(w * (x - xm) ** 2))
    if sxx <= 0:
        return 0.0, 1.0
    slope = float(np.sum(w * (x - xm) * (y - ym)) / sxx)
    resid = y - (ym + slope * (x - xm))
    dof = n - 2
    s2 = float(np.sum(w * resid ** 2) / w.sum()) * n / dof
    if s2 <= 0:
        return slope, 1.0
    se = math.sqrt(s2 / sxx)
    if se == 0:
        return slope, 1.0
    return slope, float(2.0 * norm_sf(abs(slope / se)))


# ---------------------------------------------------------------------------
# Shift-share decomposition -- the heart of it
# ---------------------------------------------------------------------------

@dataclass
class Decomposition:
    """
    Splits a change in a blended rate into the three things that can cause it.

    within       the components genuinely converted differently
    mix          the components are unchanged; their traffic shares moved
    interaction  the components that changed are also the ones that grew or shrank

    A blended CVR drop that is mostly `mix` is not a conversion problem. It is
    usually paid scaling into colder traffic, and telling a team to fix a landing
    page over it is how these tools lose their audience.
    """
    total_delta: float
    within: float
    mix: float
    interaction: float
    top_mix_movers: list = field(default_factory=list)

    @property
    def mix_share(self) -> float:
        d = abs(self.within) + abs(self.mix) + abs(self.interaction)
        return abs(self.mix) / d if d > 0 else 0.0

    def mix_dominated(self, threshold: float = 0.60) -> bool:
        return self.mix_share >= threshold

    def to_dict(self) -> dict:
        d = {k: (round(v, 6) if isinstance(v, float) else v)
             for k, v in asdict(self).items()}
        d["mix_share"] = round(self.mix_share, 4)
        return d


def decompose(base: pd.DataFrame, cur: pd.DataFrame, by: list[str],
              step: str, top_movers: int = 3) -> Decomposition:
    """
    Decompose the change in a blended step-rate between two windows across `by`.

    Components absent from one window are treated as zero-share there, which is
    correct: a source that did not exist last month contributes pure mix.
    """
    cols = ["sessions", step]
    b = base.groupby(by, dropna=False, observed=True)[cols].sum()
    c = cur.groupby(by, dropna=False, observed=True)[cols].sum()
    idx = b.index.union(c.index)
    b = b.reindex(idx, fill_value=0)
    c = c.reindex(idx, fill_value=0)

    bs = float(b["sessions"].sum())
    cs = float(c["sessions"].sum())
    if bs <= 0 or cs <= 0:
        return Decomposition(0.0, 0.0, 0.0, 0.0)

    wb = (b["sessions"] / bs).to_numpy(dtype=float)
    wc = (c["sessions"] / cs).to_numpy(dtype=float)
    with np.errstate(divide="ignore", invalid="ignore"):
        rb = np.nan_to_num(b[step].to_numpy(float) / b["sessions"].to_numpy(float))
        rc = np.nan_to_num(c[step].to_numpy(float) / c["sessions"].to_numpy(float))

    within = float(np.sum(wb * (rc - rb)))
    mixv = float(np.sum((wc - wb) * rb))
    inter = float(np.sum((wc - wb) * (rc - rb)))
    total = float(c[step].sum() / cs - b[step].sum() / bs)

    contrib = (wc - wb) * rb
    order = np.argsort(-np.abs(contrib))[:top_movers]
    movers = []
    for i in order:
        if abs(contrib[i]) <= 0:
            continue
        label = idx[i] if not isinstance(idx[i], tuple) else " / ".join(str(x) for x in idx[i])
        movers.append({
            "component": str(label),
            "share_before": round(float(wb[i]), 4),
            "share_after": round(float(wc[i]), 4),
            "its_cvr": round(float(rb[i]), 5),
            "effect_on_blended_pp": round(float(contrib[i]) * 100, 4),
        })
    return Decomposition(total, within, mixv, inter, movers)


# ---------------------------------------------------------------------------
# Windows
# ---------------------------------------------------------------------------

@dataclass
class Windows:
    current_start: pd.Timestamp
    current_end: pd.Timestamp
    baseline_start: pd.Timestamp
    baseline_end: pd.Timestamp
    excluded_dates: set = field(default_factory=set)
    baseline_mode: str = "trailing"     # or "yoy"
    notes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "current": [str(self.current_start.date()), str(self.current_end.date())],
            "baseline": [str(self.baseline_start.date()), str(self.baseline_end.date())],
            "baseline_mode": self.baseline_mode,
            "excluded_dates": sorted(str(pd.Timestamp(d).date()) for d in self.excluded_dates),
            "notes": self.notes,
        }


def build_windows(cfg: dict, complete_through: pd.Timestamp,
                  incomplete_dates: set | None = None) -> Windows:
    """
    Current and baseline windows, anchored to the last date the data vouches for.

    Never anchored to "today". The most recent day of analytics is incomplete and
    incomplete data looks exactly like a catastrophe -- see DATA_CONTRACT §6.
    """
    w = cfg.get("windows", {}) or {}
    cur_days = int(w.get("current_days", 7))
    base_days = int(w.get("baseline_days", 28))
    gap = int(w.get("gap_days", 0))

    end = pd.Timestamp(complete_through)
    cur_start = end - pd.Timedelta(days=cur_days - 1)
    base_end = cur_start - pd.Timedelta(days=1 + gap)
    base_start = base_end - pd.Timedelta(days=base_days - 1)

    excluded = set(incomplete_dates or [])
    notes = []
    for ev in cfg.get("known_events") or []:
        rng = pd.date_range(pd.Timestamp(ev["start"]), pd.Timestamp(ev["end"]))
        excluded.update(rng)
        notes.append(f"excluded from baseline: {ev.get('name','event')} "
                     f"({ev['start']}..{ev['end']})")
    return Windows(cur_start, end, base_start, base_end, excluded, "trailing", notes)


def apply_yoy_baseline(win: Windows, reason: str) -> Windows:
    """
    Swap the trailing baseline for the same calendar period last year.

    Used when the current window sits inside a declared peak period or a detected
    volume regime shift. During BFCM a trailing-28d baseline compares peak to
    pre-peak and flags the entire site as an anomaly every single day -- correct
    arithmetic, useless output.
    """
    shift = pd.Timedelta(days=364)   # 52 weeks: preserves day-of-week alignment
    return Windows(
        current_start=win.current_start, current_end=win.current_end,
        baseline_start=win.current_start - shift, baseline_end=win.current_end - shift,
        excluded_dates=win.excluded_dates, baseline_mode="yoy",
        notes=win.notes + [f"baseline switched to same period last year: {reason}"],
    )


def detect_regime_shift(daily: pd.DataFrame, win: Windows,
                        ratio_threshold: float = 1.5) -> str | None:
    """
    Is the current window a different traffic regime from its baseline?

    Pure volume check, no calendar knowledge. Catches peak periods the brand
    forgot to declare, and site migrations that changed traffic wholesale.
    """
    cur = daily[(daily["date"] >= win.current_start) & (daily["date"] <= win.current_end)]
    base = daily[(daily["date"] >= win.baseline_start) & (daily["date"] <= win.baseline_end)]
    if cur.empty or base.empty:
        return None
    cd = cur["sessions"].sum() / max(len(cur["date"].unique()), 1)
    bd = base["sessions"].sum() / max(len(base["date"].unique()), 1)
    if bd <= 0:
        return None
    ratio = cd / bd
    if ratio >= ratio_threshold:
        return f"daily sessions running {ratio:.1f}x the trailing baseline"
    if ratio <= 1 / ratio_threshold:
        return f"daily sessions running {ratio:.2f}x the trailing baseline"
    return None


def dow_adjusted_rate(base: pd.DataFrame, cur: pd.DataFrame,
                      step: str) -> tuple[float, float, float]:
    """
    Baseline rate re-weighted to the current window's day-of-week composition.

    Returns (adjusted_rate, baseline_sessions, baseline_step_count).

    Why bother: a current window holding two Mondays and no Saturday shows a shift
    that is purely calendar. Comparing Tuesdays to Tuesdays removes it without
    fitting a model to anything. On a clean 7-day window this is a no-op -- which
    is exactly why 7 is the default.
    """
    bs = float(base["sessions"].sum())
    bc = float(base[step].sum())
    if bs <= 0:
        return 0.0, 0.0, 0.0
    pooled = bc / bs
    if cur.empty:
        return pooled, bs, bc

    bg = base.groupby(base["date"].dt.dayofweek, observed=True)[["sessions", step]].sum()
    cg = cur.groupby(cur["date"].dt.dayofweek, observed=True)["sessions"].sum()
    total = float(cg.sum())
    if total <= 0:
        return pooled, bs, bc

    acc = 0.0
    for dow, cur_sess in cg.items():
        if dow in bg.index and bg.loc[dow, "sessions"] > 0:
            rate = bg.loc[dow, step] / bg.loc[dow, "sessions"]
        else:
            rate = pooled          # no matching weekday in baseline: fall back, don't invent
        acc += (cur_sess / total) * rate
    return float(acc), bs, bc


# ---------------------------------------------------------------------------
# Cell testing
# ---------------------------------------------------------------------------

@dataclass
class CellResult:
    level: int
    dims: dict
    step: str
    cur_sessions: float
    cur_count: float
    cur_revenue: float
    cur_rate: float
    base_sessions: float
    base_count: float
    base_rate_adj: float
    rel_change: float
    abs_change_pp: float
    z: float
    p: float
    revenue_at_stake: float
    direction: str
    mde_rel: float
    mix: dict | None = None
    funnel: dict | None = None
    status: str = "new"
    tier: str = "context"          # notify | flag | context -- see analyze.assign_tiers
    tier_reason: str = ""
    is_root: bool = False          # a distinct problem, not a symptom of another
    explained_by: str | None = None
    rel_change_ex_roots: float | None = None
    survived: bool = False
    notes: list = field(default_factory=list)

    def fingerprint(self) -> str:
        key = "|".join(f"{k}={v}" for k, v in sorted(self.dims.items()))
        return f"L{self.level}:{self.step}:{key}:{self.direction}"

    def to_dict(self) -> dict:
        d = asdict(self)
        for k in ("cur_rate", "base_rate_adj"):
            d[k] = round(d[k], 5)
        for k in ("rel_change", "abs_change_pp", "z", "p", "mde_rel"):
            d[k] = round(d[k], 4)
        for k in ("revenue_at_stake", "cur_revenue"):
            d[k] = round(d[k], 2)
        d["fingerprint"] = self.fingerprint()
        return d


def test_cell(level: int, dims: dict, base: pd.DataFrame, cur: pd.DataFrame,
              step: str, aov_source_step: str | None = None) -> CellResult | None:
    """Compare one cell's current window against its day-of-week-adjusted baseline."""
    cs = float(cur["sessions"].sum())
    if cs <= 0:
        return None
    cc = float(cur[step].sum())
    cr = float(cur["revenue"].sum())

    base_adj, bs, bc = dow_adjusted_rate(base, cur, step)
    if base_adj <= 0 or bs <= 0:
        return None

    cur_rate = cc / cs
    rel = (cur_rate - base_adj) / base_adj
    abs_pp = (cur_rate - base_adj) * 100.0
    z, p = two_proportion_p(cc, cs, base_adj * bs, bs)
    _, mde_rel = mde(base_adj, cs, bs)

    # Dollars, not percentages. AOV comes from the baseline window and from the
    # revenue-bearing step, so a CVR move and an AOV move never get tangled.
    rev_step = aov_source_step or step
    rev_denom = float(base[rev_step].sum())
    base_aov = (float(base["revenue"].sum()) / rev_denom) if rev_denom > 0 else 0.0
    stake = (base_adj - cur_rate) * cs * base_aov     # positive = revenue being lost

    return CellResult(
        level=level, dims=dims, step=step,
        cur_sessions=cs, cur_count=cc, cur_revenue=cr, cur_rate=cur_rate,
        base_sessions=bs, base_count=bc, base_rate_adj=base_adj,
        rel_change=rel, abs_change_pp=abs_pp, z=z, p=p,
        revenue_at_stake=stake, direction="down" if rel < 0 else "up",
        mde_rel=mde_rel,
    )


def locate_funnel_break(base: pd.DataFrame, cur: pd.DataFrame,
                        steps: list[str]) -> dict:
    """
    Which transition in the funnel actually moved.

    A CVR drop with healthy view and cart rates but a collapsed checkout rate is a
    checkout bug -- an ecom engineering ticket. The same headline drop with view
    rates already down is a traffic-quality problem -- a paid media decision. One
    number cannot tell those two teams apart; this can.

    Returns each transition's before/after rate and flags the worst one.
    """
    stages = ["sessions"] + steps
    out, worst, worst_delta = [], None, 0.0
    for prev, nxt in zip(stages, stages[1:]):
        bp, bn = float(base[prev].sum()), float(base[nxt].sum())
        cp, cn = float(cur[prev].sum()), float(cur[nxt].sum())
        if bp <= 0 or cp <= 0:
            continue
        br, cr_ = bn / bp, cn / cp
        rel = (cr_ - br) / br if br > 0 else 0.0
        out.append({
            "transition": f"{prev} -> {nxt}",
            "base_rate": round(br, 5),
            "cur_rate": round(cr_, 5),
            "rel_change": round(rel, 4),
        })
        if rel < worst_delta:
            worst_delta, worst = rel, f"{prev} -> {nxt}"
    return {"transitions": out, "worst_transition": worst,
            "worst_rel_change": round(worst_delta, 4)}


def passes_floors(c: CellResult, floors: dict) -> bool:
    return (
        c.cur_sessions >= floors.get("min_sessions_current", 300)
        and c.base_sessions >= floors.get("min_sessions_baseline", 1000)
        and c.base_count >= floors.get("min_conversions_baseline", 25)
    )


def passes_effect_size(c: CellResult, st: dict) -> bool:
    return (
        abs(c.rel_change) >= st.get("min_relative_change", 0.10)
        and abs(c.abs_change_pp) >= st.get("min_absolute_change_pp", 0.15)
    )

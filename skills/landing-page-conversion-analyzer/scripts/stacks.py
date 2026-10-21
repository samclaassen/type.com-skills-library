"""
Standing performance tables -- what the brand sees whether or not anything is wrong.

Findings answer "what broke". These answer "how are we doing", which is the question
a team actually opens the page for on the 29 days out of 30 when nothing broke. A
tool that returns only anomalies looks empty on a quiet day, and an empty page reads
as a broken tool rather than a healthy account.

Every row is always present. Size decides the notification tier, never whether a row
is computed or shown.

STANDARDISED RATES apply here as much as they do across markets. A landing page fed
by cheap paid traffic and one fed by email will always show different conversion
rates, and the gap says nothing about either page. `standardised_cvr` re-weights each
row to the portfolio's own source mix, so pages and channels are compared on
something they can actually be compared on.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from engine import dow_adjusted_rate, mde, two_proportion_p


# Standardisation only means something when the `across` dimension genuinely spans
# the rows. A landing page or a market receives traffic from many sources, so
# re-weighting to the portfolio mix is a real correction. A SOURCE, by contrast,
# usually carries a single medium -- standardising it returns its own rate back and
# would print a figure that looks like a correction and is not one. Reporting
# nothing beats reporting a reassuring identity.
STANDARDISABLE = {"landing_page", "country", "region"}
MIN_MIX_COVERAGE = 0.50


def standardised_rates(win: pd.DataFrame, step: str, by: str = "country",
                       across: str = "utm_source",
                       min_sessions: int = 100) -> pd.DataFrame:
    """
    Each market's conversion rate re-weighted to the PORTFOLIO's traffic mix.

    "If this market received the same mix of traffic as the business overall, what
    would it convert at?"

    Direct standardisation -- the same method demographers use to compare mortality
    between populations with different age structures, and it is the right tool for
    exactly the same reason. Without it, every cross-market comparison is really a
    comparison of media buying, and a market gets praised or blamed for its source
    mix.

    Worked example of why the raw number lies. Suppose two markets have IDENTICAL
    conversion within every source:

        market    paid share   paid cvr   organic share   organic cvr   RAW cvr
        US            40%         1.0%          60%           4.0%       2.80%
        DE            85%         1.0%          15%           4.0%       1.45%

    DE looks like half the market US is. Standardised to the portfolio mix, both
    come out at the same rate, because nothing about DE converts worse -- the media
    team simply buys differently there. The raw gap is a media fact wearing a
    conversion costume.

    Returns raw and standardised rates plus the gap between them; a large gap is
    itself the finding.
    """
    g = (win.groupby([by, across], observed=True)
            .agg(sessions=("sessions", "sum"), conv=(step, "sum")).reset_index())
    if g.empty:
        return pd.DataFrame()

    # Portfolio weights: the standard population every market is projected onto.
    port = g.groupby(across, observed=True)["sessions"].sum()
    total = float(port.sum())
    if total <= 0:
        return pd.DataFrame()
    weights = (port / total).to_dict()

    # Portfolio-wide rate per source, used where a market has no volume in one.
    port_rate = (g.groupby(across, observed=True)
                   .apply(lambda d: d["conv"].sum() / max(d["sessions"].sum(), 1),
                          include_groups=False)).to_dict()

    rows = []
    for market, sub in g.groupby(by, observed=True):
        sess = float(sub["sessions"].sum())
        conv = float(sub["conv"].sum())
        if sess < min_sessions:
            continue
        rates = {r[across]: (r["conv"] / r["sessions"] if r["sessions"] > 0 else None)
                 for _, r in sub.iterrows()}
        covered = num = 0.0
        for src, w in weights.items():
            rate = rates.get(src)
            if rate is None:
                continue                  # no coverage: excluded, not back-filled
            num += w * rate
            covered += w
        std = (num / covered) if covered > 0 else None
        raw = conv / sess if sess else 0.0
        rows.append({
            by: str(market),
            "sessions": int(sess),
            "conversions": int(conv),
            "raw_cvr": round(raw, 5),
            "standardised_cvr": round(std, 5) if std is not None else None,
            "mix_coverage": round(covered, 3),
            "mix_effect_pp": round((raw - std) * 100, 4) if std is not None else None,
        })
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# The market stack -- biggest to smallest, with each one's dollar impact
# ---------------------------------------------------------------------------


def stack(res, cfg: dict, windows, dim: str) -> list:
    """
    Every value of `dim`, ranked by revenue, with its contribution to the total.

    Contribution splits into two numbers that mean opposite things:

      within_contribution   this row converted differently than it used to.
                            A real change. Somebody owns it.
      mix_contribution      this row's SHARE of traffic changed while its own
                            rate held. The blended number moved; nothing about the
                            market did. Usually a media decision, not a problem.

    Both are reported in percentage points of blended rate and in dollars, and they
    sum with the interaction term to the total move. Every market appears, however
    small -- size decides the notification tier, never whether it is analysed.
    """
    cube, step = res.cube, res.conversion_step
    cur = cube[(cube["date"] >= windows.current_start) &
               (cube["date"] <= windows.current_end)]
    base = cube[(cube["date"] >= windows.baseline_start) &
                (cube["date"] <= windows.baseline_end)]
    cur = cur[~cur["date"].isin(windows.excluded_dates)]
    base = base[~base["date"].isin(windows.excluded_dates)]
    if cur.empty or base.empty:
        return []

    cur_sess_total = float(cur["sessions"].sum())
    base_sess_total = float(base["sessions"].sum())
    cur_rev_total = float(cur["revenue"].sum())
    if cur_sess_total <= 0 or base_sess_total <= 0:
        return []

    std = (standardised_rates(cur, step, by=dim, across="utm_source")
           if dim in STANDARDISABLE else pd.DataFrame())
    std_map = ({r[dim]: r for _, r in std.iterrows()} if not std.empty else {})

    keys = sorted(set(cur[dim].astype(str)) | set(base[dim].astype(str)))
    rows = []
    for key in keys:
        c = cur[cur[dim].astype(str) == key]
        b = base[base[dim].astype(str) == key]
        cs = float(c["sessions"].sum())
        bs = float(b["sessions"].sum())
        if cs <= 0 and bs <= 0:
            continue
        cc = float(c[step].sum())
        crev = float(c["revenue"].sum())

        base_rate, base_sessions, base_conv = dow_adjusted_rate(b, c, step)
        cur_rate = cc / cs if cs else 0.0
        w_cur = cs / cur_sess_total
        w_base = bs / base_sess_total

        within_pp = w_base * (cur_rate - base_rate) * 100
        mix_pp = (w_cur - w_base) * base_rate * 100

        base_aov = (float(b["revenue"].sum()) / base_conv) if base_conv > 0 else 0.0
        rev_impact = (cur_rate - base_rate) * cs * base_aov   # negative = losing money

        rel = ((cur_rate - base_rate) / base_rate) if base_rate > 0 else 0.0
        z, p = two_proportion_p(cc, cs, base_rate * base_sessions, base_sessions) \
            if base_sessions > 0 else (0.0, 1.0)
        _, mde_rel = mde(base_rate, cs, base_sessions) if base_sessions > 0 \
            else (float("inf"), float("inf"))

        s = std_map.get(key, {})
        # Low coverage means the row spans too little of the portfolio mix for the
        # standardised figure to mean anything. Report it as absent, not as equal.
        # (`if s` is unsafe here -- s may be a pandas Series, which refuses truthiness.)
        try:
            cov = float(s.get("mix_coverage"))
        except (TypeError, ValueError, AttributeError):
            cov = None
        if cov is not None and cov < MIN_MIX_COVERAGE:
            s = {}
        rows.append({
            dim: key,
            "sessions": int(cs),
            "revenue": round(crev, 2),
            "revenue_share": round(crev / cur_rev_total, 4) if cur_rev_total else 0.0,
            "traffic_share": round(w_cur, 4),
            "traffic_share_before": round(w_base, 4),
            "cvr": round(cur_rate, 5),
            "cvr_baseline": round(base_rate, 5),
            "rel_change": round(rel, 4),
            "p": round(p, 5),
            "mde_rel": round(mde_rel, 1) if np.isfinite(mde_rel) else None,
            "testable": bool(np.isfinite(mde_rel) and abs(rel) >= (mde_rel / 100.0)),
            "revenue_impact": round(rev_impact, 2),
            "within_contribution_pp": round(within_pp, 4),
            "mix_contribution_pp": round(mix_pp, 4),
            "standardised_cvr": s.get("standardised_cvr"),
            "mix_effect_pp": s.get("mix_effect_pp"),
        })

    rows.sort(key=lambda r: -r["revenue"])
    for i, r in enumerate(rows, start=1):
        r["rank"] = i
    return rows



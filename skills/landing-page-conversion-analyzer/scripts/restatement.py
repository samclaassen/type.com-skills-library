"""
What changed since the last look.

NOT a data-lag system. The brand schedules this when their data is settled and
refreshes the artifact whenever they want; nobody should have to reason about
processing lag to use it. This module exists for the one thing a schedule cannot
cover.

When someone hits refresh and a number has moved, they need to know. Analytics
platforms restate: the GA4 API reprocesses for a day or two, and the BigQuery daily
export takes late-arriving events after the table is first written. So a figure that
was on screen yesterday can be different today, and without a record there is no
answer to "why is this different?" -- which is the moment a team quietly stops
trusting the tool.

So: fingerprint each day's totals, and on the next run say plainly which previously
shown numbers changed. Silent when nothing moved, which is most of the time.

REQUIREMENT ON THE RUNTIME: re-read a trailing window each run rather than appending
new days to a cache. You cannot notice a number changing if you never look at it
twice.
"""

from __future__ import annotations

import pandas as pd

from loader import Finding


def fingerprint_days(res, lookback: int = 14) -> dict:
    """Per-day totals for the trailing window, small enough to keep in state."""
    cube = res.cube
    start = res.complete_through - pd.Timedelta(days=lookback - 1)
    d = (cube[(cube["date"] >= start) & (cube["date"] <= res.complete_through)]
         .groupby("date", observed=True)
         .agg(sessions=("sessions", "sum"),
              conversions=(res.conversion_step, "sum"),
              revenue=("revenue", "sum")))
    return {
        str(idx.date()): {
            "sessions": int(r["sessions"]),
            "conversions": int(r["conversions"]),
            "revenue": round(float(r["revenue"]), 2),
        }
        for idx, r in d.iterrows()
    }


def detect(res, state: dict | None, cfg: dict | None = None) -> tuple[list, dict]:
    """
    Compare this run's view of history against the previous run's.

    Returns (findings, report). The report carries the settling curve and the
    per-day deltas, and goes into the artifact even when nothing is material --
    a stable curve is the evidence that the numbers can be trusted.
    """
    cfg = cfg or {}
    tol = float((cfg.get("freshness") or {}).get("restatement_tolerance", 0.01))
    now = fingerprint_days(res)
    prior = ((state or {}).get("day_fingerprints") or {})

    findings, moved = [], []
    for day, cur in now.items():
        was = prior.get(day)
        if not was or was.get("sessions", 0) <= 0:
            continue
        age = (res.complete_through - pd.Timestamp(day)).days

        s_d = (cur["sessions"] - was["sessions"]) / was["sessions"]
        c_d = ((cur["conversions"] - was["conversions"]) / was["conversions"]
               if was["conversions"] else 0.0)
        r_d = ((cur["revenue"] - was["revenue"]) / was["revenue"]
               if was["revenue"] else 0.0)

        if max(abs(s_d), abs(c_d), abs(r_d)) >= tol:
            moved.append({
                "date": day, "days_old_when_reread": int(age),
                "sessions_change": round(s_d, 4),
                "conversions_change": round(c_d, 4),
                "revenue_change": round(r_d, 4),
                "was": was, "now": cur,
            })

    if moved:
        worst = max(moved, key=lambda m: abs(m["conversions_change"]))
        findings.append(Finding("data_restated", "high",
            f"{len(moved)} day(s) already analysed have changed since the last run -- "
            f"worst is {worst['date']}, conversions {worst['conversions_change']:+.1%} "
            f"({worst['was']['conversions']:,} -> {worst['now']['conversions']:,}) when "
            f"re-read {worst['days_old_when_reread']} day(s) later. Anything reported on "
            f"those days was computed from numbers that no longer hold. Any finding "
            f"resting on them is marked provisional. Nothing is wrong with the data -- "
            f"analytics platforms restate as late events arrive -- but anyone who saw the "
            f"earlier figure should know it moved.",
            {"days_moved": moved[:8], "tolerance": tol}))

    return findings, {"days_compared": len(prior), "days_moved": len(moved),
                      "moved": moved[:10], "tolerance": tol,
                      "note": ("Silent unless a previously reported day changed. This is "
                               "not a lag model -- schedule the run when the data is "
                               "settled and refresh whenever.")}


def mark_affected(alerts: list, report: dict) -> None:
    """
    Flag any open finding whose windows overlap a restated day.

    A finding computed partly from numbers that have since changed is not
    necessarily wrong, but it is no longer verified, and saying so costs nothing
    while silently letting it stand costs trust.
    """
    days = {m["date"] for m in report.get("moved", [])}
    if not days:
        return
    for a in alerts:
        a.setdefault("notes", []).append(
            f"Computed over a window overlapping {len(days)} restated day(s) "
            f"({', '.join(sorted(days)[:3])}). The underlying numbers moved after the "
            f"previous run -- treat this finding as provisional until the next run "
            f"confirms it."
        )

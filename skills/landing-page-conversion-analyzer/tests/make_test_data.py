#!/usr/bin/env python3
"""
Synthetic session cube with planted signals, for verifying the analyzer.

Signals the engine MUST catch:
  A  real CVR break   /checkout-lp x google/cpc, purchase step -35%, located at
                      begin_checkout -> purchase (upper funnel healthy)
  C  tracking break   one day where purchases collapse site-wide, traffic intact
  D  slow drift       /collections/sale decaying ~3%/30d for 120 days, never
                      enough week-over-week to trip an anomaly test
  E  alias fragments  facebook / fb / Facebook_Ads carrying the same traffic
  F  broken tag       {{site_source_name}} shipped as a literal utm_source
  G  incomplete day   last date's (not set) traffic spikes ~8x

Signals the engine MUST NOT alert on as a conversion problem:
  B  pure mix shift   a low-CVR source scales 6x, dragging blended CVR down while
                      every individual source converts exactly as before

Usage: python3 make_test_data.py --out sessions.parquet --days 120
"""
import argparse
import numpy as np
import pandas as pd

RNG = np.random.default_rng(20260827)

LANDING_PAGES = [
    ("/", 0.14, 1.00), ("/checkout-lp", 0.09, 1.65), ("/collections/sale", 0.08, 1.30),
    ("/collections/new", 0.07, 1.10), ("/products/hero-item", 0.07, 1.55),
    ("/collections/best-sellers", 0.06, 1.25), ("/products/bundle", 0.05, 1.45),
    ("/pages/quiz", 0.05, 0.75), ("/collections/gifts", 0.04, 1.15),
    ("/products/refill", 0.04, 1.70), ("/pages/lp-spring", 0.03, 0.95),
    ("/blogs/guide-to-routine", 0.05, 0.10), ("/blogs/ingredients", 0.04, 0.08),
    ("/blogs/how-to-choose", 0.03, 0.09), ("/pages/about", 0.02, 0.20),
]
LANDING_PAGES += [(f"/products/item-{i:02d}", 0.012, 0.9 + (i % 7) * 0.12)
                  for i in range(1, 15)]

SOURCES = [
    ("google", "cpc", 0.20, 1.05), ("google", "organic", 0.14, 1.35),
    ("(direct)", "(none)", 0.13, 1.50), ("klaviyo", "email", 0.08, 2.10),
    ("facebook", "paid", 0.07, 0.80), ("fb", "paid", 0.03, 0.78),
    ("Facebook_Ads", "paid", 0.02, 0.79), ("instagram", "paid", 0.05, 0.72),
    ("ig", "social", 0.02, 0.70), ("tiktok", "paid", 0.04, 0.55),
    ("bing", "cpc", 0.03, 1.00), ("klaviyo", "sms", 0.025, 1.95),
    ("display", "cheap", 0.02, 0.18), ("pinterest", "social", 0.015, 0.65),
    ("(not set)", "(not set)", 0.02, 0.60), ("reddit", "referral", 0.01, 0.75),
    ("{{site_source_name}}", "paid", 0.004, 0.70), ("chatgpt.com", "ai-assistant", 0.012, 1.25),
    ("partner-a", "affiliate", 0.01, 0.90), ("partner-b", "affiliate", 0.008, 0.85),
]

COUNTRIES = [("US", 0.70, 1.00), ("CA", 0.10, 0.92), ("GB", 0.08, 0.88),
             ("AU", 0.06, 0.85), ("DE", 0.06, 0.60)]
DEVICES = [("mobile", 0.66, 0.72), ("desktop", 0.29, 1.62), ("tablet", 0.05, 1.05)]

BASE_CVR = 0.022
P_VIEW, P_ATC, P_BC = 0.55, 0.22, 0.42     # view|session, atc|view, checkout|atc
AOV = 84.0


def build(days: int, daily_sessions: int) -> pd.DataFrame:
    lp_n, lp_w, lp_m = zip(*[(a, b, c) for a, b, c in LANDING_PAGES])
    src_s, src_m_, src_w, src_m = zip(*SOURCES)
    c_n, c_w, c_m = zip(*COUNTRIES)
    d_n, d_w, d_m = zip(*DEVICES)

    grid = np.array(np.meshgrid(np.arange(len(lp_n)), np.arange(len(src_s)),
                                np.arange(len(c_n)), np.arange(len(d_n)),
                                indexing="ij")).reshape(4, -1)
    li, si, ci, di = grid
    w = (np.array(lp_w)[li] * np.array(src_w)[si]
         * np.array(c_w)[ci] * np.array(d_w)[di])
    w = w / w.sum()
    mult = (np.array(lp_m)[li] * np.array(src_m)[si]
            * np.array(c_m)[ci] * np.array(d_m)[di])

    end = pd.Timestamp("2026-08-25")
    dates = pd.date_range(end - pd.Timedelta(days=days - 1), end)
    frames = []

    for t, day in enumerate(dates):
        dow = day.dayofweek
        seasonal = 1.0 + 0.16 * np.cos((dow - 1) * np.pi / 3.5)
        trend = 1.0 + 0.0016 * t
        total = daily_sessions * seasonal * trend * RNG.normal(1.0, 0.03)

        wd = w.copy()
        md = mult.copy()
        days_from_end = (end - day).days

        # --- B: pure mix shift. A cheap, low-converting source scales 6x in the
        #        final 7 days. Every source's own CVR is untouched.
        if days_from_end < 7:
            cheap = np.array(src_s) == "display"
            wd = np.where(cheap[si], wd * 6.0, wd)
            wd = wd / wd.sum()

        # --- A: real break, isolated to checkout->purchase on one LP x source.
        break_mask = ((np.array(lp_n)[li] == "/checkout-lp") &
                      (np.array(src_s)[si] == "google") &
                      (np.array(src_m_)[si] == "cpc"))
        purchase_penalty = np.ones_like(md)
        if days_from_end < 7:
            purchase_penalty = np.where(break_mask, 0.65, 1.0)

        # --- D: slow drift on one LP across the whole window.
        drift = np.where(np.array(lp_n)[li] == "/collections/sale",
                         1.0 - 0.0010 * (days - 1 - t) * -1, 1.0)
        drift = np.where(np.array(lp_n)[li] == "/collections/sale",
                         1.0 - 0.0011 * t, 1.0)
        md = md * drift

        # --- G: last day incomplete. (not set) balloons, everything else dips.
        if days_from_end == 0:
            ns = np.array(src_s)[si] == "(not set)"
            wd = np.where(ns, wd * 8.0, wd * 0.55)
            wd = wd / wd.sum()

        sessions = RNG.poisson(np.maximum(total * wd, 0))
        keep = sessions > 0
        if not keep.any():
            continue

        s = sessions[keep]
        m = md[keep]
        pp = purchase_penalty[keep]

        p_view = np.clip(P_VIEW * (0.7 + 0.3 * m), 0.02, 0.95)
        view = RNG.binomial(s, p_view)
        atc = RNG.binomial(view, np.clip(P_ATC * m, 0.005, 0.9))
        bc = RNG.binomial(atc, np.clip(P_BC * (0.85 + 0.15 * m), 0.005, 0.95))
        p_pur = np.clip(BASE_CVR / (P_VIEW * P_ATC * P_BC) * m * pp, 0.01, 0.98)
        pur = RNG.binomial(bc, p_pur)

        # --- C: tracking break. Purchases vanish, traffic and upper funnel intact.
        if days_from_end == 9:
            pur = RNG.binomial(pur, 0.04)

        rev = pur * AOV * RNG.normal(1.0, 0.05, size=len(pur))

        frames.append(pd.DataFrame({
            "date": day,
            "landing_page": pd.Categorical(np.array(lp_n)[li][keep]),
            "utm_source": pd.Categorical(np.array(src_s)[si][keep]),
            "utm_medium": pd.Categorical(np.array(src_m_)[si][keep]),
            "utm_campaign": pd.Categorical(
                np.where(np.isin(np.array(src_m_)[si][keep], ["cpc", "paid"]),
                         "always-on-prospecting", "(not set)")),
            "country": pd.Categorical(np.array(c_n)[ci][keep]),
            "device": pd.Categorical(np.array(d_n)[di][keep]),
            "sessions": s.astype("int64"),
            "step_view_item": view.astype("int64"),
            "step_add_to_cart": atc.astype("int64"),
            "step_begin_checkout": bc.astype("int64"),
            "step_purchase": pur.astype("int64"),
            "revenue": np.maximum(rev, 0.0),
        }))

    return pd.concat(frames, ignore_index=True)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="sessions.parquet")
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--daily-sessions", type=int, default=220_000)
    a = ap.parse_args()
    df = build(a.days, a.daily_sessions)
    df.to_parquet(a.out, index=False)
    print(f"rows={len(df):,}  sessions={df['sessions'].sum():,}  "
          f"purchases={df['step_purchase'].sum():,}  "
          f"cvr={df['step_purchase'].sum()/df['sessions'].sum():.3%}  -> {a.out}")

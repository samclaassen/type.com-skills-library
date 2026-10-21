#!/usr/bin/env python3
"""
End-to-end verification against planted signals.

Asserts both directions, which is the part that matters: the engine must CATCH the
real break and must STAY QUIET about the mix shift. A detector that fires on
everything passes a catch-only test suite and is useless in production.

    python3 tests/verify.py            # generates data, runs both modes, asserts
"""
import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

CONFIG = """
brand: "Verification Brand"
funnel:
  steps:
    - {name: view_item, event: view_item}
    - {name: add_to_cart, event: add_to_cart}
    - {name: begin_checkout, event: begin_checkout}
    - {name: purchase, event: purchase, is_conversion: true}
windows: {current_days: 7, baseline_days: 28, gap_days: 0, drift_days: 90}
floors: {min_sessions_current: 3000, min_sessions_baseline: 10000,
         min_conversions_baseline: 100, min_revenue_at_stake: 5000}
stats: {fdr_q: 0.10, min_relative_change: 0.10, min_absolute_change_pp: 0.15,
        mix_dominance_threshold: 0.60}
scan: {drill_revenue_share: 0.01, max_depth: 3, top_n_findings: 20}
landing_pages: {keep_params: [variant], known_tracking_params: [gclid, fbclid]}
source_groups: {}
acknowledged_sources: []
known_events: []
business:
  shippable_countries: [US, CA, GB, AU]
  non_converting_paths: ["/blogs/", "/pages/about"]
output: {view: both}
"""

PASS, FAIL = [], []


def check(name, ok, detail=""):
    (PASS if ok else FAIL).append(name)
    print(f"  {'PASS' if ok else 'FAIL'}  {name}{('  -- ' + detail) if detail else ''}")


def main() -> int:
    tmp = tempfile.mkdtemp(prefix="lpcvr-verify-")
    data = os.path.join(tmp, "sessions.parquet")
    cfg = os.path.join(tmp, "config.yaml")
    open(cfg, "w").write(CONFIG)
    meta = '{"complete_through":"2026-08-24","sampled":false,"other_row_sessions":0}'

    subprocess.run([sys.executable, os.path.join(HERE, "make_test_data.py"),
                    "--out", data, "--days", "120"], check=True,
                   stdout=subprocess.DEVNULL)

    def run(mode, out, state=None):
        cmd = [sys.executable, os.path.join(ROOT, "scripts", "analyze.py"),
               "--data", data, "--config", cfg, "--meta", meta,
               "--out", out, "--mode", mode]
        if state:
            cmd += ["--state", state]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return json.load(open(os.path.join(out, "findings.json")))

    b_out = os.path.join(tmp, "baseline")
    base = run("baseline", b_out)
    codes = {f["code"] for f in base["hygiene"]["findings"]}

    print("\nBASELINE")
    check("E  alias fragmentation reported",
          "source_alias_fragmentation" in codes)
    check("E  fb~facebook found behaviourally",
          any(a["short"] == "fb" and "facebook" in a["long"].lower()
              for a in base["hygiene"].get("abbreviation_candidates", [])))
    check("E  partner-a/partner-b NOT called aliases",
          not any("partner" in f["value"]
                  for c in base["hygiene"]["alias_clusters"] for f in c["fragments"]),
          "trailing-character variants must not cluster")
    check("F  unrendered template variable caught",
          "unrendered_template_variable" in codes)
    check("G  incomplete final day excluded",
          "incomplete_day" in codes)
    check("   sensitivity reported for every level",
          len(base["sensitivity"]["levels"]) >= 4)
    check("   baseline emits no alerts",
          base["alerts"] == [])

    state = os.path.join(tmp, "state.json")
    open(state, "w").write(open(os.path.join(b_out, "state.json")).read())
    scan = run("scan", os.path.join(tmp, "scan"), state)

    alerts, mixes = scan["alerts"], scan["mix_shifts"]
    top = alerts[0] if alerts else {}
    dims = " / ".join(top.get("dims", {}).values())

    print("\nSCAN")
    check("A  real break is the TOP conversion finding",
          "/checkout-lp" in dims and "google" in dims, dims or "no alerts")
    check("A  break localised to checkout -> purchase",
          top.get("funnel", {}).get("worst_transition") == "step_begin_checkout -> step_purchase",
          str(top.get("funnel", {}).get("worst_transition")))
    check("A  real break not mislabelled as mix",
          (top.get("mix") or {}).get("mix_share", 1.0) < 0.60,
          f"mix_share={(top.get('mix') or {}).get('mix_share')}")
    check("B  site move identified as mix, not conversion",
          (scan["site"]["mix"]["mix_share"] >= 0.60
           and "MIX" in " ".join(scan["site"]["notes"])),
          f"mix_share={scan['site']['mix']['mix_share']}")
    check("B  mix artifacts kept OUT of the conversion list",
          all((a.get("mix") or {}).get("mix_share", 0) < 0.60 for a in alerts))
    check("B  mix artifacts still reported separately",
          len(mixes) > 0, f"{len(mixes)} mix findings")
    check("C  tracking break detected in the BASELINE window",
          any(t["code"] == "possible_tracking_break" for t in scan["tracking_breaks"]))
    check("C  broken day excluded from the windows",
          len(scan["windows"]["excluded_dates"]) >= 2,
          str(scan["windows"]["excluded_dates"]))
    check("D  slow drift surfaced",
          any("/collections/sale" in d["landing_page"] for d in scan["drift"]))
    check("D  non-converting blog pages excluded from drift",
          not any("/blogs/" in d["landing_page"] for d in scan["drift"]))
    check("   one finding per cell, not one per funnel step",
          len({(a["level"], tuple(sorted(a["dims"].items()))) for a in alerts}) == len(alerts))
    check("   untested-but-material surfaced",
          len(scan["untested_but_material"]) > 0)
    check("   payload stays small regardless of cardinality",
          os.path.getsize(os.path.join(tmp, "scan", "findings.json")) < 120_000,
          f"{os.path.getsize(os.path.join(tmp,'scan','findings.json')):,} bytes")

    # ---- restatement: mutate history the way a real backfill does, then re-run ----
    import pandas as pd, numpy as np
    st2 = os.path.join(tmp, "state2.json")
    open(st2, "w").write(open(os.path.join(tmp, "scan", "state.json")).read())
    df = pd.read_parquet(data)
    for day, bump in [("2026-08-23", 1.09), ("2026-08-24", 1.14)]:
        m = df["date"] == pd.Timestamp(day)
        for c in ["step_view_item", "step_add_to_cart", "step_begin_checkout", "step_purchase"]:
            df.loc[m, c] = np.minimum((df.loc[m, c] * bump).round().astype("int64"),
                                      df.loc[m, "sessions"])
        df.loc[m, "revenue"] = df.loc[m, "revenue"] * bump
    restated = os.path.join(tmp, "restated.parquet")
    df.to_parquet(restated, index=False)

    cmd = [sys.executable, os.path.join(ROOT, "scripts", "analyze.py"),
           "--data", restated, "--config", cfg, "--meta", meta, "--state", st2,
           "--out", os.path.join(tmp, "scan2"), "--mode", "scan"]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    r2 = json.load(open(os.path.join(tmp, "scan2", "findings.json")))
    rcodes = {f["code"] for f in r2["data_findings"]}

    print("\nRESTATEMENT + TIERS")
    check("H  restated days detected on re-read",
          r2["restatement"]["days_moved"] == 2,
          f"days_moved={r2['restatement']['days_moved']}")
    check("H  data_restated raised",
          "data_restated" in rcodes)
    check("H  changed days are named, with the size of the change",
          all(k in r2["restatement"]["moved"][0]
              for k in ("date", "conversions_change", "was", "now")))
    check("H  affected findings marked provisional",
          any("provisional" in n for a in r2["alerts"] + r2["mix_shifts"]
              for n in a["notes"]))
    check("H  restatement forces a notification",
          "data_restated" in r2["notification"]["always_notify_triggered"])
    check("I  every finding carries a tier",
          all(a.get("tier") in ("notify", "flag", "context")
              for a in scan["alerts"] + scan["mix_shifts"]))
    check("I  notify is a strict subset, nothing suppressed",
          len([a for a in scan["alerts"] + scan["mix_shifts"] if a["tier"] == "notify"])
          < len(scan["alerts"] + scan["mix_shifts"]),
          f"{scan['notification']['notify_count']} notify of "
          f"{len(scan['alerts'] + scan['mix_shifts'])} findings")
    check("I  small findings flagged, not dropped",
          any(a["tier"] == "flag" for a in scan["alerts"] + scan["mix_shifts"]))

    print(f"\n{len(PASS)} passed, {len(FAIL)} failed")
    for f in FAIL:
        print("  FAILED:", f)
    return 1 if FAIL else 0


if __name__ == "__main__":
    raise SystemExit(main())

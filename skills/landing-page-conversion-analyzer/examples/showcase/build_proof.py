#!/usr/bin/env python3
"""Format recorded engine findings. Never recomputes rates from session data."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
scan = json.loads((ROOT/"scan/findings.json").read_text())
root = next(a for a in scan["alerts"] if a["is_root"])
fmt_rate = lambda n: f"{n*100:.2f}%"
fmt_int = lambda n: f"{n:,.0f}"
fmt_money = lambda n: f"${n:,.0f}"
proof = {
    "title":"A conversion drop, traced to one checkout segment",
    "summary":"Investigate checkout completion for Google traffic landing on /starter-kit. The analyzer identifies one affected page/source segment and separates its wider symptoms.",
    "datasetLabel":"Synthetic demo · Generated data, real engine output",
    "baselineWindow":"Aug 1–28, 2026 · 28-day baseline",
    "currentWindow":"Aug 29–Sep 4, 2026 · 7-day comparison",
    "metrics":[
        {"label":"Revenue at stake","value":fmt_money(root["revenue_at_stake"]),"note":"Modeled against the segment's baseline; not recovered revenue."},
        {"label":"Segment conversion","value":fmt_rate(root["base_rate_adj"])+" → "+fmt_rate(root["cur_rate"]),"note":"Purchase sessions ÷ sessions for /starter-kit × google."},
        {"label":"Checkout completion","value":fmt_rate(root["funnel"]["transitions"][-1]["base_rate"])+" → "+fmt_rate(root["funnel"]["transitions"][-1]["cur_rate"]),"note":"Purchase sessions ÷ checkout sessions in the affected segment."},
        {"label":"Sessions in affected segment","value":fmt_int(root["cur_sessions"]),"note":"Current comparison window."}
    ],
    "findings":[
        {"title":"Start with checkout, owned by engineering",
         "detail":"The weakest transition is begin_checkout → purchase. Earlier funnel transition rates are unchanged in the generated data.",
         "nextStep":"Verify purchase tracking, then reproduce checkout from /starter-kit with the Google campaign parameters; inspect payment errors and recent checkout changes.",
         "owner":"Engineering + analytics"},
        {"title":"One affected segment explains the wider alerts",
         "detail":f'{scan["scan_stats"]["roots"]} root finding explains {scan["scan_stats"]["explained_by_a_root"]} overlapping findings for the page, source, medium, and campaign.',
         "nextStep":"Investigate the shared segment before opening separate incidents for each aggregate.",
         "owner":"Analytics"},
        {"title":"Traffic composition does not explain this demo",
         "detail":"The engine reports zero mix contribution in the tested dimensions. The generated source, device, and visitor mix is held constant.",
         "nextStep":"On real data, verify export completeness and include device and visitor dimensions when available. Treat a checkout defect as a hypothesis until independently confirmed.",
         "owner":"Analytics + engineering"}
    ],
    "rows":[{"page":root["dims"]["landing_page"],"source":root["dims"]["utm_source"],"baselineCvr":fmt_rate(root["base_rate_adj"]),"currentCvr":fmt_rate(root["cur_rate"]),"sessions":fmt_int(root["cur_sessions"]),"impact":fmt_money(root["revenue_at_stake"])}],
    "checks":[
        "28 bundled synthetic checks passed, including mix shifts, tracking breaks, freshness, drift, and restatements.",
        "4 showcase regression checks passed: correct conversion headline, expected root segment, checkout localization, and aggregate attribution.",
        "CSV input, config, baseline state, raw JSON output, and exact reproduction commands are included.",
        "Revenue and conversion figures above are formatted from the recorded engine output."
    ],
    "limitations":[
        "Synthetic data demonstrates behavior; it is not customer performance, a testimonial, or a recovered-revenue claim.",
        "Revenue at stake is a baseline-based model, not an observed loss or guaranteed recovery.",
        "The finding localizes a change, not its cause. Tracking, payment, and checkout explanations need independent verification.",
        "This example holds source, device, and visitor mix constant. It does not test all real-world confounders.",
        "The example uses session attribution and contains too little history to demonstrate slow multi-month drift. Separate bundled tests exercise drift.",
        "Runs locally on a mapped CSV or Parquet export. Live account connections and your export mapping are not tested."
    ],
    "testCount":32,
    "evidence":{
        "rawOutput":"scan/findings.json",
        "rootFingerprint":root["fingerprint"],
        "cellsTested":scan["scan_stats"]["cells_tested"],
        "baselineSessions":root["base_sessions"],
        "currentPurchaseSessions":root["cur_count"],
        "relativeChange":root["rel_change"],
        "minimumDetectableRelativeChangePercent":root["mde_rel"],
        "sourcePaths":{
            "revenueAtStake":"alerts[is_root=true].revenue_at_stake",
            "segmentCvr":"alerts[is_root=true].base_rate_adj / cur_rate",
            "checkoutCompletion":"alerts[is_root=true].funnel.transitions[3].base_rate / cur_rate",
            "segmentSessions":"alerts[is_root=true].cur_sessions",
            "rootAndOverlapCounts":"scan_stats.roots / explained_by_a_root"
        }
    }
}
(ROOT/"proof.json").write_text(json.dumps(proof,indent=2,ensure_ascii=False)+"\n")

#!/usr/bin/env python3
"""I13-I20 known-regression corpus. Tests actual rendered destinations, not string absence alone."""
import copy
import html
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

sys.dont_write_bytecode = True
import build_scorecard as bs

ROOT = Path(__file__).resolve().parent
config = bs.load_config(str(ROOT / "scorecard.json"))
rubric = bs.load_rubric(str(ROOT / "sample_rubric_finance_ops_analyst.json"), config)
base = json.loads((ROOT / "sample_bundle_single.json").read_text())
rows = json.loads(Path(sys.argv[1]).read_text()) if len(sys.argv) > 1 else json.loads((ROOT / "repair_cases.json").read_text())
# Remove unrelated sample evidence/scores: every observed redaction belongs to this input.
base["interviews"][0]["evidence"] = []
base["interviews"][0]["scores"] = []
base["interviews"][0]["gaps"] = []
base["interviews"][0]["concerns"] = []
SAFE = "Rebuilt the reconciliation checklist and checked every variance."
checks = 0
failures = []
def require(ok, label):
    global checks
    checks += 1
    if not ok:
        failures.append(label)

def normalized(s):
    return html.unescape(s.replace("\\", ""))

with tempfile.TemporaryDirectory(prefix="interview-repair-") as tmp:
    path = Path(tmp) / "bundle.json"
    def outputs(text, kind):
        bundle = copy.deepcopy(base)
        item = {"competency": "reconciliation", "kind": kind,
                "evidence_kind": "behaviour_example", "text": text}
        if kind == "quote":
            item["speaker"] = "candidate"
        bundle["interviews"][0]["evidence"] = [item]
        path.write_text(json.dumps(bundle))
        args = SimpleNamespace(bundle=str(path), as_of="2026-09-29", scheduled=False,
            log=None, resume_pending=False, hiring_manager_preview=False, dry_run=True,
            ats_draft="ashby", json=True)
        # The production build path validates fixtures, classifies, and emits JSON + actual ATS markdown.
        result = json.loads(bs.build(args, config, rubric))
        b = bs.load_bundle(str(path), config, rubric)
        a = bs.analyse_interview(b["interviews"][0], rubric, config, b["scanner"])
        markdown = {provider: bs.render_single(b, a, rubric, config, bs.parse_as_of(args.as_of), provider)
                    for provider in ("ashby", "greenhouse", "lever")}
        return result, markdown

    # Eligible safe twins prove the exact fixture for each kind renders in all destinations.
    twins = {}
    for kind in ("note", "quote"):
        result, rendered = outputs(SAFE, kind)
        twins[kind] = result
        require(result["interviews"][0]["evidence_counts"]["reconciliation"] == 1,
                kind + ": safe twin evidence count")
        require(not result["interviews"][0]["fairness_flags"], kind + ": safe twin no flag")
        require(SAFE in normalized(result["markdown"]), kind + ": safe twin JSON")
        for provider, text in rendered.items():
            before, after = text.split("### ATS draft", 1)
            require(SAFE in normalized(before), kind + ": safe twin scorecard " + provider)
            require(SAFE in normalized(after), kind + ": safe twin ATS " + provider)

    for index, (category, text, want, reason) in enumerate(rows):
        for kind in ("note", "quote"):
            label = "{} {} {} {!r}".format(index, category, kind, text)
            try:
                result, rendered = outputs(text, kind)
            except Exception as error:
                failures.append(label + ": pipeline failed " + repr(error))
                continue
            iv = result["interviews"][0]
            flags = iv["fairness_flags"]
            if want == "w":
                require(iv["evidence_counts"]["reconciliation"] == 0, label + ": zero evidence")
                require(len(flags) == 1 and flags[0]["type"] == "redacted_evidence",
                        label + ": intended redaction not ineligible fixture")
                if reason:
                    allowed = {reason}
                    if reason == "compensation":
                        allowed.add("salary_history")
                    require(bool(flags) and bool(allowed.intersection(flags[0].get("categories", []))),
                            label + ": exact reason " + str(reason))
                require(text not in normalized(result["markdown"]), label + ": JSON no leaked text")
                for provider, output in rendered.items():
                    before, after = output.split("### ATS draft", 1)
                    require(text not in normalized(before), label + ": scorecard " + provider)
                    require(text not in normalized(after), label + ": ATS " + provider)
                    require("was withdrawn" in before, label + ": displayed reason " + provider)
            else:
                require(iv["evidence_counts"]["reconciliation"] == 1, label + ": retained evidence")
                require(not flags, label + ": no false positive")
                require(text in normalized(result["markdown"]), label + ": JSON retained")
                for provider, output in rendered.items():
                    before, after = output.split("### ATS draft", 1)
                    require(text in normalized(before), label + ": scorecard retained " + provider)
                    require(text in normalized(after), label + ": ATS retained " + provider)
# Oracle negative control: the same safe text without evidence_kind is explicitly
# ineligible, so it must NEVER count as a privacy/decision filtering success.
with tempfile.TemporaryDirectory(prefix="interview-oracle-") as tmp:
    bundle = copy.deepcopy(base)
    bundle["interviews"][0]["evidence"] = [{"competency": "reconciliation", "kind": "note", "text": SAFE}]
    path = Path(tmp) / "bundle.json"
    path.write_text(json.dumps(bundle))
    b = bs.load_bundle(str(path), config, rubric)
    a = bs.analyse_interview(b["interviews"][0], rubric, config, b["scanner"])
    require(a["fairness"][0]["type"] == "untagged_evidence", "oracle distinguishes ineligible safe fixture")
if failures:
    print("\n".join(failures))
    print("FAIL: {} failures in {} checks".format(len(failures), checks))
    sys.exit(1)
print("PASS: {} repair checks; {} known cases; safe twins in all ATS outputs and JSON".format(checks, len(rows)))

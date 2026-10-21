#!/usr/bin/env python3
"""OO10 repair regressions through the real CLI entrypoint and rendered Markdown.

Every sensitive case has an eligible work twin. All work topics and all three
content paths run with a useful sibling commitment, found quotes, a personal
withhold reason, and no sensitive text in Markdown, agenda, or structured output.
Only immutable configuration loading is reused between calls.
"""
import contextlib
import copy
import hashlib
import io
import json
import re
import sys
import tempfile
from pathlib import Path

import build_prep as bp

HERE = Path(__file__).resolve().parent
ASOF = "2026-10-13T09:30:00-07:00"
TOPICS = ("team-logistics", "project", "process", "customer", "goal",
          "work-feedback", "career-growth", "work-question")
PATHS = ("quote", "summary", "ai_summary")
FILLER = "We talked about this one in detail."
SIBLING = "Send the Q4 roadmap draft to the team."
CASE_ID = "garden-leave-for-mateo"
CASE_HASH = hashlib.sha256(CASE_ID.encode("utf-8")).hexdigest()
BASE = json.loads((HERE / "sample_bundle_manager.json").read_text(encoding="utf-8"))
CASES = json.loads((HERE / "regression_cases.json").read_text(encoding="utf-8"))


def make_bundle(text, topic, path):
    bundle = copy.deepcopy(BASE)
    bundle["work_items"] = []
    for meeting in bundle["meetings"]:
        meeting.pop("text", None)
        if meeting["id"] == "sample-1on1-0929":
            # Sensitive text comes last, preserving quote eligibility for the twin
            # and sibling even when it contains negation or an unfinished clause.
            meeting["text"] = "Dana: " + FILLER + " Dana: " + SIBLING + " Dana: " + text
    bundle["items"] = [
        dict(id=CASE_ID, kind="commitment", owner="user", key="qacase", topic=topic,
             meeting_id="sample-1on1-0929", due_phrase="by Friday",
             quote_from="summary" if path == "ai_summary" else "transcript",
             summary=text if path == "summary" else "Follow up on this item",
             quote=FILLER if path == "summary" else text),
        dict(id="qasibling", kind="commitment", owner="user", key="qasibling", topic="project",
             meeting_id="sample-1on1-0929", due_phrase="by Friday", quote_from="transcript",
             summary="Q4 roadmap draft to the team", quote=SIBLING),
    ]
    return bundle


def run_case(case, topic, path, file):
    file.write_text(json.dumps(make_bundle(case["text"], topic, path)), encoding="utf-8")
    stdout, stderr = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
        rc = bp.main(["--bundle", str(file), "--as-of", ASOF, "--json"])
    context = (case["text"], topic, path)
    assert rc == 0 and not stderr.getvalue(), (context, rc, stderr.getvalue())
    raw = stdout.getvalue()
    data = json.loads(raw)
    assert not any(data["dropped_items"].values()), (context, "ineligible", data["dropped_items"])
    ids = [value for item in data["items"] for value in item["item_ids"]]
    assert "qasibling" in ids and bp.md(SIBLING) in data["markdown"], (context, "lost useful sibling")
    if case["kind"] == "attack":
        assert CASE_ID not in ids, (context, "sensitive item shown")
        assert data["withheld_personal"]["by_pattern"] == 1, (context, "wrong reason")
        assert data["withheld_personal"]["item_ids"] == [[CASE_HASH]], (context, "unsafe withheld ID")
        assert not data["left_out_judgement"]["count"], (context, "wrong judgement reason")
        assert CASE_ID not in raw and case["text"] not in raw, (context, "sensitive structured text")
        assert bp.md(case["text"]) not in data["markdown"], (context, "sensitive Markdown")
        assert not any("Follow up on this item" in line for line in data["agenda"]), (context, "agenda leak")
    else:
        assert CASE_ID in ids, (context, "safe work withheld", data["withheld_personal"],
                                data["left_out_judgement"])
        assert not data["withheld_personal"]["count"], (context, "safe work counted as personal")
        item = next(item for item in data["items"] if CASE_ID in item["item_ids"])
        assert item["overdue_days"] == 11, (context, "missing useful overdue status")
        assert bp.md(case["text"]) in data["markdown"], (context, "work text not rendered")
        label = case["text"] if path == "summary" else "Follow up on this item"
        assert any("Overdue (yours, 11 days): " + bp.md(label)[:40] in line
                   for line in data["agenda"]), (context, "work absent from agenda")


def main():
    config = bp.load_config(bp.DEFAULT_CONFIG)
    bp.load_config = lambda path: config
    # A fast diagnostic is useful when extending patterns, but never substitutes
    # for the rendered runs below.
    if "--scan" in sys.argv:
        scanner = config["_scanner"].with_names(["Dana Ortiz", "Riley Chen"])
        failures = []
        for i, case in enumerate(CASES):
            reason = scanner.check(case["text"], case["kind"] != "attack")
            if (reason == "personal") != (case["kind"] == "attack") or (
                    case["kind"] != "attack" and reason is not None):
                failures.append(dict(index=i, reason=reason, **case))
        print(json.dumps(failures, indent=2, ensure_ascii=False))
        return bool(failures)
    errors = []
    count = 0
    with tempfile.TemporaryDirectory() as directory:
        file = Path(directory) / "bundle.json"
        for index, case in enumerate(CASES):
            for topic in TOPICS:
                for path in PATHS:
                    try:
                        run_case(case, topic, path, file)
                    except AssertionError as exc:
                        errors.append(str(exc))
                    count += 1
            if (index + 1) % 50 == 0:
                print("OO10 regression progress: {} cases, {} failures".format(index + 1, len(errors)), flush=True)
    if errors:
        print("\n".join(errors[:30]))
        print("FAIL: {} errors in {} rendered runs".format(len(errors), count))
        return 1
    print("PASS: {} rendered runs; {} cases across {} topics and {} content paths".format(
        count, len(CASES), len(TOPICS), len(PATHS)))
    return 0


if __name__ == "__main__":
    sys.exit(main())

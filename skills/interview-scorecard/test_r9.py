#!/usr/bin/env python3
"""Round-9 regression corpus (r9_cases.json) for the job-relatedness gate and the politics, pay and ranking checks.

Fixture: the shipped single-interview sample bundle with one more reconciliation item appended (so the item is
eligible: tagged behaviour_example, within max_evidence_single). Each row runs as a note and as a candidate quote
through the real build (JSON) and the three ATS renderers. Rows:
  w  withdrawn by a term list: the expected category is flagged, the text is in no output, the reason is shown,
     and the reconciliation suggested score is withdrawn;
  h  held by the job-relatedness gate: not_job_related flag, text in no output, the hold is explained, score
     withdrawn;
  k  control / safe twin: renders in the scorecard body, every ATS draft and the JSON markdown, no flag, score kept;
  o  known over-withhold (documented): must not render (fails safe).
Every w/h row of the politics, pay and ranking families is also run embedded after a work clause
("Rebuilt the AP reconciliation checklist, and <text>"), where the gate passes the first clause: it must still not
render. usage: test_r9.py [cases.json]"""
import copy
import html
import json
import os
import sys
import tempfile
from types import SimpleNamespace
from concurrent.futures import ProcessPoolExecutor

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import build_scorecard as bs  # noqa: E402

PREFIX = "Rebuilt the AP reconciliation checklist, and "
EMBED_FAMILIES = ("politics", "pay", "personal_rate", "ranking")
STATE = {}


def setup():
    if not STATE:
        STATE["config"] = bs.load_config(os.path.join(ROOT, "scorecard.json"))
        STATE["rubric"] = bs.load_rubric(os.path.join(ROOT, "sample_rubric_finance_ops_analyst.json"), STATE["config"])
        with open(os.path.join(ROOT, "sample_bundle_single.json"), encoding="utf-8") as f:
            STATE["base"] = json.load(f)
        STATE["tmp"] = tempfile.mkdtemp(prefix="interview-r9-")
    return STATE


def normalized(s):
    return " ".join(html.unescape(s.replace("\\", "")).replace("’", "'").split())


def strings(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for k, v in o.items():
            yield k
            for x in strings(v):
                yield x
    elif isinstance(o, list):
        for v in o:
            for x in strings(v):
                yield x


def outputs(text, kind):
    st = setup()
    bundle = copy.deepcopy(st["base"])
    item = {"competency": "reconciliation", "kind": kind, "evidence_kind": "behaviour_example", "text": text,
            "timestamp": None, "url": "https://notes.granola.ai/d/sample-hm-0142"}
    if kind == "quote":
        item["speaker"] = "candidate"
    bundle["interviews"][0]["evidence"].append(item)
    path = os.path.join(st["tmp"], "b{}.json".format(os.getpid()))
    with open(path, "w", encoding="utf-8") as f:
        json.dump(bundle, f)
    args = SimpleNamespace(bundle=path, as_of="2026-09-29", scheduled=False, log=None, resume_pending=False,
                           hiring_manager_preview=False, dry_run=True, ats_draft="lever", json=True)
    result = json.loads(bs.build(args, st["config"], st["rubric"]))
    b = bs.load_bundle(path, st["config"], st["rubric"])
    a = bs.analyse_interview(b["interviews"][0], st["rubric"], st["config"], b["scanner"])
    markdown = {p: bs.render_single(b, a, st["rubric"], st["config"], bs.parse_as_of(args.as_of), p)
                for p in ("ashby", "greenhouse", "lever")}
    return result, markdown


def check(row):
    origin, family, text, want, reason = row
    fails = []
    n = 0
    forms = [("note", text), ("quote", text)]
    if want in ("w", "h") and family.split("_", 1)[-1] in EMBED_FAMILIES:
        forms.append(("embedded-note", PREFIX + text[0].lower() + text[1:]))
    for kind, t in forms:
        label = "{} {} {} {!r}".format(origin, family, kind, t)
        try:
            result, rendered = outputs(t, kind.replace("embedded-", ""))
        except Exception as error:  # a crash is a failure of this row, reported with the others
            return 1, [label + ": pipeline failed " + repr(error)]
        iv = result["interviews"][0]
        flags = [f for f in iv["fairness_flags"] if f["competency"] == "reconciliation"]
        score = iv["scores"].get("reconciliation", {}).get("status")
        hay = normalized(" || ".join(strings(result)))
        nt = normalized(t)

        def req(ok, what):
            fails.append(label + ": " + what) if not ok else None
        n += 1
        if want == "k":
            req(not flags, "no flag (got {})".format(flags))
            req(score == "suggested", "score kept (got {})".format(score))
            req(nt in hay, "JSON retained")
            for p, out in rendered.items():
                before, after = out.split("### ATS draft", 1)
                req(nt in normalized(before), "scorecard retained " + p)
                req(nt in normalized(after), "ATS retained " + p)
            continue
        req(nt not in hay, "JSON no leaked text")
        req(score != "suggested", "score withdrawn (got {})".format(score))
        for p, out in rendered.items():
            before, after = out.split("### ATS draft", 1)
            req(nt not in normalized(before), "scorecard " + p)
            req(nt not in normalized(after), "ATS " + p)
        if kind.startswith("embedded"):
            req(bool(flags), "a flag explains the withheld item")
            continue
        if want == "w":
            allowed = {reason, "salary_history"} if reason == "compensation" else {reason}
            req(any(f["type"] == "redacted_evidence" and allowed & set(f["categories"]) for f in flags),
                "reason {} (got {})".format(reason, flags))
            for p, out in rendered.items():
                req("was withdrawn" in out.split("### ATS draft", 1)[0], "displayed reason " + p)
        elif want == "h":
            req([f["type"] for f in flags] == [bs.NOT_JOB_RELATED], "held as not job-related (got {})".format(flags))
            for p, out in rendered.items():
                req("not shown to be job-related" in out.split("### ATS draft", 1)[0], "displayed hold " + p)
        else:  # o
            req(bool(flags), "flagged")
    return n, fails


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "r9_cases.json")
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    workers = max(1, min(8, (os.cpu_count() or 2) - 1))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(check, rows, chunksize=4))
    runs = sum(r[0] for r in results)
    fails = [x for r in results for x in r[1]]
    by = {}
    for origin, family, text, want, reason in rows:
        by.setdefault((origin, want), 0)
        by[(origin, want)] += 1
    summary = ", ".join("{} {}={}".format(o, w, c) for (o, w), c in sorted(by.items()))
    if fails:
        print("\n".join(fails[:200]))
        print("FAIL: {} failures in {} runs over {} rows ({})".format(len(fails), runs, len(rows), summary))
        return 1
    print("PASS: {} runs over {} rows ({}); note+quote in ashby/greenhouse/lever + JSON".format(runs, len(rows),
                                                                                              summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())

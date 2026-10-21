#!/usr/bin/env python3
"""Round-10 regression corpus (r10_cases.json): QA round-9 IN repro strings (IN9-1..IN9-7) in every channel.

Fixture: the shipped single-interview sample bundle. An evidence row is appended as a 4th reconciliation item
(tagged behaviour_example, so it is eligible); a rationale row replaces the reconciliation rationale with
"Matches anchor 4. <text>"; a gap row adds a reconciliation gap note. Each row runs through the real build (JSON with
the lever ATS draft) and the three ATS renderers (ashby, greenhouse, lever markdown). For every attack row:
  - the attack text is in no output: not in the scorecard body, not in any ATS draft, not in any JSON string;
  - the stated reason is flagged: the expected category (term lists or $.personal_context) or, for "gate" rows, the
    job-relatedness gate's own flag; the scorecard explains it;
  - evidence and rationale rows withdraw the reconciliation score; a gap row drops only the note (score kept);
  - evidence rows are also run as a candidate quote and behind a work clause ("Rebuilt the AP reconciliation
    checklist; <text>"), and must still be withheld;
  - the twin (the same sentence with the sensitive phrase replaced by a neutral work phrase) renders in the scorecard
    body of every renderer, in every ATS draft (evidence) and in the JSON, with no flag and the score kept, which
    proves the attack is withheld for the stated reason and not because the sentence is unusable.
"keep" rows (IN9-7 controls) must render like a twin. usage: test_r10.py [cases.json]"""
import copy
import html
import json
import os
import re
import sys
import tempfile
from types import SimpleNamespace
from concurrent.futures import ProcessPoolExecutor

sys.dont_write_bytecode = True
ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)
import build_scorecard as bs  # noqa: E402

PREFIX = "Rebuilt the AP reconciliation checklist; "
STATE = {}
FLAG_TYPES = {"evidence": ("redacted_evidence", bs.NOT_JOB_RELATED),
              "rationale": ("withdrawn_score", bs.RATIONALE_NOT_JOB_RELATED),
              "gap": ("dropped_gap", bs.GAP_NOT_JOB_RELATED)}
GATE_SHOWN = {"evidence": "was held back: it was not shown to be job-related",
              "rationale": "its rationale was not shown to be job-related",
              "gap": "was dropped: it was not shown to be job-related"}


def setup():
    if not STATE:
        STATE["config"] = bs.load_config(os.path.join(ROOT, "scorecard.json"))
        STATE["rubric"] = bs.load_rubric(os.path.join(ROOT, "sample_rubric_finance_ops_analyst.json"), STATE["config"])
        with open(os.path.join(ROOT, "sample_bundle_single.json"), encoding="utf-8") as f:
            STATE["base"] = json.load(f)
        STATE["tmp"] = tempfile.mkdtemp(prefix="interview-r10-")
    return STATE


def normalized(s):
    return " ".join(html.unescape(s.replace("\\", "")).replace("’", "'").split()).lower()


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


def outputs(channel, text, kind):
    st = setup()
    bundle = copy.deepcopy(st["base"])
    iv = bundle["interviews"][0]
    if channel == "evidence":
        item = {"competency": "reconciliation", "kind": kind, "evidence_kind": "behaviour_example", "text": text,
                "timestamp": None, "url": "https://notes.granola.ai/d/sample-hm-0142"}
        if kind == "quote":
            item["speaker"] = "candidate"
        iv["evidence"].append(item)
    elif channel == "rationale":
        assert iv["scores"][0]["competency"] == "reconciliation"
        iv["scores"][0]["rationale"] = "Matches anchor 4. " + text
    else:
        iv["gaps"].append({"competency": "reconciliation", "note": text})
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
    channel, text, twin, reason = row["channel"], row["text"], row["twin"], row["reason"]
    fails = []
    n = 0
    labels = " ".join(normalized(v) for v in setup()["config"]["category_labels"].values())

    def run(kind, t, label):
        try:
            return outputs(channel, t, kind)
        except Exception as error:  # a crash is a failure of this row
            fails.append(label + ": pipeline failed " + repr(error))
            return None

    def req(ok, label, what):
        if not ok:
            fails.append(label + ": " + what)

    def rendered(t, kind, label):
        got = run(kind, t, label)
        if got is None:
            return
        result, md = got
        iv = result["interviews"][0]
        flags = [f for f in iv["fairness_flags"] if f["competency"] == "reconciliation"]
        req(not flags, label, "no flag (got {})".format(flags))
        req(iv["scores"].get("reconciliation", {}).get("status") == "suggested", label, "score kept")
        nt = normalized(t)
        req(nt in normalized(" || ".join(strings(result))), label, "JSON retained")
        for p, out in md.items():
            body, ats = out.split("### ATS draft", 1)
            req(nt in normalized(body), label, "scorecard retained " + p)
            if channel == "evidence":
                req(nt in normalized(ats), label, "ATS draft retained " + p)

    def withheld(t, kind, label, embedded=False):
        got = run(kind, t, label)
        if got is None:
            return
        result, md = got
        iv = result["interviews"][0]
        flags = [f for f in iv["fairness_flags"] if f["competency"] == "reconciliation"]
        nt = normalized(t)
        marker = normalized(row["marker"]) if row.get("marker") else None
        needles = [re.compile(re.escape(nt))]
        if marker and not re.search(r"(?<!\w)" + re.escape(marker) + r"(?!\w)", labels):
            needles.append(re.compile(r"(?<!\w)" + re.escape(marker) + r"(?!\w)"))  # the sensitive words alone
        hay = normalized(" || ".join(strings(result)))
        for needle in needles:
            req(not needle.search(hay), label, "JSON leaks {!r}".format(needle.pattern))
            for p, out in md.items():
                body, ats = out.split("### ATS draft", 1)
                req(not needle.search(normalized(body)), label, "scorecard leaks {!r} ({})".format(needle.pattern, p))
                req(not needle.search(normalized(ats)), label, "ATS draft leaks {!r} ({})".format(needle.pattern, p))
        score = iv["scores"].get("reconciliation", {}).get("status")
        if channel == "gap":
            req(score == "suggested", label, "a dropped gap note leaves the score (got {})".format(score))
        else:
            req(score != "suggested" and "reconciliation" in iv["withdrawn_scores"], label,
                "score withdrawn (got {})".format(score))
        if embedded:
            req(bool(flags), label, "a flag explains the withheld item")
            return
        cat_type, gate_type = FLAG_TYPES[channel]
        if reason == "gate":
            req([f["type"] for f in flags] == [gate_type], label, "held by the gate (got {})".format(flags))
            shown = GATE_SHOWN[channel]
        else:
            req(any(f["type"] == cat_type and reason in f["categories"] for f in flags), label,
                "reason {} (got {})".format(reason, flags))
            # The fairness line names the category (or says hiring-decision language), never the withheld words.
            shown = "hiring-decision language" if reason == bs.DECISION else \
                setup()["config"]["category_labels"][reason]
        for p, out in md.items():
            fair = out.split("### Fairness & compliance", 1)[-1].split("### ATS draft", 1)[0]
            req(shown in fair.replace("&amp;", "&"), label, "displayed reason {!r} ({})".format(shown, p))

    origin = "{} {} {}".format(row["origin"], channel, row["family"])
    if reason == "keep":
        n += 1
        rendered(text, row["kind"], "{} keep {!r}".format(origin, text))
        return n, fails
    kinds = [row["kind"]] + (["quote" if row["kind"] == "note" else "note"] if channel == "evidence" else [])
    for kind in kinds:
        n += 1
        withheld(text, kind, "{} {} {!r}".format(origin, kind, text))
    if channel == "evidence":
        n += 1
        t = PREFIX + text[0].lower() + text[1:]
        withheld(t, "note", "{} embedded {!r}".format(origin, t), embedded=True)
    if twin:
        n += 1
        rendered(twin, row["kind"], "{} twin {!r}".format(origin, twin))
    return n, fails


def main():
    path = sys.argv[1] if len(sys.argv) > 1 else os.path.join(ROOT, "r10_cases.json")
    with open(path, encoding="utf-8") as f:
        rows = json.load(f)
    workers = max(1, min(8, (os.cpu_count() or 2) - 1))
    with ProcessPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(check, rows, chunksize=2))
    runs = sum(r[0] for r in results)
    fails = [x for r in results for x in r[1]]
    by = {}
    for row in rows:
        key = (row["channel"], "keep" if row["reason"] == "keep" else "withhold")
        by[key] = by.get(key, 0) + 1
    summary = ", ".join("{} {}={}".format(c, w, k) for (c, w), k in sorted(by.items()))
    if fails:
        print("\n".join(fails[:200]))
        print("FAIL: {} failures in {} runs over {} rows ({})".format(len(fails), runs, len(rows), summary))
        return 1
    print("PASS: {} runs over {} rows ({}); scorecard body + ATS drafts (ashby/greenhouse/lever) + JSON, twins "
          "rendered with score kept".format(runs, len(rows), summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())

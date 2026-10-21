"""Round-10 founder-context regressions through the real CLI (IV9-1 .. IV9-8 and the round-10 owner's fresh set).

Cases are in founder_context_r10_cases.json (see "about" there for the origins; none of them is unseen validation any
more; {P}, {C1} ... are the claims and clean sentences in its "claims"). Each case puts its notes into meeting sample-iu3 of the sample bundle and appends one item (product section
unless "sec" says otherwise; with founder_stated unless "st" is false). Every case is rendered four ways: JSON
(email_body and markdown), --format draft and --audience board --format email.
- withhold: the claim is in none of the four outputs (not in the TL;DR, metrics table, sections, reasons or notes) and
  the item's withheld reason contains the expected text; its twin (the same claim with the qualifier replaced by a
  plain sentence, or the founder-stated form of an unstated item) must print, so the reason is the one tested.
- keep: the claim is in the investor-facing part of all four outputs and the item has no withheld reason.
Usage: python3 test_founder_context_r10.py  (exit 1 on any failure)."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
SCRIPT = os.environ.get("INVESTOR_TEST_SCRIPT", str(ROOT / "build_update.py"))
DATA = json.loads((ROOT / "founder_context_r10_cases.json").read_text(encoding="utf-8"))
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")


def fill(text):
    for k, v in DATA["claims"].items():
        text = text.replace("{" + k + "}", v)
    return text


def expand(case):
    return {k: (fill(v) if isinstance(v, str) and k in ("n", "c", "x") else v) for k, v in case.items()}


def unescape(t):
    out, i = [], 0
    while i < len(t):
        if t[i] == "\\" and i + 1 < len(t) and not t[i + 1].isalnum():
            out.append(t[i + 1])
            i += 2
            continue
        out.append(t[i])
        i += 1
    return "".join(out)


def run_case(tmp, name, case):
    b = json.loads((ROOT / "sample_bundle.json").read_text(encoding="utf-8"))
    m = next(x for x in b["meetings"] if x["meeting_id"] == "sample-iu3")
    m["notes_text"] = case["n"]
    item = {"section": case.get("sec", "product"), "text": case["c"], "citations": [{"meeting_id": "sample-iu3"}]}
    if case.get("st", True):
        item["founder_stated"] = {"meeting_id": "sample-iu3", "speaker": case.get("sp", "Maren"),
                                  "excerpt": case.get("x", case["c"])}
    b["items"].append(item)
    idx = len(b["items"]) - 1
    path = Path(tmp) / (name + ".json")
    path.write_text(json.dumps(b, ensure_ascii=False), encoding="utf-8")
    base = [sys.executable, SCRIPT, "--bundle", str(path), "--metrics-csv", str(ROOT / "sample_metrics.csv"),
            "--as-of", "2026-10-02T09:00:00-04:00"]
    outs = {}
    for key, extra in (("json", ["--format", "json"]), ("draft", ["--format", "draft"]),
                       ("board_email", ["--audience", "board", "--format", "email"])):
        r = subprocess.run(base + extra, stdout=subprocess.PIPE, stderr=subprocess.PIPE, universal_newlines=True,
                           cwd=tmp, env=ENV)
        if r.returncode != 0 or "Traceback" in r.stderr:
            return {"error": "{} exit {} {}".format(key, r.returncode, r.stderr[-300:])}
        outs[key] = r.stdout
    j = json.loads(outs["json"])
    outs = {k: unescape(v) for k, v in outs.items()}
    full = {"json_email": unescape(j["email_body"]), "json_markdown": unescape(j["markdown"]),
            "draft": outs["draft"], "board_email": outs["board_email"]}
    facing = {k: v.split("## Before you send")[0] for k, v in full.items()}
    claim = case["c"]
    reasons = [w for w in j["withheld"] if "items[{}]".format(idx) in w]
    return {"present": [claim in v for v in facing.values()], "anywhere": [claim in v for v in full.values()],
            "reasons": reasons}


def main():
    jobs = []
    for c in DATA["cases"]:
        c = expand(c)
        jobs.append(("case", c["id"], c))
        if c["e"] == "w" and c.get("t"):
            t = expand(c["t"])
            t.setdefault("st", True)
            jobs.append(("twin", c["id"] + "~twin", t))
    with tempfile.TemporaryDirectory() as tmp:
        with ThreadPoolExecutor(max_workers=1) as pool:
            results = list(pool.map(lambda j: run_case(tmp, j[1].replace("~", "_"), j[2]), jobs))
    failures, counts = [], {}
    for (kind, key, case), res in zip(jobs, results):
        expect = "keep" if kind == "twin" else {"w": "withhold", "k": "keep"}[case["e"]]
        origin = "twin" if kind == "twin" else case["o"]
        tally = counts.setdefault((origin, expect), [0, 0])
        tally[0] += 1
        if "error" in res:
            failures.append((kind, key, res["error"]))
            continue
        if expect == "keep":
            good = all(res["present"]) and not res["reasons"]
        else:
            want = DATA["reasons"][case["r"]]
            good = not any(res["anywhere"]) and any(want in r for r in res["reasons"])
            if case["o"] != "owner-r10-fresh-cost" and not case.get("t"):
                good = False  # every attack needs an eligible twin
        if good:
            tally[1] += 1
        else:
            failures.append((kind, key, expect, res["present"], res["anywhere"], res["reasons"][:1]))
    for (origin, expect), (n, ok) in sorted(counts.items()):
        print("{:22s} expect {:8s} cases {:4d} pass {:4d}".format(origin, expect, n, ok))
    if failures:
        for f in failures:
            print("FAIL", f)
        sys.exit(1)
    print("PASS: {} round-10 founder-context cases and {} eligible twins, 4 outputs each".format(
        len(DATA["cases"]), sum(1 for j in jobs if j[0] == "twin")))


if __name__ == "__main__":
    main()

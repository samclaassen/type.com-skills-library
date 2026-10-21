"""Round-9 founder-context regressions through the real CLI.

Cases are in founder_context_r9_cases.json, each marked with its origin:
- "qa-r8-disclosed": strings disclosed in the round-8 QA report (regressions, not unseen validation);
- "owner-r9-tuning": cases the round-9 repair owner wrote while tuning (also not unseen validation).
Each case puts its notes (and transcript) into meeting sample-iu3 of the sample bundle and adds one product item
with founder_stated. A "withhold" case must be missing from all four outputs (JSON email_body, JSON markdown,
--format draft, --format email) and its withheld reason must contain the expected text. A "keep" case must be in
all four. Every withhold case names a safe twin (the claim alone in the same label form, or a listed clean
variant); every twin is run and must print in all four outputs, so a withheld case is known to be eligible.
Usage: python3 test_founder_context_r9.py  (exit 1 on any failure)."""
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
SCRIPT = os.environ.get("INVESTOR_TEST_SCRIPT", str(ROOT / "build_update.py"))
DATA = json.loads((ROOT / "founder_context_r9_cases.json").read_text(encoding="utf-8"))
ENV = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")


def fill(text):
    for k, v in DATA["claims"].items():
        text = text.replace("{" + k + "}", v)
    return text


def expand(row):
    """A compact case row (see "about" in the JSON) as a full case."""
    claim = fill(row.get("c", "{C}"))
    case = {"id": row.get("id"), "notes": fill(row["n"]), "transcript": fill(row.get("tr", "")),
            "speaker": row.get("sp", "Maren"), "claim": claim, "excerpt": fill(row["x"]) if "x" in row else claim}
    if "f" in row:
        case["origin"] = DATA["families"][row["f"]]["origin"]
        case["expect"] = {"w": "withhold", "k": "keep"}[row["e"]]
    if case.get("expect") == "withhold":
        case["reason"] = DATA["reasons"][row["r"]]
        case["twin"] = row["t"]
    return case


def run_case(tmp, name, case):
    b = json.loads((ROOT / "sample_bundle.json").read_text(encoding="utf-8"))
    m = next(x for x in b["meetings"] if x["meeting_id"] == "sample-iu3")
    m["notes_text"] = case["notes"]
    if case.get("transcript"):
        m["transcript_text"] = case["transcript"]
        m["transcript"] = "ok"
    b["items"].append({"section": "product", "text": case["claim"], "citations": [{"meeting_id": "sample-iu3"}],
                       "founder_stated": {"meeting_id": "sample-iu3", "speaker": case["speaker"],
                                          "excerpt": case["excerpt"]}})
    path = Path(tmp) / (name + ".json")
    path.write_text(json.dumps(b), encoding="utf-8")
    base = [sys.executable, SCRIPT, "--bundle", str(path), "--metrics-csv", str(ROOT / "sample_metrics.csv"),
            "--as-of", "2026-10-02T09:00:00-04:00"]
    outs = {}
    for fmt in ("json", "draft", "email"):
        r = subprocess.run(base + ["--format", fmt], stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                           universal_newlines=True, cwd=tmp, env=ENV)
        if r.returncode != 0 or "Traceback" in r.stderr:
            return {"error": "exit {} {}".format(r.returncode, r.stderr[-300:])}
        outs[fmt] = r.stdout
    j = json.loads(outs["json"])
    claim = case["claim"]
    # kept: in the investor-facing part of every output; withheld: nowhere in any output
    present = [claim in j["email_body"], claim in j["markdown"].split("## Before you send")[0],
               claim in outs["draft"].split("## Before you send")[0], claim in outs["email"]]
    anywhere = [claim in j["email_body"], claim in j["markdown"], claim in outs["draft"], claim in outs["email"]]
    reasons = [w for w in j["withheld"] if "items[{}]".format(len(b["items"]) - 1) in w]
    return {"present": present, "anywhere": anywhere, "reasons": reasons}


def main():
    cases = [expand(r) for r in DATA["cases"]]
    twins = {k: expand(v) for k, v in DATA["twins"].items()}
    jobs = [("case", c["id"], c) for c in cases] + [("twin", k, t) for k, t in sorted(twins.items())]
    failures = []
    counts = {}
    with tempfile.TemporaryDirectory() as tmp:
        with ThreadPoolExecutor(max_workers=1) as pool:
            results = list(pool.map(lambda j: run_case(tmp, "{}-{}".format(j[0], j[1]), j[2]), jobs))
    for (kind, key, case), res in zip(jobs, results):
        expect = "keep" if kind == "twin" else case["expect"]
        tally = counts.setdefault((case.get("origin", "twin"), expect), [0, 0])
        tally[0] += 1
        if "error" in res:
            failures.append((kind, key, res["error"]))
            continue
        if expect == "keep":
            good = all(res["present"]) and not res["reasons"]
        else:
            good = not any(res["anywhere"]) and any(case["reason"] in r for r in res["reasons"])
            if case.get("twin") not in twins:
                good = False
        if good:
            tally[1] += 1
        else:
            failures.append((kind, key, expect, res["present"], res["reasons"][:1]))
    dump = os.environ.get("FOUNDER_R9_DUMP")
    if dump:
        with open(dump, "w", encoding="utf-8") as f:
            json.dump({key: " | ".join(res.get("reasons", [])) for (kind, key, case), res in zip(jobs, results)
                       if kind == "case"}, f, indent=1)
    for (origin, expect), (n, ok) in sorted(counts.items()):
        print("{:16s} expect {:8s} cases {:4d} pass {:4d}".format(origin, expect, n, ok))
    if failures:
        for f in failures:
            print("FAIL", f)
        sys.exit(1)
    print("PASS: {} round-9 founder-context cases and {} safe twins, 4 outputs each".format(len(cases), len(twins)))


if __name__ == "__main__":
    main()

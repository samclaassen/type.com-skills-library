"""R8 founder final-output regressions. Disclosed QA cases, not unseen validation."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parent
SCRIPT = os.environ.get("INVESTOR_TEST_SCRIPT", str(ROOT / "build_update.py"))
CLAIM = "We will be profitable by March."
cases = []
def add(key, source, kept=False, reason="context", transcript=None):
    cases.append((key, source, kept, reason, transcript))
for i, cue in enumerate(["Unless churn rises.", "Just kidding.", "That's if the bridge closes.",
                        "Only if the bridge closes.", "Not really though.", "Or so the board hopes."]):
    add("V12-" + str(i), "Maren (CEO): " + CLAIM + " " + cue)
for i, cue in enumerate(["In the best case scenario for us.", "That's the optimistic version, to be clear.",
                        "Here is the dream scenario we'd pitch.", "In a perfect world, sure.",
                        "Not a promise, but here it is.", "Hypothetically this is our outlook.",
                        "We were joking about the forecast.", "Under the worst-case scenario."]):
    for side in ("before", "after"):
        add("V13-" + str(i) + "-" + side, "Maren (CEO): " + (cue + " " + CLAIM if side == "before" else CLAIM + " " + cue))
for i, source in enumerate([
    "Board member (Kestrel VC): what I'd tell LPs is\nMaren: " + CLAIM,
    "Board member: quoting the deck\n> Maren: " + CLAIM,
    "Board member: Here is the deck:\nMaren: " + CLAIM,
    "Board member: A quote follows.\n  Maren: " + CLAIM,
    "Board member: A quote follows.\n>> Maren: " + CLAIM,
    "- Maren: " + CLAIM,
    "* Maren: " + CLAIM,
    "1. Maren: " + CLAIM,
]):
    add("V14-" + str(i), source, reason="attribution")
add("repeat-qualified", "Maren: " + CLAIM + " Unless churn rises. " + CLAIM)
add("conflicting-sources", "Maren: " + CLAIM, transcript="Maren: " + CLAIM + " Just kidding.")
add("conflicting-turns", "Maren: " + CLAIM + "\nBoard: Thanks.\nMaren: " + CLAIM + " Only if churn falls.")
for i, source in enumerate([
    "Maren (CEO): " + CLAIM,
    "Maren: Revenue grew. " + CLAIM + " Launch shipped.",
    "Board member: The audit closed.\nMaren (CEO): " + CLAIM + "\nOther founder: The launch shipped.",
    "Maren: " + CLAIM + " " + CLAIM,
]):
    add("retain-" + str(i), source, True)
# Independently authored before execution; initial candidate missed 2 of 12.
fresh = json.loads((ROOT / "founder_context_independent.json").read_text())["cases"]
quotes = {x["label"]: x["quote"] for x in fresh}
# Round 9 (helper 1.3.0): a line ending mid-clause ("...report says") carries into the next line, so the indented
# label is no longer a turn boundary and the claim is refused earlier, as not whole sentences (still withheld).
R9_REASON = {"indented-quoted-label": "not whole sentences"}
cases.extend((x["label"], x["source"], x["allow"], R9_REASON.get(x["label"], "attribution" if "label" in x["label"] or "blockquote" in x["label"] else "context"), None) for x in fresh)
add("S4-founder-negator", "Maren: Nobody's at Kestrel VC: " + CLAIM, reason="not whole sentences")
add("S4-founder-safe", "Maren: " + CLAIM, True)
failures = []
with tempfile.TemporaryDirectory() as tmp:
    for key, source, kept, reason, transcript in cases:
        CLAIM = quotes.get(key, "We will be profitable by March.")
        b = json.loads((ROOT / "sample_bundle.json").read_text())
        m = next(x for x in b["meetings"] if x["meeting_id"] == "sample-iu3")
        m["notes_text"] = source
        m["transcript_text"] = transcript or ""
        b["items"] = [{"section": "product", "text": CLAIM, "citations": [{"meeting_id": "sample-iu3"}],
                       "founder_stated": {"meeting_id": "sample-iu3", "speaker": "Maren", "excerpt": CLAIM}}]
        path = Path(tmp) / "bundle.json"
        path.write_text(json.dumps(b))
        cmd = [sys.executable, SCRIPT, "--bundle", str(path), "--metrics-csv", str(ROOT / "sample_metrics.csv"),
               "--as-of", "2026-10-02T09:00:00-04:00", "--format", "json"]
        run = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, cwd=str(ROOT))
        if run.returncode:
            failures.append((key, "invalid fixture", run.returncode, run.stderr))
            continue
        out = json.loads(run.stdout)
        actual = CLAIM in out["email_body"]
        valid = actual == kept and out["sections"]["product"] == int(kept)
        if not kept:
            valid = valid and any(reason in x for x in out["withheld"])
            valid = valid and CLAIM not in out["markdown"]
        if not valid:
            failures.append((key, "expected", kept, "actual", actual, "reasons", out["withheld"]))
        print(json.dumps({"case": key, "interpreter": sys.version.split()[0], "exit": run.returncode,
                          "expected": kept, "actual": actual, "pass": bool(valid)}))
if failures:
    for f in failures:
        print("FAIL", f)
    sys.exit(1)
print("PASS: {} founder final-output cases".format(len(cases)))

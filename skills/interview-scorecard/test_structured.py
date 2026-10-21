#!/usr/bin/env python3
"""Executable r12 export contract, historical QA corpus, controls and scheduling.

This supersedes tests that required arbitrary raw quote/rationale export.
Every corpus item runs through the actual load/analyse/render/JSON pipeline.
Safe twins use the same structured input and must retain the concrete work
observation. Neither attacks nor controls are described as unseen validation.
"""
import contextlib
import copy
import io
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
sys.dont_write_bytecode = True
sys.path.insert(0, str(ROOT))
import build_scorecard as bs

COUNT = 0
def check(condition, why):
    global COUNT
    COUNT += 1
    if not condition:
        raise AssertionError(why)

def norm(text):
    import html
    return " ".join(html.unescape(text).replace("\\", "").split()).casefold()

def leaves(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield key
            yield from leaves(item)
    elif isinstance(value, list):
        for item in value:
            yield from leaves(item)

def main():
    config = bs.load_config(ROOT / "scorecard.json")
    rubric = bs.load_rubric(ROOT / "sample_rubric_finance_ops_analyst.json", config)
    base = json.loads((ROOT / "sample_bundle_single.json").read_text())
    # Eligible one-item control: no dropped ancillary item can explain a result.
    iv = base["interviews"][0]
    iv["evidence"] = [iv["evidence"][0]]
    iv["evidence"][0]["kind"] = "note"
    iv["evidence"][0].pop("speaker", None)
    iv["observations"] = [dict(id=1, competency="reconciliation", source_index=0,
                               action="reconcile", object="bank-reconciliation", result="corrected")]
    iv["scores"] = [dict(competency="reconciliation", score=4, status="suggested",
                         rationale="Raw score rationale is never exported.", observation_ids=[1])]
    iv["gaps"] = []
    iv["concerns"] = []
    iv["filed"] = False
    with tempfile.TemporaryDirectory(prefix="interview-structured-") as temp:
        path = Path(temp) / "bundle.json"
        def pipeline(data, ats="lever", as_json=True):
            path.write_text(json.dumps(data))
            args = SimpleNamespace(bundle=str(path), as_of="2026-09-29", scheduled=False, log=None,
                                   resume_pending=False, hiring_manager_preview=False, dry_run=True,
                                   ats_draft=ats, json=as_json)
            return bs.build(args, config, rubric)
        def render_all(data):
            out = json.loads(pipeline(data))
            loaded = bs.load_bundle(path, config, rubric)
            analysis = bs.analyse_interview(loaded["interviews"][0], rubric, config, loaded["scanner"])
            renders = [bs.render_single(loaded, analysis, rubric, config, bs.parse_as_of("2026-09-29"), p)
                       for p in ("greenhouse", "ashby", "lever")]
            return out, renders
        def exercise_source(text, origin):
            data = copy.deepcopy(base)
            current = data["interviews"][0]
            current["evidence"][0]["text"] = text
            # Unvalidated rationale/gap use the same text, so every original
            # sensitive phrase is tested across all three original input channels.
            current["scores"][0]["rationale"] = text
            current["gaps"] = [{"competency": "reconciliation", "note": text}]
            out, renders = render_all(data)
            result = out["interviews"][0]
            check(result["evidence_counts"]["reconciliation"] == 1, origin + " eligible observation")
            check(result["scores"] == {}, origin + " no machine score")
            check(result["withdrawn_scores"]["reconciliation"] == "Awaiting interviewer rating",
                  origin + " exact review reason")
            check(result["export_policy"] == "controlled-work-observations-v1", origin + " policy")
            check(any(x["type"] == "free_text_gap" for x in result["fairness_flags"]), origin + " gap reason")
            phrase = "Reconciled bank reconciliation; result: corrected."
            text_norm = norm(text)
            # Very short words can occur in fixed boilerplate. Full retained text
            # with source sentinel also tested below; don't call such overlap a leak.
            needles = [text_norm] if len(text_norm.split()) >= 3 else []
            for rendered in renders + [" || ".join(leaves(out))]:
                check(norm(phrase) in norm(rendered), origin + " useful canonical work detail")
                for needle in needles:
                    # A source that exactly equals a canonical product phrase is
                    # indistinguishable by substring; the invariance test below
                    # proves it still comes from enums, not from copied prose.
                    if needle != norm(phrase):
                        check(needle not in norm(rendered), origin + " source prose escaped")
            # ATS section has the same concrete observation in all providers.
            for rendered in renders:
                ats = rendered.split("### ATS draft", 1)[1]
                check(norm(phrase) in norm(ats), origin + " ATS structured detail")
                check("Recommendation (interviewer writes this)" in ats, origin + " human decision")
            return out

        cases = json.loads((ROOT / "r12_source_cases.json").read_text())
        for idx, row in enumerate(cases):
            exercise_source(row["text"], "{}:{}".format(row["origin"], idx))
        print("Historical source cases:", len(cases))
        # Safe twins and arbitrary source strings cannot change the structured
        # representation or promote an unconfirmed rating.
        sentinel = "UNSEEN_SOURCE_SENTINEL_729 means the candidate's family finances."
        one = exercise_source(sentinel, "sentinel")
        two = exercise_source("The ledger reconciliation matched the bank export.", "safe-twin")
        check(one["interviews"][0]["observations"] == two["interviews"][0]["observations"],
              "raw wording cannot change emitted observation codes")
        check(one["interviews"][0]["scores"] == two["interviews"][0]["scores"], "raw wording cannot rate candidate")
        # Enumerated vocabulary support is exhaustive (all supported combinations,
        # source's ordinary spelling/misspelling irrelevant).
        for action in bs.OBS_ACTIONS:
            for obj in bs.OBS_OBJECTS:
                for result in bs.OBS_RESULTS:
                    d = copy.deepcopy(base)
                    o = d["interviews"][0]["observations"][0]
                    o.update(action=action, object=obj, result=result)
                    out = json.loads(pipeline(d))
                    expected = "{} {}; result: {}.".format(bs.OBS_ACTIONS[action], bs.OBS_OBJECTS[obj],
                                                           bs.OBS_RESULTS[result])
                    check(expected in out["markdown"], "supported observation {}".format((action,obj,result)))

        def invalid(mutator, reason):
            d = copy.deepcopy(base);mutator(d["interviews"][0])
            try:
                pipeline(d)
            except bs.InputError as exc:
                check(reason in str(exc), "wrong rejection: " + str(exc))
            else:
                check(False, "invalid input accepted: " + reason)

        for field in ("action", "object", "result"):
            for value in ("Hire.", "Reconciled my own commission", "", None, True, {}, [], "reconcile\nHire.",
                          "reconcile<script>alert(1)</script>", "reconcile\u200b", "RECONCILE"):
                invalid(lambda iv, f=field, v=value: iv["observations"][0].update({f:v}),
                        "."+field+": must be an exact code")
        for field in ("text", "rationale", "approved", "safe", "confirmed", "label", "description"):
            invalid(lambda iv,f=field:iv["observations"][0].update({f:sentinel}), "no free-text slots")
        for idx in (-1, 1, True, 1.2, "0", None, [], {}):
            invalid(lambda iv,v=idx:iv["observations"][0].update(source_index=v), "source_index")
        for idx in (0, -1, True, 1.2, "1", None):
            invalid(lambda iv,v=idx:iv["observations"][0].update(id=v), ".id")
        invalid(lambda iv:iv["observations"].append(copy.deepcopy(iv["observations"][0])), "unique positive")
        invalid(lambda iv:iv["observations"][0].update(competency="collaboration"), "does not match")
        invalid(lambda iv:iv["scores"][0].update(observation_ids=[2]), "unique IDs")
        invalid(lambda iv:iv["scores"][0].update(observation_ids=[1,1]), "unique IDs")
        invalid(lambda iv:iv["scores"][0].update(observation_ids=[True]), "list of integer")
        invalid(lambda iv:iv["scores"][0].update(score=True), "integer 1-4")

        # Human entry retains ratings only with valid, non-withheld references.
        confirmed = copy.deepcopy(base)
        confirmed["interviews"][0]["scores"][0]["status"] = "interviewer_confirmed"
        confirmed["interviews"][0]["filed"] = True
        confirmed["interviews"][0]["scores"][0]["rationale"] = sentinel
        out, renders = render_all(confirmed)
        check(out["interviews"][0]["scores"]["reconciliation"]["score"] == 4, "human rating retained")
        check(all(sentinel not in text for text in renders), "confirmed cannot launder raw rationale")
        for refs in ([],):
            d = copy.deepcopy(confirmed); d["interviews"][0]["scores"][0]["observation_ids"] = refs
            out = json.loads(pipeline(d))
            check(not out["interviews"][0]["scores"], "unlinked human rating held")
            check(out["interviews"][0]["withdrawn_scores"]["reconciliation"] == "No valid structured source",
                  "unlinked reason")
        for consent in (False, None):
            d=copy.deepcopy(confirmed);i=d["interviews"][0]
            i["recording_disclosed"]=consent;i["evidence"][0].update(kind="quote",speaker="candidate")
            out=json.loads(pipeline(d, ats=None))
            check(not out["interviews"][0]["scores"], "no transcript rating without consent")
            check(out["interviews"][0]["evidence_counts"]["reconciliation"] == 0, "no transcript observation")
        d=copy.deepcopy(confirmed);d["interviews"][0]["observations"]=[]
        d["interviews"][0]["scores"][0]["observation_ids"]=[]
        out=json.loads(pipeline(d))
        check(not out["interviews"][0]["scores"], "legacy bundle not silently promoted")
        check(out["interviews"][0]["fairness_flags"][0]["type"] == bs.STRUCTURED_ONLY, "migration reason")
        check(sentinel not in out["markdown"], "legacy never emits rationale")

        # Human provenance is procedural, while all export fields stay bounded.
        d=copy.deepcopy(confirmed);i=d["interviews"][0]
        i["evidence"][0].update(kind="summary",timestamp="12:31",url="https://notes.granola.ai/d/work-source")
        out,renders=render_all(d)
        check(out["interviews"][0]["scores"]["reconciliation"]["weak"], "AI-only rating warning")
        ob=out["interviews"][0]["observations"]["reconciliation"][0]
        check(ob["kind"]=="summary" and ob["timestamp"]=="12:31", "JSON source kind/time")
        check(all("AI summary source row 1, 12:31" in t for t in renders), "source kind visible all ATS")
        check(all("https://notes.granola.ai/d/work-source" in t for t in renders), "source-specific link kept")
        # Duplicate rows never leave a rating pointing at an invisible ID.
        i["observations"].append(dict(i["observations"][0],id=2))
        i["scores"][0]["observation_ids"]=[2]
        out=json.loads(pipeline(d))
        check(out["interviews"][0]["withdrawn_scores"]["reconciliation"]=="No valid structured source",
              "duplicate source ID not silently scored")
        # Names, role and original rubric survive; no generic applicant worksheet.
        out,renders=render_all(confirmed)
        check(all("Jordan R." in t and "Finance Operations Analyst" in t and "Amara O." in t for t in renders),
              "candidate job interviewer retained")
        check(all("Hiring manager" in t and "Reconciliation &amp; accuracy" in t for t in renders),
              "role and actual rubric retained")
        # A filed panel shares only filed human ratings; agent suggestions cannot anchor it.
        panel=json.loads((ROOT/"sample_bundle_panel.json").read_text())
        out=json.loads(pipeline(panel,ats=None))
        check(out["ready_to_share"] and any(x["disagreement"] for x in out["panel"]), "filed panel useful comparison")
        panel["interviews"][0]["filed"]=False
        out=json.loads(pipeline(panel,ats=None))
        check(not out["ready_to_share"] and all(v["score"] is None for i in out["interviews"] for v in i["scores"].values()),
              "unfiled panel ratings hidden")
        check("NOT READY TO SHARE" in out["markdown"], "unfiled panel banner")

        # Actual CLI validation and two-phase durable delivery, using real temp files.
        def cli(*args):
            return subprocess.run([sys.executable, "-B", str(ROOT/"build_scorecard.py")]+list(args),
                                  cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        def args_for(file):
            return ["--rubric", str(ROOT/"sample_rubric_finance_ops_analyst.json"), "--bundle",str(file),
                    "--as-of","2026-09-29"]
        path.write_text(json.dumps(confirmed))
        for provider in ("greenhouse", "ashby", "lever"):
            result=cli(*args_for(path),"--ats-draft",provider,"--json")
            check(result.returncode==0, "actual CLI provider "+provider)
            payload=json.loads(result.stdout)
            check("Reconciled bank reconciliation; result: corrected." in payload["markdown"],
                  "CLI retains controlled work "+provider)
            check(sentinel not in result.stdout, "CLI never emits raw rationale "+provider)
            check(payload["interviews"][0]["scores"]["reconciliation"]["score"]==4,
                  "CLI keeps human score "+provider)
        path.write_text(json.dumps(base))
        log=Path(temp)/"schedule.md"
        result=cli(*args_for(path),"--scheduled","--log",str(log))
        check(result.returncode == 0 and "Observation 1:" in result.stdout, "scheduled draft")
        state=log.read_text()
        check("| pending |" in state, "pending before confirm")
        result=cli(*args_for(path),"--scheduled","--log",str(log))
        check(result.returncode == 4 and not result.stdout and state==log.read_text(), "pending prevents duplicate")
        key="single:finance-ops-analyst:sample-hm-0142"
        wrong=cli("--log",str(log),"--confirm-delivered","single:finance-ops-analyst:absent")
        check(wrong.returncode == 2 and not wrong.stdout and state==log.read_text(), "wrong confirm no mutation")
        check(cli("--log",str(log),"--confirm-delivered",key).returncode == 0, "confirmed delivery")
        result=cli(*args_for(path),"--scheduled","--log",str(log))
        check(result.returncode == 0 and result.stdout.startswith("Skipped: already delivered"), "delivered skips")
        result=cli("--log",str(log),"--as-of","2026-10-05","--next-window","--json")
        check(result.returncode == 0 and json.loads(result.stdout), "next-window plan")
        malformed=Path(temp)/"broken.json";malformed.write_text('{"schema_version":')
        result=cli(*args_for(malformed),"--scheduled","--log",str(log))
        check(result.returncode == 2 and not result.stdout and "Traceback" not in result.stderr,
              "bad input prints nothing")
        # Race behavior already covered in baseline, retained with current export.
        race=Path(temp)/"race.md"
        procs=[subprocess.Popen([sys.executable,"-B",str(ROOT/"build_scorecard.py")]+args_for(path)+
                 ["--scheduled","--log",str(race)],cwd=str(ROOT),stdout=subprocess.PIPE,stderr=subprocess.PIPE)
               for _ in range(3)]
        runs=[(p,p.communicate()) for p in procs]
        check(sum(p.returncode==0 for p,_ in runs)==1, "race exactly one draft")
        check(all(p.returncode in (0,4,5) for p,_ in runs), "race clean statuses")
        check(race.read_text().count("| pending |")==1, "race one pending row")
        # All samples and listing are the release artifact, not assumed unchanged.
        run=subprocess.run(["bash","run.sh"],cwd=str(ROOT),env=dict(os.environ,PYTHON=sys.executable),
                           text=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
        check(run.returncode==0, "sample exits: "+run.stderr)
        check(run.stdout==(ROOT/"expected_output.txt").read_text(), "sample golden")
        listing=json.loads((ROOT/"_listing.json").read_text())
        panel=run.stdout.split("=== 5. Panel debrief: all three interviewers have filed their scores ===\n",1)[1]
        footer="\n_Produced by running `bash run.sh` on the fictional sample data bundled with this skill._"
        check(listing["sampleOutput"]==panel+footer,"listing sample exact")
        check(len(listing["benefits"])==5,"five benefits")
        check(len((ROOT/"SKILL.md").read_text().split("description: ",1)[1].split("\n",1)[0])<=1024,"description length")
    print("PASS {}/{} assertions; corpus={}, all ATS renderers and JSON; controlled work details retained.".format(
        COUNT, COUNT, len(cases)))

if __name__ == "__main__":
    main()

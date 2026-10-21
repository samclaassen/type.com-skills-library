#!/usr/bin/env bash
set -uo pipefail

PY="${PYTHON:-python3}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
SCRIPT=build_scorecard.py
RUBRIC=sample_rubric_finance_ops_analyst.json
TOTAL=0
PASS=0

ok() { TOTAL=$((TOTAL + 1)); PASS=$((PASS + 1)); echo "ok $TOTAL - $1"; }
bad() { TOTAL=$((TOTAL + 1)); echo "FAIL $TOTAL - $1"; sed 's/^/    | /' "$TMP/out" "$TMP/err" 2>/dev/null | head -20; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

variant() {
  "$PY" - "$TMP/$1.json" "$2" "$3" <<'PY'
import json, sys
b = json.load(open(sys.argv[2], encoding="utf-8"))
I0 = (b.get("interviews") or [None])[0] if isinstance(b, dict) else None  # first interview
exec(sys.argv[3])
import os
for i in (b.get("interviews") or []) if isinstance(b, dict) and not os.environ.get("NOKIND") else []:
    for e in (i.get("evidence") or []) if isinstance(i, dict) and isinstance(i.get("evidence"), list) else []:
        if isinstance(e, dict) and "evidence_kind" not in e:
            e["evidence_kind"] = "behaviour_example"
json.dump(b, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)
PY
}
single() { variant "$1" sample_bundle_single.json "$2"; }
panel() { variant "$1" sample_bundle_panel.json "$2"; }
rubric() { variant "$1" "$RUBRIC" "$2"; }

run() { "$PY" "$SCRIPT" "$@" >"$TMP/out" 2>"$TMP/err"; RC=$?; }
has() { grep -qF -- "$1" "$TMP/$2"; }
hasnt() { ! grep -qF -- "$1" "$TMP/$2"; }
expect() {
  local code="$1" pattern="$2" stream="$3" name="$4"; shift 4
  run "$@"
  if [[ "$RC" == "$code" ]] && has "$pattern" "$stream"; then ok "$name"; else bad "$name (exit $RC, want $code; '$pattern' in std$stream?)"; fi
}
S=(--rubric "$RUBRIC" --as-of 2026-09-29)
SS=("${S[@]}" --bundle sample_bundle_single.json)
se() { single "$1" "$2"; local n="$1"; shift 2; expect "$1" "$2" "$3" "$4" "${S[@]}" --bundle "$TMP/$n.json" "${@:5}"; }
pe() { panel "$1" "$2"; local n="$1"; shift 2; expect "$1" "$2" "$3" "$4" "${P[@]}" --bundle "$TMP/$n.json" "${@:5}"; }
sr() { single "$1" "$2"; local n="$1"; shift 2; run "${S[@]}" --bundle "$TMP/$n.json" "$@"; }
rx() { rubric "$1" "$2"; local n="$1"; shift 2; expect "$1" "$2" "$3" "$4" --check-rubric --rubric "$TMP/$n.json"; }

bash run.sh >"$TMP/run1" 2>&1
check "run.sh matches expected_output.txt byte for byte" 'cmp -s "$TMP/run1" expected_output.txt'
bash run.sh >"$TMP/run2" 2>&1
check "run.sh is repeatable (idempotent output)" 'cmp -s "$TMP/run1" "$TMP/run2"'
cp sample_scorecard_log.md "$TMP/log_check.md"
check "run.sh never modifies the bundled sample log" 'cmp -s sample_scorecard_log.md "$TMP/log_check.md"'

expect 0 "approved by" out "check-rubric summarises an approved rubric" --check-rubric --rubric "$RUBRIC"
expect 0 "DRAFT, must be approved" out "check-rubric accepts the template but marks it draft" --check-rubric --rubric rubric_template.json
expect 3 "no rubric file given" err "scoring without --rubric stops (exit 3)" --bundle sample_bundle_single.json --as-of 2026-09-29
expect 3 "file not found" err "missing rubric file stops (exit 3)" --rubric "$TMP/nope.json" --bundle sample_bundle_single.json --as-of 2026-09-29
expect 3 "status is draft" err "draft rubric cannot be scored against" --rubric rubric_template.json --bundle sample_bundle_single.json --as-of 2026-09-29
se other_role 'b["role_id"] = "ap-specialist"' 3 "but the rubric is for 'finance-ops-analyst'" err "rubric for another role is refused"
rx r_anchor 'del b["competencies"][0]["anchors"]["4"]' 3 "anchors: must have a behavioural anchor for each of 1, 2, 3, 4" err "rubric missing an anchor is refused"
rx r_accent 'b["competencies"][3]["anchors"]["4"] = "Native English speaker with polished delivery."' 3 "accent or native language" err "anchor naming a proxy (native speaker) is refused"
rx r_fit 'b["competencies"][5]["name"] = "Culture fit"' 3 "undefined \"culture fit\"" err "a bare 'culture fit' competency is refused"
rx r_q 'b["interview_plan"][0]["questions"][0]["text"] = "Do you have kids, and who covers childcare?"' 3 "Never plan a question on this topic" err "a planned question on family status is refused"
rx r_noapprover 'b["approved_by"] = None' 3 "approved_by: is required when status is approved" err "approved rubric needs an approver"
rx r_two 'b["competencies"] = b["competencies"][:2]; b["interview_plan"] = []' 3 "use 3 to 10" err "rubric with too few competencies is refused"
rx r_dup 'b["competencies"][1]["id"] = "reconciliation"' 3 "duplicate competency id" err "duplicate competency ids are refused"
printf '{"schema_version": 1,' >"$TMP/r_broken.json"
expect 3 "is not valid JSON (line 1" err "malformed rubric JSON stops (exit 3)" --check-rubric --rubric "$TMP/r_broken.json"

printf '{"schema_version": 1,,}' >"$TMP/broken.json"
expect 2 "is not valid JSON (line 1" err "malformed bundle JSON exits 2 with line/column" "${S[@]}" --bundle "$TMP/broken.json"
se no_date 'del I0["date"]' 2 '$.interviews[0].date: is required' err "missing date exits 2 naming the JSON path"
check "error names the bundle file" 'grep -qF "no_date.json" "$TMP/err"'
se bad_score 'I0["scores"][0]["score"] = 5' 2 "must be an integer 1-4" err "score outside 1-4 is rejected"
se unk_comp 'I0["evidence"][0]["competency"] = "charisma"' 2 "unknown competency 'charisma'" err "evidence for a competency not in the rubric is rejected"
se unk_q 'I0["questions_asked"].append("hm-9")' 2 "unknown question id 'hm-9'" err "unknown planned question id is rejected"
se unk_stage 'I0["stage_id"] = "screen-2"' 2 "unknown stage 'screen-2'" err "unknown stage is rejected"
se empty 'b["interviews"] = []' 2 "is empty: no interview to score" err "empty interview list exits 2"
se no_tr_src 'del b["sources"]["transcript"]' 2 '$.sources.transcript: required object is missing' err "missing transcript source status exits 2"
se two 'b["interviews"].append(dict(I0, interview_id="granola:sample-x", meeting_id="sample-x", interviewer={"name": "Kai P."}))' 2 "single mode takes exactly one interview" err "single mode with two interviews exits 2"
expect 2 "--as-of" err "missing --as-of exits 2" --rubric "$RUBRIC" --bundle sample_bundle_single.json
expect 2 "--as-of" err "non-date --as-of exits 2" --rubric "$RUBRIC" --bundle sample_bundle_single.json --as-of 2026-13-01
se bad_ts 'I0["evidence"][0]["timestamp"] = "about 12 minutes in"' 2 "must look like 12:31" err "bad timestamp format is rejected"
printf '{"max_quote_chars": 400}' >"$TMP/cfg.json"
expect 2 "missing key(s)" err "incomplete config exits 2" "${SS[@]}" --config "$TMP/cfg.json"

se cand_email 'b["candidate"]["email"] = "jordan@example.example"' 2 '$.candidate.email: is not allowed' err "candidate e-mail key is rejected"
se cand_age 'b["candidate"]["age"] = 41' 2 '$.candidate.age: is not allowed' err "candidate age key is rejected"
se cand_name_mail 'b["candidate"]["display_name"] = "Jordan <jordan@mail.example>"' 2 "must be a short name such as 'Jordan R.'" err "e-mail hidden in the display name is rejected"
se tr_key 'I0["transcript"] = "full text ..."' 2 '$.interviews[0].transcript: is not allowed' err "pasted transcript in the bundle is rejected"
se long_q 'I0["evidence"][0]["text"] = "word " * 120' 2 "never paste transcripts" err "over-long quote is rejected"

P=(--rubric "$RUBRIC" --as-of 2026-10-02)
se numbers 'I0["evidence"][0]["text"] = "We closed FY2024-2025 with 1,240 invoices, a $4,000.00 difference and 3 open items."' 0 "Reconciled bank reconciliation; result: corrected." out "finance source control retains structured work observation" 
se rec 'b["recommendation"] = "hire"' 2 '$.recommendation: is not allowed' err "a hire/no-hire recommendation in the bundle is rejected"
se rec2 'I0["overall_recommendation"] = "strong yes"' 2 "overall_recommendation: is not allowed" err "per-interview recommendation is rejected"
se concern_ans 'I0["concerns"][0]["answer"] = "something personal"' 2 "never the question's wording or the answer" err "a concern carrying the answer is rejected"
se concern_bad 'I0["concerns"][0]["topic"] = "hobbies"' 2 "concerns[0].topic: must be one of" err "a concern topic outside the categories is rejected"
se falsepos 'I0["evidence"][2]["text"] = "We stayed in the black, kept the aging report clean, acted in good faith and moved to straight-through processing; I manage 3 entities and tried vibe coding a recon script."' 0 "Reconciled bank reconciliation; result: corrected." out "ordinary finance source control retains structured work observation" 
se speaker 'I0["evidence"][0]["speaker"] = "interviewer"' 2 'must be "candidate" for a transcript quote' err "a 'quote' not spoken by the candidate is rejected"
se denied 'b["sources"]["transcript"] = {"provider": "granola", "status": "denied", "detail": "workspace setting"}; I0["transcript_status"] = "denied"' 2 "is a transcript quote but transcript_status is 'denied'" err "quotes from a denied transcript are rejected"
se dupev 'I0["observations"].append(dict(I0["observations"][0],id=9))' 0 "| Reconciliation &amp; accuracy ★ | Awaiting interviewer rating | 2 quotes, 1 note |" out "duplicate structured observation is counted once without promoting a rating" 
se outside 'I0["evidence"].append({"competency": "systems_data", "kind": "quote", "speaker": "candidate", "text": "I rebuilt the AP aging workbook with lookups and a tie-out tab.", "timestamp": None, "url": None}); I0["scores"].append({"competency": "systems_data", "score": 3, "status": "interviewer_confirmed", "rationale": "Built a checkable workbook with a tie-out.", "observation_ids": [5]}); I0["observations"].append({"id":5,"competency":"systems_data","source_index":len(I0["evidence"])-1,"action":"design","object":"ledger","result":"documented"})' 0 "| Systems &amp; spreadsheet fluency (outside this stage) | 3 — Meets the bar (interviewer) | 1 quote |" out "human-rated structured work outside the stage remains visible" --ats-draft ashby
se i6_missing 'del I0["recording_disclosed"]' 2 '$.interviews[0].recording_disclosed: is required' err "i6_missing_consent_exits_2"
se i6_str 'I0["recording_disclosed"] = "yes"' 2 "must be true or false or null" err "i6_string_consent_rejected"
se x2_week 'I0["date"] = "2026-W40-2"' 2 "is not a YYYY-MM-DD date" err "x2_iso_week_date_rejected"
se x2_compact 'I0["date"] = "20260929"' 2 "is not a YYYY-MM-DD date" err "x2_compact_date_rejected"
se x2_datetime 'I0["date"] = "2026-09-29T10:00:00Z"' 2 "is not a YYYY-MM-DD date" err "x2_datetime_rejected_for_a_date_field"
se x1_name 'I0["interviewer"]["name"] = "Amara [x](https://evil.example)"' 2 "must be a short name such as 'Amara O.'" err "x1_link_in_interviewer_name_rejected"
se x1_name2 'I0["interviewer"]["name"] = "Amara <b>O.</b> | x"' 2 "must be a short name such as 'Amara O.'" err "x1_html_and_pipe_in_names_rejected"
se r2_echo_comp 'I0["evidence"][0]["competency"] = "pregnant-analyst"' 2 "unknown competency a 16-character value (not shown)" err "r2_flagged_unknown_competency_not_echoed"
se r2_echo_q 'I0["questions_asked"].append("strong-hire")' 2 "unknown question id a 11-character value (not shown)" err "r2_flagged_question_id_not_echoed"
se r2_fw_date 'I0["date"] = "２０２６-09-29"' 2 "is not a YYYY-MM-DD date" err "r2_fullwidth_digit_date_rejected"
se r2_ar_date 'I0["date"] = "٢٠٢٦-٠٩-٢٩"' 2 "is not a YYYY-MM-DD date" err "r2_arabic_indic_digit_date_rejected"
se r2_ar_ts 'I0["evidence"][0]["timestamp"] = "١٢:٣٠"' 2 "must look like 12:31 or 01:12:31" err "r2_arabic_indic_timestamp_rejected"
se p3_email_title 'I0["title"] = "Screen with a@b.c"; I0["stage_id"] = None' 2 "must not contain an e-mail address" err "p3_one_letter_tld_email_in_title_exits_2"
se p3_hire_title 'i = I0; i["title"] = "Onsite - hire"; i["stage_id"] = None' 2 "refers to hiring-decision language" err "p3_title_with_a_verdict_still_exits_2"
se r5_concern_pay 'I0["concerns"] = [{"topic": "compensation"}]' 2 "concerns[0].topic" err "r5_compensation_is_not_a_question_of_concern_topic"
cp sample_scorecard_log.md "$TMP/log.md"
K=single:finance-ops-analyst:sample-hm-0142
expect 0 "Interview scorecard (DRAFT)" out "dry run drafts" "${SS[@]}" --log "$TMP/log.md" --dry-run --scheduled
check "--dry-run leaves the log unchanged" 'cmp -s sample_scorecard_log.md "$TMP/log.md"'
expect 0 "Interview scorecard (DRAFT)" out "a person asking (no --scheduled) gets the scorecard" "${SS[@]}" --log "$TMP/log.md"
check "on-request runs never write the log" 'cmp -s sample_scorecard_log.md "$TMP/log.md"'
expect 0 "Interview scorecard (DRAFT)" out "first scheduled run drafts" "${SS[@]}" --log "$TMP/log.md" --scheduled
check "first scheduled run records the key as pending (not delivered)" 'grep -qF "| $K | single | finance-ops-analyst | cand-0142 | 1 | 2026-09-29 | pending |" "$TMP/log.md"'
expect 4 "delivery was never confirmed" err "x4_pending_rerun_exits_4: rerun of a pending key exits 4" "${SS[@]}" --log "$TMP/log.md" --scheduled
check "x4_pending_rerun_prints_nothing: a pending rerun prints no scorecard" '[[ ! -s "$TMP/out" ]]'
expect 0 "Interview scorecard (DRAFT)" out "--resume-pending drafts a pending key again" "${SS[@]}" --log "$TMP/log.md" --scheduled --resume-pending
check "resume keeps exactly one (pending) row" '[[ "$(grep -c "sample-hm-0142" "$TMP/log.md")" -eq 1 ]] && grep -q "sample-hm-0142 .*| pending |" "$TMP/log.md"'
expect 0 "Confirmed: $K marked delivered" out "x4_confirm_delivered marks the key delivered" --log "$TMP/log.md" --confirm-delivered "$K"
check "confirmed row says delivered" 'grep -q "sample-hm-0142 .*| delivered |" "$TMP/log.md"'
expect 0 "Already confirmed" out "confirming twice is harmless" --log "$TMP/log.md" --confirm-delivered "$K"
expect 0 "Skipped: already delivered $K" out "scheduled run after delivery is skipped" "${SS[@]}" --log "$TMP/log.md" --scheduled
check "skip notice is exactly one line" '[[ "$(wc -l <"$TMP/out")" -eq 1 ]]'
expect 2 "nothing to confirm" err "confirming an unknown key exits 2" --log "$TMP/log.md" --confirm-delivered single:finance-ops-analyst:nope
expect 2 "is not a scorecard key" err "confirming a malformed key exits 2" --log "$TMP/log.md" --confirm-delivered "single:x|y"
expect 2 "--confirm-delivered needs --log" err "confirm without a log exits 2" --confirm-delivered "$K"
check "log has exactly one row for the interview" '[[ "$(grep -c "sample-hm-0142" "$TMP/log.md")" -eq 1 ]]'
check "log holds no candidate names" '! grep -q "Jordan" "$TMP/log.md"'
cp sample_scorecard_log.md "$TMP/lock_log.md"; : >"$TMP/lock_log.md.lock"
expect 5 "another run holds" err "x4_lock_busy: a fresh lock file stops a concurrent scheduled run (exit 5)" "${SS[@]}" --log "$TMP/lock_log.md" --scheduled
check "a locked run prints nothing and writes nothing" '[[ ! -s "$TMP/out" ]] && cmp -s sample_scorecard_log.md "$TMP/lock_log.md"'
touch -d "20 minutes ago" "$TMP/lock_log.md.lock"
expect 0 "Interview scorecard (DRAFT)" out "x4_stale_lock: a lock older than 15 minutes is cleared" "${SS[@]}" --log "$TMP/lock_log.md" --scheduled
check "the lock file is released after the run" '[[ ! -e "$TMP/lock_log.md.lock" ]]'
cp sample_scorecard_log.md "$TMP/log2.md"
"$PY" "$SCRIPT" "${P[@]}" --bundle sample_bundle_panel.json --log "$TMP/log2.md" --scheduled >/dev/null 2>&1
"$PY" "$SCRIPT" --log "$TMP/log2.md" --confirm-delivered "$(grep -o "panel:finance-ops-analyst:cand-0142:[0-9a-f]*" "$TMP/log2.md")" >/dev/null 2>&1
expect 0 "Skipped: already delivered panel:finance-ops-analyst:cand-0142:" out "a delivered panel debrief is skipped on the next schedule" "${P[@]}" --bundle sample_bundle_panel.json --log "$TMP/log2.md" --scheduled
pe panel_more 'x = json.loads(json.dumps(b["interviews"][2])); x["interview_id"] = "granola:sample-ref-0142"; x["meeting_id"] = "sample-ref-0142"; x["stage_id"] = None; x["interviewer"] = {"name": "Noor A."}; b["interviews"].append(x)' 0 "Panel debrief (DRAFT)" out "a new interview for the candidate makes a new panel key" --log "$TMP/log2.md" --scheduled
single notes_down 'b["sources"]["notes"]["status"] = "unavailable"; i = I0; i["notes_status"] = "unavailable"; i["evidence"] = [e for e in i["evidence"] if e["kind"] == "quote"]; i["observations"] = []; i["scores"] = []'
cp sample_scorecard_log.md "$TMP/log3.md"
expect 0 "Skipped: notes not available" out "x4_source_not_ok: scheduled run with unreadable notes drafts nothing" "${S[@]}" --bundle "$TMP/notes_down.json" --log "$TMP/log3.md" --scheduled
check "x4_source_not_ok_logged_as_skipped: and logs the key as skipped (nothing drafted)" 'grep -q "sample-hm-0142 .*| skipped |" "$TMP/log3.md" && [[ "$(grep -c "| skipped |" "$TMP/log3.md")" -eq 1 ]]'
echo "random notes" >"$TMP/bad_log.md"
expect 2 "not a scorecard log" err "a file that is not a scorecard log refuses to run" "${SS[@]}" --log "$TMP/bad_log.md"
printf '# Interview scorecard log\n\n| key | kind | role_id | candidate_ref | interviews | drafted_at | status |\n|---|---|---|---|---|---|---|\n| single:x:y | single | x |\n' >"$TMP/torn_log.md"
expect 2 "is not a valid log row" err "a malformed log row stops the run instead of guessing" "${SS[@]}" --log "$TMP/torn_log.md" --scheduled
expect 2 "--scheduled needs --log" err "scheduled mode without a log exits 2" "${SS[@]}" --scheduled
expect 2 "--resume-pending only applies" err "--resume-pending without --scheduled exits 2" "${SS[@]}" --resume-pending
"$PY" "$SCRIPT" "${SS[@]}" --log "$TMP/new_log.md" --scheduled >/dev/null 2>&1
check "a missing log is created with the header" 'head -1 "$TMP/new_log.md" | grep -qxF "# Interview scorecard log" && grep -qF "| status |" "$TMP/new_log.md"'
"$PY" -c 'import sys; s = open("sample_bundle_single.json", encoding="utf-8").read().replace("Clear tie-out steps", "Clear \\ud800 tie-out steps"); open(sys.argv[1], "w", encoding="utf-8").write(s)' "$TMP/p3_sur.json"
cp sample_scorecard_log.md "$TMP/p3_sur_log.md"
run "${S[@]}" --bundle "$TMP/p3_sur.json" --scheduled --log "$TMP/p3_sur_log.md"
check "p3_lone_surrogate_in_bundle_exits_2_before_output_or_log" '[[ "$RC" == 2 ]] && [[ ! -s "$TMP/out" ]] && hasnt "Traceback" err && has "unpaired surrogate" err && cmp -s sample_scorecard_log.md "$TMP/p3_sur_log.md"'
"$PY" -c 'import sys; s = open(sys.argv[2], encoding="utf-8").read().replace("Meets the bar", "Meets \\udc80 bar"); open(sys.argv[1], "w", encoding="utf-8").write(s)' "$TMP/p3_sur_rubric.json" "$RUBRIC"
run --rubric "$TMP/p3_sur_rubric.json" --as-of 2026-09-29 --bundle sample_bundle_single.json
check "p3_lone_surrogate_in_rubric_exits_cleanly" '[[ "$RC" == 3 ]] && [[ ! -s "$TMP/out" ]] && hasnt "Traceback" err && has "unpaired surrogate" err'

cp sample_scorecard_log.md "$TMP/p3_closed_log.md"
"$PY" "$SCRIPT" "${SS[@]}" --scheduled --log "$TMP/p3_closed_log.md" >&- 2>"$TMP/err"; RC=$?
check "p3_closed_stdout_exits_6_without_traceback_and_stays_pending" '[[ "$RC" == 6 ]] && hasnt "Traceback" err && has "--resume-pending" err && grep -qF "| pending |" "$TMP/p3_closed_log.md"'
run "${SS[@]}" --scheduled --log "$TMP/p3_closed_log.md"
check "p3_after_failed_print_the_next_run_stops_on_pending" '[[ "$RC" == 4 ]] && [[ ! -s "$TMP/out" ]]'
"$PY" - >"$TMP/out" 2>&1 <<'PY'
import os, subprocess, sys
r, w = os.pipe()
os.close(r)
p = subprocess.run([sys.executable, "build_scorecard.py", "--rubric", "sample_rubric_finance_ops_analyst.json",
                    "--as-of", "2026-10-02", "--bundle", "sample_bundle_panel.json"], stdout=w, stderr=subprocess.PIPE)
print("rc", p.returncode)
print(p.stderr.decode("utf-8", "replace"))
PY
check "p3_broken_pipe_exits_6_cleanly" 'grep -qx "rc 6" "$TMP/out" && ! grep -q "Traceback\|Exception ignored" "$TMP/out"'

cp sample_scorecard_log.md "$TMP/p3_lockdir.md"
mkdir "$TMP/p3_lockdir.md.lock"
expect 2 "is not a regular file" err "p3_folder_at_lock_path_exits_2" "${SS[@]}" --scheduled --log "$TMP/p3_lockdir.md"
cp sample_scorecard_log.md "$TMP/p3_future.md"
"$PY" -c 'import os, sys; open(sys.argv[1], "w").write("999 old-run\n"); t = 4102444800; os.utime(sys.argv[1], (t, t))' "$TMP/p3_future.md.lock"
run "${SS[@]}" --scheduled --log "$TMP/p3_future.md"
check "p3_lock_dated_in_the_future_is_treated_as_stale" '[[ "$RC" == 0 ]] && [[ ! -e "$TMP/p3_future.md.lock" ]] && has "Interview scorecard (DRAFT)" out'
"$PY" - "$TMP" >"$TMP/out" 2>&1 <<'PY'
import os, sys
sys.dont_write_bytecode = True
sys.path.insert(0, ".")
import build_scorecard as bs
log = os.path.join(sys.argv[1], "p3_own.md")
lock = bs.acquire_lock(log)
path = log + ".lock"
os.remove(path)  # simulate another run taking the lock over after this one looked stale
with open(path, "w") as f:
    f.write("12345 another-run-token\n")
bs.release_lock(lock)
print("kept" if os.path.exists(path) and open(path).read() == "12345 another-run-token\n" else "removed")
PY
check "p3_a_run_removes_only_its_own_lock" 'grep -qx kept "$TMP/out"'
cp sample_scorecard_log.md "$TMP/p3_race.md"
for i in 1 2 3 4 5 6; do
  ("$PY" "$SCRIPT" "${SS[@]}" --scheduled --log "$TMP/p3_race.md" >"$TMP/race_$i.out" 2>/dev/null; echo $? >"$TMP/race_$i.rc") &
done
wait
check "p3_concurrent_scheduled_runs_draft_the_key_once" '[[ $(cat "$TMP"/race_*.rc | grep -c "^0$") == 1 ]] && [[ $(grep -l "Interview scorecard (DRAFT)" "$TMP"/race_*.out | wc -l) -eq 1 ]] && ! cat "$TMP"/race_*.rc | grep -qv "^[045]$" && [[ $(grep -c "| pending |" "$TMP/p3_race.md") == 1 ]]'

single r4_down 'b["sources"]["notes"]["status"] = "unavailable"; i = I0; i["notes_status"] = "unavailable"; i["evidence"] = [e for e in i["evidence"] if e["kind"] == "quote"]; i["observations"] = []; i["scores"] = []'
cp sample_scorecard_log.md "$TMP/r4_log.md"
run "${S[@]}" --bundle "$TMP/r4_down.json" --log "$TMP/r4_log.md" --scheduled
check "r4_i2_unreadable_notes_are_logged_as_skipped" '[[ "$RC" == 0 ]] && has "Skipped: notes not available" out && grep -qF "| single:finance-ops-analyst:sample-hm-0142 | single | finance-ops-analyst | cand-0142 | 1 | 2026-09-29 | skipped |" "$TMP/r4_log.md"'
run --log "$TMP/r4_log.md" --as-of 2026-10-01 --next-window
check "r4_i2_next_window_lists_the_skipped_key_for_a_rerun" '[[ "$RC" == 0 ]] && has "single:finance-ops-analyst:sample-hm-0142 — skipped on 2026-09-29" out'
run "${SS[@]}" --log "$TMP/r4_log.md" --scheduled
check "r4_i2_a_skipped_key_is_drafted_once_its_notes_can_be_read" '[[ "$RC" == 0 ]] && has "Interview scorecard (DRAFT)" out && grep -q "sample-hm-0142 .*| pending |" "$TMP/r4_log.md" && [[ "$(grep -c sample-hm-0142 "$TMP/r4_log.md")" -eq 1 ]]'
run --log "$TMP/r4_log.md" --as-of 2026-10-01 --next-window
check "r4_i2_next_window_lists_a_pending_key_with_how_to_resume" '[[ "$RC" == 0 ]] && has "single:finance-ops-analyst:sample-hm-0142 — pending since 2026-09-29" out && has "--resume-pending" out'
run --log "$TMP/r4_log.md" --as-of 2026-10-02 --next-window --json
check "r4_i2_first_window_without_a_listing_looks_back_7_days" '[[ "$RC" == 0 ]] && "$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); assert d[\"custom_start\"] == \"2026-09-25\" and d[\"custom_end\"] == \"2026-10-03\" and d[\"since\"] is None, d" "$TMP/out"'
run --log "$TMP/r4_log.md" --as-of 2026-10-02 --mark-listed
check "r4_i2_mark_listed_records_the_listing" '[[ "$RC" == 0 ]] && grep -qxF "Listed through: 2026-10-02" "$TMP/r4_log.md"'
run --log "$TMP/r4_log.md" --as-of 2026-10-05 --next-window --json
check "r4_i2_monday_window_starts_at_the_friday_listing" '[[ "$RC" == 0 ]] && "$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); assert d[\"custom_start\"] == \"2026-10-02\" and d[\"custom_end\"] == \"2026-10-06\" and d[\"since\"] == \"2026-10-02\" and [r[\"key\"] for r in d[\"rerun\"]] == [\"single:finance-ops-analyst:sample-hm-0142\"], d" "$TMP/out"'
run --log "$TMP/r4_log.md" --as-of 2026-10-01 --mark-listed
check "r4_i2_an_older_mark_never_moves_the_listing_back" '[[ "$RC" == 0 ]] && grep -qxF "Listed through: 2026-10-02" "$TMP/r4_log.md" && has "kept 2026-10-02" out'
expect 2 "--next-window needs --log" err "r4_i2_next_window_without_a_log_exits_2" --as-of 2026-10-05 --next-window
expect 2 "--as-of" err "r4_i2_mark_listed_without_as_of_exits_2" --log "$TMP/r4_log.md" --mark-listed
check "r4_i2_docs_drop_the_fixed_lookback_and_describe_the_listing" '! grep -q "lookback_hours" SKILL.md DATA_CONTRACT.md README.md && ! grep -qF "retried rather than lost" README.md && grep -qF -- "--next-window" SKILL.md && grep -qF -- "--mark-listed" SKILL.md && grep -qF -- "--next-window" DATA_CONTRACT.md'
"$PY" - "$TMP" >"$TMP/r4_sim" 2>&1 <<'PY'
import datetime as dt, json, subprocess, sys
tmp = sys.argv[1]
base = json.load(open("sample_bundle_single.json", encoding="utf-8"))
log = tmp + "/sim_log.md"
open(log, "w", encoding="utf-8").write(open("sample_scorecard_log.md", encoding="utf-8").read())
D = dt.datetime
meetings = [("m-mon", D(2026, 9, 28, 11)), ("m-tue", D(2026, 9, 29, 17, 30)), ("m-tue-late", D(2026, 9, 29, 19)),
            ("m-wed", D(2026, 9, 30, 10)), ("m-thu", D(2026, 10, 1, 15)), ("m-fri-am", D(2026, 10, 2, 9)),
            ("m-fri-eve", D(2026, 10, 2, 18, 30)), ("m-sat", D(2026, 10, 3, 10)), ("m-sun", D(2026, 10, 4, 17)),
            ("m-sun-late", D(2026, 10, 4, 20)), ("m-mon2", D(2026, 10, 5, 9)), ("m-wed2", D(2026, 10, 7, 12)),
            ("m-fri2-eve", D(2026, 10, 9, 23)), ("m-sat2", D(2026, 10, 10, 14))]
unreadable_until = {"m-thu": D(2026, 10, 5, 0)}           # notes can't be read on the Thu and Fri runs
fail_send_once = {"m-tue", "m-sat"}                         # the first send of these fails
runs = [D(2026, 9, 28, 18) + dt.timedelta(days=n) for n in range(14)]
runs = [r for r in runs if r.weekday() < 5]
sent, failed_once, skipped_seen, refused = {}, set(), set(), set()

def script(*args):
    p = subprocess.run([sys.executable, "build_scorecard.py"] + list(args), stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE)
    return p.returncode, p.stdout.decode("utf-8"), p.stderr.decode("utf-8")

def bundle(mid, now):
    b = json.loads(json.dumps(base))
    i = b["interviews"][0]
    i["meeting_id"], i["interview_id"] = mid, "granola:" + mid
    if mid in unreadable_until and now < unreadable_until[mid]:
        b["sources"]["notes"]["status"] = "unavailable"
        i["notes_status"] = "unavailable"
        i["evidence"] = [e for e in i["evidence"] if e["kind"] == "quote"]; i["observations"] = []; i["scores"] = []
    f = "{}/sim_{}.json".format(tmp, mid)
    json.dump(b, open(f, "w", encoding="utf-8"))
    return f

for now in runs:
    as_of = now.date().isoformat()
    rc, out, err = script("--log", log, "--as-of", as_of, "--next-window", "--json")
    assert rc == 0, err
    w = json.loads(out)
    start, end = dt.date.fromisoformat(w["custom_start"]), dt.date.fromisoformat(w["custom_end"])
    listed = [m for m, t in meetings if start <= t.date() < end and t <= now]
    todo = listed + [r["key"].split(":")[-1] for r in w["rerun"] if r["key"].split(":")[-1] not in listed]
    for mid in todo:
        key = "single:finance-ops-analyst:" + mid
        resume = [r for r in w["rerun"] if r["key"] == key and r["status"] == "pending"]
        args = ["--rubric", "sample_rubric_finance_ops_analyst.json", "--bundle", bundle(mid, now), "--as-of", as_of,
                "--scheduled", "--log", log] + (["--resume-pending"] if resume else [])
        rc, out, err = script(*args)
        assert rc == 0, (mid, as_of, rc, err)
        if out.startswith("Skipped: notes not available"):
            skipped_seen.add(mid)
            rc, out, err = script("--log", log, "--confirm-delivered", key)  # an agent confirming it by mistake
            row = [l.rstrip() for l in open(log, encoding="utf-8") if "| {} |".format(key) in l]
            if rc == 2 and not out and "is skipped" in err and len(row) == 1 and row[0].endswith("| skipped |"):
                refused.add(mid)
            continue
        if out.startswith("Skipped: already delivered"):
            continue
        assert "Interview scorecard (DRAFT)" in out, (mid, out[:200])
        if mid in fail_send_once and mid not in failed_once:
            failed_once.add(mid)                            # send failed: don't confirm
            continue
        sent[mid] = sent.get(mid, 0) + 1
        rc, out, err = script("--log", log, "--confirm-delivered", key)
        assert rc == 0, err
    rc, out, err = script("--log", log, "--as-of", as_of, "--mark-listed")
    assert rc == 0, err

due = [m for m, t in meetings if t <= runs[-1]]
print("sim runs={} meetings={} delivered={} duplicates={} missed={} failed_sends={} skipped_then_sent={}".format(
    len(runs), len(due), len(sent), sum(1 for v in sent.values() if v > 1), sorted(set(due) - set(sent)),
    len(failed_once), sorted(skipped_seen & set(sent))))
old = {m for m, t in meetings for r in runs if r - dt.timedelta(hours=24) < t <= r}
print("lookback24 missed={}".format(sorted(set(due) - old)))
print("skipped_confirm_refused={} skipped_seen={}".format(sorted(refused), sorted(skipped_seen)))
PY
sed 's/^/    # /' "$TMP/r4_sim"
check "r4_i2_multi_day_schedule_delivers_every_interview_once_incl_weekend_unreadable_and_failed_sends" 'grep -qE "^sim runs=10 meetings=12 delivered=12 duplicates=0 missed=\[\] failed_sends=2 skipped_then_sent=\[.m-thu.\]$" "$TMP/r4_sim"'
check "r6_i9_simulation_refuses_confirming_a_skipped_key_which_stays_skipped_and_is_sent_later" 'grep -qxF "skipped_confirm_refused=['"'"'m-thu'"'"'] skipped_seen=['"'"'m-thu'"'"']" "$TMP/r4_sim" && grep -qE "^sim .* delivered=12 duplicates=0 missed=\[\] .*skipped_then_sent=\[.m-thu.\]$" "$TMP/r4_sim"'
check "r4_i2_simulation_shows_a_24_hour_lookback_would_miss_the_weekend" 'grep -qF "lookback24 missed=['"'"'m-fri-eve'"'"', '"'"'m-sat'"'"', '"'"'m-sun'"'"']" "$TMP/r4_sim"'

printf "%s/%s validation and scheduler checks passed.\n" "$PASS" "$TOTAL"
[[ "$PASS" == "$TOTAL" ]]

#!/usr/bin/env bash
# Guardrail, fuzz and scenario tests. Run from the skill directory: bash tests.sh
# Builds variants of the fictional sample bundles in a temp dir; never touches real data.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

TMP="$(mktemp -d)"
trap 'chmod -R u+w "$TMP" 2>/dev/null; rm -rf "$TMP"' EXIT
SCRIPT=build_decision_log.py
W1=(--as-of 2026-10-09T16:00:00-07:00)
W2=(--as-of 2026-10-16T16:00:00-07:00)
PASS=0

ok() { PASS=$((PASS + 1)); echo "ok $PASS - $1"; }
fail() { echo "FAIL - $1"; exit 1; }

# variant NAME SOURCE PYTHON_EXPR : writes $TMP/NAME.json from SOURCE after applying the edit to `b`
variant() {
  python3 - "$TMP/$1.json" "$2" "$3" <<'PY'
import json, sys
b = json.load(open(sys.argv[2], encoding="utf-8"))
exec(sys.argv[3])
json.dump(b, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)
PY
}

# run CODE NAME -- args... : exit code must match; errors (2,3,4) must leave stdout empty; never a traceback
run() {
  local code="$1" name="$2"; shift 2
  set +e
  python3 "$SCRIPT" "$@" >"$TMP/out" 2>"$TMP/err"
  local got=$?
  set -e
  [[ "$got" == "$code" ]] || { cat "$TMP/out" "$TMP/err"; fail "$name: exit $got, expected $code"; }
  ! grep -q "Traceback" "$TMP/out" "$TMP/err" || { cat "$TMP/err"; fail "$name: traceback"; }
  if [[ "$code" == 2 || "$code" == 3 || "$code" == 4 ]]; then
    [[ ! -s "$TMP/out" ]] || { cat "$TMP/out"; fail "$name: stdout not empty on exit $code"; }
  fi
}

# expect CODE PATTERN STREAM NAME -- args...   (STREAM is out or err)
expect() {
  local code="$1" pattern="$2" stream="$3" name="$4"; shift 4
  run "$code" "$name" "$@"
  grep -qF -- "$pattern" "$TMP/$stream" || { cat "$TMP/out" "$TMP/err"; fail "$name: '$pattern' not in std$stream"; }
  ok "$name"
}

has() { grep -qF -- "$1" "$TMP/out" || { cat "$TMP/out"; fail "$2: '$1' missing"; }; ok "$2"; }
hasnt() { ! grep -qF -- "$1" "$TMP/out" || { cat "$TMP/out"; fail "$2: '$1' present"; }; ok "$2"; }
fresh() { rm -f "$TMP/$1.json" "$TMP/$1.json.lock"; echo "$TMP/$1.json"; }

# A log after week 1 was posted and confirmed (the state run.sh reaches after step 3).
week1_log() {
  local log; log="$(fresh "$1")"
  python3 "$SCRIPT" --bundle sample_bundle_week1.json "${W1[@]}" --log "$log" --scheduled >/dev/null 2>&1
  python3 "$SCRIPT" --confirm-posted weekly:2026-10-05..2026-10-09 --log "$log" >/dev/null 2>&1
  echo "$log"
}

# ---------------------------------------------------------------- sample, determinism, docs
bash run.sh >"$TMP/run.txt"
cmp -s "$TMP/run.txt" expected_output.txt || { diff "$TMP/run.txt" expected_output.txt | head -20; fail "run.sh output differs from expected_output.txt"; }
ok "run.sh matches expected_output.txt byte for byte"
bash run.sh | cmp -s - "$TMP/run.txt" || fail "second run.sh differs (not deterministic)"
ok "run.sh is deterministic across runs"

python3 - <<'PY' || fail "docs consistency"
import json, os, re
listing = json.load(open("_listing.json", encoding="utf-8"))
out = open("expected_output.txt", encoding="utf-8").read()
sec = out.split("=== 6.", 1)[1].split("\n", 1)[1].split("\n=== 7.", 1)[0]
report = sec.split("\npending: ", 1)[0].rstrip("\n")
assert listing["sampleOutput"].split("\n\n_Produced by", 1)[0] == report, "sampleOutput differs from run.sh section 6"
assert listing["sampleOutput"].endswith("_Produced by running `bash run.sh` on the fictional sample data bundled with this skill (second weekly run)._")
for key in ["slug", "title", "summary", "benefits", "requirements", "roles", "businessTypes", "authorName", "sampleOutput"]:
    assert key in listing, key
assert listing["slug"] == "decision-log" and len(listing["benefits"]) == 5
assert set(listing["roles"]) <= {"sales-marketing", "customer-success", "finance-operations"}
assert set(listing["businessTypes"]) <= {"b2b", "dtc", "agency"}
skill = open("SKILL.md", encoding="utf-8").read()
m = re.match(r"---\nname: decision-log\ndescription: ([^\n]+)\n---\n", skill)
assert m and not m.group(1).startswith(("|", ">")), "frontmatter must be name + one-line description"
assert "Use when" in m.group(1)
config = json.load(open("decision_log.json", encoding="utf-8"))
contract = open("DATA_CONTRACT.md", encoding="utf-8").read()
for key in config:
    assert "`%s`" % key in contract, "config key %s undocumented" % key
for key in re.findall(r"^([a-z_]+):", skill.split("```yaml", 1)[1].split("```", 1)[0], re.M):
    body = skill.split("## Config", 1)[1].split("```", 2)[2]
    assert re.search(r"`%s[`:]" % key, body), "settings key %s is never used in SKILL.md" % key
files = sorted(f for f in os.listdir(".") if f != "_listing.json")
named = re.findall(r"`([A-Za-z_.]+\.(?:py|json|sh|txt|md))`", skill.split("## Config", 1)[0])
for f in named:
    assert os.path.isfile(f), "SKILL.md names a missing file: " + f
readme = open("README.md", encoding="utf-8").read()
for f in files:
    assert f == "README.md" or f in readme, "README.md does not list " + f
assert len(files) <= 40 and all(os.path.getsize(f) <= 256 * 1024 for f in files)
assert sum(os.path.getsize(f) for f in files) <= 512 * 1024
assert not any(os.path.isdir(f) for f in os.listdir(".")), "no subfolders"
for f in files:
    text = open(f, encoding="utf-8").read()
    for email in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text):
        assert email.endswith(".example"), "%s: non-.example address %s" % (f, email)
for f in ["sample_bundle_week1.json", "sample_bundle_week2.json"]:
    for url in re.findall(r"https?://[^\s\"]+", open(f, encoding="utf-8").read()):
        assert url.startswith("https://notes.granola.ai/d/sample-"), url
PY
ok "docs: sampleOutput is run.sh section 6, frontmatter, config keys documented, files listed, sizes, .example only"

# ---------------------------------------------------------------- input validation (exit 2, names file + path, nothing printed)
printf '{"schema_version": 1,,}' >"$TMP/broken.json"
expect 2 "is not valid JSON (line 1" err "malformed JSON rejected" --bundle "$TMP/broken.json" "${W1[@]}" --log "$(fresh v)"
grep -qF "broken.json" "$TMP/err" || fail "error does not name the file"
ok "error names the bundle file"
printf '\xff\xfe{}' >"$TMP/binary.json"
expect 2 "is not UTF-8 text" err "non-UTF-8 bundle rejected" --bundle "$TMP/binary.json" "${W1[@]}" --log "$(fresh v)"
expect 2 "file not found" err "missing bundle rejected" --bundle "$TMP/nope.json" "${W1[@]}" --log "$(fresh v)"
printf '{"schema_version": NaN}' >"$TMP/nan.json"
expect 2 "NaN is not allowed" err "NaN rejected" --bundle "$TMP/nan.json" "${W1[@]}" --log "$(fresh v)"
printf '{"schema_version": 1, "scan": {"from": "\\ud800"}}' >"$TMP/sur.json"
expect 2 "lone surrogate" err "lone surrogate rejected" --bundle "$TMP/sur.json" "${W1[@]}" --log "$(fresh v)"
python3 -c 'print("[" * 100000 + "]" * 100000)' >"$TMP/deep.json"
expect 2 "nested too deeply" err "deeply nested JSON rejected" --bundle "$TMP/deep.json" "${W1[@]}" --log "$(fresh v)"

check() {  # check NAME EDIT PATTERN [SOURCE] [ASOF...]
  local name="$1" edit="$2" pattern="$3" src="${4:-sample_bundle_week1.json}"
  variant "$name" "$src" "$edit"
  expect 2 "$pattern" err "$name" --bundle "$TMP/$name.json" "${W1[@]}" --log "$(fresh v)"
}
check schema 'b["schema_version"] = 2' '$.schema_version: must be 1'
check schema_bool 'b["schema_version"] = True' '$.schema_version: must be 1'
check no_scan 'del b["scan"]' '$.scan: required object is missing'
check scan_order 'b["scan"]["from"] = "2026-10-10"' 'from (2026-10-10) is after to (2026-10-09)'
check scan_future 'b["scan"]["to"] = "2026-10-12"' '$.scan.to: 2026-10-12 is after --as-of'
check cadence 'b["scan"]["cadence"] = "daily"' '$.scan.cadence: must be one of weekly, on_request'
check wsonly 'b["scan"]["workspace_only"] = "yes"' '$.scan.workspace_only: must be true or false'
check no_source 'del b["sources"]["meetings"]' '$.sources.meetings: required object is missing'
check source_status 'b["sources"]["meetings"]["status"] = "broken"' '$.sources.meetings.status: must be one of'
check meetings_null 'b["meetings"] = None' '$.meetings: must be a list, got null'
check decisions_dict 'b["decisions"] = {}' '$.decisions: must be a list, got dict'
check dup_meeting 'b["meetings"][1]["meeting_id"] = "sample-pc1005"' "duplicate meeting_id 'sample-pc1005'"
check bad_mid 'b["meetings"][0]["meeting_id"] = "a b"' "is not a meeting id"
check naive 'b["meetings"][0]["start"] = "2026-10-05T10:00:00"' 'has no UTC offset'
check compact 'b["meetings"][0]["start"] = "20261005T100000Z"' 'is not an ISO-8601 datetime'
check isoweek 'b["meetings"][0]["start"] = "2026-W41-1T10:00:00Z"' 'is not an ISO-8601 datetime'
check t24 'b["meetings"][0]["start"] = "2026-10-05T24:00:00Z"' 'is not an ISO-8601 datetime'
check offset60 'b["meetings"][0]["start"] = "2026-10-05T10:00:00+00:60"' 'is not an ISO-8601 datetime'
check offset15 'b["meetings"][0]["start"] = "2026-10-05T10:00:00+15:00"' 'is not an ISO-8601 datetime'
check arabic_digits 'b["meetings"][0]["start"] = "٢٠٢٦-10-05T10:00:00Z"' 'is not an ISO-8601 datetime'
check feb30 'b["decisions"][1]["revisit_on"] = "2026-02-30"' 'is not a valid date'
check year 'b["decisions"][1]["revisit_on"] = "3001-01-01"' 'outside the years 1900-2999'
check outside 'b["meetings"][0]["start"] = "2026-10-04T10:00:00-07:00"' 'is outside the scan window'
check after_asof 'b["meetings"][4]["start"] = "2026-10-09T17:00:00-07:00"' '$.meetings[4].start: is after --as-of'
check no_title 'b["meetings"][0]["title"] = "  "' '$.meetings[0].title: is required'
check long_title 'b["meetings"][0]["title"] = "x" * 301' 'at most 300 allowed'
check notes_status 'b["meetings"][0]["notes_status"] = "maybe"' '$.meetings[0].notes_status: must be one of'
check text_unread 'b["meetings"][2]["transcript_text"] = "x"' 'is given but transcript_status is denied'
check unknown_meeting 'b["decisions"][0]["meeting_id"] = "sample-zzz"' "is not in \$.meetings; every decision must cite a meeting in this bundle"
check dup_ref 'b["decisions"][1]["ref"] = "w1-1"' "duplicate ref 'w1-1'"
check kind 'b["decisions"][0]["kind"] = "idea"' '$.decisions[0].kind: must be one of decision, proposal'
check no_quote 'del b["decisions"][0]["quote"]' '$.decisions[0].quote: is required'
check no_statement 'b["decisions"][0]["statement"] = ""' '$.decisions[0].statement: is required'
check quote_not_in_text 'b["decisions"][0]["quote"] = "We decided twenty percent."' '$.decisions[0].quote: not found in the meeting text (checked against the meeting'"'"'s transcript_text, sample-pc1005' 
check quote_spliced 'b["decisions"][0]["quote"] = "Okay, decided: fifteen percent off for annual, Sam, you own updating the price book."' '$.decisions[0].quote: not found in the meeting text'
check quote_denied 'b["decisions"][2]["quote_source"] = "transcript"' 'was not read (status denied)'
check quote_notes_gone 'b["meetings"][2]["notes_status"] = "unavailable"; del b["meetings"][2]["notes_text"]' "notes of 'sample-gtm1007' were not read"
check topic 'b["decisions"][0]["topic"] = "Pricing/Annual"' 'is not a topic key'
check topic_deep 'b["decisions"][0]["topic"] = "a/b/c/d/e"' 'is not a topic key'
check area 'b["decisions"][0]["area"] = "pricing team"' 'is not an area tag'
check owner_email 'b["decisions"][0]["owner"] = "sam@tessmarrow.example"' 'must be a person'"'"'s name, not an email address'
check by_email 'b["decisions"][0]["decided_by"] = ["Dana <dana@tessmarrow.example>"]' 'not an email address'
check by_null 'b["decisions"][0]["decided_by"] = None' '$.decisions[0].decided_by: must be a list, got null'
check by_many 'b["decisions"][0]["decided_by"] = ["P%d" % i for i in range(11)]' 'at most 10 allowed'
check alt_type 'b["decisions"][0]["alternatives_rejected"] = [3]' 'must be a non-empty string'
check revisit_early 'b["decisions"][1]["revisit_on"] = "2026-10-01"' 'is before the meeting (2026-10-06)'
check prop_owner 'b["decisions"][2]["owner"] = "Leo Fischer"' 'proposals have no owner'
check prop_super 'b["decisions"][2]["supersedes"] = ["w1-1"]' 'a proposal can'"'"'t supersede anything'
check both_links 'b["decisions"][4]["same_as"] = "w1-1"; b["decisions"][4]["supersedes"] = ["w1-1"]' 'not both'
check forward_ref 'b["decisions"][0]["supersedes"] = ["w1-4"]' "is neither a logged id nor the ref of an earlier decision"
check unknown_id 'b["decisions"][3]["supersedes"] = ["dl-0000000000"]' "'dl-0000000000' is not in the decision log"
check same_as_kind 'b["decisions"][4]["same_as"] = "w1-3"' 'is a proposal, this is a decision; a decision that settles a proposal uses supersedes'
check action_unknown 'b["user_actions"] = [{"decision_id": "dl-0000000000", "action": "dismiss", "date": "2026-10-09"}]' "'dl-0000000000' is not in the decision log"
check action_kind 'b["user_actions"] = [{"decision_id": "dl-0000000000", "action": "delete", "date": "2026-10-09"}]' 'must be one of dismiss, restore'
check action_future 'b["user_actions"] = [{"decision_id": "dl-0000000000", "action": "dismiss", "date": "2026-10-10"}]' 'is after --as-of'
check action_extra 'b["user_actions"] = [{"decision_id": "dl-0000000000", "action": "dismiss", "date": "2026-10-09", "owner": "X"}]' 'is not used by action dismiss'
check action_revisit_key 'b["user_actions"] = [{"decision_id": "dl-0000000000", "action": "set_revisit", "date": "2026-10-09"}]' 'is required (a YYYY-MM-DD date, or null'
check action_self 'b["user_actions"] = [{"decision_id": "dl-0000000000", "action": "resolve", "other_id": "dl-0000000000", "resolution": "both_stand", "date": "2026-10-09"}]' 'must differ from decision_id'
check action_id 'b["user_actions"] = [{"decision_id": "DL-1", "action": "dismiss", "date": "2026-10-09"}]' 'is not a logged id'

expect 2 "--as-of: '2026-10-09T16:00:00' has no UTC offset" err "--as-of without offset rejected" --bundle sample_bundle_week1.json --as-of 2026-10-09T16:00:00 --log "$(fresh v)"
expect 2 "--as-of: is required" err "missing --as-of rejected" --bundle sample_bundle_week1.json --log "$(fresh v)"
expect 2 "needs --log" err "missing --log rejected" --bundle sample_bundle_week1.json "${W1[@]}"
expect 2 "nothing to do" err "no command rejected" --log "$(fresh v)"
expect 2 "cannot be combined" err "two commands rejected" --topics --render-log --log "$(fresh v)" "${W1[@]}"
expect 2 "only apply to a --bundle run" err "--scheduled with --search rejected" --search x --scheduled --log "$(fresh v)" "${W1[@]}"
expect 2 "--resume-pending: only applies to --scheduled" err "--resume-pending alone rejected" --bundle sample_bundle_week1.json "${W1[@]}" --log "$(fresh v)" --resume-pending
variant onreq sample_bundle_week1.json 'b["scan"]["cadence"] = "on_request"'
expect 2 "must be weekly for a --scheduled run" err "on_request bundle with --scheduled rejected" --bundle "$TMP/onreq.json" "${W1[@]}" --log "$(fresh v)" --scheduled
expect 2 "a scheduled window ends on the --as-of date" err "scheduled window not ending today rejected" --bundle sample_bundle_week1.json --as-of 2026-10-10T09:00:00-07:00 --log "$(fresh v)" --scheduled
printf '{"plan_history_days": {}, "max_quote_chars": 280, "max_runs_logged": 104, "max_mentions_kept": 20}' >"$TMP/cfg.json"
expect 2 "missing key(s): search_max_results" err "config missing key rejected" --bundle sample_bundle_week1.json "${W1[@]}" --log "$(fresh v)" --config "$TMP/cfg.json"
printf '{"plan_history_days": null, "max_quote_chars": 280, "max_runs_logged": 104, "max_mentions_kept": 20, "search_max_results": 5}' >"$TMP/cfg.json"
expect 2 '$.plan_history_days: required object is missing' err "config null object rejected" --bundle sample_bundle_week1.json "${W1[@]}" --log "$(fresh v)" --config "$TMP/cfg.json"
printf '{"plan_history_days": {"free": 0}, "max_quote_chars": 280, "max_runs_logged": 104, "max_mentions_kept": 20, "search_max_results": 5}' >"$TMP/cfg.json"
expect 2 '$.plan_history_days.free: must be an integer from 1' err "config bad plan limit rejected" --bundle sample_bundle_week1.json "${W1[@]}" --log "$(fresh v)" --config "$TMP/cfg.json"
printf '{"plan_history_days": {}, "max_quote_chars": true, "max_runs_logged": 104, "max_mentions_kept": 20, "search_max_results": 5}' >"$TMP/cfg.json"
expect 2 '$.max_quote_chars: must be an integer from 40' err "config boolean as integer rejected" --bundle sample_bundle_week1.json "${W1[@]}" --log "$(fresh v)" --config "$TMP/cfg.json"
LOGV="$(fresh v)"
run 2 "invalid bundle writes no log" --bundle "$TMP/schema.json" "${W1[@]}" --log "$LOGV"
[[ ! -e "$LOGV" ]] || fail "a failed run created the log"
ok "a failed run writes no log file"

# ---------------------------------------------------------------- quotes: normalisation and checked counts
variant qnorm sample_bundle_week1.json '
b["meetings"][4]["summary_text"] = "Team confirmed annual plans get 15% off; monthly remains list price. Deploys at \\~20 minutes &amp; Sam&apos;s **Fix it** now"
b["decisions"][4]["quote"] = "TEAM confirmed   annual plans get 15% off; monthly remains list price."
b["decisions"][0]["quote"] = "Okay, decided: fifteen percent off for annual, monthly stays at list. Sam, you own updating the price book."
b["decisions"].append(dict(b["decisions"][4], ref="q2", statement="Fix deploys now", topic="eng/deploys", area="engineering", quote="Deploys at ~20 minutes & Sam’s Fix it now"))'
run 0 "quote normalisation" --bundle "$TMP/qnorm.json" "${W1[@]}" --log "$(fresh q)"
has "quotes checked against meeting text in the bundle: 6 of 6" "quotes match across case, whitespace, entities, markdown escapes and curly quotes"
has "Granola AI summary, not verbatim speech" "summary quotes are labelled as not verbatim speech"
variant unchecked sample_bundle_week1.json 'del b["meetings"][0]["transcript_text"]; del b["meetings"][2]["notes_text"]'
run 0 "unchecked quotes" --bundle "$TMP/unchecked.json" "${W1[@]}" --log "$(fresh q)"
has "quotes checked against meeting text in the bundle: 3 of 5" "quotes without meeting text are accepted and counted as unchecked"

# Quotes follow the shared whole-sentence rule (granola_text.check_quote): QA round 4 examples, then QA round 5 X1/X2
QA='b["meetings"][0]["transcript_text"] += "\nMicrophone: Was the onboarding helpful for you?\nSystem audio (Customer): Honestly it was unhelpful for us. We were dissatisfied with the rollout.\nMicrophone: Will you renew? System audio (Customer): We will not renew this year."'
check qa_unhelpful "$QA"'; b["decisions"][0]["quote"] = "helpful for us"' '$.decisions[0].quote: not a complete sentence in the meeting text'
check qa_dissatisfied "$QA"'; b["decisions"][0]["quote"] = "satisfied with the rollout"' '$.decisions[0].quote: not a complete sentence in the meeting text'
check qa_renew "$QA"'; b["decisions"][0]["quote"] = "Will you renew? System audio (Customer): We will"' '$.decisions[0].quote: contains a speaker label'
check qa_renew_nolabel "$QA"'; b["decisions"][0]["quote"] = "Will you renew? We will not renew this year."' '$.decisions[0].quote: spans two speakers'
check qa_two_lines "$QA"'; b["decisions"][0]["quote"] = "Was the onboarding helpful for you? Honestly it was unhelpful for us."' '$.decisions[0].quote: spans two speakers'
check qa_word_end 'b["decisions"][0]["quote"] = "fifteen percent off for ann"' '$.decisions[0].quote: not a complete sentence in the meeting text'
check qa_name_label 'b["decisions"][0]["quote"] = "Sam: Got it, I'"'"'ll have the price book updated"' '$.decisions[0].quote: contains a speaker label'
check qa_name_turn 'b["decisions"][0]["quote"] = "Sam, you own updating the price book. Got it, I'"'"'ll have the price book updated"' '$.decisions[0].quote: spans two speakers'
check qa_ellipsis 'b["decisions"][0]["quote"] = "Okay, decided: fifteen percent off … Sam, you own updating the price book."' '$.decisions[0].quote: contains an ellipsis'
check qa_md_label 'b["meetings"][4]["summary_text"] += "\n**Dana:** We *will* ship [the beta](https://beta.example/x) on Friday\\."; b["decisions"][4]["quote"] = "Dana: We will ship the beta"' '$.decisions[4].quote: contains a speaker label'
check qa_partial_sentence 'b["decisions"][0]["quote"] = "fifteen percent off for annual, monthly stays at list."' '$.decisions[0].quote: not a complete sentence in the meeting text'
check qa_question_added 'b["decisions"][0]["quote"] = "Okay, decided: fifteen percent off for annual, monthly stays at list?"' "\$.decisions[0].quote: changes the sentence's final question mark"
check qa_short 'b["decisions"][0]["quote"] = "Got it"' '$.decisions[0].quote: too short (fewer than 3 words)'
# X1 (QA round 5): a quote can't drop a leading negation or skip words
X1='b["meetings"][0]["transcript_text"] += "\nRiley: Nobody will renew next quarter. None of our drivers trust the dock calendar. I would not pay for this."'
check x1_nobody "$X1"'; b["decisions"][0]["quote"] = "will renew next quarter"' '$.decisions[0].quote: not a complete sentence in the meeting text'
check x1_nobody_dot "$X1"'; b["decisions"][0]["quote"] = "will renew next quarter."' '$.decisions[0].quote: not a complete sentence in the meeting text'
check x1_none_of "$X1"'; b["decisions"][0]["quote"] = "our drivers trust the dock calendar"' '$.decisions[0].quote: not a complete sentence in the meeting text'
check x1_skip_not "$X1"'; b["decisions"][0]["quote"] = "I would ... pay for this"' '$.decisions[0].quote: contains an ellipsis'
check x1_skip_not_u "$X1"'; b["decisions"][0]["quote"] = "I would … pay for this."' '$.decisions[0].quote: contains an ellipsis'
check x1_drop_not "$X1"'; b["decisions"][0]["quote"] = "I would pay for this."' '$.decisions[0].quote: not found in the meeting text'
# X2 (QA round 5): a quote cannot cross a mid-line label. Round 9: with no sentence end before it, the label is part of
# the sentence, so the label-less prefix is a partial sentence
X2='b["meetings"][0]["transcript_text"] += "\nDana: will you renew Riley: no we will not"'
check x2_cross "$X2"'; b["decisions"][0]["quote"] = "will you renew Riley: no"' '$.decisions[0].quote: contains a speaker label'
check x2_cross_nolabel "$X2"'; b["decisions"][0]["quote"] = "will you renew Riley"' '$.decisions[0].quote: not a complete sentence in the meeting text'
# Round 9 (helper 1.3.0): with no sentence end before it, the mid-line name is not a turn boundary (fail-closed), so the answer can't be quoted alone.
check x2_answer_nostop "$X2"'; b["decisions"][0]["quote"] = "no we will not"' '$.decisions[0].quote: not a complete sentence in the meeting text'
# Round 10 (helper 1.6.0, QA round 9 SH9-1/SH9-3): a negation before an unlisted abbreviation ("asst. mgr.", "SVP.",
# "Ambass.") or a mid-line "Plan A:" does not end the turn, so the decision quote after the label exits 2 and nothing is
# logged; twins with a clear sentence end before the label are reported and logged.
SH10C="Okay, decided: we move the carrier contract to annual billing."
for atk in "Not even our asst. mgr. Priya: " "Nobody except the SVP. Priya: " "No one but Ambass. Priya: " "We dropped Plan A: "; do
  check sh10_atk 'b["meetings"][0]["transcript_text"] += "\nDana: '"$atk$SH10C"'"; b["decisions"][0]["quote"] = "'"$SH10C"'"' '$.decisions[0].quote: not a complete sentence in the meeting text'
done
for twin in "Sounds good. Priya: " "Nobody except the SVP agreed. Priya: "; do
  variant sh10_safe sample_bundle_week1.json 'b["meetings"][0]["transcript_text"] += "\nDana: '"$twin$SH10C"'"; b["decisions"][0]["quote"] = "'"$SH10C"'"'
  log="$(fresh sh10log)"
  expect 0 "we move the carrier contract to annual billing." out "SH9 (r10) twin: '$twin' then the label reports the decision quote" --bundle "$TMP/sh10_safe.json" "${W1[@]}" --log "$log"
  grep -qF "move the carrier contract to annual billing" "$log" || fail "SH9 (r10) twin: '$twin': the decision quote is not in the log"
  ok "SH9 (r10) twin: '$twin': the decision is written to the log"
done
# RS-4 (QA round 8): an empty or whitespace-only meeting text is a failed fetch, not "absent": exit 2. Twin: the key
# left out entirely still runs, with that quote counted as not checked.
check rs4_empty 'b["meetings"][0]["transcript_text"] = ""' '$.meetings[0].transcript_text: is empty'
check rs4_ws_notes 'b["meetings"][2]["notes_text"] = " \n  "' '$.meetings[2].notes_text: is empty'
check rs4_ws_summary 'b["meetings"][4]["summary_text"] = "\t"' '$.meetings[4].summary_text: is empty'
variant rs4_absent sample_bundle_week1.json 'del b["meetings"][0]["transcript_text"]'
expect 0 "quotes checked against meeting text in the bundle: 4 of 5." out "RS-4 twin: transcript_text left out runs; the quote is counted as not checked" \
  --bundle "$TMP/rs4_absent.json" "${W1[@]}" --log "$(fresh rs4)"
# Blind-attack regressions (round 6): reversals, label crossing and line joins
check ba_trailing_if 'b["meetings"][0]["transcript_text"] += "\nKim: We will renew, unless pricing changes."; b["decisions"][0]["quote"] = "We will renew"' '$.decisions[0].quote: not a complete sentence in the meeting text'
check ba_q_to_statement 'b["meetings"][0]["transcript_text"] += "\nKim: Will we renew next quarter?"; b["decisions"][0]["quote"] = "Will we renew next quarter."' '$.decisions[0].quote: changes the sentence'"'"'s final question mark'
check ba_middle 'b["meetings"][0]["transcript_text"] += "\nKim: Honestly we are not going to ship the beta on Friday at all."; b["decisions"][0]["quote"] = "going to ship the beta on Friday"' '$.decisions[0].quote: not a complete sentence in the meeting text'
check ba_lazy_line 'b["meetings"][0]["transcript_text"] += "\nKim: Nobody on the team\nWill renew next year."; b["decisions"][0]["quote"] = "Will renew next year."' '$.decisions[0].quote: not a complete sentence in the meeting text'
check ba_homoglyph 'b["meetings"][0]["transcript_text"] += "\nKim: We will ship the beta on Friday."; b["decisions"][0]["quote"] = "We will ship the betа on Friday."' '$.decisions[0].quote: not found in the meeting text'
check ba_bracket_ellipsis 'b["meetings"][0]["transcript_text"] += "\nKim: I would not pay for this."; b["decisions"][0]["quote"] = "I would [...] pay for this."' '$.decisions[0].quote: contains an ellipsis'
check ba_spaced_ellipsis 'b["meetings"][0]["transcript_text"] += "\nKim: I would not pay for this."; b["decisions"][0]["quote"] = "I would . . . pay for this."' '$.decisions[0].quote: contains an ellipsis'
check ba_speaker2 'b["meetings"][0]["transcript_text"] += "\nSpeaker 2: We will ship the beta on Friday."; b["decisions"][0]["quote"] = "Speaker 2: We will ship the beta on Friday."' '$.decisions[0].quote: contains a speaker label'
check ba_nobody_said 'b["meetings"][0]["transcript_text"] += "\nKim: Nobody said: we will renew this year."; b["decisions"][0]["quote"] = "we will renew this year."' '$.decisions[0].quote: not a complete sentence in the meeting text'
check ba_no_dana 'b["meetings"][0]["transcript_text"] += "\nKim: No Dana: we renew this year."; b["decisions"][0]["quote"] = "we renew this year."' '$.decisions[0].quote: not a complete sentence in the meeting text'
check ba_two_lines 'b["meetings"][0]["transcript_text"] += "\nKim: The pilot is paused.\nKim: The budget is frozen."; b["decisions"][0]["quote"] = "The pilot is paused. The budget is frozen."' '$.decisions[0].quote: spans two speakers'
check ba_promote_elsewhere 'b["decisions"].append(dict(b["decisions"][0], ref="x1", statement="Replace the free tier with a 14-day trial", topic="pricing/free-tier", quote="We will replace the free tier with a 14-day trial.", supersedes=["w1-3"], meeting_id="sample-ss1008", quote_source="summary"))' '$.decisions[5].quote: not found in the meeting text'
variant ba_ok sample_bundle_week1.json 'b["meetings"][0]["transcript_text"] += "\nKim: We\u2019ll ship the beta on Friday.\nKim: Decision: we ship the beta on Friday."
b["decisions"].append(dict(b["decisions"][0], ref="x1", topic="qa/x1", statement="QA x1", quote="WE'"'"'LL SHIP THE BETA ON FRIDAY"))
b["decisions"].append(dict(b["decisions"][0], ref="x2", topic="qa/x2", statement="QA x2", quote="Decision: we ship the beta on Friday."))
b["decisions"][1]["statement"] = "Go to javascript:alert(1) $x$ ==hi== :smile: <!channel> [a](https://evil.example)"'
run 0 "blind-attack positives" --bundle "$TMP/ba_ok.json" "${W1[@]}" --log "$(fresh q)"
has "quotes checked against meeting text in the bundle: 7 of 7" "case and curly apostrophes ignored; a Decision: heading is not a speaker label"
hasnt "javascript:" "markdown escapes the colon, so javascript: and :emoji: can't form"
hasnt '$x$' "markdown escapes \$ math"
hasnt "](https://evil" "markdown link syntax in a statement is inert"
variant qa_ok sample_bundle_week1.json "$QA"'
b["meetings"][0]["transcript_text"] += "\nRiley: Nobody will renew next quarter. None of our drivers trust the dock calendar. I would not pay for this.\nDana: will you renew? Riley: no we will not"
b["meetings"][4]["summary_text"] += "\n**Dana:** We *will* ship [the beta](https://beta.example/x) on Friday\\.\nLaunch \\- phase 2 &amp; 3 \\[draft\\]"
def add(i, ref, quote):
    b["decisions"].append(dict(b["decisions"][i], ref=ref, quote=quote, topic="qa/" + ref, statement="QA " + ref))
add(0, "qa1", "We will not renew this year.")
add(0, "qa2", "Honestly it was unhelpful for us.")
add(0, "qa3", "Got it, I'"'"'ll have the price book updated before the renewal batch.")
add(4, "qa4", "We will ship the beta on Friday.")
add(4, "qa5", "we **will** ship [the beta](https://other.example) on _Friday_")
add(4, "qa6", "Launch - phase 2 & 3 [draft]")
add(0, "qa7", "Nobody will renew next quarter. None of our drivers trust the dock calendar.")
add(0, "qa8", "I would not pay for this!")
add(0, "qa9", "no we will not")'
run 0 "whole-sentence quotes pass" --bundle "$TMP/qa_ok.json" "${W1[@]}" --log "$(fresh q)"
has "quotes checked against meeting text in the bundle: 14 of 14" "whole sentences within one turn pass (with the negation, consecutive sentences, a swapped final ! and a mid-line turn after a sentence end; round 9: without the \"?\" the label is not a boundary); emphasis, link syntax and Granola escapes are ignored on both sides"

python3 - <<'PY' || fail "granola_text.py copy"
import hashlib, os
mine = hashlib.sha256(open("granola_text.py", "rb").read()).hexdigest()
assert mine == "00ec63cdf7126c247832276f77f55f2a60fb8b58b9a9dc6fd4fecbe2317df168", "granola_text.py was edited: " + mine
shared = os.path.join("..", "shared", "granola_text.py")
if os.path.isfile(shared):
    assert hashlib.sha256(open(shared, "rb").read()).hexdigest() == mine, "differs from ../shared/granola_text.py"
PY
ok "granola_text.py is the unedited shared copy (SHA-256)"
mkdir -p "$TMP/elsewhere"
(cd "$TMP/elsewhere" && env -u PYTHONDONTWRITEBYTECODE python3 "$OLDPWD/build_decision_log.py" --bundle "$OLDPWD/sample_bundle_week1.json" "${W1[@]}" --log "$TMP/elsewhere/l.json" >"$TMP/out" 2>"$TMP/err") || { cat "$TMP/err"; fail "run from another directory"; }
has "quotes checked against meeting text in the bundle: 5 of 5" "the script finds granola_text.py next to itself from any working directory"
[[ ! -e __pycache__ && ! -e "$TMP/elsewhere/__pycache__" ]] || fail "__pycache__ written"
ok "importing granola_text writes no __pycache__, even without PYTHONDONTWRITEBYTECODE"

# ---------------------------------------------------------------- merge: ids, re-extraction, repeats
LOG="$(fresh m1)"
run 0 "on-request week 1" --bundle sample_bundle_week1.json "${W1[@]}" --log "$LOG"
has "**3 decisions in this window · 1 without an owner · 1 open proposal**" "on-request report counts decisions in the window"
has "· new to the log" "on-request run marks entries new to the log"
has "- Extracted: 4 decisions and 1 proposal · 4 new to the log · 1 repeat linked · 0 already logged" "repeat by topic and wording is linked, not logged twice"
has "Repeated: Thu Oct 8 in [Sales standup](https://notes.granola.ai/d/sample-ss1008)" "repeat shows its meeting"
python3 - "$LOG" <<'PY' || fail "log ids"
import json, sys
log = json.load(open(sys.argv[1], encoding="utf-8"))
assert sorted(log["records"]) == ["dl-20a4d4a6da", "dl-5fafbb740f", "dl-648475149f", "dl-81a703c67c"], sorted(log["records"])
assert log["runs"] == [] and all(r["reported_at"] is None for r in log["records"].values())
assert not any("transcript_text" in json.dumps(r) or "board pack is due on day seven" in json.dumps(r) for r in log["records"].values()), "meeting text stored"
PY
ok "ids are stable and documented; on-request runs record no run and mark nothing reported; meeting text not stored"
cp "$LOG" "$TMP/m1_before.json"
run 0 "rerun same bundle" --bundle sample_bundle_week1.json "${W1[@]}" --log "$LOG"
has "0 new to the log · 0 repeats linked · 5 already logged" "rerun of the same bundle changes nothing"
python3 - "$LOG" "$TMP/m1_before.json" <<'PY' || fail "idempotent rerun"
import json, sys
a, b = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:])
assert a["records"] == b["records"]
PY
ok "rerun leaves every log entry byte-identical"

variant reword sample_bundle_week1.json '
b["decisions"][0]["topic"] = "pricing/annual-plans"
b["decisions"][0]["statement"] = "Annual: 15% off"
b["decisions"][1]["supersedes"] = []
b["decisions"][4]["kind"] = "proposal"; del b["decisions"][4]["owner"]
b["decisions"][3]["supersedes"] = ["dl-81a703c67c"]'
run 0 "re-extraction with other topic, kind and links" --bundle "$TMP/reword.json" "${W1[@]}" --log "$LOG"
has "5 already logged" "re-extraction by meeting and quote matches despite a different topic"
has "1 entry re-extracted with a different kind (decision vs proposal) kept as logged" "a changed kind is not applied to a logged entry"
has "1 entry re-extracted with same_as or supersedes kept as logged" "new links on a re-extraction are reported, not applied"
python3 - "$LOG" "$TMP/m1_before.json" <<'PY' || fail "re-extraction kept"
import json, sys
a, b = (json.load(open(p, encoding="utf-8")) for p in sys.argv[1:])
assert a["records"] == b["records"]
PY
ok "re-extraction never changes the logged entry"

variant twoquote sample_bundle_week1.json '
b["decisions"].append(dict(b["decisions"][0], ref="w1-1b", statement="Sam owns the price book", topic="pricing/price-book-owner"))'
LOG2="$(fresh m2)"
run 0 "two decisions from one quote" --bundle "$TMP/twoquote.json" "${W1[@]}" --log "$LOG2"
has "5 new to the log" "two decisions from one quote are two entries"
run 0 "two decisions from one quote, rerun" --bundle "$TMP/twoquote.json" "${W1[@]}" --log "$LOG2"
has "0 new to the log · 0 repeats linked · 6 already logged" "rerun keeps two entries from one quote"
variant twoquote_b sample_bundle_week1.json '
b["decisions"].append(dict(b["decisions"][0], ref="w1-1c", statement="Price book is due before renewals", topic="pricing/price-book-timing"))'
run 0 "third decision from the same quote later" --bundle "$TMP/twoquote_b.json" "${W1[@]}" --log "$LOG2"
has "1 new to the log" "a further decision from an already-used quote is still logged"

variant sameas sample_bundle_week1.json '
b["decisions"][4]["statement"] = "15% annual discount confirmed"; b["decisions"][4]["same_as"] = "w1-1"'
run 0 "same_as by ref" --bundle "$TMP/sameas.json" "${W1[@]}" --log "$(fresh m3)"
has "1 repeat linked" "same_as with a bundle ref links a reworded repeat"
variant reword_nolink sample_bundle_week1.json 'b["decisions"][4]["statement"] = "15% annual discount confirmed"'
run 0 "reworded repeat without same_as" --bundle "$TMP/reword_nolink.json" "${W1[@]}" --log "$(fresh m3)"
has "Possible conflicts (1): same topic, not linked" "a reworded repeat without same_as is flagged as a possible conflict"
variant dupnote sample_bundle_week1.json '
m = dict(b["meetings"][0], meeting_id="sample-pc1005-copy", url="https://notes.granola.ai/d/sample-pc1005-copy"); b["meetings"].append(m)
b["decisions"].append(dict(b["decisions"][0], ref="dup", meeting_id="sample-pc1005-copy"))'
run 0 "duplicate notes" --bundle "$TMP/dupnote.json" "${W1[@]}" --log "$(fresh m3)"
has "Duplicate notes: 1 note with the same title and start as another" "duplicate captures are reported"
has "2 repeats linked" "a decision from both captures is linked as a repeat"

# ---------------------------------------------------------------- week 2: reversal, conflict, flags, replies
LOG="$(week1_log f1)"
run 0 "week 2 scheduled" --bundle sample_bundle_week2.json "${W2[@]}" --log "$LOG" --scheduled
has "**2 new decisions · 1 reversal · 1 possible conflict · 1 without an owner · 1 revisit date passed · 2 open proposals**" "week 2 counts"
has "⚠️ Reverses \`dl-5fafbb740f\` “Annual plans get 15% off list price; monthly plans stay at list price” (decided Mon Oct 5 in [Pricing council](https://notes.granola.ai/d/sample-pc1005))" "reversal cites the earlier decision and its meeting"
has "Reply “dl-5a9f6d4a66 supersedes dl-81a703c67c” or “both stand”." "conflict says how to resolve it"
has "\`dl-648475149f\` “Close the books on business day 5 instead of day 8, starting with October” (decided Thu Oct 8 in [Month-end close review](https://notes.granola.ai/d/sample-fin1008)) · revisit was due Thu Oct 15." "passed revisit date flagged"
has "Replies: 1 applied." "reply applied"
hasnt "Reply “owner of dl-81a703c67c" "set_owner clears the no-owner flag"
has "2. **Offer agencies a 20% referral fee for their first year** · partnerships · \`dl-4eeeaecc81\` · new" "new proposal marked new"
hasnt "1. **Replace the free tier with a 14-day trial** · pricing · \`dl-20a4d4a6da\` · new" "reported proposal not marked new again"
hasnt "**Annual plans get 15% off list price; monthly plans stay at list price** · pricing" "the week-1 decision already reported is not re-announced"

reply() {  # reply NAME LOG ASOF ACTIONS_JSON -> on-request run with an empty window and these replies
  python3 - "$TMP/$1.json" "$4" "$3" <<'PY'
import json, sys
day = sys.argv[3][:10]
b = {"schema_version": 1, "scan": {"from": day, "to": day, "cadence": "on_request"},
     "sources": {"meetings": {"provider": "granola", "status": "ok", "plan": "business"}},
     "meetings": [], "decisions": [], "user_actions": json.loads(sys.argv[2])}
json.dump(b, open(sys.argv[1], "w", encoding="utf-8"))
PY
}
A17=(--as-of 2026-10-17T09:00:00-07:00)
reply r_both "$LOG" 2026-10-17T09:00:00-07:00 '[{"decision_id": "dl-81a703c67c", "action": "resolve", "other_id": "dl-5a9f6d4a66", "resolution": "both_stand", "date": "2026-10-17"}]'
expect 2 "dl-81a703c67c has no flagged conflict with dl-5a9f6d4a66 (the conflict is listed on the newer entry)" err "both_stand on the wrong entry rejected" --bundle "$TMP/r_both.json" "${A17[@]}" --log "$LOG"
reply r_old "$LOG" 2026-10-17T09:00:00-07:00 '[{"decision_id": "dl-81a703c67c", "action": "resolve", "other_id": "dl-5a9f6d4a66", "resolution": "supersedes", "date": "2026-10-17"}]'
expect 2 "is newer than dl-81a703c67c" err "an older decision can't supersede a newer one" --bundle "$TMP/r_old.json" "${A17[@]}" --log "$LOG"
reply r_sup "$LOG" 2026-10-17T09:00:00-07:00 '[{"decision_id": "dl-5a9f6d4a66", "action": "resolve", "other_id": "dl-81a703c67c", "resolution": "supersedes", "date": "2026-10-17"}, {"decision_id": "dl-5a9f6d4a66", "action": "set_owner", "owner": "Priya Raman", "date": "2026-10-17"}, {"decision_id": "dl-648475149f", "action": "set_revisit", "revisit_on": "2026-11-16", "date": "2026-10-17", "note": "after the October close"}]'
run 0 "resolve supersedes + owner + revisit" --bundle "$TMP/r_sup.json" "${A17[@]}" --log "$LOG"
has "**0 decisions in this window · 2 open proposals**" "resolved conflict, owner and revisit clear every flag"
run 0 "replies again" --bundle "$TMP/r_sup.json" "${A17[@]}" --log "$LOG"
has "Replies: 0 applied, 3 already applied earlier." "the same replies again change nothing"
run 0 "render after resolve" --render-log --log "$LOG" "${A17[@]}"
has "Superseded by \`dl-5a9f6d4a66\` “Named CSM threshold moves to \\\$75k ARR”" "resolve supersedes shows the chain"
has "owner: Priya Raman (set by you Sat Oct 17)" "owner set by reply is labelled"
has "Revisit: Mon Nov 16 (set by you Sat Oct 17)" "revisit set by reply is labelled"

reply r_dis "$LOG" 2026-10-17T09:00:00-07:00 '[{"decision_id": "dl-4d218be690", "action": "dismiss", "date": "2026-10-17", "note": "That was a draft, not a decision"}]'
run 0 "dismiss the reversal" --bundle "$TMP/r_dis.json" "${A17[@]}" --log "$LOG"
run 0 "render after dismiss" --render-log --log "$LOG" "${A17[@]}"
has "## Dismissed (1)" "dismissed entries listed in the full log"
has "\`dl-4d218be690\` “Annual discount drops to 10%; 15% only with a 3-year prepay” · dismissed Sat Oct 17: That was a draft, not a decision" "dismissal note shown"
python3 - "$TMP/out" <<'PY' || fail "dismiss revives"
text = open(__import__("sys").argv[1], encoding="utf-8").read()
active = text.split("## Active decisions", 1)[1].split("## Open proposals", 1)[0]
assert "dl-5fafbb740f" in active and "dl-4d218be690" not in active
PY
ok "dismissing a reversal makes the earlier decision live again"
run 0 "search hides dismissed" --search "annual discount" --log "$LOG" "${A17[@]}"
hasnt "dl-4d218be690" "search leaves dismissed entries out"
run 0 "search by dismissed id" --search "dl-4d218be690" --log "$LOG" "${A17[@]}"
has "dl-4d218be690" "search by id finds a dismissed entry"
reply r_res "$LOG" 2026-10-17T09:00:00-07:00 '[{"decision_id": "dl-4d218be690", "action": "restore", "date": "2026-10-17", "note": "it was real"}]'
run 0 "restore" --bundle "$TMP/r_res.json" "${A17[@]}" --log "$LOG"
run 0 "render after restore" --render-log --log "$LOG" "${A17[@]}"
hasnt "## Dismissed" "restore brings the entry back"

# L1: dismiss and restore re-run the conflict check, so two contradictory live decisions always carry a flag
LOG_MAIN="$LOG"
LOG="$(week1_log c1)"
variant c1_w2 sample_bundle_week2.json 'b["user_actions"].append({"decision_id": "dl-81a703c67c", "action": "dismiss", "date": "2026-10-12"})'
run 0 "week 2 with the old CSM threshold dismissed" --bundle "$TMP/c1_w2.json" "${W2[@]}" --log "$LOG"
reply c1_res "$LOG" 2026-10-17T09:00:00-07:00 '[{"decision_id": "dl-81a703c67c", "action": "restore", "date": "2026-10-17"}]'
run 0 "restore the old CSM threshold" --bundle "$TMP/c1_res.json" "${A17[@]}" --log "$LOG" --json
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["flags"]["conflicts"]==[{"id":"dl-5a9f6d4a66","with":"dl-81a703c67c"}], d["flags"]' "$TMP/out" || fail "restore conflict"
ok "restoring an entry flags its conflict with a live decision on the same topic"
run 0 "search after restore" --search "named CSM threshold" --log "$LOG" "${A17[@]}"
has "⚠️ Possible conflict with \`dl-81a703c67c\`" "search never shows two contradictory live decisions without the flag (restore)"
LOG="$(week1_log c2)"
variant c2_w2 sample_bundle_week2.json 'b["decisions"].append({"ref": "w2-4", "kind": "decision", "meeting_id": "sample-pt1015", "statement": "Annual discount goes back to 12%", "topic": "pricing/annual-discount", "area": "pricing", "decided_by": ["Dana Ortiz"], "owner": "Sam Kim", "quote": "Interesting, bring numbers next time.", "quote_source": "transcript", "supersedes": ["w2-1"]})'
run 0 "week 2 with a second reversal" --bundle "$TMP/c2_w2.json" "${W2[@]}" --log "$LOG"
reply c2_dis "$LOG" 2026-10-17T09:00:00-07:00 '[{"decision_id": "dl-4d218be690", "action": "dismiss", "date": "2026-10-17"}]'
run 0 "dismiss the middle of a reversal chain" --bundle "$TMP/c2_dis.json" "${A17[@]}" --log "$LOG" --json
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); c=[x for x in d["flags"]["conflicts"] if x["with"]=="dl-5fafbb740f"]; assert len(c)==1 and c[0]["id"]!="dl-4d218be690", d["flags"]' "$TMP/out" || fail "dismiss conflict"
ok "dismissing the middle of a reversal chain flags the two live ends as a possible conflict"
run 0 "search after dismiss" --search "annual discount" --log "$LOG" "${A17[@]}"
has "⚠️ Possible conflict with \`dl-5fafbb740f\`" "search never shows two contradictory live decisions without the flag (dismiss)"
LOG="$(week1_log c3)"
run 0 "week 2" --bundle sample_bundle_week2.json "${W2[@]}" --log "$LOG"
reply c3_dr "$LOG" 2026-10-17T09:00:00-07:00 '[{"decision_id": "dl-5a9f6d4a66", "action": "dismiss", "date": "2026-10-17"}, {"decision_id": "dl-5a9f6d4a66", "action": "restore", "date": "2026-10-17", "note": "real"}]'
run 0 "dismiss and restore a flagged entry" --bundle "$TMP/c3_dr.json" "${A17[@]}" --log "$LOG" --json
python3 - "$LOG" "$TMP/out" <<'PY' || fail "conflict kept once"
import json, sys
log, out = json.load(open(sys.argv[1])), json.load(open(sys.argv[2]))
assert out["flags"]["conflicts"] == [{"id": "dl-5a9f6d4a66", "with": "dl-81a703c67c"}], out["flags"]
assert sum(len(r["conflicts"]) for r in log["records"].values()) == 1
PY
ok "dismiss then restore keeps exactly one flag for a flagged pair"
LOG="$LOG_MAIN"

reply r_conf "$LOG" 2026-10-17T09:00:00-07:00 '[{"decision_id": "dl-20a4d4a6da", "action": "confirm", "date": "2026-10-17", "note": "Dana approved it by email"}]'
run 0 "confirm a proposal" --bundle "$TMP/r_conf.json" "${A17[@]}" --log "$LOG"
has "**1 decision in this window · 1 without an owner · 1 open proposal**" "a confirmed proposal becomes a decision dated the confirmation"
has "Confirmed as decided by you on Sat Oct 17: Dana approved it by email." "confirmation is labelled as yours"
run 0 "the same confirm again" --bundle "$TMP/r_conf.json" "${A17[@]}" --log "$LOG"
has "Replies: 0 applied, 1 already applied earlier." "the same confirm reply again changes nothing"
reply r_conf2 "$LOG" 2026-10-17T09:00:00-07:00 '[{"decision_id": "dl-20a4d4a6da", "action": "confirm", "date": "2026-10-17", "note": "again"}]'
expect 2 "dl-20a4d4a6da is active, not an open proposal" err "confirming a confirmed proposal again rejected" --bundle "$TMP/r_conf2.json" "${A17[@]}" --log "$LOG"
reply r_owner_prop "$LOG" 2026-10-17T09:00:00-07:00 '[{"decision_id": "dl-4eeeaecc81", "action": "set_owner", "owner": "Leo Fischer", "date": "2026-10-17"}]'
expect 2 "set_owner applies to decisions; dl-4eeeaecc81 is an unconfirmed proposal" err "owner on an unconfirmed proposal rejected" --bundle "$TMP/r_owner_prop.json" "${A17[@]}" --log "$LOG"
reply r_early "$LOG" 2026-10-17T09:00:00-07:00 '[{"decision_id": "dl-4eeeaecc81", "action": "dismiss", "date": "2026-10-14"}]'
expect 2 "is before the entry was made (2026-10-15)" err "reply dated before the entry rejected" --bundle "$TMP/r_early.json" "${A17[@]}" --log "$LOG"

# proposals settled by a later decision; supersede rules
variant settle sample_bundle_week2.json '
b["decisions"].append({"ref": "w2-4", "kind": "decision", "meeting_id": "sample-pt1015", "statement": "Agencies get a 20% referral fee in year one",
  "topic": "partners/referral-fee", "area": "partnerships", "decided_by": ["Dana Ortiz"], "owner": "Leo Fischer",
  "quote": "Interesting, bring numbers next time.", "quote_source": "transcript", "supersedes": ["w2-3", "dl-20a4d4a6da"]})'
LOG="$(week1_log f2)"
run 0 "decision settles proposals" --bundle "$TMP/settle.json" "${W2[@]}" --log "$LOG"
has "Settles proposal \`dl-20a4d4a6da\` “Replace the free tier with a 14-day trial” (proposed Wed Oct 7 in [GTM sync](https://notes.granola.ai/d/sample-gtm1007))" "superseding a proposal settles it"
has "Open proposals (0): discussed, not decided" "settled proposals are no longer open"
variant super_twice sample_bundle_week2.json 'b["decisions"][1]["supersedes"] = ["dl-5fafbb740f"]'
LOG="$(week1_log f3)"
expect 2 "dl-5fafbb740f is superseded; only a live decision or open proposal can be superseded" err "superseding an already-superseded decision rejected" --bundle "$TMP/super_twice.json" "${W2[@]}" --log "$LOG"
variant super_dismissed sample_bundle_week2.json 'b["user_actions"].append({"decision_id": "dl-5fafbb740f", "action": "dismiss", "date": "2026-10-12"})'
expect 2 "dl-5fafbb740f is dismissed" err "superseding a dismissed decision rejected" --bundle "$TMP/super_dismissed.json" "${W2[@]}" --log "$LOG"
variant sameas_dismissed sample_bundle_week2.json 'b["user_actions"].append({"decision_id": "dl-648475149f", "action": "dismiss", "date": "2026-10-12"}); b["decisions"][1]["same_as"] = "dl-648475149f"'
expect 2 "dl-648475149f was dismissed; restore it first" err "repeat of a dismissed entry rejected" --bundle "$TMP/sameas_dismissed.json" "${W2[@]}" --log "$LOG"
python3 - "$LOG" "$TMP/cycle.json" <<'PY'
import json, sys
log = json.load(open(sys.argv[1], encoding="utf-8"))
log["records"]["dl-5fafbb740f"]["supersedes"] = ["dl-648475149f"]
log["records"]["dl-648475149f"]["supersedes"] = ["dl-5fafbb740f"]
json.dump(log, open(sys.argv[2], "w", encoding="utf-8"))
PY
expect 2 "forms a cycle; refusing to run blind" err "a supersedes cycle in the log is rejected" --render-log --log "$TMP/cycle.json" "${W2[@]}"

# ---------------------------------------------------------------- search
LOG="$(week1_log s1)"
python3 "$SCRIPT" --bundle sample_bundle_week2.json "${W2[@]}" --log "$LOG" >/dev/null 2>&1
run 0 "search question" --search "Why did we decide on the named CSM threshold?" --log "$LOG" "${W2[@]}"
has "2 matches in a log of" "search ignores question words"
has "1. **Named CSM threshold moves to \\\$75k ARR**" "search lists the newest equal match first"
run 0 "search chain" --search "annual discount" --log "$LOG" "${W2[@]}"
has "Superseded by \`dl-4d218be690\`" "search shows the reversal chain"
run 0 "search none" --search "office lease renewal" --log "$LOG" "${W2[@]}"
has "No logged decision or proposal matches. Logged entries date from Mon Oct 5, 2026 to Thu Oct 15, 2026." "no match says which dates the logged entries span"
expect 2 "has no searchable words" err "a question with only question words rejected" --search "when did we decide?" --log "$LOG" "${W2[@]}"
run 0 "search json" --search "free tier" --log "$LOG" "${W2[@]}" --json
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["matches"][0]["id"]=="dl-20a4d4a6da" and d["matches"][0]["meeting_url"].startswith("https://notes.granola.ai/")' "$TMP/out" || fail "search json"
ok "search --json returns matches with meeting links"
expect 0 "No decision log at" out "search on a missing log says so" --search "x" --log "$(fresh none)" "${W2[@]}"
expect 0 "No decision log at" out "topics on a missing log says so" --topics --log "$(fresh none)"
expect 0 "No decision log at" out "render on a missing log says so" --render-log --log "$(fresh none)" "${W2[@]}"
run 0 "topics" --topics --log "$LOG"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); ids=[x["id"] for x in d]; assert "dl-5fafbb740f" not in ids and "dl-4d218be690" in ids and all(set(x)=={"id","kind","status","topic","area","statement","date","meeting_title"} for x in d)' "$TMP/out" || fail "topics"
ok "--topics lists live decisions and open proposals only, with the documented fields"

# ---------------------------------------------------------------- weekly two-phase flow
LOG="$(fresh p1)"
expect 0 '"from": "2026-10-05"' out "next-window on an empty log starts this week's Monday" --next-window "${W1[@]}" --log "$LOG"
[[ ! -e "$LOG" ]] || fail "--next-window created the log"
ok "--next-window writes nothing"
run 0 "stage week 1" --bundle sample_bundle_week1.json "${W1[@]}" --log "$LOG" --scheduled
grep -qF "pending: post the report above, then run --confirm-posted weekly:2026-10-05..2026-10-09" "$TMP/err" || fail "no pending note"
ok "a scheduled run stages the week and says how to confirm"
cp "$TMP/out" "$TMP/staged.txt"
expect 4 "pending: a report for weekly:2026-10-05..2026-10-09 was produced" err "rerun of a pending week stops with exit 4" --bundle sample_bundle_week1.json --as-of 2026-10-09T16:05:00-07:00 --log "$LOG" --scheduled
run 0 "resume" --bundle sample_bundle_week1.json --as-of 2026-10-09T16:05:00-07:00 --log "$LOG" --scheduled --resume-pending
python3 - "$TMP/out" "$TMP/staged.txt" <<'PY' || fail "resumed report differs"
import sys
a, b = (open(p, encoding="utf-8").read().replace("16:05", "16:00") for p in sys.argv[1:])
assert a.split("### Coverage")[0] == b.split("### Coverage")[0]
assert "0 new to the log · 0 repeats linked · 5 already logged" in a
PY
ok "--resume-pending prints the same decisions and flags again (all already logged)"
expect 2 "no pending report for 'weekly:2026-10-05..2026-10-08'" err "confirming an unknown week rejected" --confirm-posted weekly:2026-10-05..2026-10-08 --log "$LOG"
expect 0 "Confirmed posted: weekly:2026-10-05..2026-10-09 (4 entries marked reported)." out "confirm marks the staged entries reported" --confirm-posted weekly:2026-10-05..2026-10-09 --log "$LOG" --as-of 2026-10-09T16:06:00-07:00
expect 0 "Already confirmed: weekly:2026-10-05..2026-10-09." out "confirming twice is harmless" --confirm-posted weekly:2026-10-05..2026-10-09 --log "$LOG"
expect 0 "Skipped: already posted weekly:2026-10-05..2026-10-09" out "the same week again is skipped" --bundle sample_bundle_week1.json --as-of 2026-10-09T16:30:00-07:00 --log "$LOG" --scheduled
expect 0 '"already_posted": {' out "next-window later that week reports already_posted" --next-window --as-of 2026-10-11T20:00:00-07:00 --log "$LOG"
variant sat sample_bundle_week1.json 'b["scan"] = {"from": "2026-10-09", "to": "2026-10-10", "cadence": "weekly"}; b["meetings"] = []; b["decisions"] = []; b["user_actions"] = [{"decision_id": "dl-81a703c67c", "action": "set_owner", "owner": "Priya Raman", "date": "2026-10-10"}]'
expect 0 "the weekly report for the week of Mon Oct 5; not posting weekly:2026-10-09..2026-10-10 as well. 1 reply in this bundle (user_actions) was not applied" out "a second window in a posted week is skipped and says replies were not applied" --bundle "$TMP/sat.json" --as-of 2026-10-10T09:00:00-07:00 --log "$LOG" --scheduled
expect 0 '"from": "2026-10-09"' out "next week's window starts where the posted one ended" --next-window "${W2[@]}" --log "$LOG"
expect 2 "--confirm-posted: log" err "confirm on a missing log rejected" --confirm-posted weekly:2026-10-05..2026-10-09 --log "$(fresh none)"
# A report run before 14:00 UTC (Fri 4pm in UTC+09:00 is 07:00 UTC) can miss a meeting dated that day in UTC-14:00,
# so the next window starts the day before its last day (SKILL.md Step 2).
LOGE="$(fresh east)"
run 0 "stage a week at 4pm UTC+09:00" --bundle sample_bundle_week1.json --as-of 2026-10-09T16:00:00+09:00 --log "$LOGE" --scheduled
run 0 "confirm it" --confirm-posted weekly:2026-10-05..2026-10-09 --log "$LOGE"
expect 0 '"from": "2026-10-08"' out "next-window steps back a day after a report run before 14:00 UTC" --next-window "${W2[@]}" --log "$LOGE"

# gap, superseded and incomplete weeks
variant late sample_bundle_week2.json 'b["scan"]["from"] = "2026-10-12"'
run 0 "window with a gap" --bundle "$TMP/late.json" "${W2[@]}" --log "$LOG" --scheduled
has "⚠️ Gap: Fri Oct 9 to Sun Oct 11, 2026 were not scanned by any posted report." "a window starting after the last posted one shows a Gap"
LOG="$(fresh p2)"
python3 "$SCRIPT" --bundle sample_bundle_week1.json "${W1[@]}" --log "$LOG" --scheduled >/dev/null 2>&1
variant wk2_from5 sample_bundle_week2.json 'b["scan"]["from"] = "2026-10-05"'
run 0 "next week while week 1 is still pending" --bundle "$TMP/wk2_from5.json" "${W2[@]}" --log "$LOG" --scheduled
has "Earlier reports never confirmed as posted: \`weekly:2026-10-05..2026-10-09\`. Their entries are included above." "an unconfirmed week is superseded and named"
has "**5 new decisions ·" "entries of the unconfirmed week are announced again"
expect 2 "was superseded by a later report" err "confirming a superseded week rejected" --confirm-posted weekly:2026-10-05..2026-10-09 --log "$LOG"
expect 2 "was superseded by a later report that repeated its entries, so it is not staged again" err "rerunning a superseded week rejected" --bundle sample_bundle_week1.json "${W1[@]}" --log "$LOG" --scheduled
variant wk2_rival sample_bundle_week2.json 'b["scan"]["from"] = "2026-10-06"'
expect 4 "pending: weekly:2026-10-05..2026-10-16 is still pending for the same week" err "a second window in a pending week stops with exit 4" --bundle "$TMP/wk2_rival.json" "${W2[@]}" --log "$LOG" --scheduled

LOG="$(fresh p3)"
variant down sample_bundle_week1.json 'b["sources"]["meetings"] = {"provider": "granola", "status": "unavailable", "detail": "HTTP 503"}; b["meetings"] = []; b["decisions"] = []'
expect 5 "Incomplete: the meeting list could not be read (Granola unavailable: HTTP 503)" out "Granola unavailable: Incomplete, exit 5" --bundle "$TMP/down.json" "${W1[@]}" --log "$LOG" --scheduled
has "Nothing from this window was added to the log and the week was not recorded" "incomplete report says nothing was recorded"
hasnt "nothing to report" "incomplete coverage never says nothing to report"
hasnt "Period key" "incomplete report has no period key to confirm"
variant down_dec sample_bundle_week1.json 'b["sources"]["meetings"]["status"] = "denied"'
expect 5 "Incomplete:" out "a denied meeting list with decisions merges nothing" --bundle "$TMP/down_dec.json" "${W1[@]}" --log "$LOG" --scheduled
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["records"]=={} and [r["status"] for r in d["runs"]]==["incomplete"]' "$LOG" || fail "incomplete merged"
ok "an incomplete run merges nothing and records only the attempt (replaced on rerun)"
expect 0 '"from": "2026-10-05"' out "next-window after an incomplete first run starts at that attempt" --next-window "${W2[@]}" --log "$LOG"

# L2: an Incomplete notice is posted once per week: --confirm-posted records it, --next-window flags it, reruns skip it
LOG="$(fresh p3b)"
expect 5 "Notice key: \`weekly:2026-10-05..2026-10-09\` · after posting this notice, confirm it" out "a scheduled Incomplete report names the key to record its notice" --bundle "$TMP/down.json" "${W1[@]}" --log "$LOG" --scheduled
expect 0 '"incomplete_notice_posted": null' out "--next-window before the notice is recorded" --next-window --as-of 2026-10-09T16:30:00-07:00 --log "$LOG"
expect 0 "Recorded: the Incomplete notice for weekly:2026-10-05..2026-10-09 was posted" out "confirming an Incomplete week records its notice" --confirm-posted weekly:2026-10-05..2026-10-09 --as-of 2026-10-09T16:01:00-07:00 --log "$LOG"
expect 0 "Already recorded: the Incomplete notice for weekly:2026-10-05..2026-10-09" out "recording the notice twice changes nothing" --confirm-posted weekly:2026-10-05..2026-10-09 --log "$LOG"
run 0 "next window after a posted notice" --next-window --as-of 2026-10-09T17:00:00-07:00 --log "$LOG"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["from"]=="2026-10-05" and d["already_posted"] is None and d["incomplete_notice_posted"]["period_key"]=="weekly:2026-10-05..2026-10-09", d' "$TMP/out" || fail "next-window notice"
ok "--next-window flags the posted Incomplete notice and still starts at the uncovered week"
expect 5 "Skipped: the Incomplete notice for the week of Mon Oct 5 was already posted (weekly:2026-10-05..2026-10-09" out "a second Incomplete run that week is not posted again" --bundle "$TMP/down.json" --as-of 2026-10-09T17:00:00-07:00 --log "$LOG" --scheduled
hasnt "## Decision log" "the skipped notice prints no report"
variant down_sat sample_bundle_week1.json 'b["scan"]["from"] = "2026-10-06"; b["sources"]["meetings"] = {"provider": "granola", "status": "denied"}; b["meetings"] = []; b["decisions"] = []'
expect 5 "Skipped: the Incomplete notice for the week of Mon Oct 5 was already posted" out "another window in the same week is not posted again either" --bundle "$TMP/down_sat.json" --as-of 2026-10-09T17:30:00-07:00 --log "$LOG" --scheduled
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert d["records"]=={} and [r.get("notice_posted", False) for r in d["runs"]].count(True)==1' "$LOG" || fail "notice kept"
ok "the posted notice survives later Incomplete attempts in the week"
run 0 "a complete run later that week still reports" --bundle sample_bundle_week1.json --as-of 2026-10-09T18:00:00-07:00 --log "$LOG" --scheduled
has "## Decision log" "a complete report is not blocked by an earlier Incomplete notice"
LOG="$(fresh p3c)"
python3 "$SCRIPT" --bundle "$TMP/down.json" "${W1[@]}" --log "$LOG" --scheduled >/dev/null 2>&1 || true
python3 "$SCRIPT" --confirm-posted weekly:2026-10-05..2026-10-09 --log "$LOG" >/dev/null 2>&1
variant down_w2b sample_bundle_week2.json 'b["scan"]["from"] = "2026-10-05"; b["sources"]["meetings"] = {"provider": "granola", "status": "unavailable"}; b["meetings"] = []; b["decisions"] = []; b["user_actions"] = []'
expect 5 "Incomplete:" out "next week's outage gets its own notice" --bundle "$TMP/down_w2b.json" "${W2[@]}" --log "$LOG" --scheduled
hasnt "Skipped:" "one notice per week, not one ever"
expect 5 "Incomplete:" out "on request, an unreadable meeting list is Incomplete too" --bundle "$TMP/down.json" "${W1[@]}" --log "$LOG"
variant old sample_bundle_week1.json 'b["scan"]["from"] = "2026-09-01"; b["sources"]["meetings"]["plan"] = "Free"'
expect 5 "but the Free plan's 30-day note history only reaches back to Wed Sep 9, 2026" out "a window older than the plan's history is Incomplete" --bundle "$TMP/old.json" "${W1[@]}" --log "$(fresh p4)" --scheduled
variant oldlimit sample_bundle_week1.json 'b["scan"]["from"] = "2026-09-25"; b["sources"]["meetings"]["history_limit_days"] = 7'
expect 5 "7-day note history" out "history_limit_days overrides the plan" --bundle "$TMP/oldlimit.json" "${W1[@]}" --log "$(fresh p4)" --scheduled
variant unread sample_bundle_week1.json '
for m in b["meetings"]:
    m["notes_status"] = "denied"; m["transcript_status"] = "denied"
    for k in ["notes_text", "summary_text", "transcript_text"]: m.pop(k, None)
b["decisions"] = []'
expect 5 "none of the 5 meetings in this window could be read" out "every meeting unreadable is Incomplete" --bundle "$TMP/unread.json" "${W1[@]}" --log "$(fresh p4)" --scheduled
variant partial sample_bundle_week1.json 'b["meetings"][2]["notes_status"] = "denied"; del b["meetings"][2]["notes_text"]; b["decisions"] = [d for d in b["decisions"] if d["meeting_id"] != "sample-gtm1007"]'
run 0 "one unreadable meeting" --bundle "$TMP/partial.json" "${W1[@]}" --log "$(fresh p4)" --scheduled
has "⚠️ Not readable (notes and transcript): [GTM sync](https://notes.granola.ai/d/sample-gtm1007), Wed Oct 7." "an unreadable meeting is named"
variant empty sample_bundle_week1.json 'b["meetings"] = []; b["decisions"] = []'
LOG="$(fresh p5)"
expect 0 "Skipped: nothing to report for weekly:2026-10-05..2026-10-09" out "an empty week with complete coverage is skipped" --bundle "$TMP/empty.json" "${W1[@]}" --log "$LOG" --scheduled
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); assert [r["status"] for r in d["runs"]]==["empty"]' "$LOG" || fail "empty run"
ok "an empty week is recorded straight away"
LOG="$(week1_log p6)"
variant empty2 sample_bundle_week2.json 'b["meetings"] = []; b["decisions"] = []; b["user_actions"] = []'
run 0 "empty week with open flags" --bundle "$TMP/empty2.json" "${W2[@]}" --log "$LOG" --scheduled
has "**0 new decisions · 1 without an owner · 1 revisit date passed · 1 open proposal**" "a week with no new decisions still reports open flags"
run 0 "dry run" --bundle sample_bundle_week2.json --as-of 2026-10-23T16:00:00-07:00 --log "$(week1_log p7)" --dry-run
cp "$TMP/p7.json" "$TMP/p7_before.json"
python3 "$SCRIPT" --bundle sample_bundle_week2.json --as-of 2026-10-23T16:00:00-07:00 --log "$TMP/p7.json" --dry-run --scheduled >/dev/null 2>&1 || true
cmp -s "$TMP/p7.json" "$TMP/p7_before.json" || fail "--dry-run wrote the log"
ok "--dry-run never writes the log"
run 0 "json" --bundle sample_bundle_week2.json "${W2[@]}" --log "$(week1_log p8)" --scheduled --json
python3 -c '
import json,sys; d=json.load(open(sys.argv[1]))
assert d["status"]=="reported" and d["confirm_with"]=="--confirm-posted weekly:2026-10-09..2026-10-16"
assert d["flags"]["reversals"]==[{"id":"dl-4d218be690","reverses":"dl-5fafbb740f"}] and d["flags"]["conflicts"]==[{"id":"dl-5a9f6d4a66","with":"dl-81a703c67c"}]
assert d["flags"]["no_owner"]==["dl-5a9f6d4a66"] and d["flags"]["revisit_passed"]==["dl-648475149f"] and len(d["proposals"])==2
assert d["markdown"].startswith("## Decision log")' "$TMP/out" || fail "json output"
ok "--json carries flags, proposals, confirm_with and the markdown"
run 0 "slack format" --bundle sample_bundle_week2.json "${W2[@]}" --log "$(week1_log p9)" --format slack
has "*Decision log — Fri Oct 9 to Fri Oct 16, 2026*" "slack format uses Slack bold headings"
has "<https://notes.granola.ai/d/sample-pc1013|Pricing council>" "slack links use <url|label>"

# timezones: the local meeting date is the decision date; window checks use it
variant tz sample_bundle_week1.json 'b["meetings"][4]["start"] = "2026-10-09T23:30:00+14:00"'
run 0 "meeting far east of the run" --bundle "$TMP/tz.json" "${W1[@]}" --log "$(fresh tzlog)"
variant tz2 sample_bundle_week1.json 'b["meetings"][0]["start"] = "2026-10-04T23:30:00-12:00"'
expect 2 "2026-10-04 is outside the scan window" err "a meeting's own local date decides the window" --bundle "$TMP/tz2.json" "${W1[@]}" --log "$(fresh tzlog)"

# ---------------------------------------------------------------- log file safety
LOG="$(week1_log l1)"
python3 - "$LOG" "$TMP/l_bad.json" <<'PY'
import json, sys
log = json.load(open(sys.argv[1], encoding="utf-8"))
log["records"]["dl-5fafbb740f"]["mention_count"] = "two"
json.dump(log, open(sys.argv[2], "w", encoding="utf-8"))
PY
expect 2 '$.records.dl-5fafbb740f.mention_count: has the wrong type; refusing to run blind' err "a corrupt log stops the run" --render-log --log "$TMP/l_bad.json" "${W2[@]}"
printf '{"log_version": 2}' >"$TMP/l_v2.json"
expect 2 "not a decision-log file (log_version 1 expected)" err "a log of another version is refused" --topics --log "$TMP/l_v2.json"
mkdir -p "$TMP/l_dir.json"
expect 2 "is not a regular file" err "a folder at the log path is refused" --bundle sample_bundle_week1.json "${W1[@]}" --log "$TMP/l_dir.json"
expect 2 "folder $TMP/nowhere does not exist" err "a missing log folder is refused" --bundle sample_bundle_week1.json "${W1[@]}" --log "$TMP/nowhere/log.json"
if [[ "$(id -u)" != 0 ]]; then
  mkdir -p "$TMP/ro"; chmod 555 "$TMP/ro"
  expect 2 "is not writable, so the run could not be recorded" err "an unwritable log folder stops the run before printing" --bundle sample_bundle_week1.json "${W1[@]}" --log "$TMP/ro/log.json" --scheduled
  chmod 755 "$TMP/ro"
else
  ok "unwritable folder check skipped (running as root)"
fi
LOG="$(fresh lk)"
echo "pid 1 token someone-else" >"$LOG.lock"
expect 3 "another run is updating this decision log" err "a held lock stops the run with exit 3" --bundle sample_bundle_week1.json "${W1[@]}" --log "$LOG" --scheduled
[[ -e "$LOG.lock" ]] || fail "someone else's lock was removed"
ok "a run never removes another run's fresh lock"
touch -d '20 minutes ago' "$LOG.lock"
run 0 "stale lock replaced" --bundle sample_bundle_week1.json "${W1[@]}" --log "$LOG" --scheduled
[[ ! -e "$LOG.lock" ]] || fail "lock left behind"
ok "a stale lock (over 15 minutes) is replaced and the run removes its own lock"
echo "pid 1 token future" >"$LOG.lock"; touch -d 'tomorrow' "$LOG.lock"
expect 0 "Confirmed posted" out "a lock dated in the future counts as stale" --confirm-posted weekly:2026-10-05..2026-10-09 --log "$LOG"
rm -f "$LOG.lock"; mkdir "$LOG.lock"
expect 2 "is not a regular file; remove it" err "a folder at the lock path stops with exit 2" --bundle sample_bundle_week1.json "${W1[@]}" --log "$LOG"
rmdir "$LOG.lock"
python3 - "$SCRIPT" "$TMP" <<'PY' || fail "concurrent runs"
import subprocess, sys
script, tmp = sys.argv[1], sys.argv[2]
log = tmp + "/race.json"
procs = [subprocess.Popen([sys.executable, script, "--bundle", "sample_bundle_week1.json", "--as-of", "2026-10-09T16:00:00-07:00",
                           "--log", log, "--scheduled"], stdout=subprocess.PIPE, stderr=subprocess.PIPE) for _ in range(6)]
results = [(p.wait(), p.stdout.read()) for p in procs]
printed = [code for code, out in results if code == 0 and out]
assert len(printed) == 1, results
assert all(code in (0, 3, 4) for code, _ in results), [c for c, _ in results]
PY
ok "six racing scheduled runs print exactly one report"

# ---------------------------------------------------------------- output streams
LOG="$(fresh io)"
set +e
python3 "$SCRIPT" --bundle sample_bundle_week1.json "${W1[@]}" --log "$LOG" --scheduled >&- 2>"$TMP/err"; code=$?
set -e
[[ "$code" == 4 ]] || fail "closed stdout after staging: exit $code"
grep -qF "was staged but could not be written to stdout" "$TMP/err" && ! grep -q Traceback "$TMP/err" || fail "closed stdout message"
ok "a staged report that can't be written exits 4 with a resume hint, no traceback"
run 0 "resume after closed stdout" --bundle sample_bundle_week1.json "${W1[@]}" --log "$LOG" --scheduled --resume-pending
set +e
python3 "$SCRIPT" --render-log --log "$LOG" "${W1[@]}" 2>"$TMP/err" | head -c 10 >/dev/null; code=${PIPESTATUS[0]}
python3 "$SCRIPT" --search price --log "$LOG" "${W1[@]}" >&- 2>"$TMP/err2"; code2=$?
set -e
! grep -q Traceback "$TMP/err" "$TMP/err2" || fail "traceback on a broken pipe"
[[ "$code2" == 2 ]] || fail "closed stdout on a read command: exit $code2"
ok "a broken pipe or closed stdout never prints a traceback"
LANG=C LC_ALL=C PYTHONIOENCODING=ascii python3 "$SCRIPT" --render-log --log "$LOG" "${W1[@]}" >"$TMP/out" 2>"$TMP/err" || fail "ascii locale"
grep -qF "Decision log" "$TMP/out" && grep -qF "“" "$TMP/out" || fail "utf-8 output"
ok "output is UTF-8 whatever the locale"

# ---------------------------------------------------------------- strict dates: same answer on every Python from 3.8
python3 - <<'PY' || fail "date table"
import importlib.util
spec = importlib.util.spec_from_file_location("bd", "build_decision_log.py"); bd = importlib.util.module_from_spec(spec); spec.loader.exec_module(bd)
def ok_moment(v):
    try:
        bd.parse_moment(v, lambda p: bd.InputError(p)); return True
    except bd.InputError:
        return False
def ok_day(v):
    try:
        bd.parse_day(v, lambda p: bd.InputError(p)); return True
    except bd.InputError:
        return False
good = ["2026-10-09T16:00:00-07:00", "2026-10-09T16:00Z", "2026-10-09T16:00:00.123+05:30", "2026-10-09T16:00:00.123456Z",
        "2026-10-09T23:59:59+14:00", "2026-10-09T00:00:00-14:00", "1900-01-01T00:00:00Z", "2999-12-31T23:59:59Z"]
bad = ["2026-10-09T16:00:00", "2026-10-09 16:00:00Z", "20261009T160000Z", "2026-W41-5T16:00Z", "2026-10-09T24:00:00Z",
       "2026-10-09T16:60:00Z", "2026-10-09T16:00:60Z", "2026-10-09T16:00:00+00:60", "2026-10-09T16:00:00+14:01",
       "2026-10-09T16:00:00+1400", "2026-10-09T16:00:00.1Z", "2026-10-09T16:00:00,123Z", "2026-02-30T10:00:00Z",
       "1899-12-31T23:59:59Z", "3000-01-01T00:00:00Z", "２０２６-10-09T16:00:00Z", "2026-10-09t16:00:00Z", "2026-10-09T16:00:00z",
       " 2026-10-09T16:00:00Z", "2026-10-09T16:00:00Z\n", "", None, 20261009, True]
assert all(ok_moment(v) for v in good), [v for v in good if not ok_moment(v)]
assert not any(ok_moment(v) for v in bad), [v for v in bad if ok_moment(v)]
assert all(ok_day(v) for v in ["2026-10-09", "2024-02-29", "1900-01-01", "2999-12-31"])
assert not any(ok_day(v) for v in ["2026-10-9", "20261009", "2026-W41", "2026-W41-5", "2025-02-29", "2026-10-09T00:00:00Z",
                                    "2026-13-01", "٢٠٢٦-10-09", "1899-12-31", "3000-01-01", None, 5])
PY
ok "strict date and datetime table (identical on Python 3.8 and 3.14)"

# ---------------------------------------------------------------- single-field mutation fuzz: bundle and log (no tracebacks)
python3 - "$TMP" <<'PY' || fail "mutation fuzz"
import contextlib, copy, importlib.util, io, json, shutil, sys, traceback
spec = importlib.util.spec_from_file_location("bd", "build_decision_log.py"); bd = importlib.util.module_from_spec(spec); spec.loader.exec_module(bd)
tmp = sys.argv[1]

def call(args):
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = bd.main(args)
    except SystemExit as exc:
        code = exc.code
    except Exception:
        return "crash", traceback.format_exc()
    return code, out.getvalue() + err.getvalue()

def paths(node, prefix=()):
    yield prefix
    if isinstance(node, dict):
        for k in list(node):
            yield from paths(node[k], prefix + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from paths(v, prefix + (i,))

MUTATIONS = [("delete", None), ("null", None), ("zero", 0), ("true", True), ("list", []), ("dict", {}),
             ("text", "x"), ("huge", "y" * 200000), ("unicode", "Zoë ✓ 日本 ‮<b>[x](javascript:alert(1))</b>"),
             ("negative", -1), ("float", 1e308), ("date", "2026-13-45"), ("id", "dl-ffffffffff")]

def mutate(doc, path, how, value):
    doc = copy.deepcopy(doc)
    if not path:
        return value if how != "delete" else {}
    parent = doc
    for key in path[:-1]:
        parent = parent[key]
    if how == "delete":
        del parent[path[-1]]
    else:
        parent[path[-1]] = copy.deepcopy(value)
    return doc

# A log with every kind of field: two posted weeks, repeats, a reversal, a conflict, replies of each kind.
log = tmp + "/fuzz_base_log.json"
for args in (["--bundle", "sample_bundle_week1.json", "--as-of", "2026-10-09T16:00:00-07:00", "--scheduled"],
             ["--confirm-posted", "weekly:2026-10-05..2026-10-09", "--as-of", "2026-10-09T16:01:00-07:00"],
             ["--bundle", "sample_bundle_week2.json", "--as-of", "2026-10-16T16:00:00-07:00", "--scheduled"],
             ["--confirm-posted", "weekly:2026-10-09..2026-10-16"]):
    code, text = call(args + ["--log", log])
    assert code == 0, text
replies = {"schema_version": 1, "scan": {"from": "2026-10-17", "to": "2026-10-17", "cadence": "on_request"},
           "sources": {"meetings": {"provider": "granola", "status": "ok"}}, "meetings": [], "decisions": [],
           "user_actions": [{"decision_id": "dl-20a4d4a6da", "action": "confirm", "date": "2026-10-17", "note": "ok"},
                            {"decision_id": "dl-4eeeaecc81", "action": "dismiss", "date": "2026-10-17", "note": "no"},
                            {"decision_id": "dl-5a9f6d4a66", "action": "resolve", "other_id": "dl-81a703c67c", "resolution": "both_stand", "date": "2026-10-17"},
                            {"decision_id": "dl-648475149f", "action": "set_revisit", "revisit_on": "2026-11-01", "date": "2026-10-17"}]}
json.dump(replies, open(tmp + "/fuzz_replies.json", "w"))
code, text = call(["--bundle", tmp + "/fuzz_replies.json", "--as-of", "2026-10-17T09:00:00-07:00", "--log", log])
assert code == 0, text
base_log = json.load(open(log, encoding="utf-8"))

runs = crashes = 0
codes = {}
for sample, asof, log_for in (("sample_bundle_week1.json", "2026-10-09T16:00:00-07:00", None),
                              ("sample_bundle_week2.json", "2026-10-16T16:00:00-07:00", "week1")):
    base = json.load(open(sample, encoding="utf-8"))
    if log_for:
        wlog = tmp + "/fuzz_week1_log.json"
        call(["--bundle", "sample_bundle_week1.json", "--as-of", "2026-10-09T16:00:00-07:00", "--log", wlog, "--scheduled"])
        call(["--confirm-posted", "weekly:2026-10-05..2026-10-09", "--log", wlog])
    for path in list(paths(base)):
        for how, value in MUTATIONS:
            if not path and how not in ("null", "list", "text", "delete"):
                continue
            doc = mutate(base, path, how, value)
            target = tmp + "/fuzz_bundle.json"
            json.dump(doc, open(target, "w", encoding="utf-8"), ensure_ascii=False)
            logp = tmp + "/fuzz_run_log.json"
            if log_for:
                shutil.copy(wlog, logp)
            else:
                open(logp, "w").close(); __import__("os").remove(logp)
            for extra in ([], ["--scheduled", "--dry-run"]):
                runs += 1
                code, text = call(["--bundle", target, "--as-of", asof, "--log", logp] + extra)
                codes[code] = codes.get(code, 0) + 1
                if code == "crash" or code not in (0, 2, 5):
                    crashes += 1
                    if crashes < 6:
                        print("bundle", sample, path, how, code, text[-600:])

for path in list(paths(base_log)):
    for how, value in MUTATIONS:
        doc = mutate(base_log, path, how, value)
        target = tmp + "/fuzz_log.json"
        json.dump(doc, open(target, "w", encoding="utf-8"), ensure_ascii=False)
        for args in (["--render-log", "--as-of", "2026-10-18T09:00:00-07:00"], ["--topics"],
                     ["--search", "annual discount CSM", "--as-of", "2026-10-18T09:00:00-07:00"],
                     ["--next-window", "--as-of", "2026-10-23T16:00:00-07:00"],
                     ["--bundle", tmp + "/fuzz_replies.json", "--as-of", "2026-10-18T09:00:00-07:00", "--dry-run"]):
            runs += 1
            code, text = call(args + ["--log", target])
            codes[code] = codes.get(code, 0) + 1
            if code == "crash" or code not in (0, 2):
                crashes += 1
                if crashes < 6:
                    print("log", path, how, args[0], code, text[-600:])
print("mutation fuzz: %d runs, exit codes %s, %d crashes" % (runs, sorted(codes.items(), key=str), crashes))
assert crashes == 0
PY
ok "single-field mutation fuzz over both sample bundles and a full log: no tracebacks"

# ---------------------------------------------------------------- escaping fuzz: every printed field, markdown and Slack
python3 - "$TMP" <<'PY' || fail "escaping fuzz"
import contextlib, copy, importlib.util, io, json, os, re, sys
spec = importlib.util.spec_from_file_location("bd", "build_decision_log.py"); bd = importlib.util.module_from_spec(spec); spec.loader.exec_module(bd)
tmp = sys.argv[1]
PAYLOADS = ["\\[click\\](https://phish.example/a)", "[click](https://phish.example/a)", "![i](https://phish.example/i.png)",
            "\\\\[x\\\\](https://phish.example)", "<https://phish.example|click>", "<https://phish.example>", "<!channel> <!here>",
            "<@U012AB3CD> <#C024BE7LR|x>", "@channel @here", "*bold", "**", "_it_ ~s~ `c`", "x*y_z~w`v", "<b>html</b> <img src=x>",
            "&lt;!here&gt; &amp;", "\\", "\\*\\*", "](https://phish.example)", "[a]: https://phish.example", "|pipe|",
            "see www.phish.example now", "visit phish.example or https://phish.example/x", "javascript:alert(1)",
            "line\n\n# Heading\n> quote\n- item\n1. one", "‮evil‬ ​zero", "1. starts like a list", "- dash", "# hash",
            "$$x^2$$ and $50k to $75k", "==highlight== a=b"]
ALLOWED = ("https://notes.granola.ai/",)

def call(args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = bd.main(args)
    return code, out.getvalue(), err.getvalue()

def strip_escapes(line):
    return re.sub(r"\\.", "", line)

problems = runs = 0
def bad(why, where, line):
    global problems
    problems += 1
    if problems < 15:
        print("%s | %s | %s" % (why, where, line[:200]))

def check(text, fmt, where):
    for line in text.split("\n"):
        if re.match(r"\s*(# Heading|> quote|- item|1\. one)\b", line):
            bad("value started a line", where, line)
        if fmt == "markdown":
            s = strip_escapes(line)
            targets = re.findall(r"\]\(([^)]*)\)", s)
            for target in targets:
                if not target.startswith(ALLOWED):
                    bad("link to %s" % target, where, line)
            s = re.sub(r"\]\(https://notes\.granola\.ai/[^)]*\)", "]", s)
            if "<" in s or ">" in s.replace("&gt;", ""):
                bad("raw < or > (HTML or autolink)", where, line)
            if re.search(r"\]\s*\[|^\s*\[[^\]]*\]:|!\[", s):
                bad("reference link or image", where, line)
            if s.count("*") % 2 or s.replace("**", "").count("*"):
                bad("unbalanced bold", where, line)
            if s.count("`") % 2 or "~" in s or "_" in s:
                bad("code, strike or italic from a value", where, line)
            if re.search(r"://|www\.|phish\.example", s):
                bad("autolink material", where, line)
            if "$" in s or "==" in s:
                bad("math or highlight (Type renders $..$ and ==..==)", where, line)
            if "@channel" in s or "‮" in line or "​" in line:
                bad("mention or invisible character", where, line)
        else:
            for link in re.findall(r"<([^>]*)>", line):
                if not re.fullmatch(r"https?://[^|\s<>]+\|[^|<>]*", link) or not link.startswith(ALLOWED):
                    bad("slack link/mention <%s>" % link, where, line)
            rest = re.sub(r"<https?://[^|\s<>]+\|[^|<>]*>", "", line)
            if "<" in rest or "@" in rest:
                bad("raw < or @", where, line)
            if rest.count("*") % 2 or rest.count("`") % 2 or "~" in rest or rest.count("_") % 2:
                bad("formatting", where, line)
            if re.search(r"://|www\.|phish\.example|\[", rest):
                bad("autolink or link material", where, line)

base = json.load(open("sample_bundle_week1.json", encoding="utf-8"))
FIELDS = [("meetings", 0, "title"), ("meetings", 2, "title"), ("decisions", 0, "statement"), ("decisions", 0, "owner"),
          ("decisions", 0, "rationale"), ("decisions", 0, "quote"), ("decisions", 2, "statement"), ("decisions", 2, "quote"),
          ("decisions", 1, "statement"), ("decisions", 3, "quote")]
for payload in PAYLOADS:
    b = copy.deepcopy(base)
    for m in b["meetings"]:
        for k in ("notes_text", "summary_text", "transcript_text"):
            m.pop(k, None)
    for section, idx, key in FIELDS:
        b[section][idx][key] = "pre " + payload + " post"
    b["decisions"][0]["decided_by"] = ["A " + payload]
    b["decisions"][0]["alternatives_rejected"] = ["B " + payload, "C"]
    b["scan"]["folder"] = "F " + payload
    b["scan"]["workspace_only"] = True
    b["sources"]["meetings"]["plan"] = ("P " + payload)[:40]
    b["sources"]["meetings"]["history_limit_days"] = 400
    b["meetings"][2]["transcript_status"] = "denied"
    path = tmp + "/esc_bundle.json"
    json.dump(b, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    log = tmp + "/esc_log.json"
    if os.path.exists(log):
        os.remove(log)
    code, out, err = call(["--bundle", path, "--as-of", "2026-10-09T16:00:00-07:00", "--log", log])
    assert code == 0, err
    ids = sorted(json.load(open(log, encoding="utf-8"))["records"])
    replies = {"schema_version": 1, "scan": {"from": "2026-10-09", "to": "2026-10-09", "cadence": "on_request"},
               "sources": {"meetings": {"provider": "granola", "status": "ok"}}, "meetings": [], "decisions": [],
               "user_actions": [{"decision_id": ids[0], "action": "dismiss", "date": "2026-10-09", "note": "N " + payload}]}
    json.dump(replies, open(tmp + "/esc_replies.json", "w", encoding="utf-8"), ensure_ascii=False)
    call(["--bundle", tmp + "/esc_replies.json", "--as-of", "2026-10-09T16:00:00-07:00", "--log", log])
    down = copy.deepcopy(b)
    down["sources"]["meetings"].update({"status": "unavailable", "detail": "D " + payload, "provider": ("Prov " + payload)[:40]})
    json.dump(down, open(tmp + "/esc_down.json", "w", encoding="utf-8"), ensure_ascii=False)
    for fmt in ("markdown", "slack"):
        for args in (["--bundle", path, "--as-of", "2026-10-09T16:00:00-07:00", "--log", log, "--dry-run"],
                     ["--bundle", tmp + "/esc_down.json", "--as-of", "2026-10-09T16:00:00-07:00", "--log", log, "--dry-run"],
                     ["--render-log", "--as-of", "2026-10-09T16:00:00-07:00", "--log", log],
                     ["--search", "pre post " + payload, "--as-of", "2026-10-09T16:00:00-07:00", "--log", log],
                     ["--search", ids[0], "--as-of", "2026-10-09T16:00:00-07:00", "--log", log]):
            runs += 1
            code, out, err = call(args + ["--format", fmt])
            if code not in (0, 5):
                if "has no searchable words" in err:
                    continue
                bad("exit %s %s" % (code, err.strip()[:200]), fmt, "")
                continue
            check(out, fmt, "%s %s %r" % (fmt, args[0], payload))
print("escaping fuzz: %d renders, %d problems" % (runs, problems))
assert problems == 0
# The checker itself must notice unescaped output: with escaping switched off, a hostile log fails it.
selftest = {"log_version": 1, "updated_at": None, "runs": [], "records": {"dl-0123456789": {
    "id": "dl-0123456789", "kind": "decision", "statement": "[click](https://phish.example/a) <b>x</b> *b @channel",
    "topic": "a/b", "area": "a", "decided_by": [], "owner": None, "owner_set": None, "rationale": None, "alternatives": [],
    "revisit_on": None, "revisit_set": None, "origin": {"meeting_id": "m", "title": "<!channel>", "date": "2026-10-09",
    "start": "2026-10-09T10:00:00Z", "url": None, "quote": "www.phish.example", "quote_source": "notes", "quote_checked": False},
    "mentions": [], "mention_count": 0, "supersedes": [], "conflicts": [], "dismissed": None, "confirmed": None,
    "logged_at": "2026-10-09T10:00:00Z", "reported_at": None, "history": []}}}
json.dump(selftest, open(tmp + "/selftest_log.json", "w"))
real_md, real_slack = bd.Markdown.text, bd.Slack.text
bd.Markdown.text = bd.Slack.text = lambda self, value: bd.clean(value)
for fmt in ("markdown", "slack"):
    code, out, err = call(["--render-log", "--as-of", "2026-10-09T16:00:00-07:00", "--log", tmp + "/selftest_log.json", "--format", fmt])
    assert code == 0, err
    before = problems
    with contextlib.redirect_stdout(io.StringIO()):
        check(out, fmt, "self-test")
    assert problems > before, "the escaping checker missed unescaped %s output" % fmt
bd.Markdown.text, bd.Slack.text = real_md, real_slack
for fmt in ("markdown", "slack"):
    code, out, err = call(["--render-log", "--as-of", "2026-10-09T16:00:00-07:00", "--log", tmp + "/selftest_log.json", "--format", fmt])
    before = problems
    check(out, fmt, "self-test escaped")
    assert problems == before
PY
ok "escaping fuzz: no value forms a link, autolink, image, raw HTML, mention, new line or formatting (markdown and Slack)"

# Two-week flow: every printed bundle field (all meeting titles, every decision field, repeats, reversals, possible
# conflicts), every reply note and owner, scan folder, source detail/provider/plan, and hostile meeting URLs; markdown,
# Slack and --json; report, --resume-pending, --render-log, --search and the Incomplete report.
python3 - "$TMP" <<'PY' || fail "escaping fuzz (two-week flow)"
import contextlib, copy, importlib.util, io, json, os, re, sys, tempfile
spec = importlib.util.spec_from_file_location("bd", "build_decision_log.py"); bd = importlib.util.module_from_spec(spec); spec.loader.exec_module(bd)
tmp = os.path.join(sys.argv[1], "esc2"); os.makedirs(tmp)
PAYLOADS = ["[click](https://phish.example/a)", "![i](https://phish.example/i.png)", "<https://phish.example|click>",
            "<!channel> <!here> <@U012AB3CD> <#C024BE7LR|x>", "@channel @here", "**bold** _it_ ~s~ `c`", "x*y_z~w`v",
            "<b>html</b> <img src=x onerror=alert(1)>", "&lt;!here&gt; &amp; &#60;b&#62;", "\\", "\\*\\*\\[x\\](https://phish.example)",
            "](https://phish.example)", "[a]: https://phish.example", "|pipe|", "www.phish.example", "phish.example",
            "HTTPS://PHISH.EXAMPLE/X", "hxxp://a b http:/\\/phish.example", "javascript:alert(1)", "data:text/html,<b>",
            "line\n\n# Heading\n> quote\n- item\n1. one", "‮evil‬ ​zero  sep \u0085nel",
            "1. starts like a list", "- dash", "# hash", "> gt", "=== setext", "[[1]](https://notes.granola.ai/d/x)",
            "foo＠phish．example", "[x](<https://phish.example>)", "~~~fence", "```fence", "$$math$$", "$5 and $7", "==hl== a=b", ":emoji: :+1:",
            "<mailto:a@phish.example>", "user at phish dot example", "a_b_c __init__", "*", "_", "`", "~", "[", "]"]
HOSTILE_URLS = ["https://phish.example/a)(b", "javascript:alert(1)", "https://notes.granola.ai/d/x\"><b>", "http://127.0.0.1/x",
                "https://notes.granola.ai:99999/d/x", "https://notes.granola.ai/d/x|<!channel>", "https://notes.granola.ai/d/a b*c_d~e`f",
                "//phish.example", "https://xn--80ak6aa92e.com/x", "https://notes.granola.ai/d/x#[y](z)", "HTTPS://Notes.Granola.AI/d/Y"]
ALLOWED_HOSTS = ("notes.granola.ai", "phish.example", "xn--80ak6aa92e.com")


def call(args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = bd.main(args)
    return code, out.getvalue(), err.getvalue()


problems = []
def bad(why, where, line):
    problems.append((why, where, line[:220]))


def check(text, fmt, where):
    for line in text.split("\n"):
        if re.match(r"\s*(# Heading|> quote|- item|1\. one|=== setext)\b", line):
            bad("value started a line", where, line)
        if "‮" in line or "​" in line or " " in line:
            bad("invisible char", where, line)
        if fmt == "markdown":
            s = re.sub(r"\\.", "", line)
            for target in re.findall(r"\]\(([^)]*)\)", s):
                if not re.fullmatch(r"https?://[a-z0-9.-]+(:[0-9]+)?(/[^\s()<>\[\]`]*)?", target) or target.split("/")[2].split(":")[0] not in ALLOWED_HOSTS:
                    bad("link target %s" % target, where, line)
            s = re.sub(r"\]\(https?://[^)\s]*\)", "]", s)
            if "<" in s or ">" in s.replace("&gt;", ""):
                bad("raw < or >", where, line)
            if re.search(r"\]\s*\[|^\s*\[[^\]]*\]:|!\[", s):
                bad("ref link/image", where, line)
            if s.replace("**", "").count("*"):
                bad("stray *", where, line)
            if s.count("`") % 2 or "~" in s or "_" in s or "$" in s or "==" in s:
                bad("code/strike/italic/math", where, line)
            if re.search(r"://|www\.|phish\.example", re.sub(r"\\.", "\x00", re.sub(r"\]\(https?://[^)\s]*\)", "]", line)), re.I):
                bad("autolink material", where, line)
            if "@channel" in s or "@here" in s:
                bad("mention", where, line)
            if re.search(r"&(?!amp;|lt;|gt;)", s):
                bad("raw entity", where, line)
        else:
            for link in re.findall(r"<([^>]*)>", line):
                m = re.fullmatch(r"(https?://([^/:|\s<>]+)[^|\s<>]*)\|[^|<>]*", link)
                if not m or m.group(2) not in ALLOWED_HOSTS:
                    bad("slack <%s>" % link, where, line)
            rest = re.sub(r"<https?://[^|\s<>]+\|[^|<>]*>", "", line)
            if "<" in rest or "@" in rest or ">" in rest.replace("&gt;", ""):
                bad("raw < > @", where, line)
            if rest.count("*") % 2 or rest.count("`") % 2 or "~" in rest or rest.count("_") % 2:
                bad("formatting", where, line)
            if re.search(r"://|www\.|phish\.example|\[|\]", rest, re.I):
                bad("autolink/link material", where, line)
            if re.search(r"&(?!amp;|lt;|gt;)", rest):
                bad("raw entity", where, line)


def name_safe(p):
    return p.replace("@", " at ").replace("＠", " at ")


w1 = json.load(open("sample_bundle_week1.json", encoding="utf-8"))
w2 = json.load(open("sample_bundle_week2.json", encoding="utf-8"))
renders = 0
for i, payload in enumerate(PAYLOADS):
    url = HOSTILE_URLS[i % len(HOSTILE_URLS)]
    for fmt in ("markdown", "slack"):
        log = os.path.join(tmp, "log_%d_%s.json" % (i, fmt))
        a = copy.deepcopy(w1)
        a["scan"]["folder"] = "F " + payload
        for j, m in enumerate(a["meetings"]):
            for k in ("notes_text", "summary_text", "transcript_text"):
                m.pop(k, None)
            m["title"] = "T%d %s" % (j, payload)
            if j == 1:
                m["url"] = url
        for j, d in enumerate(a["decisions"]):
            d["statement"] = "S%d %s" % (j, payload)
            d["rationale"] = "R%d %s" % (j, payload)
            d["quote"] = "Q%d %s" % (j, payload)
            d["alternatives_rejected"] = ["A%d %s" % (j, payload)]
            d["decided_by"] = ["N%d %s" % (j, name_safe(payload))]
            if d["kind"] == "decision" and j != 1:
                d["owner"] = "O%d %s" % (j, name_safe(payload))
        a["decisions"][4]["statement"] = a["decisions"][0]["statement"]
        a["decisions"][4]["quote"] = "Q4b " + payload
        p1 = os.path.join(tmp, "a.json"); json.dump(a, open(p1, "w", encoding="utf-8"), ensure_ascii=False)
        code, out, err = call(["--bundle", p1, "--as-of", "2026-10-09T16:00:00-07:00", "--log", log, "--scheduled", "--format", fmt])
        if code != 0:
            bad("w1 exit %s: %s" % (code, err.strip()[:200]), fmt, payload); continue
        check(out, fmt, "w1 %s %r" % (fmt, payload)); renders += 1
        call(["--confirm-posted", "weekly:2026-10-05..2026-10-09", "--log", log, "--as-of", "2026-10-09T16:01:00-07:00"])
        recs = json.load(open(log, encoding="utf-8"))["records"]
        by_topic = {}
        for rid, r in sorted(recs.items(), key=lambda kv: kv[1]["origin"]["start"]):
            by_topic.setdefault(r["topic"], rid)
        b = copy.deepcopy(w2)
        for j, m in enumerate(b["meetings"]):
            for k in ("notes_text", "summary_text", "transcript_text"):
                m.pop(k, None)
            m["title"] = "U%d %s" % (j, payload)
        for j, d in enumerate(b["decisions"]):
            d["statement"] = "V%d %s" % (j, payload)
            d["rationale"] = "W%d %s" % (j, payload)
            d["quote"] = "X%d %s" % (j, payload)
            d["alternatives_rejected"] = ["Y%d %s" % (j, payload)]
            d["decided_by"] = ["Z%d %s" % (j, name_safe(payload))]
        b["decisions"][0]["supersedes"] = [by_topic["pricing/annual-discount"]]
        b["user_actions"] = [
            {"decision_id": by_topic["cs/named-csm-threshold"], "action": "set_owner", "owner": "Own " + name_safe(payload), "date": "2026-10-12", "note": "Note " + payload},
            {"decision_id": by_topic["finance/close-calendar"], "action": "set_revisit", "revisit_on": "2026-10-14", "date": "2026-10-12", "note": "Rev " + payload},
            {"decision_id": by_topic["pricing/free-tier"], "action": "confirm", "date": "2026-10-12", "note": "Conf " + payload},
        ]
        p2 = os.path.join(tmp, "b.json"); json.dump(b, open(p2, "w", encoding="utf-8"), ensure_ascii=False)
        code, out, err = call(["--bundle", p2, "--as-of", "2026-10-16T16:00:00-07:00", "--log", log, "--scheduled", "--format", fmt, "--json"])
        if code != 0:
            bad("w2 exit %s: %s" % (code, err.strip()[:200]), fmt, payload); continue
        check(json.loads(out)["markdown"], fmt, "w2json %s %r" % (fmt, payload)); renders += 1
        code, out, err = call(["--bundle", p2, "--as-of", "2026-10-16T16:00:00-07:00", "--log", log, "--scheduled", "--format", fmt, "--resume-pending"])
        check(out, fmt, "w2 %s %r" % (fmt, payload)); renders += 1
        if "Reverses" not in out or "conflict" not in out.lower():
            bad("w2 sections missing", fmt, payload)
        # dismiss with note, then render-log and searches
        c = {"schema_version": 1, "scan": {"from": "2026-10-16", "to": "2026-10-16", "cadence": "on_request"},
             "sources": {"meetings": {"provider": "granola", "status": "ok"}}, "meetings": [], "decisions": [],
             "user_actions": [{"decision_id": by_topic["partners/referral-fee"] if "partners/referral-fee" in by_topic else sorted(recs)[0], "action": "dismiss", "date": "2026-10-16", "note": "Dis " + payload}]}
        recs2 = json.load(open(log, encoding="utf-8"))["records"]
        for rid, r in recs2.items():
            if r["topic"] == "partners/referral-fee":
                c["user_actions"][0]["decision_id"] = rid
        p3 = os.path.join(tmp, "c.json"); json.dump(c, open(p3, "w", encoding="utf-8"), ensure_ascii=False)
        code, out, err = call(["--bundle", p3, "--as-of", "2026-10-16T17:00:00-07:00", "--log", log, "--format", fmt])
        check(out, fmt, "dismiss %s %r" % (fmt, payload)); renders += 1
        for args in (["--render-log"], ["--search", "S0 V0 " + payload], ["--search", c["user_actions"][0]["decision_id"]], ["--search", "zzzz " + payload]):
            code, out, err = call(args + ["--as-of", "2026-10-16T17:00:00-07:00", "--log", log, "--format", fmt])
            if code != 0 and "no searchable words" not in err:
                bad("exit %s %s" % (code, err.strip()[:200]), fmt, repr(args))
            check(out, fmt, "%s %s %r" % (args[0], fmt, payload)); renders += 1
        # incomplete banner with hostile detail/provider
        d = copy.deepcopy(b); d["user_actions"] = []
        d["sources"]["meetings"].update({"status": "denied", "detail": "D " + payload, "provider": ("Pv " + payload)[:40], "plan": ("Pl " + payload)[:40]})
        p4 = os.path.join(tmp, "d.json"); json.dump(d, open(p4, "w", encoding="utf-8"), ensure_ascii=False)
        code, out, err = call(["--bundle", p4, "--as-of", "2026-10-23T16:00:00-07:00", "--log", log, "--dry-run", "--format", fmt])
        check(out, fmt, "incomplete %s %r" % (fmt, payload)); renders += 1
print("escaping fuzz, two-week flow: %d renders, %d problems" % (renders, len(problems)))
for p in problems[:10]:
    print(p)
assert problems == [] and renders > 700
# the checker must notice unescaped output
real_md, real_slack = bd.Markdown.text, bd.Slack.text
bd.Markdown.text = bd.Slack.text = lambda self, value: bd.clean(value)
for fmt in ("markdown", "slack"):
    log = os.path.join(tmp, "self_%s.json" % fmt)
    a = copy.deepcopy(w1)
    a["meetings"][0]["title"] = "[x](https://phish.example) <!channel> $$m$$ ==h== *b"
    for m in a["meetings"]:
        for k in ("notes_text", "summary_text", "transcript_text"):
            m.pop(k, None)
    json.dump(a, open(os.path.join(tmp, "self.json"), "w", encoding="utf-8"), ensure_ascii=False)
    code, out, err = call(["--bundle", os.path.join(tmp, "self.json"), "--as-of", "2026-10-09T16:00:00-07:00", "--log", log, "--format", fmt])
    check(out, fmt, "self-test")
    assert problems, "checker missed unescaped %s output" % fmt
    del problems[:]
bd.Markdown.text, bd.Slack.text = real_md, real_slack
PY
ok "escaping fuzz, two-week flow: every printed field incl. reversals, conflicts, repeats, replies and Incomplete (markdown, Slack, --json)"

# ---------------------------------------------------------------- scenario harness: the automation recipe over many weeks
# An agent follows SKILL.md literally: --next-window, --topics, build the bundle, run, post, confirm, update the doc;
# failed posts are resumed, lost confirms are confirmed after checking the destination, outages post an Incomplete
# notice once and record it (a later outage run that week prints Skipped), extra triggers run on Saturday or just
# after midnight on Monday, and on-request runs with replies (including dismiss and restore) happen midweek. After
# every step: no week gets two complete reports or two Incomplete notices, no entry is announced as new in two posted
# reports, every confirmed report leaves nothing older unreported, two unlinked live decisions on one topic always
# carry a conflict flag, and every extracted decision is in the log exactly once (as an entry or a linked repeat) once
# a confirmed report's window has covered its meeting.
python3 - "$TMP" <<'PY' || fail "scenario harness"
import contextlib, importlib.util, io, json, os, random, sys
from datetime import date, datetime, timedelta, timezone
spec = importlib.util.spec_from_file_location("bd", "build_decision_log.py"); bd = importlib.util.module_from_spec(spec); spec.loader.exec_module(bd)
tmp = sys.argv[1]
AGENT_TZ = timezone(timedelta(hours=-7))
OFFSETS = [-12, -10, -7, -5, 0, 1, 5.5, 9, 14]
TOPICS = ["pricing/discount", "pricing/free-tier", "cs/csm-threshold", "finance/close-day", "sales/territories", "marketing/launch"]

def call(args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = bd.main(args)
    return code, out.getvalue(), err.getvalue()

def simulate(seed, weeks):
    rnd = random.Random(seed)
    log = "%s/scenario_%d.json" % (tmp, seed)
    meetings, items, n = [], [], 0
    arrived = []          # (period_key, week, staged ids) of complete reports that reached the destination
    notices = []          # weeks whose Incomplete notice reached the destination
    events = {}
    def bump(k):
        events[k] = events.get(k, 0) + 1
    def load():
        return json.load(open(log, encoding="utf-8")) if os.path.exists(log) else {"records": {}, "runs": []}
    def where(item, records):
        hits = [rid for rid, r in records.items() if (r["origin"]["meeting_id"], r["origin"]["quote"]) == (item["meeting_id"], item["quote"])]
        hits += [rid for rid, r in records.items() for m in r["mentions"] if (m["meeting_id"], m["quote"]) == (item["meeting_id"], item["quote"])]
        return hits
    def bundle(start, end, as_of, cadence, status="ok", actions=()):
        state = load()
        topics = json.loads(call(["--topics", "--log", log])[1]) if os.path.exists(log) else []
        ms = [m for m in meetings if start <= m["start"].date() <= end and m["start"] <= as_of]
        ids = set(m["meeting_id"] for m in ms)
        decs, latest = [], {}
        for it in sorted([i for i in items if i["meeting_id"] in ids], key=lambda i: (i["start"].date(), i["start"], i["n"])):
            d = {"ref": "r%d" % it["n"], "kind": it["kind"], "meeting_id": it["meeting_id"], "statement": it["statement"],
                 "topic": it["topic"], "area": it["topic"].split("/")[0], "quote": it["quote"], "quote_source": "transcript",
                 "owner": it["owner"] if it["kind"] == "decision" else None, "decided_by": ["Dana Ortiz"]}
            if it["kind"] == "decision" and not where(it, state["records"]):
                live = {}
                busy = set(a["decision_id"] for a in actions) | set(a.get("other_id") for a in actions)
                for x in sorted(topics, key=lambda x: x["date"]):   # the newest live entry dated on or before this meeting
                    if x["date"] <= it["start"].date().isoformat() and x["id"] not in busy:
                        live[(x["topic"], x["kind"])] = x["id"]
                earlier = latest.get(it["topic"])
                target = earlier or live.get((it["topic"], "decision")) or live.get((it["topic"], "proposal"))
                if target and it["link"] == "supersedes":
                    d["supersedes"] = [target]
                elif target and it["link"] == "same_as" and (earlier or live.get((it["topic"], "decision"))):
                    d["same_as"] = earlier or live[(it["topic"], "decision")]
            if it["kind"] == "decision" and "same_as" not in d:
                latest[it["topic"]] = d["ref"]
            decs.append(d)
        b = {"schema_version": 1, "scan": {"from": start.isoformat(), "to": end.isoformat(), "cadence": cadence},
             "sources": {"meetings": {"provider": "granola", "status": status, "plan": "business"}},
             "meetings": [{"meeting_id": m["meeting_id"], "title": m["title"], "start": m["start"].isoformat(),
                           "url": "https://notes.granola.ai/d/" + m["meeting_id"], "notes_status": "ok", "transcript_status": "ok",
                           "transcript_text": m["text"]} for m in ms] if status == "ok" else [],
             "decisions": decs if status == "ok" else [], "user_actions": list(actions)}
        path = "%s/scenario_bundle_%d.json" % (tmp, seed)
        json.dump(b, open(path, "w", encoding="utf-8"))
        return path
    def check(when):
        state = load()
        weeks_seen = [w for _, w, _ in arrived]
        assert len(weeks_seen) == len(set(weeks_seen)), (seed, when, "a week got two complete reports", arrived)
        assert len(notices) == len(set(notices)), (seed, when, "a week got two Incomplete notices", notices)
        status, _ = bd.statuses(state["records"]) if state["records"] else ({}, {})
        live = sorted(rid for rid in status if status[rid] == "active")
        for i, a in enumerate(live):
            for b in live[i + 1:]:
                ra, rb = state["records"][a], state["records"][b]
                if ra["topic"] != rb["topic"] or bd.reaches_live(state["records"], a, b) or bd.reaches_live(state["records"], b, a):
                    continue
                assert any(c["with"] == b for c in ra["conflicts"]) or any(c["with"] == a for c in rb["conflicts"]), \
                    (seed, when, "two live decisions on one topic without a conflict flag", a, b)
        seen = set()
        for key, _, staged in arrived:
            assert not (seen & set(staged)), (seed, when, "announced twice", key)
            seen |= set(staged)
        posted = [r for r in state["runs"] if r["status"] == "posted"]
        if posted:
            last = max(posted, key=lambda r: bd.instant(r["as_of"]))
            for rid, rec in state["records"].items():
                if rec["dismissed"] is None and bd.instant(rec["logged_at"]) <= bd.instant(last["as_of"]):
                    assert rec["reported_at"] is not None, (seed, when, "never announced", rid)
            covered = bd.instant(last["as_of"])
            for it in items:
                if it["start"] <= covered and it["start"].date() >= monday:
                    hits = where(it, state["records"])
                    assert len(hits) == 1, (seed, when, "item logged %d times" % len(hits), it["statement"])
    def scheduled(as_of, status="ok", post_fails=False, lose_confirm=False):
        nw = json.loads(call(["--next-window", "--as-of", as_of.isoformat(), "--log", log])[1])
        if nw["already_posted"]:
            bump("already_posted"); return
        for key in nw["pending"]:
            # Step 5: a pending report. Check the destination: if it arrived, just confirm; otherwise resume and post.
            arrived_keys = [k for k, _, _ in arrived]
            if key in arrived_keys:
                code, out, err = call(["--confirm-posted", key, "--log", log])
                assert code == 0, err
                bump("late_confirm")
            else:
                bump("pending_seen")   # its post failed: the run below resumes it (same week) or supersedes it
        nw = json.loads(call(["--next-window", "--as-of", as_of.isoformat(), "--log", log])[1])
        if nw["already_posted"]:
            bump("already_posted"); return
        f, t = date.fromisoformat(nw["from"]), date.fromisoformat(nw["to"])
        path = bundle(f, t, as_of, "weekly", status)
        code, out, err = call(["--bundle", path, "--as-of", as_of.isoformat(), "--log", log, "--scheduled"])
        if code == 4:
            # a pending report from earlier this week whose post failed: resume it, post, confirm
            key = err.split("pending: ", 1)[1].split(" ", 1)[0]
            if key == "a":     # "pending: a report for <this period> ..."
                key = nw["period_key"]
            pf, pt = bd.period_bounds(key)
            pending_run = [r for r in load()["runs"] if r["period_key"] == key][-1]
            code, out, err = call(["--bundle", bundle(pf, pt, bd.instant(pending_run["as_of"]), "weekly"), "--as-of",
                                   pending_run["as_of"], "--log", log, "--scheduled", "--resume-pending"])
            assert code == 0 and out.startswith("## Decision log"), (seed, code, err)
            staged = [r for r in load()["runs"] if r["period_key"] == key][-1]["staged"]
            arrived.append((key, bd.week_of(pt), staged))
            assert call(["--confirm-posted", key, "--log", log])[0] == 0
            bump("resumed")
            return
        assert code in (0, 5), (seed, code, err)
        if code == 5:
            assert status != "ok" and "Incomplete" in out
            if out.startswith("Skipped:"):   # this week's notice was already posted: post nothing
                assert bd.week_of(t) in notices, (seed, "skipped a notice that was never posted")
                bump("incomplete_skipped"); return
            notices.append(bd.week_of(t))   # post the Incomplete notice once, then record it
            code, out, err = call(["--confirm-posted", nw["period_key"], "--as-of", as_of.isoformat(), "--log", log])
            assert code == 0 and out.startswith("Recorded:"), (seed, code, out, err)
            bump("incomplete"); return
        if out.startswith("Skipped:"):
            bump("skipped"); return
        key = nw["period_key"]
        staged = [r for r in load()["runs"] if r["period_key"] == key][-1]["staged"]
        if post_fails:
            bump("post_failed"); return
        arrived.append((key, bd.week_of(t), staged))
        if lose_confirm:
            bump("lost_confirm"); return
        code, out, err = call(["--confirm-posted", key, "--as-of", as_of.isoformat(), "--log", log])
        assert code == 0, err
        assert call(["--render-log", "--as-of", as_of.isoformat(), "--log", log])[0] == 0   # update the doc
        bump("posted")

    monday = date(2026, 10, 5)
    for w in range(weeks):   # the meetings exist first; each run sees only those that started before its --as-of
        week = monday + timedelta(days=7 * w)
        for day in range(5):
            if rnd.random() < 0.6:
                n += 1
                off = timezone(timedelta(hours=rnd.choice(OFFSETS)))
                start = datetime.combine(week + timedelta(days=day), datetime.min.time(), off) + timedelta(hours=rnd.randint(7, 22))
                mid = "s%d-m%d" % (seed, n)
                its = []
                for k in range(rnd.randint(1, 2)):
                    n += 1
                    kind = "decision" if rnd.random() < 0.75 else "proposal"
                    its.append({"n": n, "meeting_id": mid, "start": start, "kind": kind, "topic": rnd.choice(TOPICS),
                                "statement": "Item %d %s" % (n, kind), "quote": "We settled item %d today." % n,
                                "owner": rnd.choice(["Sam Kim", None]),
                                "link": rnd.choice(["supersedes", "supersedes", "same_as", "none"])})
                meetings.append({"meeting_id": mid, "title": "Meeting %d" % n, "start": start,
                                 "text": " ".join("Speaker: " + i["quote"] for i in its)})
                items.extend(its)
    for w in range(weeks):
        week = monday + timedelta(days=7 * w)
        # midweek on-request run with replies
        if rnd.random() < 0.4:
            as_of = datetime.combine(week + timedelta(days=2), datetime.min.time(), AGENT_TZ) + timedelta(hours=12)
            state = load()
            actions = []
            status, _ = bd.statuses(state["records"]) if state["records"] else ({}, {})
            touched = set()   # one reply per entry per run, as a person would give them
            for rid in sorted(state["records"]):
                rec = state["records"][rid]
                if bd.eff_date(rec) > as_of.date() or rid in touched:
                    continue
                before = len(actions)
                if status.get(rid) == "active" and not rec["owner"] and rnd.random() < 0.5:
                    actions.append({"decision_id": rid, "action": "set_owner", "owner": "Priya Raman", "date": as_of.date().isoformat()})
                elif status.get(rid) == "open" and rnd.random() < 0.2:
                    actions.append({"decision_id": rid, "action": "confirm", "date": as_of.date().isoformat()})
                elif status.get(rid) == "active" and rnd.random() < 0.05:
                    actions.append({"decision_id": rid, "action": "dismiss", "date": as_of.date().isoformat(), "note": "not a decision"})
                elif status.get(rid) == "dismissed" and rnd.random() < 0.3:
                    actions.append({"decision_id": rid, "action": "restore", "date": as_of.date().isoformat(), "note": "it was real"})
                if len(actions) > before:
                    touched.add(rid)
                    continue
                for c in rec["conflicts"]:
                    if c["resolution"] is None and status.get(rid) == "active" and status.get(c["with"]) == "active" \
                            and c["with"] not in touched and rnd.random() < 0.5:
                        actions.append({"decision_id": rid, "action": "resolve", "other_id": c["with"], "resolution":
                                        rnd.choice(["both_stand", "supersedes"]), "date": as_of.date().isoformat()})
                        touched.update([rid, c["with"]])
                        break
            path = bundle(as_of.date() - timedelta(days=6), as_of.date(), as_of, "on_request", actions=actions)
            code, out, err = call(["--bundle", path, "--as-of", as_of.isoformat(), "--log", log])
            assert code == 0, (seed, err)
            bump("on_request")
            check("on-request week %d" % w)
        fri = datetime.combine(week + timedelta(days=4), datetime.min.time(), AGENT_TZ) + timedelta(hours=rnd.randint(15, 17), minutes=rnd.choice([0, 30]))
        r = rnd.random()
        scheduled(fri, status="unavailable" if r < 0.15 else "ok", post_fails=0.15 <= r < 0.3, lose_confirm=0.3 <= r < 0.4)
        check("friday week %d" % w)
        if r < 0.15 or rnd.random() < 0.35:
            extra = fri + timedelta(hours=rnd.choice([1, 18, 56]))   # same evening, Saturday, or Monday 00:xx
            scheduled(extra, status="unavailable" if r < 0.15 and rnd.random() < 0.6 else "ok")   # the outage may last
            check("extra trigger week %d" % w)
    # a final clean run in a quiet week flushes everything
    end = datetime.combine(monday + timedelta(days=7 * weeks + 4), datetime.min.time(), AGENT_TZ) + timedelta(hours=16)
    scheduled(end)
    scheduled(end + timedelta(days=7))
    check("final")
    state = load()
    for it in items:
        assert len(where(it, state["records"])) == 1, (seed, "lost or duplicated at the end", it["statement"])
    status, _ = bd.statuses(state["records"])
    for rid, rec in state["records"].items():
        bump("final_" + status[rid])
        if rec["confirmed"]:
            bump("final_confirmed")
        if rec["mentions"]:
            bump("final_repeated")
        if any(h["action"] == "restore" for h in rec["history"]):
            bump("final_restored")
        if any(c["resolution"] for c in rec["conflicts"]):
            bump("final_conflict_resolved")
    return events, len(items)

total = {}
count = 0
for seed in range(1, 9):
    events, n = simulate(seed, 10)
    count += n
    for k, v in events.items():
        total[k] = total.get(k, 0) + v
print("scenario harness: 8 seeds x 10 weeks, %d extracted items, events %s" % (count, sorted(total.items())))
for k in ["posted", "incomplete", "incomplete_skipped", "post_failed", "resumed", "lost_confirm", "late_confirm", "already_posted", "on_request",
          "final_superseded", "final_decided", "final_dismissed", "final_restored", "final_open", "final_confirmed", "final_repeated",
          "final_conflict_resolved"]:
    assert total.get(k), "scenario never exercised " + k
PY
ok "scenario harness: weekly recipe over many simulated weeks keeps every invariant"

echo "PASS $PASS/$PASS"

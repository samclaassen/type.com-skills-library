#!/usr/bin/env bash
# Guardrail and edge-case tests. Run from the skill directory: bash tests.sh
# Builds variants of the fictional sample bundle in a temp dir; never touches real data.
# PYTHON=python3.8 bash tests.sh runs the same suite on another interpreter.
set -euo pipefail

PY="${PYTHON:-python3}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
SCRIPT=build_digest.py
BASE=(--config digest.json --as-of 2026-10-05T08:00:00-07:00 --internal-domain tamsquill.example)
PASS=0
TOTAL=0

ok() { PASS=$((PASS + 1)); TOTAL=$((TOTAL + 1)); echo "ok $PASS - $1"; }
fail() { TOTAL=$((TOTAL + 1)); echo "FAIL - $1"; echo "PASS $PASS/$TOTAL"; exit 1; }

# variant NAME PYTHON_CODE : writes $TMP/NAME.json from the sample bundle after running the code on `b`
variant() {
  "$PY" - "$TMP/$1.json" "$2" <<'PY'
import json, sys
b = json.load(open("sample_bundle.json", encoding="utf-8"))
M = dict((m["meeting_id"], m) for m in b["meetings"])
I = dict((i["id"], i) for i in b["items"])
T = dict((t["key"], t) for t in b["themes"])
A = dict((a["key"], a) for a in b["accounts"])
exec(sys.argv[2])
json.dump(b, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)
PY
}

# expect CODE PATTERN STREAM NAME -- args...   (STREAM is out or err)
# A non-zero exit reported on stderr must leave stdout empty (nothing printed before a fatal error),
# and no run may ever print a Python traceback.
expect() {
  local code="$1" pattern="$2" stream="$3" name="$4"; shift 4
  set +e
  "$PY" "$SCRIPT" "$@" >"$TMP/out" 2>"$TMP/err"
  local got=$?
  set -e
  [[ "$got" == "$code" ]] || { cat "$TMP/out" "$TMP/err"; fail "$name: exit $got, expected $code"; }
  grep -qF -- "$pattern" "$TMP/$stream" || { cat "$TMP/out" "$TMP/err"; fail "$name: '$pattern' not in std$stream"; }
  ! grep -q "Traceback" "$TMP/err" || { cat "$TMP/err"; fail "$name: traceback"; }
  if [[ "$code" != 0 && "$stream" == err && -s "$TMP/out" ]]; then cat "$TMP/out"; fail "$name: printed to stdout before failing"; fi
  ok "$name"
}

# jcheck NAME PYTHON_ASSERTS -- args... : runs with --format json and evaluates asserts on `d`
jcheck() {
  local name="$1" code="$2"; shift 2
  "$PY" "$SCRIPT" "$@" --format json >"$TMP/out.json" 2>"$TMP/err" || { cat "$TMP/err"; fail "$name: non-zero exit"; }
  "$PY" - "$TMP/out.json" "$code" <<'PY' || fail "$name"
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
T = dict((t["key"], t) for t in d["all_themes"])
exec(sys.argv[2])
PY
  ok "$name"
}

# ---------------------------------------------------------------- sample run is byte-exact
bash run.sh >"$TMP/run.txt" 2>&1
cmp -s "$TMP/run.txt" expected_output.txt || { diff "$TMP/run.txt" expected_output.txt | head -20; fail "run.sh output differs from expected_output.txt"; }
ok "run.sh matches expected_output.txt byte for byte"
"$PY" "$SCRIPT" --bundle sample_bundle.json "${BASE[@]}" >"$TMP/a.md"
"$PY" "$SCRIPT" --bundle sample_bundle.json "${BASE[@]}" >"$TMP/b.md"
cmp -s "$TMP/a.md" "$TMP/b.md" || fail "two identical runs differ"
ok "deterministic: identical input gives identical output"

# ---------------------------------------------------------------- input validation (exit 2, clear message)
printf '{"schema_version": 1,,}' >"$TMP/broken.json"
expect 2 "is not valid JSON (line 1" err "malformed JSON rejected with line/column" --bundle "$TMP/broken.json" "${BASE[@]}"
expect 2 "file not found" err "missing bundle file rejected" --bundle "$TMP/nope.json" "${BASE[@]}"
variant v_schema 'b["schema_version"] = 2'
expect 2 "\$.schema_version: must be 1" err "wrong schema_version rejected" --bundle "$TMP/v_schema.json" "${BASE[@]}"
variant no_period 'del b["period"]'
expect 2 "\$.period: required object is missing" err "missing period names the JSON path" --bundle "$TMP/no_period.json" "${BASE[@]}"
grep -qF "no_period.json" "$TMP/err" || fail "error does not name the bundle file"
ok "error names the bundle file"
variant bad_date 'b["period"]["start"] = "28/09/2026"'
expect 2 "is not a YYYY-MM-DD date" err "bad period date rejected" --bundle "$TMP/bad_date.json" "${BASE[@]}"
expect 2 "--as-of" err "naive --as-of rejected" --bundle sample_bundle.json --config digest.json \
  --as-of 2026-10-05T08:00:00 --internal-domain tamsquill.example
expect 2 "--internal-domain is required" err "missing internal domain rejected" \
  --bundle sample_bundle.json --config digest.json --as-of 2026-10-05T08:00:00-07:00
variant future 'b["period"]["end"] = "2026-10-11"'
expect 2 "cannot cover the future" err "period ending after --as-of rejected" --bundle "$TMP/future.json" "${BASE[@]}"
variant no_tracker 'del b["sources"]["issue_tracker"]'
expect 2 "\$.sources.issue_tracker: required object is missing" err "missing source status rejected" \
  --bundle "$TMP/no_tracker.json" "${BASE[@]}"
variant bad_status 'b["sources"]["crm"]["status"] = "broken"'
expect 2 "\$.sources.crm.status: must be one of" err "unknown source status rejected" --bundle "$TMP/bad_status.json" "${BASE[@]}"
variant orphan 'I["i01"]["meeting_id"] = "sample-zz"'
expect 2 "every item must cite a meeting" err "item citing an unknown meeting rejected" --bundle "$TMP/orphan.json" "${BASE[@]}"
variant no_theme 'I["i01"]["theme"] = "not-defined"'
expect 2 "is not defined in \$.themes" err "item with undefined theme rejected" --bundle "$TMP/no_theme.json" "${BASE[@]}"
variant role_email 'I["i01"]["evidence"]["speaker_role"] = "priya@outfitters.example"'
expect 2 "never an email, phone number or link" err "speaker role with an email rejected" --bundle "$TMP/role_email.json" "${BASE[@]}"
variant role_phone 'I["i01"]["evidence"]["speaker_role"] = "Ops, call +1 (555) 010-2030"'
expect 2 "never an email, phone number or link" err "speaker role with a phone number rejected" --bundle "$TMP/role_phone.json" "${BASE[@]}"
variant internal_speaker 'I["i01"]["evidence"]["speaker_side"] = "internal"'
expect 2 "internal speakers are not customer feedback" err "internal speaker rejected" --bundle "$TMP/internal_speaker.json" "${BASE[@]}"
variant denied_quote 'I["i08"]["evidence"]["kind"] = "transcript"'
expect 2 "has transcript status 'denied'" err "transcript quote from a denied transcript rejected" --bundle "$TMP/denied_quote.json" "${BASE[@]}"
variant no_comp 'del I["i14"]["competitor"]'
expect 2 "\$.items[13].competitor: is required" err "competitor item without competitor name rejected" --bundle "$TMP/no_comp.json" "${BASE[@]}"
variant long_quote 'I["i01"]["evidence"]["text"] = "word " * 120'
expect 2 "keep quotes to the relevant sentence" err "transcript dump as quote rejected" --bundle "$TMP/long_quote.json" "${BASE[@]}"
variant no_account 'del M["sample-m03"]["account_key"]'
expect 2 "account_key: is required for a customer meeting" err "customer meeting without account rejected" --bundle "$TMP/no_account.json" "${BASE[@]}"
variant bad_account 'M["sample-m03"]["account_key"] = "ghost.example"'
expect 2 "is not defined in \$.accounts" err "undefined account key rejected" --bundle "$TMP/bad_account.json" "${BASE[@]}"
variant dup_meeting 'b["meetings"].append(dict(M["sample-m03"]))'
expect 2 "duplicate meeting_id" err "duplicate meeting id rejected" --bundle "$TMP/dup_meeting.json" "${BASE[@]}"
variant bad_conf 'T["bulk-csv-import"]["issue_matches"][0]["confidence"] = "certain"'
expect 2 "confidence: must be one of high, medium, low" err "unknown match confidence rejected" --bundle "$TMP/bad_conf.json" "${BASE[@]}"
variant dup_disagree 'M["sample-m02"]["account_key"] = "kestrovane.example"'
expect 2 "disagrees with" err "two notes of one meeting filed under different accounts rejected" --bundle "$TMP/dup_disagree.json" "${BASE[@]}"

# ---------------------------------------------------------------- empty input
variant empty 'b["meetings"] = []; b["items"] = []; b["themes"] = []; b["accounts"] = []'
expect 0 "No external customer or prospect meetings found in this period" out "empty bundle says so plainly" \
  --bundle "$TMP/empty.json" "${BASE[@]}"
grep -qF -- "- None this period: no theme was raised by 2 or more accounts." "$TMP/out" || fail "empty top themes line missing"
grep -qF -- "- Meetings listed: 0." "$TMP/out" || fail "empty coverage line missing"
ok "empty bundle still renders coverage"

# ---------------------------------------------------------------- distinct accounts, dedupe
jcheck "distinct accounts: bulk CSV counts 4 accounts, not 6 raw mentions" \
  'th = T["bulk-csv-import"]; assert th["account_count"] == 4, th; assert th["mentions"] == 5, th; assert th["meeting_count"] == 4, th' \
  --bundle sample_bundle.json "${BASE[@]}"
variant repeat 'import copy
for n, mid in enumerate(["sample-m03", "sample-m03", "sample-m03"]):
    it = copy.deepcopy(I["i06"]); it["id"] = "r%d" % n; it["evidence"]["text"] = "SSO again, take %d" % n; b["items"].append(it)'
jcheck "one account repeating a theme in one meeting stays 1 account and 1 deduped item" \
  'th = T["sso-saml"]; assert th["account_count"] == 2, th; assert d["coverage"]["items_deduped"] == 15, d["coverage"]' \
  --bundle "$TMP/repeat.json" "${BASE[@]}"
variant many_meetings 'import copy
for n in range(3):
    m = copy.deepcopy(M["sample-m05"]); m["meeting_id"] = "bw-%d" % n; m["url"] = "https://notes.granola.ai/d/sample-bw%d" % n
    m["start"] = "2026-10-0%dT09:00:00-07:00" % (n + 2); b["meetings"].append(m)
    it = copy.deepcopy(I["i11"]); it["id"] = "bw%d" % n; it["meeting_id"] = m["meeting_id"]; b["items"].append(it)'
jcheck "same account across 4 meetings counts once (4 meetings, 1 account)" \
  'th = T["slack-alert-delay"]; assert th["account_count"] == 1, th; assert th["meeting_count"] == 4, th' \
  --bundle "$TMP/many_meetings.json" "${BASE[@]}"
jcheck "duplicate note of the same call is merged, not counted twice" \
  'c = d["coverage"]; assert c["buckets"]["duplicate_note"] == 1, c; assert c["items_raw"] == 17 and c["items_deduped"] == 15, c' \
  --bundle sample_bundle.json "${BASE[@]}"
variant dup_quote 'I["i03"]["evidence"] = dict(I["i01"]["evidence"]); I["i03"]["evidence"]["kind"] = "note"'
"$PY" "$SCRIPT" --bundle "$TMP/dup_quote.json" "${BASE[@]}" >"$TMP/out"
[[ "$(grep -c "re-types two hundred orders" "$TMP/out")" -eq 1 ]] || fail "identical quote from two notes shown twice"
ok "identical evidence captured in two notes is shown once"

# ---------------------------------------------------------------- meeting classification and coverage
expect 0 "- Skipped, internal-only (all attendees on tamsquill\\.example): 2." out "internal-only meetings (incl. subdomain) skipped and counted" \
  --bundle sample_bundle.json "${BASE[@]}"
! grep -qF "Weekly pipeline review" "$TMP/out" || fail "internal meeting title leaked into the digest"
ok "internal meeting titles are not listed"
grep -qF "Items dropped because their meeting was not scanned: 1 (not customer)." "$TMP/out" || fail "item from investor meeting not reported as dropped"
! grep -qF "investor updates" "$TMP/out" || fail "item from a non-customer meeting was used"
ok "items from non-customer meetings are dropped and reported"
grep -qF "Attendees unknown (only the note taker listed; not scanned): [Coffee chat, Oct 3]" "$TMP/out" || fail "attendees-unknown meeting not listed"
ok "meeting with only the note taker is listed as attendees unknown"
grep -qF "(not \"not discussed\"): [Plimsoll & Quist discovery, Oct 1]" "$TMP/out" || fail "denied transcript not listed in coverage"
ok "denied transcript listed in coverage, never read as not discussed"
variant personal_addr 'M["sample-m07"]["attendees"].append({"email": "dana.personal@freemail.example"})'
expect 0 "- Skipped, internal-only (all attendees on tamsquill\\.example): 2." out "--internal-email keeps a colleague's personal address internal" \
  --bundle "$TMP/personal_addr.json" "${BASE[@]}" --internal-email dana.personal@freemail.example
expect 0 "External, relationship not classified" out "without --internal-email that meeting is external and unclassified" \
  --bundle "$TMP/personal_addr.json" "${BASE[@]}"
variant bot_only 'M["sample-m08"]["attendees"].append({"email": "notetaker+abc@recorder.example"})'
expect 0 "- Skipped, internal-only (all attendees on tamsquill\\.example): 2." out "note-taker bots are ignored when classifying" \
  --bundle "$TMP/bot_only.json" "${BASE[@]}"
variant unclassified 'del M["sample-m11"]["kind"]; b["items"] = [i for i in b["items"] if i["meeting_id"] != "sample-m11"]'
expect 0 "External, relationship not classified (not scanned — classify and rerun): [Quirnwell Studio demo, Oct 2]" out \
  "external meeting without a kind is listed, not scanned" --bundle "$TMP/unclassified.json" "${BASE[@]}"
variant outside 'M["sample-m05"]["start"] = "2026-09-20T14:00:00-07:00"'
expect 0 "- Outside the period (ignored): 1." out "meeting outside the period ignored" --bundle "$TMP/outside.json" "${BASE[@]}"
variant gaps 'b["date_gaps"] = [{"start": "2026-10-01", "end": "2026-10-02", "reason": "Granola desktop app was offline"}]; b["sources"]["meetings"]["history_days"] = 5'
expect 0 "- Date gaps: Oct 1 – Oct 2: Granola desktop app was offline; before Sep 30: notes older than 5 days may be unavailable on this plan." out \
  "reported and plan-history date gaps listed" --bundle "$TMP/gaps.json" "${BASE[@]}"
variant misfiled 'M["sample-m06"]["account_key"] = "brackenvolt.example"'
expect 0 "⚠️ Check attribution: [Kestrovane Supply renewal call, Oct 2](https://notes.granola.ai/d/sample-m06) is filed under Brackenvolt Labs, but external attendees are on kestrovane\\.example." out \
  "account/attendee domain mismatch flagged" --bundle "$TMP/misfiled.json" "${BASE[@]}"

# ---------------------------------------------------------------- quotes
"$PY" "$SCRIPT" --bundle sample_bundle.json "${BASE[@]}" >"$TMP/out"
! grep "Note excerpt" "$TMP/out" | grep -q "“" || fail "a note excerpt was put inside quote marks"
ok "note excerpts are never rendered inside quote marks"
variant wrapped 'I["i05"]["evidence"]["text"] = "“We are migrating three years of orders.”"'
"$PY" "$SCRIPT" --bundle "$TMP/wrapped.json" "${BASE[@]}" >"$TMP/out"
grep -qF -- "- “We are migrating three years of orders.” — Operations lead, Marrowby & Vane" "$TMP/out" || fail "wrapped quote not rendered once"
! grep -qF "““" "$TMP/out" || fail "doubled quote marks"
ok "surrounding quote marks in the bundle are not doubled"
"$PY" "$SCRIPT" --bundle sample_bundle.json "${BASE[@]}" >"$TMP/out"
grep -qF "Customer participant \\(speaker unconfirmed\\), Kestrovane Supply" "$TMP/out" || fail "unconfirmed speaker not labelled"
ok "unconfirmed speakers are labelled"
variant uni 'A["marrowbyvane.example"]["name"] = "Hårbor & Viñe 🍷"; I["i07"]["evidence"]["text"] = "Le rapport du vendredi — génial, ça m’épargne un après-midi."'
expect 0 "“Le rapport du vendredi — génial, ça m’épargne un après-midi.” — Operations lead, Hårbor & Viñe 🍷" out \
  "unicode names and quotes render intact" --bundle "$TMP/uni.json" "${BASE[@]}"

# ---------------------------------------------------------------- ranking and weighting
jcheck "default ranking: distinct accounts, then type priority" \
  'k = [t["key"] for t in d["top_themes"]]; assert k == ["bulk-csv-import", "sso-saml", "dashboard-load-time", "countwhistle-comparison", "weekly-report-automation"], k' \
  --bundle sample_bundle.json "${BASE[@]}"
jcheck "ARR weighting ranks by ARR of distinct accounts" \
  'k = [t["key"] for t in d["top_themes"]]; assert k == ["bulk-csv-import", "weekly-report-automation", "sso-saml", "dashboard-load-time", "countwhistle-comparison"], k; assert T["bulk-csv-import"]["arr_sum"] == 84000' \
  --bundle sample_bundle.json "${BASE[@]}" --weighting arr
jcheck "plan weighting uses plan_weights" \
  'assert T["sso-saml"]["score"] == 4.0, T["sso-saml"]; assert T["bulk-csv-import"]["score"] == 7.0, T["bulk-csv-import"]' \
  --bundle sample_bundle.json "${BASE[@]}" --weighting plan
expect 0 "ARR \\\$84,000 (known for 2 of 4)" out "ARR coverage is stated, unknown ARR not hidden" --bundle sample_bundle.json "${BASE[@]}" --weighting arr

# ---------------------------------------------------------------- issue matching and drafts
jcheck "drafts: unmatched 2-account theme and a paying customer's bug; none for medium/high matches, praise or competitors" \
  'assert [x["theme"] for x in d["drafts"]] == ["sso-saml", "slack-alert-delay"], d["drafts"]' \
  --bundle sample_bundle.json "${BASE[@]}"
variant prospect_bug 'A["brackenvolt.example"]["status"] = "prospect"'
jcheck "a single prospect's bug gets no draft" \
  'assert [x["theme"] for x in d["drafts"]] == ["sso-saml"], d["drafts"]' --bundle "$TMP/prospect_bug.json" "${BASE[@]}"
variant tracker_down 'b["sources"]["issue_tracker"] = {"provider": "linear", "status": "unavailable", "detail": "401 from Linear"}
for t in b["themes"]:
    t["issue_matches"] = []; t["tracker_search"] = {"status": "unavailable"}'
expect 0 "Issue: tracker not searched (401 from Linear)." out "tracker unavailable is stated per theme" --bundle "$TMP/tracker_down.json" "${BASE[@]}"
grep -qF "_Not checked for duplicates — the issue tracker was not searched for this theme._" "$TMP/out" || fail "draft not flagged as unchecked"
grep -qF "Gaps: Transcripts partial (1 meeting's workspace does not allow transcript access); Billing not configured (ARR taken from the CRM); Issue tracker unavailable (401 from Linear)." "$TMP/out" || fail "tracker gap missing from Gaps"
ok "drafts without a duplicate check are flagged and the gap is listed"
"$PY" "$SCRIPT" --bundle sample_bundle.json "${BASE[@]}" >"$TMP/out"
grep -qF "likely match (medium) — confirm before linking" "$TMP/out" || fail "medium match not labelled"
grep -qF "_Low-confidence candidate TAM-98 exists; check it before creating._" "$TMP/out" || fail "low match not surfaced on its draft"
ok "match confidence is shown"

# ---------------------------------------------------------------- trends against the previous digest
expect 0 "_No previous digest on record, so every theme is marked “first digest”; trends start next time._" out \
  "no state: first digest stated" --bundle sample_bundle.json "${BASE[@]}"
cp sample_state.json "$TMP/state.json"
jcheck "trend labels: growing, new, returning, recurring" \
  'assert T["bulk-csv-import"]["trend"] == "growing" and T["bulk-csv-import"]["delta"] == 1
assert T["sso-saml"]["trend"] == "new"
assert T["countwhistle-comparison"]["trend"] == "returning"
assert T["weekly-report-automation"]["trend"] == "recurring"' \
  --bundle sample_bundle.json "${BASE[@]}" --state "$TMP/state.json"
expect 0 "- Offline mode on mobile (was 2 accounts)" out "themes that went quiet are listed" \
  --bundle sample_bundle.json "${BASE[@]}" --state "$TMP/state.json"
cmp -s sample_state.json "$TMP/state.json" || fail "an on-request run wrote the state file"
ok "on-request runs never write the state file"
expect 0 "bulk-csv-import | Bulk CSV import for orders | last seen 2026-W39" out "--known-themes lists reusable theme keys" \
  --state "$TMP/state.json" --known-themes

# ---------------------------------------------------------------- scheduled idempotency
expect 2 "--scheduled needs --state" err "--scheduled without a state file rejected" --bundle sample_bundle.json "${BASE[@]}" --scheduled
cp sample_state.json "$TMP/s.json"
expect 0 "# Customer Feedback Digest — 2026-W40" out "dry-run scheduled run renders" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/s.json" --dry-run
cmp -s sample_state.json "$TMP/s.json" || fail "--dry-run wrote the state file"
ok "--dry-run leaves the state file unchanged"
expect 0 "# Customer Feedback Digest — 2026-W40" out "first scheduled run renders and records the week" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/s.json"
"$PY" -c 'import json,sys; d=json.load(open(sys.argv[1])); w=[x for x in d["digests"] if x["week"]=="2026-W40"]; assert len(w)==1 and w[0]["status"]=="pending", w' "$TMP/s.json" || fail "week not recorded as pending"
ok "week recorded once as pending"
expect 4 "never confirmed as posted" err "F7: rerunning a pending week exits 4 and prints nothing" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/s.json"
expect 4 "--resume-pending" err "F7: pending week refused on a dry run too" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/s.json" --dry-run
expect 0 "# Customer Feedback Digest — 2026-W40" out "F7: --resume-pending re-renders a pending week" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/s.json" --resume-pending
grep -qF "Resuming pending 2026-W40" "$TMP/err" || fail "resume did not warn to check the destination"
ok "F7: resume warns to check the destination first"
"$PY" -c 'import json,sys; d=json.load(open(sys.argv[1])); w=[x for x in d["digests"] if x["week"]=="2026-W40"]; assert len(w)==1 and w[0]["status"]=="pending", w' "$TMP/s.json" || fail "resume duplicated or changed the state entry"
ok "rerun does not duplicate the week in state"
expect 2 "--resume-pending only applies" err "--resume-pending without --scheduled rejected" --bundle sample_bundle.json "${BASE[@]}" --resume-pending
expect 2 "no digest recorded for 2026-W41" err "confirming an unrecorded week rejected" --state "$TMP/s.json" --confirm-posted 2026-W41 --as-of 2026-10-05T08:01:00-07:00
expect 2 "must be an ISO week" err "bad week format rejected" --state "$TMP/s.json" --confirm-posted 2026-40 --as-of 2026-10-05T08:01:00-07:00
expect 0 "Confirmed: 2026-W40 marked as posted." out "confirm posted" --state "$TMP/s.json" --confirm-posted 2026-W40 --as-of 2026-10-05T08:01:00-07:00 --post-ref https://slack.example/p
expect 0 "Already confirmed" out "confirming twice is harmless" --state "$TMP/s.json" --confirm-posted 2026-W40 --as-of 2026-10-05T08:02:00-07:00
expect 0 "Skipped: customer feedback digest for 2026-W40 already posted" out "scheduled rerun after posting is skipped" \
  --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/s.json"
[[ "$(wc -l <"$TMP/out")" -eq 1 ]] || fail "skip notice is not a single line"
ok "skip notice is one line"
expect 0 "# Customer Feedback Digest — 2026-W40" out "a person asking again still gets the digest" --bundle sample_bundle.json "${BASE[@]}" --state "$TMP/s.json"
variant next_week 'from datetime import datetime, timedelta
b["period"] = {"start": "2026-10-05", "end": "2026-10-11"}
for m in b["meetings"]:
    m["start"] = (datetime.fromisoformat(m["start"]) + timedelta(days=7)).isoformat()'
jcheck "next week compares against the week just posted" \
  'assert d["week"] == "2026-W41"; assert T["sso-saml"]["trend"] == "recurring", T["sso-saml"]' \
  --bundle "$TMP/next_week.json" --config digest.json --as-of 2026-10-12T08:00:00-07:00 --internal-domain tamsquill.example --state "$TMP/s.json"
echo "not json" >"$TMP/bad_state.json"
expect 2 "refusing to run blind" err "unreadable state refuses to run" --bundle sample_bundle.json "${BASE[@]}" --state "$TMP/bad_state.json" --scheduled
echo '{"digests": []}' >"$TMP/foreign.json"
expect 2 "not a customer-feedback-digest state file" err "foreign state file rejected" --bundle sample_bundle.json "${BASE[@]}" --state "$TMP/foreign.json"

# ---------------------------------------------------------------- output formats
expect 0 "Full digest with quotes and coverage: https://type.com/sample-doc" out "slack summary links the doc" \
  --bundle sample_bundle.json "${BASE[@]}" --format slack --doc-url https://type.com/sample-doc
! grep -qF "“" "$TMP/out" || fail "slack summary should not carry quotes"
ok "slack summary carries no customer quotes"
jcheck "json output has markdown, coverage and drafts" \
  'assert d["status"] == "rendered" and d["markdown"].startswith("# Customer Feedback Digest"); assert d["coverage"]["scanned"] == 6' \
  --bundle sample_bundle.json "${BASE[@]}"

# ---------------------------------------------------------------- top-theme cap
"$PY" -c 'import json; c = json.load(open("digest.json", encoding="utf-8")); c["max_top_themes"] = 1; json.dump(c, open("'"$TMP"'/cap.json", "w", encoding="utf-8"))'
expect 0 "…and 4 more themes raised by 2+ accounts" out "themes over the top-theme cap are counted, not dropped" \
  --bundle sample_bundle.json --config "$TMP/cap.json" --as-of 2026-10-05T08:00:00-07:00 --internal-domain tamsquill.example
"$PY" - "$TMP/out" <<'PY' || fail "a multi-account theme over the cap is listed as a single mention"
import sys
text = open(sys.argv[1], encoding="utf-8").read()
singles = text.split("## Notable single mentions", 1)[1].split("\n## ", 1)[0]
assert all("**" + t not in singles for t in ["Single sign-on", "Dashboards slow", "Compared with Countwhistle", "Weekly report"]), singles
PY
ok "multi-account themes over the cap never appear as single mentions"

# ---------------------------------------------------------------- QA round 1 regressions
# F1 / X1: every interpolated value is escaped for Slack and markdown; only validated http(s) links are built.
variant inject 'T["bulk-csv-import"]["title"] = "<!channel> Q3 sync"
A["kestrovane.example"]["name"] = "<https://evil.example|click>"
M["sample-m06"]["title"] = "<!everyone> renewal <@U123> <#C999|ops>"
T["dashboard-load-time"]["issue_matches"][0]["state"] = "<!here>"
T["dashboard-load-time"]["issue_matches"][0]["id"] = "TAM-120|<!channel>"
T["dashboard-load-time"]["issue_matches"][0]["title"] = "[click](javascript:alert(1))"
T["sso-saml"]["description"] = "Needs SSO.\n## Injected heading ![x](https://evil.example/p.png) <img src=x onerror=alert(1)>"
T["slack-alert-delay"]["title"] = "Alerts ``` late"
I["i14"]["competitor"] = "Evil | <b>x</b>"
I["i05"]["evidence"]["speaker_role"] = "<script>alert(1)</script>"
I["i06"]["evidence"]["text"] = "We need [SSO](https://evil.example) before <!channel> signs off."
b["sources"]["transcripts"]["detail"] = "<!subteam^S1> denied"'
"$PY" "$SCRIPT" --bundle "$TMP/inject.json" "${BASE[@]}" --format slack --doc-url "https://type.com/sample-doc?a=1&b=2" >"$TMP/slack.txt" || fail "F1: slack render failed"
"$PY" - "$TMP/slack.txt" <<'PY' || fail "F1: Slack output contains a live control sequence or an unvalidated link"
import re, sys
out = open(sys.argv[1], encoding="utf-8").read()
for token in re.findall(r"<([^>]*)>", out):
    assert re.match(r"^https://(notes\.granola\.ai|issues\.tamsquill\.example)/[^|<>]*\|[^|<>]*$", token), token
for bad in ["<!channel", "<!everyone", "<!here", "<@U", "<#C", "<!subteam", "<https://evil"]:
    assert bad not in out, bad
assert "&lt;!channel&gt; Q3 sync" in out and "&lt;https:\u2215\u2215evil\u2024example|click&gt;" in out, out
assert "sample-doc?a=1&amp;b=2" in out, out
PY
ok "F1: Slack output escapes <!channel>, <@U…>, <#C…> and injected <url|label> in every field"
"$PY" "$SCRIPT" --bundle "$TMP/inject.json" "${BASE[@]}" >"$TMP/inj.md" || fail "F1: markdown render failed"
"$PY" - "$TMP/inj.md" <<'PY' || fail "F1: markdown output contains raw HTML, an injected link or a broken fence"
import re, sys
out = open(sys.argv[1], encoding="utf-8").read()
assert "<" not in out and ">" not in out.replace("&gt;", ""), [l for l in out.splitlines() if "<" in l][:3]
for target in re.findall(r"(?<!\\)\]\(([^)]*)\)", out):
    assert re.match(r"^https://(notes\.granola\.ai|issues\.tamsquill\.example)/", target), target
assert "javascript:" not in re.sub(r"\\\[click\\\]\\\(javascript:alert\\\(1\\\)\\\)", "", out)
assert "\n## Injected" not in out and "\\[SSO\\]\\(https\\://evil\\.example\\)" in out
assert "| Evil \\| &lt;b&gt;x&lt;/b&gt; |" in out, [l for l in out.splitlines() if "Evil" in l]
fences = [l for l in out.splitlines() if l.startswith("```")]
assert fences and len(fences) % 2 == 0 and "Alerts \\`\\`\\` late" in out, fences
PY
ok "F1: markdown escapes HTML, link/image syntax, table pipes and code fences in every field"

variant badurls 'M["sample-m03"]["url"] = "javascript:alert(1)"
M["sample-m05"]["url"] = "https://user@evil.example/x"
T["bulk-csv-import"]["issue_matches"][0]["url"] = "https://issues.tamsquill.example/TAM 142 (a)<b>|c\""
T["dashboard-load-time"]["issue_matches"][0]["url"] = "data:text/html,<b>x</b>"'
expect 0 "Links dropped (not an http(s) URL, or unsafe): 3" out "X1: javascript:, data: and userinfo URLs dropped and counted" \
  --bundle "$TMP/badurls.json" "${BASE[@]}"
grep -qF "(https://issues.tamsquill.example/TAM%20142%20%28a%29%3Cb%3E%7Cc%22)" "$TMP/out" || fail "unsafe URL characters not percent-encoded"
! grep -qE "javascript:|data:text|user@evil" "$TMP/out" || fail "a rejected URL was printed"
grep -qF -- "- “The Friday report alone saves me most of an afternoon.” — Operations lead, Marrowby & Vane · Marrowby & Vane weekly sync, Sep 30" "$TMP/out" || fail "citation text not kept when its link is dropped"
ok "X1: unsafe characters percent-encoded; dropped links keep their citation text"
expect 2 "--doc-url must be an http(s) link" err "X1: javascript: --doc-url rejected before printing" \
  --bundle sample_bundle.json "${BASE[@]}" --format slack --doc-url "javascript:alert(1)"

# F2: one company = one account (shared domain, or the same name ignoring case).
variant dup_domain 'b["accounts"].append({"key": "outfitters-west", "name": "Outfitters West", "domains": ["Outfitters.example"]})'
expect 2 "one company must be one account" err "F2: two account keys sharing a domain rejected" --bundle "$TMP/dup_domain.json" "${BASE[@]}"
variant dup_name 'b["accounts"].append({"key": "eoc.example", "name": "example OUTFITTERS co", "domains": ["eoc.example"]})'
expect 2 "name 'example outfitters co' is already used by \$.accounts[0]" err "F2: names differing only by case rejected" --bundle "$TMP/dup_name.json" "${BASE[@]}"

# F3: emails and phone numbers are redacted from evidence (and every displayed field), issue drafts included.
variant pii 'I["i06"]["evidence"]["text"] = "Email rae@marrowbyvane.example or call +1 (555) 010-2030 before we add seats; we went live 2026-09-28."
T["sso-saml"]["description"] = "Security reviews (ask sec@plimsollquist.example, 555.010.4477) require SAML."
M["sample-m03"]["title"] = "Sync with rae@marrowbyvane.example"'
"$PY" "$SCRIPT" --bundle "$TMP/pii.json" "${BASE[@]}" >"$TMP/pii.md" || fail "F3: render failed"
! grep -qE "rae@|sec@|555" "$TMP/pii.md" || fail "F3: email or phone number reached the markdown digest"
"$PY" - "$TMP/pii.md" <<'PY' || fail "F3: issue draft contains contact details or lost the redaction marker"
import sys
out = open(sys.argv[1], encoding="utf-8").read()
draft = out.split("### D1.", 1)[1].split("### D2.", 1)[0]
assert "\\[email removed\\]" in draft and "\\[phone removed\\]" in draft, draft
assert "@" not in draft and "555" not in draft and "we went live 2026-09-28" in draft, draft
PY
ok "F3: issue drafts never carry emails or phone numbers; dates are not mistaken for phones"
jcheck "F3: JSON evidence is redacted too" \
  'e = [x for x in T["sso-saml"]["evidence"] if "seats" in x["text"]][0]; assert "[email removed]" in e["text"] and "[phone removed]" in e["text"], e' \
  --bundle "$TMP/pii.json" "${BASE[@]}"

# F4 / X2: strict dates on every Python version; NaN/Infinity, wrong types and missing lists are clean exit 2.
variant compact 'M["sample-m01"]["start"] = "20260929T100000-0700"'
expect 2 "\$.meetings[0].start: '20260929T100000-0700' is not a datetime like" err "F4: compact datetime rejected cleanly" --bundle "$TMP/compact.json" "${BASE[@]}"
variant isoweek 'M["sample-m01"]["start"] = "2026-W40-2T10:00:00-07:00"'
expect 2 "is not a datetime like" err "F4: ISO-week datetime rejected cleanly" --bundle "$TMP/isoweek.json" "${BASE[@]}"
variant isoweek_day 'b["period"]["start"] = "2026-W40-1"'
expect 2 "is not a YYYY-MM-DD date" err "F4: ISO-week period date rejected" --bundle "$TMP/isoweek_day.json" "${BASE[@]}"
variant wide 'b["period"]["start"] = "２０２６-09-28"'
expect 2 "is not a YYYY-MM-DD date" err "X2: non-ASCII digits rejected" --bundle "$TMP/wide.json" "${BASE[@]}"
variant feb30 'M["sample-m01"]["start"] = "2026-02-30T10:00:00Z"'
expect 2 "is not a real date and time" err "X2: impossible date rejected" --bundle "$TMP/feb30.json" "${BASE[@]}"
variant frac 'M["sample-m01"]["start"] = "2026-09-29T10:00:00.5-07:00"; M["sample-m02"]["start"] = "2026-09-29T10:00-07:00"'
expect 0 "Meetings listed: 11, 1 duplicate note" out "X2: short fraction and HH:MM accepted identically on every version" --bundle "$TMP/frac.json" "${BASE[@]}"
expect 2 "--as-of: '20261005T080000Z' is not a datetime" err "F4: compact --as-of rejected" \
  --bundle sample_bundle.json --config digest.json --as-of 20261005T080000Z --internal-domain tamsquill.example
for lit in NaN Infinity -Infinity 1e999; do
  sed "s/\"arr\": 24000/\"arr\": $lit/" sample_bundle.json >"$TMP/nan.json"
  expect 2 "is not allowed (numbers must be finite)" err "X2: $lit rejected" --bundle "$TMP/nan.json" "${BASE[@]}"
done
"$PY" -c 'import sys; s=open("sample_bundle.json",encoding="utf-8").read(); open(sys.argv[1],"w",encoding="utf-8").write(s.replace("\"arr\": 24000", "\"arr\": 1" + "0"*5000))' "$TMP/bigint.json"
expect 2 "bigint.json" err "X2: 5000-digit number rejected the same way on every version" --bundle "$TMP/bigint.json" "${BASE[@]}"
variant sv_true 'b["schema_version"] = True'
expect 2 "\$.schema_version: must be 1" err "X2: schema_version true is not 1" --bundle "$TMP/sv_true.json" "${BASE[@]}"
variant no_meetings 'del b["meetings"]'
expect 2 "\$.meetings: is required" err "X2: missing meetings list is an error, not an empty digest" --bundle "$TMP/no_meetings.json" "${BASE[@]}"
for mut in 'M["sample-m01"]["meeting_id"] = 5' 'M["sample-m01"]["attendees"] = "dana"' 'I["i01"]["evidence"] = ["x"]' \
           'b["themes"] = {}' 'T["sso-saml"]["tracker_search"] = "searched"' 'A["outfitters.example"]["domains"] = "outfitters.example"' \
           'A["outfitters.example"]["arr"] = "24000"' 'I["i01"]["type"] = ["bug"]' 'b["period"] = []' 'b["date_gaps"] = [{"start": "2026-10-02", "end": "2026-10-01", "reason": "x"}]' \
           'M["sample-m01"]["attendees"][0]["email"] = "dana@<!channel>"' 'b["sources"]["meetings"]["history_days"] = 10**12'; do
  variant wrongtype "$mut"
  expect 2 "Error: bundle" err "X3: wrong type rejected cleanly: $mut" --bundle "$TMP/wrongtype.json" "${BASE[@]}"
done

# F5: quote marks are stripped only when they wrap the whole text and appear nowhere inside.
variant inner_quotes 'I["i05"]["evidence"]["text"] = "\"Fine,\" she said, \"really\""; I["i07"]["evidence"]["text"] = "\"The Friday report saves me an afternoon.\""'
"$PY" "$SCRIPT" --bundle "$TMP/inner_quotes.json" "${BASE[@]}" >"$TMP/out"
grep -qF -- "- “\"Fine,\" she said, \"really\"” — Operations lead" "$TMP/out" || fail "F5: verbatim quote with inner quote marks was altered"
grep -qF -- "- “The Friday report saves me an afternoon.” — Operations lead" "$TMP/out" || fail "F5: plain wrapping quotes not removed"
ok "F5: verbatim text with inner quote marks is kept exactly"

# F6: scheduled runs need a Monday-Sunday week; other ranges key on start..end and are never recorded.
variant rolling 'b["period"] = {"start": "2026-09-05", "end": "2026-10-04"}'
cp sample_state.json "$TMP/f6.json"
expect 2 "--scheduled needs a Monday-Sunday week" err "F6: scheduled 30-day period rejected before printing" --bundle "$TMP/rolling.json" "${BASE[@]}" --scheduled --state "$TMP/f6.json"
expect 0 "# Customer Feedback Digest — 2026-09-05..2026-10-04 (Sep 5 – Oct 4, 2026)" out "F6: a rolling period keys on start..end" \
  --bundle "$TMP/rolling.json" "${BASE[@]}" --state "$TMP/f6.json"
cmp -s sample_state.json "$TMP/f6.json" || fail "F6: rolling run wrote the state file"
expect 0 "# Customer Feedback Digest — 2026-W40" out "F6: the real weekly digest still runs after a rolling one" \
  --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/f6.json"
variant midweek 'b["period"] = {"start": "2026-09-29", "end": "2026-10-05"}'
expect 2 "--scheduled needs a Monday-Sunday week" err "F6: Tuesday-Monday period rejected for scheduled runs" --bundle "$TMP/midweek.json" \
  --config digest.json --as-of 2026-10-06T08:00:00-07:00 --internal-domain tamsquill.example --scheduled --state "$TMP/f6.json"

# X4: never record a week when the meeting list is not ok; never call incomplete coverage "nothing to report".
variant listing_down 'b["sources"]["meetings"] = {"provider": "granola", "status": "unavailable", "detail": "MCP timeout"}'
cp sample_state.json "$TMP/x4.json"
expect 5 "Not recorded: the customer feedback digest for 2026-W40 is incomplete because the meeting list (Granola) is unavailable" out \
  "X4: scheduled run with the meeting list down exits 5 and records nothing" --bundle "$TMP/listing_down.json" "${BASE[@]}" --scheduled --state "$TMP/x4.json"
cmp -s sample_state.json "$TMP/x4.json" || fail "X4: an incomplete week was recorded"
ok "X4: incomplete week leaves the state file unchanged"
variant listing_empty 'b["sources"]["meetings"]["status"] = "partial"; b["meetings"] = []; b["items"] = []'
expect 0 "does not mean there were none" out "X4: on-request run with a partial list never says nothing to report" --bundle "$TMP/listing_empty.json" "${BASE[@]}"
grep -qF "**⚠️ Coverage incomplete**" "$TMP/out" || fail "incomplete banner missing"
! grep -qF "No external customer or prospect meetings found" "$TMP/out" || fail "X4: incomplete coverage reported as nothing found"
ok "X4: incomplete coverage is bannered"

# X4: exclusive lock file around the state read-modify-write.
cp sample_state.json "$TMP/lk.json"
echo "999 test" >"$TMP/lk.json.lock"
expect 3 "another run is updating the state file" err "X4: a held lock stops a scheduled run (exit 3, nothing printed)" \
  --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/lk.json"
expect 3 "another run is updating the state file" err "X4: a held lock stops --confirm-posted" --state "$TMP/lk.json" --confirm-posted 2026-W39 --as-of 2026-10-05T08:01:00-07:00
touch -d "20 minutes ago" "$TMP/lk.json.lock"
expect 0 "# Customer Feedback Digest — 2026-W40" out "X4: a lock older than 15 minutes is treated as stale" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/lk.json"
[[ ! -e "$TMP/lk.json.lock" ]] || fail "lock not released"
ok "X4: lock released after the run"
cp sample_state.json "$TMP/race.json"
for n in 1 2 3 4 5 6; do
  ( set +e; "$PY" "$SCRIPT" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/race.json" >"$TMP/race.$n.out" 2>"$TMP/race.$n.err"; echo $? >"$TMP/race.$n.code" ) &
done
wait
"$PY" - "$TMP" <<'PY' || fail "X4: concurrent scheduled runs both printed a digest"
import json, sys
t = sys.argv[1]
codes = [int(open("%s/race.%d.code" % (t, n)).read()) for n in range(1, 7)]
printed = [n for n in range(1, 7) if open("%s/race.%d.out" % (t, n), encoding="utf-8").read()]
assert codes.count(0) == 1 and set(codes) <= {0, 3, 4} and len(printed) == 1, (codes, printed)
d = json.load(open("%s/race.json" % t))
assert [x["status"] for x in d["digests"] if x["week"] == "2026-W40"] == ["pending"], d
PY
ok "X4: six concurrent scheduled runs print exactly one digest"

# F8 / X3: state and config validated before printing; unwritable state is a clean exit 2.
mkdir -p "$TMP/ro"; cp sample_state.json "$TMP/ro/state.json"; chmod 555 "$TMP/ro"
if [[ "$(id -u)" != 0 ]]; then
  expect 2 "not writable" err "F8: unwritable state folder fails before anything is printed" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/ro/state.json"
  expect 2 "not writable" err "F8: unwritable state fails --confirm-posted cleanly" --state "$TMP/ro/state.json" --confirm-posted 2026-W39 --as-of 2026-10-05T08:01:00-07:00
fi
chmod 755 "$TMP/ro"
expect 2 "does not exist" err "X3: state in a missing folder fails before printing" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/nowhere/state.json"
mkdir -p "$TMP/statedir"
expect 2 "is not a file" err "X3: a folder passed as --state is rejected" --bundle sample_bundle.json "${BASE[@]}" --state "$TMP/statedir"
for mut in 'd["digests"][0]["themes"]["bulk-csv-import"] = "x"' 'd["digests"][0]["themes"]["bulk-csv-import"]["accounts"] = "2"' \
           'd["digests"][0]["week"] = "2026-W41"' 'del d["digests"][0]["period_start"]' 'd["digests"][0]["status"] = "done"' \
           'd["digests"][0]["posted_at"] = "yesterday"' 'd["digests"].append(dict(d["digests"][0]))' 'd["digests"][0] = []' \
           'd["schema_version"] = True' 'd["digests"][0]["post_ref"] = 7' 'd["digests"][0]["themes"]["Bad Key"] = {"title": "x", "accounts": 1}'; do
  "$PY" -c 'import json,sys; d=json.load(open("sample_state.json")); exec(sys.argv[2]); json.dump(d, open(sys.argv[1],"w"))' "$TMP/bs.json" "$mut"
  expect 2 "Error: state" err "F8: malformed state rejected cleanly: $mut" --bundle sample_bundle.json "${BASE[@]}" --state "$TMP/bs.json"
done
sed 's/"accounts": 2,/"accounts": NaN,/' sample_state.json >"$TMP/bs.json"
expect 2 "refusing to run blind" err "F8: NaN in state rejected" --known-themes --state "$TMP/bs.json"
for mut in 'c["plan_weights"] = [1]' 'c["ignored_attendee_domains"] = "read.ai"' 'c["feedback_types"]["bug"] = 5' 'c["type_priority"] = "bug"' \
           'c["currency_symbol"] = 5' 'c["plan_weights"]["pro"] = -1' 'c["free_email_domains"] = ["gmail com"]' 'c["max_top_themes"] = 2.5' \
           'c["ignored_attendee_patterns"] = c.pop("ignored_attendee_domains")' 'c["draftable_types"] = None'; do
  "$PY" -c 'import json,sys; c=json.load(open("digest.json")); exec(sys.argv[2]); json.dump(c, open(sys.argv[1],"w"))' "$TMP/bc.json" "$mut"
  expect 2 "Error: config" err "F8: malformed config rejected cleanly: $mut" --bundle sample_bundle.json --config "$TMP/bc.json" --as-of 2026-10-05T08:00:00-07:00 --internal-domain tamsquill.example
done
expect 2 "--internal-domain must be a domain" err "X1: injected --internal-domain rejected" --bundle sample_bundle.json --config digest.json --as-of 2026-10-05T08:00:00-07:00 --internal-domain "<!channel>"

# F8: bot filter matches a whole domain (or subdomain) or a listed local part, never a substring.
# (Q4-F4: .example domains only; a bot domain "bot.example" stands in for read.ai.)
"$PY" -c 'import json,sys; c=json.load(open("digest.json")); c["ignored_attendee_domains"].append("bot.example"); json.dump(c, open(sys.argv[1],"w"))' "$TMP/botcfg.json"
BOTBASE=(--config "$TMP/botcfg.json" --as-of 2026-10-05T08:00:00-07:00 --internal-domain tamsquill.example)
variant spread 'M["sample-m08"]["attendees"].append({"email": "ops@robot.example"})'
expect 0 "External, relationship not classified (not scanned — classify and rerun): [Product standup, Oct 2]" out \
  "F8: robot.example is a person, not the bot.example bot" --bundle "$TMP/spread.json" "${BOTBASE[@]}"
variant readai 'M["sample-m08"]["attendees"].append({"email": "notes@eu.bot.example"}); M["sample-m07"]["attendees"].append({"email": "no-reply+cal@calendar.example"})'
expect 0 "- Skipped, internal-only (all attendees on tamsquill\\.example): 2." out "F8: bot subdomains and listed local parts are ignored" \
  --bundle "$TMP/readai.json" "${BOTBASE[@]}"

# F8: output does not depend on the hash seed.
PYTHONHASHSEED=1 "$PY" "$SCRIPT" --bundle sample_bundle.json "${BASE[@]}" --format json >"$TMP/h1.json"
PYTHONHASHSEED=2 "$PY" "$SCRIPT" --bundle sample_bundle.json "${BASE[@]}" --format json >"$TMP/h2.json"
PYTHONHASHSEED=3 "$PY" "$SCRIPT" --bundle "$TMP/inject.json" "${BASE[@]}" --format slack >"$TMP/h3.txt"
PYTHONHASHSEED=4 "$PY" "$SCRIPT" --bundle "$TMP/inject.json" "${BASE[@]}" --format slack >"$TMP/h4.txt"
cmp -s "$TMP/h1.json" "$TMP/h2.json" && cmp -s "$TMP/h3.txt" "$TMP/h4.txt" || fail "F8: output depends on PYTHONHASHSEED"
ok "F8: output is identical across hash seeds"

# ---------------------------------------------------------------- QA round 2 regressions
# R2-1: markdown escapes backslash first, then every markdown-active character; autolinks (scheme://, www., bare
# domains) are defused; Slack values cannot ping, link or open bold/italic/strike/code. Fuzzed across every field.
"$PY" - "$TMP" "$SCRIPT" <<'PY' || fail "R2-1: fuzz: an input value formed a working link, autolink, raw HTML, mention or formatting"
import json, re, subprocess, sys
tmp, script = sys.argv[1], sys.argv[2]
base = json.load(open("sample_bundle.json", encoding="utf-8"))
PAYLOADS = [
    "\\[click\\](https://phish.example/a)", "[click](https://phish.example/a) ![x](https://phish.example/p.png)",
    "<https://phish.example> <img src=x onerror=alert(1)> <b>bold</b>",
    "see https://phish.example/a or www.phish.example or phish.example/login or ftp://phish.example",
    "**bold** __u__ *it* _it_ ~~s~~ ~s~ `code` ```fence``` \\", "<!channel> <!here> <@U123> <#C1|ops> <!subteam^S1> @channel @here",
    "&lt;b&gt; &#91;x&#93;(https://phish.example) &amp; &#x5B;", "[ref]: https://phish.example \"t\"", "# Heading", "- item",
    "1. item", "> quote", "| a | b |", "---", "===", "a\\", "\\\\[x\\\\](https://phish.example)", "<https://phish.example|click>",
    "trailing *", "_", "x](https://phish.example)", "javascript:alert(1)", "[x]<https://phish.example>", "\\*not bold\\*",
    "+ plus", "2) item", "## Closing ##", "*", "~", "`", "Ünïcødé — “quotes” 😀",
    "cost $5 to $9 and hi", "$x^2$ and $$y$$", "==hi== and ==mark==", "a == b", "$51,000 | $72,000",
]

def fill(b, v, role):
    for n, a in enumerate(b["accounts"]):
        a["name"], a["segment"], a["plan"], a["arr_source"] = v("acct%s" % "abcdefghij"[n]), v("seg"), v("plan"), v("arrsrc")
    for n, m in enumerate(b["meetings"]):
        m["title"] = v("mtg%s" % "abcdefghijklmnop"[n])
    for t in b["themes"]:
        t["title"], t["description"] = v("theme"), v("desc")
        if t.get("tracker_search"):
            t["tracker_search"]["query"], t["tracker_search"]["detail"] = v("query"), v("detail")
        for mt in t.get("issue_matches", []):
            mt["id"], mt["title"], mt["state"], mt["reason"] = v("id"), v("issue"), v("state"), v("reason")
    for it in b["items"]:
        it["summary"] = v("summary")
        if it.get("competitor"):
            it["competitor"] = v("comp")
        it["evidence"]["text"] = v("quote")
        it["evidence"]["speaker_role"] = role
    for k, s in b["sources"].items():
        s["detail"] = v("src")
        if k in ("crm", "billing"):
            s["provider"] = v("prov")
    b["period"]["timezone_label"] = v("tz")
    b["date_gaps"] = [{"start": "2026-09-30", "end": "2026-09-30", "reason": v("gap")}]
    b["sources"]["issue_tracker"]["status"] = "partial"
    return b

def render(b, name, fmt):
    path = "%s/%s.json" % (tmp, name)
    json.dump(b, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    r = subprocess.run([sys.executable, script, "--bundle", path, "--config", "digest.json", "--as-of",
                        "2026-10-05T08:00:00-07:00", "--internal-domain", "tamsquill.example", "--format", fmt,
                        "--doc-url", "https://type.com/sample-doc"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert r.returncode == 0 and b"Traceback" not in r.stderr, (name, fmt, r.stderr.decode("utf-8", "replace"))
    return r.stdout.decode("utf-8")

PUNCT = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")

def unescape_marks(out):
    """Replace every backslash-escaped ASCII punctuation char with NUL, so only active characters remain."""
    res, i = [], 0
    while i < len(out):
        if out[i] == "\\" and i + 1 < len(out) and out[i + 1] in PUNCT:
            res.append("\x00"); i += 2
        else:
            res.append(out[i]); i += 1
    return "".join(res)

def md_counts(out):
    act = unescape_marks(out)
    c = dict((ch, act.count(ch)) for ch in "[]()<>*_`~|#@$=")
    c["scheme"] = len(re.findall(r"://", act))
    c["www"] = len(re.findall(r"www\.", act, re.I))
    c["domain-dot"] = len(re.findall(r"[^\W_]\.[^\W\d_]", act))
    c["entity"] = len(re.findall(r"&(?!lt;|gt;)#?\w+;", act))
    c["email"] = len(re.findall(r"\S@\S", act))
    c["block"] = len([l for l in act.split("\n") if re.match(r"\s*([-+=>#]|\d+[.)])", l)])
    c["link"] = len(re.findall(r"\]\(", act))
    return c

def slack_counts(out):
    c = dict((ch, out.count(ch)) for ch in "<>*_~`@")
    c["scheme"] = len(re.findall(r"://", out))
    c["domain-dot"] = len(re.findall(r"[^\W_]\.[^\W\d_]", out))
    c["raw-amp"] = len(re.findall(r"&(?!amp;|lt;|gt;)", out))
    return c

ROLE_OK = re.compile(r"https?://|www\.|@|[0-9]{3}", re.I)
benign = fill(json.loads(json.dumps(base)), lambda tag: "Val " + tag, "Val role")
want_md, want_slack = md_counts(render(benign, "fz_base", "markdown")), slack_counts(render(benign, "fz_base", "slack"))
for n, p in enumerate(PAYLOADS):
    role = p if len(p) <= 50 and not ROLE_OK.search(p) else "**r** _r_ <!here> [r](x) `r`"
    b = fill(json.loads(json.dumps(base)), lambda tag: "%s %s" % (p, tag), role)
    md, sl = render(b, "fz%d" % n, "markdown"), render(b, "fz%d" % n, "slack")
    got_md, got_sl = md_counts(md), slack_counts(sl)
    diff = dict((k, (want_md[k], got_md[k])) for k in want_md if want_md[k] != got_md[k])
    assert not diff, ("markdown", p, diff)
    diff = dict((k, (want_slack[k], got_sl[k])) for k in want_slack if want_slack[k] != got_sl[k])
    assert not diff, ("slack", p, diff)
    for target in re.findall(r"\]\(([^)]*)\)", unescape_marks(md)):
        assert target.startswith("https://notes.granola.ai/") or target.startswith("https://issues.tamsquill.example/"), (p, target)
    assert "phish.example" not in md and "phish.example" not in sl, p
PY
ok "R2-1: fuzz: no input value forms a markdown link, autolink, raw HTML, entity, emphasis or block marker, or a Slack mention, link or formatting run"
variant bslink 'I["i06"]["evidence"]["text"] = "We need \\[SSO\\](https://phish.example/a) before we add seats."
T["sso-saml"]["title"] = "\\[click\\](https://phish.example/a)"
A["marrowbyvane.example"]["name"] = "Marrowby \\[x\\](https://phish.example)"'
"$PY" "$SCRIPT" --bundle "$TMP/bslink.json" "${BASE[@]}" >"$TMP/bslink.md" || fail "R2-1: render failed"
"$PY" - "$TMP/bslink.md" <<'PY' || fail "R2-1: backslash-escaped link syntax still forms a link (digest or issue draft)"
import sys
out = open(sys.argv[1], encoding="utf-8").read()
draft = [s for s in out.split("\n### D")[1:] if "click" in s]
assert draft and "Title: \\\\\\[click\\\\\\]\\(https\\://phish\\.example/a\\)" in draft[0], draft
assert "\\\\\\[SSO\\\\\\]\\(https\\://phish\\.example/a\\)" in draft[0], draft
assert "https://phish" not in out and "phish.example" not in out, [l for l in out.splitlines() if "phish" in l][:3]
PY
ok "R2-1: backslash-escaped link syntax is defused in titles, account names, quotes and issue drafts"
variant slackfmt 'T["bulk-csv-import"]["title"] = "*Bulk* _CSV_ ~import~ `now` @channel see https://phish.example or www.phish.example"
A["kestrovane.example"]["name"] = "Kestrovane* Supply_ evil.example"'
"$PY" "$SCRIPT" --bundle "$TMP/slackfmt.json" "${BASE[@]}" --format slack --doc-url "https://type.com/sample-doc_x~y" >"$TMP/sfmt.txt" || fail "R2-1: slack render failed"
"$PY" - "$TMP/sfmt.txt" <<'PY' || fail "R2-1: Slack values can still open bold/italic/strike/code, mention @channel or auto-link"
import sys
out = open(sys.argv[1], encoding="utf-8").read()
line = [l for l in out.splitlines() if "Bulk" in l][0].split(" — ")[0]
assert not any(ch in line for ch in "*_~`@") and "://" not in line and "phish.example" not in line, line
churn = [l for l in out.splitlines() if "Kestrovane" in l][0]
assert "*" not in churn and "_" not in churn.split("<")[0] and "evil.example" not in churn, churn
assert "https://type.com/sample-doc_x~y" in out, "doc link was altered"
PY
ok "R2-1: Slack neutralises * _ ~ \` @ and bare links inside values; the validated doc link is untouched"

# R2-2: phone redaction is pattern-based with phone-like grouping; dates, versions, IPs, order numbers and counts survive.
"$PY" - <<'PY' || fail "R2-2: phone/email redaction corpus"
import sys
sys.path.insert(0, ".")
sys.dont_write_bytecode = True
import build_digest as bd
R = "[phone removed]"
POS = [("+33 1 23 45 67 89", R), ("+49 30 12345678", R), ("07700 900123", R), ("(555) 123-4567", R),
       ("+1 (555) 010-2030", R), ("555.010.4477", R), ("555-010-2030", R), ("+44 20 7946 0958", R),
       ("+44 (0)20 7946 0958", R), ("020 7946 0958", R), ("1-800-555-0199", R), ("+1-555-010-2030", R),
       ("+447700900123", R), ("+61 2 9876 5432", R), ("+81 3-1234-5678", R), ("01 23 45 67 89", R), ("030 12345678", R),
       ("(020) 7946 0958", R), ("Phone:555-010-2030", "Phone:" + R), ("tel:+15550102030", "tel:" + R),
       ("call 555 010 2030 at 10:00", "call %s at 10:00" % R), ("Kickoff 2026-10-15, call +1 555 010 2030.", "Kickoff 2026-10-15, call %s." % R),
       ("2026-10-15 555-010-2030", "2026-10-15 " + R), ("Call me (555) 123-4567 today", "Call me %s today" % R),
       ("ring +33 1 23 45 67 89 or 07700 900123", "ring %s or %s" % (R, R)),
       ("Email rae@marrowbyvane.example.", "Email [email removed]."), ("(rae@marrowbyvane.example)", "([email removed])"),
       ("<ops@x.example>", "<[email removed]>")]
NEG = ["Kickoff 2026-10-15 10:00 PT", "2026-09-28 – 2026-10-04", "2026-09-28 - 2026-10-04", "2026-09-28-2026-10-04",
       "from 2026-10-15T10:00:00-07:00 to 2026-10-15T11:00:00-07:00", "15.10.2026 14:30", "28/09/2026 - 04/10/2026",
       "version 10.4.12.1", "upgraded to 2.14.3 then 3.0.1", "IP 192.168.100.254", "IP 10.0.0.1 and 172.16.254.1",
       "server 192.168.100.254:8080", "PO-2026-004471", "PO 4471-2290-118", "order #4471 2290 118", "Order 4471-2290-118",
       "invoice INV-0042-7781", "invoice 2026 0042 7781", "1,250 seats", "12 500 000 000 rows", "$1 250 000 000",
       "ticket 123456789", "123456789012", "between 09:00-17:30", "build 2026.10.15.1234", "SKU 100-200-300-400",
       "case 00123 45678", "2026-10-15", "we went live 2026-09-28.", "seats: 250 → 1,000 by Q3 2026", "ref #2026-10-15-0042"]
bad = []
for text, want in POS:
    got = bd.redact(text)
    if got != want:
        bad.append(("missed", text, got))
for text in NEG:
    got = bd.redact(text)
    if got != text:
        bad.append(("altered", text, got))
assert len(POS) + len(NEG) >= 40
assert not bad, bad
assert bd.has_phone("Ops, call +1 (555) 010-2030") and not bd.has_phone("Ops lead since 2026-09-28")
print("corpus", len(POS) + len(NEG))
PY
ok "R2-2: redaction corpus (59 cases): international/US/UK formats caught without a stray '('; dates, ranges, versions, IPs, order and invoice numbers, counts untouched"
variant phones 'I["i06"]["evidence"]["text"] = "Call (555) 123-4567 about PO 4471-2290-118; kickoff 2026-10-15 10:00 PT on 10.4.12.1."'
jcheck "R2-2: quotes keep dates, versions and order numbers; the phone goes without a stray '('" \
  'e = [x for x in T["sso-saml"]["evidence"] if "kickoff" in x["text"]][0]; assert e["text"] == "Call [phone removed] about PO 4471-2290-118; kickoff 2026-10-15 10:00 PT on 10.4.12.1.", e' \
  --bundle "$TMP/phones.json" "${BASE[@]}"
"$PY" - <<'PY' || fail "R2-2: listing/README/DATA_CONTRACT/SKILL do not say redaction is pattern-based"
import json, re
listing = json.load(open("_listing.json", encoding="utf-8"))
text = " ".join(listing["benefits"]) + " " + listing["summary"]
assert "pattern-based" in text, "listing"
assert "emails and phone numbers redacted)" not in text, "old listing claim"
for f in ["README.md", "DATA_CONTRACT.md", "SKILL.md", "feedback-extraction-guide.md"]:
    s = open(f, encoding="utf-8").read()
    assert "pattern-based" in s and re.search(r"can miss|may miss|misses", s), f
    assert "9+ digits" not in s and "digit runs (9+" not in s, f
PY
ok "R2-2: listing, README, DATA_CONTRACT, SKILL and guide say redaction is pattern-based and can miss formats"

# R2-3: duplicate-account check ignores free-mail domains; same-name companies are told to rename or merge.
"$PY" -c 'import json,sys; c=json.load(open("digest.json")); c["free_email_domains"].append("freemail.example"); json.dump(c, open(sys.argv[1],"w"))' "$TMP/fm.json"
variant freemail 'A["brackenvolt.example"]["domains"].append("freemail.example"); A["kestrovane.example"]["domains"].append("FreeMail.example")
M["sample-m06"]["attendees"].append({"email": "person@freemail.example"})'
expect 0 "# Customer Feedback Digest — 2026-W40" out "R2-3: two accounts listing a configured free-mail domain are not 'one company'" \
  --bundle "$TMP/freemail.json" --config "$TMP/fm.json" --as-of 2026-10-05T08:00:00-07:00 --internal-domain tamsquill.example
variant samename 'b["accounts"].append({"key": "plimsoll-north", "name": "PLIMSOLL & QUIST", "domains": ["pq-north.example"]})'
expect 2 "make the names distinct" err "R2-3: two companies with the same name are told to make names distinct or merge" --bundle "$TMP/samename.json" "${BASE[@]}"
grep -qF "merge" "$TMP/err" || fail "R2-3: same-name message lost the merge option"
ok "R2-3: same-name message offers both 'make distinct' and 'merge'"

# R2-4: a scheduled run only covers a week that has ended (period end before the --as-of date).
cp sample_state.json "$TMP/r4.json"
expect 2 "has not ended" err "R2-4: scheduled run on the Sunday of the week is refused (nothing printed)" --bundle sample_bundle.json \
  --config digest.json --as-of 2026-10-04T00:01:00-07:00 --internal-domain tamsquill.example --scheduled --state "$TMP/r4.json"
cmp -s sample_state.json "$TMP/r4.json" || fail "R2-4: unfinished week was recorded as pending"
ok "R2-4: unfinished week leaves the state file unchanged"
expect 0 "# Customer Feedback Digest — 2026-W40" out "R2-4: an on-request run may still cover the week so far" --bundle sample_bundle.json \
  --config digest.json --as-of 2026-10-04T00:01:00-07:00 --internal-domain tamsquill.example --state "$TMP/r4.json"

# R2-5: locks: future mtime is stale, a folder at the lock path is exit 2, stale takeover checks the inode and
# token, and a run only ever removes its own lock.
cp sample_state.json "$TMP/lf.json"; echo "999 test" >"$TMP/lf.json.lock"; touch -d "+2 hours" "$TMP/lf.json.lock"
expect 0 "# Customer Feedback Digest — 2026-W40" out "R2-5: a lock with a future mtime is treated as stale" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/lf.json"
[[ ! -e "$TMP/lf.json.lock" ]] || fail "R2-5: lock left behind"
cp sample_state.json "$TMP/ld.json"; mkdir "$TMP/ld.json.lock"
expect 2 "is not a regular file" err "R2-5: a folder at the lock path is a clean exit 2" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/ld.json"
expect 2 "is not a regular file" err "R2-5: a folder at the lock path stops --confirm-posted cleanly" --state "$TMP/ld.json" --confirm-posted 2026-W39 --as-of 2026-10-05T08:01:00-07:00
"$PY" - "$TMP" <<'PY' || fail "R2-5: lock removed a lock it does not own, or broke a lock that changed since it was judged stale"
import os, sys, time
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, ".")
sys.dont_write_bytecode = True
import build_digest as bd
tmp = Path(sys.argv[1])
as_of = datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc)
lock = bd.StateLock(tmp / "own.json", as_of)
lock.__enter__()
os.remove(str(lock.path)); lock.path.write_text("4242 other run\n")
lock.__exit__(None, None, None)
assert lock.path.read_text() == "4242 other run\n", "released someone else's lock"
os.remove(str(lock.path))
stale = bd.StateLock(tmp / "st.json", as_of)
stale.path.write_text("1 crashed\n"); old = time.time() - 3600; os.utime(str(stale.path), (old, old))
seen = stale._identity()
os.remove(str(stale.path)); stale.path.write_text("2 fresh run\n")
assert stale._break_stale(seen) is False and stale.path.read_text() == "2 fresh run\n", "broke a fresh lock"
os.utime(str(stale.path), (old, old)); seen = stale._identity()
assert stale._break_stale(seen) is True and not stale.path.exists(), "did not break an unchanged stale lock"
PY
ok "R2-5: release only removes this run's lock; stale takeover re-checks inode and contents first"
for round in 1 2 3 4; do
  cp sample_state.json "$TMP/sr.json"; echo "1 crashed" >"$TMP/sr.json.lock"; touch -d "30 minutes ago" "$TMP/sr.json.lock"
  for n in 1 2 3 4 5 6; do
    ( set +e; "$PY" "$SCRIPT" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/sr.json" >"$TMP/sr.$n.out" 2>"$TMP/sr.$n.err"; echo $? >"$TMP/sr.$n.code" ) &
  done
  wait
  "$PY" - "$TMP" <<'PY' || fail "R2-5: concurrent runs taking over a stale lock printed more than one digest"
import sys
t = sys.argv[1]
codes = [int(open("%s/sr.%d.code" % (t, n)).read()) for n in range(1, 7)]
printed = [n for n in range(1, 7) if open("%s/sr.%d.out" % (t, n), encoding="utf-8").read()]
errs = "".join(open("%s/sr.%d.err" % (t, n), encoding="utf-8").read() for n in range(1, 7))
assert codes.count(0) == 1 and set(codes) <= {0, 3, 4} and len(printed) == 1 and "Traceback" not in errs, (codes, printed, errs)
PY
done
ok "R2-5: six concurrent runs racing for a stale lock print exactly one digest (4 rounds)"

# R2-7: DATA_CONTRACT names the real config keys.
! grep -qF "ignored_attendee_patterns" DATA_CONTRACT.md SKILL.md README.md || fail "R2-7: nonexistent ignored_attendee_patterns cited"
"$PY" - <<'PY' || fail "R2-7: a config key is missing from DATA_CONTRACT.md"
import sys
sys.path.insert(0, ".")
sys.dont_write_bytecode = True
import build_digest as bd
doc = open("DATA_CONTRACT.md", encoding="utf-8").read()
missing = [k for k in bd.CONFIG_KEYS if "`%s`" % k not in doc]
assert not missing, missing
PY
ok "R2-7: DATA_CONTRACT cites only real config keys, and all of them"

# Cross-cutting: UTF-8 stdout, broken pipes and closed stdout never traceback; pending recovery still works.
cp sample_state.json "$TMP/enc.json"
set +e; PYTHONIOENCODING=ascii "$PY" "$SCRIPT" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/enc.json" >"$TMP/enc.out" 2>"$TMP/enc.err"; code=$?; set -e
[[ "$code" == 0 ]] && ! grep -q Traceback "$TMP/enc.err" && grep -qF "Customer Feedback Digest — 2026-W40" "$TMP/enc.out" || { cat "$TMP/enc.err"; fail "X: ASCII stdout encoding crashed the run"; }
ok "X: stdout is UTF-8 even when the environment says ASCII"
cp sample_state.json "$TMP/bp.json"
"$PY" - "$TMP" "$PY" "$SCRIPT" <<'PY' || fail "X: broken pipe after staging pending printed a traceback or lost the pending record"
import json, subprocess, sys
tmp, py, script = sys.argv[1:4]
args = [py, script, "--bundle", "sample_bundle.json", "--config", "digest.json", "--as-of", "2026-10-05T08:00:00-07:00",
        "--internal-domain", "tamsquill.example", "--scheduled", "--state", tmp + "/bp.json"]
p = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
p.stdout.close()
err = p.stderr.read().decode("utf-8", "replace"); code = p.wait()
assert code == 1 and "Traceback" not in err and "Exception ignored" not in err and "--resume-pending" in err, (code, err)
d = json.load(open(tmp + "/bp.json"))
assert [x["status"] for x in d["digests"] if x["week"] == "2026-W40"] == ["pending"], d
r = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
assert r.returncode == 4 and not r.stdout and b"Traceback" not in r.stderr, r
PY
ok "X: broken pipe after staging is a clean exit 1; the week stays pending and the next run exits 4"
set +e; "$PY" "$SCRIPT" --bundle sample_bundle.json "${BASE[@]}" >&- 2>"$TMP/closed.err"; code=$?; set -e
[[ "$code" == 1 ]] && ! grep -q Traceback "$TMP/closed.err" || { cat "$TMP/closed.err"; fail "X: closed stdout crashed"; }
ok "X: closed stdout is a clean exit 1"
set +e; "$PY" "$SCRIPT" --bundle sample_bundle.json "${BASE[@]}" 2>"$TMP/head.err" | head -c 20 >/dev/null; set -e
! grep -qE "Traceback|Exception ignored" "$TMP/head.err" || { cat "$TMP/head.err"; fail "X: | head printed a traceback"; }
ok "X: piping into head never prints a traceback"
"$PY" -c 'import json,sys; b=json.load(open("sample_bundle.json")); b["meetings"][2]["title"] = "Sync \ud800 broken"; json.dump(b, open(sys.argv[1],"w"))' "$TMP/surr.json"
expect 2 "lone surrogate" err "Q4-F3: a lone surrogate in the bundle is a clean exit 2 before any output" --bundle "$TMP/surr.json" "${BASE[@]}"

# Cross-cutting: null or wrong-type config/state lists are exit 2 with a message, never a traceback.
for key in feedback_types type_priority draftable_types draft_if_customer_types plan_weights ignored_attendee_domains \
           ignored_attendee_local_parts free_email_domains; do
  for val in None 7 '"x"' '{"a": 1}' '[None]' '[3]'; do
    "$PY" -c 'import json,sys; c=json.load(open("digest.json")); c[sys.argv[2]] = eval(sys.argv[3]); json.dump(c, open(sys.argv[1],"w"))' "$TMP/cn.json" "$key" "$val"
    set +e; "$PY" "$SCRIPT" --bundle sample_bundle.json --config "$TMP/cn.json" --as-of 2026-10-05T08:00:00-07:00 --internal-domain tamsquill.example >"$TMP/out" 2>"$TMP/err"; code=$?; set -e
    if [[ "$key" == plan_weights && "$val" == '{"a": 1}' ]]; then continue; fi
    [[ "$code" == 2 && ! -s "$TMP/out" ]] && grep -qF "Error: config" "$TMP/err" && ! grep -q Traceback "$TMP/err" || { cat "$TMP/err"; fail "X: config $key = $val not a clean exit 2"; }
  done
done
ok "X: null or wrong-type config lists and maps are a clean exit 2 (47 cases)"
for val in None 7 '"x"' '{}'; do
  "$PY" -c 'import json,sys; d=json.load(open("sample_state.json")); d["digests"] = eval(sys.argv[2]); json.dump(d, open(sys.argv[1],"w"))' "$TMP/sn.json" "$val"
  expect 2 "Error: state" err "X: state digests = $val rejected cleanly" --bundle sample_bundle.json "${BASE[@]}" --state "$TMP/sn.json"
done
! grep -qE "@""(gmail|googlemail|outlook|hotmail|live|yahoo|icloud|me|aol|proton|protonmail)\.(com|me)" tests.sh run.sh sample_*.json || fail "X: real free-mail address in tests or samples"
ok "X: tests and samples use .example free-mail domains only"

# ---------------------------------------------------------------- pre-QA round 3 regressions
# P3-1: no dot inside a word survives unescaped in markdown (www.123.example and www.-x.example autolinked on GitHub;
# a zero-width space before the dot let markdown-it linkify a domain); Slack breaks the dot after www too.
"$PY" - <<'PY' || fail "P3-1: www.<digit>, www.-x or an invisible character before a dot still leaves an autolink"
import re, sys
sys.path.insert(0, ".")
sys.dont_write_bytecode = True
import build_digest as bd
PUNCT = set("!\"#$%&'()*+,-./:;<=>?@[\\]^_`{|}~")
def active(s):
    out, i = [], 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] in PUNCT:
            out.append("\x00"); i += 2
        else:
            out.append(s[i]); i += 1
    return "".join(out)
for p in ["www.123.example", "see www.-evil.example now", "WWW.1evil.co", "evil\u200b.com", "evil\u00ad.com",
          "evil\u200d.com", "www\u200b.evil.com", "sub.evil.co", "evil.123.com", "e\u0301vil.com", "evil_.com", "evil-.com"]:
    v = bd.clean_text(p)
    m = active(bd.md(v))
    assert not re.search(r"\S\.[\w-]", m), (p, bd.md(v))
    s = bd.slack(v)
    assert not re.search(r"(?i)www\.", s) and not re.search(r"[^\W_]\.[^\W\d_]", s), (p, s)
assert bd.clean_text("a\u200bb\u2060c\ufeffd\u00ade") == "abcde"
assert bd.clean_text("\U0001F468\u200d\U0001F469 x") == "\U0001F468\u200d\U0001F469 x", "emoji joiner kept"
PY
ok "P3-1: every dot inside a word is escaped in markdown; invisible characters are dropped; Slack breaks www."

# P3-2: phone recall for '+' dotted numbers, other scripts' digits and dash look-alikes; ranges and ISBNs survive.
"$PY" - <<'PY' || fail "P3-2: phone redaction recall/precision"
import sys
sys.path.insert(0, ".")
sys.dont_write_bytecode = True
import build_digest as bd
R = "[phone removed]"
for text in ["+1.555.123.4567", "\uff15\uff15\uff15-\uff11\uff12\uff13-\uff14\uff15\uff16\uff17", "+1 555\u2013123\u20134567",
             "555\u2011123\u20114567", "+1\u200b555\u200b123\u200b4567", "555 123\u200b 4567", "\u0660\u0667\u0667\u0660\u0660 \u0669\u0660\u0660\u0661\u0662\u0663",
             "\uff0b44 20 7946 0958"]:
    got = bd.redact(bd.clean_text(text))
    assert got == R, (text, got)
for text in ["between 100 000-200 000 users", "100 000\u2013200 000 users", "ISBN 978-3-16-148410-0", "Postcode 94103-1234", "1.234.567.890 rows",
             "version 1.2.3.4567", "pages 120-135"]:
    assert bd.redact(text) == text, (text, bd.redact(text))
PY
ok "P3-2: '+1.555.123.4567', full-width/Arabic-Indic digits and dash look-alikes are redacted; number ranges and ISBNs are not"

# P3-3: one company cannot pass as two accounts through invisible characters or full-width letters.
for nm in 'Marrowby \u200b& Vane' '\uff2d\uff21\uff32\uff32\uff2f\uff37\uff22\uff39 & Vane' 'Marrow\u00adby & Vane'; do
  variant zwname "b[\"accounts\"].append({\"key\": \"mv-copy\", \"name\": u\"$nm\", \"domains\": [\"mv-copy.example\"]})"
  expect 2 "is already used by" err "P3-3: account name '$nm' matches an existing account" --bundle "$TMP/zwname.json" "${BASE[@]}"
done

# P3-4: the same attendee domain in meetings filed under two accounts is flagged (it would be counted twice).
variant twokeys 'b["accounts"].append({"key": "outfitters-west", "name": "Outfitters West", "status": "customer"})
M["sample-m05"]["account_key"] = "outfitters-west"; M["sample-m05"]["attendees"].append({"email": "kai@outfitters.example"})
A["brackenvolt.example"]["domains"] = []'
expect 0 "Same company under two accounts? Attendees from outfitters\\.example are in meetings filed under Example Outfitters Co and Outfitters West" out \
  "P3-4: an attendee domain shared by two accounts is flagged in Coverage" --bundle "$TMP/twokeys.json" "${BASE[@]}"
expect 0 "Coverage" out "P3-4: no shared-domain warning on the sample" --bundle sample_bundle.json "${BASE[@]}"
! grep -q "Same company" "$TMP/out" || fail "P3-4: false shared-domain warning on the sample"
ok "P3-4: the sample has no shared-domain warning"

# P3-5: the churn section quotes the churn-risk item, not another item merged into the same row.
variant churnmix 'b["items"].insert(0, {"meeting_id": "sample-m06", "type": "objection", "theme": "renewal-price-increase",
  "evidence": {"kind": "transcript", "text": "Procurement wants a fixed price for three years.", "speaker_role": "CFO"}})'
"$PY" "$SCRIPT" --bundle "$TMP/churnmix.json" "${BASE[@]}" >"$TMP/churnmix.md" || fail "P3-5: render failed"
"$PY" - "$TMP/churnmix.md" <<'PY' || fail "P3-5: churn-risk section shows a different item's quote"
import sys
sect = open(sys.argv[1], encoding="utf-8").read().split("## Churn-risk signals")[1].split("\n## ")[0]
assert "Procurement" not in sect and "price goes up again" in sect, sect
PY
ok "P3-5: churn-risk signals quote the churn-risk item"

# P3-6: one competitor written in two cases is one row.
variant compcase 'I["i10"]["competitor"] = "COUNTWHISTLE"'
"$PY" "$SCRIPT" --bundle "$TMP/compcase.json" "${BASE[@]}" >"$TMP/compcase.md" || fail "P3-6: render failed"
[[ "$(grep -ciE "^\| countwhistle \|" "$TMP/compcase.md")" == 1 ]] && grep -qiE "^\| countwhistle \| 2 \(" "$TMP/compcase.md" || { grep "^|" "$TMP/compcase.md"; fail "P3-6: competitor split by case"; }
ok "P3-6: competitor names that differ only by case are one row"

# P3-7: a period that is not 7 days long is never 'growing' against a one-week digest.
variant month 'b["period"] = {"start": "2026-09-28", "end": "2026-10-27"}'
jcheck "P3-7: 30-day period compared with a weekly digest says recurring, not growing" \
  'assert T["bulk-csv-import"]["trend"] == "recurring" and T["bulk-csv-import"]["delta"] is None, T["bulk-csv-import"]
assert all(t["trend"] != "growing" for t in d["all_themes"])' \
  --bundle "$TMP/month.json" --config digest.json --as-of 2026-10-28T08:00:00-07:00 --internal-domain tamsquill.example --state sample_state.json

# P3-8: duplicate notes merge transitively (A~B share one attendee, B~C share another).
variant trans 'M["sample-m01"]["attendees"] = [{"email": "dana@tamsquill.example"}, {"email": "priya@outfitters.example"}]
b["meetings"].append({"meeting_id": "sample-m01c", "title": "Example Outfitters QBR", "start": "2026-09-29T17:00:00Z",
  "url": "https://notes.granola.ai/d/sample-m01c", "kind": "customer", "account_key": "outfitters.example",
  "attendees": [{"email": "sam@tamsquill.example"}, {"email": "lee@outfitters.example"}]})
M["sample-m02"]["attendees"].append({"email": "lee@outfitters.example"})'
jcheck "P3-8: three notes of one call (linked through different attendees) are one meeting" \
  'assert d["coverage"]["buckets"]["duplicate_note"] == 2, d["coverage"]' --bundle "$TMP/trans.json" "${BASE[@]}"

# P3-9: errors raised after loading still name the bundle file.
variant noacct 'M["sample-m05"]["account_key"] = None'
expect 2 "noacct.json: \$.meetings[4].account_key: is required" err "P3-9: missing account_key error names the bundle file" --bundle "$TMP/noacct.json" "${BASE[@]}"

# P3-10: a state file with a lone-surrogate escape is exit 2 before anything is printed (was a traceback on write).
"$PY" -c 'import json,sys; d=json.load(open("sample_state.json")); d["digests"][0]["post_ref"] = "\ud800"; open(sys.argv[1],"w").write(json.dumps(d))' "$TMP/surrstate.json"
expect 2 "lone surrogate" err "P3-10: lone surrogate in the state file is a clean exit 2" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/surrstate.json"

# P3-11: a state path that is a broken link is not treated as an empty history.
ln -s "$TMP/moved-state.json" "$TMP/dangling.json"
expect 2 "link to a file that does not exist" err "P3-11: dangling state link refuses to run blind" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/dangling.json"
[[ -L "$TMP/dangling.json" ]] || fail "P3-11: the state link was replaced"
ok "P3-11: the dangling state link is left alone"

# P3-12: dates outside 1900-2999 are rejected cleanly (year 1 with an offset crashed in date arithmetic).
variant year1 'b["period"] = {"start": "0001-01-01", "end": "0001-01-07"}
for m in b["meetings"]: m["start"] = "0001-01-01T00:30:00+01:00"'
expect 2 "is outside the years 1900-2999" err "P3-12: year-1 dates are a clean exit 2" --bundle "$TMP/year1.json" --config digest.json --as-of 0001-01-08T00:00:00Z --internal-domain tamsquill.example
expect 2 "is outside the years 1900-2999" err "P3-12: --as-of in year 9999 is a clean exit 2" --bundle sample_bundle.json --config digest.json --as-of 9999-12-31T23:59:59-14:00 --internal-domain tamsquill.example

# P3-13: a folder at the takeover-guard path does not strand this run's own lock.
cp sample_state.json "$TMP/guard.json"; mkdir "$TMP/guard.json.lock.takeover"
expect 0 "Customer Feedback Digest" out "P3-13: run succeeds with a folder at the guard path" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/guard.json"
[[ ! -e "$TMP/guard.json.lock" ]] || fail "P3-13: the run left its lock behind"
ok "P3-13: the run removed its own lock"

# P3-14: 'at most 92 days' counts both ends.
variant d93 'b["period"] = {"start": "2026-07-04", "end": "2026-10-04"}'
expect 2 "spans more than 92 days" err "P3-14: a 93-day period is rejected" --bundle "$TMP/d93.json" "${BASE[@]}"
variant d92 'b["period"] = {"start": "2026-07-05", "end": "2026-10-04"}'
expect 0 "Customer Feedback Digest" out "P3-14: a 92-day period is accepted" --bundle "$TMP/d92.json" "${BASE[@]}"

# P3-15: config values shown in the digest are escaped; plan weights match case-insensitively.
"$PY" -c 'import json,sys; c=json.load(open("digest.json")); c["currency_symbol"]="<b>"; c["plan_weights"]={"GROWTH": 50}; json.dump(c, open(sys.argv[1],"w"))' "$TMP/cfg_cur.json"
expect 0 "ARR &lt;b&gt;" out "P3-15: currency symbol is escaped" --bundle sample_bundle.json --config "$TMP/cfg_cur.json" --as-of 2026-10-05T08:00:00-07:00 --internal-domain tamsquill.example --weighting arr
jcheck "P3-15: plan_weights keys match plans case-insensitively" \
  'assert T["bulk-csv-import"]["score"] >= 50, T["bulk-csv-import"]["score"]' \
  --bundle sample_bundle.json --config "$TMP/cfg_cur.json" --as-of 2026-10-05T08:00:00-07:00 --internal-domain tamsquill.example --weighting plan

# P3-16: a scheduled run mentions earlier weeks still pending (a crashed or unconfirmed post is not forgotten).
"$PY" -c 'import json,sys; d=json.load(open("sample_state.json")); d["digests"][1]["status"]="pending"; d["digests"][1]["posted_at"]=None; json.dump(d, open(sys.argv[1],"w"))' "$TMP/pend39.json"
expect 0 "Earlier week(s) 2026-W39 were rendered but never confirmed" err "P3-16: earlier pending week is reported on stderr" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/pend39.json"

# P3-17: weights and ARR are capped, so a plan-weighted or ARR score can never become inf (JSON "Infinity").
"$PY" -c 'import json,sys; c=json.load(open("digest.json")); c["plan_weights"]["growth"]=1e308; json.dump(c, open(sys.argv[1],"w"))' "$TMP/cfg_inf.json"
expect 2 "plan_weights.growth must be a non-negative number up to" err "P3-17: a huge plan weight is a clean exit 2" --bundle sample_bundle.json --config "$TMP/cfg_inf.json" --as-of 2026-10-05T08:00:00-07:00 --internal-domain tamsquill.example --weighting plan
variant arrinf 'for a in b["accounts"]: a["arr"] = 1e308'
expect 2 "arr: must be a non-negative finite number up to" err "P3-17: ARR above 10^15 is a clean exit 2" --bundle "$TMP/arrinf.json" "${BASE[@]}" --weighting arr

# ---------------------------------------------------------------- pre-QA round 4 regressions
# P4-1: lists of amounts, years or ascending counts in a quote are not phone numbers (they were rewritten).
"$PY" - <<'PY' || fail "P4-1: number lists in quotes rewritten as phone numbers, or a phone format lost"
import sys
sys.path.insert(0, ".")
sys.dont_write_bytecode = True
import build_digest as bd
R = "[phone removed]"
KEEP = ["prices 9.99 19.99 29.99", "rates 1.25 2.50 3.75 4.25", "scores 0.91 0.87 0.93", "in 2019 2020 2021",
        "2023-2024-2025", "years 2026 2025 2024", "1200 1500 1800 seats", "batches of 5000 10000 20000",
        "grew 120-150-200", "IDs 4821 4822 4823", "12 24 36 48 60 months", "steps 1 2 3 4 5 6 7 8 9 10",
        "100.5 200.25 300.125", "Python 3.10 3.11 3.12 3.13"]
for text in KEEP:
    assert bd.redact(text) == text, (text, bd.redact(text))
for text in ["212 555 1234", "212-555-1234", "1-212-555-1234", "06 12 34 56 78", "98765 43210", "138 0013 8000",
             "612-345-678", "+1 2024 2025 2026", "(212) 555-1234", "+1.555.123.4567"]:
    assert bd.redact(text) == R, (text, bd.redact(text))
PY
ok "P4-1: lists of amounts, years and ascending numbers survive; NANP, leading-0 and '+' phones are still removed"

# P4-2: an email written with a full-width @ (or full-width letters) is redacted like any other.
"$PY" - <<'PY' || fail "P4-2: full-width email address not redacted"
import sys
sys.path.insert(0, ".")
sys.dont_write_bytecode = True
import build_digest as bd
for text in ["user＠example.com", "ｕｓｅｒ＠ｅｘａｍｐｌｅ.com",
             "user﹫example.com", "user@example．com"]:
    got = bd.redact(bd.clean_text("mail " + text + " today"))
    assert got == "mail [email removed] today", (text, got)
assert bd.redact("a＠ b") == "a＠ b"
PY
ok "P4-2: emails with full-width @, dots or letters are redacted"

# P4-3: --dry-run promises never to write the state file, so it cannot be combined with --confirm-posted.
"$PY" -c 'import json,sys; d=json.load(open("sample_state.json")); d["digests"][1]["status"]="pending"; d["digests"][1]["posted_at"]=None; json.dump(d, open(sys.argv[1],"w"))' "$TMP/dryconf.json"
cp "$TMP/dryconf.json" "$TMP/dryconf.before"
expect 2 "--dry-run cannot be combined with --confirm-posted" err "P4-3: --confirm-posted --dry-run refused" --state "$TMP/dryconf.json" --confirm-posted 2026-W39 --as-of 2026-10-05T08:00:00-07:00 --dry-run
cmp -s "$TMP/dryconf.json" "$TMP/dryconf.before" || fail "P4-3: --dry-run --confirm-posted wrote the state file"
ok "P4-3: the state file is unchanged"

# P4-4: quiet themes beyond max_quieter_themes are counted, not silently dropped.
"$PY" -c 'import json,sys
d=json.load(open("sample_state.json"))
for n in range(7): d["digests"][1]["themes"]["gone-%d" % n] = {"title": "Gone %d" % n, "accounts": 1, "mentions": 1}
json.dump(d, open(sys.argv[1],"w"))' "$TMP/quiet.json"
expect 0 "…and 3 more themes from the previous digest did not come up" out "P4-4: quieter overflow is reported" --bundle sample_bundle.json "${BASE[@]}" --state "$TMP/quiet.json"

# P4-5: integers too large for a float are rejected like floats (they printed as 400-digit counts).
"$PY" -c 'import json,sys; d=json.load(open("sample_state.json")); d["digests"][1]["themes"]["mobile-offline-mode"]["accounts"]=10**400; open(sys.argv[1],"w").write(json.dumps(d))' "$TMP/bigint.json"
expect 2 "is too large" err "P4-5: a 401-digit integer in the state file is a clean exit 2" --bundle sample_bundle.json "${BASE[@]}" --state "$TMP/bigint.json"
"$PY" -c 'import json,sys; c=json.load(open("digest.json")); c["max_top_themes"]=10**20; open(sys.argv[1],"w").write(json.dumps(c))' "$TMP/cfg_bigint.json"
expect 2 "is too large" err "P4-5: a 21-digit integer in the config is a clean exit 2" --bundle sample_bundle.json --config "$TMP/cfg_bigint.json" --as-of 2026-10-05T08:00:00-07:00 --internal-domain tamsquill.example

# P4-6: a state path that is a link updates the file it points to (and locks it there), never replacing the link.
mkdir "$TMP/real"; cp sample_state.json "$TMP/real/s.json"; ln -s "$TMP/real/s.json" "$TMP/slink.json"
expect 0 "Customer Feedback Digest" out "P4-6: scheduled run through a state link" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/slink.json"
[[ -L "$TMP/slink.json" ]] && grep -q '"2026-W40"' "$TMP/real/s.json" || fail "P4-6: the link was replaced or its target not updated"
expect 4 "never confirmed" err "P4-6: a run on the target path sees the pending week" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$TMP/real/s.json"

# P4-7: a path the OS cannot even check (name too long) is a clean exit 2 on every Python, never a traceback
# (3.8) or an empty history (3.12+ Path.exists() hides the error).
LONG="$TMP/$(printf 'x%.0s' {1..300})"
expect 2 "$TMP/" err "P4-7: over-long --bundle" --bundle "$LONG" "${BASE[@]}"
expect 2 "$TMP/" err "P4-7: over-long --config" --bundle sample_bundle.json --config "$LONG" --as-of 2026-10-05T08:00:00-07:00 --internal-domain tamsquill.example
expect 2 "cannot be checked" err "P4-7: over-long --state with --known-themes" --state "$LONG" --known-themes
expect 2 "$TMP/" err "P4-7: over-long --state on a scheduled run" --bundle sample_bundle.json "${BASE[@]}" --scheduled --state "$LONG"
expect 2 "$TMP/" err "P4-7: over-long --state with --confirm-posted" --state "$LONG" --confirm-posted 2026-W40 --as-of 2026-10-05T08:00:00-07:00
expect 2 "cannot be checked" err "P4-7: over-long --state on a read-only run" --bundle sample_bundle.json "${BASE[@]}" --state "$LONG"

# ---------------------------------------------------------------- QA round 4 regressions
# Q4-B1: `$…$` (Type math) and `==…==` (highlight) are escaped in every markdown value, issue drafts included.
"$PY" - <<'PY' || fail "Q4-B1: md() leaves \$ or = unescaped"
import sys
sys.path.insert(0, ".")
sys.dont_write_bytecode = True
import build_digest as bd
assert bd.md("cost $5 to $9 and hi") == "cost \\$5 to \\$9 and hi", bd.md("cost $5 to $9 and hi")
assert bd.md("==hi==") == "\\=\\=hi\\=\\=", bd.md("==hi==")
PY
ok "Q4-B1: md() escapes \$ and ="
variant mathhl 'T["sso-saml"]["title"] = "SSO cost $5 to $9 and ==hi=="
I["i06"]["evidence"]["text"] = "It would cost $5 to $9 per seat, ==really== cheap."'
"$PY" "$SCRIPT" --bundle "$TMP/mathhl.json" "${BASE[@]}" >"$TMP/mathhl.md" || fail "Q4-B1: render failed"
"$PY" - "$TMP/mathhl.md" <<'PY' || fail "Q4-B1: \$ or == reached the digest or an issue draft unescaped"
import re, sys
out = open(sys.argv[1], encoding="utf-8").read()
assert re.search(r"(?<!\\)\$", out) is None, [l for l in out.splitlines() if "$" in l][:3]
assert re.search(r"(?<!\\)=", out.replace("\\=", "")) is None, [l for l in out.splitlines() if "=" in l][:3]
draft = [x for x in out.split("\n### D")[1:] if "SSO" in x]
assert draft and "Title: SSO cost \\$5 to \\$9 and \\=\\=hi\\=\\=" in draft[0], draft
assert "cost \\$5 to \\$9 per seat, \\=\\=really\\=\\= cheap" in out, out
PY
ok "Q4-B1: \$…\$ and ==…== in titles, quotes and issue drafts are escaped"

# Q4-B2: no doc or listing claims the script checks quotes against meeting text (the bundle carries none).
"$PY" - <<'PY' || fail "Q4-B2: a doc claims the script verifies quotes"
import json, re
listing = json.load(open("_listing.json", encoding="utf-8"))
texts = {"_listing.json": " ".join(listing["benefits"]) + " " + listing["summary"]}
for f in ["README.md", "DATA_CONTRACT.md", "SKILL.md", "feedback-extraction-guide.md"]:
    texts[f] = open(f, encoding="utf-8").read()
for f, t in texts.items():
    t = " ".join(t.split())
    assert not re.search(r"script (verifies|checks|confirms) (that )?(each |every |the )?quotes?", t, re.I), f
    assert "Every quote is verbatim" not in t, f
for f in ["SKILL.md", "DATA_CONTRACT.md", "_listing.json"]:
    assert "does not check quotes against" in " ".join(texts[f].split()), f
PY
ok "Q4-B2: docs say the agent copies quotes and the script does not check them against meeting text"

# Q4-F1: descending number lists and zero-padded lists (0800 0900 1000) are not phones; a cue word or + still is.
"$PY" - <<'PY' || fail "Q4-F1: number list redacted as a phone, or a phone missed"
import sys
sys.path.insert(0, ".")
sys.dont_write_bytecode = True
import build_digest as bd
R = "[phone removed]"
KEEP = ["shifts at 0800 0900 1000", "0800 0900 1000 1100", "1000 0900 0800", "tiers 5000 2000 1000",
        "50000 40000 30000 seats", "budget 40000 30000", "1800 1500 1200 seats", "prices 29.99 19.99 9.99",
        "40000-50000 a year", "we budgeted 40000-50000", "300-200-100 units"]
for text in KEEP:
    assert bd.redact(text) == text, (text, bd.redact(text))
for text, want in [("98765 43210", R), ("call me on 0800 0900 1000", "call me on " + R),
                   ("phone: 5000 2000 1000", "phone: " + R), ("+44 5000 2000 1000", R), ("07700 900123", R),
                   ("01 23 45 67 89", R), ("06 12 34 56 78", R), ("212 555 1234", R)]:
    assert bd.redact(text) == want, (text, bd.redact(text))
PY
ok "Q4-F1: descending and zero-padded number lists survive; cue words, '+' and phone shapes are still removed"

# Q4-F2: the email pattern caps the local part at 64 characters, so long tokens stay fast.
"$PY" - <<'PY' || fail "Q4-F2: email redaction slow on long tokens"
import sys, time
sys.path.insert(0, ".")
sys.dont_write_bytecode = True
import build_digest as bd
for s in ["a" * 32000, "a" * 32000 + "@", "a." * 16000 + "@x", "x" * 32000 + "@y.example"]:
    t = time.time()
    bd.redact(s)
    assert time.time() - t < 1.0, (s[:10], len(s), time.time() - t)
assert bd.redact("mail %s@x.example now" % ("a" * 64)) == "mail [email removed] now"
PY
ok "Q4-F2: 32k-character tokens redact in well under a second; 64-character local parts still match"

# Q4-F3: UTC offsets beyond ±14:00 or with minutes >= 60 are exit 2 (bundle and --as-of); ±14:00 is accepted.
expect 2 "UTC offset" err "Q4-F3: --as-of offset +14:30 rejected" --bundle sample_bundle.json --config digest.json --as-of 2026-10-05T08:00:00+14:30 --internal-domain tamsquill.example
expect 2 "UTC offset" err "Q4-F3: --as-of offset -15:00 rejected" --bundle sample_bundle.json --config digest.json --as-of 2026-10-05T08:00:00-15:00 --internal-domain tamsquill.example
expect 2 "UTC offset" err "Q4-F3: --as-of offset +05:60 rejected" --bundle sample_bundle.json --config digest.json --as-of 2026-10-05T08:00:00+05:60 --internal-domain tamsquill.example
expect 0 "Customer Feedback Digest" out "Q4-F3: --as-of offset -14:00 accepted" --bundle sample_bundle.json --config digest.json --as-of 2026-10-05T08:00:00-14:00 --internal-domain tamsquill.example
variant offs 'M["sample-m02"]["start"] = M["sample-m02"]["start"][:19] + "+23:59"'
expect 2 "UTC offset" err "Q4-F3: meeting start with offset +23:59 rejected" --bundle "$TMP/offs.json" "${BASE[@]}"
"$PY" -c 'import json,sys; c=json.load(open("digest.json")); c["currency_symbol"] = "\udc80"; json.dump(c, open(sys.argv[1],"w"))' "$TMP/surrcfg.json"
expect 2 "lone surrogate" err "Q4-F3: a lone surrogate in the config is a clean exit 2" --bundle sample_bundle.json --config "$TMP/surrcfg.json" --as-of 2026-10-05T08:00:00-07:00 --internal-domain tamsquill.example
expect 2 "--doc-url" err "Q4-F3: --doc-url with undecodable bytes is a clean exit 2" --bundle sample_bundle.json "${BASE[@]}" --format slack --doc-url $'https://type.com/\xff'
expect 2 "--post-ref" err "Q4-F3: --post-ref with undecodable bytes is a clean exit 2" --state "$TMP/st_pr.json" --confirm-posted 2026-W40 --as-of 2026-10-05T08:00:00-07:00 --post-ref $'https://slack.example/\xff'

# Q4-F4: tests use .example addresses only (no real domains in attendee emails).
! grep -nE '"email": "[^"]*@[^"]*\.(ai|com|io|co|net|org)"' tests.sh || fail "Q4-F4: a real-looking domain in a test address"
ok "Q4-F4: test addresses use .example domains"

# X5 / X6: no overclaims, invented sample names only, listing sample matches run.sh.
! grep -qiF "stops double posts" README.md SKILL.md DATA_CONTRACT.md || fail "X5: 'stops double posts' overclaim"
ok "X5: no 'stops double posts' overclaim"
set +e; grep -rliE "copperquill|quillmoss|ledgerline|tallyboard|brightwater|kestrel supply|harbor & vine|juniper & vale|copperline|fernhill|linear\.app/" \
  --exclude=tests.sh --exclude=LICENSE.txt --exclude-dir=__pycache__ .; rc=$?; set -e
[[ "$rc" == 1 ]] || fail "X6: old or possibly-real sample names remain (grep exit $rc)"
[[ ! -e __pycache__ ]] || fail "tests left a __pycache__ folder in the package"
ok "X6: sample names are the invented set"
if [[ -f _listing.json ]]; then
  "$PY" - <<'PY' || fail "_listing.json sampleOutput differs from section 1 of run.sh output"
import json
listing = json.load(open("_listing.json", encoding="utf-8"))["sampleOutput"]
run = open("expected_output.txt", encoding="utf-8").read().split("\n", 1)[1].split("\n=== 2.", 1)[0]
assert listing == run + "\n_Produced by running `bash run.sh` on the fictional sample data bundled with this skill._"
PY
  ok "_listing.json sampleOutput matches run.sh section 1"
else
  ok "_listing.json is not shipped with the skill; listing check skipped"
fi

# Counts belong in execution receipts, not a self-referential documentation assertion.
! grep -qE '`bash tests.sh`, [0-9]+ checks' SKILL.md || fail "docs hard-code test count"
ok "docs: no hard-coded check count"

echo "PASS $PASS/$TOTAL"

#!/usr/bin/env bash
# Negative and guardrail tests. Run from the skill directory: bash tests.sh
# Builds variants of the fictional sample bundle in a temp dir; never touches real data.
set -euo pipefail

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
SCRIPT=build_briefing.py
BASE=(--config briefing.json --as-of 2026-10-06T09:35:00-07:00 --internal-domain ledgerline.example)
PASS=0

ok() { PASS=$((PASS + 1)); echo "ok $PASS - $1"; }
fail() { echo "FAIL - $1"; exit 1; }

# variant NAME PYTHON_EXPR : writes $TMP/NAME.json from the Example Outfitters sample after applying the edit to `b`
variant() {
  python3 - "$TMP/$1.json" "$2" <<'PY'
import json, sys
b = json.load(open("sample_bundle_outfitters.json", encoding="utf-8"))
exec(sys.argv[2])
json.dump(b, open(sys.argv[1], "w", encoding="utf-8"))
PY
}

# expect_exit CODE PATTERN STREAM -- args...   (STREAM is out or err)
expect() {
  local code="$1" pattern="$2" stream="$3" name="$4"; shift 4
  set +e
  python3 "$SCRIPT" "$@" >"$TMP/out" 2>"$TMP/err"
  local got=$?
  set -e
  [[ "$got" == "$code" ]] || { cat "$TMP/out" "$TMP/err"; fail "$name: exit $got, expected $code"; }
  grep -qF -- "$pattern" "$TMP/$stream" || { cat "$TMP/out" "$TMP/err"; fail "$name: '$pattern' not in std$stream"; }
  ok "$name"
}

# 1. Internal-only meeting: one-line skip notice, exit 0, no briefing.
expect 0 "Skipped: internal-only meeting" out "internal-only meeting is skipped" \
  --bundle sample_bundle_internal.json "${BASE[@]}"
[[ "$(wc -l <"$TMP/out")" -eq 1 ]] || fail "internal-only skip must be exactly one line"
! grep -q "Briefing:" "$TMP/out" || fail "internal-only meeting produced a briefing"
ok "internal-only skip is a single line with no briefing"

# 2. Missing required field -> exit 2 naming file and JSON path.
variant no_start 'del b["meeting"]["start"]'
expect 2 "\$.meeting.start: is required" err "missing meeting.start exits 2 with JSON path" \
  --bundle "$TMP/no_start.json" "${BASE[@]}"
grep -qF "no_start.json" "$TMP/err" || fail "error message does not name the file"
ok "error message names the bundle file"

# 3. --as-of without a UTC offset -> exit 2.
expect 2 "--as-of" err "naive --as-of rejected" \
  --bundle sample_bundle_outfitters.json --config briefing.json \
  --as-of 2026-10-06T09:35:00 --internal-domain ledgerline.example

# 4. No internal domain -> exit 2 (internal meetings could not be recognised).
expect 2 "--internal-domain" err "missing internal domain rejected" \
  --bundle sample_bundle_outfitters.json --config briefing.json --as-of 2026-10-06T09:35:00-07:00

# 5. Competitor mention without a source quote -> exit 2 (never surface unsourced intel).
variant no_quote 'del b["signals"]["competitor_mentions"][0]["quote"]'
expect 2 "competitor_mentions[0].quote" err "competitor mention without quote rejected" \
  --bundle "$TMP/no_quote.json" "${BASE[@]}"

# 6. Malformed JSON -> exit 2 with line/column.
printf '{"schema_version": 1,,}' >"$TMP/broken.json"
expect 2 "is not valid JSON (line 1" err "malformed JSON rejected" --bundle "$TMP/broken.json" "${BASE[@]}"

# 7. Duplicate check: a scheduled run on a logged event is skipped; a person asking again (no --scheduled)
#    gets the briefing again without a second log row; --dry-run does not append.
cp sample_briefing_log.md "$TMP/log.md"
expect 0 "Briefing: Example Outfitters Co (fictional)" out "dry run briefs" \
  --bundle sample_bundle_outfitters.json "${BASE[@]}" --log "$TMP/log.md" --dry-run
cmp -s sample_briefing_log.md "$TMP/log.md" || fail "--dry-run modified the log"
ok "--dry-run leaves the log unchanged"
expect 0 "Briefing: Example Outfitters Co (fictional)" out "first real run briefs" \
  --bundle sample_bundle_outfitters.json "${BASE[@]}" --log "$TMP/log.md"
expect 0 "Skipped: already briefed" out "second scheduled run is deduplicated" \
  --bundle sample_bundle_outfitters.json "${BASE[@]}" --log "$TMP/log.md" --scheduled --lookahead-minutes 30
expect 0 "Briefing: Example Outfitters Co (fictional)" out "person asking again (not scheduled) is briefed again" \
  --bundle sample_bundle_outfitters.json "${BASE[@]}" --log "$TMP/log.md"
! grep -q "Skipped" "$TMP/out" || fail "interactive rerun was skipped"
[[ "$(grep -c '7kq2n4rp9d0v1s3h5j6m8b2c4f' "$TMP/log.md")" -eq 1 ]] || fail "log row not appended exactly once"
ok "log has exactly one row for the event after an interactive rerun"

# 8. Moved meeting (same event id, new start) is a new key and is briefed again.
variant moved 'b["meeting"]["start"] = "2026-10-06T10:15:00-07:00"; b["meeting"]["end"] = "2026-10-06T11:00:00-07:00"'
expect 0 "Briefing: Example Outfitters Co (fictional)" out "rescheduled event is briefed again" \
  --bundle "$TMP/moved.json" "${BASE[@]}" --log "$TMP/log.md" --dry-run

# 9. Log that is not a briefing log -> exit 2 (never run blind).
echo "random notes" >"$TMP/bad_log.md"
expect 2 "not a briefing log" err "unreadable log refuses to run" \
  --bundle sample_bundle_outfitters.json "${BASE[@]}" --log "$TMP/bad_log.md"

# 10. Scheduled run outside the lookahead window -> skip.
expect 0 "does not start within the next 30 minutes" out "outside lookahead is skipped" \
  --bundle sample_bundle_outfitters.json --config briefing.json \
  --as-of 2026-10-06T08:00:00-07:00 --internal-domain ledgerline.example --scheduled --lookahead-minutes 30

# 11. No CRM record -> said plainly, no deal table.
variant no_crm 'b["crm"] = {"record_found": False, "deal": None, "contacts": [], "open_tasks": []}'
expect 0 "No CRM record found in HubSpot for Example Outfitters Co (fictional)" out "missing CRM record is stated plainly" \
  --bundle "$TMP/no_crm.json" "${BASE[@]}"
! grep -q "| Stage |" "$TMP/out" || fail "deal table rendered without a CRM record"
grep -qF "First meeting. No prior touchpoints." "$TMP/out" || fail "attendees not in CRM should be flagged new"
ok "no deal table and attendees flagged new without CRM"

# 12. crm: none -> deal data not available.
variant crm_none 'b["sources"]["crm"] = {"provider": "none", "status": "not_configured"}; b["crm"] = None'
expect 0 "Deal data not available" out "crm none says deal data not available" \
  --bundle "$TMP/crm_none.json" "${BASE[@]}"
grep -qF "Gaps: CRM not configured" "$TMP/out" || fail "crm none not listed in Gaps"
ok "crm none appears in Gaps"

# 13. enrichment: full but sources unavailable -> say so, do not guess.
variant enr_down 'b["sources"]["enrichment"] = {"provider": "web", "status": "unavailable", "detail": "web search returned errors"}'
expect 0 "Company context: not available — enrichment sources unavailable (web search returned errors)" out \
  "enrichment full + unavailable is stated" --bundle "$TMP/enr_down.json" "${BASE[@]}" --enrichment full

# 14. enrichment: full with cited items renders them with sources.
variant enr_ok 'b["sources"]["enrichment"] = {"provider": "web", "status": "ok"}; b["enrichment"]["items"] = [{"kind": "hiring", "summary": "Hiring a Director of Ecommerce Ops", "source_url": "https://outfitters.example/careers", "date": "2026-10-01"}]'
expect 0 "Hiring a Director of Ecommerce Ops (hiring, Oct 1 — https://outfitters.example/careers)" out \
  "enrichment items are cited" --bundle "$TMP/enr_ok.json" "${BASE[@]}" --enrichment full

# 15. Stale + past close date: 20 days idle, close date passed.
variant stale 'b["crm"]["deal"]["last_activity_at"] = "2026-09-16"; b["crm"]["deal"]["close_date"] = "2026-09-30"'
expect 0 "| Days since last activity | 20 | ⚠️ Stale (≥14 days); last email Oct 3 — activity not logged in CRM |" out \
  "stale record flagged at >= stale_days" --bundle "$TMP/stale.json" "${BASE[@]}"
grep -qF "| Close date | Sep 30 | ⚠️ Passed 6 days ago" "$TMP/out" || fail "past close date not flagged"
ok "past close date flagged"

# 16. Exactly stale_days (14) is stale; 13 is not.
variant edge14 'b["crm"]["deal"]["last_activity_at"] = "2026-09-22"; b["email"]["threads"] = []'
expect 0 "| Days since last activity | 14 | ⚠️ Stale (≥14 days) |" out "14 days is stale" --bundle "$TMP/edge14.json" "${BASE[@]}"
variant edge13 'b["crm"]["deal"]["last_activity_at"] = "2026-09-23"'
expect 0 "| Days since last activity | 13 | |" out "13 days is not stale" --bundle "$TMP/edge13.json" "${BASE[@]}"

# 16b. Clearly outdated record (>= outdated_days) is said plainly in TL;DR and risks.
variant outdated 'b["crm"]["deal"]["last_activity_at"] = "2026-07-01"'
expect 0 "The CRM record looks outdated, so treat these figures as unconfirmed." out "outdated record stated in TL;DR" \
  --bundle "$TMP/outdated.json" "${BASE[@]}"
grep -qF "CRM record looks outdated (97 days since last activity)" "$TMP/out" || fail "outdated risk line missing"
ok "outdated risk line present"

# 17. Budget at or under the amount -> no conflict.
variant budget_ok 'b["signals"]["budget_statements"][0]["amount"] = 2000; b["signals"]["budget_statements"][0]["period"] = "mo"'
expect 0 "| Amount | \$24,000/yr | |" out "monthly budget annualised (2,000/mo = 24,000/yr) is not a conflict" \
  --bundle "$TMP/budget_ok.json" "${BASE[@]}"

# 18. JSON output is valid and lists flags in priority order.
python3 "$SCRIPT" --bundle sample_bundle_outfitters.json "${BASE[@]}" --json >"$TMP/out.json"
python3 - "$TMP/out.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
codes = [f["code"] for f in d["flags"]]
assert d["status"] == "briefed", d["status"]
assert codes == ["close_date_unclear", "budget_conflict", "unanswered_question", "overdue_commitment", "signer_first_meeting"], codes
assert len(d["agenda"]) == 4, d["agenda"]
PY
ok "--json output valid with flags in priority order"

# 19. Manual run with no external attendee information -> exit 2 telling the agent to ask (not "internal-only").
MANUAL='b["meeting"]["calendar_source"] = "manual"; b["sources"]["calendar"] = {"provider": "manual", "status": "ok"}; b["meeting"]["event_id"] = "outfitters-2026-10-06"'
variant manual_none "$MANUAL"'; b["meeting"]["attendees"] = []'
expect 2 "no external attendee information supplied" err "manual run with no attendees and no domain exits 2" \
  --bundle "$TMP/manual_none.json" "${BASE[@]}"
grep -qF "external_domains" "$TMP/err" || fail "error does not point at meeting.external_domains"
! grep -q "internal-only" "$TMP/out" "$TMP/err" || fail "missing attendee info reported as internal-only"
ok "missing attendee info is not reported as internal-only"

# 20. Manual run naming only our own rep -> same exit 2, not an internal-only skip.
variant manual_rep_only "$MANUAL"'; b["meeting"]["attendees"] = b["meeting"]["attendees"][:1]'
expect 2 "no external attendee information supplied" err "manual run with only internal attendees exits 2" \
  --bundle "$TMP/manual_rep_only.json" "${BASE[@]}"

# 21. Manual run with only the company domain -> briefs, says attendees are not named, lists the gap.
variant manual_domain "$MANUAL"'; b["meeting"]["attendees"] = b["meeting"]["attendees"][:2]; b["meeting"]["external_domains"] = ["Outfitters.example"]'
expect 0 "Briefing: Example Outfitters Co (fictional)" out "manual run with only a company domain briefs" \
  --bundle "$TMP/manual_domain.json" "${BASE[@]}"
grep -qF "External attendees not named — only the company domain (outfitters.example) was given." "$TMP/out" \
  || fail "domain-only run does not say attendees are unnamed"
grep -qF "Gaps: Attendees not named (company domain only)." "$TMP/out" || fail "domain-only gap missing"
! grep -q "Marcus Lee\*\*" "$TMP/out" || fail "domain-only run invented attendees from CRM contacts"
ok "domain-only run states the gap and invents no attendees"

# 22. Calendar event with no attendees at all -> exit 2 (calendar events always list attendees).
variant cal_empty 'b["meeting"]["attendees"] = []'
expect 2 "must list at least one attendee for a calendar event" err "calendar event without attendees exits 2" \
  --bundle "$TMP/cal_empty.json" "${BASE[@]}"

# 23. external_domains naming our own domain -> exit 2.
variant manual_internal_dom "$MANUAL"'; b["meeting"]["attendees"] = []; b["meeting"]["external_domains"] = ["eu.ledgerline.example"]'
expect 2 "is one of your internal domains" err "internal domain in external_domains rejected" \
  --bundle "$TMP/manual_internal_dom.json" "${BASE[@]}"

# 24. Regression: manual event key is derived by the script (domain-based when a domain is known), has a single
#     "manual:" prefix, and ignores whatever event_id the bundle carried.
cp sample_briefing_log.md "$TMP/log_manual.md"
expect 0 "Event key: manual:outfitters.example-2026-10-06@2026-10-06T10:00:00-07:00" out "manual event key is domain-based, single prefix" \
  --bundle "$TMP/manual_domain.json" "${BASE[@]}" --log "$TMP/log_manual.md"
variant manual_prefixed "$MANUAL"'; b["meeting"]["event_id"] = "manual:something-else"; b["meeting"]["external_domains"] = ["outfitters.example"]'
expect 0 "Skipped: already briefed manual:outfitters.example-2026-10-06@2026-10-06T10:00:00-07:00" out \
  "a different or manual:-prefixed event_id maps to the same derived key" \
  --bundle "$TMP/manual_prefixed.json" "${BASE[@]}" --log "$TMP/log_manual.md" --scheduled --lookahead-minutes 30
! grep -q "manual:manual:" "$TMP/log_manual.md" "$TMP/out" || fail "doubled manual: prefix"
ok "no manual:manual: anywhere"

# 25. crm: none says "not configured" (TL;DR, deal section, log); a failing CRM says "unreachable".
cp sample_briefing_log.md "$TMP/log_none.md"
expect 0 "Deal data not available (CRM not configured)." out "crm none TL;DR says not configured" \
  --bundle "$TMP/crm_none.json" "${BASE[@]}" --log "$TMP/log_none.md"
! grep -q "unreachable" "$TMP/out" || fail "crm none described as unreachable"
grep -qF "| not configured |" "$TMP/log_none.md" || fail "crm none not logged as 'not configured'"
ok "crm none logged as not configured"
variant crm_down 'b["sources"]["crm"] = {"provider": "hubspot", "status": "unavailable", "detail": "403 on deals search"}'
cp sample_briefing_log.md "$TMP/log_down.md"
expect 0 "Deal data not available (CRM unreachable)." out "configured-but-failing CRM says unreachable" \
  --bundle "$TMP/crm_down.json" "${BASE[@]}" --log "$TMP/log_down.md"
grep -qF "| unavailable |" "$TMP/log_down.md" || fail "failing CRM not logged as unavailable"
ok "failing CRM logged as unavailable"

# 26. Optional call-notes source (Granola) and call_transcript signals are shown with their source.
variant granola 'b["sources"]["call_notes"] = {"provider": "granola", "status": "ok"}; b["signals"]["competitor_mentions"][0]["source"] = {"type": "call_transcript", "date": "2026-09-10", "ref": "Granola: Discovery call"}'
expect 0 "Call notes: Granola ✓" out "call-notes source appears on the Sources line" \
  --bundle "$TMP/granola.json" "${BASE[@]}"
grep -qF "(Priya Sharma, call transcript Sep 10)" "$TMP/out" || fail "call_transcript source not rendered"
ok "call_transcript source rendered"
variant granola_down 'b["sources"]["call_notes"] = {"provider": "granola", "status": "unavailable", "detail": "not added to this Space"}'
expect 0 "Gaps: Call notes unavailable (not added to this Space)." out "unavailable call-notes source listed in Gaps" \
  --bundle "$TMP/granola_down.json" "${BASE[@]}"

# 27. no_history_found: no CRM record + 0 emails, 0 Slack, 0 call notes -> TL;DR says so plainly (not "No open flags").
COLD='b["crm"] = {"record_found": False, "deal": None, "contacts": [], "open_tasks": []}; b["email"]["threads"] = []; b["slack"]["mentions"] = []; b["signals"] = {}; b["sources"]["call_notes"] = {"provider": "granola", "status": "ok"}'
variant cold "$COLD"
expect 0 "**TL;DR** — No history found anywhere (no CRM record, 0 emails, 0 Slack mentions, 0 call notes) — treat as a cold first call; confirm who's joining and why." out \
  "no history anywhere is stated plainly in the TL;DR" --bundle "$TMP/cold.json" "${BASE[@]}"
! grep -q "No open flags" "$TMP/out" || fail "cold call reads as 'No open flags'"
grep -qF -- "- None found in email (0 threads), Slack (last 7 days), call notes (Granola)." "$TMP/out" || fail "open-threads empty state does not name the sources read"
grep -qF -- "1. Open by confirming who's joining, their roles and why they took the call" "$TMP/out" || fail "cold-call agenda item missing or not first"
grep -qF "Call notes: Granola ✓ (0 past calls)" "$TMP/out" || fail "call-notes count missing from Sources"
ok "cold call: open threads name the sources read, agenda opens with who's joining"
python3 "$SCRIPT" --bundle "$TMP/cold.json" "${BASE[@]}" --json | python3 -c 'import json,sys; c=[f["code"] for f in json.load(sys.stdin)["flags"]]; assert c[0]=="no_history_found", c'
ok "no_history_found is the top-priority flag"

# 28. crm: none + no comms + Slack unreachable -> says what was not read instead of "anywhere"; no call notes configured -> not mentioned.
variant cold_none "$COLD"'; b["crm"] = None; b["sources"]["crm"] = {"provider": "none", "status": "not_configured"}; b["sources"]["slack"] = {"provider": "slack", "status": "unavailable"}; del b["sources"]["call_notes"]'
expect 0 "No history found in the sources read (no CRM configured, 0 emails; not read: Slack unavailable) — treat as a cold first call" out \
  "no history with an unread source is not called 'anywhere'" --bundle "$TMP/cold_none.json" "${BASE[@]}"
grep -qF -- "- None found in email (0 threads)." "$TMP/out" || fail "open threads names unread Slack or a CRM that is not configured"
grep -qF -- "- No history with this company in any source read" "$TMP/out" || fail "no-history risk line missing"
! grep -qiE "call notes \(|Granola" "$TMP/out" || fail "call notes mentioned though not configured"
ok "empty-state lines name only the sources read"

# 28b. Risks empty state names only the sources read (crm: none, Slack unreachable, Granola read; history exists).
variant quiet 'b["crm"] = None; b["sources"]["crm"] = {"provider": "none", "status": "not_configured"}; b["sources"]["slack"] = {"provider": "slack", "status": "unavailable"}; b["sources"]["call_notes"] = {"provider": "granola", "status": "ok"}; b["signals"] = {}'
expect 0 "- None evidenced in the data read for this briefing: email (5 threads), call notes (Granola)." out \
  "risks empty state names only the sources read" --bundle "$TMP/quiet.json" "${BASE[@]}"
! grep -q "No history found" "$TMP/out" || fail "no_history_found fired although email history exists"
ok "email history suppresses no_history_found"

# 29. History present (a past Granola call) -> no_history_found does not fire.
variant warm_call "$COLD"'; b["call_notes"] = {"calls": [{"date": "2026-09-10", "title": "Intro call"}]}'
python3 "$SCRIPT" --bundle "$TMP/warm_call.json" "${BASE[@]}" --json >"$TMP/warm.json"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); c=[f["code"] for f in d["flags"]]; assert "no_history_found" not in c, c; assert "(1 past call)" in d["briefing_markdown"]' "$TMP/warm.json"
ok "one past call in the recorder suppresses no_history_found"

# 30. Manual naming: "Northpeak Outfitters" and "Northpeak Outfitters Inc." (no domain) give the same key.
NP='b["company"] = {"name": NAME}; b["meeting"] = {"calendar_source": "manual", "start": "2026-10-07T14:00:00-07:00", "attendees": [{"email": "jo@northpeak.example", "name": "Jo Park"}]}; b["sources"]["calendar"] = {"provider": "manual", "status": "ok"}'
variant np1 "${NP/NAME/\"Northpeak Outfitters\"}"
variant np2 "${NP/NAME/\"Northpeak Outfitters, Inc.\"}"
variant np3 "${NP/NAME/\"  NORTHPEAK   outfitters LLC \"}"
for v in np1 np2 np3; do
  expect 0 "Event key: manual:northpeak-outfitters-2026-10-07@2026-10-07T14:00:00-07:00" out "manual key for $v is the name slug" \
    --bundle "$TMP/$v.json" "${BASE[@]}"
done
cp sample_briefing_log.md "$TMP/log_np.md"
python3 "$SCRIPT" --bundle "$TMP/np1.json" "${BASE[@]}" --log "$TMP/log_np.md" >/dev/null
expect 0 "Skipped: already briefed manual:northpeak-outfitters-2026-10-07" out "second scheduled run with 'Inc.' is deduplicated" \
  --bundle "$TMP/np2.json" --config briefing.json --as-of 2026-10-07T13:40:00-07:00 --internal-domain ledgerline.example \
  --scheduled --lookahead-minutes 30 --log "$TMP/log_np.md"
variant np_dom "${NP/NAME/\"Northpeak Outfitters Inc.\"}"'; b["company"]["domain"] = "WWW.Northpeak.example"'
variant np_dom2 "${NP/NAME/\"Northpeak\"}"'; b["company"]["domain"] = "northpeak.example"'
expect 0 "Event key: manual:northpeak.example-2026-10-07@" out "company domain is preferred for the manual key" --bundle "$TMP/np_dom.json" "${BASE[@]}"
expect 0 "Event key: manual:northpeak.example-2026-10-07@" out "same domain, different name spelling -> same key" --bundle "$TMP/np_dom2.json" "${BASE[@]}"

# 31. Manual runs say the call is not on the calendar, right under the heading; calendar runs do not.
expect 0 "Not on your calendar — time supplied by you." out "manual run states it is not on the calendar" --bundle "$TMP/np1.json" "${BASE[@]}"
[[ "$(sed -n 2p "$TMP/out")" == "Not on your calendar — time supplied by you." ]] || fail "not-on-calendar line is not directly under the heading"
grep -qF "— Call with Northpeak Outfitters at Wed Oct 7, 14:00 UTC-07:00" "$TMP/out" || fail "manual title default missing"
variant np_nocrm "${NP/NAME/\"Northpeak Outfitters Inc.\"}"'; b["crm"] = {"record_found": False, "deal": None}'
python3 "$SCRIPT" --bundle "$TMP/np_nocrm.json" "${BASE[@]}" >"$TMP/out"
grep -qF "No CRM record found in HubSpot for Northpeak Outfitters Inc. This meeting" "$TMP/out" || fail "no-CRM line missing"
! grep -qF "Inc.." "$TMP/out" || fail "company name ending in '.' printed with a double period"
ok "not-on-calendar line sits under the heading; title defaults to 'Call with <Company>'"
python3 "$SCRIPT" --bundle sample_bundle_outfitters.json "${BASE[@]}" >"$TMP/out"
! grep -q "Not on your calendar" "$TMP/out" || fail "calendar run says not on calendar"
ok "calendar run has no not-on-calendar line"

echo "ALL TESTS PASSED ($PASS checks)"

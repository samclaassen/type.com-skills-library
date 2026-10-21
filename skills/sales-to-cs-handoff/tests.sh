#!/usr/bin/env bash
# Guardrail, edge-case and regression tests. Run from the skill directory: bash tests.sh
# Builds variants of the fictional sample bundle in a temp dir; never touches real data.
# Set PYTHON=/path/to/python3.8 to run the suite on another interpreter.
set -euo pipefail

PY="${PYTHON:-python3}"
export PYTHONDONTWRITEBYTECODE=1  # some tests import build_handoff; leave no __pycache__ in the skill folder
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
SCRIPT=build_handoff.py
ASOF=2026-10-01T09:00:00-07:00
PASS=0
# The shipped config plus a fictional free-mail domain, so tests never use a real provider's addresses.
TCFG="$TMP/handoff_test.json"
"$PY" - "$TCFG" <<'PY'
import json, sys
c = json.load(open("handoff.json", encoding="utf-8"))
c["consumer_domains"].append("freemail.example")
json.dump(c, open(sys.argv[1], "w", encoding="utf-8"))
PY
BASE=(--config "$TCFG" --as-of "$ASOF" --internal-domain corvalto.example)

ok() { PASS=$((PASS + 1)); echo "ok $PASS - $1"; }
fail() { echo "FAIL - $1"; exit 1; }

# variant NAME PYTHON_CODE : writes $TMP/NAME.json from the sample after running the code on `b`
variant() {
  "$PY" - "$TMP/$1.json" "$2" <<'PY'
import json, sys
b = json.load(open("sample_bundle_quillfen.json", encoding="utf-8"))
def meeting(mid):
    return next(m for m in b["meetings"] if m["meeting_id"] == mid)
exec(sys.argv[2])
json.dump(b, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)
PY
}

# expect CODE PATTERN STREAM NAME -- args...   (STREAM is out or err)
expect() {
  local code="$1" pattern="$2" stream="$3" name="$4"; shift 4
  set +e
  "$PY" "$SCRIPT" "$@" >"$TMP/out" 2>"$TMP/err"
  local got=$?
  set -e
  [[ "$got" == "$code" ]] || { cat "$TMP/out" "$TMP/err"; fail "$name: exit $got, expected $code"; }
  [[ "$code" == 0 || ! -s "$TMP/out" ]] || { cat "$TMP/out"; fail "$name: printed to stdout before failing"; }
  ! grep -q "Traceback" "$TMP/err" || { cat "$TMP/err"; fail "$name: traceback"; }
  grep -qF -- "$pattern" "$TMP/$stream" || { cat "$TMP/out" "$TMP/err"; fail "$name: '$pattern' not in std$stream"; }
  ok "$name"
}
has() { grep -qF -- "$1" "$TMP/out" || { cat "$TMP/out"; fail "$2: '$1' missing"; }; ok "$2"; }
lacks() { ! grep -qF -- "$1" "$TMP/out" || { cat "$TMP/out"; fail "$2: '$1' present"; }; ok "$2"; }

# ---------------------------------------------------------------- sample and determinism
bash run.sh >"$TMP/run_out"
cmp -s "$TMP/run_out" expected_output.txt || { diff "$TMP/run_out" expected_output.txt | head -20; fail "run.sh output differs from expected_output.txt"; }
ok "run.sh matches expected_output.txt byte for byte"

expect 0 "# Sales-to-CS handoff: Quillfen Freight Co (fictional)" out "sample renders the handoff doc" \
  --bundle sample_bundle_quillfen.json "${BASE[@]}"
cp "$TMP/out" "$TMP/first"
for h in "## Read this first" "## Deal snapshot" "## Why they bought" "## Success criteria and how they'll measure them" \
         "## Timeline and milestones" "## What sales promised" "## Stakeholder map" \
         "## Technical requirements, integrations, data and security" "## Objections, risks and competitors" \
         "## Don't ask them again" "## Open questions" "## Handoff checklist" "## Coverage"; do
  grep -qxF -- "$h" "$TMP/out" || fail "section missing: $h"
done
ok "all 13 sections are present"
"$PY" "$SCRIPT" --bundle sample_bundle_quillfen.json "${BASE[@]}" >"$TMP/second"
cmp -s "$TMP/first" "$TMP/second" || fail "two runs differ"
ok "output is deterministic"
! grep -q "@" "$TMP/out" || fail "an email address leaked into the doc"
ok "no email addresses are printed"

# ---------------------------------------------------------------- meetings: exclusion, dedupe, order
has "5 meetings from 6 notes" "duplicate note of the same meeting is merged"
has "(also captured as [copy 2](https://notes.granola.ai/d/sample-k4b))" "merged meeting keeps both note links"
has "1 internal-only meeting (Quillfen deal review, Sep 22)" "internal-only meeting is excluded and listed"
grep -q "^| [0-9] | Sep 22 |" "$TMP/out" && fail "internal-only meeting appears in the meetings table"
ok "internal-only meeting is not in the meetings table"
has "Quillfen Bakery tasting, Sep 10: different company" "agent-excluded meeting is listed with its reason"
has "1 note-taker bot dropped" "note-taker bot is dropped and counted"
lacks "**Notetaker**" "note-taker bot is not a stakeholder"
[[ "$(grep -n '^| 1 | Aug 20' "$TMP/out" | cut -d: -f1)" -lt "$(grep -n '^| 5 | Sep 29' "$TMP/out" | cut -d: -f1)" ]] || fail "meetings not chronological"
ok "meetings are numbered chronologically"

variant noshift 'meeting("sample-k4b")["start"] = "2026-09-15T15:00:00-04:00"'
expect 0 "6 meetings from 6 notes" out "different UTC start is not merged" --bundle "$TMP/noshift.json" "${BASE[@]}"

variant repeat 'b["meetings"].append(dict(meeting("sample-k1")))'
expect 0 "1 repeated meeting id" out "exact repeated meeting id is merged and counted" --bundle "$TMP/repeat.json" "${BASE[@]}"
variant repeat_bad 'm = dict(meeting("sample-k1")); m["title"] = "Something else"; b["meetings"].append(m)'
expect 2 "is listed twice with a different title or start" err "same id with different title exits 2" \
  --bundle "$TMP/repeat_bad.json" "${BASE[@]}"

variant incomplete 'm = meeting("sample-k5"); m["attendees_incomplete"] = True; m["notes_read"] = True'
expect 0 "attendee list incomplete" out "internal attendees + incomplete list + title match is kept" \
  --bundle "$TMP/incomplete.json" "${BASE[@]}"
has "6 meetings from 7 notes" "kept meeting is counted"

variant noatt 'm = meeting("sample-k5"); m["attendees"] = []; m["notes_read"] = True'
expect 0 "6 meetings from 7 notes" out "meeting with no participants listed is kept, not called internal-only" \
  --bundle "$TMP/noatt.json" "${BASE[@]}"
has "| 5 | Sep 22 | [Quillfen deal review](https://notes.granola.ai/d/sample-k5) | title; attendee list incomplete |" "it is shown as attendee list incomplete"

variant internal_dup 'm = dict(meeting("sample-k5")); m["meeting_id"] = "sample-k5b"; m["url"] = "https://notes.granola.ai/d/sample-k5b"; b["meetings"].append(m)'
expect 0 "1 internal-only meeting (Quillfen deal review, Sep 22)" out "two notes of one internal meeting are listed once" \
  --bundle "$TMP/internal_dup.json" "${BASE[@]}"

variant thin 'b["meetings"] = [meeting("sample-k6")]; b["findings"] = {}; b["stakeholders"] = []'
expect 0 "Thin record: only 1 sales meeting found" out "a single meeting is flagged as thin" --bundle "$TMP/thin.json" "${BASE[@]}"

variant empty 'b["meetings"] = []; b["findings"] = {}; b["stakeholders"] = []; b["excluded_meetings"] = []'
expect 0 "No sales meetings with Quillfen Freight Co (fictional) were found" out "no meetings is said plainly" \
  --bundle "$TMP/empty.json" "${BASE[@]}"
has "**Meetings read**: none." "coverage says none read"
has "None captured in the 4 email threads and the CRM deal read." "empty sections name what was read"

# ---------------------------------------------------------------- promises
expect 0 "Conflicting terms on First-year discount" out "promise conflict across meetings is flagged" \
  --bundle sample_bundle_quillfen.json "${BASE[@]}"
has "Latest stated: 20% off year one if signed by Sep 30" "conflict shows the latest statement without resolving it"
has "Promised but **not in the contract**" "not-in-contract promises are flagged"
has "Roadmap items sold: Multi-depot routing" "roadmap promise is flagged"
has "Not checked against the contract (the contract does not settle them either way): Dispatcher training and P1 response time." "unchecked promises are named"
has "notes: “Dana offered 15% off the first year.” ([Commercials and rollout plan, Sep 15](https://notes.granola.ai/d/sample-k4a))" \
  "quote from the merged duplicate note resolves to the primary meeting link"
has "— Dana Ortiz, Email from Dana Ortiz, Sep 30 · email" "email-sourced promise is cited with its date"

variant sameterms 'p = b["findings"]["promises"]; p[3]["terms"] = "20% Off year one, if signed by Sep 30."'
expect 0 "## What sales promised" out "same terms with different case and punctuation" --bundle "$TMP/sameterms.json" "${BASE[@]}"
lacks "Conflicting terms" "no conflict when only case and punctuation differ"

variant nocontract 'b["deal"]["contract_read"] = False'
expect 0 "Not checked against the contract (the contract was not read)" out "unread contract is stated" \
  --bundle "$TMP/nocontract.json" "${BASE[@]}"

variant noquote 'del b["findings"]["promises"][0]["quote"]'
expect 2 "\$.findings.promises[0].quote: is required" err "promise without a verbatim quote exits 2" \
  --bundle "$TMP/noquote.json" "${BASE[@]}"
grep -qF "noquote.json" "$TMP/err" || fail "error does not name the bundle file"
ok "error names the bundle file"

variant badcat 'b["findings"]["promises"][1]["category"] = "freebie"'
expect 2 "\$.findings.promises[1].category: must be one of" err "unknown promise category exits 2" \
  --bundle "$TMP/badcat.json" "${BASE[@]}"

variant quoteflag 'del b["findings"]["pains"][0]["quote_from"]'
expect 2 "\$.findings.pains[0].quote_from" err "quote without quote_from exits 2" --bundle "$TMP/quoteflag.json" "${BASE[@]}"

variant strip 'b["findings"]["pains"][0]["quote"] = "\"Every load gets typed in twice.\""'
expect 0 "“Every load gets typed in twice.”" out "surrounding quote marks are not doubled" --bundle "$TMP/strip.json" "${BASE[@]}"
lacks "“\"" "no doubled quote marks"

# ---------------------------------------------------------------- transcripts and sources of findings
variant denied_quote 'b["findings"]["objections"][1]["quote_from"] = "transcript"'
expect 2 "quote_from is transcript but the transcript for meeting 'sample-k3' is denied" err \
  "transcript quote from a denied transcript exits 2" --bundle "$TMP/denied_quote.json" "${BASE[@]}"

variant email_transcript 'b["findings"]["promises"][7]["quote_from"] = "transcript"'
expect 2 "quote_from is transcript but the source is not a meeting" err "transcript quote from email exits 2" \
  --bundle "$TMP/email_transcript.json" "${BASE[@]}"

variant unknown_mid 'b["findings"]["risks"][0]["source"]["meeting_id"] = "sample-zz"'
expect 2 "unknown meeting 'sample-zz'" err "finding citing an unknown meeting exits 2" --bundle "$TMP/unknown_mid.json" "${BASE[@]}"

variant internal_cite 'b["findings"]["risks"][0]["source"]["meeting_id"] = "sample-k5"; b["findings"]["risks"][0]["quote_from"] = "notes"'
expect 2 "'sample-k5' is an internal-only meeting" err "finding citing an internal-only meeting exits 2" \
  --bundle "$TMP/internal_cite.json" "${BASE[@]}"

expect 0 "Transcript access: 4 ok, 1 denied, 0 unavailable, 0 not requested." out "transcript access is counted after merging" \
  --bundle sample_bundle_quillfen.json "${BASE[@]}"
has "Transcript access denied for 1 meeting" "denied transcript is a named gap"

# ---------------------------------------------------------------- stakeholders
grep -c '^- \*\*Marcus Bell\*\*' "$TMP/out" | grep -qx 1 || fail "Marcus Bell listed more than once"
ok "duplicate stakeholder entries merge into one"
has "1 duplicate stakeholder entry" "stakeholder merge is counted"
has "⚠️ Went quiet: last seen Aug 27, missed the last 3 meetings." "went-quiet stakeholder is flagged"
has "**Hannah Cole**, Dispatch Manager — end user. 1 meeting (Sep 3). ⚠️ Appeared once (Sep 3)." "appeared-once stakeholder is flagged"
has "**Jordan Lee** — role not recorded. 1 meeting (Aug 27). ⚠️ Appeared once (Aug 27). Not described in the notes. Email domain is not the account's." \
  "unlisted attendee from another domain is added and labelled"
has "**Tomás Ruiz**, IT Lead" "unicode names render"
has "Our side (ask them for context): Dana Ortiz (4 meetings), Sam Kim (3 meetings)" "our side is listed by meeting count"

variant ghost_signer 'b["stakeholders"].append({"name": "Priya Anand", "title": "COO", "roles": ["signer"]})'
expect 0 "Priya Anand (COO) is the signer but was not in any meeting read." out "signer never in a meeting is flagged" \
  --bundle "$TMP/ghost_signer.json" "${BASE[@]}"

variant care_nosrc 'del b["stakeholders"][0]["cares_about"][0]["source"]'
expect 2 "\$.stakeholders[0].cares_about[0].source: required object is missing" err "cares-about item without a source exits 2" \
  --bundle "$TMP/care_nosrc.json" "${BASE[@]}"

variant nochamp 'b["stakeholders"][0]["roles"] = ["day_to_day"]'
expect 0 "No champion identified in the meetings or CRM." out "missing champion is flagged" --bundle "$TMP/nochamp.json" "${BASE[@]}"
has "- [ ] Champion or day-to-day owner identified" "checklist shows the missing champion"

variant signer_only 'b["stakeholders"][1]["roles"] = ["signer"]; b["stakeholders"][2]["roles"] = ["signer"]'
expect 0 "## Stakeholder map" out "signer without economic-buyer role" --bundle "$TMP/signer_only.json" "${BASE[@]}"
lacks "No economic buyer identified" "a signer counts as the economic buyer"

# ---------------------------------------------------------------- milestones and dates
expect 0 "⚠️ Milestone date already passed: Carrier API connected in staging (was due Oct 10)." out "passed milestone is flagged" \
  --bundle sample_bundle_quillfen.json --config handoff.json --as-of 2026-10-12T09:00:00-07:00 --internal-domain corvalto.example
expect 0 "Milestone due in 9 days: Carrier API connected in staging (Oct 10)." out "milestone due soon is flagged" \
  --bundle sample_bundle_quillfen.json "${BASE[@]}"
"$PY" "$SCRIPT" --bundle sample_bundle_quillfen.json --config handoff.json --as-of 2027-01-05T09:00:00-08:00 --internal-domain corvalto.example >"$TMP/out"
has "Aug 20, 2026" "dates outside the as-of year include the year"

# ---------------------------------------------------------------- coverage gaps and source status
variant freeplan 'b["sources"]["meeting_notes"]["plan"] = "free"'
expect 0 "Granola free (Basic) plan: only notes from the last 30 days (Sep 1 onward)" out "free-plan 30-day window is a gap" \
  --bundle "$TMP/freeplan.json" "${BASE[@]}"
variant personal 'b["sources"]["meeting_notes"]["scopes"] = ["personal"]'
expect 0 "Only personal Granola notes were searchable" out "personal-only scope is a gap" --bundle "$TMP/personal.json" "${BASE[@]}"
variant cycle 'b["deal"]["created_date"] = "2026-07-01"'
expect 0 "The CRM deal was created Jul 1 but the first meeting found is Aug 20 (50 days later)." out "sales-cycle gap is reported" \
  --bundle "$TMP/cycle.json" "${BASE[@]}"
variant notes_down 'b["sources"]["meeting_notes"] = {"provider": "granola", "status": "denied", "detail": "workspace blocks MCP access"}'
expect 0 "Meeting notes (Granola) denied (workspace blocks MCP access)" out "denied meeting-notes source is a gap" \
  --bundle "$TMP/notes_down.json" "${BASE[@]}"
has "Meeting notes: Granola access denied" "sources line shows the denied notes source"
variant crm_none 'b["sources"]["crm"] = {"provider": "none", "status": "not_configured"}; b["deal"] = None'
expect 0 "No CRM configured: contract terms were not checked" out "crm not configured is stated" --bundle "$TMP/crm_none.json" "${BASE[@]}"
has "CRM: not configured" "sources line shows CRM not configured"
has 'Handoff key: `account:quillfenfreight.example`' "without a deal the key uses the account domain"
variant crm_down 'b["sources"]["crm"]["status"] = "unavailable"; b["sources"]["crm"]["detail"] = "HTTP 503"; b["deal"] = None'
expect 0 "CRM (HubSpot) unreachable: HTTP 503." out "unreachable CRM is a gap" --bundle "$TMP/crm_down.json" "${BASE[@]}"
variant crm_miss 'b["deal"] = None'
expect 0 "No CRM deal matched this account" out "CRM read but no deal is a gap" --bundle "$TMP/crm_miss.json" "${BASE[@]}"
variant email_denied 'b["sources"]["email"]["status"] = "denied"'
expect 0 "Email (Gmail) access denied." out "denied email is a gap" --bundle "$TMP/email_denied.json" "${BASE[@]}"

variant uni 'b["account"] = {"name": "Société Générale de Fret (fictional)", "domains": [], "aliases": []}; b["deal"] = None; b["sources"]["crm"] = {"provider": "none", "status": "not_configured"}; [m.__setitem__("matched_by", ["title"]) for m in b["meetings"]]'
expect 0 "# Sales-to-CS handoff: Société Générale de Fret (fictional)" out "unicode account name renders" --bundle "$TMP/uni.json" "${BASE[@]}"
has 'Handoff key: `account:societe-generale-de-fret-fictional`' "key falls back to an ASCII slug of the name"

# ---------------------------------------------------------------- input validation
printf '{"schema_version": 1,,}' >"$TMP/broken.json"
expect 2 "is not valid JSON (line 1" err "malformed JSON exits 2 with line and column" --bundle "$TMP/broken.json" "${BASE[@]}"
expect 2 "file not found" err "missing bundle exits 2" --bundle "$TMP/nope.json" "${BASE[@]}"
expect 2 "--as-of" err "as-of without offset exits 2" --bundle sample_bundle_quillfen.json --config handoff.json \
  --as-of 2026-10-01T09:00:00 --internal-domain corvalto.example
expect 2 "--internal-domain" err "missing internal domain exits 2" --bundle sample_bundle_quillfen.json --config handoff.json --as-of "$ASOF"
variant v2 'b["schema_version"] = 2'
expect 2 "\$.schema_version: must be 1" err "wrong schema version exits 2" --bundle "$TMP/v2.json" "${BASE[@]}"
variant owndomain 'b["account"]["domains"] = ["corvalto.example"]'
expect 2 "is one of your internal domains" err "internal domain as account domain exits 2" --bundle "$TMP/owndomain.json" "${BASE[@]}"
variant freemail 'b["account"]["domains"] = ["freemail.example"]'
expect 2 "is a personal email provider" err "consumer domain as account domain exits 2" --bundle "$TMP/freemail.json" "${BASE[@]}"
variant nostart 'del b["meetings"][0]["start"]'
expect 2 "\$.meetings[0].start: is required" err "meeting without start exits 2" --bundle "$TMP/nostart.json" "${BASE[@]}"
variant naive 'b["meetings"][0]["start"] = "2026-08-20T10:00:00"'
expect 2 "\$.meetings[0].start: '2026-08-20T10:00:00' has no UTC offset" err "meeting start without offset exits 2" \
  --bundle "$TMP/naive.json" "${BASE[@]}"
variant nomatch 'b["meetings"][0]["matched_by"] = []'
expect 2 "\$.meetings[0].matched_by: list how this meeting was matched" err "meeting without match reason exits 2" \
  --bundle "$TMP/nomatch.json" "${BASE[@]}"
variant badstatus 'b["sources"]["email"]["status"] = "maybe"'
expect 2 "\$.sources.email.status: must be one of ok, unavailable, denied, not_configured" err "bad source status exits 2" \
  --bundle "$TMP/badstatus.json" "${BASE[@]}"
variant badsearch 'b["search"]["to"] = "2026-07-01"'
expect 2 "\$.search.to: is before \$.search.from" err "search window ending before it starts exits 2" --bundle "$TMP/badsearch.json" "${BASE[@]}"
"$PY" - "$TMP/badconfig.json" <<'PY'
import json, sys
c = json.load(open("handoff.json", encoding="utf-8"))
del c["checklist"]
json.dump(c, open(sys.argv[1], "w"))
PY
expect 2 "missing key(s): checklist" err "config missing a key exits 2" --bundle sample_bundle_quillfen.json --config "$TMP/badconfig.json" \
  --as-of "$ASOF" --internal-domain corvalto.example

variant pipe 'meeting("sample-k2")["title"] = "Deep dive | TMS"'
expect 0 'Deep dive \| TMS' out "pipes in titles are escaped in tables" --bundle "$TMP/pipe.json" "${BASE[@]}"

# ---------------------------------------------------------------- scheduled mode and the log (two-phase)
cp sample_handoff_log.md "$TMP/log.md"
expect 2 "--scheduled needs --log" err "scheduled run without a log exits 2" --bundle sample_bundle_quillfen.json "${BASE[@]}" --scheduled
expect 0 "# Sales-to-CS handoff" out "scheduled dry run prints the doc" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/log.md" --scheduled --dry-run
cmp -s sample_handoff_log.md "$TMP/log.md" || fail "--dry-run modified the log"
ok "--dry-run leaves the log unchanged"
expect 0 "DRAFT Slack post" out "slack draft renders" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/log.md" --format slack
cmp -s sample_handoff_log.md "$TMP/log.md" || fail "a draft format wrote the log"
ok "drafts never write the log"
expect 0 "# Sales-to-CS handoff" out "H1: a person asking (doc with --log) gets the doc" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/log.md"
cmp -s sample_handoff_log.md "$TMP/log.md" || fail "H1: a person-asked run wrote the log"
ok "H1: a person-asked run never writes the log"
variant open_deal 'b["deal"]["closed_won"] = False; b["deal"]["stage"] = "Contract sent"'
expect 0 "This is a pre-close draft." out "H1: a pre-close doc run with --log prints the draft" --bundle "$TMP/open_deal.json" "${BASE[@]}" --log "$TMP/pre.md"
[[ ! -e "$TMP/pre.md" ]] || fail "H1: a pre-close doc run created a log row"
ok "H1: a pre-close doc run records nothing, so the post-close handoff is not skipped"
expect 0 "# Sales-to-CS handoff" out "first scheduled run writes the doc" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/log.md" --scheduled
grep -qF "| hubspot:deal/48213 | Quillfen Freight Co (fictional) | 2026-09-30 | pending | $ASOF |  | 5 | 8 | 1 |" "$TMP/log.md" || { cat "$TMP/log.md"; fail "pending row content"; }
ok "X4: scheduled run records key, account, close date, pending, time, meetings, promises and conflicts"
grep -qF "| hubspot:deal/47790 | Fernquill Supply (fictional) | 2026-09-12 | delivered |" "$TMP/log.md" || fail "earlier rows lost"
ok "X4: earlier log rows are kept when the log is rewritten"
expect 4 "is pending since $ASOF" err "X4: rerunning a pending handoff exits 4" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/log.md" --scheduled
grep -qF -- "--confirm-delivered hubspot:deal/48213" "$TMP/err" || fail "pending message does not say how to confirm"
ok "X4: the pending message names --confirm-delivered and --resume-pending"
expect 0 "# Sales-to-CS handoff" out "X4: --resume-pending prints a pending handoff again" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/log.md" --scheduled --resume-pending
[[ "$(grep -c 'hubspot:deal/48213' "$TMP/log.md")" -eq 1 ]] || fail "resume added a row"
ok "X4: resuming keeps one pending row"
expect 2 "--resume-pending only applies to --scheduled" err "--resume-pending without --scheduled exits 2" --bundle sample_bundle_quillfen.json "${BASE[@]}" --resume-pending
expect 0 "Confirmed: hubspot:deal/48213 marked delivered." out "X4: --confirm-delivered marks the handoff delivered" \
  --confirm-delivered hubspot:deal/48213 --as-of 2026-10-01T09:30:00-07:00 --log "$TMP/log.md"
grep -qF "| hubspot:deal/48213 | Quillfen Freight Co (fictional) | 2026-09-30 | delivered | $ASOF | 2026-10-01T09:30:00-07:00 | 5 | 8 | 1 |" "$TMP/log.md" || { cat "$TMP/log.md"; fail "delivered row content"; }
ok "X4: the row records when delivery was confirmed"
expect 0 "Already confirmed: hubspot:deal/48213" out "X4: confirming twice is harmless" --confirm-delivered hubspot:deal/48213 --as-of "$ASOF" --log "$TMP/log.md"
expect 2 "is not in" err "X4: confirming a key that was never recorded exits 2" --confirm-delivered hubspot:deal/1 --as-of "$ASOF" --log "$TMP/log.md"
expect 2 "--confirm-delivered needs --log" err "X4: confirming without a log exits 2" --confirm-delivered hubspot:deal/1 --as-of "$ASOF"
expect 0 "Skipped: already handed off hubspot:deal/48213 (delivered 2026-10-01T09:30:00-07:00)." out "scheduled run after delivery is skipped" \
  --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/log.md" --scheduled
[[ "$(wc -l <"$TMP/out")" -eq 1 ]] || fail "skip notice must be one line"
ok "skip notice is one line"
[[ "$(grep -c 'hubspot:deal/48213' "$TMP/log.md")" -eq 1 ]] || fail "log row not written exactly once"
ok "log has exactly one row for the handoff"

expect 0 "Skipped: hubspot:deal/48213 is not a closed-won deal" out "scheduled run skips a deal that is not closed-won" \
  --bundle "$TMP/open_deal.json" "${BASE[@]}" --scheduled --log "$TMP/log2.md"
[[ ! -e "$TMP/log2.md" ]] || fail "skipped run created a log"
ok "skipped run writes no log"
expect 0 "not marked closed-won (stage: Contract sent). This is a pre-close draft." out "person-asked run on an open deal says pre-close" \
  --bundle "$TMP/open_deal.json" "${BASE[@]}"
expect 0 "# Sales-to-CS handoff" out "new log is created on the first scheduled write" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/new_log.md" --scheduled
head -1 "$TMP/new_log.md" | grep -qxF "# Sales-to-CS handoff log" || fail "new log has no title"
ok "new log has the title and header"
[[ ! -e "$TMP/new_log.md.lock" ]] || fail "lock file left behind"
ok "X4: the lock file is removed after the run"
variant notes_down_sched 'b["sources"]["meeting_notes"]["status"] = "unavailable"'
expect 3 "sources.meeting_notes is unavailable" err "X4: scheduled run with the meeting list not ok exits 3" \
  --bundle "$TMP/notes_down_sched.json" "${BASE[@]}" --scheduled --log "$TMP/log3.md"
[[ ! -e "$TMP/log3.md" ]] || fail "a run without readable meetings recorded the handoff"
ok "X4: nothing is recorded when the meeting list was not read"
touch "$TMP/lock_log.md.lock"
expect 5 "another run holds the lock" err "X4: a second concurrent run exits 5" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/lock_log.md" --scheduled
[[ ! -e "$TMP/lock_log.md" ]] || fail "locked run wrote the log"
ok "X4: the locked run wrote nothing"
touch -d "20 minutes ago" "$TMP/lock_log.md.lock"
expect 0 "# Sales-to-CS handoff" out "X4: a lock older than 15 minutes is stale and removed" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/lock_log.md" --scheduled
printf '# Sales-to-CS handoff log\n\n| handoff_key | account | close_date | written_at | meetings_read | promises | conflicts |\n|---|---|---|---|---|---|---|\n| hubspot:deal/48213 | Old | 2026-09-30 | 2026-09-30T10:00:00-07:00 | 5 | 8 | 1 |\n' >"$TMP/legacy_log.md"
expect 0 "Skipped: already handed off hubspot:deal/48213 (delivered 2026-09-30T10:00:00-07:00)." out "X4: rows of a pre-confirm (7-column) log count as delivered" \
  --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/legacy_log.md" --scheduled
sed -i 's/hubspot:deal\/48213/hubspot:deal\/1/' "$TMP/legacy_log.md"
expect 0 "# Sales-to-CS handoff" out "a scheduled run on a 7-column log writes the doc" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/legacy_log.md" --scheduled
grep -qxF "| hubspot:deal/1 | Old | 2026-09-30 | delivered | 2026-09-30T10:00:00-07:00 | 2026-09-30T10:00:00-07:00 | 5 | 8 | 1 |" "$TMP/legacy_log.md" || { cat "$TMP/legacy_log.md"; fail "legacy upgrade"; }
ok "X4: a 7-column log is upgraded to the current format on write"
echo "random notes" >"$TMP/bad_log.md"
expect 2 "not a handoff log" err "unrecognised log refuses to run" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/bad_log.md"
printf '# Sales-to-CS handoff log\n\n| handoff_key | account | close_date | status | written_at | delivered_at | meetings_read | promises | conflicts |\n|---|---|---|---|---|---|---|---|---|\n| a | b |\n' >"$TMP/short_log.md"
expect 2 "has 2 columns, expected 9" err "log row with wrong column count exits 2" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/short_log.md"
printf '# Sales-to-CS handoff log\n\n| handoff_key | account | close_date | status | written_at | delivered_at | meetings_read | promises | conflicts |\n|---|---|---|---|---|---|---|---|---|\n| a | b | c | maybe | e | f | 1 | 2 | 3 |\n' >"$TMP/status_log.md"
expect 2 "has status 'maybe'" err "X3: log row with an unknown status exits 2" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/status_log.md" --scheduled

# ---------------------------------------------------------------- H4 / X3: never crash after output
expect 2 "does not exist" err "H4: log in a missing folder exits 2 before printing" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/nope/log.md" --scheduled
mkdir -p "$TMP/logdir.md"
expect 2 "is not a file" err "X3: a log path that is a folder exits 2" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/logdir.md" --scheduled
if [[ "$(id -u)" -ne 0 ]]; then
  mkdir -p "$TMP/ro" && chmod 555 "$TMP/ro"
  expect 2 "is not writable" err "H4: log in a read-only folder exits 2 before printing" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/ro/log.md" --scheduled
  expect 2 "is not writable" err "X3: confirm in a read-only folder exits 2" --confirm-delivered x:1 --as-of "$ASOF" --log "$TMP/ro/log.md"
  chmod 755 "$TMP/ro"
  mkdir -p "$TMP/locked" && cp sample_bundle_quillfen.json "$TMP/locked/b.json" && chmod 000 "$TMP/locked"
  expect 2 "is not a readable file (unreadable)" err "X3: a bundle inside an unreadable folder exits 2 (Python 3.8 raised here)" \
    --bundle "$TMP/locked/b.json" "${BASE[@]}"
  expect 2 "is not writable" err "X3: a log inside an unreadable folder exits 2" \
    --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/locked/log.md" --scheduled
  chmod 755 "$TMP/locked"
fi
expect 2 "is not a readable file (folder)" err "X3: a bundle path that is a folder exits 2" --bundle "$TMP" "${BASE[@]}"
for mutation in 'c["quiet_days"] = "14"' 'c["quiet_days"] = 10**9' 'c["role_labels"] = []' 'c["checklist"] = {"a": 1}' 'c["checklist"][0]["satisfied_by"] = "pains"' \
                'c["bot_email_patterns"] = [1]' 'c["key_roles"] = ["boss"]' 'c = []' 'c["checklist"][0]["satisfied_by"] = ["nope"]'; do
  "$PY" - "$TMP/cfg.json" "$mutation" <<'PY'
import json, sys
c = json.load(open("handoff.json", encoding="utf-8"))
exec(sys.argv[2])
json.dump(c, open(sys.argv[1], "w"))
PY
  expect 2 "config" err "X3: bad config ($mutation) exits 2" --bundle sample_bundle_quillfen.json --config "$TMP/cfg.json" --as-of "$ASOF" --internal-domain corvalto.example
done

variant talbot 'meeting("sample-k1")["attendees"] += [{"name": "Ann Talbot", "email": "ann.talbot@quillfenfreight.example"}, {"email": "pat.lee@quillfenfreight.example"}, {"email": "bot@meetbot.example"}]'
expect 0 "**Ann Talbot**" out "a surname ending in bot is a person, not a note-taker" --bundle "$TMP/talbot.json" "${BASE[@]}"
has "2 note-taker bots dropped" "an address whose local part is bot@ is dropped"
has "**pat\.lee**" "an attendee listed by email only is shown without the address"
! grep -q "@" "$TMP/out" || fail "an email address leaked into the doc"
ok "email-only attendees never print their address"

# ---------------------------------------------------------------- H2: who counts as a customer
variant h2_internal 'm = meeting("sample-k5"); m["attendees"] = [{"name": "Dana Ortiz"}, {"name": "Sam Kim", "email": "samkim.home@freemail.example"}]; m["notes_read"] = True'
expect 0 "1 internal-only meeting (Quillfen deal review, Sep 22)" out "H2: name-only and personal-email colleagues make an internal-only meeting" \
  --bundle "$TMP/h2_internal.json" "${BASE[@]}"
has "5 meetings from 6 notes" "H2: the internal review is not counted as a customer meeting"
variant h2_staff 'meeting("sample-k6")["attendees"].append({"name": "Sam Kim", "email": "samkim.home@freemail.example"})'
expect 0 "Our side (ask them for context): Dana Ortiz (4 meetings), Sam Kim (4 meetings)" out "H2: a colleague on a personal address counts as our side" \
  --bundle "$TMP/h2_staff.json" "${BASE[@]}"
lacks "**Sam Kim**" "H2: a colleague on a personal address is not listed as a stakeholder"
variant h2_unclassified 'meeting("sample-k5")["attendees"].append({"name": "Riley Shaw", "email": "riley.shaw@freemail.example"})'
expect 0 "1 meeting with no customer attendee identified" out "H2: only unknown or personal-email outsiders → unclassified, excluded" \
  --bundle "$TMP/h2_unclassified.json" "${BASE[@]}"
has "5 meetings from 6 notes" "H2: the unclassified meeting is not a customer meeting"
lacks "**Riley Shaw**" "H2: people from an unclassified meeting are not stakeholders"
variant h2_cite 'meeting("sample-k5")["attendees"].append({"name": "Riley Shaw"}); b["findings"]["risks"][0]["source"]["meeting_id"] = "sample-k5"; b["findings"]["risks"][0]["quote_from"] = "notes"'
expect 2 "no customer attendee identified" err "H2: a finding citing an unclassified meeting exits 2" --bundle "$TMP/h2_cite.json" "${BASE[@]}"
variant h2_vouched 'm = meeting("sample-k5"); m["attendees"].append({"name": "Riley Shaw", "email": "riley.shaw@freemail.example"}); m["attendees_incomplete"] = True'
expect 0 "6 meetings from 7 notes" out "H2: an unclassified meeting is kept when marked incomplete and matched by title" \
  --bundle "$TMP/h2_vouched.json" "${BASE[@]}"

# ---------------------------------------------------------------- H3 / X1: links and escaping
variant h3_urls 'meeting("sample-k1")["url"] = "javascript:alert(1)"; meeting("sample-k2")["url"] = "https://notes.example/a b)c<d>\"e|f"; meeting("sample-k3")["url"] = "data:text/html,<b>x</b>"; b["deal"]["url"] = "JavaScript:alert(2)"; b["findings"]["promises"][7]["source"]["url"] = "//no-scheme.example/x"'
expect 0 "4 links dropped because they were not an http(s) URL" out "H3: non-http(s) links are dropped and counted as a gap" --bundle "$TMP/h3_urls.json" "${BASE[@]}"
lacks "javascript:" "H3: no javascript: link survives"
lacks "data:text" "H3: no data: link survives"
has "| 1 | Aug 20 | Quillfen &lt;&gt; Corvalto intro &amp; discovery |" "H3: a dropped link keeps its text"
has "(https://notes.example/a%20b%29c%3Cd%3E%22e%7Cf)" "H3: spaces, ), <, >, \" and | in URLs are percent-encoded"
variant x1_md 'b["findings"]["pains"][0]["text"] = "[click](https://evil.example) ![img](https://evil.example/i.png)"; meeting("sample-k2")["title"] = "<script>alert(1)</script> <img src=x onerror=alert(1)>"; b["stakeholders"][0]["name"] = "Elena [Novak](javascript:x)"'
expect 0 "\\[click\\](https\\://evil\\.example)" out "X1: brackets in values cannot open a markdown link" --bundle "$TMP/x1_md.json" "${BASE[@]}"
lacks "[click](" "X1: no injected markdown link"
lacks "![img](" "X1: no injected markdown image"
has "&lt;script&gt;alert(1)&lt;/script&gt;" "X1: raw HTML in a title is neutralised"
lacks "<script>" "X1: no raw script tag"
lacks "<img" "X1: no raw img tag"
lacks "[Novak](" "X1: link syntax in a person's name is inert"
has "**Elena \\[Novak\\](javascript:x)**" "X1: the name is shown with its brackets escaped"
variant x1_slack 'b["account"]["name"] = "<!channel> A & B"; b["findings"]["pains"][0]["text"] = "<https://evil.example|click> <@U123> <#C999> <!here> <!everyone> <!subteam^S1>"; b["deal"]["owner"] = "<@U777>"; b["findings"]["objections"][0]["text"] = "<!channel>"; b["stakeholders"][1]["name"] = "<@U888>"'
expect 0 "*New customer handoff: &lt;!channel&gt; A &amp; B*" out "X1: Slack draft escapes &, < and > in the account name" \
  --bundle "$TMP/x1_slack.json" "${BASE[@]}" --format slack --post-to "<!channel>" --doc-url "https://type.com/d?a=1&b=2"
for bad in "<!channel>" "<!here>" "<!everyone>" "<@U" "<#C" "<https://evil" "<!subteam"; do
  grep -qF -- "$bad" "$TMP/out" && { cat "$TMP/out"; fail "X1: '$bad' survived in the Slack draft"; }
done
ok "X1: no Slack mention, channel ping or injected link survives"
has "DRAFT Slack post for &lt;!channel&gt;" "X1: the channel name on the draft is escaped"
has "Full handoff: https://type.com/d?a=1&amp;b=2" "X1: & in the doc link is escaped for Slack"
expect 2 "--doc-url: 'javascript:alert(1)' is not an http(s) URL" err "X1: a non-http(s) --doc-url exits 2" \
  --bundle sample_bundle_quillfen.json "${BASE[@]}" --format slack --doc-url "javascript:alert(1)"
expect 0 "Sales-to-CS handoff: &lt;!channel&gt; A &amp; B" out "X1: CRM-note draft neutralises raw HTML" --bundle "$TMP/x1_slack.json" "${BASE[@]}" --format crm-note
lacks "<!channel>" "X1: CRM-note draft has no raw angle brackets from data"
variant x1_ref 'b["deal"]["record_ref"] = "hubspot:deal/1 | x"'
expect 2 "is the handoff log key: use only letters" err "X1: a record_ref that would break the log table exits 2" --bundle "$TMP/x1_ref.json" "${BASE[@]}"

# ---------------------------------------------------------------- H5: contact details in free text
variant h5 'b["stakeholders"][4]["note"] = "Reach him at owen.p@freemail.example, +1 (415) 555-0100 or 415.555.0199"; b["findings"]["answered_questions"][0]["answer"] = "Call 4155550100 or mailto:ops@quillfenfreight.example"; b["findings"]["pains"][0]["quote"] = "Ping me on +44 20 7946 0958, every load gets typed in twice."'
expect 0 "Note (CRM or user): Reach him at (email removed), (phone removed) or (phone removed)" out "H5: emails and phones are removed from notes" \
  --bundle "$TMP/h5.json" "${BASE[@]}"
has "Call (phone removed) or (email removed)" "H5: a bare 10-digit number after 'Call' and mailto addresses are removed"
has "“Ping me on (phone removed), every load gets typed in twice.”" "H5: phones are removed from quotes"
! grep -q "@" "$TMP/out" || fail "H5: an email address leaked into the doc"
ok "H5: no email address in the doc"
lacks "555-0100" "H5: no phone number in the doc"
"$PY" "$SCRIPT" --bundle "$TMP/h5.json" "${BASE[@]}" --format slack >"$TMP/out"
"$PY" "$SCRIPT" --bundle "$TMP/h5.json" "${BASE[@]}" --format crm-note >>"$TMP/out"
"$PY" "$SCRIPT" --bundle "$TMP/h5.json" "${BASE[@]}" --json >>"$TMP/out"
! grep -q "@\|555-0100\|4155550100" "$TMP/out" || fail "H5: contact details leaked into a draft or the JSON"
ok "H5: drafts and JSON carry no contact details"
"$PY" "$SCRIPT" --bundle sample_bundle_quillfen.json "${BASE[@]}" >"$TMP/out"
lacks "removed)" "H5: dates, amounts and counts in the sample are not mistaken for contact details"

# ---------------------------------------------------------------- H8 / X2: strict dates, numbers and required keys
for bad in "20260820T100000-0700" "2026-W34-4T10:00:00-07:00" "2026-232T10:00:00-07:00" "2026-08-20 10:00:00-07:00" \
           "2026-08-20T10:00:00.5-07:00" "2026-08-20T10-07:00" "2026-02-30T10:00:00-07:00" "0001-01-01T00:00:00+05:00" "2026-08-20T10:00:00+0700"; do
  variant baddt "meeting(\"sample-k1\")[\"start\"] = \"$bad\""
  expect 2 "\$.meetings[0].start" err "X2: datetime '$bad' exits 2" --bundle "$TMP/baddt.json" "${BASE[@]}"
done
for bad in "20260801" "2026-W31" "2026-W31-6" "2026-213" "2026-8-1" "2026-02-30" "1899-12-31"; do
  variant badday "b[\"search\"][\"from\"] = \"$bad\""
  expect 2 "\$.search.from" err "X2: date '$bad' exits 2" --bundle "$TMP/badday.json" "${BASE[@]}"
done
variant zulu 'meeting("sample-k1")["start"] = "2026-08-20T17:00:00Z"; b["search"]["from"] = "2026-08-01T00:00:00.000Z"'
expect 0 "## Coverage" out "X2: Z offsets and a datetime where a date is expected are accepted" --bundle "$TMP/zulu.json" "${BASE[@]}"
expect 2 "--as-of" err "X2: compact --as-of exits 2" --bundle sample_bundle_quillfen.json --config handoff.json --as-of 20261001T090000-0700 --internal-domain corvalto.example
sed 's/"amount": 48000/"amount": NaN/' sample_bundle_quillfen.json >"$TMP/nan.json"
expect 2 "NaN is not a valid JSON number" err "H8: a NaN amount exits 2" --bundle "$TMP/nan.json" "${BASE[@]}"
sed 's/"amount": 48000/"amount": -Infinity/' sample_bundle_quillfen.json >"$TMP/inf.json"
expect 2 "-Infinity is not a valid JSON number" err "H8: an Infinity amount exits 2" --bundle "$TMP/inf.json" "${BASE[@]}"
sed 's/"amount": 48000/"amount": 1e999/' sample_bundle_quillfen.json >"$TMP/huge.json"
expect 2 "\$.deal.amount: must be a finite" err "H8: an overflowing amount exits 2" --bundle "$TMP/huge.json" "${BASE[@]}"
variant amount_str 'b["deal"]["amount"] = "48000"'
expect 2 "\$.deal.amount" err "X2: an amount given as a string exits 2" --bundle "$TMP/amount_str.json" "${BASE[@]}"
variant nomeetings 'del b["meetings"]; b["findings"] = {}; b["stakeholders"] = []'
expect 2 "\$.meetings: is required" err "H8: a bundle without meetings exits 2" --bundle "$TMP/nomeetings.json" "${BASE[@]}"
variant nodomains 'del b["account"]["domains"]'
expect 2 "\$.account.domains: is required" err "X2: an account without domains exits 2" --bundle "$TMP/nodomains.json" "${BASE[@]}"
for mutation in 'b["meetings"][0]["attendees"] = "dana"' 'b["findings"] = []' 'b["stakeholders"][0]["roles"] = {"a": 1}' \
                'b["meetings"][0]["meeting_id"] = 7' 'b["deal"]["term_months"] = 10**30' 'b["sources"]["meeting_notes"]["scopes"] = "public"' \
                'b["meetings"][0]["matched_by"] = [["domain"]]' 'b["findings"]["promises"][0]["flags"] = []' 'b["email"] = 4'; do
  variant badtype "$mutation"
  expect 2 "bundle" err "X2: wrong type ($mutation) exits 2" --bundle "$TMP/badtype.json" "${BASE[@]}"
done
"$PY" -c 'print("[" * 100000 + "]" * 100000)' >"$TMP/deep.json"
expect 2 "nested too deeply" err "X3: deeply nested JSON exits 2" --bundle "$TMP/deep.json" "${BASE[@]}"
printf '\xff\xfe' >"$TMP/binary.json"
expect 2 "is not UTF-8 text" err "X3: a non-UTF-8 bundle exits 2" --bundle "$TMP/binary.json" "${BASE[@]}"

# ---------------------------------------------------------------- other formats
expect 0 "# What sales promised Quillfen Freight Co (fictional)" out "promises-only view renders" \
  --bundle sample_bundle_quillfen.json "${BASE[@]}" --format promises
lacks "## Stakeholder map" "promises view has no stakeholder map"
expect 0 "DRAFT CRM note: not saved." out "CRM note draft is labelled as a draft" --bundle sample_bundle_quillfen.json "${BASE[@]}" --format crm-note
has "- First-year discount: 15% off year one [CONFLICTING, NOT IN CONTRACT]" "CRM note tags conflicting promises"
expect 0 "DRAFT Slack post for (no channel chosen): not posted." out "Slack draft without a channel says so" \
  --bundle sample_bundle_quillfen.json "${BASE[@]}" --format slack
"$PY" "$SCRIPT" --bundle sample_bundle_quillfen.json "${BASE[@]}" --json >"$TMP/out.json"
"$PY" - "$TMP/out.json" <<'PY' || fail "--json output"
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["status"] == "written" and d["handoff_key"] == "hubspot:deal/48213", d
assert len(d["meetings"]) == 5 and d["meetings"][3]["note_ids"] == ["sample-k4a", "sample-k4b"], d["meetings"]
assert d["conflicts"] == [{"topic": "First-year discount", "terms": ["15% off year one", "20% off year one if signed by Sep 30"]}]
assert d["excluded_internal_only"] == 1 and d["duplicate_notes_merged"] == 1
assert d["markdown"].startswith("# Sales-to-CS handoff")
PY
ok "--json emits structured output"

# ---------------------------------------------------------------- QA round 2 regressions
# QA2 helper: in-process runs (fast) with markup checks. Usage: python3 $TMP/r2.py MODE ...
cat >"$TMP/r2.py" <<'PY'
import contextlib, copy, io, json, os, re, sys, tempfile
sys.path.insert(0, os.getcwd())
import build_handoff as h

ASOF = "2026-10-01T09:00:00-07:00"
CFG = os.environ["R2_CONFIG"]
SAMPLE = json.load(open("sample_bundle_quillfen.json", encoding="utf-8"))
TMPD = tempfile.mkdtemp()
PLACEHOLDER = h.DOC_PLACEHOLDER  # script text in the drafts when no --doc-url is given
FORMATS = ["doc", "promises", "crm-note", "slack", "json"]
# Payloads that try to make a link, an autolink, raw HTML, a Slack mention/link or Slack/markdown formatting.
PAYLOADS = [
    r"\[click\](https://phish.example/a)", "[x](https://phish.example/b)", "<https://phish.example/c>",
    "<https://phish.example/d|click>", "<!channel> <!here> <@U123> <#C123> <!subteam^S1>",
    "<img src=x onerror=alert(1)>", "*bold* _it_ ~st~ `code`", r"\\\[y\\\](https://phish.example/e)",
    "[ref]: https://phish.example/f [ref]", r"\<https://phish.example/h\>", "![i](https://phish.example/i.png)",
    r"a | b \| c", '<a href="https://phish.example/j">x</a>', "[x]<https://phish.example/k>", "@channel @here",
    "&lt;https://phish.example/g&gt; &#60;b&#62;", "x\\", "**[z](https://phish.example/z)**",
    # R4: Type math and highlight, bare URLs, www. and bare-domain autolinks
    "cost $5 to $9 and $x^2$ ==hi== a==b==", "see https://phish.example/u or http://phish.example",
    "www.phish.example/w and WWW.Phish.Example", "Visit phish.example/p or evil.example now",
]


def neutral(payload):
    """Same words, no punctuation: what the payload would print if it carried no markup."""
    return re.sub(r"[^A-Za-z0-9\s]", " ", payload)


def unescaped(text, chars, backslash):
    out = []
    i = 0
    while i < len(text):
        c = text[i]
        if backslash and c == "\\":
            i += 2
            continue
        if c in chars:
            out.append(c)
        i += 1
    return out


# Autolink starts a renderer could see: scheme://, www. and a dot between a word and a letter (bare domain).
AUTOLINK_RE = re.compile(r"[A-Za-z]://|(?i:\bwww)\.|[\w-]\.(?=[^\W\d_])")


def skeleton(text, fmt):
    """Markup characters that are live in the output (not escaped), plus autolink starts."""
    text = text.replace(PLACEHOLDER, "")
    if fmt == "slack":
        live = unescaped(text, "<>*_~`@", backslash=False)
    elif fmt == "crm-note":
        live = unescaped(text, "[]<>", backslash=True)
    else:
        live = unescaped(text, "[]<>*_~`|$=", backslash=True)
        live += ["](" for _ in re.finditer(r"(?<!\\)(?:\\\\)*\]\(", text)]
    live += ["autolink"] * len(AUTOLINK_RE.findall(text))
    return sorted(live)


def absolute_problems(text, fmt):
    text = text.replace(PLACEHOLDER, "")
    bad = []
    if "<http" in text or re.search(r"<[A-Za-z/!?@#]", text):
        bad.append("raw < tag, autolink or Slack mention")
    if re.search(r"&(?!amp;|lt;|gt;)#?[A-Za-z0-9]+;", text):
        bad.append("live HTML entity from input")
    if fmt != "slack":
        for m in re.finditer(r"\]\(([^)\s]*)", text):
            pre = text[:m.start()]
            if (len(pre) - len(pre.rstrip("\\"))) % 2 == 0 and "phish" in m.group(1):
                bad.append("working markdown link to " + m.group(1))
        if re.search(r"(?m)^\s*\[[^\]\\]*\]:", text):
            bad.append("link reference definition")
    return bad


def run(bundle, fmt, extra=()):
    path = os.path.join(TMPD, "b.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, ensure_ascii=False)
    argv = ["--bundle", path, "--config", CFG, "--as-of", ASOF, "--internal-domain", "corvalto.example"] + list(extra)
    argv += ["--json"] if fmt == "json" else ["--format", fmt]
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = h.main(argv)
    text = out.getvalue()
    if code == 0 and fmt == "json":
        text = json.loads(text)["markdown"]
    return code, text, out.getvalue(), err.getvalue()


def leaves(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from leaves(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from leaves(v, path + (i,))
    elif isinstance(obj, str):
        yield path


def put(obj, path, value):
    obj = copy.deepcopy(obj)
    t = obj
    for p in path[:-1]:
        t = t[p]
    t[path[-1]] = value
    return obj


CONFIG = h.load_config(h.Path(CFG))
AS_OF = h.parse_moment(ASOF, ValueError)


def render_fast(bundle):
    """Same calls as main(), loading and analysing once for all formats (the --json markdown is the doc)."""
    path = os.path.join(TMPD, "fast.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, ensure_ascii=False)
    try:
        b = h.load_bundle(h.Path(path), ["corvalto.example"], CONFIG)
        a = h.analyse(b, CONFIG, AS_OF, ["corvalto.example"])
    except h.InputError:
        return None
    key = h.handoff_key(b)
    return {fmt: h.render(fmt, b, a, CONFIG, AS_OF, key, None, None) for fmt in ["doc", "promises", "crm-note", "slack"]}


def check_fast(bundle_p, bundle_n, label, failures):
    out_p = render_fast(bundle_p)
    if out_p is None:
        return 0
    out_n = render_fast(bundle_n)
    for fmt, text_p in out_p.items():
        probs = absolute_problems(text_p, fmt)
        if out_n is not None and skeleton(text_p, fmt) != skeleton(out_n[fmt], fmt):
            probs.append("input added markup")
        failures.extend("{} {}: {}".format(label, fmt, p) for p in probs)
    return len(out_p)


def check_pair(bundle_p, bundle_n, label, failures, extra=()):
    ran = 0
    for fmt in FORMATS:
        code_p, text_p, raw_p, err_p = run(bundle_p, fmt, extra)
        if code_p != 0:
            if raw_p:
                failures.append("{} {}: printed to stdout before exit {}".format(label, fmt, code_p))
            continue
        code_n, text_n, _, _ = run(bundle_n, fmt, extra)
        probs = absolute_problems(text_p, fmt)
        if code_n == 0 and skeleton(text_p, fmt) != skeleton(text_n, fmt):
            probs.append("input added markup")
        for p in probs:
            failures.append("{} {}: {}".format(label, fmt, p))
        ran += 1
    return ran


mode = sys.argv[1]
failures = []
if mode == "fuzz":
    # Every string field of the sample, one at a time (and all at once), for each payload, in every format.
    picks = [int(x) for x in sys.argv[2].split(",")] if len(sys.argv) > 2 else range(len(PAYLOADS))
    paths = list(leaves(SAMPLE))
    renders = 0
    for i in picks:
        payload = PAYLOADS[i]
        for path in paths:
            if path[-1] in ("url", "schema_version"):
                continue
            renders += check_fast(put(SAMPLE, path, payload), put(SAMPLE, path, neutral(payload)),
                                  "payload {} at {}".format(i, path), failures)
    print("renders", renders)
elif mode == "fuzz-all-at-once":
    for i, payload in enumerate(PAYLOADS):
        bp, bn = copy.deepcopy(SAMPLE), copy.deepcopy(SAMPLE)
        for path in leaves(SAMPLE):
            if path[-1] in ("url", "schema_version", "meeting_id"):
                continue
            tp, tn = put(bp, path, payload), put(bn, path, neutral(payload))
            # keep the field only if the bundle still loads with it
            try:
                cfg = h.load_config(h.Path(CFG))
                p = os.path.join(TMPD, "probe.json")
                json.dump(tn, open(p, "w", encoding="utf-8"), ensure_ascii=False)
                h.load_bundle(h.Path(p), ["corvalto.example"], cfg)
                json.dump(tp, open(p, "w", encoding="utf-8"), ensure_ascii=False)
                h.load_bundle(h.Path(p), ["corvalto.example"], cfg)
            except h.InputError:
                continue
            bp, bn = tp, tn
        if check_pair(bp, bn, "all fields = payload {}".format(i), failures, ("--post-to", payload)) != len(FORMATS):
            failures.append("all fields = payload {}: a format did not render".format(i))
elif mode == "phones":
    POSITIVE = [
        ("Call (555) 123-4567 today", "Call (phone removed) today"), ("+1 (415) 555-0134", None), ("+1 415 555 0134", None),
        ("+1-415-555-0134", None), ("415-555-0134", None), ("415.555.0134", None), ("Tel. 415 555 0134", "Tel. (phone removed)"),
        ("1-800-555-0199", None), ("+14155550134", None), ("+44 20 7946 0958", None), ("+44 (0)20 7946 0958", None),
        ("+44 7700 900123", None), ("07700 900123", None), ("020 7946 0958", None), ("+33 1 23 45 67 89", None),
        ("01 23 45 67 89", None), ("01.23.45.67.89", None), ("+33 1.23.45.67.89", None), ("+49 30 12345678", None),
        ("+49 (0)30 12345678", None), ("030 12345678", None), ("0049 30 12345678", None), ("+61 2 9876 5432", None),
        ("+81 3-1234-5678", None), ("+91 98765 43210", None), ("+55 11 91234-5678", None), ("+353 1 234 5678", None),
        ("+31 20 123 4567", None), ("+34 912 345 678", None), ("+39 06 1234 5678", None),
        ("Call 4155550134", "Call (phone removed)"), ("mobile: 07700900123", "mobile: (phone removed)"),
        ("(+33 1 23 45 67 89)", "((phone removed))"), ("+1 (415) 555-0134 ext. 12", "(phone removed) ext. 12"),
        ("phone:415-555-0134.", "phone:(phone removed)."), ("Kickoff at 10:00, call 415 555 0134", "Kickoff at 10:00, call (phone removed)"),
    ]
    NEGATIVE = [
        "Kickoff 2026-10-15 10:00 PT", "2026-10-15T10:00:00-07:00", "2026-10-01 – 2026-12-31", "2026-10-01 - 2026-12-31",
        "2026-10-01 to 2026-12-31", "2026-10-15 10:00-11:30", "15.10.2026 10:00", "10/15/2026 10:00", "2026.10.15 1000",
        "Version 10.4.2.1", "v2.14.3", "release 2026.10.1", "Server 192.168.100.254", "10.0.0.1:8080",
        "IPv4 10.20.30.40 and 172.16.254.1", "PO 4500012345", "PO 4500 0123 45", "PO-2026-001234", "order #123-456-7890",
        "Order No. 123 456 7890", "invoice INV-2026-0042", "Invoice 2026 0042 1187", "account 1234 5678 9012",
        "ticket 123-456-7890", "SKU 400-123-4567", "1,250 seats", "1 250 seats", "1 200 000 000 rows", "$120,000/yr",
        "€1.250.000.000", "2024 2025 2026", "ISO 27001:2022", "SOC 2 Type II", "09:00-17:00 PT", "Q4 2026",
        "4111 1111 1111 1111", "build 20261015.1234", "contract 2026 0001 2345", "~5,000 users by 2027",
        "20261001-20261231", "Ref 0207 946 0958", "Call at 10:30 on 2026-10-15",
    ]
    for text, want in POSITIVE:
        got = h.redact(text)
        want = want or "(phone removed)"
        if got != want:
            failures.append("phone not redacted as expected: {!r} -> {!r} (want {!r})".format(text, got, want))
    for text in NEGATIVE:
        got = h.redact(text)
        if got != text:
            failures.append("non-phone altered: {!r} -> {!r}".format(text, got))
    print("cases", len(POSITIVE) + len(NEGATIVE), "positive", len(POSITIVE), "negative", len(NEGATIVE))
for f in failures[:25]:
    print("FAIL:", f)
print("failures", len(failures))
sys.exit(1 if failures else 0)
PY
export R2_CONFIG="$TCFG"

# QA2-1: backslash first, so backslash-escaped link syntax stays text
variant r2_bs 'b["account"]["name"] = r"\[click\](https://phish.example/a)"; meeting("sample-k1")["title"] = r"\[click\](https://phish.example/t)"; b["findings"]["pains"][0]["quote"] = r"\[click\](https://phish.example/q)"; meeting("sample-k2")["title"] = r"Deep dive \| TMS"'
expect 0 '# Sales-to-CS handoff: \\\[click\\\](https\://phish\.example/a)' out "QA2-1: backslash is escaped first in the account name" --bundle "$TMP/r2_bs.json" "${BASE[@]}"
has '[\\\[click\\\](https\://phish\.example/t), Aug 20](https://notes.granola.ai/d/sample-k1)' "QA2-1: a title cannot replace the real Granola link"
has '“\\\[click\\\](https\://phish\.example/q)”' "QA2-1: backslash link syntax in a quote stays text"
has '| [Deep dive \\\| TMS](https://notes.granola.ai/d/sample-k2) |' "QA2-1: a backslash-pipe in a title stays inside its table cell"
"$PY" - "$TMP/out" <<'PY' || fail "QA2-1: a working link to phish.example in the doc"
import re, sys
t = open(sys.argv[1], encoding="utf-8").read()
for m in re.finditer(r"\]\(([^)\s]*)", t):
    pre = t[:m.start()]
    assert (len(pre) - len(pre.rstrip("\\"))) % 2 == 1 or "phish" not in m.group(1), m.group(0)
PY
ok "QA2-1: no unescaped ]( leads to an injected URL"
for fmt in promises crm-note slack; do
  expect 0 "" out "QA2-1: $fmt renders the backslash payload" --bundle "$TMP/r2_bs.json" "${BASE[@]}" --format "$fmt"
  "$PY" - "$TMP/out" "$fmt" <<'PY' || fail "QA2-1: injected link in $fmt"
import re, sys
t, fmt = open(sys.argv[1], encoding="utf-8").read(), sys.argv[2]
for m in re.finditer(r"\]\(([^)\s]*)", t):
    pre = t[:m.start()]
    assert (len(pre) - len(pre.rstrip("\\"))) % 2 == 1 or "phish" not in m.group(1), m.group(0)
assert "<http" not in t
PY
  ok "QA2-1: no working injected link in $fmt"
done
variant r2_fmt 'b["findings"]["promises"][0]["topic"] = "*x* _y_ ~z~ `w`"; b["account"]["name"] = "A*B_C"'
expect 0 '**Conflicting terms on First-year discount**' out "QA2-X: formatting characters in values" --bundle "$TMP/r2_fmt.json" "${BASE[@]}"
has '\*x\* \_y\_ \~z\~ \`w\`' "QA2-X: markdown formatting characters in values are backslash-escaped"
expect 0 '*New customer handoff: A＊B＿C*' out "QA2-X: Slack formatting characters in values cannot break the bold" --bundle "$TMP/r2_fmt.json" "${BASE[@]}" --format slack
variant r2_amp 'meeting("sample-k1")["title"] = "R&D &lt;b&gt; &#60;i&#62;"'
expect 0 'R&amp;D &amp;lt;b&amp;gt; &amp;#60;i&amp;#62;' out "QA2-X: & in values is escaped in markdown, so entities stay text" --bundle "$TMP/r2_amp.json" "${BASE[@]}"

# QA2-X: fuzz. No input value may add markup (link, autolink, raw HTML, Slack mention/link/formatting) in any format.
"$PY" "$TMP/r2.py" fuzz-all-at-once >"$TMP/fuzz1" 2>&1 || { tail -30 "$TMP/fuzz1"; fail "QA2-X: fuzz (every field at once) found markup from input"; }
ok "QA2-X: fuzz, every text field at once x 22 payloads x 5 formats incl. --json: no markup, math, highlight or autolink from input"
"$PY" "$TMP/r2.py" fuzz 0,4,6,18,19,20,21 >"$TMP/fuzz2" 2>&1 || { tail -30 "$TMP/fuzz2"; fail "QA2-X: fuzz (one field at a time) found markup from input"; }
ok "QA2-X: fuzz, each text field alone x 7 payloads x 4 formats ($(head -1 "$TMP/fuzz2")): no markup from input"

# QA2-2 / QA2-3: phone redaction corpus (positive and negative)
"$PY" "$TMP/r2.py" phones >"$TMP/phones" 2>&1 || { cat "$TMP/phones"; fail "QA2-2/3: phone corpus"; }
ok "QA2-2/3: phone corpus ($(head -1 "$TMP/phones")) passes"
variant r2_dates 'b["findings"]["pains"][0]["quote"] = "Kickoff 2026-10-15 10:00 PT, PO 4500012345, 1,250 seats, 192.168.10.254, v2.14.3, 2026-10-01 – 2026-12-31"; b["findings"]["triggers"][0]["quote"] = "Call +33 1 23 45 67 89 or +49 30 12345678 or 07700 900123 or (555) 123-4567."'
expect 0 '“Kickoff 2026-10-15 10:00 PT, PO 4500012345, 1,250 seats, 192\.168\.10\.254, v2\.14\.3, 2026-10-01 – 2026-12-31”' out \
  "QA2-2: dates, times, PO numbers, seats, IPs, versions and date ranges in a quote are untouched" --bundle "$TMP/r2_dates.json" "${BASE[@]}"
has '“Call (phone removed) or (phone removed) or (phone removed) or (phone removed).”' "QA2-3: international and (555) formats are removed without a stray ("

# QA2-4: required list config keys
for key in key_roles bot_email_patterns consumer_domains checklist; do
  "$PY" - "$TMP/cfg_null.json" "$key" <<'PY'
import json, sys
c = json.load(open("handoff.json", encoding="utf-8"))
c[sys.argv[2]] = None
json.dump(c, open(sys.argv[1], "w"))
PY
  expect 2 "\$.$key: is required" err "QA2-4: config $key = null exits 2" --bundle sample_bundle_quillfen.json --config "$TMP/cfg_null.json" --as-of "$ASOF" --internal-domain corvalto.example
done

# QA2-5: log parsing
printf '# Sales-to-CS handoff log\n\n| handoff_key | account | close_date | status | written_at | delivered_at | meetings_read | promises | conflicts |\n|---|---|---|---|---|---|---|---|---|\n  | hubspot:deal/48213 | Quillfen | 2026-09-30 | delivered | 2026-09-30T10:00:00-07:00 | 2026-09-30T11:00:00-07:00 | 5 | 8 | 1 |\n' >"$TMP/indent_log.md"
expect 0 "Skipped: already handed off hubspot:deal/48213 (delivered 2026-09-30T11:00:00-07:00)." out "QA2-5: an indented delivered row is honoured" \
  --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/indent_log.md" --scheduled
printf '  | hubspot:deal/1 | Other | 2026-09-30 | delivered | 2026-09-30T10:00:00-07:00 | 2026-09-30T11:00:00-07:00 | 1 | 1 | 0 |\n' >>"$TMP/indent_log.md"
sed -i 's/hubspot:deal\/48213/hubspot:deal\/2/' "$TMP/indent_log.md"
expect 0 "# Sales-to-CS handoff" out "QA2-5: a new handoff is written next to indented rows" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/indent_log.md" --scheduled
grep -qF "| hubspot:deal/1 | Other |" "$TMP/indent_log.md" && grep -qF "| hubspot:deal/2 | Quillfen |" "$TMP/indent_log.md" || { cat "$TMP/indent_log.md"; fail "QA2-5: rewrite dropped an indented row"; }
ok "QA2-5: rewriting the log keeps indented rows"
printf '# Sales-to-CS handoff log\n\n| handoff_key | account | close_date | status | written_at | delivered_at | meetings_read | promises | conflicts |\n|---|---|---|---|---|---|---|---|---|\nhubspot:deal/48213 | Quillfen | 2026-09-30 | delivered | 2026-09-30T10:00:00-07:00 | 2026-09-30T11:00:00-07:00 | 5 | 8 | 1\n' >"$TMP/nopipe_log.md"
expect 2 "line 5 looks like a table row" err "QA2-5: a row without its leading pipe refuses to run" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/nopipe_log.md" --scheduled
for row in '| hubspot:deal/9 | Q | 2026-09-30 | delivered | <!channel> | 2026-09-30T11:00:00-07:00 | 5 | 8 | 1 |' \
           '| hubspot:deal/9 | Q | 2026-09-30 | delivered | 2026-09-30T10:00:00-07:00 | [x](https://phish.example) | 5 | 8 | 1 |' \
           '| deal [x](y) | Q | 2026-09-30 | delivered | 2026-09-30T10:00:00-07:00 | 2026-09-30T11:00:00-07:00 | 5 | 8 | 1 |' \
           '| hubspot:deal/9 | Q | 30/09/2026 | delivered | 2026-09-30T10:00:00-07:00 | 2026-09-30T11:00:00-07:00 | 5 | 8 | 1 |' \
           '| hubspot:deal/9 | Q | 2026-09-30 | delivered | 2026-09-30T10:00:00-07:00 | 2026-09-30T11:00:00-07:00 | five | 8 | 1 |' \
           '| hubspot:deal/9 | Q | 2026-09-30 | pending | 2026-09-30T10:00:00-07:00 | 2026-09-30T11:00:00-07:00 | 5 | 8 | 1 |'; do
  printf '# Sales-to-CS handoff log\n\n| handoff_key | account | close_date | status | written_at | delivered_at | meetings_read | promises | conflicts |\n|---|---|---|---|---|---|---|---|---|\n%s\n' "$row" >"$TMP/cell_log.md"
  expect 2 "--log" err "QA2-5/6: a log row with an invalid cell refuses to run ($(echo "$row" | cut -c1-50))" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/cell_log.md" --scheduled
done

# QA2-6: record_ref charset; everything printed from the log is checked
for ref in 'deal/<!channel>' 'deal/[x](https://phish.example)' 'deal/*x*' 'deal/`x`' 'deal/a&b' "deal/é" 'deal/a\b'; do
  variant r2_ref "b[\"deal\"][\"record_ref\"] = $("$PY" -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$ref")"
  expect 2 "\$.deal.record_ref" err "QA2-6: record_ref '$ref' exits 2" --bundle "$TMP/r2_ref.json" "${BASE[@]}"
done
variant r2_prov 'b["sources"]["crm"]["provider"] = "Hub <Spot>"; b["deal"]["record_ref"] = "48213"'
expect 0 'Handoff key: `hub-spot:48213`' out "QA2-6: a CRM provider name is slugged in the key" --bundle "$TMP/r2_prov.json" "${BASE[@]}"
variant r2_dom 'b["account"]["domains"] = ["quillfen<b>.example"]'
expect 2 "\$.account.domains" err "QA2-6: an account domain with markup exits 2" --bundle "$TMP/r2_dom.json" "${BASE[@]}"
expect 2 "--confirm-delivered" err "QA2-6: --confirm-delivered with markup in the key exits 2" --confirm-delivered '<!channel>' --as-of "$ASOF" --log "$TMP/log.md"
variant r2_acct 'b["account"]["name"] = "Quill [x](https://phish.example) | <b>"'
expect 0 "# Sales-to-CS handoff" out "QA2-6: scheduled run with markup in the account name" --bundle "$TMP/r2_acct.json" "${BASE[@]}" --scheduled --log "$TMP/acct_log.md"
grep -qF '| hubspot:deal/48213 | Quill \[x\](https\://phish\.example) / &lt;b&gt; | 2026-09-30 | pending |' "$TMP/acct_log.md" || { cat "$TMP/acct_log.md"; fail "QA2-6: account name in the log is not escaped"; }
ok "QA2-6: the account name is escaped in the log"
expect 4 "is pending" err "QA2-6: the escaped log row reads back" --bundle "$TMP/r2_acct.json" "${BASE[@]}" --scheduled --log "$TMP/acct_log.md"
"$PY" - "$TMP/surrogate.json" <<'PY'
import json, sys
b = json.load(open("sample_bundle_quillfen.json", encoding="utf-8"))
text = json.dumps(b, ensure_ascii=True).replace('"Quillfen Freight Co (fictional)"', '"Quillfen \\ud800"', 1)
open(sys.argv[1], "w", encoding="ascii").write(text)
PY
expect 2 "not valid Unicode" err "QA2-X: a lone surrogate in the bundle exits 2 (no crash on output or log write)" --bundle "$TMP/surrogate.json" "${BASE[@]}" --scheduled --log "$TMP/sur_log.md"

# QA2-7: stdout encoding and broken pipe
set +e
PYTHONIOENCODING=ascii LC_ALL=C "$PY" "$SCRIPT" --bundle sample_bundle_quillfen.json "${BASE[@]}" --scheduled --log "$TMP/ascii_log.md" >"$TMP/out" 2>"$TMP/err"
got=$?
set -e
[[ "$got" == 0 ]] && ! grep -q Traceback "$TMP/err" && grep -qF "⚠️" "$TMP/out" || { cat "$TMP/err"; fail "QA2-7: ascii stdout"; }
ok "QA2-7: an ASCII-only stdout still gets UTF-8 output, exit 0"
"$PY" - "$PY" "$SCRIPT" "$TMP/pipe_log.md" "$TCFG" "$ASOF" <<'PY' || fail "QA2-7: broken pipe"
import os, subprocess, sys
py, script, log, cfg, asof = sys.argv[1:]
args = [py, script, "--bundle", "sample_bundle_quillfen.json", "--config", cfg, "--as-of", asof,
        "--internal-domain", "corvalto.example", "--scheduled", "--log", log]
r, w = os.pipe()
os.close(r)  # nobody reads stdout: the first write fails with EPIPE
p = subprocess.run(args, stdout=w, stderr=subprocess.PIPE)
os.close(w)
err = p.stderr.decode()
assert p.returncode == 6, (p.returncode, err)
assert "Traceback" not in err and "Exception ignored" not in err, err
assert "--resume-pending" in err, err
assert "| pending |" in open(log, encoding="utf-8").read()
p = subprocess.run(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
assert p.returncode == 4 and not p.stdout, (p.returncode, p.stderr)
p = subprocess.run(args + ["--resume-pending"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
assert p.returncode == 0 and p.stdout.startswith(b"# Sales-to-CS handoff"), p.returncode
PY
ok "QA2-7: a broken pipe after staging exits 6 without a traceback; the handoff stays pending (exit 4, then --resume-pending)"
if [[ -w /dev/full ]]; then
  set +e
  "$PY" "$SCRIPT" --bundle sample_bundle_quillfen.json "${BASE[@]}" --format slack >/dev/full 2>"$TMP/err"
  got=$?
  set -e
  [[ "$got" == 6 ]] && grep -qF "could not be written" "$TMP/err" && ! grep -q "Traceback\|Exception ignored" "$TMP/err" || { cat "$TMP/err"; fail "QA2-7: /dev/full exit $got"; }
  ok "QA2-7: a full disk on stdout exits 6 without a traceback"
fi

# QA2-8: lock edge cases
cp sample_handoff_log.md "$TMP/flog.md"
touch -d "+1 day" "$TMP/flog.md.lock"
expect 0 "# Sales-to-CS handoff" out "QA2-8: a lock with a future modification time is stale" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/flog.md" --scheduled
[[ ! -e "$TMP/flog.md.lock" ]] || fail "QA2-8: lock left behind"
ok "QA2-8: the taken-over lock is released"
mkdir -p "$TMP/dlog.md.lock"
expect 2 "is not a file" err "QA2-8: a lock path that is a folder exits 2" --bundle sample_bundle_quillfen.json "${BASE[@]}" --log "$TMP/dlog.md" --scheduled
expect 2 "is not a file" err "QA2-8: a lock path that is a folder exits 2 on confirm" --confirm-delivered hubspot:deal/1 --as-of "$ASOF" --log "$TMP/dlog.md"
"$PY" - "$TMP/ilog.md" <<'PY' || fail "QA2-8: inode-checked takeover and own-lock release"
import os, sys, time
sys.path.insert(0, os.getcwd())
import build_handoff as h
log = h.Path(sys.argv[1])
lock = h.LogLock(log)
# a stale lock that another run replaces between our check and our removal must survive
open(str(lock.path), "w").write("other")
old = time.time() - 3600
os.utime(str(lock.path), (old, old))
seen = os.lstat(str(lock.path))
os.unlink(str(lock.path))
open(str(lock.path), "w").write("fresh run")
assert not lock.remove_stale(seen), "removed a lock that was replaced"
assert open(str(lock.path)).read() == "fresh run"
os.unlink(str(lock.path))
# leaving only removes our own lock
with h.LogLock(log) as mine:
    os.unlink(str(mine.path))
    open(str(mine.path), "w").write("someone else")
assert open(str(lock.path)).read() == "someone else", "released a lock this run does not hold"
os.unlink(str(lock.path))
with h.LogLock(log):
    assert os.path.exists(str(lock.path))
assert not os.path.exists(str(lock.path)), "own lock not released"
PY
ok "QA2-8: stale takeover checks the inode, and a run only removes its own lock"

# ---------------------------------------------------------------- pre-QA round 3 (PQ3) regressions
# PQ3-1: a domain bot pattern (read.example here, like read.ai in handoff.json) matches the address's domain or a
# subdomain, not any substring, so a customer at spread.example is a person; a real bot address is still dropped.
"$PY" - "$TCFG" "$TMP/pq_botcfg.json" <<'PY'
import json, sys
c = json.load(open(sys.argv[1], encoding="utf-8"))
c["bot_email_patterns"].append("read.example")
json.dump(c, open(sys.argv[2], "w", encoding="utf-8"))
PY
variant pq_bot 'b["account"]["domains"] = ["spread.example"]
for m in b["meetings"]:
    for a in m.get("attendees", []):
        a["email"] = a["email"].replace("quillfenfreight.example", "spread.example").replace("quillfen-eu.example", "spread.example")
for s in b["stakeholders"]:
    if s.get("email"):
        s["email"] = s["email"].replace("quillfenfreight.example", "spread.example").replace("quillfen-eu.example", "spread.example")
meeting("sample-k2")["attendees"] += [{"name": "Read Bot", "email": "meetings@notes.read.example"}]'
expect 0 "**Meetings read**: 5 meetings from 6 notes" out "PQ3-1: customers at spread.example are not dropped by the read.example bot pattern" --bundle "$TMP/pq_bot.json" --config "$TMP/pq_botcfg.json" --as-of "$ASOF" --internal-domain corvalto.example
has "**Elena Novak**" "PQ3-1: the spread.example champion is still a stakeholder"
has "2 note-taker bots dropped" "PQ3-1: an address on a read.example subdomain is still a bot"
"$PY" -c 'import json, os, sys; sys.path.insert(0, os.getcwd()); import build_handoff as h; pats = json.load(open("handoff.json", encoding="utf-8"))["bot_email_patterns"]; assert "read.ai" in pats and h.DOMAIN_RE.fullmatch("read.ai"); assert not h.is_bot("ann@bread.ai.example", "read.ai") and h.is_bot("meetings@notes.read.example", "read.example") and h.is_bot("meetings@read.example", "read.example") and not h.is_bot("ann@bread.example", "read.example") and h.is_bot("notetaker+1@x.example", "notetaker")' || fail "PQ3-1: shipped read.ai pattern"
ok "PQ3-1: the shipped read.ai pattern is a domain pattern: it matches that domain and its subdomains only"

# PQ3-2: only a surrounding pair of quotation marks is stripped; inner quotes keep the quote verbatim
variant pq_quote 'b["findings"]["pains"][0]["quote"] = "\"Fast\" matters more than \"cheap\""'
expect 0 "“\"Fast\" matters more than \"cheap\"”" out "PQ3-2: a quote that starts and ends with inner quotation marks is kept verbatim" --bundle "$TMP/pq_quote.json" "${BASE[@]}"

# PQ3-3: the handoff key is printed exactly (code span, no escaping or redaction), so the documented confirm works
variant pq_key 'b["deal"]["record_ref"] = "salesforce:opportunity/006_Ab_415.555.0134"'
expect 0 'Handoff key: `salesforce:opportunity/006_Ab_415.555.0134`' out "PQ3-3: a key with _ and phone-like digits prints exactly" --bundle "$TMP/pq_key.json" "${BASE[@]}" --scheduled --log "$TMP/pq_key_log.md"
key="$(sed -n 's/^Handoff key: `\([^`]*\)`.*/\1/p' "$TMP/out")"
expect 0 "Confirmed: salesforce:opportunity/006_Ab_415.555.0134 marked delivered." out "PQ3-3: the key copied from the doc confirms delivery" --confirm-delivered "$key" --as-of "$ASOF" --log "$TMP/pq_key_log.md"
expect 0 "Handoff key: salesforce:opportunity/006_Ab_415.555.0134" out "PQ3-3: the CRM note prints the key exactly" --bundle "$TMP/pq_key.json" "${BASE[@]}" --format crm-note

# PQ3-4: accounts named in another script, with no domain or deal, get distinct keys
for name in "株式会社テスト" "ООО Ромашка" "ООО Василёк"; do
  variant pq_name "b[\"account\"] = {\"name\": \"$name\", \"domains\": []}; b[\"deal\"] = None; b[\"sources\"][\"crm\"] = {\"provider\": \"none\", \"status\": \"not_configured\"}; [m.__setitem__(\"matched_by\", [\"title\"]) for m in b[\"meetings\"]]"
  "$PY" "$SCRIPT" --bundle "$TMP/pq_name.json" "${BASE[@]}" | grep "^Handoff key:" >>"$TMP/pq_keys"
done
[[ "$(sort -u "$TMP/pq_keys" | wc -l)" == 3 ]] && ! grep -q "account:account" "$TMP/pq_keys" || { cat "$TMP/pq_keys"; fail "PQ3-4: non-Latin names share a key"; }
ok "PQ3-4: three accounts named in other scripts get three different keys"

# PQ3-5: a scheduled run needs deal.record_ref, so a later bundle without it cannot re-key and re-post the deal
variant pq_noref 'b["deal"]["record_ref"] = None'
expect 2 "record_ref is required for --scheduled" err "PQ3-5: scheduled run without record_ref exits 2" --bundle "$TMP/pq_noref.json" "${BASE[@]}" --scheduled --log "$TMP/pq_noref_log.md"
[[ ! -e "$TMP/pq_noref_log.md" ]] || fail "PQ3-5: the log was written"
ok "PQ3-5: nothing is recorded"
expect 0 "# Sales-to-CS handoff" out "PQ3-5: an on-request run without record_ref still works" --bundle "$TMP/pq_noref.json" "${BASE[@]}"

# PQ3-6: a malformed --internal-domain exits 2 instead of silently treating colleagues as customers
for bad in "https://corvalto.example" "corvalto.example,other.example" "corvalto" "dana@corvalto.example"; do
  expect 2 "--internal-domain" err "PQ3-6: --internal-domain '$bad' exits 2" --bundle sample_bundle_quillfen.json --config "$TCFG" --as-of "$ASOF" --internal-domain "$bad"
done
expect 0 "# Sales-to-CS handoff" out "PQ3-6: @Corvalto.example is cleaned and accepted" --bundle sample_bundle_quillfen.json --config "$TCFG" --as-of "$ASOF" --internal-domain "@Corvalto.example"

# PQ3-7: strict times and offsets, ASCII digits, identical on every Python (3.11+ alone accepts T24:00)
for start in "2026-09-15T24:00:00-07:00" "2026-09-15T11:00:00+00:60" "2026-09-15T11:00:00+15:00" "2026-09-15T11:00:00-23:59" "２０２６-09-15T11:00:00-07:00" "2026-09-15T11:00:60-07:00"; do
  variant pq_start "meeting(\"sample-k4a\")[\"start\"] = \"$start\""
  expect 2 "\$.meetings[3].start" err "PQ3-7: start '$start' exits 2" --bundle "$TMP/pq_start.json" "${BASE[@]}"
done
expect 2 "--as-of" err "PQ3-7: --as-of T24:00 exits 2" --bundle sample_bundle_quillfen.json --config "$TCFG" --as-of 2026-10-01T24:00:00Z --internal-domain corvalto.example
variant pq_due 'b["findings"]["milestones"][0]["due"] = "2026-10-10T24:00:00Z"'
expect 2 "\$.findings.milestones[0].due" err "PQ3-7: a T24:00 datetime in a date field exits 2" --bundle "$TMP/pq_due.json" "${BASE[@]}"
variant pq_offs 'meeting("sample-k4a")["start"] = "2026-09-15T11:00:00+14:00"; meeting("sample-k4b")["start"] = "2026-09-15T14:00:00-12:00"'
expect 0 "# Sales-to-CS handoff" out "PQ3-7: offsets +14:00 and -12:00 are accepted" --bundle "$TMP/pq_offs.json" "${BASE[@]}"

# PQ3-8: undecodable bytes in --doc-url are a clean exit 2, not a traceback
expect 2 "--doc-url" err "PQ3-8: --doc-url with invalid UTF-8 exits 2" --bundle sample_bundle_quillfen.json "${BASE[@]}" --format slack --doc-url "$(printf 'https://type.example/\xff')"

# PQ3-9: links with a user name or password (incl. the backslash trick) or a bogus host are dropped
for u in 'https://notes.granola.ai\\@evil.example/d/x' "https://user:pw@notes.example/d/x" "https://.example/d" "https://a..b/d" "https://notes/d" "https://notes.example:0/d"; do
  variant pq_url "meeting(\"sample-k2\")[\"url\"] = \"$u\""
  expect 0 "1 link dropped because it was not an http(s) URL" out "PQ3-9: link '$u' is dropped and counted" --bundle "$TMP/pq_url.json" "${BASE[@]}"
  lacks "evil.example" "PQ3-9: no link to '$u'"
done
expect 2 "--doc-url" err "PQ3-9: a --doc-url with credentials exits 2" --bundle sample_bundle_quillfen.json "${BASE[@]}" --format slack --doc-url "https://type.com\\@evil.example/d"

# PQ3-10: redaction recall for international phones and non-ASCII emails; precision for order labels
"$PY" - <<'PY' || fail "PQ3-10: redaction corpus"
import os, sys
sys.path.insert(0, os.getcwd())
import build_handoff as h
phones = ["+33 (0)1 23 45 67 89", "+61 (0)2 9876 5432", "+353 (0)1 234 5678", "+81-90-1234-5678", "+91-22-1234-5678",
          "+43 1 234567890", "+1 415–555–0134", "415–555–0134", "+44 20 7946 0958", "+86 138 0013 8000",
          "+55 11 91234-5678", "+7 495 123-45-67", "090-1234-5678", "+82 10-1234-5678", "+1 (415) 555-0134"]
emails = ["josé@empresa.example", "info@münchen.example", "李@例子.example", "maya＠northwind.example",
          "maya​@northwind.example", "dürr@firma.example"]
keep = ["PO#: 4500 0123 45", "Orders 1234 5678 90", "Invoices #2026 1015 0001", "Kickoff 2026-10-15 10:00 PT",
        "2026-10-15–2026-10-30", "15.09.2026", "10.0.0.1", "$1,200,000", "1 200 000 000", "v2.14.3", "2024–2026",
        "100–200 seats", "9:00–17:00", "between 1 000 and 5 000 seats"]
bad = [p for p in phones if "(phone removed)" not in h.redact("Call " + p + " today")]
bad += [e for e in emails if "(email removed)" not in h.redact("Write to " + e)]
bad += [k for k in keep if h.redact(k) != k]
assert not bad, bad
PY
ok "PQ3-10: international phones and non-ASCII emails are removed; order labels, dates and ranges are kept"

# PQ3-11: Slack draft: markdown link syntax in a value is inert (brackets become look-alikes)
variant pq_slack 'b["findings"]["pains"][0]["text"] = "[click](https://phish.example/a) ![i](https://phish.example/i.png)"'
expect 0 "［click］(https:∕∕phish․example/a)" out "PQ3-11: brackets in a Slack value become look-alikes" --bundle "$TMP/pq_slack.json" "${BASE[@]}" --format slack
lacks "](" "PQ3-11: no markdown link syntax in the Slack draft"

# PQ3-12: conflicts ignore thousands separators, but still catch different amounts
variant pq_conf 'p = b["findings"]["promises"]; base = dict(p[0]); p.append(dict(base, topic="Seat price", terms="$1,500 per seat")); p.append(dict(base, topic="Seat price", terms="$1500 per seat")); p.append(dict(base, topic="Setup fee", terms="$1,500")); p.append(dict(base, topic="Setup fee", terms="$15.00"))'
expect 0 "**Setup fee**" out "PQ3-12: \$1,500 vs \$15.00 is a conflict" --bundle "$TMP/pq_conf.json" "${BASE[@]}" --format promises
grep -qxF -- "- **Setup fee**" "$TMP/out" || { cat "$TMP/out"; fail "PQ3-12: \$1,500 vs \$15.00 not in the conflicts block"; }
! grep -qxF -- "- **Seat price**" "$TMP/out" && ! grep -F "**Seat price**:" "$TMP/out" | grep -q "conflicting" || { cat "$TMP/out"; fail "PQ3-12: \$1,500 vs \$1500 flagged as a conflict"; }
ok "PQ3-12: \$1,500 vs \$1500 is not a conflict"

# PQ3-13: a milestone due on the as-of date says "today"
variant pq_today 'b["findings"]["milestones"][0]["due"] = "2026-10-01"'
expect 0 "Milestone due today: Carrier API connected in staging (Oct 1)." out "PQ3-13: due today" --bundle "$TMP/pq_today.json" "${BASE[@]}"
lacks "due in 0 days" "PQ3-13: never 'due in 0 days'"

# PQ3-14: email items sort at the end of their date in the --as-of offset, so an email sent after an evening call
# is the latest version
variant pq_order 'meeting("sample-k6")["start"] = "2026-09-29T18:00:00-07:00"; p = b["findings"]["promises"]; base = dict(p[0]); p.append(dict(base, topic="Onboarding fee", terms="waived", quote="We will waive onboarding.", quote_from="notes", source={"type": "meeting", "meeting_id": "sample-k6"})); p.append(dict(base, topic="Onboarding fee", terms="half price", quote="Per our call, onboarding is half price.", quote_from="email", speaker=None, source={"type": "email", "date": "2026-09-29", "label": "Email from Dana Ortiz"}))'
expect 0 "Latest stated: half price (Email from Dana Ortiz, Sep 29)" out "PQ3-14: an email after an evening call is the latest" --bundle "$TMP/pq_order.json" "${BASE[@]}" --format promises

# PQ3-15/16: contradictory meeting lists and colleagues listed as stakeholders exit 2
variant pq_both 'b["excluded_meetings"].append({"meeting_id": "sample-k1", "title": "x", "start": "2026-08-20T10:00:00-07:00", "reason": "dup"})'
expect 2 "is also in \$.meetings" err "PQ3-15: a meeting both listed and excluded exits 2" --bundle "$TMP/pq_both.json" "${BASE[@]}"
variant pq_staff 'b["stakeholders"].append({"name": "Dana Ortiz", "email": "dana@corvalto.example", "roles": ["champion"]})'
expect 2 "\$.stakeholders[6].email: is on one of your internal domains" err "PQ3-16: a colleague listed as a stakeholder exits 2" --bundle "$TMP/pq_staff.json" "${BASE[@]}"

# PQ3-17: control, zero-width and direction-override characters never reach the output
variant pq_inv 'meeting("sample-k1")["title"] = "Intro‮⁦ call\u0007\u001b[31m"; b["stakeholders"][0]["note"] = "Reach her at elena​@quillfenfreight.example"'
expect 0 "[Intro call\\[31m, Aug 20]" out "PQ3-17: invisible characters are removed from a title" --bundle "$TMP/pq_inv.json" "${BASE[@]}"
has "Reach her at (email removed)" "PQ3-17: a zero-width space cannot hide an email address"
"$PY" -c 'import re, sys; t = open(sys.argv[1], encoding="utf-8").read(); sys.exit(1 if re.search("[\x00-\x08\x1b\u200b\u202a-\u202e\u2066-\u2069]", t) else 0)' "$TMP/out" || fail "PQ3-17: invisible character printed"
ok "PQ3-17: no control or direction-override character is printed"

# PQ3-18: drafts without --doc-url contain no raw <…> placeholder
for fmt in crm-note slack; do
  expect 0 "Full handoff: (link to the handoff doc" out "PQ3-18: $fmt placeholder is plain text" --bundle sample_bundle_quillfen.json "${BASE[@]}" --format "$fmt"
  lacks "<" "PQ3-18: no raw < in the $fmt draft"
done

# ---------------------------------------------------------------- QA round 4 (R4) regressions
# R4-B1: $…$ (Type math) and ==…== (highlight) in a value are escaped in markdown; amounts the script prints too
variant r4_math 'b["account"]["name"] = "Cost $5 to $9 and ==hi=="; b["findings"]["promises"][0]["terms"] = "$5 to $9, $x$ and ==y=="; b["findings"]["pains"][0]["quote"] = "It costs $5 to $9 and hi"'
expect 0 '# Sales-to-CS handoff: Cost \$5 to \$9 and \=\=hi\=\=' out "R4-B1: \$ and = in the account name are escaped" --bundle "$TMP/r4_math.json" "${BASE[@]}"
has '“It costs \$5 to \$9 and hi”' "R4-B1: \$ in a quote is escaped"
has '| Amount | \$48,000/yr |' "R4-B1: the deal amount's \$ is escaped too, so two amounts on a line cannot open math"
for fmt in doc promises json; do
  if [[ "$fmt" == json ]]; then expect 0 "" out "R4-B1: --json renders" --bundle "$TMP/r4_math.json" "${BASE[@]}" --json
  else expect 0 "" out "R4-B1: $fmt renders" --bundle "$TMP/r4_math.json" "${BASE[@]}" --format "$fmt"; fi
  "$PY" - "$TMP/out" "$fmt" <<'PY' || fail "R4-B1: live \$ or = in $fmt"
import json, re, sys
t = open(sys.argv[1], encoding="utf-8").read()
if sys.argv[2] == "json":
    t = json.loads(t)["markdown"]
live = re.findall(r"(?<!\\)(?:\\\\)*([$=])", t)
assert not live, live
PY
  ok "R4-B1: no unescaped \$ or = in the $fmt markdown"
done

# R4-H1: bare URLs, www. and bare domains in any value never become links (doc, promises, CRM note, Slack)
variant r4_auto 'meeting("sample-k1")["title"] = "Intro https://phish.example/a www.phish.example evil.example/x"; b["findings"]["promises"][0]["terms"] = "see http://phish.example/t or WWW.Evil.Example"; b["findings"]["pains"][0]["text"] = "Go to phish.example now"; b["account"]["name"] = "Quillfen phish.example"; b["findings"]["objections"][0]["text"] = "see http://phish.example/t or WWW.Evil.Example"'
expect 0 'Intro https\://phish\.example/a www\.phish\.example evil\.example/x' out "R4-H1: markdown breaks scheme:// and dots inside words" --bundle "$TMP/r4_auto.json" "${BASE[@]}"
for fmt in doc promises crm-note slack; do
  expect 0 "" out "R4-H1: $fmt renders" --bundle "$TMP/r4_auto.json" "${BASE[@]}" --format "$fmt"
  "$PY" - "$TMP/out" <<'PY' || fail "R4-H1: autolink from a value in $fmt"
import re, sys
t = open(sys.argv[1], encoding="utf-8").read()
t = re.sub(r"\]\(https?://[^)\s]+\)", "]", t)  # link targets the script builds from checked url fields
bad = re.findall(r"\w+://\S*|(?i:\bwww)\.\S*|[\w-]+\.(?=[^\W\d_])\S*", t)
assert not bad, bad
assert "phish" in t
PY
  ok "R4-H1: no URL, www. or bare-domain autolink from a value in $fmt"
done
expect 0 "Go to phish․example now" out "R4-H1: Slack uses a look-alike dot in bare domains" --bundle "$TMP/r4_auto.json" "${BASE[@]}" --format slack
has "Open objections: see http:∕∕phish․example/t or WWW․Evil․Example" "R4-H1: Slack breaks :// and www. with look-alikes"
expect 0 "Why they bought: Go to phish․example now" out "R4-H1: CRM note uses the same look-alikes" --bundle "$TMP/r4_auto.json" "${BASE[@]}" --format crm-note
expect 0 "Full handoff: https://type.com/sample/x" out "R4-H1: the doc link the script builds still works in Slack" --bundle "$TMP/r4_auto.json" "${BASE[@]}" --format slack --doc-url "https://type.com/sample/x"

# R4-H2: number lists (prices, seat tiers, ascending or descending, 0800 0900 1000) are not phones without + or a cue
"$PY" - <<'PY' || fail "R4-H2: number lists"
import os, sys
sys.path.insert(0, os.getcwd())
import build_handoff as h
keep = ["9.99 19.99 29.99", "29.99 19.99 9.99", "Plans 9.99 19.99 29.99 49.99 per seat", "500 1000 2000 5000",
        "5000 2000 1000 500", "Tiers 100 250 500 1000 2500 seats", "0800 0900 1000", "1000 0900 0800",
        "1200 1500 1800 2400", "We budgeted 40000-50000 a year", "12000 10000 8000 6000", "4.99 9.99 14.99 19.99 24.99"]
phones = [("06 12 34 56 78", None), ("07700 900 123", None), ("020 3456 7890", None), ("Mobile 987 654 3210", "Mobile (phone removed)"),
          ("Call 0800 0900 1000", "Call (phone removed)"), ("+1 500 1000 2000", None), ("01 23 45 67 89", None)]
bad = ["changed: {!r} -> {!r}".format(t, h.redact(t)) for t in keep if h.redact(t) != t]
bad += ["missed: {!r} -> {!r}".format(t, h.redact(t)) for t, want in phones if h.redact(t) != (want or "(phone removed)")]
print("\n".join(bad))
sys.exit(1 if bad else 0)
PY
ok "R4-H2: price lists, seat tiers and 0800 0900 1000 stay; phones with a leading 0, + or a cue are still removed"
variant r4_prices 'p = b["findings"]["promises"]; base = dict(p[0]); p.append(dict(base, topic="Monthly price", terms="monthly price 9.99 19.99 29.99")); p.append(dict(base, topic="Monthly price", terms="monthly price 9.99 24.99 29.99")); b["findings"]["pains"][0]["quote"] = "Seat tiers 500 1000 2000 5000 at 9.99 19.99 29.99"'
expect 0 "- **Monthly price**" out "R4-H2: two price lists conflict" --bundle "$TMP/r4_prices.json" "${BASE[@]}" --format promises
has "  - monthly price 9\.99 19\.99 29\.99: " "R4-H2: the first price list is shown"
has "  - monthly price 9\.99 24\.99 29\.99: " "R4-H2: the conflicting price list is shown"
lacks "(phone removed)" "R4-H2: no price is removed as a phone"
expect 0 "Conflicting terms on Monthly price: monthly price 9.99 19.99 29.99 vs monthly price 9.99 24.99 29.99" out \
  "R4-H2: Slack shows both prices" --bundle "$TMP/r4_prices.json" "${BASE[@]}" --format slack
expect 0 "“Seat tiers 500 1000 2000 5000 at 9\.99 19\.99 29\.99”" out "R4-H2: tiers and prices in a quote stay" --bundle "$TMP/r4_prices.json" "${BASE[@]}"

# H6: phone removal acts only on clear signals: a leading +, a cue word right before ("call", "mobile", "text me on"),
# or a standard phone format with one separator. Number lists (repeated values, prices, tiers, years, ranges, in any
# order) are never phones without a + or a cue word.
"$PY" - <<'PY' >"$TMP/h6" 2>&1 || { cat "$TMP/h6"; fail "H6: signal-only phone corpus"; }
import os, sys
sys.path.insert(0, os.getcwd())
import build_handoff as h
keep = ["36000 36000 38000", "36000 38000 36000", "38000 36000 36000", "Annual price 36000 36000 38000",
        "36000.00 36000.00 38000.00", "36,000 36,000 38,000", "40000-50000-45000", "100 100 100 100",
        "300 300 3000", "33 33 33 33 33", "500 250 1000 750", "1200 950 1800 2400", "5000 4000 6000 3000",
        "999 1999 2999 999", "250 199 2499", "Tiers 5000 10000 5000 25000", "seats 100 250 100 500 1000",
        "2025 2026 2025 2027", "2026 2024 2025", "1999 2999 1999", "10000 25000 10000", "9.99 19.99 9.99",
        "19.99 9.99 29.99", "12.50 125.00 1250.00", "0800 0900 1000 0900", "0800 0800 0900", "0010 0020 0030",
        "12 34 56 78 90", "On the call we heard 36000 36000 38000",
        "36000 36000 38000 per year, then call", "Phone budget 36000 36000 38000"]
phones = ["415 555 0134", "987 654 3210", "(555) 123-4567", "(212) 555 0100", "555-123-4567", "555.123.4567", "1-800-555-0199",
          "07700 900123", "07700-900123", "07700 900 123", "020 7946 0958", "0161 496 0000", "+44 7700 900123",
          "+44 (0)161 496 0000", "06 12 34 56 78", "06.12.34.56.78", "06-12-34-56-78", "01 45 45 67 89",
          "+33 6 12 34 56 78", "030 12345678", "089 1234567", "+49 30 12345678", "+49 (0)89 1234567",
          "0049 30 12345678", "+1 415 555 0134", "+81 3-1234-5678", "+61 2 9876 5432", "+353 1 234 5678",
          "+14155550134"]
cued = [("Call 415 555 0134", "Call (phone removed)"), ("Text me on 987 654 3210", "Text me on (phone removed)"),
        ("WhatsApp: 4155550134", "WhatsApp: (phone removed)"), ("fax 020 7946 0958", "fax (phone removed)"),
        ("cell 987 654 3210 after 5", "cell (phone removed) after 5")]
bad = ["changed: {!r} -> {!r}".format(t, h.redact(t)) for t in keep if h.redact(t) != t]
bad += ["missed: {!r} -> {!r}".format(p, h.redact(p)) for p in phones if h.redact(p) != "(phone removed)"]
bad += ["missed: {!r} -> {!r}".format(t, h.redact(t)) for t, want in cued if h.redact(t) != want]
print("cases", len(keep) + len(phones) + len(cued), "kept", len(keep), "removed", len(phones) + len(cued))
print("\n".join(bad))
sys.exit(1 if bad else 0)
PY
ok "H6: signal-only phone corpus ($(head -1 "$TMP/h6")): lists stay, UK/US/FR/DE/international phones go"
[[ "$(head -1 "$TMP/h6" | awk '{print $2}')" -ge 40 ]] || fail "H6: corpus has fewer than 40 cases"
ok "H6: corpus has 40+ cases"
variant h6_prices 'p = b["findings"]["promises"]; base = dict(p[0]); p.append(dict(base, topic="Annual price", terms="36000 36000 38000")); p.append(dict(base, topic="Annual price", terms="36000 38000 38000")); b["findings"]["pains"][0]["quote"] = "Year one 36000, then 36000 36000 38000"'
expect 0 "- **Annual price**" out "H6: two non-monotonic price lists conflict" --bundle "$TMP/h6_prices.json" "${BASE[@]}" --format promises
has "  - 36000 36000 38000: " "H6: QA's 36000 36000 38000 prints in full"
has "  - 36000 38000 38000: " "H6: the conflicting price list prints in full"
lacks "(phone removed)" "H6: no price is removed as a phone (promises)"
expect 0 "Conflicting terms on Annual price: 36000 36000 38000 vs 36000 38000 38000" out \
  "H6: Slack shows both prices" --bundle "$TMP/h6_prices.json" "${BASE[@]}" --format slack
expect 0 "“Year one 36000, then 36000 36000 38000”" out "H6: a repeated-value price list in a quote stays" --bundle "$TMP/h6_prices.json" "${BASE[@]}"
grep -F "Annual price" "$TMP/out" | grep -qF "36000 36000 38000" || { cat "$TMP/out"; fail "H6: doc conflict line lacks the first price"; }
grep -F "Annual price" "$TMP/out" | grep -qF "36000 38000 38000" || { cat "$TMP/out"; fail "H6: doc conflict line lacks the second price"; }
ok "H6: the doc's conflict warning shows both prices"
lacks "(phone removed)" "H6: no price is removed as a phone (doc)"

# R4-H3: the email pattern stays fast on very long tokens (local part capped at 64 characters)
"$PY" - <<'PY' || fail "R4-H3: long tokens"
import os, sys, time
sys.path.insert(0, os.getcwd())
import build_handoff as h
for text in ["a" * 32000, "a." * 16000, "a" * 32000 + "@", "a-" * 16000 + "@x", "1" * 32000, "+" + "1 " * 16000]:
    t = time.perf_counter()
    h.redact(text)
    took = time.perf_counter() - t
    assert took < 1.0, (text[:20], len(text), took)
assert h.redact("ops@quillfen.example") == "(email removed)"
assert "@" not in h.redact("x" * 70 + "@quillfen.example")
PY
ok "R4-H3: redacting 32,000-character tokens takes under a second; addresses are still removed"

# R4-H4: the tl;dv bot pattern is its domain, so a person whose address contains "tldv" is kept
"$PY" - <<'PY' || fail "R4-H4: tldv pattern"
import json, os, sys
sys.path.insert(0, os.getcwd())
import build_handoff as h
pats = json.load(open("handoff.json", encoding="utf-8"))["bot_email_patterns"]
assert "tldv.io" in pats and "tldv" not in pats, pats
for person in ["mattldvorak@quillfen.example", "matt.ldvorak@tldvx.example", "a.tldv@quillfen.example"]:
    assert not any(h.is_bot(person, p) for p in pats), person
assert h.is_bot("bot@meet.tldv.example", "tldv.example") and h.is_bot("x@tldv.example", "tldv.example")
PY
ok "R4-H4: mattldvorak@… is a person; the shipped pattern is the tldv.io domain"
variant r4_tldv 'meeting("sample-k2")["attendees"].append({"name": "Matt Ldvorak", "email": "mattldvorak@quillfenfreight.example"})'
expect 0 "**Matt Ldvorak**" out "R4-H4: a customer named Ldvorak is a stakeholder, not a bot" --bundle "$TMP/r4_tldv.json" "${BASE[@]}"

# R4-H5: every email address and bot domain in the tests is on .example
! grep -noE "[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}" tests.sh | grep -vE "\.example$" || fail "R4-H5: a test address is not on .example"
! grep -nE "@[a-z.-]*(read|otter|fireflies)""\.ai([^.a-z-]|$)" tests.sh || fail "R4-H5: a real recorder domain in the tests"
ok "R4-H5: tests use .example addresses only"

# R4-B2: the docs never claim the script checks quotes against meeting text (the bundle has none)
"$PY" - <<'PY' || fail "R4-B2: docs"
import json, re
docs = {f: open(f, encoding="utf-8").read() for f in ["SKILL.md", "README.md", "DATA_CONTRACT.md", "_listing.json"]}
for f, t in docs.items():
    for m in re.finditer(r"(?i)(script|it) (checks|verifies|validates|confirms)[^.]{0,60}(quote|verbatim)", t):
        raise SystemExit("{}: {}".format(f, m.group(0)))
for f in docs:  # X3/X4: one wording everywhere
    assert "The agent copies the quotes; the script does not check them against the meeting text" in docs[f], f
PY
ok "R4-B2/X3: SKILL.md, README, DATA_CONTRACT and the listing all say the agent copies the quotes and the script does not check them"

# R4-docs: the listing's sampleOutput is section 1 of run.sh plus the "Produced by" footer
"$PY" - "$TMP/run_out" <<'PY' || fail "R4-docs: sampleOutput differs from run.sh section 1"
import json, sys
run = open(sys.argv[1], encoding="utf-8").read()
section = run.split("=== 1.", 1)[1].split("\n", 1)[1].split("\n=== 2.", 1)[0].rstrip("\n")
footer = "\n\n_Produced by running `bash run.sh` on the fictional sample data bundled with this skill._"
assert json.load(open("_listing.json", encoding="utf-8"))["sampleOutput"] == section + footer
PY
ok "R4-docs: _listing.json sampleOutput == run.sh section 1 + footer"

# QA2-9: no real free-mail domains in the tests
! grep -nE "@(g""mail|y""ahoo|h""otmail|o""utlook)\.com" tests.sh || fail "QA2-9: tests use a real free-mail address"
ok "QA2-9: tests use .example free-mail addresses only"

# R6-H7: spaced US numbers, connecting "is", and nearby non-phone values.
"$PY" - <<'PY' || fail "R6-H7 phone regression"
import build_handoff as h
for text in ["Her cell is 415 555 0199", "415 555 0199", "Her cell is 4155550199"]:
    assert "(phone removed)" in h.redact(text), text
for text in ["36000 36000 38000", "2026 2027 2028", "Order 415 555 0199"]:
    assert h.redact(text) == text, text
PY
ok "R6-H7: spaced US phones removed; price lists and references preserved"

"$PY" - <<'PY' || fail "H8 owner/country phone parity"
import sys
import build_handoff as h
fail=[];n=0
for owner in ("Dana's", "Dana’s", "My", "His", "Her", "Their", ""):
 for adjective in ("personal", "direct", "best", "work", "home", ""):
  for sep in (" is ", "'s ", "’s ", ": ", " - "):
   for num in ('4155550199','07700900123'):
    text=' '.join(x for x in (owner,adjective,'Number') if x)+sep+num
    n+=1
    if not h.redact(text).endswith('(phone removed)'):fail.append(text)
for text in ('1 415 555 0199','1 800 555 0199','  Number: 07700900123', 'Notes\n  Number: 07700900123'):
 n+=1
 if not h.redact(text).endswith('(phone removed)'):fail.append(text)
for text in ('Ticket number is 4155550199','Order number: 4155550199','PO number is 4155550199','Invoice number: 4155550199','Case number is 4155550199','Account number: 4155550199','Reference number is 4155550199','My number is 7.','Number: 3','36000 36000 38000','300 300 3000','250 199 2499','2024 2025 2026','Prices 9.99 19.99 29.99'):
 n+=1
 if h.redact(text)!=text:fail.append(text+' -> '+h.redact(text))
print('PASS %d/%d'%(n-len(fail),n));print('\n'.join(fail));sys.exit(bool(fail))
PY
ok "H8: 438 owner, separator, country-code and numeric-control assertions"

echo "PASS $PASS/$PASS"

#!/usr/bin/env bash
# Guardrail and edge-case tests. Run from the skill directory: bash tests.sh
# Builds variants of the fictional sample bundles in a temp dir; never touches real data.
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
SCRIPT=build_commitments.py
W1=(--config commitments.json --as-of 2026-10-09T16:00:00-07:00 --internal-domain brackenvole.example)
W2=(--config commitments.json --as-of 2026-10-16T16:00:00-07:00 --internal-domain brackenvole.example)
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

# expect CODE PATTERN STREAM NAME -- args...   (STREAM is out or err)
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

has() { grep -qF -- "$1" "$TMP/out" || { cat "$TMP/out"; fail "$2: '$1' missing"; }; ok "$2"; }
hasnt() { ! grep -qF -- "$1" "$TMP/out" || { cat "$TMP/out"; fail "$2: '$1' present"; }; ok "$2"; }

# ---------------------------------------------------------------- sample and determinism
bash run.sh >"$TMP/run.txt"
cmp -s "$TMP/run.txt" expected_output.txt || { diff "$TMP/run.txt" expected_output.txt | head -20; fail "run.sh output differs from expected_output.txt"; }
ok "run.sh matches expected_output.txt byte for byte"
bash run.sh | cmp -s - "$TMP/run.txt" || fail "second run.sh differs (not deterministic)"
ok "run.sh is deterministic across runs"

# ---------------------------------------------------------------- input validation (exit 2, names file + path)
printf '{"schema_version": 1,,}' >"$TMP/broken.json"
expect 2 "is not valid JSON (line 1" err "malformed JSON rejected" --bundle "$TMP/broken.json" "${W1[@]}"
grep -qF "broken.json" "$TMP/err" || fail "error does not name the file"
ok "error names the bundle file"

printf '\xff\xfe{}' >"$TMP/binary.json"
expect 2 "is not UTF-8 text" err "non-UTF-8 bundle rejected" --bundle "$TMP/binary.json" "${W1[@]}"

expect 2 "file not found" err "missing bundle file rejected" --bundle "$TMP/nope.json" "${W1[@]}"

variant v_schema sample_bundle_week1.json 'b["schema_version"] = 2'
expect 2 '$.schema_version: must be 1' err "wrong schema_version rejected" --bundle "$TMP/v_schema.json" "${W1[@]}"

variant no_quote sample_bundle_week1.json 'del b["commitments"][0]["quote"]'
expect 2 '$.commitments[0].quote: is required' err "commitment without a verbatim quote rejected" --bundle "$TMP/no_quote.json" "${W1[@]}"

variant no_owner sample_bundle_week1.json 'del b["commitments"][0]["owner"]'
expect 2 '$.commitments[0].owner: is required' err "commitment without an owner rejected" --bundle "$TMP/no_owner.json" "${W1[@]}"

variant no_slack sample_bundle_week1.json 'del b["sources"]["slack"]'
expect 2 '$.sources.slack: required object is missing' err "missing source status rejected" --bundle "$TMP/no_slack.json" "${W1[@]}"

expect 2 "--as-of" err "--as-of without offset rejected" --bundle sample_bundle_week1.json --config commitments.json \
  --as-of 2026-10-09T16:00:00 --internal-domain brackenvole.example
expect 2 "--internal-domain" err "missing internal domain rejected" --bundle sample_bundle_week1.json --config commitments.json \
  --as-of 2026-10-09T16:00:00-07:00
expect 2 "--scheduled: needs --state" err "scheduled run without a state file rejected" --bundle sample_bundle_week1.json "${W1[@]}" --scheduled
expect 2 "--bundle and --as-of are required" err "missing --bundle rejected" --as-of 2026-10-09T16:00:00-07:00

variant denied_quote sample_bundle_week1.json 'b["commitments"][4]["quote_source"] = "transcript"'
expect 2 "was not read (status denied)" err "transcript quote from a denied transcript rejected" --bundle "$TMP/denied_quote.json" "${W1[@]}"

variant notes_gone sample_bundle_week1.json 'b["meetings"][2]["notes_status"] = "unavailable"'
expect 2 "notes of 'sample-3c4d' were not read" err "notes quote from unread notes rejected" --bundle "$TMP/notes_gone.json" "${W1[@]}"

variant ev_unavail sample_bundle_week1.json 'b["sources"]["email"] = {"provider": "gmail", "status": "unavailable", "detail": "401"}'
expect 2 "sent_email evidence comes from sources.email, whose status is unavailable" err "evidence from an unread source rejected" --bundle "$TMP/ev_unavail.json" "${W1[@]}"

variant ev_early sample_bundle_week1.json 'b["evidence"][0]["date"] = "2026-10-01"'
expect 2 "is before the commitment was made (2026-10-05)" err "evidence older than the commitment rejected" --bundle "$TMP/ev_early.json" "${W1[@]}"

variant ev_future sample_bundle_week1.json 'b["evidence"][0]["date"] = "2026-10-12"'
expect 2 '$.evidence[0].date: 2026-10-12 is after --as-of' err "evidence dated in the future rejected" --bundle "$TMP/ev_future.json" "${W1[@]}"

variant weak_nowhy sample_bundle_week1.json 'del b["evidence"][1]["why"]'
expect 2 '$.evidence[1].why: is required' err "weak evidence without a why rejected" --bundle "$TMP/weak_nowhy.json" "${W1[@]}"

variant ev_both sample_bundle_week1.json 'b["evidence"][0]["commitment_id"] = "ct-0000000000"'
expect 2 "give exactly one of ref" err "evidence with both ref and commitment_id rejected" --bundle "$TMP/ev_both.json" "${W1[@]}"

variant ev_nourl sample_bundle_week1.json 'del b["evidence"][0]["url"]'
expect 2 '$.evidence[0].url: is required' err "evidence without a link rejected" --bundle "$TMP/ev_nourl.json" "${W1[@]}"

variant bad_known sample_bundle_week1.json 'b["commitments"][0]["known_id"] = "ct-deadbeef00"'
expect 2 "'ct-deadbeef00' is not in the state file" err "invented known_id rejected" --bundle "$TMP/bad_known.json" "${W1[@]}"

variant wrong_dir sample_bundle_week1.json 'b["commitments"][3]["direction"] = "we_owe"'
expect 2 "is we_owe but owner_email priya@fennaroot.example is not on an internal domain" err "we_owe by an outside owner rejected" --bundle "$TMP/wrong_dir.json" "${W1[@]}"

variant early_due sample_bundle_week1.json 'b["commitments"][5]["due"]["date"] = "2026-10-01"'
expect 2 "is before the meeting it was promised in" err "explicit due date before the meeting rejected" --bundle "$TMP/early_due.json" "${W1[@]}"

variant rel_date sample_bundle_week1.json 'b["commitments"][0]["due"]["date"] = "2026-10-07"'
expect 2 "only allowed when kind is explicit" err "agent-resolved relative date rejected (script resolves it)" --bundle "$TMP/rel_date.json" "${W1[@]}"

variant out_window sample_bundle_week1.json 'b["meetings"][0]["start"] = "2026-09-28T10:00:00-07:00"'
expect 2 "is outside the scan window" err "meeting outside the scan window rejected" --bundle "$TMP/out_window.json" "${W1[@]}"

variant naive_start sample_bundle_week1.json 'b["meetings"][0]["start"] = "2026-10-05T10:00:00"'
expect 2 "has no UTC offset" err "meeting start without offset rejected" --bundle "$TMP/naive_start.json" "${W1[@]}"

variant dup_ref sample_bundle_week1.json 'b["commitments"][1]["ref"] = "c1"'
expect 2 "duplicate ref 'c1'" err "duplicate commitment ref rejected" --bundle "$TMP/dup_ref.json" "${W1[@]}"

variant bad_same sample_bundle_week1.json 'b["commitments"][0]["same_as"] = "c3"'
expect 2 "links commitments with different directions" err "same_as across directions rejected" --bundle "$TMP/bad_same.json" "${W1[@]}"

variant bad_same_owner sample_bundle_week1.json 'b["commitments"][8]["same_as"] = "c2"'
expect 2 "links commitments with different owners" err "same_as across owners rejected" --bundle "$TMP/bad_same_owner.json" "${W1[@]}"

variant aud_noreason sample_bundle_week1.json 'b["meetings"][5]["audience"] = "external"'
expect 2 '$.meetings[5].audience_reason: is required' err "audience override needs a reason" --bundle "$TMP/aud_noreason.json" "${W1[@]}"

variant ev_internal sample_bundle_week1.json 'b["evidence"].append({"ref": "c10", "kind": "sent_email", "strength": "strong", "date": "2026-10-09", "url": "https://mail.example/x", "summary": "x"})'
expect 2 "was excluded from this run (scope external)" err "evidence for an out-of-scope commitment rejected" --bundle "$TMP/ev_internal.json" "${W1[@]}"

echo '{"due_soon_days": 7}' >"$TMP/bad_config.json"
expect 2 "missing key(s)" err "incomplete config rejected" --bundle sample_bundle_week1.json --config "$TMP/bad_config.json" \
  --as-of 2026-10-09T16:00:00-07:00 --internal-domain brackenvole.example

echo 'not json' >"$TMP/bad_state.json"
expect 2 "refusing to run blind" err "corrupt state file refuses to run" --bundle sample_bundle_week1.json "${W1[@]}" --state "$TMP/bad_state.json"
echo '{"state_version": 9, "commitments": {}, "runs": []}' >"$TMP/v9_state.json"
expect 2 "not a commitment-tracker state file" err "unknown state version refuses to run" --bundle sample_bundle_week1.json "${W1[@]}" --state "$TMP/v9_state.json"

# ---------------------------------------------------------------- empty input
variant empty sample_bundle_week1.json 'b["meetings"] = []; b["commitments"] = []; b["evidence"] = []'
expect 0 "Meetings scanned: 0" out "empty bundle on request prints an honest empty digest" --bundle "$TMP/empty.json" "${W1[@]}"
has "**0 overdue · 0 due in the next 7 days · 0 open with no date · 0 they owe us · 0 done since last run**" "empty digest has zero counts"
expect 0 "Skipped: nothing to report" out "empty bundle on a schedule posts nothing" --bundle "$TMP/empty.json" "${W1[@]}" --state "$TMP/empty_state.json" --scheduled
[[ "$(wc -l <"$TMP/out")" -eq 1 ]] || fail "scheduled skip must be a single line"
ok "scheduled skip is one line"

# ---------------------------------------------------------------- dedupe, ids, due dates in the sample
expect 0 "Commitments — Mon Oct 5 to Fri Oct 9, 2026" out "week 1 renders" --bundle sample_bundle_week1.json "${W1[@]}"
[[ "$(grep -c 'Send pricing for the two new locations' "$TMP/out")" -eq 1 ]] || fail "duplicate notes of one meeting produced two items"
ok "two notes of the same meeting give one item"
[[ "$(grep -c '\*\*Send the revised SOW\*\*' "$TMP/out")" -eq 1 ]] || fail "repeated commitment listed twice"
ok "commitment repeated in a later meeting is listed once"
has "re-promised Thu Oct 8, was due Wed Oct 7" "re-promise moves the due date and says it slipped"
has "Repeated in [Fennaroot Dental — security review]" "repeat cites its meeting and quote"
has "due Thu Oct 8 (inferred from “tomorrow”)" "relative date marked inferred"
has "due Wed Oct 7 · **2 days overdue**" "explicit date used as given"
has "(Granola AI summary, not verbatim speech)" "summary-sourced quotes are labelled"
has "Transcript denied: [Spokewren Cycles onboarding]" "denied transcript is a coverage gap, not silence"
has "Likely done: [Sent “Renewal next steps” to Tomás Rivera]" "weak evidence gives likely done with unicode intact"
has "1 soft mention not listed" "soft mentions counted but hidden by default"
hasnt "joint case study" "soft mention text hidden by default"
has "1 commitment from internal-only meetings skipped" "internal-only commitments skipped under scope external"
hasnt "Q4 forecast sheet" "internal commitment not listed"
has "Gaps: Tracker: not configured" "unconfigured source on the Gaps line"
has "free plan, 30-day note history; this window is inside it." "plan history limit reported"

expect 0 "### Soft mentions (1, not tracked)" out "--soft-mentions lists them" --bundle sample_bundle_week1.json "${W1[@]}" --soft-mentions
has "We should think about a joint case study at some point." "soft mention quoted when enabled"

expect 0 "Update the Q4 forecast sheet" out "--scope all includes internal meetings" --bundle sample_bundle_week1.json "${W1[@]}" --scope all
has "Sam Kim → internal" "internal commitment labelled internal"

expect 0 "our side filtered to: Dana Ortiz" out "--owner filter" --bundle sample_bundle_week1.json "${W1[@]}" --owner "Dana Ortiz"
hasnt "Send the renewal quote" "--owner hides teammates' items"
has "2 of ours hidden by the owner filter" "--owner reports what it hid"
has "Return the signed security questionnaire" "--owner keeps they-owe-us items"

# IDs are stable and ignore curly vs straight apostrophes
python3 - <<'PY' || fail "id normalisation"
import importlib.util
spec = importlib.util.spec_from_file_location("bc", "build_commitments.py"); bc = importlib.util.module_from_spec(spec); spec.loader.exec_module(bc)
a = bc.commitment_id("m1", "I'll send the deck by Friday.", "Dana Ortiz", "Send the deck")
b = bc.commitment_id("m1", "I’ll send the deck by Friday", "dana  ortiz", "send over the deck")
c = bc.commitment_id("m2", "I'll send the deck by Friday.", "Dana Ortiz", "Send the deck")
d = bc.commitment_id("m1", "I'll send the deck by Friday.", "Dana Ortiz", "Send the pricing sheet")
assert a == b and a != c and a != d and a.startswith("ct-") and len(a) == 13, (a, b, c, d)
assert bc.norm_deliverable("Send over the deck") == bc.norm_deliverable("send the deck")
PY
ok "ids stable across punctuation, case and apostrophe style; differ by meeting and by deliverable (C4)"

# ---------------------------------------------------------------- relative due dates (business days, timezones)
python3 - <<'PY' || fail "due-date resolution"
import importlib.util, json
from datetime import date
spec = importlib.util.spec_from_file_location("bc", "build_commitments.py"); bc = importlib.util.module_from_spec(spec); spec.loader.exec_module(bc)
cfg = json.load(open("commitments.json")); cfg["_business"] = [0, 1, 2, 3, 4]; cfg["_holidays"] = {date(2026, 11, 26)}
cal = bc.Calendar(cfg)
def r(phrase, day):
    out = bc.resolve_due({"kind": "relative", "phrase": phrase}, day, cal)
    return (out["date"].isoformat() if out["date"] else None, out["kind"])
mon, fri, wed = date(2026, 10, 5), date(2026, 10, 9), date(2026, 11, 25)
cases = [
    (("today", mon), ("2026-10-05", "inferred")),
    (("EOD", mon), ("2026-10-05", "inferred")),
    (("tomorrow", fri), ("2026-10-12", "inferred")),            # Friday -> next business day
    (("tomorrow", wed), ("2026-11-27", "inferred")),            # skips the configured holiday
    (("by Friday", mon), ("2026-10-09", "inferred")),
    (("by Friday EOD", mon), ("2026-10-09", "inferred")),
    (("this Thursday", mon), ("2026-10-08", "inferred")),
    (("by Friday", fri), (None, "ambiguous")),                  # same weekday: never guess
    (("by Monday", fri), ("2026-10-12", "inferred")),
    (("next Friday", mon), ("2026-10-16", "inferred")),
    (("by end of week", mon), ("2026-10-09", "inferred")),
    (("end of week", date(2026, 10, 10)), ("2026-10-16", "inferred")),  # said on Saturday
    (("next week", mon), ("2026-10-16", "inferred")),
    (("early next week", fri), ("2026-10-13", "inferred")),
    (("in 3 business days", fri), ("2026-10-14", "inferred")),
    (("within two days", fri), ("2026-10-11", "inferred")),
    (("in a week", mon), ("2026-10-12", "inferred")),
    (("end of month", date(2026, 10, 20)), ("2026-10-30", "inferred")),  # Oct 31 2026 is a Saturday
    (("next month", mon), ("2026-11-30", "inferred")),
    (("end of quarter", mon), ("2026-12-31", "inferred")),
    (("by Oct 9", mon), ("2026-10-09", "explicit")),
    (("January 5th", date(2026, 12, 14)), ("2027-01-05", "explicit")),  # year rolls over
    (("2026-10-20", mon), ("2026-10-20", "explicit")),
    (("soon", mon), (None, "none")),
    (("ASAP", mon), (None, "none")),
    (("10/9", mon), (None, "none")),                              # locale-ambiguous: no date
    (("in a couple of days", mon), (None, "none")),
]
bad = [(args, want, r(*args)) for args, want in cases if r(*args) != want]
assert not bad, bad
PY
ok "relative due dates: weekdays, business days, holidays, month/quarter ends, vague and ambiguous phrases"

# Meeting local date (not UTC) anchors relative dates: 6pm Thursday in -07:00 is Friday in UTC.
variant tz sample_bundle_week1.json 'b["meetings"][3]["start"] = "2026-10-08T18:00:00-07:00"; b["commitments"][7]["due"] = {"kind": "relative", "phrase": "tomorrow"}'
expect 0 "due Fri Oct 9 (inferred from “tomorrow”)" out "relative dates use the meeting's local date and offset" --bundle "$TMP/tz.json" "${W1[@]}"

# ---------------------------------------------------------------- state: idempotency, new markers, done drop-off, dismissals
S="$TMP/state.json"
expect 0 "Commitments" out "dry run renders" --bundle sample_bundle_week1.json "${W1[@]}" --state "$S" --dry-run --scheduled
[[ ! -e "$S" ]] || fail "--dry-run wrote the state file"
ok "--dry-run never writes state"

expect 0 "**new**" out "first scheduled run marks items new" --bundle sample_bundle_week1.json "${W1[@]}" --state "$S" --scheduled
grep -qF "pending: post the digest above, then run --confirm-posted weekly:2026-10-05..2026-10-09" "$TMP/err" || fail "no confirm hint"
ok "scheduled digest is staged as pending with a confirm hint on stderr"
cp "$TMP/out" "$TMP/digest_w1.txt"
expect 0 "Confirmed posted: weekly:2026-10-05..2026-10-09" out "--confirm-posted commits the period" \
  --confirm-posted weekly:2026-10-05..2026-10-09 --state "$S"
cp "$S" "$TMP/state_after1.json"
expect 0 "Skipped: already posted weekly:2026-10-05..2026-10-09" out "same period on a schedule is skipped once posted" \
  --bundle sample_bundle_week1.json "${W1[@]}" --state "$S" --scheduled
cmp -s "$S" "$TMP/state_after1.json" || fail "skipped run changed the state file"
ok "skipped run leaves the state file byte-identical"

expect 0 "Commitments — Mon Oct 5 to Fri Oct 9, 2026" out "a person asking again gets the digest" \
  --bundle sample_bundle_week1.json "${W1[@]}" --state "$S"
hasnt "**new**" "items already reported are not new again"
hasnt "Send pricing for the two new locations" "done items drop off after one report"
has "Done since last run (0)" "nothing newly done on the rerun"
python3 -c "import json,sys; d=json.load(open('$S')); sys.exit(0 if len(d['commitments'])==8 else 1)" || fail "overlapping rerun duplicated items"
ok "overlapping rerun keeps 8 tracked items (no duplicates)"

python3 build_commitments.py --list-open --state "$S" >"$TMP/open.json"
python3 -c "import json; d=json.load(open('$TMP/open.json')); ids={x['commitment_id'] for x in d}; assert 'ct-81931a148b' in ids and 'ct-898a3abf81' not in ids and len(d)==6, d" \
  || fail "--list-open"
ok "--list-open lists the 6 items still needing a completion check"
expect 2 "--list-open: needs --state" err "--list-open without state rejected" --list-open

expect 0 "1 dismissed by you (hidden)" out "week 2 applies the dismissal" --bundle sample_bundle_week2.json "${W2[@]}" --state "$S" --scheduled
python3 build_commitments.py --confirm-posted weekly:2026-10-09..2026-10-16 --state "$S" >/dev/null
hasnt "Introduce Marcus" "dismissed item hidden"
has "Slack: unavailable (search\\.messages returned token_expired)" "unavailable source on the Gaps line"
has "3 items carried over from earlier runs" "carried-over items counted"

variant week3 sample_bundle_week2.json 'b["scan"] = {"from": "2026-10-19", "to": "2026-10-23", "cadence": "weekly"}; b["meetings"] = []; b["commitments"] = []; b["evidence"] = []; b["user_actions"] = []'
W3=(--config commitments.json --as-of 2026-10-23T16:00:00-07:00 --internal-domain brackenvole.example)
expect 0 "1 dismissed by you (hidden)" out "dismissal persists without being resent" --bundle "$TMP/week3.json" "${W3[@]}" --state "$S" --dry-run
hasnt "Introduce Marcus" "dismissed item stays hidden"
hasnt "Send the revised SOW" "done item reported last week does not return"

variant restore "$TMP/week3.json" 'b["user_actions"] = [{"commitment_id": "ct-81931a148b", "action": "restore", "date": "2026-10-20"}]'
expect 0 "Introduce Marcus to our data engineer" out "restore brings a dismissed item back" --bundle "$TMP/restore.json" "${W3[@]}" --state "$S" --dry-run

variant markdone "$TMP/week3.json" 'b["user_actions"] = [{"commitment_id": "ct-d3b4a7efd6", "action": "mark_done", "date": "2026-10-19", "note": "you said the checklist went out by hand"}]'
expect 0 "you said the checklist went out by hand (Mon Oct 19)" out "user mark_done moves an item to done" --bundle "$TMP/markdone.json" "${W3[@]}" --state "$S" --dry-run

variant against "$TMP/week3.json" 'b["evidence"] = [{"commitment_id": "ct-628dc18c50", "kind": "later_meeting", "strength": "against", "date": "2026-10-20", "url": "https://notes.granola.ai/d/sample-zz", "summary": "Marcus said the full export is still coming"}]'
expect 0 "Still outstanding per [Marcus said the full export is still coming]" out "against evidence overrides an older weak signal" --bundle "$TMP/against.json" "${W3[@]}" --state "$S" --dry-run
has "polite nudge" "overdue they-owe item offered as a nudge draft"

variant reopen "$TMP/week3.json" 'b["evidence"] = [{"commitment_id": "ct-6e2d041bc1", "kind": "received_email", "strength": "strong", "date": "2026-10-19", "url": "https://mail.example/q", "summary": "Questionnaire received"}, {"commitment_id": "ct-6e2d041bc1", "kind": "later_meeting", "strength": "against", "date": "2026-10-21", "url": "https://notes.granola.ai/d/sample-yy", "summary": "Priya said the signed copy was the wrong version"}]'
expect 0 "Still outstanding per [Priya said the signed copy was the wrong version]" out "later against evidence reopens a done item" --bundle "$TMP/reopen.json" "${W3[@]}" --state "$S" --dry-run

variant setdue "$TMP/week3.json" 'b["user_actions"] = [{"commitment_id": "ct-6e2d041bc1", "action": "set_due", "date": "2026-10-19", "due_date": "2026-10-30", "note": "Priya asked for more time"}]'
expect 0 "due Fri Oct 30 (set by you Mon Oct 19)" out "user can correct a due date" --bundle "$TMP/setdue.json" "${W3[@]}" --state "$S" --dry-run
variant setdue_bad "$TMP/week3.json" 'b["user_actions"] = [{"commitment_id": "ct-6e2d041bc1", "action": "set_due", "date": "2026-10-19"}]'
expect 2 '$.user_actions[0].due_date: is required' err "set_due without a date rejected" --bundle "$TMP/setdue_bad.json" "${W3[@]}" --state "$S" --dry-run

variant bad_action "$TMP/week3.json" 'b["user_actions"] = [{"commitment_id": "ct-0000000000", "action": "dismiss", "date": "2026-10-20"}]'
expect 2 "'ct-0000000000' is not a tracked commitment" err "dismissing an unknown id rejected" --bundle "$TMP/bad_action.json" "${W3[@]}" --state "$S"

# known_id links a re-worded re-extraction to the tracked item instead of creating a new one
variant relink sample_bundle_week1.json 'b["scan"] = {"from": "2026-10-05", "to": "2026-10-09", "cadence": "on_request"}; b["commitments"] = [dict(b["commitments"][4], quote="Dana will send Marcus the storefront checklist next week", deliverable="Share the storefront setup checklist", known_id="ct-d3b4a7efd6")]; b["evidence"] = []'
python3 build_commitments.py --bundle "$TMP/relink.json" "${W1[@]}" --state "$S" --json >"$TMP/relink_out.json"
python3 -c "import json; d=json.load(open('$S')); assert len(d['commitments'])==9, len(d['commitments']); assert len(d['commitments']['ct-d3b4a7efd6']['mentions'])==1" \
  || fail "known_id relink"
ok "known_id merges a re-worded extraction into the tracked item"
expect 2 "on_request bundles cannot be run with --scheduled" err "on_request bundle refused on a schedule" --bundle "$TMP/relink.json" "${W1[@]}" --state "$S" --scheduled

# same_as merges paraphrases inside one bundle
variant sameas sample_bundle_week1.json 'b["commitments"][10]["deliverable"] = "Get the SOW over"; b["commitments"][10]["same_as"] = "c1"'
expect 0 "Repeated in [Fennaroot Dental — security review]" out "same_as merges paraphrased repeats" --bundle "$TMP/sameas.json" "${W1[@]}"
[[ "$(grep -c 'Get the SOW over' "$TMP/out")" -eq 0 ]] || fail "same_as left a second item"
ok "same_as leaves one item"

# ---------------------------------------------------------------- coverage edge cases
variant oldscan sample_bundle_week1.json 'b["scan"]["from"] = "2026-08-31"'
expect 0 "free plan, 30-day note history** — meetings before Wed Sep 9 may be missing" out "window beyond the plan's history is flagged" --bundle "$TMP/oldscan.json" "${W1[@]}"

variant override sample_bundle_week1.json 'b["meetings"][5]["audience"] = "external"; b["meetings"][5]["audience_reason"] = "notes show a customer joined"'
expect 0 "1 audience set by hand because attendee metadata was incomplete" out "audience override is disclosed" --bundle "$TMP/override.json" "${W1[@]}"
has "Update the Q4 forecast sheet" "overridden meeting's commitments included"

variant granola_down sample_bundle_week1.json 'b["sources"]["meetings"]["status"] = "unavailable"; b["sources"]["meetings"]["detail"] = "MCP timeout"; b["meetings"] = []; b["commitments"] = []; b["evidence"] = []'
expect 0 "Source: Granola — **unavailable**: MCP timeout" out "meeting source failure is stated plainly" --bundle "$TMP/granola_down.json" "${W1[@]}"

# ---------------------------------------------------------------- JSON mode
python3 build_commitments.py --bundle sample_bundle_week1.json "${W1[@]}" --json >"$TMP/out.json"
python3 - "$TMP/out.json" <<'PY' || fail "--json payload"
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["status"] == "reported" and d["period_key"] == "weekly:2026-10-05..2026-10-09"
assert d["counts"] == {"overdue": 1, "due_soon": 2, "later": 0, "no_date": 1, "they": 2, "done": 2}, d["counts"]
assert [n["id"] for n in d["nudge_candidates"]] == ["ct-628dc18c50"], d["nudge_candidates"]
assert d["markdown"].startswith("## Commitments")
PY
ok "--json emits counts, groups, nudge candidates and markdown"

# ---------------------------------------------------------------- meetings recorded with different UTC offsets
cat >"$TMP/tz.json" <<'JSON'
{"schema_version": 1,
 "scan": {"from": "2026-10-05", "to": "2026-10-09", "cadence": "on_request"},
 "sources": {"meetings": {"provider": "granola", "status": "ok", "detail": null},
   "email": {"provider": null, "status": "not_configured", "detail": null},
   "slack": {"provider": null, "status": "not_configured", "detail": null},
   "tracker": {"provider": null, "status": "not_configured", "detail": null},
   "calendar": {"provider": null, "status": "not_configured", "detail": null}},
 "meetings": [
  {"meeting_id": "mA", "title": "Kickoff (West)", "start": "2026-10-06T10:00:00-07:00", "url": "https://notes.granola.ai/d/sample-a",
   "attendees": [{"email": "dana@brackenvole.example"}, {"email": "lee@cust.example"}], "notes_status": "ok", "transcript_status": "not_requested"},
  {"meeting_id": "mB", "title": "Kickoff (East)", "start": "2026-10-06T12:00:00-04:00", "url": "https://notes.granola.ai/d/sample-b",
   "attendees": [{"email": "dana@brackenvole.example"}, {"email": "lee@cust.example"}], "notes_status": "ok", "transcript_status": "not_requested"}
 ],
 "commitments": [
  {"ref": "c1", "meeting_id": "mB", "quote": "Dana will send the SOW by October 20.", "quote_source": "notes", "direction": "we_owe", "owner": "Dana", "owner_email": "dana@brackenvole.example", "counterpart": "Cust", "deliverable": "Send the SOW", "due": {"kind": "explicit", "date": "2026-10-20", "phrase": "by October 20"}},
  {"ref": "c2", "meeting_id": "mA", "quote": "Dana: actually I'll send the SOW by Friday.", "quote_source": "notes", "direction": "we_owe", "owner": "Dana", "owner_email": "dana@brackenvole.example", "counterpart": "Cust", "deliverable": "Send SOW", "due": {"kind": "relative", "phrase": "by Friday"}}
 ]}
JSON
expect 0 "due Fri Oct 9 (inferred from “by Friday”) · re-promised Tue Oct 6, was due Tue Oct 20" out \
  "the latest mention is chosen by instant, not by the local clock text" \
  --bundle "$TMP/tz.json" --config commitments.json --as-of 2026-10-07T09:00:00-07:00 --internal-domain brackenvole.example

# ================================================================ QA round 1 regressions (C1-C10, X1-X6)

# ---------------------------------------------------------------- C1 / X4: meeting source not ok on a schedule
S1="$TMP/c1_state.json"
variant c1_down sample_bundle_week1.json 'b["sources"]["meetings"]["status"] = "unavailable"; b["sources"]["meetings"]["detail"] = "MCP timeout"; b["meetings"] = []; b["commitments"] = []; b["evidence"] = []'
expect 5 "Incomplete: meeting notes could not be read (Granola unavailable: MCP timeout)" out \
  "C1: Granola unavailable on a schedule prints an incomplete digest and exits 5" --bundle "$TMP/c1_down.json" "${W1[@]}" --state "$S1" --scheduled
hasnt "nothing to report" "C1: never 'nothing to report' when the meeting source was not read"
has "This period was not recorded" "C1: the digest says the period was not recorded"
python3 -c "import json,sys; d=json.load(open('$S1')); sys.exit(0 if [r['status'] for r in d['runs']] == ['incomplete'] else 1)" || fail "C1: period recorded although Granola was down"
ok "C1: a period is never recorded as posted or pending when the meeting source was not ok (only the attempt)"
expect 0 "Commitments — Mon Oct 5 to Fri Oct 9, 2026" out "C1: once Granola is back the same period gets a full digest (no 'already ran')" \
  --bundle sample_bundle_week1.json "${W1[@]}" --state "$S1" --scheduled
has "**new**" "C1: the full digest after recovery still marks items new"
variant c1_unread sample_bundle_week1.json 'b["commitments"] = []; b["evidence"] = []; b["meetings"] = [dict(b["meetings"][0], notes_status="unavailable", transcript_status="unavailable")]'
expect 5 "Notes unavailable: [Fennaroot Dental — Q4 rollout]" out "C1: unread meetings are never summarised as 'nothing to report'" \
  --bundle "$TMP/c1_unread.json" "${W1[@]}" --state "$TMP/c1_unread_state.json" --scheduled
hasnt "Skipped" "C1: an empty but incomplete scan is still printed"

# ---------------------------------------------------------------- C2 / X4: two-phase post, pending, resume, failure handling
S2="$TMP/c2_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${W1[@]}" --state "$S2" --scheduled >"$TMP/c2_first.txt" 2>/dev/null
cp "$S2" "$TMP/c2_pending.json"
expect 4 "pending: a digest for weekly:2026-10-05..2026-10-09 was produced" err \
  "C2: re-running a pending period exits 4 instead of printing the digest again" --bundle sample_bundle_week1.json "${W1[@]}" --state "$S2" --scheduled
[[ ! -s "$TMP/out" ]] || fail "C2: pending rerun printed to stdout"
ok "C2: pending rerun prints nothing that could be posted"
cmp -s "$S2" "$TMP/c2_pending.json" || fail "C2: pending rerun changed the state"
ok "C2: pending rerun leaves the state unchanged"
expect 0 "Commitments — Mon Oct 5 to Fri Oct 9, 2026" out "C2: --resume-pending prints the pending digest again (post failed)" \
  --bundle sample_bundle_week1.json "${W1[@]}" --state "$S2" --scheduled --resume-pending
cmp -s "$TMP/out" "$TMP/c2_first.txt" || fail "C2: resumed digest differs from the first one"
ok "C2: the resumed digest is identical (items still new, done items still shown)"
expect 2 "no pending digest for 'weekly:2026-10-12..2026-10-16'" err "C2: confirming an unknown period is rejected" \
  --confirm-posted weekly:2026-10-12..2026-10-16 --state "$S2"
expect 0 "Confirmed posted: weekly:2026-10-05..2026-10-09 (8 new, 2 reported done)" out "C2: --confirm-posted commits new and done marks" \
  --confirm-posted weekly:2026-10-05..2026-10-09 --state "$S2" --as-of 2026-10-09T16:03:00-07:00
expect 0 "Already confirmed: weekly:2026-10-05..2026-10-09" out "C2: confirming twice is harmless" \
  --confirm-posted weekly:2026-10-05..2026-10-09 --state "$S2"
python3 - "$S2" <<'PY' || fail "C2: confirm did not commit"
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
run = d["runs"][-1]
assert run["status"] == "posted" and run["confirmed_at"] == "2026-10-09T16:03:00-07:00", run
assert all(r["first_reported"] for r in d["commitments"].values())
assert sorted(c for c, r in d["commitments"].items() if r["done_reported"]) == ["ct-6014d29ff0", "ct-898a3abf81"]
PY
ok "C2: the confirmed run is recorded with its marks"
# superseded: week 1 never confirmed, week 2 runs anyway
S2b="$TMP/c2b_state.json"
cp "$TMP/c2_pending.json" "$S2b"
expect 0 'the digest for `weekly:2026-10-05..2026-10-09` was never confirmed posted' out \
  "C2: a later period supersedes an unconfirmed one and says so" --bundle sample_bundle_week2.json "${W2[@]}" --state "$S2b" --scheduled
has "Send the revised SOW" "C2: items of the unconfirmed digest are not lost"
expect 2 "was superseded by a later digest" err "C2: a superseded period cannot be confirmed" \
  --confirm-posted weekly:2026-10-05..2026-10-09 --state "$S2b"
# X3: the state is written before anything is printed
mkdir -p "$TMP/ro" && chmod 555 "$TMP/ro"
if [[ -w "$TMP/ro" ]]; then ok "X3: (skipped: running as a user who can write read-only folders)"; else
expect 2 "is not writable, so the run could not be recorded" err "X3: unwritable state folder fails before printing" \
  --bundle sample_bundle_week1.json "${W1[@]}" --state "$TMP/ro/state.json" --scheduled
[[ ! -s "$TMP/out" ]] || fail "X3: digest printed although the state could not be written"
ok "X3: nothing printed before the fatal state error"
fi
chmod 755 "$TMP/ro"
expect 2 "does not exist" err "X3: missing state folder fails before printing" \
  --bundle sample_bundle_week1.json "${W1[@]}" --state "$TMP/nope/state.json" --scheduled
mkdir -p "$TMP/statedir.json"
expect 2 "is not a regular file" err "X3: a folder passed as the state file is rejected" \
  --bundle sample_bundle_week1.json "${W1[@]}" --state "$TMP/statedir.json"

# ---------------------------------------------------------------- X4: exclusive lock, stale lock, concurrent runs
S4="$TMP/lock_state.json"
touch "$S4.lock"
expect 3 "another run is updating this state file" err "X4: a fresh lock file stops the run (exit 3)" \
  --bundle sample_bundle_week1.json "${W1[@]}" --state "$S4" --scheduled
[[ ! -s "$TMP/out" && ! -e "$S4" ]] || fail "X4: locked run printed or wrote"
ok "X4: a locked run prints and writes nothing"
touch -d "20 minutes ago" "$S4.lock"
expect 0 "Commitments" out "X4: a lock older than 15 minutes is treated as stale" \
  --bundle sample_bundle_week1.json "${W1[@]}" --state "$S4" --scheduled
[[ ! -e "$S4.lock" ]] || fail "X4: lock not released"
ok "X4: the lock is released after the run"
for i in 1 2 3; do
  S5="$TMP/race_$i.json"
  for j in 1 2 3 4; do
    (python3 build_commitments.py --bundle sample_bundle_week1.json "${W1[@]}" --state "$S5" --scheduled >"$TMP/race_${i}_$j.out" 2>"$TMP/race_${i}_$j.err" \
      && echo 0 >"$TMP/race_${i}_$j.code" || echo $? >"$TMP/race_${i}_$j.code") &
  done
  wait
  posted=$(cat "$TMP"/race_"$i"_*.out | grep -c "^## Commitments" || true)
  [[ "$posted" -eq 1 ]] || { cat "$TMP"/race_"$i"_*.err; fail "X4: $posted concurrent runs printed a digest"; }
  ! grep -q Traceback "$TMP"/race_"$i"_*.err || fail "X4: traceback in a concurrent run"
  for j in 1 2 3 4; do [[ "$(cat "$TMP/race_${i}_$j.code")" =~ ^(0|3|4)$ ]] || fail "X4: unexpected exit code"; done
done
ok "X4: four concurrent scheduled runs (x3): exactly one digest, the rest exit 3 or 4"

# ---------------------------------------------------------------- C3: merge only into open items
S3="$TMP/c3_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${W1[@]}" --state "$S3" --scheduled >/dev/null 2>&1
python3 build_commitments.py --confirm-posted weekly:2026-10-05..2026-10-09 --state "$S3" >/dev/null
python3 build_commitments.py --bundle sample_bundle_week2.json "${W2[@]}" --state "$S3" --scheduled >/dev/null 2>&1
python3 build_commitments.py --confirm-posted weekly:2026-10-09..2026-10-16 --state "$S3" >/dev/null
W4=(--config commitments.json --as-of 2026-10-23T16:00:00-07:00 --internal-domain brackenvole.example)
variant c3 sample_bundle_week2.json '
b["scan"] = {"from": "2026-10-16", "to": "2026-10-23", "cadence": "weekly"}
b["meetings"] = [dict(b["meetings"][0], meeting_id="sample-c3c3", title="Fennaroot Dental check-in", start="2026-10-20T10:00:00-07:00", url="https://notes.granola.ai/d/sample-c3c3", account="Fennaroot Dental")]
b["commitments"] = [
  {"ref": "n1", "meeting_id": "sample-c3c3", "direction": "we_owe", "owner": "Sam Kim", "owner_email": "sam@brackenvole.example", "counterpart": "Fennaroot Dental", "deliverable": "Send pricing for the two new locations", "quote": "I will send updated pricing for the two new locations by Friday.", "quote_source": "transcript", "due": {"kind": "relative", "phrase": "by Friday"}},
  {"ref": "n2", "meeting_id": "sample-c3c3", "direction": "we_owe", "owner": "Dana Ortiz", "owner_email": "dana@brackenvole.example", "counterpart": "Spokewren Cycles", "deliverable": "Introduce Marcus to our data engineer", "quote": "I still owe Marcus that intro to our data engineer.", "quote_source": "transcript", "due": {"kind": "none"}}]
b["evidence"] = []; b["user_actions"] = []'
expect 0 'promised again; the earlier `ct-898a3abf81` was done' out "C3: a new promise matching a done item becomes a new item that notes the earlier one" \
  --bundle "$TMP/c3.json" "${W4[@]}" --state "$S3" --dry-run
has "**Send pricing for the two new locations** · Sam Kim → Fennaroot Dental · due Fri Oct 23" "C3: the new promise is listed with its own due date"
has 'promised again; the earlier `ct-81931a148b` was dismissed' "C3: a new promise matching a dismissed item is not hidden by the dismissal"
expect 0 "Done since last run (0)" out "C3: re-running an old week after confirm does not revive done items" \
  --bundle sample_bundle_week1.json "${W1[@]}" --state "$S3" --dry-run
hasnt "promised again" "C3: duplicate notes of an already-done promise are not new promises"
python3 build_commitments.py --bundle "$TMP/c3.json" "${W4[@]}" --state "$S3" --json >"$TMP/c3.json.out"
python3 - "$TMP/c3.json.out" "$S3" <<'PY2' || fail "C3: done or dismissed item absorbed the new promise"
import json, sys
out = json.load(open(sys.argv[1], encoding="utf-8")); st = json.load(open(sys.argv[2], encoding="utf-8"))["commitments"]
new = [i for g in out["groups"].values() for i in g if i["new"]]
assert sorted(i["deliverable"] for i in new) == ["Introduce Marcus to our data engineer", "Send pricing for the two new locations"], new
assert all(i["id"] not in ("ct-898a3abf81", "ct-81931a148b") for i in new)
assert st["ct-898a3abf81"]["last_status"] == "done" and len(st["ct-898a3abf81"]["mentions"]) == 2
assert st["ct-81931a148b"]["dismissed"] and len(st["ct-81931a148b"]["mentions"]) == 1
PY2
ok "C3: the done and dismissed items keep their status and mentions; the new promises are open items"
variant c3_known sample_bundle_week2.json 'b["commitments"][0]["known_id"] = "ct-898a3abf81"; b["evidence"] = []; b["user_actions"] = []'
expect 2 "'ct-898a3abf81' is done or dismissed; a new promise becomes a new item" err "C3: known_id pointing at a done item from a new meeting is rejected" \
  --bundle "$TMP/c3_known.json" "${W2[@]}" --state "$S3" --dry-run

# ---------------------------------------------------------------- C4: deliverable in the id; old state files
variant c4 sample_bundle_week1.json 'b["commitments"].append(dict(b["commitments"][0], ref="c1x", deliverable="Send the legal redlines"))'
expect 0 "**Send the legal redlines**" out "C4: two deliverables from one sentence are two items" --bundle "$TMP/c4.json" "${W1[@]}"
has "**Send the revised SOW**" "C4: the first deliverable is kept too"
python3 - "$TMP" <<'PY' || fail "C4: v1 state migration"
# Build a state_version 1 file the way the old script wrote it (ids without the deliverable, old runs format),
# including the old bug: one id for two deliverables of one sentence, so only one was tracked.
import hashlib, importlib.util, json, subprocess, sys
tmp = sys.argv[1]
spec = importlib.util.spec_from_file_location("bc", "build_commitments.py"); bc = importlib.util.module_from_spec(spec); spec.loader.exec_module(bc)
w1 = ["--config", "commitments.json", "--as-of", "2026-10-09T16:00:00-07:00", "--internal-domain", "brackenvole.example"]
subprocess.run([sys.executable, "build_commitments.py", "--bundle", "sample_bundle_week1.json"] + w1 + ["--state", tmp + "/v1.json"], check=True, stdout=subprocess.DEVNULL)
d = json.load(open(tmp + "/v1.json", encoding="utf-8"))
def old_id(m, q, o):
    return "ct-" + hashlib.sha256(("%s|%s|%s" % (m, bc.norm_text(q), bc.norm_owner(o))).encode("utf-8")).hexdigest()[:10]
items = {}
for cid, r in d["commitments"].items():
    first = r["mentions"][0]
    nid = old_id(first["meeting_id"], first["quote"], r["owner"])
    r["id"] = nid
    r.pop("earlier", None)
    r["first_reported"] = "2026-10-09T16:00:00-07:00"
    if r["last_status"] == "done":
        r["done_reported"] = "2026-10-09T16:00:00-07:00"
    items[nid] = r
d = {"state_version": 1, "commitments": items, "updated_at": "2026-10-09T16:00:00-07:00",
     "runs": [{"period_key": "weekly:2026-10-05..2026-10-09", "as_of": "2026-10-09T16:00:00-07:00", "mode": "scheduled", "result": "reported", "counts": {}},
              {"period_key": "on_request:2026-10-05..2026-10-09", "as_of": "2026-10-09T17:00:00-07:00", "mode": "on_request", "result": "reported", "counts": {}}]}
json.dump(d, open(tmp + "/v1.json", "w", encoding="utf-8"), ensure_ascii=False)
json.dump(sorted(items), open(tmp + "/v1_ids.json", "w"))
PY
expect 0 "Skipped: already posted weekly:2026-10-05..2026-10-09" out "C4: a state_version 1 file is read; its posted period is honoured" \
  --bundle sample_bundle_week1.json "${W1[@]}" --state "$TMP/v1.json" --scheduled
expect 0 "Commitments" out "C4: re-running the same bundle on a v1 state" --bundle "$TMP/c4.json" "${W1[@]}" --state "$TMP/v1.json"
python3 - "$TMP" <<'PY' || fail "C4: v1 ids not kept"
import json, sys
tmp = sys.argv[1]
d = json.load(open(tmp + "/v1.json", encoding="utf-8")); old = json.load(open(tmp + "/v1_ids.json"))
assert d["state_version"] == 2, d["state_version"]
assert set(old) <= set(d["commitments"]), "old ids lost"
new = [r for c, r in d["commitments"].items() if c not in old]
assert [r["deliverable"] for r in new] == ["Send the legal redlines"], [r["deliverable"] for r in new]
assert [r["status"] for r in d["runs"]] == ["posted"], d["runs"]
PY
ok "C4: v1 state migrates to v2, keeps every old id, and only the second deliverable becomes a new item"

# ---------------------------------------------------------------- C5: window from the last posted run
python3 build_commitments.py --next-window weekly --as-of 2026-10-16T16:00:00-07:00 --state "$S2" >"$TMP/nw.json"
python3 -c "import json; d=json.load(open('$TMP/nw.json')); assert (d['from'], d['to'], d['pending']) == ('2026-10-09', '2026-10-16', []), d" \
  || fail "C5: --next-window"
ok "C5: --next-window starts on the day of the last posted run (catches Friday evening and the weekend)"
python3 build_commitments.py --next-window weekly --as-of 2026-10-07T09:00:00-07:00 --state "$TMP/no_state.json" >"$TMP/nw0.json"
python3 -c "import json; d=json.load(open('$TMP/nw0.json')); assert (d['from'], d['last_posted']) == ('2026-10-05', None), d" \
  || fail "C5: --next-window without history"
ok "C5: with no posted run yet the weekly window starts on Monday"
variant c5_gap sample_bundle_week2.json 'b["scan"]["from"] = "2026-10-12"'
expect 0 "Gap: the window after the last posted digest starts Fri Oct 9, but this window starts Mon Oct 12" out \
  "C5: a window that skips days since the last posted run is flagged" --bundle "$TMP/c5_gap.json" "${W2[@]}" --state "$S2" --scheduled --dry-run

# ---------------------------------------------------------------- C6: on-request and --owner runs don't touch reporting state
S6="$TMP/c6_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${W1[@]}" --state "$S6" --owner "Dana Ortiz" >/dev/null
python3 build_commitments.py --bundle sample_bundle_week1.json "${W1[@]}" --state "$S6" >/dev/null
python3 -c "import json,sys; d=json.load(open('$S6')); sys.exit(0 if not any(r['first_reported'] or r['done_reported'] for r in d['commitments'].values()) and d['runs']==[] else 1)" \
  || fail "C6: on-request run changed reporting state"
ok "C6: on-request and --owner runs never mark items reported or log a period"
expect 0 "**new**" out "C6: the next scheduled digest still announces everything" --bundle sample_bundle_week1.json "${W1[@]}" --state "$S6" --scheduled
[[ "$(grep -c '\*\*new\*\*' "$TMP/out")" -eq 6 ]] || fail "C6: items lost their new marker"
has "Done since last run (2)" "C6: done items still reach the team digest"

# ---------------------------------------------------------------- X1 / C7: Slack and markdown injection, URLs
variant inj sample_bundle_week1.json '
b["meetings"][0]["title"] = "<!channel> Q4 [click](javascript:alert(1)) | <@U012AB3CD>"
b["meetings"][0]["url"] = "javascript:alert(1)"
b["meetings"][3]["url"] = "https://notes.granola.ai/d/sample 5e6f(x)<y>"
b["commitments"][0]["owner"] = "Dana <!here> Ortiz"
b["commitments"][0]["deliverable"] = "Send the SOW <https://evil.example|click> &amp; *now*\n## injected"
b["commitments"][0]["quote"] = "I will <!everyone> send it <#C024BE7LR> by Wednesday ` [x](https://evil.example)"
b["commitments"][3]["counterpart"] = "<!subteam^S1> Fennaroot"
b["evidence"][0]["url"] = "data:text/html,<script>1</script>"
b["evidence"][0]["summary"] = "Sent <https://evil.example|pricing>"
b["evidence"][1]["why"] = "<!channel> weak"
b["sources"]["tracker"]["detail"] = "<!here> not connected"
'
for fmt in markdown slack; do
  expect 0 "Commitments" out "X1: hostile strings render ($fmt)" --bundle "$TMP/inj.json" "${W1[@]}" --format "$fmt" --soft-mentions
  python3 - "$TMP/out" "$fmt" <<'PY' || fail "X1: injection survived in $fmt"
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
fmt = sys.argv[2]
for bad in ["<!channel>", "<!here>", "<!everyone>", "<@U012AB3CD>", "<#C024BE7LR>", "<!subteam", "<https://evil", "<script>",
            "\n## injected"]:
    assert bad not in text, bad
if fmt == "slack":
    links = re.findall(r"<([^>]*)>", text)
    assert links and all(re.fullmatch(r"https?://[^|\s]+\|[^|]*", l) for l in links), links  # only script-built links
    assert not any("evil" in l.split("|")[0] or "javascript" in l for l in links)
    assert "&lt;!channel&gt;" in text and "&amp;amp;" in text and "\n*Overdue (" in text
else:
    targets = re.findall(r"(?<!\\)\]\(([^)]*)\)", text)  # every unescaped ](...) is a real markdown link
    assert targets and all(t.startswith(("https://", "http://")) and "evil" not in t for t in targets), targets
    assert "&lt;!channel&gt; Q4 \\[click\\](javascript:alert(1)) \\|" in text and "\\*now\\*" in text
assert "https://notes.granola.ai/d/sample%205e6f%28x%29%3Cy%3E" in text
assert "Links dropped: 2 links (only absolute http/https links are shown): `$.meetings[0].url`, `$.evidence[0].url`." in text
PY
  ok "X1: no ping, mention, raw HTML, injected link or javascript:/data: URL survives ($fmt); unsafe URL chars encoded"
done

# ---------------------------------------------------------------- X2: strict dates and numbers (same on 3.8 and 3.14)
variant x2_compact sample_bundle_week1.json 'b["scan"]["from"] = "20261005"'
expect 2 "\$.scan.from: '20261005' is not a YYYY-MM-DD date" err "X2: compact date rejected" --bundle "$TMP/x2_compact.json" "${W1[@]}"
variant x2_week sample_bundle_week1.json 'b["evidence"][0]["date"] = "2026-W41-4"'
expect 2 "\$.evidence[0].date: '2026-W41-4' is not a YYYY-MM-DD date" err "X2: ISO-week date rejected" --bundle "$TMP/x2_week.json" "${W1[@]}"
variant x2_tail sample_bundle_week1.json 'b["evidence"][0]["date"] = "2026-10-08junk"'
expect 2 "is not a YYYY-MM-DD date" err "X2: trailing junk after a date rejected (no slicing)" --bundle "$TMP/x2_tail.json" "${W1[@]}"
variant x2_start sample_bundle_week1.json 'b["meetings"][0]["start"] = "20261005T100000-0700"'
expect 2 "is not an ISO-8601 datetime with a UTC offset" err "X2: compact datetime rejected" --bundle "$TMP/x2_start.json" "${W1[@]}"
expect 2 "--as-of: '2026-10-09T16:00:00.5-07:00' is not an ISO-8601 datetime" err "X2: odd fractional seconds rejected on every Python" \
  --bundle sample_bundle_week1.json --config commitments.json --as-of 2026-10-09T16:00:00.5-07:00 --internal-domain brackenvole.example
variant x2_far sample_bundle_week1.json 'b["scan"]["to"] = "9999-12-31"; b["meetings"][0]["start"] = "9999-12-31T10:00:00-07:00"'
expect 2 "is outside the years 1900-2999" err "X2: far-future dates rejected instead of overflowing" --bundle "$TMP/x2_far.json" "${W1[@]}"
printf '%s' "$(sed 's/"history_limit_days": null/"history_limit_days": NaN/' sample_bundle_week1.json)" >"$TMP/x2_nan.json"
expect 2 "NaN is not allowed" err "X2: NaN rejected" --bundle "$TMP/x2_nan.json" "${W1[@]}"
python3 -c "print('[' * 100000 + ']' * 100000)" >"$TMP/x2_deep.json"
expect 2 "nested too deeply" err "X2: absurdly nested JSON rejected cleanly" --bundle "$TMP/x2_deep.json" "${W1[@]}"
python3 -c "import json; c=json.load(open('commitments.json')); c['holidays']=['20261225']; json.dump(c, open('$TMP/x2_cfg.json','w'))"
expect 2 "\$.holidays[0]: '20261225' is not a YYYY-MM-DD date" err "X2: compact holiday rejected" --bundle sample_bundle_week1.json \
  --config "$TMP/x2_cfg.json" --as-of 2026-10-09T16:00:00-07:00 --internal-domain brackenvole.example
python3 -c "import json; c=json.load(open('commitments.json')); c['prune_after_days']=10**12; json.dump(c, open('$TMP/x2_cfg2.json','w'))"
expect 2 "\$.prune_after_days: must be an integer from 1 to 36500" err "X2: huge config number rejected instead of overflowing" \
  --bundle sample_bundle_week1.json --config "$TMP/x2_cfg2.json" --as-of 2026-10-09T16:00:00-07:00 --internal-domain brackenvole.example

# ---------------------------------------------------------------- C8: strict state validation (single-field mutation fuzz)
S8="$TMP/c8_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${W1[@]}" --state "$S8" --scheduled >/dev/null 2>&1
python3 build_commitments.py --confirm-posted weekly:2026-10-05..2026-10-09 --state "$S8" >/dev/null
python3 build_commitments.py --bundle sample_bundle_week2.json "${W2[@]}" --state "$S8" --scheduled >/dev/null 2>&1
python3 - "$S8" "$TMP" <<'PY' || fail "C8: state mutation fuzz"
import contextlib, copy, importlib.util, io, json, sys
spec = importlib.util.spec_from_file_location("bc", "build_commitments.py"); bc = importlib.util.module_from_spec(spec); spec.loader.exec_module(bc)
base = json.load(open(sys.argv[1], encoding="utf-8")); tmp = sys.argv[2]
target = tmp + "/fuzz_state.json"
def paths(node, prefix=()):
    yield prefix
    if isinstance(node, dict):
        for k in sorted(node):
            yield from paths(node[k], prefix + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from paths(v, prefix + (i,))
commands = [
    ["--bundle", "sample_bundle_week2.json", "--config", "commitments.json", "--as-of", "2026-10-16T16:00:00-07:00",
     "--internal-domain", "brackenvole.example", "--state", target, "--scheduled", "--resume-pending", "--dry-run"],
    ["--list-open", "--state", target],
]
MUTATIONS = [("null", None), ("int", 7), ("str", "x"), ("list", []), ("dict", {}), ("bool", True), ("missing", KeyError)]
runs = crashes = rejected = 0
for path in list(paths(base))[1:]:
    original = base
    for key in path:
        original = original[key]
    # A string field given a number, list, object or boolean must always be refused (counts are informational).
    strict = isinstance(original, str) and "counts" not in path
    for name, value in MUTATIONS:
        state = copy.deepcopy(base)
        parent = state
        for key in path[:-1]:
            parent = parent[key]
        if value is KeyError:
            if isinstance(parent, list):
                del parent[path[-1]]
            else:
                del parent[path[-1]]
        else:
            parent[path[-1]] = copy.deepcopy(value)
        mutated = json.dumps(state)
        for argv in commands + ([["--confirm-posted", "weekly:2026-10-09..2026-10-16", "--state", target]] if path[0] == "runs" else []):
            runs += 1
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(mutated)
            out, err = io.StringIO(), io.StringIO()
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    code = bc.main(argv)
            except SystemExit as exc:
                code = exc.code
            except Exception as exc:  # any traceback is a failure
                crashes += 1
                print("CRASH %s=%s %s: %r" % ("/".join(map(str, path)), name, argv[0], exc))
                continue
            if code not in (0, 2):
                crashes += 1
                print("EXIT %s %s=%s %s" % (code, "/".join(map(str, path)), name, argv[0]))
            if strict and name in ("int", "list", "dict", "bool") and code != 2:
                crashes += 1
                print("ACCEPTED wrong type %s=%s %s (exit %s)" % ("/".join(map(str, path)), name, argv[0], code))
            if code == 2:
                rejected += 1
                if out.getvalue():
                    crashes += 1
                    print("PRINTED before error: %s=%s" % ("/".join(map(str, path)), name))
print("fuzz: %d runs, %d rejected with exit 2, %d crashes" % (runs, rejected, crashes))
assert crashes == 0 and rejected > runs // 3, (runs, rejected, crashes)
PY
ok "C8: every single-field mutation of a real state (null, wrong type, missing) exits 0 or 2, never a traceback; wrong types always exit 2"
for bad in '[]' '{"state_version": 2, "commitments": [], "runs": []}' '{"state_version": true, "commitments": {}, "runs": []}' \
           '{"state_version": 2, "commitments": {"ct-0000000000": {"id": "ct-1111111111"}}, "runs": []}' \
           '{"state_version": 2, "commitments": {}, "runs": [{"period_key": "x", "as_of": "20261009T1600", "mode": "scheduled", "status": "posted", "staged": null}]}'; do
  printf '%s' "$bad" >"$TMP/c8_bad.json"
  expect 2 "refusing to run blind" err "C8: corrupt state rejected: ${bad:0:40}" --bundle sample_bundle_week1.json "${W1[@]}" --state "$TMP/c8_bad.json"
done

# ---------------------------------------------------------------- C9: small fixes
python3 - <<'PY' || fail "C9: month end on a weekend"
import importlib.util, json
from datetime import date
spec = importlib.util.spec_from_file_location("bc", "build_commitments.py"); bc = importlib.util.module_from_spec(spec); spec.loader.exec_module(bc)
cfg = json.load(open("commitments.json")); cfg["_business"] = [0, 1, 2, 3, 4]; cfg["_holidays"] = set()
cal = bc.Calendar(cfg)
def r(phrase, day):
    out = bc.resolve_due({"kind": "relative", "phrase": phrase}, day, cal)
    return (out["date"].isoformat() if out["date"] else None, out["kind"])
assert r("end of month", date(2026, 10, 31)) == ("2026-11-30", "inferred"), r("end of month", date(2026, 10, 31))   # Saturday
assert r("end of month", date(2026, 10, 30)) == ("2026-10-30", "inferred")
assert r("end of quarter", date(2027, 1, 3)) == ("2027-03-31", "inferred")                # Sunday after quarter start
assert r("end of quarter", date(2026, 12, 31)) == ("2026-12-31", "inferred")
assert r("end of quarter", date(2027, 3, 31)) == ("2027-03-31", "inferred")
assert r("end of month", date(2027, 7, 31)) == ("2027-08-31", "inferred")                 # Saturday Jul 31
PY
ok "C9: 'end of month/quarter' said on a weekend after the last business day rolls to the next period"
variant c9_year sample_bundle_week1.json 'b["commitments"][5]["due"] = {"kind": "relative", "phrase": "by Oct 20"}'
expect 0 "due Tue Oct 20 (year inferred from “by Oct 20”)" out "C9: year-only inference is labelled" --bundle "$TMP/c9_year.json" "${W1[@]}"
variant c9_reopen "$TMP/week3.json" 'b["user_actions"] = [{"commitment_id": "ct-97efb9ee06", "action": "reopen", "date": "2026-10-20", "note": "Priya says v2 is missing the appendix"}]'
expect 0 "Reopened by you: Priya says v2 is missing the appendix (Tue Oct 20)." out "C9: a done item can be reopened" \
  --bundle "$TMP/c9_reopen.json" "${W3[@]}" --state "$S"
python3 build_commitments.py --list-open --state "$S" >"$TMP/open2.json"
python3 -c "import json; d=json.load(open('$TMP/open2.json')); assert 'ct-97efb9ee06' in {x['commitment_id'] for x in d}" || fail "C9: reopened item not in --list-open"
ok "C9: a reopened item is back in --list-open"
expect 0 "No state file at $TMP/never.json yet: nothing is tracked." out "C9: --list-open without a state file says so" --list-open --state "$TMP/never.json"
python3 -c "import json; c=json.load(open('commitments.json')); c['prune_open_after_days']=5; json.dump(c, open('$TMP/c9_cfg.json','w'))"
expect 0 "items open more than 5 days with no new mention dropped from tracking" out "C9: open items are pruned by age (configurable)" \
  --bundle "$TMP/week3.json" --config "$TMP/c9_cfg.json" --as-of 2026-10-23T16:00:00-07:00 --internal-domain brackenvole.example --state "$S" --dry-run
python3 -c "import json; c=json.load(open('commitments.json')); del c['prune_open_after_days']; json.dump(c, open('$TMP/c9_old.json','w'))"
expect 0 "Commitments" out "C9: older configs without prune_open_after_days still work (default 180)" \
  --bundle sample_bundle_week1.json --config "$TMP/c9_old.json" --as-of 2026-10-09T16:00:00-07:00 --internal-domain brackenvole.example

# ================================================================ QA round 2 regressions (R1-R12, cross-cutting)
WD=(--config commitments.json --internal-domain brackenvole.example)

# window_bundle NEXT_WINDOW_JSON OUT : the bundle an agent builds for the window --next-window returned
# (sources from the week-1 sample, only meetings inside the window, no evidence or replies)
window_bundle() {
  python3 - "$1" "$2" <<'PY'
import json, sys
nw = json.load(open(sys.argv[1], encoding="utf-8"))
b = json.load(open("sample_bundle_week1.json", encoding="utf-8"))
b["scan"] = {"from": nw["from"], "to": nw["to"], "cadence": nw["cadence"]}
b["meetings"] = [m for m in b["meetings"] if nw["from"] <= m["start"][:10] <= nw["to"]]
ids = {m["meeting_id"] for m in b["meetings"]}
b["commitments"] = [c for c in b["commitments"] if c["meeting_id"] in ids]
b["evidence"] = []
b["user_actions"] = []
json.dump(b, open(sys.argv[2], "w", encoding="utf-8"), ensure_ascii=False)
PY
}

# ---------------------------------------------------------------- R1: the documented automation flow, end to end
SR1="$TMP/r1_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$SR1" --scheduled >/dev/null 2>&1
python3 build_commitments.py --confirm-posted weekly:2026-10-05..2026-10-09 --as-of 2026-10-09T16:01:00-07:00 --state "$SR1" >/dev/null
expect 0 '"already_posted": {' out "R1: --next-window at 16:30 reports that this week's digest is already posted" \
  --next-window weekly --as-of 2026-10-09T16:30:00-07:00 --state "$SR1"
cp "$TMP/out" "$TMP/r1_nw.json"
python3 -c "import json; d=json.load(open('$TMP/r1_nw.json')); assert d['already_posted']['period_key'] == 'weekly:2026-10-05..2026-10-09', d" \
  || fail "R1: already_posted does not name the posted period"
ok "R1: already_posted names the confirmed period"
window_bundle "$TMP/r1_nw.json" "$TMP/r1_bundle.json"
cp "$SR1" "$TMP/r1_before.json"
expect 0 "Skipped: already posted weekly:2026-10-05..2026-10-09" out \
  "R1: stage 16:00 -> confirm -> 16:30 --next-window -> run: the second run of the week posts nothing" \
  --bundle "$TMP/r1_bundle.json" "${WD[@]}" --as-of 2026-10-09T16:30:00-07:00 --state "$SR1" --scheduled
hasnt "## Commitments" "R1: no digest is printed for the same weekly slot"
cmp -s "$SR1" "$TMP/r1_before.json" || fail "R1: the skipped run changed the state"
ok "R1: the skipped run leaves the state byte-identical"
python3 build_commitments.py --next-window weekly --as-of 2026-10-16T16:00:00-07:00 --state "$SR1" >"$TMP/r1_nw2.json"
python3 -c "import json; d=json.load(open('$TMP/r1_nw2.json')); assert d['already_posted'] is None and d['from'] == '2026-10-09', d" \
  || fail "R1: next week's window"
ok "R1: next week's --next-window is not blocked"
expect 0 "## Commitments — Fri Oct 9 to Fri Oct 16, 2026" out "R1: next week's scheduled run still posts" \
  --bundle sample_bundle_week2.json "${WD[@]}" --as-of 2026-10-16T16:00:00-07:00 --state "$SR1" --scheduled
# daily cadence: one digest per day
SR1d="$TMP/r1d_state.json"
variant r1_daily sample_bundle_week1.json 'b["scan"]["cadence"] = "daily"'
python3 build_commitments.py --bundle "$TMP/r1_daily.json" "${WD[@]}" --as-of 2026-10-09T08:00:00-07:00 --state "$SR1d" --scheduled >/dev/null 2>&1
python3 build_commitments.py --confirm-posted daily:2026-10-05..2026-10-09 --state "$SR1d" >/dev/null
python3 build_commitments.py --next-window daily --as-of 2026-10-09T08:30:00-07:00 --state "$SR1d" >"$TMP/r1d_nw.json"
window_bundle "$TMP/r1d_nw.json" "$TMP/r1d_bundle.json"
expect 0 "Skipped: already posted daily:2026-10-05..2026-10-09" out "R1: a daily schedule that fires twice in one day posts once" \
  --bundle "$TMP/r1d_bundle.json" "${WD[@]}" --as-of 2026-10-09T08:30:00-07:00 --state "$SR1d" --scheduled

# ---------------------------------------------------------------- R2: no posted run yet -> earliest pending/attempted window
SR2="$TMP/r2_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$SR2" --scheduled >/dev/null 2>&1
python3 build_commitments.py --next-window weekly --as-of 2026-10-16T16:00:00-07:00 --state "$SR2" >"$TMP/r2_nw.json"
python3 -c "import json; d=json.load(open('$TMP/r2_nw.json')); assert (d['from'], d['pending']) == ('2026-10-05', ['weekly:2026-10-05..2026-10-09']), d" \
  || fail "R2: unconfirmed week 1 skipped by --next-window"
ok "R2: with week 1 never confirmed, next week's window starts at week 1's from"
expect 0 "Gap: no digest has been confirmed posted yet; the earliest unposted window started Mon Oct 5" out \
  "R2: a window that skips unposted days says so" --bundle sample_bundle_week2.json "${WD[@]}" --as-of 2026-10-16T16:00:00-07:00 --state "$SR2" --scheduled --dry-run
SR2b="$TMP/r2b_state.json"
variant r2_down sample_bundle_week1.json 'b["sources"]["meetings"]["status"] = "unavailable"; b["sources"]["meetings"]["detail"] = "MCP timeout"; b["meetings"] = []; b["commitments"] = []; b["evidence"] = []'
python3 build_commitments.py --bundle "$TMP/r2_down.json" "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$SR2b" --scheduled >/dev/null 2>&1 || true
python3 build_commitments.py --next-window weekly --as-of 2026-10-16T16:00:00-07:00 --state "$SR2b" >"$TMP/r2b_nw.json"
python3 -c "import json; d=json.load(open('$TMP/r2b_nw.json')); assert d['from'] == '2026-10-05' and d['already_posted'] is None, d" \
  || fail "R2: a first run that exited 5 lost its window"
ok "R2: a first run that exited 5 (Granola down) is scanned again next time"
expect 0 "the period stays open" out "R2/C4: confirming an incomplete period only records what its digest announced" --confirm-posted weekly:2026-10-05..2026-10-09 --state "$SR2b"
python3 build_commitments.py --next-window weekly --as-of 2026-10-16T16:00:00-07:00 --state "$SR2b" >"$TMP/r2b_nw.json"
python3 -c "import json; d=json.load(open('$TMP/r2b_nw.json')); assert d['from'] == '2026-10-05' and d['last_posted'] is None, d" \
  || fail "R2: a confirmed incomplete period was recorded as covered"
ok "R2/C4: the confirmed incomplete period is still scanned again next time"
printf '%s' '{"state_version": 2, "commitments": {}, "runs": [{"period_key": "weekly:2026-10-05..2026-10-09", "as_of": "2026-10-09T16:00:00-07:00", "mode": "scheduled", "status": "incomplete", "staged": null}]}' >"$TMP/r2c_state.json"
expect 2 "was incomplete" err "R2: an incomplete period logged by an older version (nothing staged) cannot be confirmed" \
  --confirm-posted weekly:2026-10-05..2026-10-09 --state "$TMP/r2c_state.json"

# ---------------------------------------------------------------- R3: partial coverage is never complete
SR3="$TMP/r3_state.json"
variant r3_old sample_bundle_week1.json 'b["scan"]["from"] = "2026-08-31"; b["meetings"] = []; b["commitments"] = []; b["evidence"] = []'
expect 5 "Incomplete: this window starts Mon Aug 31, but the free plan's 30-day note history only reaches back to Wed Sep 9" out \
  "R3: a window older than the plan's note history is incomplete on a schedule (exit 5)" \
  --bundle "$TMP/r3_old.json" "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$SR3" --scheduled
hasnt "nothing to report" "R3: never 'nothing to report' when the window is past the history limit"
python3 -c "import json,sys; d=json.load(open('$SR3')); sys.exit(0 if all(r['status'] == 'incomplete' for r in d['runs']) else 1)" \
  || fail "R3: a window past the history limit was recorded"
ok "R3: a window past the history limit is never recorded as posted or pending"
expect 0 "the period stays open" out "R3/C4: confirming it only records what its digest announced" --confirm-posted weekly:2026-08-31..2026-10-09 --state "$SR3"
python3 -c "import json,sys; d=json.load(open('$SR3')); sys.exit(0 if all(r['status'] == 'incomplete' for r in d['runs']) else 1)" \
  || fail "R3/C4: a confirmed incomplete period was recorded as posted"
ok "R3/C4: the confirmed incomplete period is still not recorded as posted"
variant r3_inside sample_bundle_week1.json 'b["scan"]["from"] = "2026-09-09"; b["meetings"] = []; b["commitments"] = []; b["evidence"] = []'
expect 0 "Skipped: nothing to report" out "R3: the same empty run inside the history limit is complete" \
  --bundle "$TMP/r3_inside.json" "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$TMP/r3b_state.json" --scheduled
python3 build_commitments.py --bundle "$TMP/r3_old.json" "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --json >"$TMP/r3.json" || true
python3 -c "import json; d=json.load(open('$TMP/r3.json')); assert d['status'] == 'incomplete' and d['coverage']['incomplete'], d" \
  || fail "R3: --json does not report the incomplete window"
ok "R3: --json reports status incomplete with the reason"
variant r3_unread sample_bundle_week1.json 'b["commitments"] = []; b["evidence"] = []; b["meetings"] = [dict(m, notes_status="denied", transcript_status="unavailable") for m in b["meetings"]]'
expect 5 "Incomplete: none of the 6 meetings in this window could be read" out \
  "R3: every meeting's notes and transcript unreadable -> incomplete, exit 5" \
  --bundle "$TMP/r3_unread.json" "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$TMP/r3c_state.json" --scheduled
expect 0 "the period stays open" out "R3 (all unreadable)/C4: confirming it only records what its digest announced" --confirm-posted weekly:2026-10-05..2026-10-09 --state "$TMP/r3c_state.json"
python3 -c "import json,sys; d=json.load(open('$TMP/r3c_state.json')); sys.exit(0 if all(r['status'] == 'incomplete' for r in d['runs']) else 1)" \
  || fail "R3 (all unreadable)/C4: a confirmed incomplete period was recorded as posted"
ok "R3 (all unreadable)/C4: the confirmed incomplete period is still not recorded as posted"
# (a read that failed with "unavailable" can be retried, so it makes the run Incomplete instead: see QA round 4, C1)
variant r3_some sample_bundle_week1.json 'b["meetings"][5]["notes_status"] = "denied"; b["commitments"] = [c for c in b["commitments"] if c["meeting_id"] != "sample-9c0d"]'
expect 0 "Notes denied: [Weekly pipeline]" out "R3: one meeting denied among several is listed, run still completes" \
  --bundle "$TMP/r3_some.json" "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$TMP/r3d_state.json" --scheduled

# ---------------------------------------------------------------- R4: far-future relative dates
python3 - <<'PY' || fail "R4: resolve_due range check"
import importlib.util, json
from datetime import date
spec = importlib.util.spec_from_file_location("bc", "build_commitments.py"); bc = importlib.util.module_from_spec(spec); spec.loader.exec_module(bc)
cfg = json.load(open("commitments.json")); cfg["_business"] = [0, 1, 2, 3, 4]; cfg["_holidays"] = set()
cal = bc.Calendar(cfg)
def r(phrase, day=date(2026, 10, 9)):
    return bc.resolve_due({"kind": "relative", "phrase": phrase}, day, cal)
assert r("in 3650 days")["date"] == date(2036, 10, 6), r("in 3650 days")
for phrase in ["in 3651 days", "in 400000 days", "in 99999999 days", "in 99999999 weeks", "in 99999999 business days",
               "within 5000 business days"]:
    out = r(phrase)
    assert out["date"] is None and out["kind"] == "none" and "more than 3650 days" in out["note"], (phrase, out)
for phrase in ["next month", "end of quarter", "next week", "in 30 days", "Dec 31"]:
    out = r(phrase, date(2999, 12, 20))
    assert out["date"] is None or out["date"].year <= 2999, (phrase, out)
PY
ok "R4: relative dates beyond 3650 days (or past 2999) resolve to no date with a note, no overflow"
SR4="$TMP/r4_state.json"
variant r4 sample_bundle_week1.json 'b["commitments"][5]["due"] = {"kind": "relative", "phrase": "in 400000 days"}; b["commitments"][0]["due"] = {"kind": "relative", "phrase": "in 99999999 days"}; b["commitments"][1]["due"] = {"kind": "relative", "phrase": "in 99999999 business days"}'
expect 0 "is more than 3650 days out — confirm" out "R4: a far-future phrase is shown as needing confirmation" \
  --bundle "$TMP/r4.json" "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$SR4"
expect 0 "## Commitments" out "R4: the state stays valid for the next run" \
  --bundle sample_bundle_week2.json "${WD[@]}" --as-of 2026-10-16T16:00:00-07:00 --state "$SR4"

# ---------------------------------------------------------------- R5: reworded re-extraction in the one-day overlap
SR5="$TMP/r5_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$SR5" --scheduled >/dev/null 2>&1
python3 build_commitments.py --confirm-posted weekly:2026-10-05..2026-10-09 --state "$SR5" >/dev/null
variant r5 sample_bundle_week1.json '
b["scan"] = {"from": "2026-10-08", "to": "2026-10-09", "cadence": "on_request"}
b["meetings"] = [m for m in b["meetings"] if m["start"][:10] >= "2026-10-08"]
b["commitments"] = [dict(c, deliverable="Get the revised statement of work over to Fennaroot") for c in b["commitments"] if c["ref"] == "c9"]
b["evidence"] = []'
python3 -c "import json; b=json.load(open('$TMP/r5.json')); assert len(b['commitments']) == 1, b['commitments']" || fail "R5: fixture"
python3 build_commitments.py --bundle "$TMP/r5.json" "${WD[@]}" --as-of 2026-10-09T17:00:00-07:00 --state "$SR5" >"$TMP/out"
python3 -c "import json,sys; d=json.load(open('$SR5'))['commitments']; sys.exit(0 if len(d)==8 and len(d['ct-97efb9ee06']['mentions'])==2 else 1)" \
  || fail "R5: a reworded re-extraction of the same quote became a second item"
ok "R5: same meeting + same verbatim quote + same owner merges a reworded deliverable"
hasnt "Get the revised statement of work over" "R5: no duplicate item is listed"
variant r5_two sample_bundle_week1.json 'b["commitments"].append(dict(b["commitments"][0], ref="c1x", deliverable="Send the legal redlines"))'
python3 build_commitments.py --bundle "$TMP/r5_two.json" "${WD[@]}" --as-of 2026-10-09T17:00:00-07:00 --state "$SR5" >"$TMP/out"
has "**Send the legal redlines**" "R5: a second deliverable from one sentence is still its own item"

# ---------------------------------------------------------------- R6-R8: reopen ordering
SR6="$TMP/r6_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$SR6" --scheduled >/dev/null 2>&1
python3 build_commitments.py --confirm-posted weekly:2026-10-05..2026-10-09 --state "$SR6" >/dev/null
W6=(--config commitments.json --as-of 2026-10-16T16:00:00-07:00 --internal-domain brackenvole.example)
R6BASE='
b["scan"] = {"from": "2026-10-12", "to": "2026-10-16", "cadence": "on_request"}
b["meetings"] = [dict(b["meetings"][0], meeting_id="sample-r6r6", title="Fennaroot Dental check-in", start="2026-10-14T10:00:00-07:00", url="https://notes.granola.ai/d/sample-r6r6")]
b["commitments"] = []; b["evidence"] = []; b["user_actions"] = []'
variant r6 sample_bundle_week1.json "$R6BASE"'
b["commitments"] = [{"ref": "n1", "meeting_id": "sample-r6r6", "direction": "we_owe", "owner": "Sam Kim", "owner_email": "sam@brackenvole.example", "counterpart": "Fennaroot Dental", "deliverable": "Send pricing for the two new locations", "quote": "I will resend the pricing for the two new locations by Friday.", "quote_source": "transcript", "due": {"kind": "relative", "phrase": "by Friday"}}]
b["user_actions"] = [{"commitment_id": "ct-898a3abf81", "action": "reopen", "date": "2026-10-14", "note": "Priya never got the pricing"}]'
cp "$SR6" "$TMP/r6a.json"
python3 build_commitments.py --bundle "$TMP/r6.json" "${W6[@]}" --state "$TMP/r6a.json" --json >"$TMP/r6.out"
python3 - "$TMP/r6.out" "$TMP/r6a.json" <<'PY' || fail "R6: reopen + repeat made two open items"
import json, sys
out = json.load(open(sys.argv[1], encoding="utf-8")); st = json.load(open(sys.argv[2], encoding="utf-8"))["commitments"]
hits = [i for g in out["groups"].values() for i in g if i["deliverable"] == "Send pricing for the two new locations"]
assert [i["id"] for i in hits] == ["ct-898a3abf81"], hits
assert len(st) == 8 and len(st["ct-898a3abf81"]["mentions"]) == 3, (len(st), st["ct-898a3abf81"]["mentions"])
assert "promised again" not in out["markdown"]
PY
ok "R6: reopen is applied before merging, so the repeated promise joins the reopened item"
cp "$SR6" "$TMP/r7a.json"
variant r7 sample_bundle_week1.json "$R6BASE"'
b["evidence"] = [{"commitment_id": "ct-898a3abf81", "kind": "later_meeting", "strength": "against", "date": "2026-10-14", "url": "https://notes.granola.ai/d/sample-r6r6", "summary": "Priya is still waiting on the pricing"}]'
expect 0 "Still outstanding per [Priya is still waiting on the pricing]" out "R7: 'still waiting' evidence reopens a reported-done item" \
  --bundle "$TMP/r7.json" "${W6[@]}" --state "$TMP/r7a.json"
python3 -c "import json,sys; d=json.load(open('$TMP/r7a.json'))['commitments']['ct-898a3abf81']; sys.exit(0 if d['done_reported'] is None else 1)" \
  || fail "R7: done_reported still set after 'still waiting' evidence"
ok "R7: done_reported is cleared"
python3 build_commitments.py --list-open --state "$TMP/r7a.json" >"$TMP/r7_open.json"
python3 -c "import json; d=json.load(open('$TMP/r7_open.json')); assert 'ct-898a3abf81' in {x['commitment_id'] for x in d}" || fail "R7: not in --list-open"
ok "R7: the reopened item is back in --list-open"
python3 -c "import json; c=json.load(open('commitments.json')); c['prune_after_days']=1; json.dump(c, open('$TMP/r7_cfg.json','w'))"
variant r7_empty sample_bundle_week1.json "$R6BASE"'
b["meetings"] = []'
python3 build_commitments.py --bundle "$TMP/r7_empty.json" --config "$TMP/r7_cfg.json" --as-of 2026-10-16T16:00:00-07:00 --internal-domain brackenvole.example --state "$TMP/r7a.json" >/dev/null
python3 -c "import json,sys; d=json.load(open('$TMP/r7a.json'))['commitments']; sys.exit(0 if 'ct-898a3abf81' in d else 1)" \
  || fail "R7: an open (reopened) item was pruned as done"
ok "R7: a reopened item is not pruned while open"
cp "$SR6" "$TMP/r8a.json"
variant r8 sample_bundle_week1.json "$R6BASE"'
b["meetings"] = []
b["user_actions"] = [{"commitment_id": "ct-898a3abf81", "action": "reopen", "date": "2026-10-14", "note": "Priya never got it"},
                     {"commitment_id": "ct-898a3abf81", "action": "mark_done", "date": "2026-10-14", "note": "resent by hand"},
                     {"commitment_id": "ct-d3b4a7efd6", "action": "mark_done", "date": "2026-10-14", "note": "sent the checklist"},
                     {"commitment_id": "ct-d3b4a7efd6", "action": "reopen", "date": "2026-10-14", "note": "wrong checklist"}]'
expect 0 "resent by hand (Wed Oct 14)" out "R8: a same-day mark_done after a reopen counts (action order, not just date)" \
  --bundle "$TMP/r8.json" "${W6[@]}" --state "$TMP/r8a.json" --dry-run
has "Reopened by you: wrong checklist (Wed Oct 14)." "R8: a same-day reopen after a mark_done keeps the item open"
variant r8b1 sample_bundle_week1.json "$R6BASE"'
b["meetings"] = []
b["user_actions"] = [{"commitment_id": "ct-898a3abf81", "action": "reopen", "date": "2026-10-14", "note": "Priya never got it"}]'
variant r8b2 sample_bundle_week1.json "$R6BASE"'
b["meetings"] = []
b["user_actions"] = [{"commitment_id": "ct-898a3abf81", "action": "mark_done", "date": "2026-10-14", "note": "resent by hand"}]'
python3 build_commitments.py --bundle "$TMP/r8b1.json" "${W6[@]}" --state "$TMP/r8a.json" >/dev/null
expect 0 "resent by hand (Wed Oct 14)" out "R8: same-day reopen and mark_done in two runs: the later run wins" \
  --bundle "$TMP/r8b2.json" --config commitments.json --as-of 2026-10-16T16:05:00-07:00 --internal-domain brackenvole.example --state "$TMP/r8a.json"

# ---------------------------------------------------------------- R9: strict datetimes, identical on every Python
for bad in 2026-10-09T24:00:00-07:00 2026-10-09T16:00:00+00:60 2026-10-09T16:00:00+14:01 2026-10-09T16:00:00-15:00 \
           2026-10-09T16:60:00-07:00 2026-10-09T16:00:60-07:00 2026-10-09T16:00:00+0700 ２０２６-10-09T16:00:00-07:00; do
  expect 2 "--as-of: '$bad' is not" err "R9: --as-of $bad rejected" --bundle sample_bundle_week1.json "${WD[@]}" --as-of "$bad"
done
for good in 2026-10-09T23:59:59Z 2026-10-09T16:00:00+14:00 2026-10-09T16:00:00-12:00 2026-10-09T16:00:00.123+05:45; do
  expect 0 "## Commitments" out "R9: --as-of $good accepted" --bundle sample_bundle_week1.json "${WD[@]}" --as-of "$good"
done
variant r9_digits sample_bundle_week1.json 'b["evidence"][0]["date"] = "２０２６-10-08"'
expect 2 "is not a YYYY-MM-DD date" err "R9: non-ASCII digits in a date rejected" --bundle "$TMP/r9_digits.json" "${W1[@]}"

# ---------------------------------------------------------------- R10: safe_url needs a real hostname and port
python3 - <<'PY' || fail "R10: safe_url"
import importlib.util
spec = importlib.util.spec_from_file_location("bc", "build_commitments.py"); bc = importlib.util.module_from_spec(spec); spec.loader.exec_module(bc)
bad = ["https://.example/", "https://example./", "https://a..example/", "https://-x.example/", "https://x-.example/",
       "https://x_y.example/", "https://example:99999/", "https://good.example:0/", "https://good.example:/x", "https://localhost/",
       "https://10.0.0.1/", "https://good.123/", "https://good.example:65536/", "javascript:alert(1)", "https://"]
for u in bad:
    assert bc.safe_url(u) is None, (u, bc.safe_url(u))
good = {"https://notes.granola.ai/d/x": "https://notes.granola.ai/d/x", "HTTPS://Mail.Example:8443/a b": "https://mail.example:8443/a%20b",
        "https://xn--bcher-kva.example/": "https://xn--bcher-kva.example/", "http://a-b.c-d.example:65535": "http://a-b.c-d.example:65535"}
for u, want in good.items():
    assert bc.safe_url(u) == want, (u, bc.safe_url(u))
PY
ok "R10: links need a valid hostname (non-empty letter/digit/hyphen labels, alphabetic TLD) and a port 1-65535"

# ---------------------------------------------------------------- R11 + cross-cutting: escaping fuzz (markdown and Slack)
python3 - "$TMP" <<'PY' || fail "R11: escaping fuzz"
import contextlib, copy, importlib.util, io, json, re, sys
spec = importlib.util.spec_from_file_location("bc", "build_commitments.py"); bc = importlib.util.module_from_spec(spec); spec.loader.exec_module(bc)
tmp = sys.argv[1]
base = json.load(open("sample_bundle_week1.json", encoding="utf-8"))
PAYLOADS = ["\\[click\\](https://phish.example/a)", "[click](https://phish.example/a)", "![i](https://phish.example/i.png)",
            "\\\\[x\\\\](https://phish.example)", "<https://phish.example|click>", "<https://phish.example>", "<!channel> <!here>",
            "<@U012AB3CD> <#C024BE7LR|x>", "*bold", "**", "_it_ ~s~ `c`", "x*y_z~w`v", "<b>html</b> <img src=x>",
            "&lt;!here&gt; &amp;", "\\", "\\*\\*", "](https://phish.example)", "[a]: https://phish.example", "|pipe|", "cost $5 to $9 and hi", "==hl== $$x$$ a=b"]
FIELDS = [("meetings", 0, "title"), ("meetings", 3, "title"), ("meetings", 0, "account"), ("commitments", 0, "owner"),
          ("commitments", 0, "deliverable"), ("commitments", 0, "quote"), ("commitments", 3, "counterpart"),
          ("commitments", 5, "deliverable"), ("evidence", 0, "summary"), ("evidence", 1, "why"),
          ("commitments", 4, "quote"), ("commitments", 8, "quote")]
ALLOWED = ("https://notes.granola.ai/", "https://mail.google.com/", "https://calendar.google.com/")
def strip_escapes(line):
    return re.sub(r"\\.", "", line)
problems = runs = 0
for payload in PAYLOADS:
    for section, idx, key in FIELDS:
        b = copy.deepcopy(base)
        b[section][idx][key] = "pre " + payload + " post"
        b["sources"]["tracker"]["detail"] = payload
        path = tmp + "/fuzz_bundle.json"
        json.dump(b, open(path, "w", encoding="utf-8"), ensure_ascii=False)
        for fmt in ["markdown", "slack"]:
            runs += 1
            out, err = io.StringIO(), io.StringIO()
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                code = bc.main(["--bundle", path, "--config", "commitments.json", "--as-of", "2026-10-09T16:00:00-07:00",
                                "--internal-domain", "brackenvole.example", "--format", fmt, "--soft-mentions"])
            text = out.getvalue()
            def bad(why, line):
                global problems
                problems += 1
                if problems < 15:
                    print("%s | %s | %s.%d.%s | %s | %s" % (fmt, why, section, idx, key, payload, line))
            if code != 0:
                bad("exit %s %s" % (code, err.getvalue().strip()), "")
                continue
            for line in text.split("\n"):
                if fmt == "markdown":
                    s = strip_escapes(line)
                    if "<" in s:
                        bad("raw < (HTML or autolink)", line)
                    if len(re.findall(r"(?<![0-9A-Za-z])_|_(?![0-9A-Za-z])", s)) % 2:
                        bad("unbalanced italic", line)
                    for target in re.findall(r"\]\(([^)]*)\)", s):
                        if not target.startswith(ALLOWED):
                            bad("link to %s" % target, line)
                    if re.search(r"\]\s*\[|^\s*\[[^\]]*\]:", s):
                        bad("reference link", line)
                    if s.count("*") % 2 or s.replace("**", "").count("*"):
                        bad("unbalanced bold", line)
                    if s.count("`") % 2:
                        bad("unbalanced code", line)
                    if "~" in s:
                        bad("strikethrough", line)
                    if re.search(r"\$|==", re.sub(r"`[^`]*`", "", s)):
                        bad("math or highlight ($ or ==)", line)
                else:
                    for link in re.findall(r"<([^>]*)>", line):
                        if not re.fullmatch(r"https?://[^|\s<>]+\|[^|<>]*", link) or not link.startswith(ALLOWED):
                            bad("slack link/mention <%s>" % link, line)
                    rest = re.sub(r"<https?://[^|\s<>]+\|[^|<>]*>", "", line)
                    if "<" in rest:
                        bad("raw <", line)
                    if rest.count("*") % 2:
                        bad("unbalanced bold", line)
                    if rest.count("`") % 2 or "~" in rest:
                        bad("code/strike formatting", line)
                    if rest.count("_") % 2:
                        bad("unbalanced italic", line)
print("escaping fuzz: %d renders, %d problems" % (runs, problems))
assert problems == 0
PY
ok "R11: no bundle value yields a link, autolink, reference link, raw HTML, Slack mention/link or broken bold/code (markdown and Slack)"
python3 - <<'PY' || fail "R11: Slack formatting characters"
import importlib.util
spec = importlib.util.spec_from_file_location("bc", "build_commitments.py"); bc = importlib.util.module_from_spec(spec); spec.loader.exec_module(bc)
s = bc.FORMATS["slack"].text("Send *now* _really_ ~maybe~ `code` <!here> & co")
assert not any(ch in s for ch in "*_~`<>"), s
assert s == "Send ∗now∗ ＿really＿ ∼maybe∼ ˋcodeˋ &lt;!here&gt; &amp; co", s
m = bc.FORMATS["markdown"].text("\\[click\\](https://phish.example/a)")
assert m == "\\\\\\[click\\\\\\](https\\://phish\\.example/a)", m
PY
ok "R11: Slack shows * _ ~ \` as look-alikes; markdown escapes the backslash before brackets"

# ---------------------------------------------------------------- cross-cutting: stdout, broken pipe, input encoding
PYTHONIOENCODING=ascii python3 build_commitments.py --bundle sample_bundle_week1.json "${W1[@]}" >"$TMP/ascii.out" 2>"$TMP/ascii.err" \
  || { cat "$TMP/ascii.err"; fail "X: ascii stdout"; }
grep -qF "Commitments — Mon Oct 5" "$TMP/ascii.out" || fail "X: digest not written as UTF-8 on an ASCII stdout"
ok "X: stdout is UTF-8 even when the environment says ASCII"
python3 - "$TMP" <<'PY' || fail "X: broken pipe"
import os, subprocess, sys
tmp = sys.argv[1]
state = tmp + "/bp_state.json"
base = [sys.executable, "build_commitments.py", "--bundle", "sample_bundle_week1.json", "--config", "commitments.json",
        "--as-of", "2026-10-09T16:00:00-07:00", "--internal-domain", "brackenvole.example", "--state", state, "--scheduled"]
r, w = os.pipe()
os.close(r)
p = subprocess.run(base, stdout=w, stderr=subprocess.PIPE)
os.close(w)
err = p.stderr.decode("utf-8", "replace")
assert "Traceback" not in err and "Exception ignored" not in err, err
assert p.returncode == 4 and "--resume-pending" in err, (p.returncode, err)
p2 = subprocess.run(base, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
assert p2.returncode == 4 and not p2.stdout, (p2.returncode, p2.stdout)
p3 = subprocess.run(base + ["--resume-pending"], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
assert p3.returncode == 0 and b"## Commitments" in p3.stdout, p3.returncode
p4 = subprocess.run([sys.executable, "build_commitments.py", "--list-open", "--state", state], stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE, close_fds=True)
assert p4.returncode == 0
r, w = os.pipe(); os.close(r)
p5 = subprocess.run([sys.executable, "build_commitments.py", "--bundle", "sample_bundle_week1.json", "--config", "commitments.json",
                     "--as-of", "2026-10-09T16:00:00-07:00", "--internal-domain", "brackenvole.example"], stdout=w, stderr=subprocess.PIPE)
os.close(w)
err5 = p5.stderr.decode("utf-8", "replace")
assert p5.returncode == 2 and "Traceback" not in err5 and "could not write" in err5, (p5.returncode, err5)
PY
ok "X: a broken pipe after staging exits 4 cleanly (no traceback); the rerun stops with exit 4 and --resume-pending recovers"
code=0
python3 build_commitments.py --bundle sample_bundle_week1.json "${W1[@]}" >&- 2>"$TMP/closed.err" || code=$?
[[ "$code" == 2 ]] && ! grep -q Traceback "$TMP/closed.err" || { cat "$TMP/closed.err"; fail "X: closed stdout (exit $code)"; }
ok "X: a closed stdout gives a clean exit 2"
code=0
python3 build_commitments.py --bundle sample_bundle_week1.json --config commitments.json --as-of 2026-10-09T16:00:00-07:00 >"$TMP/out" 2>&- || code=$?
[[ "$code" == 2 && ! -s "$TMP/out" ]] || fail "X: closed stderr (exit $code, stdout: $(head -c 80 "$TMP/out"))"
ok "X: with stderr closed, an error message never falls back to stdout"
python3 -c "s=open('sample_bundle_week1.json', encoding='utf-8').read(); open('$TMP/x_surrogate.json', 'w', encoding='utf-8').write(s.replace('so your legal team can start.', 'so your legal \\\\ud800 team can start.'))"
expect 2 "lone surrogate" err "X: unpaired surrogates in the bundle are rejected before anything is printed" --bundle "$TMP/x_surrogate.json" "${W1[@]}"
[[ ! -s "$TMP/out" ]] || fail "X: printed before the error"
ok "X: nothing printed before the surrogate error"

# ---------------------------------------------------------------- cross-cutting: null or wrong-type lists
for edit in 'c["holidays"] = None' 'c["business_days"] = None' 'c["plan_history_days"] = None' 'c["holidays"] = "2026-12-25"' 'c["business_days"] = {"mon": 1}'; do
  python3 -c "import json; c=json.load(open('commitments.json')); $edit; json.dump(c, open('$TMP/x_cfg.json','w'))"
  expect 2 "config" err "X: config list $edit rejected with exit 2" --bundle sample_bundle_week1.json --config "$TMP/x_cfg.json" \
    --as-of 2026-10-09T16:00:00-07:00 --internal-domain brackenvole.example
done
for edit in 'b["meetings"] = None' 'b["commitments"] = None' 'b["evidence"] = None' 'b["user_actions"] = None' 'b["meetings"][0]["attendees"] = None' 'b["evidence"] = {}'; do
  variant x_list sample_bundle_week1.json "$edit"
  expect 2 "must be a list" err "X: bundle list $edit rejected with exit 2" --bundle "$TMP/x_list.json" "${W1[@]}"
done

# ---------------------------------------------------------------- cross-cutting: lock edge cases
S7="$TMP/lock2_state.json"
touch "$S7.lock"; touch -d "+2 hours" "$S7.lock"
expect 0 "## Commitments" out "X: a lock with a future mtime is treated as stale" --bundle sample_bundle_week1.json "${W1[@]}" --state "$S7" --scheduled
[[ ! -e "$S7.lock" ]] || fail "X: lock left behind"
mkdir -p "$TMP/lockdir_state.json.lock"
expect 2 "is not a regular file" err "X: a lock path that is a directory is refused (exit 2)" --bundle sample_bundle_week1.json "${W1[@]}" --state "$TMP/lockdir_state.json" --scheduled
[[ ! -s "$TMP/out" ]] || fail "X: lock-directory run printed"
python3 - "$TMP" <<'PY' || fail "X: lock ownership"
import importlib.util, os, sys, time
from pathlib import Path
spec = importlib.util.spec_from_file_location("bc", "build_commitments.py"); bc = importlib.util.module_from_spec(spec); spec.loader.exec_module(bc)
tmp = Path(sys.argv[1])
lock = bc.StateLock(tmp / "own.json")
lock.acquire()
os.unlink(str(lock.path))
lock.path.write_text("pid 1 token someone-else\n")    # another run took over meanwhile
lock.release()
assert lock.path.exists(), "release removed a lock this run does not hold"
os.unlink(str(lock.path))
# a stale lock is removed only if it is still the same file (inode) that was found stale
stale = bc.StateLock(tmp / "inode.json")
stale.path.write_text("pid 1 token old\n")
os.utime(str(stale.path), (time.time() - 3600, time.time() - 3600))
old = os.stat(str(stale.path))
os.unlink(str(stale.path))
stale.path.write_text("pid 2 token fresh\n")              # replaced by a live run before the takeover
assert not bc.remove_if_same(stale.path, old), "removed a lock that replaced the stale one"
assert stale.path.exists()
PY
ok "X: release only removes this run's own lock; a stale takeover checks the inode first"

# ---------------------------------------------------------------- cross-cutting: addresses and redaction claims
python3 - <<'PY' || fail "X: addresses"
import os, re
for name in sorted(os.listdir(".")):
    if not os.path.isfile(name):
        continue
    text = open(name, encoding="utf-8").read()
    for domain in re.findall(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)", text):
        assert domain.endswith(".example"), (name, domain)
PY
ok "X: every email address in the package uses a .example domain"
! grep -qiE "redact" _listing.json README.md DATA_CONTRACT.md SKILL.md || fail "X: a doc claims redaction the script doesn't do"
ok "X: no doc claims phone or email redaction (the script does not redact; it never needs phone numbers)"

# ================================================================ pre-QA round 3 regressions (P1-)
# ---------------------------------------------------------------- P1: a resumed digest keeps its window's end as the boundary
SP1="$TMP/p1_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$SP1" --scheduled >/dev/null 2>&1
# the Friday post failed; Monday's automation resumes it (as of now), posts and confirms it
python3 build_commitments.py --bundle sample_bundle_week1.json "${WD[@]}" --as-of 2026-10-12T09:00:00-07:00 --state "$SP1" --scheduled --resume-pending >/dev/null 2>&1
python3 build_commitments.py --confirm-posted weekly:2026-10-05..2026-10-09 --as-of 2026-10-12T09:01:00-07:00 --state "$SP1" >/dev/null
python3 build_commitments.py --next-window weekly --as-of 2026-10-16T16:00:00-07:00 --state "$SP1" >"$TMP/p1_nw.json"
python3 -c "import json; d=json.load(open('$TMP/p1_nw.json')); assert d['from'] == '2026-10-09' and d['already_posted'] is None, d" \
  || { cat "$TMP/p1_nw.json"; fail "P1: a digest resumed on Monday moved the window past Friday evening, or took Monday's week"; }
ok "P1: resuming Friday's digest on Monday keeps Friday as the next window's start and doesn't use up the new week"

# ---------------------------------------------------------------- P2: meetings in zones behind the run's zone are not lost
SP2="$TMP/p2_state.json"
variant p2_empty sample_bundle_week1.json 'b["scan"] = {"from": "2026-10-05", "to": "2026-10-12", "cadence": "weekly"}; b["meetings"] = []; b["commitments"] = []; b["evidence"] = []'
expect 0 "Skipped: nothing to report" out "P2: a Monday 00:00 London run with nothing to report is recorded" \
  --bundle "$TMP/p2_empty.json" "${WD[@]}" --as-of 2026-10-12T00:00:00+01:00 --state "$SP2" --scheduled
python3 build_commitments.py --next-window weekly --as-of 2026-10-19T00:00:00+01:00 --state "$SP2" >"$TMP/p2_nw.json"
python3 -c "import json; d=json.load(open('$TMP/p2_nw.json')); assert d['from'] == '2026-10-11', d" \
  || { cat "$TMP/p2_nw.json"; fail "P2: a Sunday-evening New York meeting that started after the London run would never be scanned"; }
ok "P2: the next window reaches back to Sunday for a meeting in New York that started after Monday 00:00 London"
window_bundle "$TMP/p2_nw.json" "$TMP/p2_bundle.json"
python3 - "$TMP/p2_bundle.json" <<'PY'
import json, sys
b = json.load(open(sys.argv[1], encoding="utf-8"))
src = json.load(open("sample_bundle_week1.json", encoding="utf-8"))
m = dict(src["meetings"][4], meeting_id="p2-ny", start="2026-10-11T20:00:00-04:00")
b["meetings"] = [m]
b["commitments"] = [dict(next(c for c in src["commitments"] if c["ref"] == "c9"), ref="p2c", meeting_id="p2-ny", quote="I'll send the SOW tomorrow.", due={"kind": "relative", "phrase": "tomorrow"})]
json.dump(b, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)
PY
expect 0 "Fennaroot Dental — security review" out "P2: that meeting is accepted in the next window and its promise is listed" \
  --bundle "$TMP/p2_bundle.json" "${WD[@]}" --as-of 2026-10-19T00:00:00+01:00 --state "$SP2" --scheduled --dry-run
python3 build_commitments.py --next-window weekly --as-of 2026-10-16T16:00:00-07:00 --state "$SR1" >"$TMP/p2_nw2.json"
python3 -c "import json; d=json.load(open('$TMP/p2_nw2.json')); assert d['from'] == '2026-10-09', d" || fail "P2: normal overlap changed"
ok "P2: a Friday 4pm run in the US still overlaps by just that day"

# ---------------------------------------------------------------- P3: a promise repeated after older evidence keeps the item open
SP3="$TMP/p3_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$SP3" --scheduled >/dev/null 2>&1
python3 build_commitments.py --confirm-posted weekly:2026-10-05..2026-10-09 --state "$SP3" >/dev/null
P3EDIT='
src = json.load(open("sample_bundle_week1.json", encoding="utf-8"))
b["meetings"].append(dict(src["meetings"][4], meeting_id="p3-m", start="2026-10-14T11:00:00-07:00", title="Fennaroot Dental — legal sync"))
b["commitments"].append(dict(next(c for c in src["commitments"] if c["ref"] == "c9"), ref="p3c", meeting_id="p3-m", quote="Sorry, that was the old SOW. I will send the revised one by Friday.", due={"kind": "relative", "phrase": "by Friday"}))'
variant p3 sample_bundle_week2.json "import json; $P3EDIT"
expect 0 "re-promised Wed Oct 14" out "P3: evidence dated before a later re-promise does not close the item" \
  --bundle "$TMP/p3.json" "${W2[@]}" --state "$SP3" --scheduled --dry-run
python3 - "$TMP/out" <<'PY' || fail "P3: the re-promised SOW was reported done (its new promise silently dropped)"
import sys
text = open(sys.argv[1], encoding="utf-8").read()
done = text.split("### Done since last run", 1)[1].split("###", 1)[0] if "### Done since last run" in text else ""
assert "Send the revised SOW" not in done, done
PY
ok "P3: the re-promised item stays open (not in Done)"
variant p3_after sample_bundle_week2.json "import json; $P3EDIT
b['evidence'][0]['date'] = '2026-10-15'"
python3 build_commitments.py --bundle "$TMP/p3_after.json" "${W2[@]}" --state "$SP3" --scheduled --dry-run --json >"$TMP/p3.json.out"
python3 -c "import json; d=json.load(open('$TMP/p3.json.out')); assert 'ct-97efb9ee06' in [i['id'] for i in d['groups']['done']], d['groups']" \
  || fail "P3: evidence dated after the re-promise no longer closes the item"
ok "P3: evidence on or after the latest promise still closes it"

# ---------------------------------------------------------------- P4: dismiss then restore in one bundle
SP4="$TMP/p4_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$SP4" >/dev/null
variant p4 sample_bundle_week1.json 'b["user_actions"] = [{"commitment_id": "ct-81931a148b", "action": "dismiss", "date": "2026-10-09"}, {"commitment_id": "ct-81931a148b", "action": "restore", "date": "2026-10-09"}]'
expect 0 "Introduce Marcus to our data engineer" out "P4: dismiss then restore in one bundle leaves the item restored" \
  --bundle "$TMP/p4.json" "${W1[@]}" --state "$SP4" --dry-run
variant p4b sample_bundle_week1.json 'b["user_actions"] = [{"commitment_id": "ct-81931a148b", "action": "restore", "date": "2026-10-09"}, {"commitment_id": "ct-81931a148b", "action": "dismiss", "date": "2026-10-09"}]'
python3 build_commitments.py --bundle "$TMP/p4b.json" "${W1[@]}" --state "$SP4" --dry-run >"$TMP/out"
hasnt "Introduce Marcus to our data engineer" "P4: restore then dismiss leaves it dismissed"

# ---------------------------------------------------------------- P5: a repeat from before an item closed joins it
SP5="$TMP/p5_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$SP5" --scheduled >/dev/null 2>&1
python3 build_commitments.py --confirm-posted weekly:2026-10-05..2026-10-09 --state "$SP5" >/dev/null
variant p5_ev sample_bundle_week2.json 'b["scan"]["cadence"] = "on_request"; b["meetings"] = []; b["commitments"] = []; b["user_actions"] = []; b["evidence"] = b["evidence"][:1]'
python3 build_commitments.py --bundle "$TMP/p5_ev.json" "${WD[@]}" --as-of 2026-10-13T09:00:00-07:00 --state "$SP5" >/dev/null
# a meeting from Fri Oct 9 (read only now) where Dana had said it again, before the Oct 12 email delivered it
P5EDIT='
src = json.load(open("sample_bundle_week1.json", encoding="utf-8"))
b["meetings"].append(dict(src["meetings"][4], meeting_id="p5-m", start="2026-10-09T17:00:00-07:00", title="Fennaroot Dental — legal sync"))
b["commitments"].append(dict(next(c for c in src["commitments"] if c["ref"] == "c9"), ref="p5c", meeting_id="p5-m", quote="The SOW is coming Monday, promise.", due={"kind": "relative", "phrase": "Monday"}))'
variant p5 sample_bundle_week2.json "import json; $P5EDIT"
python3 build_commitments.py --bundle "$TMP/p5.json" "${W2[@]}" --state "$SP5" --scheduled --dry-run >"$TMP/out"
hasnt "promised again; the earlier \`ct-97efb9ee06\`" "P5: a repeat said before the item was delivered is not a new open item"
variant p5_late sample_bundle_week2.json "import json; $P5EDIT
b['meetings'][-1]['start'] = '2026-10-14T17:00:00-07:00'"
python3 build_commitments.py --bundle "$TMP/p5_late.json" "${W2[@]}" --state "$SP5" --scheduled --dry-run >"$TMP/out"
has "promised again; the earlier \`ct-97efb9ee06\` was done" "P5: a promise made after the item was delivered is still a new item"

# ---------------------------------------------------------------- P6: a superseded period is not staged again
SP6="$TMP/p6_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$SP6" --scheduled >/dev/null 2>&1
variant p6_w2 sample_bundle_week2.json 'b["scan"]["from"] = "2026-10-05"'
python3 build_commitments.py --bundle "$TMP/p6_w2.json" "${WD[@]}" --as-of 2026-10-16T16:00:00-07:00 --state "$SP6" --scheduled >/dev/null 2>&1
cp "$SP6" "$TMP/p6_before.json"
expect 2 "weekly:2026-10-05..2026-10-09 was superseded by a later digest" err "P6: resuming a superseded period is refused" \
  --bundle sample_bundle_week1.json "${WD[@]}" --as-of 2026-10-16T17:00:00-07:00 --state "$SP6" --scheduled --resume-pending
cmp -s "$SP6" "$TMP/p6_before.json" || fail "P6: the refused run changed the state"
expect 0 "Confirmed posted: weekly:2026-10-05..2026-10-16" out "P6: the newer pending digest can still be confirmed" \
  --confirm-posted weekly:2026-10-05..2026-10-16 --state "$SP6"

# ---------------------------------------------------------------- P7: a scheduled dry run offers nothing to confirm
python3 build_commitments.py --bundle sample_bundle_week1.json "${W1[@]}" --state "$TMP/p7_state.json" --scheduled --dry-run --json >"$TMP/p7.json"
python3 -c "import json; d=json.load(open('$TMP/p7.json')); assert 'confirm_with' not in d, d.get('confirm_with')" \
  || fail "P7: --dry-run --json offered a confirm_with for a period that was never staged"
ok "P7: --scheduled --dry-run --json has no confirm_with"

# ---------------------------------------------------------------- P8: a racing second trigger in the same slot posts nothing
SP8="$TMP/p8_state.json"
python3 build_commitments.py --next-window weekly --as-of 2026-10-10T00:01:00-07:00 --state "$SP8" >"$TMP/p8_nw_b.json"   # agent B asks first
python3 build_commitments.py --bundle sample_bundle_week1.json "${WD[@]}" --as-of 2026-10-09T23:59:00-07:00 --state "$SP8" --scheduled >/dev/null 2>&1 # agent A stages
window_bundle "$TMP/p8_nw_b.json" "$TMP/p8_b.json"
python3 -c "import json; b=json.load(open('$TMP/p8_b.json')); assert b['scan']['to'] == '2026-10-10', b['scan']" || fail "P8: fixture"
cp "$SP8" "$TMP/p8_before.json"
expect 4 "weekly:2026-10-05..2026-10-09 is still pending" err "P8: B's run in the same week stops while A's digest is pending" \
  --bundle "$TMP/p8_b.json" "${WD[@]}" --as-of 2026-10-10T00:01:00-07:00 --state "$SP8" --scheduled
cmp -s "$SP8" "$TMP/p8_before.json" || fail "P8: B's refused run changed the state"
expect 0 "Confirmed posted: weekly:2026-10-05..2026-10-09" out "P8: A's digest can still be confirmed" \
  --confirm-posted weekly:2026-10-05..2026-10-09 --state "$SP8"

# ---------------------------------------------------------------- P9: Slack link_names can't turn a quote into a mention
variant p9 sample_bundle_week1.json 'b["meetings"][0]["title"] = "@channel heads-up for @here and @dana.ortiz"; b["commitments"][0]["quote"] = "I will loop in @everyone by Wednesday."'
python3 build_commitments.py --bundle "$TMP/p9.json" "${W1[@]}" --format slack >"$TMP/out"
python3 - "$TMP/out" <<'PY' || fail "P9: a bare @mention from bundle text reached the Slack digest"
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
assert not re.search(r"@(channel|here|everyone|dana)", text), re.findall(r".{20}@\w+", text)
assert "＠channel heads-up" in text and "＠everyone" in text
PY
ok "P9: in Slack, @ before a name in bundle text shows as ＠ (no mention even with link_names)"

# ---------------------------------------------------------------- P10: a window with a Gap is never "nothing to report"
SP10="$TMP/p10_state.json"
variant p10_aug sample_bundle_week1.json 'b["scan"] = {"from": "2026-08-24", "to": "2026-08-28", "cadence": "weekly"}; b["meetings"] = []; b["commitments"] = []; b["evidence"] = []'
python3 build_commitments.py --bundle "$TMP/p10_aug.json" "${WD[@]}" --as-of 2026-08-28T16:00:00-07:00 --state "$SP10" --scheduled >/dev/null
# six weeks later on a free plan: the agent starts the window at the 30-day history limit (Step 1), so Aug 28-Sep 8 can't be read
variant p10_oct sample_bundle_week1.json 'b["scan"] = {"from": "2026-09-09", "to": "2026-10-09", "cadence": "weekly"}; b["meetings"] = []; b["commitments"] = []; b["evidence"] = []'
expect 0 "Gap: the window after the last posted digest starts Fri Aug 28" out "P10: an empty window that starts after the last posted one still reports its Gap" \
  --bundle "$TMP/p10_oct.json" "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$SP10" --scheduled
hasnt "nothing to report" "P10: never 'nothing to report' when days were skipped"

# ---------------------------------------------------------------- P11: re-extractions that vary the trim, then the wording
SP11="$TMP/p11_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${W1[@]}" --state "$SP11" >/dev/null
P11BASE='b["scan"]["cadence"] = "on_request"; b["evidence"] = []; c = next(x for x in b["commitments"] if x["ref"] == "c4"); b["commitments"] = [c]; b["meetings"] = [m for m in b["meetings"] if m["meeting_id"] == c["meeting_id"]]'
variant p11a sample_bundle_week1.json "$P11BASE"'; c["quote"] = c["quote"].split(" ", 1)[1]'
variant p11b sample_bundle_week1.json "$P11BASE"'; c["deliverable"] = "Get the integration checklist over to Spokewren"'
python3 build_commitments.py --bundle "$TMP/p11a.json" "${W1[@]}" --state "$SP11" >/dev/null
python3 build_commitments.py --bundle "$TMP/p11b.json" "${W1[@]}" --state "$SP11" >"$TMP/out"
hasnt "Get the integration checklist over" "P11: a re-extraction with the first quote but new wording joins the item after a differently trimmed one"
python3 -c "import json; d=json.load(open('$SP11'))['commitments']; assert len(d) == 8, sorted(x['deliverable'] for x in d.values())" \
  || fail "P11: a third extraction of one promise became a second item"
ok "P11: still one item for that promise"

# ---------------------------------------------------------------- P12: a skipped run says the replies it carried were not applied
# R1's state right after the 16:00 digest was confirmed (SR1 itself has since moved on to week 2)
SP12="$TMP/p12_state.json"
cp "$TMP/r1_before.json" "$SP12"
variant p12 "$TMP/r1_bundle.json" 'b["user_actions"] = [{"commitment_id": "ct-81931a148b", "action": "dismiss", "date": "2026-10-09"}]'
expect 0 "1 reply in this bundle (user_actions) was not applied" out "P12: Skipped: already posted names the replies it did not apply" \
  --bundle "$TMP/p12.json" "${WD[@]}" --as-of 2026-10-09T16:30:00-07:00 --state "$SP12" --scheduled
cmp -s "$SP12" "$TMP/r1_before.json" || fail "P12: the skipped run applied the reply"
ok "P12: the skipped run leaves the state byte-identical"
variant p12b "$TMP/p12.json" 'b["user_actions"].append({"commitment_id": "ct-81931a148b", "action": "reopen", "date": "2026-10-09"})'
expect 0 "2 replies in this bundle (user_actions) were not applied; apply them" out "P12: two skipped replies are counted" \
  --bundle "$TMP/p12b.json" "${WD[@]}" --as-of 2026-10-09T16:30:00-07:00 --state "$SP12" --scheduled

# ---------------------------------------------------------------- P13: two open items with one wording: a repeat joins the newest
SP13="$TMP/p13_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${W1[@]}" --state "$SP13" >/dev/null
P13M='{"meeting_id": "p13-%s", "title": "Fennaroot Dental check-in", "start": "2026-10-%sT10:00:00-07:00", "url": "https://notes.granola.ai/d/sample-p13", "account": "Fennaroot Dental", "attendees": [{"email": "sam@brackenvole.example"}, {"email": "priya@fennaroot.example"}], "notes_status": "ok", "transcript_status": "ok"}'
P13C='{"ref": "r%s", "meeting_id": "p13-%s", "direction": "we_owe", "owner": "Sam Kim", "counterpart": "Fennaroot Dental", "deliverable": "Send pricing for the two new locations", "quote": "Pricing for the new locations, again, by Friday (%s).", "quote_source": "transcript", "due": {"kind": "relative", "phrase": "by Friday"}}'
variant p13a sample_bundle_week1.json "import json
b['scan'] = {'from': '2026-10-12', 'to': '2026-10-12', 'cadence': 'on_request'}; b['meetings'] = [json.loads('''$P13M''' % ('a', '12'))]
b['commitments'] = [json.loads('''$P13C''' % ('a', 'a', 'a'))]; b['evidence'] = []"
python3 build_commitments.py --bundle "$TMP/p13a.json" "${WD[@]}" --as-of 2026-10-12T12:00:00-07:00 --state "$SP13" >/dev/null
variant p13b sample_bundle_week1.json "import json
b['scan'] = {'from': '2026-10-13', 'to': '2026-10-14', 'cadence': 'on_request'}; b['meetings'] = [json.loads('''$P13M''' % ('b', '14'))]
b['commitments'] = [json.loads('''$P13C''' % ('b', 'b', 'b'))]; b['evidence'] = []
b['user_actions'] = [{'commitment_id': 'ct-898a3abf81', 'action': 'reopen', 'date': '2026-10-13'}]"
python3 build_commitments.py --bundle "$TMP/p13b.json" "${WD[@]}" --as-of 2026-10-14T12:00:00-07:00 --state "$SP13" >/dev/null
python3 - "$SP13" <<'PY' || fail "P13: the repeat joined the older, reopened item instead of the newer one"
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))["commitments"]
old = d["ct-898a3abf81"]
new = [r for c, r in d.items() if r.get("earlier") == "ct-898a3abf81"]
assert len(new) == 1, new
assert [m["meeting_id"] for m in new[0]["mentions"]] == ["p13-a", "p13-b"], new[0]["mentions"]
assert all(m["meeting_id"] not in ("p13-a", "p13-b") for m in old["mentions"]), old["mentions"]
PY
ok "P13: a repeat joins the newest open item with that wording"

# ---------------------------------------------------------------- P14: a repeat on the day you dismissed an item is a new promise
SP14="$TMP/p14_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${W1[@]}" --state "$SP14" >/dev/null
variant p14a sample_bundle_week1.json 'b["scan"] = {"from": "2026-10-12", "to": "2026-10-12", "cadence": "on_request"}; b["meetings"] = []; b["commitments"] = []; b["evidence"] = []
b["user_actions"] = [{"commitment_id": "ct-81931a148b", "action": "dismiss", "date": "2026-10-12"}]'
python3 build_commitments.py --bundle "$TMP/p14a.json" "${WD[@]}" --as-of 2026-10-12T10:00:00-07:00 --state "$SP14" >/dev/null
variant p14b sample_bundle_week1.json '
src = dict(b)
m = dict(next(x for x in src["meetings"] if x["meeting_id"] == "sample-3c4d"), meeting_id="p14-m", start="2026-10-12T15:00:00-07:00", transcript_status="ok")
c = dict(next(x for x in src["commitments"] if x["ref"] == "c6"), ref="p14c", meeting_id="p14-m", quote="I will introduce Marcus to our data engineer this week, for real.", quote_source="transcript")
b["scan"] = {"from": "2026-10-12", "to": "2026-10-13", "cadence": "on_request"}; b["meetings"] = [m]; b["commitments"] = [c]; b["evidence"] = []'
expect 0 'promised again; the earlier `ct-81931a148b` was dismissed' out "P14: a promise made after you dismissed the item that morning is a new item" \
  --bundle "$TMP/p14b.json" "${WD[@]}" --as-of 2026-10-13T09:00:00-07:00 --state "$SP14" --dry-run

# ---------------------------------------------------------------- P15: a done item dismissed later still closed when it was done
SP15="$TMP/p15_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${W1[@]}" --state "$SP15" >/dev/null
variant p15a sample_bundle_week1.json 'b["scan"] = {"from": "2026-10-14", "to": "2026-10-14", "cadence": "on_request"}; b["meetings"] = []; b["commitments"] = []; b["evidence"] = []
b["user_actions"] = [{"commitment_id": "ct-898a3abf81", "action": "dismiss", "date": "2026-10-14"}]'
python3 build_commitments.py --bundle "$TMP/p15a.json" "${WD[@]}" --as-of 2026-10-14T10:00:00-07:00 --state "$SP15" >/dev/null
variant p15b sample_bundle_week1.json '
m = dict(b["meetings"][0], meeting_id="p15-m", start="2026-10-12T10:00:00-07:00")
c = dict(next(x for x in b["commitments"] if x["ref"] == "c2"), ref="p15c", meeting_id="p15-m", quote="I will send pricing for the two new locations again, updated, by Friday.")
b["scan"] = {"from": "2026-10-12", "to": "2026-10-15", "cadence": "on_request"}; b["meetings"] = [m]; b["commitments"] = [c]; b["evidence"] = []'
expect 0 'promised again; the earlier `ct-898a3abf81` was dismissed' out "P15: a promise made after the item was done (Oct 8) is new even though it was dismissed later (Oct 14)" \
  --bundle "$TMP/p15b.json" "${WD[@]}" --as-of 2026-10-15T09:00:00-07:00 --state "$SP15" --dry-run

# ---------------------------------------------------------------- P16: a re-extraction from a meeting the closed item cites joins it
SP16="$TMP/p16_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${W1[@]}" --state "$SP16" >/dev/null
variant p16a sample_bundle_week1.json 'b["scan"] = {"from": "2026-10-06", "to": "2026-10-06", "cadence": "on_request"}; b["meetings"] = []; b["commitments"] = []; b["evidence"] = []
b["user_actions"] = [{"commitment_id": "ct-81931a148b", "action": "dismiss", "date": "2026-10-06"}]'
python3 build_commitments.py --bundle "$TMP/p16a.json" "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$SP16" >/dev/null
variant p16b sample_bundle_week1.json 'c = next(x for x in b["commitments"] if x["ref"] == "c6"); c["quote"] = "will intro Marcus to the data engineer"
b["scan"]["cadence"] = "on_request"; b["commitments"] = [c]; b["meetings"] = [m for m in b["meetings"] if m["meeting_id"] == c["meeting_id"]]; b["evidence"] = []'
python3 build_commitments.py --bundle "$TMP/p16b.json" "${W1[@]}" --state "$SP16" --dry-run >"$TMP/out"
hasnt "Introduce Marcus to our data engineer" "P16: re-reading the meeting of a dismissed item (quote trimmed differently) doesn't revive it as a new item"

# ================================================================ pre-QA round 4 regressions (Q1-)
# q_bundle NAME FROM TO CADENCE MEETINGS_JSON COMMITMENTS_JSON ACTIONS_JSON : a small bundle with the week-1 sources
q_bundle() {
  python3 - "$TMP/$1.json" "$2" "$3" "$4" "$5" "$6" "$7" <<'PY'
import json, sys
b = json.load(open("sample_bundle_week1.json", encoding="utf-8"))
b["scan"] = {"from": sys.argv[2], "to": sys.argv[3], "cadence": sys.argv[4]}
b["meetings"], b["commitments"], b["user_actions"], b["evidence"] = json.loads(sys.argv[5]), json.loads(sys.argv[6]), json.loads(sys.argv[7]), []
json.dump(b, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)
PY
}
QM='{"meeting_id": "%s", "title": "Spokewren Cycles check-in", "start": "%s", "url": "https://notes.granola.ai/d/sample-q", "account": "Spokewren Cycles", "attendees": [{"email": "dana@brackenvole.example"}, {"email": "marcus@spokewren.example"}], "notes_status": "ok", "transcript_status": "ok"}'
QC='{"ref": "%s", "meeting_id": "%s", "direction": "we_owe", "owner": "Dana Ortiz", "counterpart": "Spokewren Cycles", "deliverable": "Introduce Marcus to our data engineer", "quote": "%s", "quote_source": "transcript", "due": {"kind": "none"}}'

# ---------------------------------------------------------------- Q1: a dismissal sticks while the promise was recently repeated
# The item was first made in October and promised again in February; dismissing it in February must not forget it
# the same day (pruning counts from its latest mention, evidence or dismissal, not from the first meeting).
SQ1="$TMP/q1_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${W1[@]}" --state "$SQ1" >/dev/null
q_bundle q1a 2027-02-10 2027-02-10 on_request "[$(printf "$QM" q1-m 2027-02-10T10:00:00-08:00)]" "[$(printf "$QC" q1c q1-m "Sorry, that intro slipped; I will set it up.")]" '[]'
python3 build_commitments.py --bundle "$TMP/q1a.json" "${WD[@]}" --as-of 2027-02-10T12:00:00-08:00 --state "$SQ1" >"$TMP/out"
hasnt "promised again" "Q1: the February repeat joins the open October item"
q_bundle q1b 2027-02-12 2027-02-12 on_request '[]' '[]' '[{"commitment_id": "ct-81931a148b", "action": "dismiss", "date": "2027-02-12"}]'
python3 build_commitments.py --bundle "$TMP/q1b.json" "${WD[@]}" --as-of 2027-02-12T12:00:00-08:00 --state "$SQ1" >/dev/null
python3 -c "import json,sys; d=json.load(open(sys.argv[1]))['commitments']; assert 'ct-81931a148b' in d and d['ct-81931a148b']['dismissed'], sorted(d)" "$SQ1" \
  || fail "Q1: a dismissed item repeated two days earlier was pruned the moment it was dismissed"
ok "Q1: the dismissed item stays in the state file (its latest mention is recent)"
q_bundle q1c 2027-02-01 2027-02-14 on_request "[$(printf "$QM" q1-m 2027-02-10T10:00:00-08:00)]" "[$(printf "$QC" q1c q1-m "Sorry, that intro slipped; I will set it up.")]" '[]'
python3 build_commitments.py --bundle "$TMP/q1c.json" "${WD[@]}" --as-of 2027-02-14T12:00:00-08:00 --state "$SQ1" >"$TMP/out"
hasnt "Introduce Marcus to our data engineer" "Q1: re-reading the February meeting doesn't bring the dismissed promise back as a new item"

# ---------------------------------------------------------------- Q2: a resumed digest covers only what was listed when it was staged
# Staged Fri 00:30 in Tokyo for Mon-Fri; the post failed; resumed and confirmed on Monday with a new --as-of. A Thursday
# 10am Los Angeles meeting (Fri 2am in Tokyo) started after the staged run, so it wasn't in that digest: the next
# window must still include Thursday.
SQ2="$TMP/q2_state.json"
q_bundle q2a 2026-10-12 2026-10-16 weekly "[$(printf "$QM" q2-m 2026-10-13T10:00:00+09:00)]" "[$(printf "$QC" q2c q2-m "I will set up that intro.")]" '[]'
python3 build_commitments.py --bundle "$TMP/q2a.json" "${WD[@]}" --as-of 2026-10-16T00:30:00+09:00 --state "$SQ2" --scheduled >/dev/null 2>&1
python3 build_commitments.py --next-window weekly --as-of 2026-10-16T00:31:00+09:00 --state "$SQ2" >/dev/null
python3 build_commitments.py --bundle "$TMP/q2a.json" "${WD[@]}" --as-of 2026-10-19T09:00:00+09:00 --state "$SQ2" --scheduled --resume-pending >/dev/null 2>&1
python3 build_commitments.py --confirm-posted weekly:2026-10-12..2026-10-16 --as-of 2026-10-19T09:01:00+09:00 --state "$SQ2" >/dev/null
python3 build_commitments.py --next-window weekly --as-of 2026-10-23T16:00:00+09:00 --state "$SQ2" >"$TMP/q2_nw.json"
python3 -c "import json; d=json.load(open('$TMP/q2_nw.json')); assert d['from'] <= '2026-10-15', d" \
  || { cat "$TMP/q2_nw.json"; fail "Q2: a Thursday-morning Los Angeles meeting that started after the staged run would never be scanned"; }
ok "Q2: after a resume with a later --as-of, the next window still reaches back as far as the staged run allowed"

# ---------------------------------------------------------------- Q3: a scheduled window can't end after the run
# A daily window ending tomorrow would use up tomorrow's slot, so tomorrow's run would post nothing.
SQ3="$TMP/q3_state.json"
q_bundle q3a 2026-10-12 2026-10-14 daily '[]' '[]' '[]'
expect 2 "is after --as-of" err "Q3: a scheduled window that ends after --as-of is refused" \
  --bundle "$TMP/q3a.json" "${WD[@]}" --as-of 2026-10-13T08:00:00-04:00 --state "$SQ3" --scheduled
[[ ! -s "$TMP/out" && ! -e "$SQ3" ]] || fail "Q3: the refused run printed or recorded something"
ok "Q3: nothing printed or recorded"

# ---------------------------------------------------------------- Q4: a re-extraction joins the open item that already cites its meeting
# After P13 there are two open items with one wording: the reopened ct-898a3abf81 (from Mon Oct 5) and the newer promise.
# Re-reading Monday's meeting with the quote trimmed differently belongs to the reopened item, not the newest one.
cp "$SP13" "$TMP/q4_state.json"
variant q4 sample_bundle_week1.json 'b["scan"]["cadence"] = "on_request"; b["evidence"] = []; c = next(x for x in b["commitments"] if x["ref"] == "c2")
c["quote"] = "get you pricing for the two new locations by Friday."; b["commitments"] = [c]; b["meetings"] = [m for m in b["meetings"] if m["meeting_id"] == "sample-1a2b"]'
python3 build_commitments.py --bundle "$TMP/q4.json" "${WD[@]}" --as-of 2026-10-14T13:00:00-07:00 --state "$TMP/q4_state.json" >/dev/null
python3 - "$TMP/q4_state.json" <<'PY' || fail "Q4: Monday's promise is now tracked by two open items"
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))["commitments"]
holders = sorted(c for c, r in d.items() if any(m["meeting_id"] == "sample-1a2b" for m in r["mentions"])
                 and "pricing" in r["deliverable"])
assert holders == ["ct-898a3abf81"], holders
PY
ok "Q4: the re-extraction stays with the item that already cites that meeting"

# ---------------------------------------------------------------- Q5: a run never prunes an item its own bundle refers to
# An open item last active in January gets (old, weak) evidence in an August scheduled run whose post fails; the same
# command with --resume-pending must still work, so that run must not drop the item it was given evidence for.
SQ5="$TMP/q5_state.json"
q_bundle q5a 2026-01-05 2026-01-05 on_request "[$(printf "$QM" q5-m 2026-01-05T10:00:00-08:00)]" "[$(printf "$QC" q5c q5-m "I will set up that intro.")]" '[]'
python3 build_commitments.py --bundle "$TMP/q5a.json" "${WD[@]}" --as-of 2026-01-05T12:00:00-08:00 --state "$SQ5" >/dev/null
Q5ID=$(python3 -c "import json,sys; print(list(json.load(open(sys.argv[1]))['commitments'])[0])" "$SQ5")
variant q5b "$TMP/q5a.json" "b['scan'] = {'from': '2026-07-27', 'to': '2026-07-31', 'cadence': 'weekly'}; b['meetings'] = []; b['commitments'] = []
b['evidence'] = [{'commitment_id': '$Q5ID', 'kind': 'sent_email', 'strength': 'weak', 'date': '2026-01-06', 'url': 'https://mail.google.com/mail/u/0/#sent/sample-q5', 'summary': 'Email to Marcus', 'why': 'no intro in it'}]"
python3 build_commitments.py --bundle "$TMP/q5b.json" "${WD[@]}" --as-of 2026-07-31T16:00:00-07:00 --state "$SQ5" --scheduled >/dev/null 2>&1
expect 0 "Introduce Marcus to our data engineer" out "Q5: --resume-pending with the same bundle still prints the digest" \
  --bundle "$TMP/q5b.json" "${WD[@]}" --as-of 2026-07-31T16:00:00-07:00 --state "$SQ5" --scheduled --resume-pending

# ---------------------------------------------------------------- Q6: "bring back ct-…" sticks for an item with no recent activity
SQ6="$TMP/q6_state.json"
python3 build_commitments.py --bundle "$TMP/q5a.json" "${WD[@]}" --as-of 2026-01-05T12:00:00-08:00 --state "$SQ6" >/dev/null
q_bundle q6a 2026-01-09 2026-01-09 on_request '[]' '[]' "[{\"commitment_id\": \"$Q5ID\", \"action\": \"dismiss\", \"date\": \"2026-01-09\"}]"
python3 build_commitments.py --bundle "$TMP/q6a.json" "${WD[@]}" --as-of 2026-01-09T12:00:00-08:00 --state "$SQ6" >/dev/null
q_bundle q6b 2026-07-31 2026-07-31 on_request '[]' '[]' "[{\"commitment_id\": \"$Q5ID\", \"action\": \"restore\", \"date\": \"2026-07-31\"}]"
expect 0 "Introduce Marcus to our data engineer" out "Q6: the restored item is listed" \
  --bundle "$TMP/q6b.json" "${WD[@]}" --as-of 2026-07-31T12:00:00-07:00 --state "$SQ6"
q_bundle q6c 2026-08-03 2026-08-03 on_request '[]' '[]' '[]'
expect 0 "Introduce Marcus to our data engineer" out "Q6: and still listed by the next run (the restore counts as activity)" \
  --bundle "$TMP/q6c.json" "${WD[@]}" --as-of 2026-08-03T12:00:00-07:00 --state "$SQ6"

# ---------------------------------------------------------------- Q7: bare URLs and domains in bundle text can't be auto-linked
# Meeting titles can come from outside invitees, so a title or quote with a bare URL must not become a clickable link
# in the posted digest (markdown escapes :// and dots, which renders the same; Slack shows look-alikes).
variant q7 sample_bundle_week1.json 'b["meetings"][0]["title"] = "Pay at https://phish.example/pay or www.phish.example"
b["commitments"][0]["quote"] = "Details at phish.example and http://phish.example:8080/x, mail x@phish.example."
b["commitments"][0]["deliverable"] = "Send it to phish.example"'
for fmt in markdown slack; do
  python3 build_commitments.py --bundle "$TMP/q7.json" "${W1[@]}" --format $fmt >"$TMP/q7_$fmt.out"
done
python3 - "$TMP" <<'PY' || fail "Q7: a bare URL or domain from bundle text could be auto-linked"
import re, sys
md = open(sys.argv[1] + "/q7_markdown.out", encoding="utf-8").read()
sl = open(sys.argv[1] + "/q7_slack.out", encoding="utf-8").read()
assert "phish" in md and "phish" in sl
for line in md.split("\n"):
    bare = re.sub(r"\]\((https://[^)]*)\)", "]", re.sub(r"\\.", "", line))
    assert not re.search(r"://|www\.|phish\.example", bare, re.I), line
for line in sl.split("\n"):
    bare = re.sub(r"<https://[^|<>]+\|", "<", line)
    assert not re.search(r"://|www\.|phish\.example", bare, re.I), line
PY
ok "Q7: bare URLs, www. and domains in titles, quotes and deliverables are not linkable in markdown or Slack"

# ---------------------------------------------------------------- Q8: an older period can't supersede a newer pending digest
# Week 2's digest is staged (pending); a late run for week 1 must not quietly supersede it (it would stop week 2's
# digest from ever being confirmed or resumed): it stops with exit 4 and changes nothing.
SQ8="$TMP/q8_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$SQ8" --scheduled >/dev/null 2>&1
python3 build_commitments.py --confirm-posted weekly:2026-10-05..2026-10-09 --state "$SQ8" >/dev/null
python3 build_commitments.py --bundle sample_bundle_week2.json "${WD[@]}" --as-of 2026-10-16T16:00:00-07:00 --state "$SQ8" --scheduled >/dev/null 2>&1
variant q8_old sample_bundle_week1.json 'b["scan"] = {"from": "2026-10-01", "to": "2026-10-02", "cadence": "weekly"}; b["meetings"] = []; b["commitments"] = []; b["evidence"] = []'
cp "$SQ8" "$TMP/q8_before.json"
expect 4 "weekly:2026-10-09..2026-10-16 is still pending" err "Q8: a run for an earlier week stops while a later week's digest is pending" \
  --bundle "$TMP/q8_old.json" "${WD[@]}" --as-of 2026-10-16T16:05:00-07:00 --state "$SQ8" --scheduled
cmp -s "$SQ8" "$TMP/q8_before.json" || fail "Q8: the refused run changed the state"
ok "Q8: the state is unchanged and week 2's digest can still be confirmed"

# ---------------------------------------------------------------- QA round 4: B1 math/highlight, B2 docs, C1-C3
python3 - <<'PY' || fail "B1: markdown escapes \$ and ="
import importlib.util, re
spec = importlib.util.spec_from_file_location("bc", "build_commitments.py"); bc = importlib.util.module_from_spec(spec); spec.loader.exec_module(bc)
s = bc.FORMATS["markdown"].text("cost $5 to $9 and hi ==hl== a=b")
assert s == "cost \\$5 to \\$9 and hi \\=\\=hl\\=\\= a\\=b", s
bare = re.sub(r"\\.", "", s)
assert "$" not in bare and "=" not in bare, s
sl = bc.FORMATS["slack"].text("cost $5 to $9 ==hl==")
assert sl == "cost $5 to $9 ==hl==", sl  # Slack mrkdwn has no math or highlight syntax: left as typed
PY
ok "B1: markdown escapes \$ (math) and = (==highlight==) in values; Slack leaves them as typed"
variant b1_math sample_bundle_week1.json '
b["meetings"][0]["title"] = "Pricing: cost $5 to $9 and ==hi=="
b["commitments"][0]["quote"] = "It will cost $51,000 | $72,000 ==tops=="
b["commitments"][0]["deliverable"] = "Send the $5 to $9 ==quote=="
b["meetings"][0]["url"] = "javascript:alert(1)"
b["evidence"][0]["url"] = "data:text/html,x"
'
expect 0 "Commitments" out "B1: a bundle full of \$ and == renders" --bundle "$TMP/b1_math.json" "${W1[@]}"
python3 - "$TMP/out" <<'PY' || fail "B1: unescaped \$ or == in the markdown digest"
import re, sys
text = open(sys.argv[1], encoding="utf-8").read()
assert "Links dropped" in text
for line in text.split("\n"):
    bare = re.sub(r"`[^`]*`", "", re.sub(r"\\.", "", line))  # drop escapes, then the script's own code spans
    assert "$" not in bare and "==" not in bare, line
PY
ok "B1: no line of the markdown digest (incl. the Links dropped paths) can open math or a highlight"

# B2 (docs): the bundle carries no meeting text, so the docs must not claim the script checks a quote's wording
python3 - <<'PY' || fail "B2: docs claim the script verifies quotes"
import re
docs = {f: open(f, encoding="utf-8").read() for f in ["SKILL.md", "README.md", "DATA_CONTRACT.md", "NOTICE.md", "_listing.json"]}
for f, t in docs.items():
    flat = " ".join(t.split())
    for bad in [r"script (requires|checks|verifies|validates)[^.]{0,40}verbatim", r"verif\w+ (that )?(the )?quotes? (are|is )?verbatim",
                r"checks? (the )?quotes? against"]:
        assert not re.search(bad, flat, re.I), (f, bad)
for f in ["SKILL.md", "DATA_CONTRACT.md"]:
    assert "doesn't check the wording" in " ".join(docs[f].split()), f
PY
ok "B2: docs say the agent copies quotes and the script doesn't check their wording (no meeting text in the bundle)"

# C1: a meeting whose notes read failed (a transient error) and that wasn't read another way makes the run Incomplete
SC1="$TMP/r4c1_state.json"
variant r4c1 sample_bundle_week1.json '
m = [x for x in b["meetings"] if x["meeting_id"] == "sample-7a8b"][0]
m["notes_status"] = "unavailable"; m["transcript_status"] = "not_requested"
b["commitments"] = [c for c in b["commitments"] if c["meeting_id"] != "sample-7a8b"]
'
expect 5 "1 meeting could not be read" out "C1: an unread meeting (notes unavailable) makes a scheduled run Incomplete (exit 5)" \
  --bundle "$TMP/r4c1.json" "${W1[@]}" --state "$SC1" --scheduled
has "Notes unavailable:" "C1: Coverage names the unread meeting"
python3 -c "import json,sys; d=json.load(open('$SC1')); sys.exit(0 if [r['status'] for r in d['runs']] == ['incomplete'] else 1)" \
  || fail "C1: a period with an unread meeting was staged"
ok "C1: nothing is staged, so the next window starts before the unread meeting and reads it again"
expect 0 "the period stays open" out "C1/C4: confirming the posted Incomplete digest records what it announced but leaves the period open" --confirm-posted weekly:2026-10-05..2026-10-09 --state "$SC1"
python3 build_commitments.py --next-window weekly --as-of 2026-10-09T16:30:00-07:00 --state "$SC1" >"$TMP/r4c1_nw.json"
python3 -c "import json; d=json.load(open('$TMP/r4c1_nw.json')); assert d['from'] <= '2026-10-08' and d['last_posted'] is None, d" \
  || fail "C1: next window skips the unread meeting"
ok "C1: --next-window still covers the unread meeting's day"
expect 0 "Commitments — Mon Oct 5 to Fri Oct 9, 2026" out "C1: once the meeting reads, the same period gets its digest" \
  --bundle sample_bundle_week1.json --config commitments.json --as-of 2026-10-09T16:30:00-07:00 --internal-domain brackenvole.example \
  --state "$SC1" --scheduled
has "Send the revised SOW" "C1: the re-read meeting's commitment is in the digest"
variant r4c1_denied sample_bundle_week1.json '
m = [x for x in b["meetings"] if x["meeting_id"] == "sample-7a8b"][0]
m["notes_status"] = "denied"; m["transcript_status"] = "denied"
b["commitments"] = [c for c in b["commitments"] if c["meeting_id"] != "sample-7a8b"]
'
expect 0 "Notes denied:" out "C1: a denied meeting (permission, not transient) is listed in Coverage, not Incomplete" \
  --bundle "$TMP/r4c1_denied.json" "${W1[@]}" --state "$TMP/r4c1_denied_state.json" --scheduled --dry-run
variant r4c1_tx sample_bundle_week1.json '
m = [x for x in b["meetings"] if x["meeting_id"] == "sample-7a8b"][0]
m["notes_status"] = "unavailable"; m["transcript_status"] = "ok"
for c in b["commitments"]:
    if c["meeting_id"] == "sample-7a8b": c["quote_source"] = "transcript"
'
expect 0 "read from the transcript only" out "C1: notes unavailable but transcript read is not Incomplete" \
  --bundle "$TMP/r4c1_tx.json" "${W1[@]}" --state "$TMP/r4c1_tx_state.json" --scheduled --dry-run
expect 0 "Incomplete: 1 meeting could not be read" out "C1: an on-request run says Incomplete too (exit 0)" --bundle "$TMP/r4c1.json" "${W1[@]}"

# C2: a stale scheduled run (an --as-of earlier than the last posted run) is refused
SC2="$TMP/r4c2_state.json"
python3 build_commitments.py --bundle sample_bundle_week1.json "${W1[@]}" --state "$SC2" --scheduled >/dev/null
python3 build_commitments.py --confirm-posted weekly:2026-10-05..2026-10-09 --state "$SC2" >/dev/null
variant r4c2_old sample_bundle_week1.json 'b["scan"] = {"from": "2026-09-28", "to": "2026-10-02", "cadence": "weekly"}; b["meetings"] = []; b["commitments"] = []; b["evidence"] = []'
cp "$SC2" "$TMP/r4c2_before.json"
expect 2 "is earlier than the last posted run" err "C2: a scheduled run with an --as-of before the last posted run is refused" \
  --bundle "$TMP/r4c2_old.json" "${WD[@]}" --as-of 2026-10-02T16:00:00-07:00 --state "$SC2" --scheduled
cmp -s "$SC2" "$TMP/r4c2_before.json" || fail "C2: the refused stale run changed the state"
ok "C2: the refused stale run stages nothing and leaves the state unchanged"
expect 0 "Commitments" out "C2: an on-request run for an older window still works" \
  --bundle "$TMP/r4c2_old.json" "${WD[@]}" --as-of 2026-10-02T16:00:00-07:00 --state "$SC2"

# C3: a Gap reaching beyond the plan's note history says those days can't be recovered
SC3="$TMP/r4c3_state.json"
python3 build_commitments.py --bundle "$TMP/p10_aug.json" "${WD[@]}" --as-of 2026-08-28T16:00:00-07:00 --state "$SC3" --scheduled >/dev/null
python3 build_commitments.py --confirm-posted weekly:2026-08-24..2026-08-28 --state "$SC3" >/dev/null
expect 0 "can't be recovered" out "C3: a Gap entirely beyond the 30-day history says it can't be recovered" \
  --bundle "$TMP/p10_oct.json" "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$SC3" --scheduled --dry-run
has "meetings from Fri Aug 28 to Tue Sep 8, 2026 were not scanned" "C3: the Gap still names the unscanned days"
hasnt "window on Fri Aug 28" "C3: no advice to start a window the plan can't read (it would exit 5)"
variant r4c3_part sample_bundle_week1.json 'b["scan"] = {"from": "2026-09-20", "to": "2026-10-09", "cadence": "weekly"}; b["meetings"] = []; b["commitments"] = []; b["evidence"] = []'
expect 0 "start the window on Wed Sep 9 to scan the rest" out "C3: a partly recoverable Gap suggests starting at the history limit" \
  --bundle "$TMP/r4c3_part.json" "${WD[@]}" --as-of 2026-10-09T16:00:00-07:00 --state "$SC3" --scheduled --dry-run
has "can't be recovered" "C3: and says the older days can't be recovered"
hasnt "window on Fri Aug 28" "C3: never the unreadable start"

# ---------------------------------------------------------------- QA round 5: C4 bounded retry for unreadable meetings
# c4_bundle NAME FROM TO CADENCE MODE : the week-1 sample in a wider window. The Thu Oct 8 security review (sample-7a8b)
# is unreadable (stuck), readable (ok), missing from the bundle (missing), or only meeting re-read from an earlier
# window (reread: no other meetings, the review re-read with reread: true). Its promise is a distinct item here.
c4_bundle() {
  python3 - "$TMP/$1.json" "$2" "$3" "$4" "$5" <<'PY'
import json, sys
b = json.load(open("sample_bundle_week1.json", encoding="utf-8"))
out, frm, to, cadence, mode = sys.argv[1:]
b["scan"] = {"from": frm, "to": to, "cadence": cadence}
for c in b["commitments"]:
    if c["meeting_id"] == "sample-7a8b":
        c["deliverable"] = "Send the pen test report"; c["quote"] = "I'll send the pen test report by Monday."
m = [x for x in b["meetings"] if x["meeting_id"] == "sample-7a8b"][0]
if mode in ("stuck", "missing"):
    b["commitments"] = [c for c in b["commitments"] if c["meeting_id"] != "sample-7a8b"]
    b["evidence"] = [e for e in b["evidence"] if e.get("ref") in {c["ref"] for c in b["commitments"]} or e.get("commitment_id")]
if mode == "stuck":
    m["notes_status"] = "unavailable"; m["transcript_status"] = "not_requested"
if mode == "missing":
    b["meetings"] = [x for x in b["meetings"] if x["meeting_id"] != "sample-7a8b"]
if mode == "reread":
    m["reread"] = True
    b["meetings"] = [m]
    b["commitments"] = [c for c in b["commitments"] if c["meeting_id"] == "sample-7a8b"]
    b["evidence"], b["user_actions"] = [], []
b["meetings"] = [x for x in b["meetings"] if x.get("reread") or frm <= x["start"][:10] <= to]
keep = {x["meeting_id"] for x in b["meetings"]}
b["commitments"] = [c for c in b["commitments"] if c["meeting_id"] in keep]
refs = {c["ref"] for c in b["commitments"]}
b["evidence"] = [e for e in b["evidence"] if e.get("commitment_id") or e.get("ref") in refs]
json.dump(b, open(out, "w", encoding="utf-8"), ensure_ascii=False)
PY
}
# jq-free JSON checks on $TMP/out:  jcheck NAME PYTHON_EXPR   (d = the --json result)
jcheck() {
  python3 -c "import json,sys; d=json.load(open('$TMP/out', encoding='utf-8')); sys.exit(0 if ($2) else 1)" \
    || { head -c 3000 "$TMP/out"; fail "$1"; }
  ok "$1"
}
newids() { python3 -c "import json; d=json.load(open('$TMP/out', encoding='utf-8')); print(' '.join(sorted(i['id'] for g in d['groups'].values() for i in g if i['new'])))"; }
C4CFG="$TMP/c4_config.json"
python3 -c "import json; c=json.load(open('commitments.json')); c['unreadable_retry_days']=30; json.dump(c, open('$C4CFG','w'))"

# 1. Unreadable for one run, then it recovers (weekly): run 1 Incomplete and posted once (and confirmed); run 2 reads it.
SR="$TMP/c4_recover.json"
c4_bundle c4r1 2026-10-05 2026-10-09 weekly stuck
expect 5 '"status": "incomplete"' out "C4: a meeting whose read failed makes the first scheduled run Incomplete" \
  --bundle "$TMP/c4r1.json" "${W1[@]}" --state "$SR" --scheduled --json
jcheck "C4: the Incomplete run reports attempt 1 of the retry budget" \
  "[(x['meeting_id'], x['attempts']) for x in d['coverage']['retrying']] == [('sample-7a8b', 1)] and not d['coverage']['not_covered']"
jcheck "C4: an Incomplete scheduled run offers --confirm-posted for the digest it printed" \
  "d.get('confirm_with') == '--confirm-posted weekly:2026-10-05..2026-10-09'"
N1="$(newids)"
[[ -n "$N1" ]] || fail "C4: the first Incomplete digest flags its items new"
expect 0 "Recorded the Incomplete digest" out "C4: confirming the posted Incomplete digest records the items it announced" \
  --confirm-posted weekly:2026-10-05..2026-10-09 --as-of 2026-10-09T16:01:00-07:00 --state "$SR"
python3 build_commitments.py --next-window weekly --as-of 2026-10-16T16:00:00-07:00 --state "$SR" >"$TMP/out"
jcheck "C4: the confirmed Incomplete period is still not posted, so the next window starts before the unread meeting" \
  "d['from'] == '2026-10-05' and d['last_posted'] is None and d['unconfirmed_incomplete'] == []"
c4_bundle c4r2 2026-10-05 2026-10-16 weekly ok
expect 0 '"status": "reported"' out "C4: once the meeting reads, the next run stages its digest" \
  --bundle "$TMP/c4r2.json" "${W2[@]}" --state "$SR" --scheduled --json
jcheck "C4: items the Incomplete digest announced are not flagged new again" \
  "not (set('$N1'.split()) & {i['id'] for g in d['groups'].values() for i in g if i['new']})"
jcheck "C4: the recovered meeting's promise is new" \
  "[i['new'] for g in d['groups'].values() for i in g if i['deliverable'] == 'Send the pen test report'] == [True]"
python3 -c "import json,sys; d=json.load(open('$SR')); sys.exit(0 if d['unread_meetings'] == {} else 1)" || fail "C4: recovered meeting still tracked as unread"
ok "C4: a meeting that read again is no longer tracked as unread"

# 2. The same item is never re-flagged new across Incomplete runs (weekly, two Incomplete runs in a row).
SN="$TMP/c4_noreflag.json"
python3 build_commitments.py --bundle "$TMP/c4r1.json" "${W1[@]}" --state "$SN" --config "$C4CFG" --scheduled --json >"$TMP/out" || true
N1="$(newids)"
python3 build_commitments.py --confirm-posted weekly:2026-10-05..2026-10-09 --state "$SN" >/dev/null
c4_bundle c4n2 2026-10-05 2026-10-16 weekly stuck
expect 5 '"status": "incomplete"' out "C4: still unreadable a week later, inside the retry budget: Incomplete again" \
  --bundle "$TMP/c4n2.json" "${W2[@]}" --config "$C4CFG" --state "$SN" --scheduled --json
jcheck "C4: the second Incomplete digest doesn't flag the first one's items new again" \
  "not (set('$N1'.split()) & {i['id'] for g in d['groups'].values() for i in g if i['new']})"
grep -q "(new)\|· new\|\*\*new\*\*" <(python3 -c "import json; print(json.load(open('$TMP/out'))['markdown'])") \
  && fail "C4: the second Incomplete digest still marks items new" || ok "C4: no item in the second Incomplete digest is marked new"
jcheck "C4: the retry counts a second attempt" "[x['attempts'] for x in d['coverage']['retrying']] == [2]"

# 3. A second trigger in the same week is not a second attempt
SS="$TMP/c4_slot.json"
python3 build_commitments.py --bundle "$TMP/c4r1.json" "${W1[@]}" --state "$SS" --scheduled >/dev/null 2>&1 || true
expect 5 '"status": "incomplete"' out "C4: the schedule firing again the same week stays Incomplete" \
  --bundle "$TMP/c4r1.json" --config commitments.json --as-of 2026-10-09T16:30:00-07:00 --internal-domain brackenvole.example \
  --state "$SS" --scheduled --json
jcheck "C4: a second trigger in the same week doesn't use up a retry" "[x['attempts'] for x in d['coverage']['retrying']] == [1]"

# 4. Unreadable for 3+ weeks: gives up after the retry budget, the digest posts with a Coverage line, later runs are normal
SG="$TMP/c4_giveup.json"
python3 build_commitments.py --bundle "$TMP/c4r1.json" "${W1[@]}" --state "$SG" --scheduled --json >"$TMP/out" || true
N1="$(newids)"
python3 build_commitments.py --confirm-posted weekly:2026-10-05..2026-10-09 --state "$SG" >/dev/null
expect 0 '"status": "reported"' out "C4: a week later (past unreadable_retry_days) the run gives up on the meeting and stages the digest" \
  --bundle "$TMP/c4n2.json" "${W2[@]}" --state "$SG" --scheduled --json
jcheck "C4: the given-up meeting is listed as not covered" \
  "[(x['meeting_id'], x['attempts']) for x in d['coverage']['not_covered']] == [('sample-7a8b', 2)] and not d['coverage']['retrying']"
jcheck "C4: the digest says which meeting could not be read and that it is not covered" \
  "'Meeting Fennaroot Dental — security review, Thu Oct 8 could not be read after 2 attempts — not covered' in d['markdown']"
jcheck "C4: no item announced by the Incomplete digest is flagged new again" \
  "not (set('$N1'.split()) & {i['id'] for g in d['groups'].values() for i in g if i['new']})"
expect 0 "Confirmed posted" out "C4: the digest with the not-covered meeting is confirmed like any other" \
  --confirm-posted weekly:2026-10-05..2026-10-16 --as-of 2026-10-16T16:01:00-07:00 --state "$SG"
python3 -c "import json,sys; d=json.load(open('$SG'))['unread_meetings']['sample-7a8b']; sys.exit(0 if d['given_up'] and d['attempts'] == 2 else 1)" \
  || fail "C4: the given-up meeting isn't recorded in the state"
ok "C4: the state records the meeting as given up, so it never blocks a run again"
python3 build_commitments.py --next-window weekly --as-of 2026-10-23T16:00:00-07:00 --state "$SG" >"$TMP/out"
jcheck "C4: --next-window moves on and lists the given-up meeting for an opportunistic re-read" \
  "d['from'] == '2026-10-16' and [x['meeting_id'] for x in d['reread']] == ['sample-7a8b']"
c4_bundle c4g3 2026-10-16 2026-10-23 weekly stuck
expect 0 '"status": "reported"' out "C4: the week after giving up, a normal digest is staged" \
  --bundle "$TMP/c4g3.json" --config commitments.json --as-of 2026-10-23T16:00:00-07:00 --internal-domain brackenvole.example \
  --state "$SG" --scheduled --json
jcheck "C4: and it flags nothing new that was announced before" "not {i['id'] for g in d['groups'].values() for i in g if i['new']}"
python3 build_commitments.py --confirm-posted weekly:2026-10-16..2026-10-23 --state "$SG" >/dev/null

# 5. Readable again after giving up: the re-read meeting (outside the window, reread: true) is merged and disclosed
c4_bundle c4g4 2026-10-23 2026-10-30 weekly reread
expect 0 '"status": "reported"' out "C4: a given-up meeting that reads again is re-read from an earlier window" \
  --bundle "$TMP/c4g4.json" --config commitments.json --as-of 2026-10-30T16:00:00-07:00 --internal-domain brackenvole.example \
  --state "$SG" --scheduled --json
jcheck "C4: its promise is tracked and flagged new once" \
  "[i['new'] for g in d['groups'].values() for i in g if i['deliverable'] == 'Send the pen test report'] == [True]"
jcheck "C4: Coverage says the meeting listed as not covered was read again" \
  "'Fennaroot Dental — security review, Thu Oct 8 was read again' in d['markdown'] and [x['meeting_id'] for x in d['coverage']['read_again']] == ['sample-7a8b']"
python3 build_commitments.py --next-window weekly --as-of 2026-10-30T16:30:00-07:00 --state "$SG" >"$TMP/out"
jcheck "C4: until that digest is confirmed posted, the meeting is still offered for re-reading (never lost to a failed post)" \
  "[x['meeting_id'] for x in d['reread']] == ['sample-7a8b']"
python3 build_commitments.py --confirm-posted weekly:2026-10-23..2026-10-30 --state "$SG" >/dev/null
python3 build_commitments.py --next-window weekly --as-of 2026-11-06T16:00:00-07:00 --state "$SG" >"$TMP/out"
jcheck "C4: once a digest that read it is posted, the meeting is no longer listed for re-reading" "d['reread'] == []"
expect 0 '"status": "reported"' out "C4: re-reading a meeting another run already read again (a racing trigger) is harmless" \
  --bundle "$TMP/c4g4.json" --config commitments.json --as-of 2026-11-06T16:00:00-07:00 --internal-domain brackenvole.example \
  --state "$SG" --dry-run --json
jcheck "C4: ... and flags nothing new again" "not {i['id'] for g in d['groups'].values() for i in g if i['new']}"
variant c4bad2 "$TMP/c4g4.json" 'b["meetings"][0]["reread"] = "yes"'
expect 2 "reread" err "C4: reread must be true or false" \
  --bundle "$TMP/c4bad2.json" --config commitments.json --as-of 2026-11-06T16:00:00-07:00 --internal-domain brackenvole.example

# 6. Daily: the run budget (unreadable_retry_runs: 2 retries) gives up on the third failed run
SD="$TMP/c4_daily.json"
for d in 2026-10-08 2026-10-09; do
  c4_bundle "c4d_$d" 2026-10-08 "$d" daily stuck
  expect 5 '"status": "incomplete"' out "C4: daily run on $d with the meeting unreadable: Incomplete (within the retry budget)" \
    --bundle "$TMP/c4d_$d.json" --config "$C4CFG" --as-of "${d}T23:00:00-07:00" --internal-domain brackenvole.example --state "$SD" --scheduled --json
done
c4_bundle c4d3 2026-10-08 2026-10-12 daily stuck
expect 0 '"status": "reported"' out "C4: the third failed daily run gives up and stages the digest" \
  --bundle "$TMP/c4d3.json" --config "$C4CFG" --as-of 2026-10-12T23:00:00-07:00 --internal-domain brackenvole.example --state "$SD" --scheduled --json
jcheck "C4: ... listing the meeting as not covered after 3 attempts" "[x['attempts'] for x in d['coverage']['not_covered']] == [3]"

# 7. A retrying meeting left out of the next bundle is never dropped silently
SM="$TMP/c4_missing.json"
python3 build_commitments.py --bundle "$TMP/c4r1.json" "${W1[@]}" --config "$C4CFG" --state "$SM" --scheduled >/dev/null 2>&1 || true
c4_bundle c4m2 2026-10-05 2026-10-16 weekly missing
expect 5 "not in this run's meeting list" out "C4: a meeting whose read failed and that is missing from the next bundle keeps the run Incomplete" \
  --bundle "$TMP/c4m2.json" "${W2[@]}" --config "$C4CFG" --state "$SM" --scheduled
c4_bundle c4m3 2026-10-05 2026-10-19 weekly missing
expect 0 "could not be read after 3 attempts — not covered" out "C4: ... and is listed as not covered once the budget is used up" \
  --bundle "$TMP/c4m3.json" --config "$C4CFG" --as-of 2026-10-19T16:00:00-07:00 --internal-domain brackenvole.example --state "$SM" --scheduled

# C4 (merge): a promise read late (a meeting re-read after it was given up) joins the open item that existed when it
# was said, not a newer open item of the same wording (it can't repeat a promise made after it).
SQ="$TMP/c4_late.json"
QD=(--config commitments.json --internal-domain brackenvole.example --state "$SQ")
q_bundle c4l1 2026-10-01 2026-10-01 on_request "[$(printf "$QM" c4-mA 2026-10-01T10:00:00-07:00)]" "[$(printf "$QC" r1 c4-mA "I will introduce Marcus to our data engineer.")]" '[]'
python3 "$SCRIPT" --bundle "$TMP/c4l1.json" --as-of 2026-10-01T18:00:00-07:00 "${QD[@]}" --json >"$TMP/out"
X="$(python3 -c "import json; print(json.load(open('$TMP/out'))['groups']['no_date'][0]['id'])")"
q_bundle c4l2 2026-10-06 2026-10-06 on_request '[]' '[]' "[{\"commitment_id\": \"$X\", \"action\": \"mark_done\", \"date\": \"2026-10-06\"}]"
python3 "$SCRIPT" --bundle "$TMP/c4l2.json" --as-of 2026-10-06T18:00:00-07:00 "${QD[@]}" >/dev/null
q_bundle c4l3 2026-10-08 2026-10-08 on_request "[$(printf "$QM" c4-mB 2026-10-08T10:00:00-07:00)]" "[$(printf "$QC" r2 c4-mB "Sorry, that slipped; I will introduce Marcus to our data engineer.")]" '[]'
python3 "$SCRIPT" --bundle "$TMP/c4l3.json" --as-of 2026-10-08T18:00:00-07:00 "${QD[@]}" >/dev/null
q_bundle c4l4 2026-10-30 2026-10-30 on_request '[]' '[]' "[{\"commitment_id\": \"$X\", \"action\": \"reopen\", \"date\": \"2026-10-30\"}]"
python3 "$SCRIPT" --bundle "$TMP/c4l4.json" --as-of 2026-10-30T18:00:00-07:00 "${QD[@]}" >/dev/null
q_bundle c4l5 2026-10-01 2026-10-30 on_request "[$(printf "$QM" c4-mA2 2026-10-01T10:00:00-07:00)]" "[$(printf "$QC" r3 c4-mA2 "I will introduce Marcus to our data engineer.")]" '[]'
python3 "$SCRIPT" --bundle "$TMP/c4l5.json" --as-of 2026-10-30T18:30:00-07:00 "${QD[@]}" >/dev/null
python3 - "$SQ" "$X" <<'PY' || fail "C4: a promise read late joined an open item made after it"
import json, sys
items = json.load(open(sys.argv[1]))["commitments"]
where = [cid for cid, r in items.items() if any(m["meeting_id"] == "c4-mA2" for m in r["mentions"])]
assert where == [sys.argv[2]], (where, sys.argv[2], {c: [m["meeting_id"] for m in r["mentions"]] for c, r in items.items()})
PY
ok "C4: a promise read late joins the open item that existed when it was said, not a newer one of the same wording"

# 8. Config and state validation
python3 -c "import json; c=json.load(open('commitments.json')); c['unreadable_retry_runs']=-1; json.dump(c, open('$TMP/c4_badcfg.json','w'))"
expect 2 "unreadable_retry_runs" err "C4: a negative retry budget is refused" \
  --bundle sample_bundle_week1.json --config "$TMP/c4_badcfg.json" --as-of 2026-10-09T16:00:00-07:00 --internal-domain brackenvole.example
python3 - "$SG" "$TMP" <<'PY' || fail "C4: unread_meetings state fuzz"
import contextlib, copy, importlib.util, io, json, sys
spec = importlib.util.spec_from_file_location("bc", "build_commitments.py"); bc = importlib.util.module_from_spec(spec); spec.loader.exec_module(bc)
base = json.load(open(sys.argv[1], encoding="utf-8")); target = sys.argv[2] + "/c4_fuzz.json"
base["unread_meetings"] = {"sample-7a8b": {"title": "T", "day": "2026-10-08", "url": None, "attempts": 2, "first_failed": "2026-10-09T16:00:00-07:00",
                           "last_failed": "2026-10-16T16:00:00-07:00", "last_slot": "weekly:2026-10-12", "given_up": "2026-10-16T16:00:00-07:00",
                           "reread_until": "2026-11-07"}}
bad = 0
def run(state):
    open(target, "w", encoding="utf-8").write(json.dumps(state))
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        return bc.main(["--next-window", "weekly", "--as-of", "2026-11-06T16:00:00-07:00", "--state", target])
assert run(base) == 0
for key in list(base["unread_meetings"]["sample-7a8b"]) + [None]:
    for value in [None, 7, "x", [], {}, True, KeyError]:
        s = copy.deepcopy(base)
        if key is None:
            if value is KeyError:
                s.pop("unread_meetings")
            else:
                s["unread_meetings"] = value
        elif value is KeyError:
            del s["unread_meetings"]["sample-7a8b"][key]
        else:
            s["unread_meetings"]["sample-7a8b"][key] = value
        try:
            code = run(s)
        except SystemExit as e:
            code = e.code
        if code not in (0, 2):
            print("EXIT", code, key, value); bad += 1
        if key is None and value is KeyError and code != 0:
            print("a state file without unread_meetings (older version) must load"); bad += 1
        if key in ("title", "day", "first_failed", "last_slot", "reread_until") and value in (7, [], {}, True) and code != 2:
            print("ACCEPTED", key, value); bad += 1
        if key == "attempts" and value in ("x", None, [], True, KeyError) and code != 2:
            print("ACCEPTED", key, value); bad += 1
assert bad == 0
PY
ok "C4: every mutation of an unread_meetings record exits 0 or 2 (wrong types 2); older state files without it load"

# 9. Docs (C4, X3, X4)
python3 - <<'PY' || fail "C4/X3/X4 docs"
import json, re
flat = lambda f: " ".join(open(f, encoding="utf-8").read().split())
skill, contract, readme, notice = flat("SKILL.md"), flat("DATA_CONTRACT.md"), flat("README.md"), flat("NOTICE.md")
listing = json.load(open("_listing.json", encoding="utf-8"))
for key in ["unreadable_retry_runs", "unreadable_retry_days", "not covered", "reread", "unconfirmed_incomplete"]:
    assert key in skill, ("SKILL.md", key)
for key in ["unread_meetings", "reread", "given_up", "unreadable_retry_runs", "not_covered"]:
    assert key in contract, ("DATA_CONTRACT.md", key)
assert "not covered" in readme and "retried" in readme, "README"
# X4: the live-run note says this round's changes were tested offline only
assert re.search(r"QA round 5[^.]*(tested|verified) offline only", skill), "SKILL.md live-run note"
# X3: the same quote wording everywhere: the agent copies quotes; the script can't verify them
phrase = "the agent copies each quote; the script can't verify the wording"
for name, text in [("SKILL.md", skill), ("README.md", readme), ("NOTICE.md", notice),
                   ("_listing.json", " ".join(listing["benefits"]))]:
    assert phrase in text.replace("The agent", "the agent"), (name, "quote wording")
PY
ok "C4/X3/X4: SKILL, DATA_CONTRACT and README document the bounded retry; live-run note says round 5 was tested offline only; one quote wording everywhere"

# ---------------------------------------------------------------- S: scenario harness (the automation recipe over many weeks)
cat >"$TMP/scenario.py" <<'PY'
# Scenario harness: an agent following SKILL.md's automation recipe literally over many simulated weeks (daily and
# weekly cadences, odd run times, DST in New York and London, meetings in 7 zones, Granola outages, unreadable
# notes and failed get_meetings batches (re-read by a later run), meetings unreadable for days, weeks (given up after
# the retry budget, then re-read from an earlier window) or for good, Incomplete digests posted and confirmed, stale triggers refused, failed posts, lost confirms, resumes with the original or a later --as-of, supersedes, racing triggers, held
# locks, bad confirms, on-request and --owner runs, user replies in any order, repeats after delivery, re-extractions
# trimmed or reworded, duplicate notes, two deliverables per sentence, upgrades from a state_version 1 file, pruning).
# Invariants after every step: the state loads, every meeting is scanned by a posted digest (or sits in a disclosed
# Gap, or is listed by name as not covered, and re-read once readable again), no period or slot is posted twice,
# nothing is announced new twice (across complete and Incomplete digests), no open promise is lost or tracked twice,
# --list-open matches the state, rejected confirms change nothing.  usage: python3 - SCRIPT SEEDS WEEKS [FIRST_SEED]
import importlib.util, io, json, os, random, re, sys, tempfile, shutil
from datetime import date, datetime, timedelta, timezone

SCRIPT = sys.argv[1]
spec = importlib.util.spec_from_file_location("bc", SCRIPT)
bc = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bc)
CONFIG = os.path.join(os.path.dirname(os.path.abspath(SCRIPT)), "commitments.json")


def call(argv):
    out, err = io.StringIO(), io.StringIO()
    old = sys.stdout, sys.stderr
    sys.stdout, sys.stderr = out, err
    try:
        code = bc.main(argv)
    except SystemExit as e:
        code = e.code
    finally:
        sys.stdout, sys.stderr = old
    if "Traceback" in err.getvalue():
        raise AssertionError("traceback: " + err.getvalue())
    return code, out.getvalue(), err.getvalue()


# ---------------------------------------------------------------- time zones with DST
def nth_sunday(y, m, n):
    d = date(y, m, 1)
    d += timedelta(days=(6 - d.weekday()) % 7)
    return d + timedelta(weeks=n - 1)


def last_sunday(y, m):
    d = (date(y, m + 1, 1) if m < 12 else date(y + 1, 1, 1)) - timedelta(days=1)
    while d.weekday() != 6:
        d -= timedelta(days=1)
    return d


def offset_hours(tz, local):
    y = local.year
    if tz in ("NY", "LA"):
        base = -5 if tz == "NY" else -8
        a = datetime.combine(nth_sunday(y, 3, 2), datetime.min.time()) + timedelta(hours=2)
        b = datetime.combine(nth_sunday(y, 11, 1), datetime.min.time()) + timedelta(hours=2)
        return base + (1 if a <= local < b else 0)
    if tz == "LON":
        a = datetime.combine(last_sunday(y, 3), datetime.min.time()) + timedelta(hours=1)
        b = datetime.combine(last_sunday(y, 10), datetime.min.time()) + timedelta(hours=2)
        return 1 if a <= local < b else 0
    return {"UTC": 0, "TYO": 9, "KOL": 5.5, "AKL": 13}[tz]


def aware(tz, local):
    return local.replace(tzinfo=timezone(timedelta(hours=offset_hours(tz, local))))


def iso(dt):
    return dt.isoformat()


# ---------------------------------------------------------------- world
class Ob:
    """A ground-truth obligation."""
    def __init__(self, oid, direction, owner, cust, deliverable, made):
        self.oid, self.direction, self.owner, self.cust, self.deliverable = oid, direction, owner, cust, deliverable
        self.made = made
        self.delivered = None   # date
        self.dismissed = None
        self.extracted = False
        self.done_at = None     # when the user said it was done
        self.email = None
        self.pruned = False
        self.last_activity = made


class World:
    def __init__(self, rng, start, weeks, tz):
        self.rng, self.tz = rng, tz
        self.meetings = []  # dicts
        self.obs = []
        self.promises = []  # (meeting_id, quote, deliverable, ob)
        n = 0
        day = start
        end = start + timedelta(weeks=weeks)
        while day < end:
            k = rng.choice([0, 1, 1, 2, 3]) if day.weekday() < 5 else rng.choice([0, 0, 0, 1])
            for _ in range(k):
                n += 1
                mtz = tz if rng.random() < 0.6 else rng.choice(["NY", "LON", "LA", "TYO", "UTC", "KOL", "AKL"])
                hour = rng.choice([0, 1, 7, 9, 10, 11, 13, 14, 15, 16, 17, 22, 23])
                local = datetime.combine(day, datetime.min.time()) + timedelta(hours=hour, minutes=rng.choice([0, 15, 30, 59]))
                start_dt = aware(mtz, local)
                cust = "cust%d" % rng.randint(1, 4)
                internal_only = rng.random() < 0.07
                m = {"meeting_id": "m%04d" % n, "title": "Call %d <%s>" % (n, cust), "start": start_dt,
                     "cust": cust, "internal": internal_only,
                     "notes_ok": rng.random() > 0.05, "transcript": rng.choice(["ok", "ok", "denied", "not_requested"]),
                     "url": "https://notes.granola.ai/d/sample-%d" % n, "promises": []}
                self.meetings.append(m)
                if rng.random() < 0.05:
                    self.meetings.append(dict(m, meeting_id=m["meeting_id"] + "b", url=m["url"] + "b", twin=m))
            day += timedelta(days=1)
        self.meetings.sort(key=lambda m: m["start"])
        # C4: some meetings can't be read (get_meetings fails) for a while: a few days (recovers by a later run),
        # 3-7 weeks (given up, then readable again: re-read from an earlier window), or for good.
        srng = random.Random(start.toordinal() * 31 + len(self.meetings))
        for m in self.meetings:
            r = srng.random()
            m["stuck_until"] = None
            m["stuck"] = r < 0.05
            if r < 0.02:
                m["stuck_until"] = m["start"] + timedelta(days=srng.uniform(0.5, 9))
            elif r < 0.04:
                m["stuck_until"] = m["start"] + timedelta(days=srng.uniform(21, 50))
        # promises, in meeting order; each quote carries "#<pid> <word>" markers; pid -> obligation is decided at
        # first extraction (a repeat of an obligation already closed by then is a new obligation with the same wording)
        self.pid_ob = {}
        self.successor = {}
        self.pid_base = {}
        for m in self.meetings:
            if m["internal"]:
                continue
            if m.get("twin"):
                m["promises"] = m["twin"]["promises"]  # same list object: the second note of one call
                continue
            for _ in range(rng.choice([0, 1, 1, 2])):
                mday = m["start"].date()
                here = [self.pid_base[p] for p, _ in m["promises"]]
                repeatable = [o for o in self.obs if o.cust == m["cust"] and o.made < mday and o not in here
                              and not o.delivered == mday]
                if repeatable and rng.random() < 0.3:
                    o = rng.choice(repeatable)
                    pid = self.new_pid(o)
                    word = o.deliverable.split()[2] if "the" in o.deliverable else "item"
                    m["promises"].append((pid, "Sorry, %s slipped; again (#%d %s)." % (o.deliverable.lower(), pid, word)))
                    continue
                direction = "we_owe" if rng.random() < 0.7 else "they_owe"
                owner = rng.choice(["Dana Ortiz", "Sam Kim"]) if direction == "we_owe" else "Priya " + m["cust"]
                if rng.random() < 0.15:  # two deliverables in one sentence
                    o1 = self.new_ob(direction, owner, m["cust"], "Send the deck %d", mday)
                    o2 = self.new_ob(direction, owner, m["cust"], "Send the pricing %d", mday)
                    p1, p2 = self.new_pid(o1), self.new_pid(o2)
                    q = "I'll send the deck and the pricing by Friday (#%d deck, #%d pricing)." % (p1, p2)
                    m["promises"].append((p1, q))
                    m["promises"].append((p2, q))
                else:
                    o = self.new_ob(direction, owner, m["cust"], "Send item %d", mday)
                    p = self.new_pid(o)
                    m["promises"].append((p, "I'll get you item %d %s (#%d item)." % (
                        o.oid, rng.choice(["by Friday", "next week", "soon", "tomorrow"]), p)))

    def new_ob(self, direction, owner, cust, fmt, mday):
        o = Ob(len(self.obs), direction, owner, cust, fmt % len(self.obs), mday)
        self.obs.append(o)
        if self.rng.random() < 0.6:
            o.delivered = mday + timedelta(days=self.rng.randint(0, 20))
        o.email = o.delivered  # the day an email delivered it (a same-day reply then doesn't count as said first)
        return o

    def new_pid(self, o):
        pid = len(self.pid_base)
        self.pid_base[pid] = o
        return pid

    def ob_for(self, pid, start, ingested):
        mday = start.date()
        """Obligation of a promise, fixed at its first extraction."""
        if pid not in self.pid_ob:
            o = self.pid_base[pid]
            while o.oid in self.successor:
                o = self.successor[o.oid]
            # SKILL.md: a repeat on (or after) the day you dismissed the item or marked it done is a new promise;
            # one after the day evidence delivered it is new too
            closed = (o.dismissed is not None and o.dismissed.date() <= mday) or \
                     (o.delivered is not None and o.delivered < mday) or \
                     (o.done_at is not None and o.done_at.date() <= mday and o.email != o.done_at.date())
            if closed:
                o2 = Ob(len(self.obs), o.direction, o.owner, o.cust, o.deliverable, mday)
                self.obs.append(o2)
                self.successor[o.oid] = o2
                o = o2
            self.pid_ob[pid] = o
        return self.pid_ob[pid]


MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def parse_window(text):
    parts = [p.strip() for p in text.split(" to ")]
    year = int(re.search(r"(\d{4})$", parts[-1]).group(1))
    out = []
    for p in parts:
        m = re.match(r"\w+ (\w+) (\d+)(?:, (\d{4}))?", p)
        out.append(date(int(m.group(3) or year), MONTHS.index(m.group(1)) + 1, int(m.group(2))))
    if len(out) == 1:
        out.append(out[0])
    if out[0] > out[1]:
        out[0] = out[0].replace(year=out[0].year - 1)
    return out[0], out[1]


MARK_RE = re.compile(r"#(\d+) (\w+)")


# ---------------------------------------------------------------- agent following the recipe
class Sim:
    def __init__(self, seed, weeks):
        self.rng = rng = random.Random(seed)
        self.seed = seed
        self.tz = rng.choice(["NY", "LON", "UTC", "TYO", "LA"])
        self.cadence = rng.choice(["weekly", "daily"])
        year = rng.choice([2026, 2027])
        start = rng.choice([date(year, 2, 2), date(year, 3, 2), date(year, 9, 28), date(year, 10, 12)])
        start -= timedelta(days=start.weekday())
        self.start = start
        self.weeks = weeks
        self.world = World(rng, start, weeks, self.tz)
        self.dir = tempfile.mkdtemp(prefix="sim")
        self.state = os.path.join(self.dir, "state.json")
        self.posts = []        # (period_key, as_of, json result)
        self.covered = set()   # meeting ids in confirmed/empty bundles
        self.disclosed = set() # meeting ids a confirmed digest listed as not covered (given up after retries)
        self.inc_posts = []    # (period_key, as_of, json result) of Incomplete digests that reached the destination
        self.v1_unread = set() # unread meetings forgotten by a simulated downgrade to a state_version 1 file
        self.gaps = []         # (from, to) disclosed gaps
        self.bundles = {}      # period_key -> (bundle path, meeting ids, as_of)
        self.new_announced = {}  # oid -> period key that announced it new (confirmed)
        self.violations = []
        self.stats = {"runs": 0, "posted": 0, "skipped": 0, "pending": 0, "incomplete": 0, "resumed": 0, "superseded": 0,
                      "locked": 0, "ondemand": 0}
        self.plan_free = rng.random() < 0.3
        self.migrate_at = rng.randint(2, 12) if rng.random() < 0.3 else None  # upgrade from a state_version 1 file
        self.first_from = None
        self.outage = None
        if rng.random() < 0.2:
            a = start + timedelta(weeks=rng.randint(1, 4))
            self.outage = (a, a + timedelta(weeks=rng.randint(1, 6)))
        self.seen = set()
        self.before = {}
        self.bundle_obs = {}
        self.ext_ok_prob = 0.85
        self.ingested = {}     # oid -> date evidence was given

    def v(self, what):
        line = "seed %d: %s" % (self.seed, what)
        if line not in self.violations:
            self.violations.append(line)
    def base(self):
        return ["--config", CONFIG, "--internal-domain", "ours.example", "--state", self.state]

    def load(self):
        if not os.path.exists(self.state):
            return {"commitments": {}, "runs": []}
        with open(self.state, encoding="utf-8") as f:
            return json.load(f)

    def obs_of(self, rec):
        out = set()
        for mnt in rec["mentions"]:
            marks = MARK_RE.findall(mnt["quote"])
            if len(marks) > 1:
                marks = [x for x in marks if x[1] in rec["deliverable"].lower()] or marks
            pid = int(marks[0][0])
            if pid in self.world.pid_ob:
                out.add(self.world.pid_ob[pid].oid)
        return out

    # -- schedule
    def run_times(self):
        rng = self.rng
        times = []
        day = self.start
        end = self.start + timedelta(weeks=self.weeks)
        while day < end:
            if self.cadence == "weekly" and day.weekday() == 4:
                choice = rng.random()
                if choice < 0.6:
                    t = (day, 16, 0)
                elif choice < 0.7:
                    t = (day, 23, 59)
                elif choice < 0.8:
                    t = (day + timedelta(days=1), 10, 0)
                elif choice < 0.9:
                    t = (day + timedelta(days=2), 23, 59)
                else:
                    t = (day + timedelta(days=3), 0, 0)
                times.append(t)
                if rng.random() < 0.25:
                    times.append((t[0], min(t[1] + rng.choice([0, 1]), 23), 30))  # schedule fires again
            if self.cadence == "daily" and day.weekday() < 5:
                times.append((day, rng.choice([8, 8, 8, 0, 23]), rng.choice([0, 0, 59])))
                if rng.random() < 0.1:
                    times.append((day, 23, 59))
            day += timedelta(days=1)
        times.sort()
        out = []
        for d, h, mi in times:
            out.append(aware(self.tz, datetime.combine(d, datetime.min.time()) + timedelta(hours=h, minutes=mi)))
        out = sorted(set(out))
        return out

    # -- bundle building (Steps 2-4)
    def stuck(self, m, now):
        return m["stuck"] and (m["stuck_until"] is None or now < m["stuck_until"])

    def build_bundle(self, frm, to, now, cadence, granola_status="ok", email_ok=True, actions=None, owner=None, unreadable=False, flaky=None, reread=()):
        rng = self.rng
        meetings, commitments, evidence = [], [], []
        listed = 0
        not_read = set()  # meetings whose get_meetings batch failed (flaky = the batch index), retried by a later run
        self.staged_obs = []
        self.staged_seen = []
        today = now.date()
        st = self.load()
        state_obs = {}
        open_ids = []
        code, out, _ = call(["--list-open", "--state", self.state])
        assert code == 0, out
        if out.startswith("["):
            open_ids = [x["commitment_id"] for x in json.loads(out)]
        if granola_status == "ok":
            # Step 2: the window's meetings, plus each meeting --next-window lists under reread that isn't in this
            # window and now reads (within the plan's note history); one that still fails is left out.
            ids = {x["meeting_id"] for x in reread}
            todo = [(m, False) for m in self.world.meetings if m["start"] <= now and frm <= m["start"].date() <= to]
            todo += [(m, True) for m in self.world.meetings if m["meeting_id"] in ids and m["start"].date() < frm
                     and not self.stuck(m, now) and not (self.plan_free and m["start"].date() < today - timedelta(days=30))]
            for m, again in todo:
                notes = "ok" if m["notes_ok"] and not unreadable else "denied"
                transcript = "denied" if unreadable else m["transcript"]
                if (flaky is not None and listed // 10 == flaky) or self.stuck(m, now):
                    notes, transcript = "unavailable", "not_requested"
                    not_read.add(m["meeting_id"])
                listed += 1
                meetings.append({"meeting_id": m["meeting_id"], "title": m["title"], "start": iso(m["start"]),
                                 "url": m["url"], "account": m["cust"],
                                 "attendees": [{"email": "dana@ours.example"}] + ([] if m["internal"] else [{"email": "p@%s.example" % m["cust"]}]),
                                 "notes_status": notes, "transcript_status": transcript})
                if again:
                    meetings[-1]["reread"] = True
                    self.stats["reread"] = self.stats.get("reread", 0) + 1
                if notes != "ok" and transcript != "ok":
                    continue
                src = "notes" if notes == "ok" else "transcript"
                for i, (pid, q) in enumerate(m["promises"]):
                    o = self.world.ob_for(pid, m["start"], self.ingested)
                    self.staged_obs.append((o, m["start"].date()))
                    ref = "r%s-%d" % (m["meeting_id"], i)
                    deliverable = o.deliverable
                    seen_key = (m["meeting_id"], pid)
                    if seen_key in self.seen and rng.random() < 0.3:
                        if rng.random() < 0.5 or "and the pricing" in q:
                            q = q.split(" ", 1)[1] if rng.random() < 0.5 else q.rstrip(".")  # trimmed differently
                            self.stats["trimmed"] = self.stats.get("trimmed", 0) + 1
                        else:
                            deliverable = "Get %s over to them" % o.deliverable.split(" ", 1)[1]
                            self.stats["reworded"] = self.stats.get("reworded", 0) + 1
                    self.staged_seen.append(seen_key)
                    commitments.append({"ref": ref, "meeting_id": m["meeting_id"], "direction": o.direction,
                                        "owner": o.owner, "counterpart": o.cust, "deliverable": deliverable,
                                        "quote": q, "quote_source": src,
                                        "due": {"kind": "relative", "phrase": "by Friday"} if "Friday" in q else {"kind": "none"}})
                    if email_ok and o.delivered is None and rng.random() < 0.05:
                        evidence.append({"ref": ref, "kind": "later_meeting", "strength": "against", "date": today.isoformat(),
                                         "url": "https://notes.granola.ai/d/sample-w%d" % o.oid, "summary": "Still waiting on item %d" % o.oid})
                    if email_ok and o.delivered is not None and o.delivered <= today and o.delivered >= m["start"].date() \
                            and rng.random() < 0.3:
                        evidence.append({"ref": ref, "kind": "slack_message", "strength": "weak", "date": o.delivered.isoformat(),
                                         "url": "https://slack.example/%d" % o.oid, "summary": "Sending item %d" % o.oid, "why": "says sending"})
                    if email_ok and o.delivered is not None and o.delivered <= today and o.delivered >= m["start"].date():
                        evidence.append({"ref": ref, "kind": "sent_email" if o.direction == "we_owe" else "received_email",
                                         "strength": "strong", "date": o.delivered.isoformat(),
                                         "url": "https://mail.example/%d" % o.oid, "summary": "Delivered item %d" % o.oid})
                        self.ingested.setdefault(o.oid, today)
        # evidence for items still open in the state file
        if email_ok:
            for cid in open_ids:
                rec = st["commitments"][cid]
                for oid in sorted(self.obs_of(rec)):
                    o = self.world.obs[oid]
                    if o.delivered is not None and o.delivered <= today and o.delivered >= date.fromisoformat(rec["origin"]["day"]):
                        evidence.append({"commitment_id": cid, "kind": "sent_email" if o.direction == "we_owe" else "received_email",
                                         "strength": "strong", "date": o.delivered.isoformat(),
                                         "url": "https://mail.example/%d" % o.oid, "summary": "Delivered item %d" % o.oid})
                        self.ingested.setdefault(oid, today)
        sources = {"meetings": {"provider": "granola", "status": granola_status, "detail": None,
                                "plan": "free" if self.plan_free else "business"},
                   "email": {"provider": "gmail", "status": "ok" if email_ok else "unavailable", "detail": None},
                   "slack": {"provider": "slack", "status": "ok" if email_ok else "unavailable", "detail": None},
                   "tracker": {"provider": None, "status": "not_configured", "detail": None},
                   "calendar": {"provider": None, "status": "not_configured", "detail": None}}
        b = {"schema_version": 1, "scan": {"from": frm.isoformat(), "to": to.isoformat(), "cadence": cadence},
             "sources": sources, "meetings": meetings, "commitments": commitments, "evidence": evidence,
             "user_actions": actions or []}
        path = os.path.join(self.dir, "b-%s-%s-%d.json" % (frm, to, rng.randint(0, 1 << 30)))
        with open(path, "w", encoding="utf-8") as f:
            json.dump(b, f)
        self.bundle_obs[path] = (self.staged_obs, self.staged_seen)
        self.stats["batch_failures"] = self.stats.get("batch_failures", 0) + (1 if not_read else 0)
        return path, [m["meeting_id"] for m in meetings if m["meeting_id"] not in not_read]  # only what was read counts as covered

    def user_actions(self, now):
        """Random replies about tracked items; ground truth follows them."""
        rng = self.rng
        st = self.load()
        acts = []
        ids = sorted(st["commitments"])
        for _ in range(rng.choice([0, 0, 1, 2])):
            if not ids:
                break
            cid = rng.choice(ids)
            rec = st["commitments"][cid]
            kind = rng.choice(["dismiss", "restore", "mark_done", "reopen", "set_due"])
            a = {"commitment_id": cid, "action": kind, "date": now.date().isoformat()}
            if kind == "set_due":
                a["due_date"] = (now.date() + timedelta(days=3)).isoformat()
            acts.append(a)
        return acts

    def record_truth(self, acts, now):
        st = self.load()
        for a in acts:  # an item pruned by the same run (dismissed, old) is looked up in the state before it
            rec = st["commitments"].get(a["commitment_id"]) or self.before.get(a["commitment_id"])
            if rec is None:
                continue
            day = date.fromisoformat(a["date"])  # replies count from the start of the day they were given
            start = aware(self.tz, datetime.combine(day, datetime.min.time()))
            for oid in self.obs_of(rec):
                o = self.world.obs[oid]
                if a["action"] == "dismiss":
                    o.dismissed = start
                elif a["action"] == "restore":
                    o.dismissed = None
                elif a["action"] == "mark_done":
                    if o.delivered is None or o.delivered > day:
                        o.delivered = day
                        o.done_at = start
                    self.ingested.setdefault(oid, now.date())
                elif a["action"] == "reopen":
                    if o.delivered is not None and o.delivered <= day:
                        o.delivered = None
                        o.done_at = None
                        o.email = None
                        self.ingested.pop(oid, None)

    # -- one scheduled run
    def scheduled(self, now):
        rng = self.rng
        cad = self.cadence
        self.stats["runs"] += 1
        code, out, err = call(["--next-window", cad, "--as-of", iso(now), "--state", self.state, "--config", CONFIG])
        if code != 0:
            self.v("next-window exit %s %s" % (code, err))
            return
        nw = json.loads(out)
        self.confirm_lost_incomplete(nw, now)
        if nw["already_posted"]:
            self.stats["skipped"] += 1
            return
        for p in nw["pending"]:
            posted = [x for x in self.posts if x[0] == p]
            if posted:  # it reached the destination; the confirm was lost
                self.confirm(p, now)
            elif rng.random() < 0.5 and p in self.bundles:
                path, mids, as_of0, acts0 = self.bundles[p]
                use = as_of0 if rng.random() < 0.5 else now
                self.stats["resumed"] += 1
                self.last_skip = False
                if self.run_bundle(path, mids, use, p, resume=True) in (0, 5) and not self.last_skip:
                    self.record_truth(acts0, use)
                self.check_state()
            else:
                self.stats["superseded"] += 1
        frm, to = date.fromisoformat(nw["from"]), date.fromisoformat(nw["to"])
        if self.first_from is None:
            self.first_from = frm  # meetings before the first window are out of scope by design
        if rng.random() < 0.3:  # re-ask after resolving pending (also fine to reuse the earlier answer)
            code, out, err = call(["--next-window", cad, "--as-of", iso(now), "--state", self.state, "--config", CONFIG])
            nw = json.loads(out)
            if nw["already_posted"]:
                self.stats["skipped"] += 1
                return
            frm, to = date.fromisoformat(nw["from"]), date.fromisoformat(nw["to"])
        if self.plan_free and frm < to - timedelta(days=30):
            self.gaps.append((frm, to - timedelta(days=31)))
            frm = to - timedelta(days=30)
        r = rng.random()
        granola = "unavailable" if r < 0.08 or (self.outage and self.outage[0] <= now.date() < self.outage[1]) else "ok"
        acts = self.user_actions(now) if rng.random() < 0.3 else []
        path, mids = self.build_bundle(frm, to, now, cad, granola, rng.random() < self.ext_ok_prob, acts,
                                       unreadable=rng.random() < 0.04,
                                       flaky=rng.randint(0, 1) if rng.random() < 0.08 else None, reread=nw.get("reread", []))
        key = "%s:%s..%s" % (cad, frm, to)
        if not any(r["period_key"] == key and r["status"] == "pending" for r in self.load()["runs"]):
            self.bundles[key] = (path, mids, now, acts)  # keep the bundle a pending digest was staged from
        if rng.random() < 0.05:  # another run holds the lock
            lock = self.state + ".lock"
            with open(lock, "w") as f:
                f.write("pid 1 token x\n")
            code, out, err = call(["--bundle", path, "--as-of", iso(now), "--scheduled", "--json"] + self.base())
            os.unlink(lock)
            if code != 3:
                self.v("locked run exit %s" % code)
            self.stats["locked"] += 1
        race = None
        if rng.random() < 0.08:
            later = now + timedelta(minutes=rng.choice([1, 3, 7]))
            c0, o0, e0 = call(["--next-window", cad, "--as-of", iso(later), "--state", self.state, "--config", CONFIG])
            race = (later, json.loads(o0))
        self.last_skip = False
        code = self.run_bundle(path, mids, now, key)
        skipped_acts = acts if code == 0 and self.last_skip else []
        if code in (0, 5) and not skipped_acts:
            self.record_truth(acts, now)  # the replies are in the state now, before anything else runs
        if race is not None and not race[1]["already_posted"]:
            later, nw2 = race
            f2, t2 = date.fromisoformat(nw2["from"]), date.fromisoformat(nw2["to"])
            if self.plan_free and f2 < t2 - timedelta(days=30):
                f2 = t2 - timedelta(days=30)
            p2, m2 = self.build_bundle(f2, t2, later, cad, "ok", True, [], reread=nw2.get("reread", []))
            k2 = "%s:%s..%s" % (cad, f2, t2)
            c3, o3, _ = call(["--next-window", cad, "--as-of", iso(later), "--state", self.state, "--config", CONFIG])
            self.confirm_lost_incomplete(json.loads(o3), later)  # the racing agent checks the destination first
            self.bundles.setdefault(k2, (p2, m2, later, []))
            self.stats["races"] = self.stats.get("races", 0) + 1
            self.run_bundle(p2, m2, later, k2)
        if rng.random() < 0.1:
            bad = rng.choice(["weekly:2020-01-06..2020-01-10", "daily:%s..%s" % (frm, frm), "%s:%s..%s" % (cad, frm, to + timedelta(days=1))]
                             + [r["period_key"] for r in self.load()["runs"] if r["status"] in ("superseded",)
                                and not any(x["period_key"] == r["period_key"] and x["status"] == "pending"
                                            for x in self.load()["runs"])])
            before = open(self.state, "rb").read() if os.path.exists(self.state) else None
            c2, o2, e2 = call(["--confirm-posted", bad, "--as-of", iso(now), "--state", self.state])
            after = open(self.state, "rb").read() if os.path.exists(self.state) else None
            st = [r for r in self.load()["runs"] if r["period_key"] == bad]
            if not st and c2 != 2:
                self.v("confirm of unknown period %s exit %s" % (bad, c2))
            if c2 == 2 and before != after:
                self.v("rejected confirm changed the state")
            self.stats["badconfirm"] = self.stats.get("badconfirm", 0) + 1
        if rng.random() < 0.05:
            self.stale_trigger()
        self.check_state()
        if skipped_acts:  # SKILL.md Step 7: replies a skipped run didn't apply go in an on-request run
            self.on_request(now + timedelta(minutes=1), skipped_acts)

    def stale_trigger(self):
        """A trigger delivered late: a scheduled run with an --as-of before the last posted run is refused (exit 2) and
        changes nothing."""
        done = [r for r in self.load()["runs"] if r["status"] in ("posted", "empty")]
        if not done:
            return
        last = max(bc.instant(r["as_of"]) for r in done)
        stale = last - timedelta(days=self.rng.choice([1, 3, 8]), minutes=1)
        to = stale.date()
        path, _ = self.build_bundle(to - timedelta(days=4), to, stale, self.cadence, "ok", True, [])
        before = open(self.state, "rb").read()
        code, out, err = call(["--bundle", path, "--as-of", iso(stale), "--scheduled", "--json"] + self.base())
        if code != 2 or "earlier than the last posted run" not in err:
            self.v("stale scheduled run (as of %s, last posted %s) exit %s: %s" % (iso(stale), iso(last), code, out[:200]))
        if open(self.state, "rb").read() != before:
            self.v("refused stale run changed the state")
        self.staged_obs, self.staged_seen = [], []
        self.stats["stale"] = self.stats.get("stale", 0) + 1

    def run_bundle(self, path, mids, now, key, resume=False):
        rng = self.rng
        self.trace = getattr(self, "trace", [])
        argv = ["--bundle", path, "--as-of", iso(now), "--scheduled", "--json"] + self.base()
        if resume:
            argv.append("--resume-pending")
        self.before.update(self.load()["commitments"])
        code, out, err = call(argv)
        if code == 4:
            self.stats["pending"] += 1
            return code
        if code == 2:
            self.v("exit 2 on %s: %s" % (key, err.strip()[:300]))
            return code
        if code == 5:
            self.mark_extracted(path)
            self.stats["incomplete"] += 1
            res = json.loads(out)
            # Exit 5: post the Incomplete digest once (not again if one for this slot already reached the
            # destination), then confirm it so the items it announced aren't flagged new again.
            if res.get("confirm_with") and not any(self.slot(k) == self.slot(key) for k, _, _ in self.inc_posts):
                fate = rng.random()
                if fate >= 0.1:
                    self.inc_posts.append((key, now, res))
                    self.stats["inc_posted"] = self.stats.get("inc_posted", 0) + 1
                    self.announce("Incomplete %s at %s" % (key, iso(now)), res)
                    if fate >= 0.2:
                        self.confirm_incomplete(key, now)
            return code
        if code != 0:
            self.v("exit %s on %s" % (code, key))
            return code
        res = json.loads(out)
        self.last_skip = res["status"] == "skipped" and "already posted" in res["reason"]
        if res["status"] != "skipped" or "nothing to report" in res["reason"]:
            self.mark_extracted(path)
        if res["status"] == "skipped":
            if "nothing to report" in res["reason"]:
                self.covered.update(mids)
            self.stats["skipped"] += 1
            return code
        if res["status"] != "reported":
            self.v("status %s" % res["status"])
            return code
        # post: may fail, may succeed with a lost confirm
        fate = rng.random()
        if fate < 0.1:
            return code  # post failed; not confirmed
        self.post(key, now, res, mids)
        if fate < 0.2:
            return code  # posted, confirm lost (crash)
        self.confirm(key, now)
        if rng.random() < 0.1:
            self.confirm(key, now)  # confirming twice is harmless
        return code

    def mark_extracted(self, path=None):
        if path is not None:
            self.staged_obs, self.staged_seen = self.bundle_obs[path]
        for o, day in self.staged_obs:
            o.extracted = True
            o.last_activity = max(o.last_activity, day)
        self.staged_obs = []
        self.seen.update(self.staged_seen)
        self.staged_seen = []

    def slot(self, key):
        cad, rest = key.split(":")
        to = date.fromisoformat(rest.split("..")[1])
        return cad, (to - timedelta(days=to.weekday())) if cad == "weekly" else to

    def post(self, key, now, res, mids):
        for k, n, _, _ in self.posts:
            if k == key:
                self.v("period %s posted twice" % key)
            elif self.slot(k) == self.slot(key):
                self.v("two digests posted for one slot: %s and %s" % (k, key))
        self.posts.append((key, now, res, mids))
        self.stats["posted"] += 1

    def confirm_lost_incomplete(self, nw, now):
        """Recipe: an Incomplete digest that reached the destination but whose confirm was lost is confirmed now."""
        for p in nw.get("unconfirmed_incomplete", []):
            if any(k == p for k, _, _ in self.inc_posts):
                self.confirm_incomplete(p, now)

    def confirm_incomplete(self, key, now):
        code, out, err = call(["--confirm-posted", key, "--as-of", iso(now), "--state", self.state])
        if code != 0 or "stays open" not in out:
            self.v("confirm of Incomplete %s exit %s: %s %s" % (key, code, out.strip(), err.strip()))

    def announce(self, post_id, res):
        """Every item a posted digest (complete or Incomplete) flags new must not have been announced new before."""
        st = self.load()
        for grp in res["groups"].values():
            for item in grp:
                if not item["new"]:
                    continue
                rec = st["commitments"].get(item["id"])
                if rec is None:
                    continue
                for oid in self.obs_of(rec):
                    if oid in self.new_announced and self.new_announced[oid] != post_id and not self.world.obs[oid].pruned and not self.v1_related(oid) and not any(
                            r["dismissed"] and oid in self.obs_of(r) for r in st["commitments"].values()):
                        # a repeat merged into an older item is fine only if the item was not announced before
                        self.v("obligation %d announced new in %s and %s" % (oid, self.new_announced[oid], post_id))
                    self.new_announced.setdefault(oid, post_id)

    def confirm(self, key, now):
        code, out, err = call(["--confirm-posted", key, "--as-of", iso(now), "--state", self.state])
        if code != 0:
            self.v("confirm %s exit %s: %s" % (key, code, err.strip()))
            return
        post = [x for x in self.posts if x[0] == key][-1]
        self.covered.update(post[3])
        m = re.search(r"meetings from (.+?) were not scanned", post[2].get("markdown", ""))
        if m:
            self.gaps.append(parse_window(m.group(1)))
            self.stats["gaps"] = self.stats.get("gaps", 0) + 1
        # C4: a meeting given up after its retries is disclosed by name in the posted digest
        for x in post[2]["coverage"]["not_covered"]:
            if "could not be read after %d attempt" % x["attempts"] not in post[2]["markdown"]:
                self.v("not-covered meeting %s missing from the digest text" % x["meeting_id"])
            if x["meeting_id"] not in self.disclosed:
                self.stats["gave_up"] = self.stats.get("gave_up", 0) + 1
            self.disclosed.add(x["meeting_id"])
        self.stats["read_again"] = self.stats.get("read_again", 0) + len(post[2]["coverage"]["read_again"])
        self.announce(key, post[2])

    def on_request(self, now, acts=None):
        self.stats["ondemand"] += 1
        frm = now.date() - timedelta(days=14)
        if self.plan_free:
            frm = max(frm, now.date() - timedelta(days=30))
        if acts is None:
            acts = self.user_actions(now)
        path, mids = self.build_bundle(frm, now.date(), now, "on_request", "ok", True, acts)
        argv = ["--bundle", path, "--as-of", iso(now), "--json"] + self.base()
        if self.rng.random() < 0.3:
            argv += ["--owner", "Dana Ortiz"]
        self.before.update(self.load()["commitments"])
        code, out, err = call(argv)
        if code == 0:
            self.mark_extracted()
            self.record_truth(acts, now)
        if code != 0:
            self.v("on-request exit %s: %s" % (code, err.strip()[:300]))
            return
        self.check_state()

    def check_state(self):
        st = self.load()
        today = None
        # every tracked obligation appears in at most one item that is not closed
        seen = {}
        for cid, rec in st["commitments"].items():
            status = rec["last_status"]
            if status in ("dismissed", "done"):
                continue
            for oid in self.obs_of(rec):
                if oid in seen:
                    self.v("obligation %d open in two items %s and %s" % (oid, seen[oid], cid))
                seen[oid] = cid
        # every extracted, truth-open obligation is tracked in an open item (unless pruned for inactivity)
        last = max([bc.instant(r["as_of"]).date() for r in st["runs"]] + [date(1900, 1, 1)])
        for o in self.world.obs:
            if not o.extracted or o.dismissed is not None:
                continue
            if o.delivered is not None and o.oid in self.ingested:
                continue
            if o.oid not in seen:
                where = [cid for cid, rec in st["commitments"].items() if o.oid in self.obs_of(rec)]
                stat = [st["commitments"][c]["last_status"] for c in where]
                if where and o.delivered is not None:
                    continue  # evidence for it may not have been searched yet
                if not where and (last - o.last_activity).days > 170:
                    if not o.pruned:
                        self.stats["pruned"] = self.stats.get("pruned", 0) + 1
                    o.pruned = True
                    continue  # pruned after prune_open_after_days, disclosed in the digest
                twin = [x for c in where for x in self.obs_of(st["commitments"][c]) if x != o.oid]
                if any(self.world.obs[x].deliverable == o.deliverable and self.world.obs[x].delivered is not None
                       and x in self.ingested for x in twin):
                    continue  # two open promises of the very same thing: delivering it closes both
                self.v("open obligation %d (%s) lost: items %s %s" % (o.oid, o.deliverable, where, stat))
        # --list-open == items not dismissed and not reported done
        code, out, err = call(["--list-open", "--state", self.state])
        if code != 0:
            self.v("state no longer loads: " + err.strip()[:200])
        ids = sorted(x["commitment_id"] for x in json.loads(out)) if out.startswith("[") else []
        posted = sorted(r["period_key"] for r in st["runs"] if r["status"] == "posted")
        if len(posted) != len(set(posted)):
            self.v("a period is logged as posted twice")
        if not set(posted) <= {x[0] for x in self.posts}:
            self.v("state logs a posted period that never reached the destination: %s" % sorted(set(posted) - {x[0] for x in self.posts}))
        want = sorted(c for c, r in st["commitments"].items() if not r["dismissed"] and not r["done_reported"])
        if ids != want:
            self.v("--list-open mismatch")

    def finish(self):
        # coverage: every meeting that started before the last posted run (minus a day for zones ahead)
        posted_runs = [x for x in self.load()["runs"] if x["status"] in ("posted", "empty")]
        if not posted_runs:
            return
        last = max(bc.instant(r["as_of"]) for r in posted_runs)
        for m in self.world.meetings:
            if m["start"] < last - timedelta(days=1) and m["meeting_id"] not in self.covered and \
                    m["start"].date() >= self.first_from:
                d = m["start"].date()
                if any(a <= d <= b for a, b in self.gaps):
                    continue
                if m["meeting_id"] in self.disclosed:
                    # given up and listed as not covered; once readable again within the note history (120 days on
                    # plans without a limit) and a week before the last posted run, a re-read must have covered it
                    back = m["stuck_until"]
                    horizon = 29 if self.plan_free else 119
                    if back is not None and m["meeting_id"] not in self.v1_unread and back + timedelta(days=8) < last and (back + timedelta(days=8)).date() <= d + timedelta(days=horizon):
                        self.v("meeting %s readable again since %s but never re-read" % (m["meeting_id"], iso(back)))
                    continue
                self.v("meeting %s (%s) never covered by a posted digest" % (m["meeting_id"], iso(m["start"])))

    def v1_related(self, oid):
        """The obligation, or one it repeats, lost its item in the v1 upgrade (the old id scheme's bug), so its
        later history is not comparable."""
        pred = {v.oid: k for k, v in self.world.successor.items()}
        while oid is not None:
            if getattr(self.world.obs[oid], "v1_lost", False):
                return True
            oid = pred.get(oid)
        return False

    def to_v1(self):
        """Rewrite the state the way the first release stored it (state_version 1): ids without the deliverable (so
        two deliverables of one sentence collide and only one survives, the old bug), no `earlier` or evidence
        `order`, and posted/empty runs logged with `result`. The next run upgrades it."""
        import hashlib
        st = self.load()
        if any(r["status"] == "pending" for r in st["runs"]):
            return False
        items = {}
        for cid in sorted(st["commitments"]):
            r = st["commitments"][cid]
            first = r["mentions"][0]
            raw = "%s|%s|%s" % (first["meeting_id"], bc.norm_text(first["quote"]), bc.norm_owner(r["owner"]))
            nid = "ct-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]
            if nid in items:  # the old id scheme kept one of them
                for oid in self.obs_of(r):
                    if oid not in self.obs_of(items[nid]):
                        self.world.obs[oid].extracted = False
                        self.world.obs[oid].v1_lost = True
                # the old file lost this record, so the next extraction of any of its meetings is a first extraction
                gone = {x["meeting_id"] for x in r["mentions"]}
                self.seen = {k for k in self.seen if k[0] not in gone}
                continue
            r = dict(r, id=nid)
            r.pop("earlier", None)
            r["evidence"] = [{k: v for k, v in e.items() if k != "order"} for e in r["evidence"]]
            items[nid] = r
        runs = [{"period_key": r["period_key"], "as_of": r["as_of"], "mode": "scheduled",
                 "result": "reported" if r["status"] == "posted" else "skipped", "counts": r.get("counts", {})}
                for r in st["runs"] if r["status"] in ("posted", "empty")]
        # a state_version 1 file has no unread_meetings: given-up meetings are no longer offered for re-reading
        self.v1_unread.update(st.get("unread_meetings", {}))
        with open(self.state, "w", encoding="utf-8") as f:
            json.dump({"state_version": 1, "commitments": items, "runs": runs, "updated_at": st.get("updated_at")}, f)
        self.before.update(items)
        self.stats["v1"] = self.stats.get("v1", 0) + 1
        return True

    def go(self):
        times = self.run_times()
        for i, now in enumerate(times):
            if self.migrate_at is not None and i >= self.migrate_at and os.path.exists(self.state) and self.to_v1():
                self.migrate_at = None
            if self.rng.random() < 0.15:
                self.on_request(now - timedelta(minutes=10))
            self.scheduled(now)
        # flush: a final run well after the end so everything before is due
        end = times[-1] + timedelta(days=8) if times else None
        if end is not None:
            for extra in range(3):
                self.scheduled(end + timedelta(days=7 * extra))
            self.finish()
        shutil.rmtree(self.dir)


def main():
    seeds, weeks = int(sys.argv[2]), int(sys.argv[3])
    off = int(sys.argv[4]) if len(sys.argv) > 4 else 0
    allv, tot = [], {}
    for seed in range(off, off + seeds):
        sim = Sim(seed, weeks)
        try:
            sim.go()
        except AssertionError as e:
            sim.v("assert " + str(e)[:400])
        allv += sim.violations
        for k, val in sim.stats.items():
            tot[k] = tot.get(k, 0) + val
    print("scenario: %d seeds x %d weeks, %d scheduled runs, %d digests posted, %d resumed, %d superseded, %d races, "
          "%d incomplete (%d Incomplete digests posted), %d failed notes batches, %d meetings given up after retries "
          "(%d read again later, %d re-reads), %d stale triggers, %d on-request runs, %d v1 upgrades, %d pruned: "
          "%d invariant violations" % (
              seeds, weeks, tot["runs"], tot["posted"], tot["resumed"], tot["superseded"], tot.get("races", 0),
              tot["incomplete"], tot.get("inc_posted", 0), tot.get("batch_failures", 0), tot.get("gave_up", 0),
              tot.get("read_again", 0), tot.get("reread", 0), tot.get("stale", 0), tot["ondemand"], tot.get("v1", 0),
              tot.get("pruned", 0), len(allv)))
    for v in allv[:200]:
        print("  " + v[:300])
    sys.exit(1 if allv else 0)


main()
PY
python3 "$TMP/scenario.py" "$PWD/build_commitments.py" 10 8 >"$TMP/scenario.txt" 2>&1 || { cat "$TMP/scenario.txt"; fail "S: scenario invariants"; }
ok "S: $(head -1 "$TMP/scenario.txt")"
# two half-year runs: long enough for items to be pruned, one starting from a state_version 1 file
python3 "$TMP/scenario.py" "$PWD/build_commitments.py" 2 26 500 >"$TMP/scenario2.txt" 2>&1 || { cat "$TMP/scenario2.txt"; fail "S: long scenario invariants"; }
ok "S: $(head -1 "$TMP/scenario2.txt")"

# ---------------------------------------------------------------- C10 / X5 / X6: listing and sample claims
python3 - <<'PY' || fail "listing"
import json, re, subprocess
listing = json.load(open("_listing.json", encoding="utf-8"))
assert not re.search(r"\bevery promise\b|\bFinds every\b", listing["summary"]), listing["summary"]
run = subprocess.run(["bash", "run.sh"], stdout=subprocess.PIPE, check=True).stdout.decode("utf-8")
first = run.split("=== 1.", 1)[1].split("\n", 1)[1].split("\npending: ", 1)[0]
assert listing["sampleOutput"].split("\n\n_Produced by", 1)[0] == first.rstrip("\n"), "sampleOutput differs from run.sh"
PY
ok "C10: listing summary no longer claims every promise; sampleOutput matches run.sh section 1"
! grep -qiE "haulwise|ledgerline|fernhill|harborview|kestrel|larkspur|shopify|quil{2}mos{2}|peb{2}lewick|cop{2}erquil{2}" sample_bundle_week1.json sample_bundle_week2.json expected_output.txt _listing.json SKILL.md DATA_CONTRACT.md README.md \
  || fail "X6: a possibly real company or product name is still in the samples"
ok "X6: samples use only the invented names"
python3 - <<'PY' || fail "size limits"
import os
files = [f for f in os.listdir(".") if os.path.isfile(f) and f != "_listing.json"]
sizes = [os.path.getsize(f) for f in files]
assert len(files) <= 40 and max(sizes) <= 256 * 1024 and sum(sizes) <= 512 * 1024, (len(files), max(sizes), sum(sizes))
PY
ok "package size limits hold (<= 40 files, <= 256 KB each, <= 512 KB total)"

! grep -qiE "[0-9]+ (assertions|tests)\b|PASS [0-9]+/[0-9]+" SKILL.md README.md DATA_CONTRACT.md NOTICE.md _listing.json \
  || fail "a doc hard-codes a tests.sh count (it drifts; say PASS n/n instead)"
ok "no doc hard-codes how many assertions tests.sh runs"

echo "PASS $PASS/$PASS"

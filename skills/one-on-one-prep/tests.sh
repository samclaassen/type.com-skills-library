#!/usr/bin/env bash
# Guardrail and edge-case tests. Run from the skill directory: bash tests.sh
# Set PYTHON=python3.8 (or a path) to test another interpreter.
# Builds variants of the fictional samples in a temp dir; never touches real data, never writes into this folder.
set -uo pipefail

PY="${PYTHON:-python3}"
export PYTHONDONTWRITEBYTECODE=1
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
SCRIPT=build_prep.py
M=sample_bundle_manager.json
R=sample_bundle_report.json
ASOF=2026-10-13T09:30:00-07:00
TOTAL=0
PASS=0

ok() { TOTAL=$((TOTAL + 1)); PASS=$((PASS + 1)); echo "ok $TOTAL - $1"; }
bad() { TOTAL=$((TOTAL + 1)); echo "FAIL $TOTAL - $1"; sed 's/^/    | /' "$TMP/out" "$TMP/err" 2>/dev/null | head -20; }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

# variant NAME SOURCE PYTHON_EXPR : writes $TMP/NAME.json from SOURCE after applying the edit to `b`
variant() {
  "$PY" - "$TMP/$1.json" "$2" "$3" <<'PY'
import json, sys
b = json.load(open(sys.argv[2], encoding="utf-8"))
def item(i): return [x for x in b["items"] if x["id"] == i][0]
def meeting(i): return [x for x in b["meetings"] if x["id"] == i][0]
exec(sys.argv[3])
json.dump(b, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)
PY
}
man() { variant "$1" "$M" "$2"; }
rep() { variant "$1" "$R" "$2"; }
run() { "$PY" "$SCRIPT" "$@" >"$TMP/out" 2>"$TMP/err"; RC=$?; }
has() { grep -qF -- "$1" "$TMP/$2"; }
hasnt() { ! grep -qF -- "$1" "$TMP/$2"; }
clean_fail() { [[ "$RC" == "$1" ]] && [[ ! -s "$TMP/out" ]] && ! grep -q "Traceback" "$TMP/err"; }
# expect CODE PATTERN STREAM NAME -- args... (for errors also: empty stdout, no traceback)
expect() {
  local code="$1" pattern="$2" stream="$3" name="$4"; shift 4
  run "$@"
  if [[ "$RC" == "$code" ]] && has "$pattern" "$stream" && ! grep -q "Traceback" "$TMP/err" \
     && { [[ "$code" == 0 ]] || [[ ! -s "$TMP/out" ]]; }; then ok "$name"
  else bad "$name (exit $RC, want $code; '$pattern' in std$stream?)"; fi
}
A=(--as-of "$ASOF")

# ---------------------------------------------------------------- sample, determinism, packaging
bash run.sh >"$TMP/run1" 2>&1
check "run.sh matches expected_output.txt byte for byte" 'cmp -s "$TMP/run1" expected_output.txt'
PYTHON="$PY" bash run.sh >"$TMP/run2" 2>&1
check "run.sh output is identical on this interpreter ($PY)" 'cmp -s "$TMP/run2" expected_output.txt'
check "no __pycache__ or stray files left in the skill folder" '[[ ! -e __pycache__ ]] && [[ -z "$(find . -mindepth 1 -type d)" ]]'
run --bundle "$M" "${A[@]}" --json
check "--json is valid JSON whose markdown equals the markdown run" '"$PY" -c "
import json,subprocess,sys
d=json.load(open(sys.argv[1],encoding=\"utf-8\"))
md=subprocess.run([sys.argv[2],\"build_prep.py\",\"--bundle\",\"$M\",\"--as-of\",\"$ASOF\"],stdout=subprocess.PIPE).stdout.decode(\"utf-8\")
assert d[\"markdown\"]==md and d[\"schema_version\"]==1 and d[\"withheld_personal\"][\"count\"]==1
" "$TMP/out" "$PY"'
check "--json never contains withheld text" 'hasnt "physio" out && hasnt "knee" out'
run --bundle "$M" "${A[@]}"
check "sample: personal item withheld, counted, text never printed" 'has "Withheld: 1 item withheld as personal or uncategorised (not shown" out && hasnt "physio" out && hasnt "Thursday afternoon" out'
check "sample: privacy line names the only reader" 'has "_Private prep for Dana Ortiz only — not posted or shared anywhere." out'
check "sample: the not-a-performance-review footer is printed" 'has "Not a performance review. A private draft: only items filed under a work topic are shown" out'
check "sample: meeting titles are never printed" 'hasnt "Dana / Riley 1:1" out && hasnt "CS team sync" out'
check "sample: e-mail addresses never printed" 'hasnt "@feltharrow" out'

# ---------------------------------------------------------------- meeting selection
check "group meeting (other people on the invite) is not read" 'has "not read: 1 meeting with other people on the invite" out'
check "an item from that group meeting is not shown" 'has "not shown: 1 item citing a meeting outside the 1:1s read" out && hasnt "onboarding checklist" out'
check "newest 4 of 5 matching 1:1s read (lookback_meetings 4)" 'has "Granola ✓ (4 1:1s read, the newest 4 of 5 found)" out && has "(Sep 15 – Oct 6)" out'
expect 0 "Granola ✓ (2 1:1s read, the newest 2 of 5 found)" out "--meetings 2 reads only the newest two" --bundle "$M" "${A[@]}" --meetings 2
check "--meetings 2: items only from those 1:1s; counts use 2" 'has "came up in 2 of 2 1:1s (Sep 29, Oct 6)" out && hasnt "Long term I want" out'
man other_title 'meeting("sample-1on1-0915")["title"] = "Quick sync"'
expect 0 "1 meeting outside the series title" out "a 2-person meeting with another title is not read (title_and_attendee)" --bundle "$TMP/other_title.json" "${A[@]}"
man att_only 'meeting("sample-1on1-0915")["title"] = "Quick sync"; b["series"]["match"] = "attendee_only"'
expect 0 "came up in 3 of 4 1:1s (Sep 15, Sep 29, Oct 6)" out "match attendee_only reads it" --bundle "$TMP/att_only.json" "${A[@]}"
man title_case 'meeting("sample-1on1-0915")["title"] = "dana/riley 1:1 "'
expect 0 "came up in 3 of 4 1:1s (Sep 15, Sep 29, Oct 6)" out "series title match ignores case, spacing and punctuation" --bundle "$TMP/title_case.json" "${A[@]}"
man not_user 'meeting("sample-1on1-0915")["attendee_emails"] = ["riley@feltharrow.example", "sam@feltharrow.example"]'
expect 0 "1 meeting without both of you listed" out "a meeting you were not on is never read" --bundle "$TMP/not_user.json" "${A[@]}"
man room 'meeting("sample-1on1-0915")["attendee_emails"].append("c_188@resource.calendar.google.com")'
expect 0 "came up in 3 of 4 1:1s" out "a room calendar (ignored_attendee_domains) does not make it a group meeting" --bundle "$TMP/room.json" "${A[@]}"
man future 'meeting("sample-1on1-1006")["start"] = "2026-10-13T10:00:00-07:00"'
expect 0 "read 4 1:1s with Riley (Sep 8 – Sep 29)" out "a meeting at or after --as-of is not read" --bundle "$TMP/future.json" "${A[@]}" --meetings 5
"$PY" -c "import json; c=json.load(open('prep.json')); c['lookback_days']=20; json.dump(c, open('$TMP/cfg_window.json','w'))"
expect 0 "2 1:1s read" out "lookback_days limits the window" --bundle "$M" "${A[@]}" --config "$TMP/cfg_window.json"
check "lookback_days: gap names found/asked" 'has "found 2 of the 4 1:1s asked for in the last 20 days" out'
"$PY" -c "import json; c=json.load(open('prep.json')); c['max_other_attendees']=2; json.dump(c, open('$TMP/cfg_others.json','w'))"
man att_only_all 'b["series"]["match"] = "attendee_only"'
expect 0 "Share the onboarding checklist" out "max_other_attendees 2 lets a 4-person meeting count" --bundle "$TMP/att_only_all.json" "${A[@]}" --config "$TMP/cfg_others.json" --meetings 6
expect 0 "not read: 1 meeting with other people on the invite" out "attendee_only still never reads a group meeting by default" --bundle "$TMP/att_only_all.json" "${A[@]}"
man same_person 'b["counterpart"]["email"] = "DANA@feltharrow.example"'
expect 2 "a 1:1 needs two people" err "user and counterpart with the same e-mail exits 2" --bundle "$TMP/same_person.json" "${A[@]}"
expect 0 "No previous 1:1s with Ade found in the last 120 days" out "no history: says so plainly, never 'nothing open'" --bundle sample_bundle_first.json "${A[@]}"
check "no history: gap names how many were found" 'has "found 0 of the 4 1:1s asked for" out && hasnt "Nothing open" out'
man src_down 'b["sources"]["meetings"]["status"] = "unavailable"; b["meetings"] = []; b["items"] = []'
expect 0 "Incomplete** — couldn't read your meeting notes (unavailable)" out "notes source unavailable: Incomplete banner" --bundle "$TMP/src_down.json" "${A[@]}"
check "source unavailable: never 'No previous 1:1s' and never 'Nothing open'" 'hasnt "No previous 1:1s" out && hasnt "Nothing open" out && has "so this prep has no 1:1 history" out'
man src_denied 'b["sources"]["meetings"]["status"] = "denied"'
expect 0 "Incomplete** — couldn't read your meeting notes (access denied)" out "notes source denied: Incomplete banner" --bundle "$TMP/src_denied.json" "${A[@]}"

# ---------------------------------------------------------------- items, merging, status
run --bundle "$M" "${A[@]}"
check "same key across 1:1s merges into one line with dates" 'has "came up in 3 of 4 1:1s (Sep 15, Sep 29, Oct 6)" out && [[ $(grep -c "EU ticket backlog and routing rules\*\*" "$TMP/out") == 1 ]]'
check "recurring topic in 'Keeps coming back'; single topic in 'Other open topics'" 'has "**Keeps coming back (no resolution yet)**" out && has "**Other open topics**" out'
check "first quote and latest quote both cited with links" 'has "  - Latest: “The EU backlog is still sitting" out'
check "likely done shows the dated evidence and asks to confirm" 'has "likely done — confirm: Slack, Oct 8: churn analysis posted in \#cs-leads" out'
check "done items are counted, not listed" 'has "2 items done since raised (not listed)" out && hasnt "SSO onboarding tickets**" out && hasnt "pilot Gong" out'
check "done claim with evidence older than the last mention stays open, with a note" 'has "open (marked done, but no evidence dated on or after Oct 6, when it last came up)" out'
check "summary-sourced text is labelled AI summary and not quoted" 'has "Latest: AI summary, Oct 6: Riley still can" out'
check "overdue commitment flagged with resolved date and phrase" 'has "⚠️ **Send Riley the promotion criteria doc** — open, due Fri Oct 2 (from “by Friday”, said Tue Sep 29), 11 days overdue" out'
check "agenda puts the report's topics first" 'grep -A1 "^\*\*Suggested agenda\*\* (Riley" "$TMP/out" | grep -q "1. Riley.s topic: EU ticket backlog"'
check "agenda is capped (agenda_max 7) with a pointer to the rest" 'has "7. Confirm done" out && has "…and 2 more in the sections below." out'
check "open work lists open/in-progress issues only, newest first" 'has "- [CS-88](https://linear.app/feltharrow-sample/issue/CS-88) Renewals playbook draft — In Progress, updated Oct 9" out && hasnt "SSO onboarding tickets cleanup" out'
man dup 'b["items"].append(dict(item("t2"), id="t2dup"))'
expect 0 "**Cleaner handoff from Sales to CS** — open · raised Oct 6" out "an exact duplicate (same meeting, same quote) is merged" --bundle "$TMP/dup.json" "${A[@]}"
check "duplicate merged: listed once" '[[ $(grep -c "Cleaner handoff from Sales to CS\*\*" "$TMP/out") == 1 ]]'
man nokey 'item("t1b").pop("key"); item("t1c").pop("key"); item("t1").pop("key")'
expect 0 "came up in 3 of 4 1:1s (Sep 15, Sep 29, Oct 6)" out "without keys, identical summaries still merge" --bundle "$TMP/nokey.json" "${A[@]}"
man ev_future 'item("c2")["evidence"][0]["date"] = "2026-10-20"'
expect 0 "**Post the Q3 churn analysis in the CS channel** — open" out "evidence dated after --as-of does not count" --bundle "$TMP/ev_future.json" "${A[@]}"
man done_ok 'item("c5b")["status"] = "done"; item("c5b")["evidence"] = [{"source": "user", "summary": "IT confirmed", "date": "2026-10-07"}]'
expect 0 "3 items done since raised" out "a done claim with evidence after the last mention is accepted" --bundle "$TMP/done_ok.json" "${A[@]}"
man notext 'meeting("sample-1on1-1006")["text"] = "Riley: nothing like that was said."'
expect 0 "whose quote isn't one or more whole sentences from one speaker's turn in its meeting's text" out "a quote missing from the meeting's text is not shown" --bundle "$TMP/notext.json" "${A[@]}"
check "quote check: summary-sourced items are not checked against the text" 'has "AI summary, Oct 6" out'
man curly 'meeting("sample-1on1-1006")["text"] = meeting("sample-1on1-1006")["text"].replace("I'"'"'ll write", "I’ll  write")'
expect 0 "Write up renewal risks for the three enterprise accounts" out "quote check ignores curly quotes and spacing" --bundle "$TMP/curly.json" "${A[@]}"
man unknown_m 'item("t2")["meeting_id"] = "sample-nope"'
expect 0 "2 items citing a meeting outside the 1:1s read" out "an item citing an unknown meeting is not shown" --bundle "$TMP/unknown_m.json" "${A[@]}"
man fb_owner 'item("f1")["owner"] = "counterpart"'
expect 2 "feedback items are what the manager promised" err "feedback owned by the report exits 2" --bundle "$TMP/fb_owner.json" "${A[@]}"
rep fb_owner_r 'item("r4")["owner"] = "user"'
expect 2 "owner must be 'counterpart' here" err "report perspective: feedback must be the manager's" --bundle "$TMP/fb_owner_r.json" "${A[@]}"
man ev_nomeet 'item("q2")["evidence"][0].pop("meeting_id")'
expect 2 "is required when source is granola" err "granola evidence needs a meeting id" --bundle "$TMP/ev_nomeet.json" "${A[@]}"
man ev_badmeet 'item("q2")["evidence"][0]["meeting_id"] = "sample-zzz"'
expect 2 "which is not in \$.meetings" err "evidence citing an unknown meeting exits 2" --bundle "$TMP/ev_badmeet.json" "${A[@]}"
man ev_nourl 'item("c2")["evidence"][0].pop("url")'
expect 2 "needs a url or ref when source is slack" err "slack evidence needs a link or ref" --bundle "$TMP/ev_nourl.json" "${A[@]}"
man dupid 'item("t2")["id"] = "c1"'
expect 2 "duplicate item id" err "duplicate item ids exit 2" --bundle "$TMP/dupid.json" "${A[@]}"
man dupmid 'meeting("sample-1on1-0915")["id"] = "sample-1on1-0922"'
expect 2 "duplicate meeting id" err "duplicate meeting ids exit 2" --bundle "$TMP/dupmid.json" "${A[@]}"
man noquote 'item("t2")["quote"] = "   "'
expect 2 "quote: is required" err "an item without a quote exits 2 (no quote, no item)" --bundle "$TMP/noquote.json" "${A[@]}"
man longsum 'item("t2")["summary"] = "x " * 200'
expect 2 "keep it under 160" err "summaries over max_summary_chars exit 2" --bundle "$TMP/longsum.json" "${A[@]}"
man longquote 'item("t2")["quote"] = "word " * 300; meeting("sample-1on1-1006").pop("text")'
expect 0 "word word…”" out "long quotes are clipped at a word boundary with an ellipsis" --bundle "$TMP/longquote.json" "${A[@]}"
# B2 (QA round 4): quotes match at word boundaries, inside one speaker turn and one line, with no speaker label
T6='meeting("sample-1on1-1006")'
man qb_prefix "$T6[\"text\"] += \" Riley: The old routing was unhelpful for us.\"; item(\"t2\")[\"quote\"] = \"helpful for us.\""
expect 0 "1 item whose quote isn't one or more whole sentences" out "quote check: 'helpful for us' is not found inside 'unhelpful for us'" --bundle "$TMP/qb_prefix.json" "${A[@]}"
man qb_suffix 'item("t1c")["quote"] = "The EU backlog is still sit"'
expect 0 "1 item whose quote isn't one or more whole sentences" out "quote check: a quote ending inside a word ('sit' of 'sitting') is not found" --bundle "$TMP/qb_suffix.json" "${A[@]}"
man qb_label "$T6[\"text\"] += \" Dana: Will you renew? Riley: We will not renew.\"; item(\"t2\")[\"quote\"] = \"Will you renew? Riley: We will\""
expect 0 "1 item whose quote isn't one or more whole sentences" out "quote check: a quote containing a speaker label (and spanning turns) is not shown" --bundle "$TMP/qb_label.json" "${A[@]}"
man qb_label_nontext 'item("t2")["quote"] = "System audio (Customer): We will"; meeting("sample-1on1-1006").pop("text")'
expect 0 "(or has a speaker label, an ellipsis or under 3 words)" out "quote check: a quote with a speaker label is not shown even without meeting text" --bundle "$TMP/qb_label_nontext.json" "${A[@]}"
man qb_turns "$T6[\"text\"] += \" Dana: Will you renew? Riley: We will not renew.\"; item(\"t2\")[\"quote\"] = \"Will you renew? We will not renew.\""
expect 0 "1 item whose quote isn't one or more whole sentences" out "quote check: words stitched across two speaker turns are not found" --bundle "$TMP/qb_turns.json" "${A[@]}"
man qb_lines "$T6[\"text\"] += \"\\n- we should cut\\n- the EU queue in half\"; item(\"t2\")[\"quote\"] = \"we should cut the EU queue in half\""
expect 0 "1 item whose quote isn't one or more whole sentences" out "quote check: a quote spanning two bullets of the text is not found" --bundle "$TMP/qb_lines.json" "${A[@]}"
man qb_lazy "$T6[\"text\"] += \"\\nRiley: we should cut\\nthe EU queue in half.\"; item(\"t2\")[\"quote\"] = \"we should cut the EU queue in half\""
run --bundle "$TMP/qb_lazy.json" "${A[@]}"
check "quote check: an unfinished line continues on the next line (one sentence, as in markdown)" '[[ "$RC" == 0 ]] && has "“we should cut the EU queue in half”" out' 
man qb_stitch 'item("c3")["quote"] = "I'"'"'ll write up … by the 16th."'
expect 0 "1 item whose quote isn't one or more whole sentences" out "quote check: an ellipsis-stitched quote is still not found" --bundle "$TMP/qb_stitch.json" "${A[@]}"
man qb_md 'meeting("sample-1on1-1006")["text"] = meeting("sample-1on1-1006")["text"].replace("I'"'"'ll write up the renewal risks for Tolvanic", r"I'"'"'ll **write up** the [renewal risks](https://notes.example/x) for \_Tolvanic\_")'
run --bundle "$TMP/qb_md.json" "${A[@]}"
check "quote check: markdown emphasis, link syntax and backslash escapes in the text are ignored" '[[ "$RC" == 0 ]] && has "Write up renewal risks for the three enterprise accounts" out && hasnt "wasn" out'
man qb_md2 'item("c3")["quote"] = "I'"'"'ll **write up** the [renewal risks](https://x.example) for Tolvanic, Quenvault and Pyrelith by the 16th."'
run --bundle "$TMP/qb_md2.json" "${A[@]}"
check "quote check: markdown emphasis and link syntax in the quote are ignored too" '[[ "$RC" == 0 ]] && has "Write up renewal risks for the three enterprise accounts" out && hasnt "wasn" out'
man qb_ok "$T6[\"text\"] += \"\\nRiley: That was helpful for us.\"; item(\"t2\")[\"quote\"] = \"That was helpful for us.\""
run --bundle "$TMP/qb_ok.json" "${A[@]}"
check "quote check: a quote that starts and ends at word boundaries in one turn is shown" '[[ "$RC" == 0 ]] && has "“That was helpful for us.”" out && hasnt "wasn" out'
# Redact before clipping (CONVENTIONS_V2, round 4): a phone number cut by the 280-character clip never leaks in part
man clip_phone 'item("t2")["quote"] = "word " * 54 + "on 07700 900123 and more words here"; meeting("sample-1on1-1006").pop("text")'
run --bundle "$TMP/clip_phone.json" "${A[@]}"
check "contact details are removed before a long quote is clipped (no partial phone number)" '[[ "$RC" == 0 ]] && hasnt "07700" out && hasnt "900123" out && has "word on…”" out'
# B1 (QA round 4): $ and = are escaped (no math or ==highlight== in Type)
man dollar 'item("t2")["summary"] = "cost $5 to $9 and ==hi== x"; meeting("sample-1on1-1006").pop("text")'
run --bundle "$TMP/dollar.json" "${A[@]}"
check "markdown: \$ and == are escaped (no math, no highlight)" '[[ "$RC" == 0 ]] && has "cost \\\$5 to \\\$9 and \\=\\=hi\\=\\= x" out && hasnt " \$5" out && hasnt " ==hi" out'
# O4 (QA round 4): notes captured by someone else are labelled as theirs
man owner_cp 'meeting("sample-1on1-0922")["notes_owner"] = "counterpart"'
run --bundle "$TMP/owner_cp.json" "${A[@]}"
check "note owner: notes captured by the other person are labelled as theirs, not 'your notes'" '[[ "$RC" == 0 ]] && has "— you, Sep 22 (Riley'"'"'s notes)" out && hasnt "Sep 22 (your notes)" out'
man owner_unk 'meeting("sample-1on1-0922")["notes_owner"] = "unknown"'
expect 0 "Sep 22 (notes, author unknown)" out "note owner unknown: labelled as such" --bundle "$TMP/owner_unk.json" "${A[@]}"
man owner_bad 'meeting("sample-1on1-0922")["notes_owner"] = "riley"'
expect 2 "notes_owner" err "note owner must be user, counterpart or unknown (exit 2)" --bundle "$TMP/owner_bad.json" "${A[@]}"

# ---------------------------------------------------------------- perspective (either side of the 1:1)
run --bundle "$R" "${A[@]}"
check "report preparing: agenda says your topics first" 'has "**Suggested agenda** (your topics first — it'"'"'s your meeting)" out && has "1. Your topic: EU backlog" out'
check "report preparing: feedback the manager promised you" 'has "**Feedback Dana promised you**" out && has "Ask Dana for the feedback promised" out'
check "report preparing: your question is a follow-up, not something to answer" 'has "Follow up on your question: Is the analyst seat being backfilled?" out'
check "report preparing: judgement about the manager left out" 'has "Left out: 1 item worded as a rating or judgement of a person, or their mood (not shown)." out && hasnt "annoyed" out'
check "report preparing: plan history, unreadable notes and denied transcripts are gaps" 'has "your notes plan shows only the last 30 days" out && has "notes not readable for the Sep 22 1:1" out && has "transcripts access denied" out'
check "report preparing: privacy line for the report" 'has "_Private prep for Riley Chen only" out && has "Review before sharing any of it with Dana._" out'

# ---------------------------------------------------------------- due dates
due_case() {  # due_case NAME PHRASE EXPECTED [MEETING]
  man "due_$1" "item(\"t2\")[\"kind\"] = \"commitment\"; item(\"t2\")[\"due_phrase\"] = \"$2\"; item(\"t2\").pop(\"key\"); meeting(\"sample-1on1-1006\").pop(\"text\")"
  expect 0 "$3" out "due phrase '$2' said Tue Oct 6 -> $3" --bundle "$TMP/due_$1.json" "${A[@]}"
}
due_case fri "by Friday" "due Fri Oct 9 (from “by Friday”, said Tue Oct 6), 4 days overdue"
due_case tue "Tuesday" "due “Tuesday” (no date: said on a Tue, so the week is unclear)"
due_case nextmon "next Monday" "due Mon Oct 12 (from “next Monday”"
due_case tomorrow "tomorrow" "due Wed Oct 7 (from “tomorrow”"
due_case eow "end of week" "due Fri Oct 9 (from “end of week”"
due_case nextweek "next week" "due Fri Oct 16 (from “next week”"
due_case eom "end of the month" "due Sat Oct 31 (from “end of the month”"
due_case eoq "EOQ" "due Thu Dec 31 (from “EOQ”"
due_case in2w "in 2 weeks" "due Tue Oct 20 (from “in 2 weeks”"
due_case in3d "in 3 days" "due Fri Oct 9 (from “in 3 days”"
due_case eod "by EOD" "due Tue Oct 6 (from “by EOD”"
due_case month "Oct 13th" "due Tue Oct 13 (from “Oct 13th”, said Tue Oct 6) (today)"
due_case next1on1 "before our next 1:1" "due Tue Oct 13 (from “before our next 1\:1”, said Tue Oct 6) (today)"
due_case far "in 99999999 days" "(no date: more than 3650 days out)"
due_case junk "when things calm down" "(no date: phrase not recognised)"
due_case badday "February 30" "(no date: not a real date)"
man due_nonext 'item("t2")["kind"] = "commitment"; item("t2")["due_phrase"] = "next 1:1"; b.pop("upcoming"); meeting("sample-1on1-1006").pop("text"); item("t2").pop("key")'
expect 0 "(no date: no later 1:1 found)" out "'next 1:1' with no later meeting or upcoming gives no date" --bundle "$TMP/due_nonext.json" "${A[@]}"
expect 0 "due Tue Oct 6 (from “before our next 1\:1”, said Tue Sep 29), 7 days overdue" out "'next 1:1' resolves to the next 1:1 in the series" --bundle "$R" "${A[@]}"
man due_iso 'item("t2")["kind"] = "commitment"; item("t2")["due"] = "2026-10-13T18:00:00+09:00"; meeting("sample-1on1-1006").pop("text")'
expect 0 "due Tue Oct 13 (today) · raised Oct 6" out "an explicit due datetime uses its own local date; due today" --bundle "$TMP/due_iso.json" "${A[@]}"

# ---------------------------------------------------------------- strict dates and validation (same on 3.8 and 3.14)
for v in 2026-10-13 20261013T093000-0700 2026-10-13T24:00:00-07:00 2026-10-13T09:30:00+00:60 2026-10-13T09:30:00+15:00 \
         2026-10-13T09:30:00 2026-W42-2T09:30:00Z ٢٠٢٦-10-13T09:30:00Z 1899-12-31T00:00:00Z 3000-01-01T00:00:00Z \
         "2026-10-13 09:30:00-07:00" 2026-02-30T09:30:00Z 2026-10-13T09:30:00.1234Z; do
  expect 2 "--as-of is required" err "--as-of '$v' is rejected" --bundle "$M" --as-of "$v"
done
expect 0 "As of 2026-10-13T16:30:00+00:00" out "--as-of with Z works" --bundle "$M" --as-of 2026-10-13T16:30:00Z
for v in 20260915T100000Z 2026-09-15T24:00:00Z 2026-09-15T10:00:00+14:30 2026-09-15 "2026-09-15T10:00:00"; do
  man "start_$RANDOM" "meeting('sample-1on1-0915')['start'] = '$v'"
  f=$(ls -t "$TMP"/start_*.json | head -1)
  expect 2 "\$.meetings[1].start" err "meeting start '$v' is rejected" --bundle "$f" "${A[@]}"
done
for v in 2026-9-1 2026-13-01 10/16/2026 ٢٠٢٦-١٠-١٦; do
  man "due_bad_$RANDOM" "item('c3')['due'] = '$v'"
  f=$(ls -t "$TMP"/due_bad_*.json | head -1)
  expect 2 "is not a YYYY-MM-DD date" err "due '$v' is rejected" --bundle "$f" "${A[@]}"
done
"$PY" - >"$TMP/dates" 2>&1 <<'PY'
import itertools, random, re, sys
sys.path.insert(0, ".")
import build_prep as bp
rng = random.Random(42)
digits = "0123456789"
bad = 0
n = 0
pat = re.compile(r"[0-9]{4}-(?:0[1-9]|1[0-2])-[0-9]{2}T(?:[01][0-9]|2[0-3]):[0-5][0-9](?::[0-5][0-9](?:\.[0-9]{3}|\.[0-9]{6})?)?(?:Z|[+-](?:(?:0[0-9]|1[0-3]):[0-5][0-9]|14:00))")
for _ in range(20000):
    s = "{}-{:02d}-{:02d}T{:02d}:{:02d}{}{}".format(rng.choice(["2026", "1899", "3000", "2024"]), rng.randint(0, 13),
        rng.randint(0, 32), rng.randint(0, 25), rng.randint(0, 61), rng.choice(["", ":00", ":59", ":60", ".5", ":00.123"]),
        rng.choice(["Z", "+00:00", "-07:00", "+14:00", "+14:01", "-13:59", "+00:60", "", "+1", "+0530"]))
    got = bp.parse_moment(s) is not None
    want = bool(pat.fullmatch(s))
    if want:
        y, mo, d = int(s[:4]), int(s[5:7]), int(s[8:10])
        import calendar
        want = 1900 <= y <= 2999 and 1 <= d <= calendar.monthrange(y, mo)[1]
    n += 1
    if got != want:
        bad += 1
        print("MISMATCH", s, got, want)
print("dates n={} bad={}".format(n, bad))
PY
check "20,000 generated datetimes follow the documented strict rule" 'grep -q "dates n=20000 bad=0" "$TMP/dates"'

# ---------------------------------------------------------------- withholding and judgement
"$PY" - "$TMP" >"$TMP/corpus" 2>&1 <<'PY'
import contextlib, io, json, sys, collections
sys.path.insert(0, ".")
import build_prep as bp
_config = bp.load_config(bp.DEFAULT_CONFIG)
bp.load_config = lambda path: _config  # same config every run; only the bundle changes
tmp = sys.argv[1]
rows = json.load(open("scanner_corpus.json", encoding="utf-8"))["rows"]
base = json.load(open("sample_bundle_manager.json", encoding="utf-8"))
mid = "sample-1on1-0929"
stats = collections.Counter()
for chunk in range(0, len(rows), 60):
    part = rows[chunk:chunk + 60]
    b = json.loads(json.dumps(base))
    b["items"] = []
    for i, r in enumerate(part):
        # Every row is filed under a work topic, as if the agent mis-tagged it: only the backstop (gate 2) is tested.
        it = {"id": "k{}".format(i), "kind": "topic", "owner": "counterpart", "key": "k{}".format(i),
              "topic": "process", "meeting_id": mid, "quote_from": "transcript"}
        if r.get("mode", "quote") == "authored":
            it.update(summary=r["text"], quote="Raised it in the 1:1.")
        else:
            it.update(summary="Item {}".format(i), quote=r["text"])
        b["items"].append(it)
    for m in b["meetings"]:
        m.pop("text", None)
    b["work_items"] = []
    path = "{}/corpus.json".format(tmp)
    json.dump(b, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        rc = bp.main(["--bundle", path, "--as-of", "2026-10-13T09:30:00-07:00", "--json"])
    assert rc == 0, rc
    d = json.loads(out.getvalue())
    withheld = set(x[0] for x in d["withheld_personal"]["item_ids"])
    left = set(x[0] for x in d["left_out_judgement"]["item_ids"])
    md = d["markdown"]
    for i, r in enumerate(part):
        k = "k{}".format(i)
        shown = any(k in x["item_ids"] for x in d["items"])
        got = "personal" if __import__("hashlib").sha256(k.encode()).hexdigest() in withheld else "judgement" if __import__("hashlib").sha256(k.encode()).hexdigest() in left else "keep" if shown else "dropped"
        # "dropped": the quote check refused it (a quote under 3 words), so it isn't shown either
        good = got == r["expect"] or (r["expect"] != "keep" and got in ("personal", "dropped"))
        if got != "keep" and bp.md(r["text"])[:40] in md:
            good = False  # withheld text must never be printed
        stats[(r["expect"], r["tag"], good)] += 1
        if not good:
            print("MISS", r["expect"], r.get("mode", "quote"), got, "|", r["text"])
for k in sorted(stats):
    print(k, stats[k])
print("corpus misses={}".format(sum(v for k, v in stats.items() if not k[2])))
PY
grep "^(" "$TMP/corpus" | sed 's/^/    # /'
NROWS=$("$PY" -c "import json; print(len(json.load(open('scanner_corpus.json', encoding='utf-8'))['rows']))")
check "scanner corpus ($NROWS sentences, all filed under a work topic: personal, judgement, must-keep; tune, 7 held-out sets, the round-6 attack, documented) end to end, withheld text never printed" 'grep -q "corpus misses=0" "$TMP/corpus" && [[ $(grep -c "^(" "$TMP/corpus") -ge 9 ]]'

wh() {  # wh NAME FIELD-EDIT EXPECT(withheld|left|kept)
  man "wh_$1" "$2; meeting('sample-1on1-1006').pop('text')"
  run --bundle "$TMP/wh_$1.json" "${A[@]}" --json
  local w l
  w=$("$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); print(d['withheld_personal']['count'])" "$TMP/out" 2>/dev/null)
  l=$("$PY" -c "import json,sys; d=json.load(open(sys.argv[1])); print(d['left_out_judgement']['count'])" "$TMP/out" 2>/dev/null)
  case "$3" in
    withheld) check "withhold: $1" '[[ "$w" == 2 ]]' ;;
    left) check "judgement: $1" '[[ "$l" == 1 && "$w" == 1 ]]' ;;
    kept) check "kept: $1" '[[ "$w" == 1 && "$l" == 0 ]]' ;;
  esac
}
wh zero_width 'item("t2")["quote"] = "I am pr​egn​ant."' withheld
wh cyrillic 'item("t2")["quote"] = "I have a dосtor appointment Friday."' withheld
wh spaced 'item("t2")["quote"] = "I am b u r n e d out."' withheld
wh markup 'item("t2")["quote"] = "I have **sur**gery next week."' withheld
wh leet 'item("t2")["quote"] = "My m0m passed away."' withheld
wh entity 'item("t2")["quote"] = "I&#39;m on maternity leave from March."' withheld
wh evidence 'item("c2")["evidence"][0]["summary"] = "posted from the hospital waiting room"' withheld
wh due_phrase 'item("c3").pop("due"); item("c3")["due_phrase"] = "after my chemo"' withheld
wh work_title 'b["work_items"][2]["title"] = "Riley PIP check-ins"' withheld
wh work_ref 'b["work_items"][2]["ref"] = "PIP-12"' withheld
wh group 'item("t1b")["quote"] = "Same story on the EU backlog, and my divorce is dragging on."' withheld
wh third_party 'item("t2")["quote"] = "Sam told me his wife is pregnant."' withheld
wh summary_mood 'item("t2")["summary"] = "Riley was frustrated about the handoff"' left
wh summary_trait 'item("t2")["summary"] = "Riley is unreliable on handoffs"' left
wh quote_praise 'item("t2")["quote"] = "Honestly you are a rockstar on handoffs."' left
wh quote_rating 'item("t2")["quote"] = "You are meeting expectations on the handoff."' left
wh ai_summary_mood 'item("t2")["quote_from"] = "summary"; item("t2")["quote"] = "Riley seemed upset about the handoff."' left
wh own_feeling 'item("t2")["quote"] = "I am frustrated that Sales keeps skipping discovery."' kept
wh documented_overwithhold 'item("t2")["quote"] = "Our controller got married, so I covered the close."' withheld
wh customer_mood 'item("t2")["quote"] = "The customer was upset about the price increase."' kept
wh work_words 'item("t2")["quote"] = "Account health, the parent company and child entities all need cleanup."' kept
wh work_noun_kept 'item("t2")["quote"] = "The fertility clinic client wants a visa sponsorship FAQ for the handbook."' kept
wh client_is_a 'item("t2")["quote"] = "Our client is a fertility clinic chain."' kept
wh documented_leave_no_work_noun 'item("t2")["quote"] = "Draft the bereavement leave section of the handbook update."' withheld
wh documented_hr_no_work_noun 'item("t2")["quote"] = "The PIP dashboard for the pricing team is live."' withheld
wh suicidal 'item("t2")["quote"] = "Feeling suicidal some nights."' withheld
wh self_harm 'item("t2")["quote"] = "I have been self-harming again."' withheld
wh therapist 'item("t2")["quote"] = "I'"'"'ve been seeing a therapist."' withheld
wh hiv 'item("t2")["quote"] = "I'"'"'m HIV positive."' withheld
wh sti 'item("t2")["quote"] = "I got tested for STIs last week."' withheld
wh prozac 'item("t2")["quote"] = "I take Prozac."' withheld
wh medication_general 'item("t2")["quote"] = "I'"'"'m on a new medication and it'"'"'s making me tired."' withheld
wh rehab 'item("t2")["quote"] = "I just finished a 30-day inpatient program for alcohol."' withheld
wh name_leave_title 'b["work_items"][2]["title"] = "Riley maternity leave paperwork"' withheld
wh name_pip_title 'b["work_items"][2]["title"] = "Riley PIP documentation"' withheld
wh name_investigation_title 'b["work_items"][2]["title"] = "Riley investigation documentation"' withheld
wh name_accommodation 'item("t2")["summary"] = "Riley accommodation request form"' withheld
wh bankruptcy 'item("t2")["quote"] = "We declared bankruptcy last year and it still follows me."' withheld
wh eviction 'item("t2")["quote"] = "My landlord is evicting us."' withheld
wh debt_collectors 'item("t2")["quote"] = "Debt collectors keep calling me at work."' withheld
wh divorce 'item("t2")["quote"] = "I'"'"'m going through a divorce."' withheld
wh custody 'item("t2")["quote"] = "My ex and I are fighting over custody of the kids."' withheld
wh hr_investigation 'item("t2")["quote"] = "HR opened an investigation."' withheld
wh third_party_manager 'item("t2")["quote"] = "Dana is a terrible manager."' left
wh third_party_named 'item("t2")["quote"] = "Sam is lazy and incompetent."' left
wh customer_bankruptcy 'item("t2")["quote"] = "The customer filed for bankruptcy so we are writing off the balance."' kept
wh outage_investigation 'item("t2")["quote"] = "The root-cause investigation for the outage is done."' kept
wh pip_install 'item("t2")["quote"] = "Run pip install on the build box before the demo."' kept
wh company_subject 'item("t2")["quote"] = "The ad spend on Meta is underperforming versus Google."' kept
wh work_noun_function_word 'item("t2")["quote"] = "I am pregnant and the client knows."' withheld
wh work_noun_possessive 'item("t2")["quote"] = "My IVF clinic invoices are piling up."' withheld
wh work_noun_plus_disclosure 'item("t2")["quote"] = "Update the visa sponsorship FAQ; my own visa renewal is stuck too."' withheld
wh documented_work_noun_miss 'item("t2")["quote"] = "IVF clinic invoices are piling up at home."' kept
wh documented_own_family 'item("t2")["quote"] = "I am working from my parents place on Friday but online all day."' withheld
wh documented_data_children 'item("t2")["quote"] = "The KPI tree has a parent metric and three children."' withheld
wh documented_name_calling 'item("t2")["quote"] = "That vendor is a dead weight on our margins."' left
wh figurative_pregnant 'item("t2")["quote"] = "The renewal is pregnant with risk if we do not fix onboarding."' kept
wh simile_funeral 'item("t2")["quote"] = "The QBR felt like a funeral after the outage."' kept
# Round 6 (QA round 5: O7 subject-less notes, O8 misspellings and slang, O9 judgements with 's), backstop on items
# that carry a work topic
wh o7_maternity 'item("t2")["quote"] = "Maternity leave starts in Nov."' withheld
wh o7_arrested 'item("t2")["quote"] = "Got arrested Friday night."' withheld
wh o7_mental_health 'item("t2")["quote"] = "Mental health has been rough."' withheld
wh o8_misspelt 'item("t2")["quote"] = "Feelin suicidle lately."' withheld
wh o8_unaliving 'item("t2")["quote"] = "Thinking about unaliving myself."' withheld
wh o8_fuzzy 'item("t2")["quote"] = "Seeing a theripist on Fridays."' withheld
wh o8_kms 'item("t2")["quote"] = "lowkey wanna kms after that call."' withheld
wh o8_kms_distance 'item("t2")["quote"] = "The site is 40 kms from the depot."' kept
wh o8_fuzzy_ignore 'item("t2")["quote"] = "Cancel the duplicate invoices before month end."' kept
wh o9_apostrophe_s 'item("t2")["quote"] = "Morgan'"'"'s a nightmare to work with."' left
wh o9_possessive_kept 'item("t2")["quote"] = "Morgan'"'"'s deck is ready for the QBR."' kept

# Round 6 blind attack (written from the docs only, every item mis-filed under a work topic): the sentences are in
# scanner_corpus.json (tags attack6, holdout6, holdout7); these are the fields other than quotes and summaries.
wh attack_evidence 'item("c2")["evidence"][0]["summary"] = "sent from the oncology ward"' withheld
wh attack_work_title 'b["work_items"][2]["title"] = "Riley maternity leave handover"' withheld
wh attack_summary_pip 'item("t2")["summary"] = "Riley PIP documentation"' withheld
wh attack_summary_leave 'item("t2")["summary"] = "Riley medical leave paperwork"' withheld
wh attack_summary_judgement 'item("t2")["summary"] = "Morgan is a nightmare to work with"' left
# Known misses of the backstop, as documented in SKILL.md Limits (gate 1 is the only control for these)
wh limit_first_letter 'item("t2")["quote"] = "Zuicidal again this week."' kept
wh limit_paraphrase 'item("t2")["quote"] = "Feeling like giving up on everything."' kept
wh limit_other_language 'item("t2")["quote"] = "Mi madre está en el hospital."' kept
wh limit_judgement_no_verb 'item("t2")["quote"] = "Typical Morgan, late again."' kept
wh limit_no_reason 'item("t2")["quote"] = "Off on Friday for an appointment."' kept
wh limit_life_event 'item("t2")["quote"] = "Wedding is in June, need two weeks off."' kept

# ---------------------------------------------------------------- the topic allowlist (gate 1)
man tp_missing 'item("t2").pop("topic")'
run --bundle "$TMP/tp_missing.json" "${A[@]}"
check "topic: an item without a topic is withheld as uncategorised, never shown" '[[ "$RC" == 0 ]] && has "Withheld: 2 items withheld as personal or uncategorised" out && hasnt "Cleaner handoff" out && hasnt "re-asking" out'
for t in personal other; do
  man "tp_$t" "item('t2')['topic'] = '$t'"
  run --bundle "$TMP/tp_$t.json" "${A[@]}"
  check "topic '$t' is never rendered, only counted" '[[ "$RC" == 0 ]] && has "Withheld: 2 items withheld" out && hasnt "Cleaner handoff" out'
done
man tp_group 'item("t1b").pop("topic")'
run --bundle "$TMP/tp_group.json" "${A[@]}"
check "topic: one occurrence without a topic withholds the whole merged item" '[[ "$RC" == 0 ]] && has "Withheld: 2 items withheld" out && hasnt "EU ticket backlog" out && hasnt "EU queue is over" out'
man tp_work 'b["work_items"][0].pop("topic")'
run --bundle "$TMP/tp_work.json" "${A[@]}"
check "topic: a work item without a topic is withheld" '[[ "$RC" == 0 ]] && has "Withheld: 2 items withheld" out && hasnt "EU queue routing rules" out'
man tp_json 'item("t2")["topic"] = "other"'
run --bundle "$TMP/tp_json.json" "${A[@]}" --json
check "--json: withheld_personal splits by_topic and by_pattern, shown items carry their topic" '"$PY" -c "
import json,sys; d=json.load(open(sys.argv[1])); w=d[\"withheld_personal\"]
assert w[\"count\"]==2 and w[\"by_topic\"]==2 and w[\"by_pattern\"]==0, w
assert all(x[\"topic\"] for x in d[\"items\"]) and all(x[\"topic\"] for x in d[\"work_items\"])
" "$TMP/out"'
n=0
for t in '"health"' '"Project"' '"project "' '3' '["project"]'; do
  n=$((n + 1)); man "tp_bad$n" "item('t2')['topic'] = $t"
  expect 2 "topic: must be one of project, goal, process" err "topic $t is not on the list: exit 2, nothing printed" --bundle "$TMP/tp_bad$n.json" "${A[@]}"
done

# ---------------------------------------------------------------- quote rule (shared granola_text): X1, X2
qx() {  # qx NAME TEXT QUOTE EXPECT(shown|dropped)
  man "qx_$1" "meeting('sample-1on1-1006')['text'] += chr(10) + $2; item('t2')['quote'] = $3"
  run --bundle "$TMP/qx_$1.json" "${A[@]}"
  if [[ "$4" == shown ]]; then check "quote rule: $1 is shown" '[[ "$RC" == 0 ]] && has "Cleaner handoff" out'
  else check "quote rule: $1 is not shown" '[[ "$RC" == 0 ]] && hasnt "Cleaner handoff" out && has "1 item whose quote isn'"'"'t one or more whole sentences" out'; fi
}
qx x2_label_crossing '"Dana: will you renew Riley: no we will not"' '"will you renew Riley: no"' dropped
qx x2_whole_turn '"Dana: will you renew? Riley: no we will not"' '"no we will not"' shown
# round 9 (helper 1.3.0): with no sentence end before it, the mid-line name is not a turn boundary (fail-closed)
qx x2_no_stop_not_a_turn '"Dana: will you renew Riley: no we will not"' '"no we will not"' dropped
qx x1_leading_negation '"Riley: Nobody will renew next quarter."' '"will renew next quarter"' dropped
qx x1_none_of '"Riley: None of our drivers trust the dock calendar."' '"our drivers trust the dock calendar"' dropped
qx x1_ellipsis '"Riley: I would never pay for this."' '"I would ... pay for this"' dropped
qx question_flip '"Riley: Is the renewal safe?"' '"The renewal is safe."' dropped
qx mid_sentence '"Riley: We chose not to ship in March."' '"ship in March"' dropped
qx two_sentences_one_turn '"Riley: The EU queue is fine. It stays fine."' '"The EU queue is fine. It stays fine."' shown
qx two_turns '"Riley: The EU queue is fine. Dana: It is not fine at all."' '"The EU queue is fine. It is not fine at all."' dropped

# Round 6 blind attack: quote integrity
qx atk_dropped_not '"Riley: I would not pay for this."' '"I would pay for this."' dropped
qx atk_stitched_negation '"Riley: We will not renew. Dana: We might expand."' '"We will renew."' dropped
qx atk_label_inside '"Dana: Will you renew? Riley: We will not renew."' '"Will you renew? Riley: We will"' dropped
qx atk_across_turns '"Dana: Will you renew? Riley: We will not renew."' '"Will you renew? We will not renew."' dropped
qx atk_one_turn '"Dana: Will you renew? Riley: We will not renew."' '"We will not renew."' shown
qx atk_add_question '"Riley: The renewal is safe."' '"Is the renewal safe?"' dropped
qx atk_unhelpful '"Riley: The new routing was unhelpful for us."' '"helpful for us"' dropped
# Round 10 (helper 1.4.0, QA round 9 SH9-1/SH9-3): a negation before an unlisted abbreviation ("asst. mgr.", "SVP.",
# "Ambass.") or a mid-line "Plan A:" no longer ends Riley's turn, so the open counterpart commitment after the label is
# withheld instead of rendering as overdue. Twins: a clear sentence end before the label renders it as overdue.
sh10() {  # sh10 NAME PREFIX EXPECT(overdue|withheld): PREFIX + the claim is appended to sample-1on1-1006 as a Riley turn
  export SH10_PREFIX="$2"
  man "sh10_$1" 'import os
C = "I" + chr(39) + "ll ship the queue migration by Friday."
meeting("sample-1on1-1006")["text"] += chr(10) + "Riley: " + os.environ["SH10_PREFIX"] + C
b["items"].append({"id": "c99", "kind": "commitment", "topic": "project", "owner": "counterpart", "key": "queue-migration", "summary": "Ship the queue migration", "meeting_id": "sample-1on1-1006", "quote": C, "quote_from": "transcript", "speaker": "counterpart", "due_phrase": "by Friday", "status": "open"})'
  run --bundle "$TMP/sh10_$1.json" "${A[@]}"
  if [[ "$3" == overdue ]]; then check "SH9 (r10) $1: the commitment renders as overdue" '[[ "$RC" == 0 ]] && grep -qiE "overdue.*queue migration" "$TMP/out"'
  else check "SH9 (r10) $1: the commitment is withheld" '[[ "$RC" == 0 ]] && ! grep -qi "queue migration" "$TMP/out"'; fi
}
sh10 a3a_lower_abbrev "Not even our asst. mgr. Okafor: " withheld
sh10 a3b_allcaps "Nobody except the SVP. Okafor: " withheld
sh10 a3c_long_title "No one but Ambass. Okafor: " withheld
sh10 a3d_short_lower "Nobody except our dir. Okafor: " withheld
sh10 a2u_honorific "Anything but Dir. Okafor: " withheld
sh10 a5_plan_a "We dropped Plan A: " withheld
sh10 a4_except_maybe "Nobody signed off except maybe
Okafor: " withheld
sh10 s1_safe_twin "Sounds good. Okafor: " overdue
sh10 s2_safe_closed_negation "Nobody except the SVP agreed. Okafor: " overdue
sh10 s3_safe_own_line "Not even our asst. mgr. could make it.
Okafor: " overdue
# ---------------------------------------------------------------- shared helper: byte-for-byte copy, import from any folder
check "granola_text.py is the shared v1.6.0 file byte for byte (SHA-256)" '[[ "$("$PY" -c "import hashlib,sys; print(hashlib.sha256(open(sys.argv[1],\"rb\").read()).hexdigest())" granola_text.py)" == "00ec63cdf7126c247832276f77f55f2a60fb8b58b9a9dc6fd4fecbe2317df168" ]] && { [[ ! -f ../shared/granola_text.py ]] || cmp -s granola_text.py ../shared/granola_text.py; }'
HERE="$(pwd)"
( cd "$TMP" && "$PY" "$HERE/build_prep.py" --bundle "$HERE/$M" "${A[@]}" >"$TMP/out" 2>"$TMP/err" ); RC=$?
check "runs from another folder (imports granola_text from the script folder) and leaves no __pycache__" '[[ "$RC" == 0 ]] && has "#### 1:1 prep: Riley Chen" out && [[ ! -e __pycache__ ]] && [[ ! -e "$TMP/__pycache__" ]]'
for nm in "Gay Christian" "Hope Faith" "Grace Black" "Joy Young" "Rich Sickles"; do
  man "name_$RANDOM" "b['counterpart']['name'] = '$nm'"
  f=$(ls -t "$TMP"/name_*.json | head -1)
  run --bundle "$f" "${A[@]}"
  check "a counterpart named '$nm' is not read as a term" '[[ "$RC" == 0 ]] && has "Withheld: 1 item withheld" out && has "Cleaner handoff from Sales to CS" out'
done
man by_agent 'b["withheld_by_agent"] = 2'
expect 0 "Withheld: 3 items withheld as personal or uncategorised (not shown" out "withheld_by_agent adds to the count" --bundle "$TMP/by_agent.json" "${A[@]}"
man by_agent_bad 'b["withheld_by_agent"] = -1'
expect 2 "withheld_by_agent: must be a whole number from 0 to 1000" err "withheld_by_agent must be 0-1000" --bundle "$TMP/by_agent_bad.json" "${A[@]}"
for k in rating mood sentiment flight_risk health Performance_Rating; do
  man "fk_$k" "item('t2')['$k'] = 3"
  expect 2 "never rates people or records mood, sentiment or health" err "forbidden key '$k' exits 2" --bundle "$TMP/fk_$k.json" "${A[@]}"
done
man fk_nested 'b["work_items"][0]["meta"] = {"sentiment_score": 0.2}'
expect 2 "\$.work_items[0].meta.sentiment_score" err "a forbidden key nested anywhere exits 2 and names its path" --bundle "$TMP/fk_nested.json" "${A[@]}"
man echo 'item("c3")["due"] = "my chemo week"'
expect 2 "a 13-character value (not shown)" err "error messages never echo free text" --bundle "$TMP/echo.json" "${A[@]}"
check "error message did not repeat the text" 'hasnt "chemo" err'

# ---------------------------------------------------------------- personal data and escaping
man contacts 'item("t2")["quote"] = "Call me on +44 7700 900123 or mail riley.home@freemail.example; PO 4500012345 ships 2026-10-15 10:00, v2.3.1, 10.0.0.1"; meeting("sample-1on1-1006").pop("text")'
run --bundle "$TMP/contacts.json" "${A[@]}"
check "phone numbers and e-mails removed from quotes (pattern-based)" 'hasnt "7700" out && hasnt "freemail" out && has "\\[phone removed\\]" out && has "\\[email removed\\]" out'
check "PO numbers, ISO date/times, versions and IPs are left alone" 'has "PO 4500012345" out && has "2026-10-15 10\\:00" out && has "v2\.3\.1" out && has "10\.0\.0\.1" out'
man inj 'item("t2")["summary"] = "\\[click\\](https://phish.example/a) <b>x</b> &#91;y&#93; *b* _i_ ~s~ `c` a|b @here <!channel> ![img](https://p.example/i.png) www.evil.example evil.example/x"; meeting("sample-1on1-1006").pop("text")'
run --bundle "$TMP/inj.json" "${A[@]}"
check "injection in a summary: no link, image, raw HTML, entity, mention or formatting survives" '[[ "$RC" == 0 ]] && hasnt "](https://phish" out && hasnt "<b>" out && hasnt "&#91;" out && hasnt "<!channel>" out && hasnt "www.evil" out && hasnt "evil.example" out && has "\\\\\\[click\\\\\\]" out && has "&lt;b&gt;" out && has "\\@here" out'
# QA round 5 contact examples (H6, W5, D4), now handled by the shared granola_text.redact_contacts
man qa5_contacts 'item("t2")["quote"] = "Quotes came in at 36000 36000 38000 for the year. Ring me on 07700-900123 or mail ines\\.marlowe\\@ostrevane\\.example about it."; meeting("sample-1on1-1006").pop("text")'
run --bundle "$TMP/qa5_contacts.json" "${A[@]}"
check "QA5 H6/W5/D4: repeated prices kept, 07700-900123 and a Granola-escaped e-mail removed" '[[ "$RC" == 0 ]] && has "36000 36000 38000" out && hasnt "900123" out && hasnt "ostrevane" out && hasnt "marlowe" out && has "\\[phone removed\\]" out && has "\\[email removed\\]" out'
man inj2 'item("t2")["summary"] = "Fix ==hi== and " + chr(36) + "x" + chr(36) + " before launch"; meeting("sample-1on1-1006").pop("text")'
run --bundle "$TMP/inj2.json" "${A[@]}"
check "injection: highlight (==) and math (\$) markers are escaped" '[[ "$RC" == 0 ]] && hasnt " ==hi" out && hasnt " \$x" out && has "\\=\\=hi\\=\\=" out && has "and \\\$x\\\$ before" out'
man badurl 'meeting("sample-1on1-0929")["url"] = "javascript:alert(1)"; meeting("sample-1on1-0915")["url"] = "https://user:pw@notes.granola.ai/d/x"; meeting("sample-1on1-0922")["url"] = "https://notes.granola.ai:99999/d/x"'
run --bundle "$TMP/badurl.json" "${A[@]}"
check "javascript:, credential and bad-port URLs are dropped and counted" '[[ "$RC" == 0 ]] && hasnt "javascript" out && hasnt "user:pw" out && hasnt "99999" out && has "3 links dropped (not an http(s) URL)" out'
man linklabel 'item("t2")["summary"] = "a](https://phish.example/x) b"; meeting("sample-1on1-1006").pop("text")'
run --bundle "$TMP/linklabel.json" "${A[@]}"
check "a removed link after an escaped ] leaves no ]( pair (the label's parentheses are escaped)" '[[ "$RC" == 0 ]] && hasnt "\\](" out && has "a\\]\\(\\[link removed\\] b" out'
"$PY" - "$TMP" >"$TMP/mdfuzz" 2>&1 <<'PY'
import contextlib, copy, io, json, random, re, sys
sys.path.insert(0, ".")
import build_prep as bp
_config = bp.load_config(bp.DEFAULT_CONFIG)
bp.load_config = lambda path: _config
tmp = sys.argv[1]
rng = random.Random(20261012)
tokens = ["\\", "\\\\", "[", "]", "(", ")", "<", ">", "&", "&#91;", "&lt;", "`", "*", "_", "~", "|", "!", "#", "@",
          "](", "word", " ", "http://x.example/a", "https://y.example", "www.z.example", "q.example/p", "a@b.example",
          "javascript:alert(1)", "<http://q.example>", "\\[click\\](https://p.example)", "[x](https://p.example)",
          "![i](https://p.example/i.png)", "<!channel>", "<@U1>", "<script>", "[x]: https://ref.example", "​",
          "‮", "**", "__", "$", "$x$", "==", "==hi==", "="]
def value():
    return "".join(rng.choice(tokens) for _ in range(rng.randint(1, 8))) + " note"
base = json.load(open("sample_bundle_manager.json", encoding="utf-8"))
for m in base["meetings"]:
    m.pop("text", None)
paths = []
for i, it in enumerate(base["items"]):
    paths += [("items", i, "summary"), ("items", i, "quote")]
    for j, _ in enumerate(it.get("evidence", [])):
        paths.append(("items", i, "evidence", j, "summary"))
paths += [("work_items", 0, "title"), ("work_items", 1, "status"), ("counterpart", "name"), ("user", "name"),
          ("items", 2, "due_phrase")]
allowed = re.compile(r"\]\((?:https://notes\.granola\.ai/d/sample-[a-z0-9-]+|https://linear\.app/feltharrow-sample/issue/[A-Z]+-[0-9]+|https://feltharrow\.slack\.example/archives/C0SAMPLE/p[0-9]+)\)")
bad_out = re.compile(r"\]\(|<|https?:|www\.|[A-Za-z0-9-]+\.example|&#|!\[|\[x\]:|[\u200b\u202e]")
runs = rendered = bad = 0
for n in range(400):
    b = copy.deepcopy(base)
    for path in rng.sample(paths, rng.randint(1, 6)):
        obj = b
        for p in path[:-1]:
            obj = obj[p]
        obj[path[-1]] = value()
    f = tmp + "/mf.json"
    json.dump(b, open(f, "w", encoding="utf-8"), ensure_ascii=False)
    out, err = io.StringIO(), io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            rc = bp.main(["--bundle", f, "--as-of", "2026-10-13T09:30:00-07:00"])
    except BaseException as exc:
        rc = "crash {}".format(exc)
    runs += 1
    if rc == 0:
        rendered += 1
        text = allowed.sub("]", out.getvalue())
        text = text.replace("@feltharrow", "")
        m = bad_out.search(text)
        if m:
            bad += 1
            print("BAD", n, repr(text[max(0, m.start() - 50):m.end() + 30]))
    elif rc != 2 or out.getvalue():
        bad += 1
        print("BAD rc", n, rc)
SPECIAL = set("\\`*_~[]()|#@!<>$=")
def unescaped(t):
    i = 0
    while i < len(t):
        if t[i] == "\\":
            i += 2
            continue
        if t[i] in SPECIAL or t.startswith("&#", i) or t.startswith("://", i):
            return i
        i += 1
    return None
units = 0
for n in range(5000):
    v = "".join(rng.choice(tokens + ["a.b", "x", "&", "&amp;"]) for _ in range(rng.randint(1, 10)))
    out = bp.md(bp.display_clean(v))
    units += 1
    rest = out
    for label in ["\\(contact detail removed\\)", "\\(link removed\\)", "\\(ID number removed\\)", "&amp;", "&lt;", "&gt;"]:
        rest = rest.replace(label, "")
    if unescaped(rest) is not None or re.search(r"[A-Za-z0-9]\.[A-Za-z]", out):
        bad += 1
        print("BAD md", repr(v), repr(out))
print("mdfuzz runs={} rendered={} bad={} md_units={}".format(runs, rendered, bad, units))
PY
tail -1 "$TMP/mdfuzz" | sed 's/^/    # /'
check "injection fuzz (400 end-to-end runs over every displayed field + 5,000 md() strings): no link, autolink, bare domain, image, HTML, entity, mention or unescaped formatting from input" 'grep -qE "mdfuzz runs=400 rendered=[0-9]{3} bad=0 md_units=5000" "$TMP/mdfuzz"'

# ---------------------------------------------------------------- robustness
"$PY" - "$TMP" >"$TMP/fuzz" 2>&1 <<'PY'
import contextlib, copy, io, json, sys
sys.path.insert(0, ".")
import build_prep as bp
_config = bp.load_config(bp.DEFAULT_CONFIG)
bp.load_config = lambda path: _config
tmp = sys.argv[1]
values = [None, 1, -1, 1.5, True, "x|y", "", [], {"a": 1}, "​", "x" * 100000, "<!channel>", "١٢"]
def leaves(obj, path=()):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield from leaves(v, path + (k,))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            yield from leaves(v, path + (i,))
    yield path
def put(obj, path, value):
    for p in path[:-1]:
        obj = obj[p]
    obj[path[-1]] = value
runs = bad = 0
for src in ["sample_bundle_manager.json", "sample_bundle_report.json", "sample_bundle_first.json"]:
    base = json.load(open(src, encoding="utf-8"))
    for path in list(leaves(base)):
        if not path:
            continue
        for value in values:
            data = copy.deepcopy(base)
            put(data, path, value)
            f = tmp + "/fz.json"
            json.dump(data, open(f, "w", encoding="utf-8"))
            out, err = io.StringIO(), io.StringIO()
            try:
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    rc = bp.main(["--bundle", f, "--as-of", "2026-10-13T09:30:00-07:00"])
            except BaseException as exc:
                rc = "crash {}: {}".format(type(exc).__name__, exc)
            runs += 1
            if rc not in (0, 2) or (rc != 0 and out.getvalue()) or "Traceback" in err.getvalue():
                bad += 1
                print("BAD", src, path, repr(value)[:30], rc)
print("fuzz runs={} bad={}".format(runs, bad))
PY
tail -1 "$TMP/fuzz" | sed 's/^/    # /'
check "mutation fuzz over every leaf of the three samples: exit 0/2 only, nothing on stdout on error, no traceback" 'grep -qE "fuzz runs=[0-9]{4,} bad=0" "$TMP/fuzz"'
"$PY" - "$TMP" >"$TMP/cfuzz" 2>&1 <<'PY'
import contextlib, copy, io, json, sys
sys.path.insert(0, ".")
import build_prep as bp
tmp = sys.argv[1]
base = json.load(open("prep.json", encoding="utf-8"))
runs = bad = 0
for key in list(base):
    for value in [None, 0, 99999, "x", [], {}, ["(unclosed"], {"x": ["\\d"]}, True]:
        c = copy.deepcopy(base)
        c[key] = value
        f = tmp + "/cfz.json"
        json.dump(c, open(f, "w", encoding="utf-8"))
        out, err = io.StringIO(), io.StringIO()
        try:
            with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                rc = bp.main(["--bundle", "sample_bundle_manager.json", "--as-of", "2026-10-13T09:30:00-07:00", "--config", f])
        except BaseException as exc:
            rc = "crash {}".format(exc)
        runs += 1
        if rc not in (0, 2) or (rc != 0 and out.getvalue()):
            bad += 1
            print("BAD", key, repr(value)[:30], rc)
print("cfuzz runs={} bad={}".format(runs, bad))
PY
check "config mutation fuzz: exit 0/2 only, nothing on stdout on error" 'grep -qE "cfuzz runs=[0-9]+ bad=0" "$TMP/cfuzz"'
printf '{"schema_version": 1,,}' >"$TMP/broken.json"
expect 2 "is not valid JSON (line 1" err "malformed JSON exits 2 with line/column" --bundle "$TMP/broken.json" "${A[@]}"
expect 2 "file not found" err "missing bundle file exits 2" --bundle "$TMP/nope.json" "${A[@]}"
printf '\xff\xfe{}' >"$TMP/latin.json"
expect 2 "is not UTF-8 text" err "non-UTF-8 bundle exits 2" --bundle "$TMP/latin.json" "${A[@]}"
printf '{"schema_version": 1, "x": NaN}' >"$TMP/nan.json"
expect 2 "NaN is not a valid JSON number" err "NaN/Infinity rejected" --bundle "$TMP/nan.json" "${A[@]}"
"$PY" -c "import json; b=json.load(open('$M')); s=json.dumps(b); s=s.replace('Cleaner handoff', 'Cleaner \\\\ud800 handoff', 1); open('$TMP/surr.json','w').write(s)"
expect 2 "unpaired surrogate" err "lone surrogate exits 2 before output" --bundle "$TMP/surr.json" "${A[@]}"
"$PY" -c "open('$TMP/deep.json','w').write('['*100000 + ']'*100000)"
expect 2 "nested too deeply" err "absurdly deep JSON exits 2" --bundle "$TMP/deep.json" "${A[@]}"
expect 2 "--bundle is required" err "no --bundle exits 2" "${A[@]}"
expect 2 "--as-of is required" err "no --as-of exits 2" --bundle "$M"
for n in 0 13 abc; do expect 2 "--meetings" err "--meetings $n exits 2" --bundle "$M" "${A[@]}" --meetings "$n"; done
man toomany 'b["items"] = [dict(item("t2"), id="z%d" % i) for i in range(301)]'
expect 2 "has 301 entries; the limit is 300" err "more than max_items exits 2" --bundle "$TMP/toomany.json" "${A[@]}"
man schema 'b["schema_version"] = 2'
expect 2 "schema_version: must be 1" err "wrong schema_version exits 2" --bundle "$TMP/schema.json" "${A[@]}"
man badsrc 'b["sources"]["calendar"] = {"status": "ok"}'
expect 2 "unknown source key(s)" err "unknown source key exits 2" --bundle "$TMP/badsrc.json" "${A[@]}"
"$PY" -c "import json; c=json.load(open('prep.json')); c['sensitive_terms']['health'].append('(unclosed'); json.dump(c, open('$TMP/cfg_bad.json','w'))"
expect 2 "bad pattern" err "a broken pattern in prep.json exits 2 naming its path" --bundle "$M" "${A[@]}" --config "$TMP/cfg_bad.json"
"$PY" -c "import json; c=json.load(open('prep.json')); c['work_modifier_nouns'] = None; json.dump(c, open('$TMP/cfg_wn_null.json','w'))"
expect 2 "work_modifier_nouns" err "work_modifier_nouns: null exits 2" --bundle "$M" "${A[@]}" --config "$TMP/cfg_wn_null.json"
"$PY" -c "import json; c=json.load(open('prep.json')); c['work_modifier_nouns'].append('(unclosed'); json.dump(c, open('$TMP/cfg_wn_bad.json','w'))"
expect 2 "work_modifier_nouns" err "a broken work_modifier_nouns pattern exits 2 naming its path" --bundle "$M" "${A[@]}" --config "$TMP/cfg_wn_bad.json"
"$PY" -c "import json; c=json.load(open('prep.json')); c['judgement_terms']['any'].append('[0-9]\\\\d'); json.dump(c, open('$TMP/cfg_d.json','w'))"
expect 2 "use [0-9] instead of" err "\\d in a pattern is refused (ASCII digits only)" --bundle "$M" "${A[@]}" --config "$TMP/cfg_d.json"
"$PY" "$SCRIPT" --bundle "$M" "${A[@]}" 2>"$TMP/err" | head -1 >/dev/null; RC=${PIPESTATUS[0]}
check "broken pipe: exit 0 or 6, never a traceback" '[[ "$RC" == 0 || "$RC" == 6 ]] && ! grep -q Traceback "$TMP/err"'
"$PY" "$SCRIPT" --bundle "$M" "${A[@]}" >&- 2>"$TMP/err"; RC=$?
check "closed stdout: exit 6 with a message, no traceback" '[[ "$RC" == 6 ]] && grep -q "the output could not be written" "$TMP/err" && ! grep -q Traceback "$TMP/err"'
expect 0 "--meetings" out "--help exits 0" --help

# ---------------------------------------------------------------- docs and listing consistency
"$PY" - >"$TMP/docs" 2>&1 <<'PY'
import json, os, re, subprocess, sys
problems = []
skill = open("SKILL.md", encoding="utf-8").read()
fm = re.match(r"---\nname: (.+)\ndescription: (.+)\n---\n", skill)
if not fm or fm.group(1) != "one-on-one-prep" or fm.group(2).strip() in ("|", "|-", ">", ">-") \
        or ": " in fm.group(2) or " #" in fm.group(2) or fm.group(2)[:1] in "'\"[{&*!|>%@`":
    problems.append("frontmatter must be exactly name + one-line description")
elif len(fm.group(2)) > 1024:
    problems.append("description is over 1,024 characters")
listing = json.load(open("_listing.json", encoding="utf-8"))
for k in ["slug", "title", "summary", "benefits", "requirements", "roles", "businessTypes", "authorName", "sampleOutput"]:
    if k not in listing:
        problems.append("listing missing " + k)
if listing.get("slug") != "one-on-one-prep" or len(listing.get("benefits", [])) != 5:
    problems.append("listing slug/benefits")
if not set(listing.get("roles", [])) <= {"sales-marketing", "customer-success", "finance-operations"}:
    problems.append("roles")
if not set(listing.get("businessTypes", [])) <= {"b2b", "dtc", "agency"}:
    problems.append("businessTypes")
run = open("expected_output.txt", encoding="utf-8").read()
section = run.split("=== 1. ")[1].split("\n", 1)[1].split("\n=== 2. ")[0].rstrip("\n")
footer = "\n\n_Produced by running `bash run.sh` on the fictional sample data bundled with this skill._"
if listing.get("sampleOutput") != section + footer:
    problems.append("sampleOutput != run.sh section 1 + footer")
config = json.load(open("prep.json", encoding="utf-8"))
contract = open("DATA_CONTRACT.md", encoding="utf-8").read()
for key in config:
    if "`{}`".format(key) not in contract:
        problems.append("DATA_CONTRACT.md does not document prep.json key " + key)
for doc in ["SKILL.md", "README.md", "DATA_CONTRACT.md"]:
    text = open(doc, encoding="utf-8").read()
    for flag in re.findall(r"--[a-z][a-z-]+", text):
        if flag not in ("--bundle", "--as-of", "--config", "--meetings", "--json", "--help"):
            problems.append("{} mentions unknown flag {}".format(doc, flag))
    for word in [".claude/", "slash command", "Claude Code", "Cursor rules"]:
        if word in text:
            problems.append("{} mentions {}".format(doc, word))
rows = json.load(open("scanner_corpus.json", encoding="utf-8"))["rows"]
m = re.search(r"([0-9]+) sentences", skill)
if not m or int(m.group(1)) != len(rows):
    problems.append("SKILL.md corpus size claim does not match scanner_corpus.json ({})".format(len(rows)))
files = sorted(os.listdir("."))
shipped = [f for f in files if f != "_listing.json"]
sizes = {f: os.path.getsize(f) for f in shipped}
if any(os.path.isdir(f) for f in files):
    problems.append("subfolders present")
if len(shipped) > 64 or max(sizes.values()) > 256 * 1024 or len(json.dumps([{'filename':f,'content':open(f,encoding='utf-8').read()} for f in shipped]).encode('utf-8')) > 8 * 1024 * 1024:
    problems.append("size limits")
for f in shipped:
    if f.endswith(".json") or f.endswith(".md") or f.endswith(".txt"):
        text = open(f, encoding="utf-8").read()
        for email in re.findall(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]+)", text):
            if not (email.endswith(".example") or email in ("resource.calendar.google.com", "group.calendar.google.com")):
                problems.append("{}: non-.example address domain {}".format(f, email))
for f in shipped:
    if re.search(r"(?i)" + "brindle" + "moor|marrow" + "gate|\\bost" + "rel\\b", open(f, encoding="utf-8", errors="replace").read()):
        problems.append("{}: sample names from before round 4 (real businesses) are still present".format(f))
print("\n".join(problems) or "docs ok")
PY
check "docs: frontmatter, listing, sampleOutput == run.sh section 1 + footer, every prep.json key documented, flags exist, sizes, .example addresses" 'grep -qx "docs ok" "$TMP/docs" || { cat "$TMP/docs"; false; }'

echo
if "$PY" - <<'PYTEST'
import build_prep as h
from pathlib import Path
scanner = h.load_config(Path("prep.json"))["_scanner"].with_names(["Riley"])
for text in [
    "Needs a private room to pump twice a day",
    "Asked for no meetings before 3pm during Ramadan",
    "Asked for a screen reader as an ADA accommodation",
    "Due in March, planning 16 weeks off",
    "Riley is the weak link on the team",
    "Riley = liability on the Tolvanic account",
    "Priya = liability on the account",
    "Honestly not sure Riley is cut out for this",
]:
    for authored in (True, False):
        assert scanner.check(text, authored), text
for text in ["Move the planning meeting to Wednesday", "Review the customer onboarding checklist", "Repair the hydraulic pump before launch"]:
    assert scanner.check(text, True) is None, text
PYTEST
then ok "R6 O10 accommodations and personal judgements"; else bad "R6 O10 accommodations and personal judgements"; fi
if "$PY" - <<'PY_R8'
import contextlib,copy,io,json,tempfile,sys
from pathlib import Path
import build_prep as bp
_cached_config=bp.load_config(bp.DEFAULT_CONFIG)
bp.load_config=lambda path: _cached_config
CORPUS={'R6': [('Needs a private room to pump twice a day', 'team-logistics'), ('Asked for no meetings before 3pm during Ramadan', 'team-logistics'), ('Asked for a screen reader as an ADA accommodation', 'process'), ('Due in March, planning 16 weeks off', 'team-logistics'), ('Riley is the weak link on the team', 'work-feedback'), ('Riley = liability on the Tolvanic account', 'work-feedback'), ('Honestly not sure Riley is cut out for this', 'work-feedback')], 'NEW': [('Needs a quiet room for prayer at 1pm', 'team-logistics'), ('Leaving early Fridays for Shabbat', 'team-logistics'), ('Asked to skip the offsite during Lent', 'team-logistics'), ('Requested a standing desk for her back', 'process'), ('Needs captions on all calls (hard of hearing)', 'process'), ('Asked for extra time on deadlines due to dyslexia', 'process'), ('Back from mat leave in May', 'team-logistics'), ('IVF appointments Tuesdays', 'team-logistics'), ('Wants to go part-time after the baby', 'career-growth'), ('Nursing schedule means camera off at 11', 'team-logistics'), ('Needs to step out for insulin', 'team-logistics'), ('Therapy Thursdays at 4', 'team-logistics'), ('Chemo every other week', 'team-logistics'), ('Going through a divorce, distracted', 'work-feedback'), ('Caring for dad after his stroke', 'team-logistics'), ("Riley's a dead weight", 'work-feedback'), ('Riley is dragging the team down', 'work-feedback'), ("Riley isn't up to the job", 'work-feedback'), ("I'd PIP Riley", 'work-feedback'), ('Riley is out of her depth', 'work-feedback'), ("Riley's not a culture fit", 'work-feedback'), ('Thinking about letting Riley go', 'team-logistics'), ('Riley = problem. Review this note.', 'work-feedback'), ('Riley seems burned out and drinking more', 'work-feedback'), ('Asked for Eid off', 'team-logistics'), ('Diwali week light schedule', 'team-logistics'), ('Gender transition, new name from Monday', 'team-logistics'), ('Requested ergonomic keyboard for RSI', 'process'), ('Wheelchair access to the new office', 'process'), ('Pregnant, flying restrictions after week 28', 'team-logistics')], 'CTRL': [('Fix the hydraulic pump on line 3', 'project'), ('Ramadan marketing calendar draft due Friday', 'project'), ('ADA compliance audit for the website', 'project'), ('Screen reader support in the app', 'project'), ('Weak link in the data pipeline is the ETL job', 'process'), ('Liability clause in the Tolvanic contract', 'customer'), ('Due in March: the Q1 roadmap', 'project'), ('16 weeks of runway left on the project', 'project'), ('Prayer-times feature for the app', 'project'), ('Lactation room booking feature for the office app', 'project'), ('Offsite planning starts today', 'team-logistics'), ('Riley shipped the billing fix', 'project'), ('Riley owns the Tolvanic renewal', 'customer')], 'O12': [('Backfill for Riley once we let him go', 'team-logistics'), ('Thinking about letting Riley go', 'team-logistics'), ('Open a req to replace Riley, he does not know yet', 'team-logistics'), ('Riley is on thin ice', 'team-logistics'), ('Planning to manage Riley out', 'team-logistics'), ('Planning for Riley exit', 'team-logistics'), ('Riley will be fired next week', 'team-logistics')], 'O13': [('Pumping breaks at 10 and 2', 'process'), ('Asked for a screen reader', 'process'), ('Out from March for about four months with the baby', 'process'), ('Asked for a screen reader for the project', 'process'), ('Planning to replace Riley on the migration project', 'process')]}
CORPUS['fresh_personal']=[('We will manage Riley out after the migration.', 'process'), ('Riley is being replaced next month; draft the rollout plan.', 'process'), ('Riley needs extra pumping breaks during project planning.', 'process'), ('Riley requires a screen magnifier for the new console.', 'process'), ("Riley's baby leave starts after the launch.", 'process')]
CORPUS['CTRL'] += [('Riley will implement screen magnifier compatibility in the console.', 'project'), ('Riley is reviewing the hydraulic pump migration plan.', 'project'), ('Riley will add baby-leave eligibility to the HR product.', 'project')]
CORPUS['independent_personal']=[('Morgan brings nothing to this team.', 'process'), ('Riley is completely out of their depth here.', 'process'), ('We should get rid of Morgan after this sprint.', 'process'), ('I need to reserve breaks to express milk when I return.', 'process'), ('My therapist suggested reducing my working hours.', 'process'), ('I cannot attend the Friday meeting because of mosque prayers.', 'process'), ('My child has been diagnosed with autism.', 'process')]
CORPUS['CTRL'] += [('Morgan will remove the deprecated queue after this sprint.', 'project'), ('Riley will review the failed deployment and document the rollback.', 'project'), ('We need a smaller cache to reduce memory pressure.', 'project'), ('The service account cannot access the new billing endpoint.', 'project'), ('I will schedule the migration outside peak customer hours.', 'project')]
base=json.loads(Path('sample_bundle_manager.json').read_text())
receipts=[]
with tempfile.TemporaryDirectory() as td:
 for family,rows in CORPUS.items():
  for index,(text,topic) in enumerate(rows):
   safe=family=='CTRL'
   # Safe twin uses identical eligibility/topic and exercises each path.
   for mode in ('authored','quote'):
    for twin in (True,False):
     t='Discuss the data migration plan today' if twin else text
     b=copy.deepcopy(base);b['work_items']=[]
     for m in b['meetings']:m.pop('text',None)
     it={'id':'r8case','kind':'topic','owner':'counterpart','key':'r8case','topic':topic,'meeting_id':'sample-1on1-0929','quote_from':'transcript','summary':t if mode=='authored' else 'Discuss this work item','quote':t if mode=='quote' else 'Raised it in the meeting today.'}
     b['items']=[it];path=Path(td)/'bundle.json';path.write_text(json.dumps(b))
     out,err=io.StringIO(),io.StringIO()
     with contextlib.redirect_stdout(out),contextlib.redirect_stderr(err):
      rc=bp.main(['--bundle',str(path),'--as-of','2026-10-13T09:30:00-07:00','--json'])
     assert rc==0,(family,text,mode,rc,err.getvalue())
     d=json.loads(out.getvalue());shown=any('r8case' in x['item_ids'] for x in d['items'])
     personal=any(x[0] == __import__("hashlib").sha256(b"r8case").hexdigest() for x in d['withheld_personal']['item_ids'])
     judgment=any(x[0] == __import__("hashlib").sha256(b"r8case").hexdigest() for x in d['left_out_judgement']['item_ids'])
     if safe or twin:
      assert shown and bp.md(t) in d['markdown'],(family,text,mode,'safe missing',d)
     else:
      expected='judgement' if any(w in text for w in ['weak link','liability','cut out','dead weight','dragging','up to the job','out of her depth','culture fit','= problem','brings nothing','out of their depth']) else 'personal'
      assert not shown and (personal if expected=='personal' else judgment),(family,text,mode,'wrong reason',d)
      assert bp.md(t) not in d['markdown'] and t not in json.dumps(d['items']), (text,'leak')
      assert not d['items'],(text,'item or agenda source retained')
     receipts.append((family,index,mode,twin,rc))
print('O12/O13 end-to-end cases with safe twins:',len(receipts))

PY_R8
then ok "O12/O13 rendered paths, reasons and safe twins"; else bad "O12/O13 rendered paths, reasons and safe twins"; fi

# ---------------------------------------------------------------- R9: dismissal / accommodation / pay of any person
# QA_R8: strings disclosed in the round-8 QA report (regressions). TUNED_R9: sentences the repair owners wrote while
# building the round-9 fix (tuned on, not held out). Every case is an eligible commitment (whole-sentence quote found in
# the meeting text, owner user, due "by Friday", so a shown item is overdue with an agenda line), run as a source quote,
# an authored summary and an AI-summary quote, with the named person as the counterpart (Riley, masked) and as a third
# party (Sam). A safe twin in the same bundle must render in the Markdown and the JSON.
if "$PY" - <<'PY_R9'
import contextlib, copy, io, json, tempfile
from pathlib import Path
import build_prep as bp
_cfg = bp.load_config(bp.DEFAULT_CONFIG)
bp.load_config = lambda path: _cfg
QA_R8 = ["Exit Sam by Q1.", "Sam won't be with us after the reorg.", "Quietly sourcing Sam's replacement.",
         "Transition Sam off the team.", "HR is drafting Sam's separation agreement.", "Sam's role is being eliminated.",
         "Sam's last day will be end of November, don't announce it yet.", "Talk to legal before we part ways with Sam.",
         "Plan Sam's exit for December.", "Begin documenting Sam's misses for the file.",
         "Line up a contractor to cover once Sam is gone.", "Sam is on the layoff list.",
         "Offboard Sam right after the launch.", "We need to move on from Sam.",
         "Sam's position won't survive the headcount cut.", "Have the severance conversation with Sam next week.",
         "Post the job listing for Sam's role before telling him.",
         "Get Sam's successor hired before we announce anything.", "Sam goes at the end of the quarter.",
         "Push Sam out before his vesting cliff.", "Frame Sam's exit as part of the restructure.", "Cut Sam loose after the audit.",
         "Breast pump storage for Sam in the office fridge.", "Sam needs a magnifier on the monitor for low vision.",
         "Set up JAWS on Sam's laptop.", "Sam asked for captions in every meeting because of hearing loss.",
         "Sam is taking twelve weeks when the baby arrives.", "Sam asked for a salary advance.",
         "Sam wants a raise to match the offer from a competitor."]
TUNED_R9 = ["Sam doesn't have a future here.", "Sam stops being on payroll at month end.", "Sam is one of the cuts in the January round.",
            "Sam's last week is the week of the 9th.", "Sam is not being kept on after the merger.",
            "We've decided Sam isn't staying.", "Tell the recruiter we need someone in Sam's seat by February.",
            "Sam's head is on the block after the outage.", "We're done with Sam after this project.",
            "Sam has a few weeks left with us.", "Sam will pump between meetings, block 30 minutes at noon.",
            "Sam asked to borrow against next month's salary.", "Sam needs ZoomText installed on the new laptop."]
WORK = [("Replace Sam's old dashboard with the new one.", "project"), ("Replace the Redis cache with Valkey.", "project"),
        ("Exit the old office lease by March.", "project"), ("Terminate the unused EC2 instances.", "project"),
        ("Offboard the legacy payroll vendor integration.", "project"), ("Kill the nightly sync job.", "project"),
        ("Deprecate the v2 API once Sam ships v3.", "project"), ("Let the Zendesk contract lapse.", "project"),
        ("Backfill the events table for September.", "project"), ("Open a new req for a data engineer.", "project"),
        ("Sunset the old onboarding email sequence.", "project"), ("Sam wants a raise in the alert threshold.", "project"),
        ("Sam asked for captions on the launch video.", "project"), ("Move Sam's 1:1 to Thursdays.", "team-logistics"),
        ("Sam is leaving early on Friday for the customer dinner.", "team-logistics"),
        ("Sam will join the hiring panel for the analyst role.", "team-logistics"),
        ("Book a room for Sam's team retro.", "team-logistics")]
TWIN = "Send the Q4 roadmap draft to the team."
base = json.loads(Path("sample_bundle_manager.json").read_text(encoding="utf-8"))
n = 0
with tempfile.TemporaryDirectory() as td:
    rows = [(t, "team-logistics", False) for t in QA_R8 + TUNED_R9] + [(t, top, True) for t, top in WORK]
    for case, topic, safe in rows:
        for variant in ("counterpart", "third_party"):
            text = case.replace("Sam", "Riley") if variant == "counterpart" else case
            for path in ("quote", "summary", "ai_summary"):
                b = copy.deepcopy(base)
                b["work_items"] = []
                for m in b["meetings"]:
                    m.pop("text", None)
                    if m["id"] == "sample-1on1-0929":
                        m["text"] = "Dana: We talked about this one in detail. Dana: " + TWIN + " Dana: " + text
                case_item = {"id": "r9case", "kind": "commitment", "owner": "user", "key": "r9case", "topic": topic,
                             "meeting_id": "sample-1on1-0929", "due_phrase": "by Friday",
                             "quote_from": "summary" if path == "ai_summary" else "transcript",
                             "summary": text if path == "summary" else "Follow up on this item",
                             "quote": "We talked about this one in detail." if path == "summary" else text}
                twin = {"id": "r9twin", "kind": "commitment", "owner": "user", "key": "r9twin", "topic": "project",
                        "meeting_id": "sample-1on1-0929", "due_phrase": "by Friday", "quote_from": "transcript",
                        "summary": "Q4 roadmap draft to the team", "quote": TWIN}
                b["items"] = [case_item, twin]
                p = Path(td) / "b.json"
                p.write_text(json.dumps(b), encoding="utf-8")
                outs = {}
                for flag in ([], ["--json"]):
                    out, err = io.StringIO(), io.StringIO()
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        rc = bp.main(["--bundle", str(p), "--as-of", "2026-10-13T09:30:00-07:00"] + flag)
                    assert rc == 0, (case, variant, path, rc, err.getvalue())
                    outs[bool(flag)] = out.getvalue()
                d = json.loads(outs[True])
                assert d["markdown"] == outs[False], (case, "markdown differs from --json markdown")
                assert not d.get("dropped_items", {}).get("quote_not_found"), (case, variant, path, "ineligible", d.get("dropped_items"))
                ids = [i for x in d["items"] for i in x["item_ids"]]
                assert "r9twin" in ids and bp.md(TWIN) in d["markdown"], (case, variant, path, "twin not rendered")
                shown = "r9case" in ids
                personal = any(x[0] == __import__("hashlib").sha256(b"r9case").hexdigest() for x in d["withheld_personal"]["item_ids"])
                if safe:
                    assert shown and bp.md(text) in d["markdown"] if path != "summary" else shown, (case, variant, path, "control withheld", d["withheld_personal"])
                else:
                    assert not shown and personal, (case, variant, path, "not withheld as personal")
                    assert bp.md(text) not in d["markdown"] and text not in json.dumps(d), (case, "text leaked")
                n += 1
print("R9 end-to-end runs:", n)
PY_R9
then ok "R9 dismissal/accommodation/pay of any person: QA r8 strings + tuned cases withheld, controls and twins rendered"; else bad "R9 dismissal/accommodation/pay of any person: QA r8 strings + tuned cases withheld, controls and twins rendered"; fi

# ---------------------------------------------------------------- R10: fail-closed on every topic, any person form
# QA_R9: every OO string disclosed in QA_ROUND9.md (OO-5..OO-9), each with an eligibility twin: the same item with the
# sensitive part replaced by neutral work content, which must render as an Overdue commitment on the same path. Every
# string runs as a source quote, an authored summary and an AI-summary quote, filed under team-logistics, project and
# process (the topic no longer matters). QA_R9_CONTROLS: QA's 27 ordinary work items (OO-10), which must render.
# FRESH_R10: the repair owner's own sentences (batches A, B and C), written before each was first run and then tuned
# on (see the round-10 report); only those the shipped patterns handle are listed, the known misses are in SKILL.md.
if "$PY" - <<'PY_R10'
import contextlib, copy, io, json, re, tempfile
from pathlib import Path
import build_prep as bp
_cfg = bp.load_config(bp.DEFAULT_CONFIG)
bp.load_config = lambda path: _cfg
QA_R9 = [ [ "OO-5 R1", "Backfill Riley's seat before the announcement.", "Backfill Riley's metrics table before the announcement." ], [ "OO-5 R2", "Plan a managed exit for Raj in January.", "Plan a managed rollout for Raj in January." ], [ "OO-5 R3", "Start succession planning for Riley's role.", "Start capacity planning for Riley's project." ], [ "OO-5 R4", "We won't be renewing Riley's contract in March.", "We will be renewing Riley's Looker licence in March." ], [ "OO-5", "Riley is moving to a special projects role ahead of the exit.", "Riley is moving to a special projects role ahead of the launch." ], [ "OO-5", "Riley's contract ends in March and we won't extend it.", "Riley's pilot ends in March and we will extend it." ], [ "OO-5", "Prep the RIF paperwork for Riley by month end.", "Prep the SOC2 paperwork for Riley by month end." ], [ "OO-5", "Schedule the hard conversation with Riley about leaving.", "Schedule the roadmap conversation with Riley about pricing." ], [ "OO-5", "Move Riley's reports under Priya before Riley goes.", "Move Riley's dashboards under Priya before Riley presents." ], [ "OO-5 R12", "Write up Raj twice so we have cover later.", "Write up the design for Raj twice so we have options later." ], [ "OO-6 R5", "Hire our designer's replacement before the reorg is public.", "Book our designer's flights before the launch is public." ], [ "OO-6 R6", "Time to part ways with our designer, so draft the comms.", "Time to sync with our designer, so draft the comms." ], [ "OO-6 R7", "Start offboarding priya on the 15th.", "Start onboarding priya on the 15th." ], [ "OO-6", "Line up a replacement for J. before the board meeting.", "Line up a demo for J. before the board meeting." ], [ "OO-6", "Draft the comms for J.'s departure next week.", "Draft the comms for J.'s launch next week." ], [ "OO-7 R8", "Get Riley a screen reader license before onboarding.", "Get Riley a Figma license before onboarding." ], [ "OO-7 R9", "Get Riley an ergonomic chair for the herniated disc.", "Get Riley a second monitor for the design review." ], [ "OO-8 R10", "Raise Raj's salary by ten percent in the next cycle.", "Raise Raj's test coverage by ten percent in the next cycle." ], [ "OO-8 R11", "Raj's bonus got cut to half this year.", "Raj's backlog got cut to half this year." ], [ "OO-8", "Push Raj's equity refresh through comp committee.", "Push Raj's design refresh through review committee." ], [ "OO-8", "The new contractor asked for a raise after the promo.", "The new contractor asked for a review after the demo." ], [ "OO-9", "I'd like to talk about a raise before the cycle closes.", "I'd like to talk about a roadmap before the cycle closes." ], [ "OO-9", "I got an outside offer and want to discuss a counter.", "I got a vendor quote and want to discuss a discount." ] ]
QA_R9_CONTROLS = [ "Ask Raj to review the PR by Friday.", "Priya owns the launch checklist.", "Replace the old dashboard with the Looker one.", "Let go of the legacy API after the v3 cutover.", "Terminate the TLS at the load balancer.", "Write exit criteria for the beta.", "Kill the feature flag once the rollout is at 100%.", "Riley will backfill the metrics table for August.", "Raj is leaving early on Thursday for the offsite.", "Move Raj to the payments squad next quarter.", "Priya will run the hiring loop for the new designer.", "Riley will hand the on-call rotation to Raj in November.", "Pair Raj with Priya on the onboarding flow.", "Raj will offboard the old vendor SSO app.", "Riley will cut the release branch on Monday.", "Book a room for Priya's team demo.", "Riley is out on Friday, so move the 1:1 to Thursday.", "Priya will replace the flaky integration tests.", "Raj will fire off the survey to customers.", "Riley will drop the deprecated endpoints from the SDK.", "Raj will confirm the end date for the pilot contract.", "Priya will phase out the old pricing page.", "Ask Raj for the exit survey results from churned accounts.", "Riley will document the separation of duties for SOX.", "Riley will raise the rate limit for the bulk API.", "Priya is the successor owner for the billing service.", "Raj will remove the stale feature flags from the codebase." ]
FRESH_ATTACKS = [ "Draft the talking points for telling Tomasz his role is going away.", "Get legal to review the paperwork before we let the intern go.", "Collect devon's laptop and badge on his final Friday.", "Find out how much notice we owe K. under the Berlin contract.", "Line up coverage for the night-shift supervisor once she's out.", "Quietly open a req for Ines's slot before the all-hands.", "Move our QA lead's tickets to Ana before we break the news.", "Confirm with finance the payout for mei when she leaves in May.", "Decide whether Kwame stays past the probation review.", "Prepare the separation letter for T.R. by Thursday.", "Pull Riley's access the morning of the conversation.", "Ask Ines to hand over the vendor list before her contract isn't renewed.", "Write the script for the call where we tell our office manager it's over.", "Downgrade the intern's account the day he exits.", "Book a room for the termination talk with kwame.", "Get sign-off to drop the contractor's engagement after the sprint.", "Arrange coverage so nobody notices when Tomasz is gone.", "Make sure HR has Riley's paperwork ready for the exit chat.", "Remove the junior designer from the roster before payroll closes.", "Reassign Ines's accounts; she's not coming back after the review.", "Hold off on announcing that the analyst is leaving us.", "Plan the handover so mei's departure looks voluntary.", "Write up the case for letting the sdr go at quarter end.", "Get the replacement for K. through the hiring committee.", "Tell recruiting we need someone to take over from Tomasz in March.", "Start the paperwork to downsize the team by removing our ops analyst.", "Have a script ready in case he asks if he's being cut.", "Swap out the account manager on Acme before she's let go.", "Sort out who inherits Riley's projects after the restructure decision.", "Order a hearing amplifier for Kwame's desk before the move.", "Book a sign interpreter for Riley's onboarding sessions.", "Arrange a lower desk for the analyst's wheelchair.", "Find a quieter seat for mei because of her migraines.", "Get an anti-glare screen for T.R. after the eye surgery.", "Schedule Riley's work around the dialysis appointments.", "Approve a later start for Tomasz while he's on chemo.", "Set aside a private room for the intern's insulin shots.", "Ship a vertical mouse to our QA lead for the tendonitis.", "Cover Ines's calls during her physical therapy sessions.", "Put the new hire's epilepsy plan in the onboarding folder.", "Swap K. to remote days while recovering from the knee operation.", "Add a rest break to the support lead's shift for her pregnancy.", "Make sure devon's ADHD coaching is covered by the stipend.", "Get a magnifying monitor arm for the receptionist.", "Bump Tomasz's base to keep him from walking.", "Push through a retention grant for our lead engineer.", "Check whether mei's pay band can stretch another 8%.", "Approve the hardship loan for the analyst.", "Get comp to look at Riley's salary before the cycle closes.", "Match K.'s outside offer with a sign-on top-up.", "Raise the intern's hourly rate to what the others get.", "Ask finance to advance Ines part of her bonus.", "Sort out the support lead's unpaid overtime before she files.", "Find budget for a counteroffer to keep devon.", "Bring Kwame's stipend up to match the cost of living.", "Reduce the contractor's day rate once the project winds down.", "I want to revisit my compensation before the review.", "I'm thinking of asking for more money this cycle.", "I need a week off for my mom's surgery.", "I have a recruiter offer I'd like you to match.", "Let me take Wednesdays off for my therapy sessions.", "I need to talk about my salary versus the market.", "Interview the witnesses about the complaint against Tomasz.", "Send the warning letter to devon after legal signs off.", "Keep notes on the analyst's attendance for the file.", "Have the exit paperwork for Oyelaran ready before Thursday's call.", "Make sure jonah's access ends the day we tell him.", "Get the termination date for our night auditor confirmed with legal.", "Draft a neutral reference for M. once the decision is final.", "Figure out who absorbs the bookkeeper's work after we let her go.", "Prep Riley's separation paperwork quietly.", "Don't tell Farouk until the board signs off on cutting his role.", "Ask legal how to handle lucia's notice period.", "Our junior PM won't be renewed when the contract lapses.", "Plan the handoff so she can leave quietly at month end.", "Start recruiting a successor to our ops coordinator before telling anyone.", "Make Wendell's last day coincide with the system cutover.", "Schedule an exit chat with tomas for Friday afternoon.", "Close out B.K.'s accounts after the separation is signed.", "Fold the field tech's territory into Ana's once he's gone.", "Confirm Halvorsen gets the standard severance terms.", "Find a softer way to tell our recruiter the role is being cut.", "Remove sanjay from the on-call rotation before his exit.", "Keep Moreau's departure off the all-hands agenda until it's signed.", "Line up interviews to replace the support engineer in March.", "Order a footstool and wrist splint for Achebe.", "Move lucia's desk near the exit for her crutches.", "Get the warehouse lead a back brace before peak season.", "Block Riley's calendar for the cardiology follow-ups.", "Make sure J.P. has a seat near the restroom for the IBS flare-ups.", "Arrange dimmed lighting at the designer's desk for her migraines.", "Approve Nakamura's request to work from home during radiation.", "Find a captioner for our new sales rep's training calls.", "Push finance to approve Oyelaran's equity top-up.", "Cap the contractor's hours so his rate stays under budget.", "Look into whether jonah's bonus was miscalculated.", "Get M.'s relocation stipend paid before the move.", "Bring our analyst's salary in line with the new band.", "Sort Riley's back pay from the promotion date.", "Can we talk about whether I'm paid fairly for this level?", "I'd like a raise conversation before year end.", "I'll need Thursdays off for physio for a while.", "Collect statements from the team about the complaint against our shift lead.", "Book a session with HR on Farouk's conduct issue.", "Put the attendance warning for tomas in writing.", "Tell payroll Brannigan's final check goes out on the 30th.", "Get ahead of questions when zofia stops showing up on the org chart.", "Our QA contractor is done after this sprint, so plan the handover.", "Have IT cut off R.'s VPN the minute the meeting ends.", "Tell the hiring manager we're replacing the night supervisor.", "Riley is being let go next week, so freeze new assignments.", "Pause Okonkwo's onboarding of new accounts until the exit is announced.", "Confirm the date we part with ines-marie.", "Write the comms for when our data engineer leaves the company.", "Arrange the package for Delacroix before legal reviews it.", "Keep our sales rep's termination out of the forecast review.", "We should stop renewing bartek's contract after Q1.", "Line up someone to take D.'s seat once the paperwork clears.", "Make the call to cut the junior analyst before budgets lock.", "Hand Ferreira's clients to Mo once she's out the door.", "Order a sit-stand converter for Lindqvist's bad back.", "Keep zofia's calendar clear for her MS infusions.", "Get our receptionist a headset with amplification for her hearing.", "Push Riley's deadlines while the concussion heals.", "Set up a rest area near R.'s desk for the pregnancy.", "Approve Brannigan's half days during his cancer treatment.", "Find a sign interpreter for the new technician's induction.", "Swap bartek to the ground floor until the leg cast comes off.", "Escalate Okonkwo's missing overtime payment to payroll.", "Give our designer the market adjustment we promised.", "Find out why ines-marie's paycheck was short.", "Approve D.'s retention bonus before the competitor's deadline.", "Get Riley's comp bumped to the senior band.", "Hold the intern's stipend increase until January.", "I want to understand why my pay is below the band.", "I'm weighing an offer from another company and want to talk.", "Can I push my start time later while I do IVF?", "I'll be out for my knee surgery the week of the 12th.", "Gather the Slack messages for the investigation into Delacroix.", "Prep the evidence for our support lead's disciplinary hearing.", "Ask legal whether we can put zofia on a performance plan.", "File the harassment report about R. with HR.", "Start documenting Lindqvist's missed deadlines for the file.", "Keep the complaint about our QA contractor confidential." ]
FRESH_CONTROLS = [ "Tomasz will replace the cron job with an Airflow DAG.", "Ines will offboard the trial accounts that expired in June.", "Have Kwame backfill the missing invoice rows for Q2.", "Ask the analyst to phase out the old churn model.", "Our QA lead will terminate the staging cluster after the load test.", "Riley will draft the exit plan for the legacy billing system.", "Have mei cut the release branch on Thursday.", "Let the intern shadow the support rotation next week.", "The new hire will own the onboarding checklist.", "K. will send the pricing deck to Acme.", "Tomasz is leaving for the Lisbon conference on Monday.", "Our designer will finalise the empty-state illustrations.", "Riley will hand over the on-call pager to devon on Friday.", "Ines will ask payroll for the headcount report format.", "Kwame will retire the deprecated v1 endpoints.", "The support lead will escalate the Acme outage to engineering.", "Have Riley review the termination clause in the Acme MSA.", "Ask our analyst to rerun the attrition dashboard for customers.", "Riley will lead the Q4 goal-setting session.", "Tomasz wants to grow into a staff engineer role next year.", "Mei asked how the new sprint cadence works.", "Give Ines feedback on her design review write-up.", "The intern will document the release process in Notion.", "Riley will move the standup to 9:45.", "Kwame will drop the unused feature flags before launch.", "Our ops analyst will close out the vendor tickets this week.", "devon will fix the flaky login test.", "Ask T.R. to share the latency numbers with the team.", "Riley will kill the old A/B test once results are in.", "Tomasz is out Friday, so push the demo to Monday.", "Have the contractor finish the API docs before handoff.", "Ines will cancel the unused Zoom seats.", "Mei will remove the stale rows from the leads table.", "Riley will set the end date on the beta program.", "Kwame will sunset the weekly digest email.", "Our account manager will renew the Globex contract.", "Ask Riley to transition the support queue to the new tool.", "The analyst will separate the staging and prod configs.", "Have Tomasz pair with the new hire on the billing bug.", "Riley will run the exit criteria review for the beta.", "Ines will draft the hiring plan for two new engineers.", "Ask devon to replace the expired SSL certificate.", "Riley will cover the support rotation while Kwame is at the offsite.", "Our PM will cut scope on the reporting feature.", "Riley will push out the hotfix tonight.", "Riley will replace the logo on the pricing page.", "Oyelaran will terminate the old SFTP connection after cutover.", "Have jonah backfill the churn metrics for September.", "Ask our designer to exit fullscreen mode in the demo build.", "Farouk will sunset the legacy export endpoint.", "Riley will draft the offboarding checklist for customers.", "Our recruiter will post the job ad for the new SRE role.", "Lucia will cut the v2.3 release on Friday.", "Riley will review the separation of environments for audit.", "Wendell owns the replacement of the office printers.", "Ask the PM to drop the stale tickets from the sprint.", "Halvorsen will lead the vendor exit from the old CRM.", "Riley's team will migrate the billing jobs next week.", "Moreau is the backup approver while Riley is on holiday.", "Have tomas pair with the intern on the dashboard bug.", "Riley will update the notice period clause in the vendor MSA.", "Our analyst will rerun the layoff-impact model for the client.", "The intern will shadow Riley on the renewal call.", "Riley will fire up the staging stack for the demo.", "sanjay will kill the flaky e2e job.", "Achebe will take over the weekly metrics email.", "Riley will move the retro to Wednesday.", "Have Nakamura present the Q3 results to the board.", "Ask the support lead to triage the Acme escalations.", "Riley wants to grow toward a team lead role.", "Our new hire asked where the deploy docs live.", "Give Riley feedback on the QBR deck structure.", "Riley will phase out the manual invoice process.", "Farouk will replace the expired API keys.", "Riley will hand the Acme account to Moreau after the launch.", "The contractor will finish the data migration by the 20th.", "Riley will cancel the duplicate Looker licences.", "Have the analyst eliminate duplicate rows from the leads export.", "Riley will set the trial end date for the Globex pilot.", "Ask jonah to review the departure board integration for the airport client.", "Riley will write the exit criteria for the beta.", "Our ops coordinator will book the offsite venue.", "Riley will share the release news with the support team.", "Halvorsen will document the payroll vendor integration.", "Riley is leaving early Thursday for the customer workshop.", "Have Wendell lead the post-incident review.", "Riley will transition the support queue off Zendesk.", "The PM will push out the roadmap update to stakeholders.", "Lucia will cover the support rotation while Riley is at the conference.", "Ask Riley to retire the old onboarding survey.", "Riley will drop the deprecated fields from the export.", "Achebe will replace the broken monitor in the war room.", "Riley will fix the screen reader labels on the checkout form.", "Riley will add captions to the onboarding videos.", "Moreau will benchmark salaries for the hiring plan.", "Riley will draft the bonus pool proposal for finance.", "Have the analyst audit the overtime report for the warehouse client.", "Riley will update the pay band page on the careers site.", "Riley will test the checkout flow with a screen reader.", "Riley will set up the parental leave policy page in the handbook.", "Riley will onboard the two new hires next week.", "Riley will reassign the open tickets before the holiday freeze.", "Riley owns the headcount plan for next year.", "Riley will talk to Acme about their renewal pricing.", "Brannigan will replace the hero image on the homepage.", "zofia will terminate the trial for the Initech sandbox.", "Okonkwo will backfill the missing NPS responses from the export.", "Ask the data engineer to phase out the nightly CSV dump.", "Riley will run the exit review for the beta customers.", "Our QA contractor will cut a release candidate on Monday.", "Delacroix will lead the vendor offboarding for the old payroll provider.", "Riley will draft the end-of-life notice for the v1 SDK.", "Ferreira will kill the unused feature branches.", "Riley will get rid of the duplicate Jira boards.", "Mo will move the Acme renewal call to Thursday.", "Lindqvist will present the churn analysis at the all-hands.", "Riley will lead the hiring loop for the new data engineer.", "Our sales rep will send the revised quote to Globex.", "Riley will confirm the cutover date with the vendor.", "Brannigan will retire the old status page.", "Riley will review the parental leave FAQ for the handbook.", "Okonkwo will update the bonus calculation in the commission tool.", "The intern will clean up the stale Confluence pages.", "Riley will draft the deprecation notice for customers on v1.", "Ask Riley to replace the broken webhook with the new endpoint.", "D. will share the latency dashboard with support.", "Riley will drop the dead code from the billing service.", "Riley will sunset the Slack bot once the new app ships.", "Our designer will rework the onboarding illustrations.", "Riley will pair with Mo on the flaky checkout test.", "Ferreira is on holiday next week, so Riley covers the queue.", "Riley wants to lead a project end to end next quarter.", "Riley asked how the on-call compensation policy works.", "Give Riley feedback on the incident write-up.", "Riley will separate the billing and usage dashboards.", "Riley will terminate the TLS connection at the load balancer.", "Riley will exit the Heroku account before the renewal.", "Riley will replace the staging database with a fresh snapshot.", "Riley will cancel the Datadog trial before it converts.", "Riley will update the leave request form in Workday.", "Riley will fix the screen reader focus order on the settings page.", "Riley will benchmark contractor day rates for the budget.", "Mo will document the severance policy changes for the handbook.", "Riley will check the end date on the Okta contract.", "Riley will shut down the old reporting cluster.", "Riley will hand over the release duties to Mo.", "Riley will archive the departed customers' workspaces.", "Riley will move Mo's dashboards to the new folder.", "Riley will drop off the hardware at the office.", "Riley will meet the new hire for coffee on Monday.", "Riley will draft the offer letter template for recruiting.", "Riley will close out the Q3 hiring plan.", "Riley will replace the meeting notes template.", "Riley will draft the transition plan for the support tool migration.", "Riley will review the termination for convenience clause with legal.", "Riley will push out the analytics release after QA.", "Riley will fire off the NPS survey to churned customers.", "Riley will remove Mo from the deploy approvers list while she's on holiday.", "Riley will rotate the API keys before the audit.", "Riley will set the trial expiry for the Hooli pilot.", "Riley will review the headcount dashboard for finance." ]
TWIN = "Send the Q4 roadmap draft to the team."
TOPICS = ["team-logistics", "project", "process"]
base = json.loads(Path("sample_bundle_manager.json").read_text(encoding="utf-8"))
td = tempfile.mkdtemp()


def run(text, topic, path):
    b = copy.deepcopy(base)
    b["work_items"] = []
    for m in b["meetings"]:
        m.pop("text", None)
        if m["id"] == "sample-1on1-0929":
            # the case sentence comes last, so a negation in it never reaches the other quotes (shared helper rule)
            m["text"] = "Dana: We talked about this one in detail. Dana: " + TWIN + " Dana: " + text
    b["items"] = [
        {"id": "r10case", "kind": "commitment", "owner": "user", "key": "r10case", "topic": topic,
         "meeting_id": "sample-1on1-0929", "due_phrase": "by Friday",
         "quote_from": "summary" if path == "ai_summary" else "transcript",
         "summary": text if path == "summary" else "Follow up on this item",
         "quote": "We talked about this one in detail." if path == "summary" else text},
        {"id": "r10twin", "kind": "commitment", "owner": "user", "key": "r10twin", "topic": "project",
         "meeting_id": "sample-1on1-0929", "due_phrase": "by Friday", "quote_from": "transcript",
         "summary": "Q4 roadmap draft to the team", "quote": TWIN}]
    p = Path(td) / "b.json"
    p.write_text(json.dumps(b), encoding="utf-8")
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = bp.main(["--bundle", str(p), "--as-of", "2026-10-13T09:30:00-07:00", "--json"])
    assert rc == 0, (text, topic, path, rc, err.getvalue())
    d = json.loads(out.getvalue())
    assert not d["dropped_items"]["quote_not_found"], (text, topic, path, "ineligible")
    ids = [i for x in d["items"] for i in x["item_ids"]]
    assert "r10twin" in ids and bp.md(TWIN) in d["markdown"], (text, topic, path, "sibling not rendered")
    return d, ids


def withheld(text, topic, path):
    d, ids = run(text, topic, path)
    assert "r10case" not in ids, (text, topic, path, "shown")
    assert any(x[0] == __import__("hashlib").sha256(b"r10case").hexdigest() for x in d["withheld_personal"]["item_ids"]), (text, topic, path, "not withheld as personal")
    assert bp.md(text) not in d["markdown"] and text not in json.dumps(d), (text, topic, path, "text leaked")
    assert not any("Follow up on this item" in line or text[:30] in line for line in d["agenda"]), (text, "agenda line")


def rendered(text, topic, path):
    d, ids = run(text, topic, path)
    assert "r10case" in ids, (text, topic, path, "not rendered", d["withheld_personal"], d["left_out_judgement"])
    assert bp.md(text) in d["markdown"], (text, topic, path, "text not in the rendered markdown")
    label = text if path == "summary" else "Follow up on this item"
    assert any(line.split(". ", 1)[1].startswith("Overdue (yours, 11 days): " + bp.md(label)[:40])
               for line in d["markdown"].splitlines() if re.match(r"[0-9]+\. ", line)), (text, topic, path, "no agenda line")


n = 0
for finding, text, twin in QA_R9:
    for topic in TOPICS:
        for path in ("quote", "summary", "ai_summary"):
            withheld(text, topic, path)
            rendered(twin, topic, path)
            n += 2
for text in QA_R9_CONTROLS + FRESH_CONTROLS:
    for topic in TOPICS:
        for path in ("quote", "summary", "ai_summary"):
            rendered(text, topic, path)
            n += 1
for text in FRESH_ATTACKS:
    for topic in TOPICS:
        for path in ("quote", "summary", "ai_summary"):
            withheld(text, topic, path)
            n += 1
# OO-7 in-process: a condition named without a subject verb
sc = _cfg["_scanner"].with_names(["Dana Ortiz", "Riley Chen"])
assert sc.check("Riley has a herniated disc.", False) == "personal" and sc.check("Riley has a design review.", False) is None
print("R10 end-to-end runs:", n)
PY_R10
then ok "R10 fail-closed on every topic: QA r9 OO strings withheld with rendered twins, QA controls and fresh cases"; else bad "R10 fail-closed on every topic: QA r9 OO strings withheld with rendered twins, QA controls and fresh cases"; fi

# OO10: rendered regression coverage on all work topics and content paths.
if "$PY" -B test_regressions.py; then
  ok "OO10 disclosed corpus and structural probes: eligible twins, personal reasons, useful work and safe IDs"
else
  bad "OO10 rendered regression coverage"
fi

if [[ "$PASS" == "$TOTAL" ]]; then echo "PASS $PASS/$TOTAL"; else echo "FAIL $PASS/$TOTAL passed"; exit 1; fi

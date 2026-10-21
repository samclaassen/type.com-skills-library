#!/usr/bin/env bash
# Negative and edge-case tests. Run from the skill directory: bash tests.sh
set -euo pipefail

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
NL="python3 newsletter.py"
CFG=sample_config.json
BRIEF=sample_brief.md
pass=0
SECT="$(printf '\302\247')"  # section sign, kept out of the file so it stays pure ASCII

ok() { pass=$((pass + 1)); echo "ok $pass - $1"; }
fail() { echo "NOT OK - $1"; exit 1; }

# expect_exit CODE OUTFILE -- command...
expect_exit() {
  local want="$1" out="$2"; shift 3
  set +e; "$@" > "$out" 2>&1; local got=$?; set -e
  [ "$got" = "$want" ] || { cat "$out"; fail "expected exit $want, got $got: $*"; }
}
expect_grep() { grep -qF -- "$2" "$1" || { cat "$1"; fail "missing in output: $2"; }; }

# mutate SRC DEST PYTHON_EXPR  (d is the loaded JSON object)
mutate() {
  python3 - "$1" "$2" "$3" <<'PY'
import json, sys
src, dest, expr = sys.argv[1:4]
d = json.load(open(src, encoding="utf-8"))
exec(expr)
json.dump(d, open(dest, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
PY
}

# 1. good draft passes with exit 0
expect_exit 0 "$tmp/o1" -- $NL lint --draft sample_draft.json --config $CFG --brief $BRIEF
expect_grep "$tmp/o1" "**Result: PASS**"
ok "good draft lints clean (exit 0)"

# 2. bad draft fails with exit 1 and names each class of problem
expect_exit 1 "$tmp/o2" -- $NL lint --draft sample_draft_bad.json --config $CFG --brief $BRIEF
for s in "2 options; config asks for exactly 3" "ALL-CAPS word(s) FREE, LAST, CHANCE" "contains emoji" \
         'spam-trigger word "free"' 'false urgency "last chance"' 'fake reply/forward prefix "RE:"' \
         "5 sentences; must be 2-3" 'starts with "welcome to"' "4 sections; config allows 3" \
         "body is 31 words; target 80-150" '"game-changer"' '"delve"' '"in today'"'"'s fast-paced"' \
         'unverified figure "73%"' 'unverified quote "the future of agency work' \
         'references a source ("according to") but has no source_url' 'unfinished placeholder "[link]"' \
         '"event" but config cta_type is "product"' "product CTA needs a URL"; do
  expect_grep "$tmp/o2" "$s"
done
ok "bad draft fails (exit 1) with every planted problem reported"

# 3. malformed JSON -> exit 2 naming the file and position
printf '{"issue_date": "2026-10-02",' > "$tmp/broken.json"
expect_exit 2 "$tmp/o3" -- $NL lint --draft "$tmp/broken.json" --config $CFG
expect_grep "$tmp/o3" "broken.json: invalid JSON at line 1"
ok "malformed JSON exits 2 with file and position"

# 4. missing field -> exit 2 naming the exact field
mutate sample_draft.json "$tmp/nobody.json" 'del d["sections"][1]["body"]'
expect_exit 2 "$tmp/o4" -- $NL lint --draft "$tmp/nobody.json" --config $CFG
expect_grep "$tmp/o4" "nobody.json: sections[2].body: missing"
ok "missing section body exits 2 naming sections[2].body"

# 5. blank cta_type (unconfigured skill) -> exit 2, never guessed
mutate $CFG "$tmp/cfg_blank.json" 'd["cta_type"] = ""'
expect_exit 2 "$tmp/o5" -- $NL lint --draft sample_draft.json --config "$tmp/cfg_blank.json"
expect_grep "$tmp/o5" "cta_type: is blank"
ok "blank cta_type exits 2 instead of guessing"

# 6. over budget -> FAIL with deterministic cut suggestion (fewest sections, least words lost)
mutate $CFG "$tmp/cfg_tight.json" 'd["max_word_count"] = 350'
expect_exit 1 "$tmp/o6" -- $NL lint --draft sample_draft.json --config "$tmp/cfg_tight.json" --brief $BRIEF
expect_grep "$tmp/o6" "427 words; limit 350. Cut ${SECT}2 (-111 words -> 316)"
mutate $CFG "$tmp/cfg_tighter.json" 'd["max_word_count"] = 200'
expect_exit 1 "$tmp/o6b" -- $NL lint --draft sample_draft.json --config "$tmp/cfg_tighter.json" --brief $BRIEF
expect_grep "$tmp/o6b" "limit 200. Cut ${SECT}2, ${SECT}3 (-231 words -> 196)"
ok "over-budget drafts get a deterministic cut plan"

# 7. fewer topics than sections -> reduced, not padded (no FAIL/WARN for count)
mutate sample_draft.json "$tmp/two.json" 'd["sections"] = d["sections"][:2]; d["brief_topic_count"] = 2; d["facts"] = d["facts"][:2]; d["preheader"] = "Capacity Forecast is live, and why one COO hid her utilization targets."; d["subject_lines"][2] = "Stop showing designers their utilization"'
expect_exit 0 "$tmp/o7" -- $NL lint --draft "$tmp/two.json" --config $CFG --brief $BRIEF
expect_grep "$tmp/o7" "2 of 3: reduced to match 2 brief topic(s), no padding"
ok "fewer brief topics reduce the section count without a failure"

# 7b. padding: more sections than brief topics -> FAIL even within the config limit (regression)
mutate sample_draft.json "$tmp/padded.json" 'd["brief_topic_count"] = 2'
expect_exit 1 "$tmp/o7b" -- $NL lint --draft "$tmp/padded.json" --config $CFG --brief $BRIEF
expect_grep "$tmp/o7b" "padding: 3 sections for 2 brief topics"
ok "3 sections for a 2-topic brief fails as padding"

# 7c. reduced draft without brief_topic_count -> WARN that says exactly what to add (regression)
mutate "$tmp/two.json" "$tmp/two_nobtc.json" 'del d["brief_topic_count"]'
expect_exit 0 "$tmp/o7c" -- $NL lint --draft "$tmp/two_nobtc.json" --config $CFG --brief $BRIEF
expect_grep "$tmp/o7c" 'add "brief_topic_count": <number of topics in the brief> to draft.json'
ok "missing brief_topic_count WARN names the exact field to add"

# 7d. all scripts are pure ASCII so they survive copy/paste (regression)
if LC_ALL=C grep -nP '[^\x00-\x7F]' ./*.py ./*.sh > "$tmp/o7d"; then cat "$tmp/o7d"; fail "non-ASCII bytes in scripts"; fi
if grep -n '\\[uUN]' newsletter.py > "$tmp/o7d2"; then cat "$tmp/o7d2"; fail "unicode escapes in newsletter.py"; fi
ok "newsletter.py, run.sh and tests.sh are pure ASCII with no unicode escapes"

# 8. a figure marked [unverified] is a WARN, an unmarked invented one is a FAIL
mutate sample_draft.json "$tmp/marked.json" 'd["sections"][0]["body"] = d["sections"][0]["body"].replace("according to our launch report.", "according to our launch report. Teams also logged 31% less overtime [unverified].")'
expect_exit 0 "$tmp/o8" -- $NL lint --draft "$tmp/marked.json" --config $CFG --brief $BRIEF
expect_grep "$tmp/o8" '"31%" marked [unverified]'
mutate sample_draft.json "$tmp/invented.json" 'd["sections"][0]["body"] = d["sections"][0]["body"].replace("according to our launch report.", "according to our launch report. Teams also logged 31% less overtime.")'
expect_exit 1 "$tmp/o8b" -- $NL lint --draft "$tmp/invented.json" --config $CFG --brief $BRIEF
expect_grep "$tmp/o8b" 'unverified figure "31%"'
ok "unverifiable figures: marked -> WARN, unmarked -> FAIL"

# 9. urgency is allowed (as WARN) only when the brief gives a real deadline
mutate sample_draft.json "$tmp/urgent.json" 'd["subject_lines"][0] = "Last call for the Oct 9 ops roundtable"'
expect_exit 1 "$tmp/o9" -- $NL lint --draft "$tmp/urgent.json" --config $CFG --brief $BRIEF
expect_grep "$tmp/o9" 'false urgency "last call" with no cta.deadline'
ok "false urgency in a subject line fails without a deadline"

# 10. reply CTA must ask a question and must not link away
mutate $CFG "$tmp/cfg_reply.json" 'd["cta_type"] = "reply"'
mutate sample_draft.json "$tmp/reply.json" 'd["cta"] = {"type": "reply", "text": "Tell us how you plan capacity."}'
expect_exit 1 "$tmp/o10" -- $NL lint --draft "$tmp/reply.json" --config "$tmp/cfg_reply.json" --brief $BRIEF
expect_grep "$tmp/o10" "reply CTA must ask the reader a genuine question"
ok "reply CTA without a question fails"

# 11. rendered HTML escapes hostile text and still passes check-html
mutate sample_draft.json "$tmp/hostile.json" 'd["sections"][0]["headline"] = "<script>alert(1)</script> & <div>"'
$NL render --draft "$tmp/hostile.json" --config $CFG --format html --out "$tmp/hostile.html"
expect_grep "$tmp/hostile.html" "&lt;script&gt;alert(1)&lt;/script&gt; &amp; &lt;div&gt;"
expect_exit 0 "$tmp/o11" -- $NL check-html --file "$tmp/hostile.html"
ok "render escapes text; output still passes check-html"

# 12. check-html rejects web-style HTML
cat > "$tmp/webby.html" <<'HTML'
<html><head><link rel="stylesheet" href="https://cdn.example/x.css"><style>.a{color:red}</style></head>
<body><div style="display:flex;gap:8px"><div class="col" style="width:800px;position:absolute">
<a href="javascript:alert(1)" onclick="x()">Go</a><img src="http://x.example/a.png"></div></body></html>
HTML
expect_exit 1 "$tmp/o12" -- $NL check-html --file "$tmp/webby.html"
for s in "<link> is not email-safe" "<style> is not email-safe" "<div> is not email-safe" "flexbox" \
         "style width:800px exceeds 600px" "positioning is stripped" "event handler attribute" \
         'href "javascript:alert(1)"' "<img> without alt text" "no <table>" "<div> is never closed"; do
  expect_grep "$tmp/o12" "$s"
done
ok "check-html fails web-style markup (div, style, link, flex, width, script)"

# 13. config validation: bad colour and unknown format exit 2
mutate $CFG "$tmp/cfg_colour.json" 'd["brand"]["accent_color"] = "teal"'
expect_exit 2 "$tmp/o13" -- $NL render --draft sample_draft.json --config "$tmp/cfg_colour.json"
expect_grep "$tmp/o13" "brand.accent_color: 'teal' is not a #RRGGBB hex colour"
ok "invalid brand colour exits 2"

# 14. determinism: run.sh twice gives identical output, equal to expected_output.txt
bash run.sh > "$tmp/r1"
bash run.sh > "$tmp/r2"
cmp -s "$tmp/r1" "$tmp/r2" || fail "run.sh output differs between runs"
cmp -s "$tmp/r1" expected_output.txt || { diff "$tmp/r1" expected_output.txt | head -20; fail "run.sh output differs from expected_output.txt"; }
ok "run.sh is deterministic and matches expected_output.txt"

# 15. topical "deadline" in a subject line is fine; "deadline today" without cta.deadline fails
mutate sample_draft.json "$tmp/dl_ok.json" 'd["subject_lines"][0] = "Why the deadline you set is the deadline you get"'
expect_exit 0 "$tmp/o15a" -- $NL lint --draft "$tmp/dl_ok.json" --config $CFG --brief sample_brief.md
mutate sample_draft.json "$tmp/dl_bad.json" 'd["subject_lines"][0] = "Deadline today: book your seat"'
expect_exit 1 "$tmp/o15b" -- $NL lint --draft "$tmp/dl_bad.json" --config $CFG --brief sample_brief.md
expect_grep "$tmp/o15b" 'false urgency "deadline today"'
ok "bare 'deadline' is not urgency; 'deadline today' without cta.deadline fails"

echo "ALL TESTS PASSED ($pass tests)"

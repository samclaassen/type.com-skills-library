#!/usr/bin/env bash
# End-to-end checks for Holiday Support Macros. No network or credentials needed.
#   bash tests/run_tests.sh
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
export PYTHONDONTWRITEBYTECODE=1
pass=0
ok() { pass=$((pass + 1)); echo "ok $pass - $1"; }
fail() { echo "FAIL - $1"; exit 1; }
has() { grep -qF -- "$2" "$1" || { echo "--- missing: $2"; cat "$1"; fail "$3"; }; }
hs() { python3 scripts/holiday_support.py --as-of 2026-10-07 "$@"; }

python3 tests/make_cases.py "$tmp" > /dev/null

# 1. Sample report is byte-identical to the published expected output, and stable across runs.
bash examples/run.sh "$tmp/sample" > "$tmp/out.txt" 2> /dev/null
cmp -s "$tmp/out.txt" examples/expected_output.txt || { diff "$tmp/out.txt" examples/expected_output.txt | head; fail "sample output differs"; }
bash examples/run.sh "$tmp/sample2" > "$tmp/out2.txt" 2> /dev/null
cmp -s "$tmp/out.txt" "$tmp/out2.txt" || fail "output is not deterministic"
ok "sample report matches examples/expected_output.txt"

# 2. The sample's macros, preview and daily forecast match examples/expected/ byte for byte.
for f in macros.csv macros_preview.md forecast.csv; do
  cmp -s "$tmp/sample/$f" "examples/expected/$f" || { diff "$tmp/sample/$f" "examples/expected/$f" | head; fail "$f differs"; }
done
ok "macros.csv, macros_preview.md and forecast.csv match examples/expected/"

# 3. macros.csv is valid CSV: header name,tags,body and the 17 expected macros, each with tags and a body.
python3 - "$tmp/sample/macros.csv" <<'EOF' || fail "macros.csv is not the expected CSV"
import csv, sys
rows = list(csv.reader(open(sys.argv[1], encoding="utf-8", newline="")))
assert rows[0] == ["name", "tags", "body"], rows[0]
names = [r[0] for r in rows[1:]]
expected = ["WISMO: not shipped yet", "WISMO: BFCM order not shipped yet", "WISMO: shipped, in transit",
            "Shipping: holiday order-by dates", "Shipping: only the fastest method is still on time",
            "Shipping: after the last order date", "Discount: code not working", "Discount: item not in the sale",
            "Order: change or cancel", "Order: change the shipping address", "Returns: holiday window",
            "Returns: returning a gift", "Damaged or defective item", "Gift options", "Out of stock: when is it back?",
            "Holiday support hours", "Shipping: international orders"]
assert names == ["Holiday 2026 - " + n for n in expected], names
assert all(len(r) == 3 and r[1].startswith("holiday-2026,") and r[2].startswith("Hi {{customer_first_name}},")
           for r in rows[1:])
EOF
ok "macros.csv is valid CSV with the 17 expected macros"

# 4. The fixture generator reproduces the bundled sample data exactly.
cp -r examples "$tmp/ex" && python3 "$tmp/ex/make_fixtures.py" > /dev/null
for f in tickets.csv orders.csv macros_existing.csv policies.json; do
  cmp -s "$tmp/ex/data/$f" "examples/data/$f" || fail "make_fixtures.py does not reproduce $f"
done
ok "make_fixtures.py regenerates examples/data byte for byte"

# 5. Classification of 39 hand-labeled tickets (whole words, 'not' phrases, negation within 3 words, tags, curly
#    apostrophes, escalation and "hasn't arrived" ahead of general types), plus the escalate column.
hs --policies "$tmp/policies_full.json" --tickets "$tmp/labeled.csv" --out-dir "$tmp/lab" > /dev/null
python3 - "$tmp" <<'EOF' || fail "hand-labeled tickets misclassified"
import csv, sys
t = sys.argv[1]
exp = {r["id"]: r["expected"] for r in csv.DictReader(open(t + "/labeled.csv", encoding="utf-8"))}
got = {r["id"]: r["type"] for r in csv.DictReader(open(t + "/lab/tickets_classified.csv", encoding="utf-8"))}
bad = [(i, exp[i], got.get(i)) for i in exp if exp[i] != got.get(i)]
assert len(exp) == 39 and not bad, bad
esc = {r["id"]: r["escalate"] for r in csv.DictReader(open(t + "/lab/tickets_classified.csv", encoding="utf-8"))}
assert esc["L39"] == "yes" and esc["L01"] == "no", esc
EOF
ok "39 hand-labeled tickets classified as labeled, escalations flagged"

# 6. Rule precedence: the first matching type wins, and moving a type up the list changes the result.
grep -q '^L22,2025-11-28,damaged_defective,no,broken,' "$tmp/lab/tickets_classified.csv" || fail "damaged should beat refund"
grep -q '^L12,2025-11-28,price_adjustment,no,refund the difference,' "$tmp/lab/tickets_classified.csv" || fail "price adjustment should beat return"
grep -q '^L13,2025-11-28,discount_code,no,discount code,' "$tmp/lab/tickets_classified.csv" || fail "discount should beat cancel"
hs --policies "$tmp/policies_full.json" --tickets "$tmp/labeled.csv" --rules "$tmp/rules_swapped.json" --out-dir "$tmp/lab2" > /dev/null
grep -q '^L22,2025-11-28,return_exchange,no,refund,' "$tmp/lab2/tickets_classified.csv" || fail "rule order not respected"
ok "rule precedence follows the order of the rules file"

# 7. Forecast and staffing on a hand-computed case.
#    Last season: 36 tickets, 200 orders -> 18.0 per 100 orders (WISMO 18 -> 9.0).
#    Ratio = this season's orders / last season's over the 7 days up to the day (here, from the first day):
#    Fri 150/100 = 1.5, Sat (150+30)/(100+50) = 1.2, Sun (150+30+120)/200 = 1.5.
#    Forecast 18*1.5 = 27.0, 8*1.2 = 9.6, 10*1.5 = 15.0 -> 51.6 tickets (52), +43.3% on 36.
#    10 tickets per agent per 8-hour shift: Fri 27*8/10 = 21.6 agent-hours, 3 agents; capacity 2*10 = 20 -> short 7.
#    Carry-over: 7.0, then 7.0+9.6-10 = 6.6, then 6.6+15.0-10 = 11.6 (12) on Sun, not cleared.
#    Agents needed include the tickets waiting from the day before: Sat (7+9.6)/10 -> 2, Sun (6.6+15)/10 -> 3.
hs --policies "$tmp/policies_full.json" --tickets "$tmp/hand_tickets.csv" --orders "$tmp/hand_orders.csv" \
   --agents 2 --weekend-agents 1 --tickets-per-agent 10 --json "$tmp/fc.json" > "$tmp/fc.md"
has "$tmp/fc.md" "36 tickets over 3 days and 200 orders (18.0 tickets per 100 orders)" "tickets per 100 orders"
has "$tmp/fc.md" "| Where is my order (WISMO) | 18 | 50.0% | 9.0 | Fri Nov 28 (10) |" "per-type rate"
has "$tmp/fc.md" "Projected orders 300 vs 200 last season (+50.0%). Forecast 52 tickets (+43.3%)." "forecast total"
has "$tmp/fc.md" "| Fri Nov 27 | 150 | 27 | WISMO 15, Return or exchange 6 | 21.6 | 3 | 2 | 7 | 7 |" "peak-day staffing"
has "$tmp/fc.md" "Unanswered tickets peak at 12 on Sun Nov 29 and don't clear before the season ends." "carry-over"
python3 - "$tmp/fc.json" <<'EOF' || fail "forecast JSON differs from the hand computation"
import json, sys
a = json.load(open(sys.argv[1]))
days = [(d["date"], d["tickets"], d["agents_needed"]) for d in a["forecast"]["days"]]
assert days == [("2026-11-27", 27.0, 3), ("2026-11-28", 9.6, 2), ("2026-11-29", 15.0, 3)], days
assert a["forecast"]["total"] == 51.6 and a["tickets"]["by_type"]["wismo"]["per_100_orders"] == 9.0
assert a["short_days"] == ["2026-11-27", "2026-11-29"], a["short_days"]
EOF
ok "forecast and staffing match the hand-computed case"

# 8. Policy gaps: a type with >= 3% of tickets and no policy is a GAP with a decide-by date; under 3% is not.
hs --policies "$tmp/policies_gap.json" --tickets "$tmp/hand_tickets.csv" --out-dir "$tmp/gap" > "$tmp/gap.md"
has "$tmp/gap.md" "**CRITICAL**: No written policy for “Return or exchange” tickets (\`returns\` missing in policies.json), but they were 4 of 36 tickets (11.1%) last season. Decide it by Nov 13." "returns gap"
has "$tmp/gap.md" "| Damaged or defective | 11.1% | 0 of 1 | **GAP**: \`damaged_defective\` missing. Decide by Nov 13 |" "damaged gap"
has "$tmp/gap.md" "| Gift options | 2.8% | 0 of 1 | No policy (\`gift_options\`), low volume |" "low-volume type"
! grep -q "No written policy for “Gift options”" "$tmp/gap.md" || fail "a type under 3% was reported as a gap"
has "$tmp/sample/macros_preview.md" "need price_adjustment in policies.json" "sample price-adjustment gap"
ok "policy gaps found at >= 3% with a decide-by date"

# 9. A missing policy means no macro (and a note); optional lines drop; present policies generate their macros.
python3 - "$tmp" <<'EOF' || fail "macro generated without its policy"
import csv, sys
t = sys.argv[1]
body = {r["name"]: r["body"] for r in csv.DictReader(open(t + "/gap/macros.csv", encoding="utf-8"))}
assert not [n for n in body if "Returns:" in n or "Damaged" in n or "Gift options" in n], list(body)
offered = body["Holiday 2026 - Price adjustment: request (we offer them)"]
assert "within 14 days of your order" in offered and "don't apply to Last Chance" in offered, offered
assert "Holiday 2026 - Price adjustment: request (we don't offer them)" not in body
assert "return" not in body["Holiday 2026 - Order: change or cancel"].lower()
EOF
has "$tmp/gap.md" "- Not generated: “Holiday 2026 - Damaged or defective item”: needs \`damaged_defective\` in policies.json." "not-generated note"
hs --policies "$tmp/policies_nobacklog.json" --tickets "$tmp/hand_tickets.csv" > "$tmp/nob.md"
has "$tmp/nob.md" "**WARNING**: Macro “Holiday 2026 - WISMO: BFCM order not shipped yet” not generated: needs \`bfcm_backlog\` in policies.json." "backlog warning"
ok "missing policy -> macro not generated and the gap reported"

# 10. Stale-macro audit: past dates, dates and numbers that contradict the policy, unknown codes, promises.
hs --policies "$tmp/policies_full.json" --tickets "$tmp/hand_tickets.csv" --macros "$tmp/stale_macros.csv" > "$tmp/stale.md"
has "$tmp/stale.md" "| Old shipping | CRITICAL | Says “Order by Dec 18, 2025”: that date has passed, and this year's last order dates are Dec 14 (Standard) and Dec 21 (Express) |" "past date"
has "$tmp/stale.md" "| Ground cutoff | CRITICAL | Says “Order by Dec 17”: this year's last order dates are" "wrong cutoff"
has "$tmp/stale.md" "Says “items within 14 days” but the return window is 30 days (returns.standard_days)" "return window"
has "$tmp/stale.md" "doesn't mention the holiday window (orders Nov 1–Dec 24 can be returned until Jan 31, 2027)" "holiday window"
has "$tmp/stale.md" "Mentions code OLDCODE15, which isn't one of this year's codes (MAYA20, VIPEARLY and WELCOME10)" "old code"
has "$tmp/stale.md" "Mentions code FREESHIP" "code after the word 'code'"
has "$tmp/stale.md" "Promises “definitely arrive in time for the holidays”" "promise"
has "$tmp/stale.md" "| Year | WARNING | Mentions 2024 |" "old year"
has "$tmp/stale.md" "Says “over \$50” but the free-shipping threshold is \$75" "free-shipping threshold"
has "$tmp/stale.md" "Says “damage within 7 days” but the damage-report window is 14 days" "damage window"
has "$tmp/stale.md" "Says “cancel within 2 hours” but the change/cancel window is 1 hour" "edit window"
has "$tmp/stale.md" "Says “reply within 48 hours” but the first-reply time is 24 hours" "reply time"
has "$tmp/stale.md" "No problems found in 3: Returns ok, Clean, Refund timing." "clean macros flagged"
ok "stale dates, wrong numbers, old codes and promises detected; clean macros pass"

# 11. Helpdesk placeholders survive generation, and no policy fact is left unfilled.
python3 - "$tmp/sample/macros.csv" <<'EOF' || fail "placeholders not preserved"
import csv, json, re, sys
tpl = {t["name"]: t for t in json.load(open("references/macro_templates.json", encoding="utf-8"))["templates"]}
allowed = {"customer_first_name", "order_number", "tracking_url", "agent_first_name"}
for r in csv.DictReader(open(sys.argv[1], encoding="utf-8")):
    t = tpl[r["name"].split(" - ", 1)[1]]
    want = [p for line in t["body"] if not line.startswith("?") for p in re.findall(r"\{\{(\w+)\}\}", line)]
    got = re.findall(r"\{\{(\w+)\}\}", r["body"])
    assert set(got) <= allowed and want == got, (r["name"], want, got)
    assert "[[" not in r["body"] and "]]" not in r["body"], r["name"]
EOF
ok "placeholders preserved, no unfilled policy facts"

# 12. Invalid input exits 2 with the reason; templates may not carry facts of their own.
set +e
hs --policies "$tmp/policies_full.json" --tickets "$tmp/bad_dates.csv" > /dev/null 2> "$tmp/e1"; r1=$?
hs --policies "$tmp/policies_full.json" --tickets "$tmp/dup_ids.csv" > /dev/null 2> "$tmp/e2"; r2=$?
hs --policies "$tmp/policies_badtype.json" --tickets "$tmp/hand_tickets.csv" > /dev/null 2> "$tmp/e3"; r3=$?
hs --policies "$tmp/policies_lastyear.json" --tickets "$tmp/hand_tickets.csv" > /dev/null 2> "$tmp/e4"; r4=$?
hs --policies "$tmp/policies_full.json" --tickets "$tmp/hand_tickets.csv" --orders "$tmp/gap_orders.csv" > /dev/null 2> "$tmp/e5"; r5=$?
hs --policies "$tmp/policies_full.json" --tickets "$tmp/hand_tickets.csv" --templates "$tmp/digit_templates.json" > /dev/null 2> "$tmp/e6"; r6=$?
hs --policies "$tmp/policies_full.json" --tickets "$tmp/hand_tickets.csv" --templates "$tmp/bad_placeholder.json" > /dev/null 2> "$tmp/e7"; r7=$?
hs --policies "$tmp/policies_full.json" --tickets "$tmp/hand_tickets.csv" --rules "$tmp/bad_rules.json" > /dev/null 2> "$tmp/e8"; r8=$?
hs --policies "$tmp/policies_full.json" --tickets "$tmp/nope.csv" > /dev/null 2> "$tmp/e9"; r9=$?
set -e
[ "$r1" = 2 ] && grep -q "line 4: '28/11/2025 10:00' is not a real date" "$tmp/e1" || fail "bad date accepted (rc=$r1)"
[ "$r2" = 2 ] && grep -q "duplicate ticket id 'H005' (first on line 6)" "$tmp/e2" || fail "duplicate id accepted (rc=$r2)"
[ "$r3" = 2 ] && grep -q "returns.standard_days must be a whole number" "$tmp/e3" || fail "bad policy type accepted (rc=$r3)"
[ "$r4" = 2 ] && grep -q "update policies.json for this season" "$tmp/e4" || fail "last season's policies accepted (rc=$r4)"
[ "$r5" = 2 ] && grep -q "orders file has no row for 1 day of last season (first: 2025-11-29)" "$tmp/e5" || fail "orders gap accepted (rc=$r5)"
[ "$r6" = 2 ] && grep -q "Templates hold wording only" "$tmp/e6" || fail "template with a number accepted (rc=$r6)"
[ "$r7" = 2 ] && grep -q "allowed placeholders" "$tmp/e7" || fail "unknown placeholder accepted (rc=$r7)"
[ "$r8" = 2 ] && grep -q "keywords must be a non-empty list" "$tmp/e8" || fail "empty rule accepted (rc=$r8)"
[ "$r9" = 2 ] && grep -q "cannot read tickets file" "$tmp/e9" || fail "missing file accepted (rc=$r9)"
ok "invalid input exits 2 with the reason (incl. templates that carry facts)"

# 13. [QA 2.3 BLOCKER] The real Holiday Shipping Cutoff Checker --json schema is read without losing data: full
#     carrier names, checkout rates, the backlog fields bfcm_window / bfcm_ship_within_business_days / bfcm_clears.
#     Where policies.json disagrees, the checker's computed values win and the report warns with both values.
hs --policies "$tmp/policies_full.json" --tickets "$tmp/hand_tickets.csv" --cutoffs "$tmp/cutoffs_checker.json" \
   --out-dir "$tmp/cut" > "$tmp/cut.md"
has "$tmp/cut.md" "**WARNING**: policies.json and cutoffs_checker.json disagree on the BFCM backlog: policies.json says orders Nov 26–Nov 30 ship within 3 business days, caught up by Thu Dec 3; the Cutoff Checker computed orders Nov 26–Nov 30 ship within 4 business days, caught up by Mon Dec 7. The macros use the Cutoff Checker's values." "backlog conflict warning"
has "$tmp/cut/macros_preview.md" "ship within 4 business days, and we expect to be fully caught up by Monday, December 7." "checker backlog used"
has "$tmp/cut/macros_preview.md" "- Standard: order by Monday, December 14" "checker rate date"
has "$tmp/cut/macros_preview.md" "- Express: order by Monday, December 21" "checker rate date (express)"
has "$tmp/cut/macros_preview.md" "Use: Nov 26–Dec 7" "backlog macro window follows the checker"
! grep -q "caught up by Thursday, December 3" "$tmp/cut/macros.csv" || fail "macro promises an earlier clear date than the checker"
! grep -qE "(^|[^S] )Ground Advantage|- Ground:|UPS Ground: order by" "$tmp/cut/macros.csv" || fail "carrier names lost or carrier dates shown"
! grep -q "replaced the shipping dates" "$tmp/cut.md" || fail "false 'replaced' note"
# No bfcm_backlog in policies.json: the backlog macro is built from the cutoffs file instead of disappearing.
hs --policies "$tmp/policies_nobacklog.json" --tickets "$tmp/hand_tickets.csv" --cutoffs "$tmp/cutoffs_checker.json" \
   --out-dir "$tmp/cut2" > "$tmp/cut2.md"
grep -q "Holiday 2026 - WISMO: BFCM order not shipped yet" "$tmp/cut2/macros.csv" || fail "backlog macro dropped"
has "$tmp/cut2.md" "The BFCM backlog macro uses the backlog computed in cutoffs_checker.json" "backlog from cutoffs note"
# Unknown or missing fields, and the old hand-written shape, exit 2 instead of being dropped.
set +e
hs --policies "$tmp/policies_full.json" --tickets "$tmp/hand_tickets.csv" --cutoffs "$tmp/cutoffs_unknown.json" > /dev/null 2> "$tmp/c1"; c1=$?
hs --policies "$tmp/policies_full.json" --tickets "$tmp/hand_tickets.csv" --cutoffs "$tmp/cutoffs_missing.json" > /dev/null 2> "$tmp/c2"; c2=$?
hs --policies "$tmp/policies_full.json" --tickets "$tmp/hand_tickets.csv" --cutoffs "$tmp/cutoffs_legacy.json" > /dev/null 2> "$tmp/c3"; c3=$?
set -e
[ "$c1" = 2 ] && grep -q "unknown field backlog.bfcm_note" "$tmp/c1" || fail "unknown cutoffs field accepted (rc=$c1)"
[ "$c2" = 2 ] && grep -q "missing checkout_rates" "$tmp/c2" || fail "missing cutoffs field accepted (rc=$c2)"
[ "$c3" = 2 ] && grep -q "hand-written cutoffs file" "$tmp/c3" || fail "old cutoffs shape accepted (rc=$c3)"
ok "Cutoff Checker --json: names, rates and backlog read; conflicts warned; bad files exit 2"

# 14. [QA 2.6] Re-run with the live macros export: generic placeholders left unmapped are "needs manual fill", never
#     verified; mapped to Gorgias they are verified; on Help Scout the order number and tracking link stay manual;
#     an edited fact is caught.
hs --policies examples/data/policies.json --tickets examples/data/tickets.csv --orders examples/data/orders.csv \
   --macros examples/expected/macros.csv > "$tmp/ver.md"
[ "$(grep -c '| verified |' "$tmp/ver.md")" = 0 ] || fail "unmapped placeholders marked verified"
[ "$(grep -c '| needs manual fill |' "$tmp/ver.md")" = 17 ] || fail "unmapped drafts should need manual fill"
has "$tmp/ver.md" "No problems found in 17:" "own macros flagged by the audit"
has "$tmp/ver.md" "- Needs manual fill: “Holiday 2026 - WISMO: shipped, in transit”: \`{{customer_first_name}}\`, \`{{order_number}}\`, \`{{tracking_url}}\` and \`{{agent_first_name}}\` still in generic form, not mapped to your helpdesk." "manual-fill note"
python3 - "$tmp" <<'EOF'
import csv, sys
GORGIAS = {"customer_first_name": "{{ticket.customer.firstname}}", "agent_first_name": "{{current_user.firstname}}",
           "order_number": "{{ticket.customer.integrations.shopify.orders[0].name}}",
           "tracking_url": "{{ticket.customer.integrations.shopify.orders[0].fulfillments[0].tracking_url}}"}
HELPSCOUT = {"customer_first_name": "{%customer.firstName,fallback=there%}", "agent_first_name": "{%user.firstName%}",
             "order_number": "[order number]", "tracking_url": "[tracking link]"}
rows = list(csv.reader(open("examples/expected/macros.csv", encoding="utf-8", newline="")))
for name, m in (("gorgias", GORGIAS), ("helpscout", HELPSCOUT)):
    out = [rows[0]]
    for r in rows[1:]:
        b = r[2]
        for k, v in m.items():
            b = b.replace("{{%s}}" % k, v)
        out.append([r[0], r[1], b])
    csv.writer(open(sys.argv[1] + "/live_" + name + ".csv", "w", encoding="utf-8", newline=""),
               lineterminator="\n").writerows(out)
for r in rows:
    if r[0].endswith("holiday order-by dates"):
        r[2] = r[2].replace("Monday, December 14", "Friday, December 18")
csv.writer(open(sys.argv[1] + "/edited.csv", "w", encoding="utf-8", newline=""), lineterminator="\n").writerows(rows)
EOF
hs --policies examples/data/policies.json --tickets examples/data/tickets.csv --orders examples/data/orders.csv \
   --macros "$tmp/live_gorgias.csv" --helpdesk gorgias > "$tmp/ver_g.md"
[ "$(grep -c '| verified |' "$tmp/ver_g.md")" = 18 ] || fail "mapped Gorgias macros should all be verified"
hs --policies examples/data/policies.json --tickets examples/data/tickets.csv --orders examples/data/orders.csv \
   --macros "$tmp/live_helpscout.csv" --helpdesk help-scout > "$tmp/ver_h.md"
has "$tmp/ver_h.md" "| Holiday 2026 - WISMO: shipped, in transit | WISMO | all season | none (no policy facts) | needs manual fill |" "Help Scout tracking link"
has "$tmp/ver_h.md" "| Holiday 2026 - Gift options | Gift options | all season | gift_options | verified |" "Help Scout macro without order fields"
has "$tmp/ver_h.md" "\`{{order_number}}\` and \`{{tracking_url}}\`: no variable in Help Scout, so the agent must type it in" "Help Scout reason"
hs --policies examples/data/policies.json --tickets examples/data/tickets.csv --orders examples/data/orders.csv \
   --macros "$tmp/edited.csv" > "$tmp/ver2.md"
has "$tmp/ver2.md" "| Holiday 2026 - Shipping: holiday order-by dates | Shipping deadline question | until Dec 14 | shipping, bfcm_backlog | applied |" "edited macro not downgraded"
has "$tmp/ver2.md" "says “by Friday, December 18”: this year's last order dates are" "edited date not caught"
ok "live macros: unmapped placeholders need manual fill, mapped ones verified, edited fact caught"

# 15. Scripts parse under Python 3.8 grammar.
python3 - <<'EOF' || fail "not Python 3.8 compatible"
import ast
for p in ("scripts/holiday_support.py", "examples/make_fixtures.py", "tests/make_cases.py"):
    ast.parse(open(p, encoding="utf-8").read(), p, feature_version=(3, 8))
EOF
ok "scripts parse as Python 3.8"

# 16. [QA 2.1 BLOCKER] Templates can't carry promises of their own, and the sample macros promise nothing that isn't
#     in policies.json (final sale, gift receipt prices, price-adjustment remedy, refund approval, restock page).
set +e
hs --policies "$tmp/policies_full.json" --tickets "$tmp/hand_tickets.csv" --templates "$tmp/promise_templates.json" > /dev/null 2> "$tmp/p1"; p1=$?
set -e
[ "$p1" = 2 ] && grep -q "contains the promise 'guaranteed'" "$tmp/p1" || fail "template promise accepted (rc=$p1)"
python3 - "$tmp/sample/macros.csv" <<'EOF' || fail "sample macros promise something policies.json doesn't say"
import csv, sys
body = {r["name"]: r["body"] for r in csv.DictReader(open(sys.argv[1], encoding="utf-8"))}
text = "\n".join(body.values())
for bad in ("anything you don't need", "don't show prices", "refund the difference", "on the product page"):
    assert bad not in text, bad
change = body["Holiday 2026 - Order: change or cancel"]
assert "return eligible items" in change and "Items from Last Chance and Gift Cards are final sale." in change, change
dmg = body["Holiday 2026 - Damaged or defective item"]
assert "If that's worth more than $150, the support lead approves it first" in dmg, dmg
assert body["Holiday 2026 - Gift options"].count("gift receipt") == 1
EOF
hs --policies "$tmp/policies_storecredit.json" --tickets "$tmp/hand_tickets.csv" --out-dir "$tmp/sc" > /dev/null
python3 - "$tmp/sc/macros.csv" <<'EOF' || fail "price-adjustment remedy not taken from policies.json"
import csv, sys
body = {r["name"]: r["body"] for r in csv.DictReader(open(sys.argv[1], encoding="utf-8"))}
pa = body["Holiday 2026 - Price adjustment: request (we offer them)"]
assert "store credit, not a refund" in pa and "we'll refund the difference" not in pa, pa
EOF
ok "templates can't carry promises; every promise in the sample macros comes from policies.json"

# 17. [QA 2.2 BLOCKER] Shipping dates are given per checkout rate (the earliest of its carriers), never per carrier,
#     with the checkout rate names and the coverage qualifier.
python3 - "$tmp/sample/macros.csv" <<'EOF' || fail "shipping macro shows carrier dates or misses the rates"
import csv, sys
body = {r["name"]: r["body"] for r in csv.DictReader(open(sys.argv[1], encoding="utf-8"))}
s = body["Holiday 2026 - Shipping: holiday order-by dates"]
for want in ("- Standard: order by Monday, December 14", "- Express: order by Monday, December 21",
             "Choose Standard or Express at checkout.", "most addresses (95% of our customers)"):
    assert want in s, (want, s)
assert "UPS Ground" not in s and "Tuesday, December 15" not in s and "shipping method at checkout" not in s, s
last = body["Holiday 2026 - Shipping: only the fastest method is still on time"]
assert "passed for Standard" in last and "choose Express at checkout" in last, last
EOF
ok "shipping macros use checkout rates: Standard Dec 14, Express Dec 21, 95% qualifier"

# 18. [QA 2.3 BLOCKER, integration] Fresh run: Budget Planner --csv -> Shipping Cutoff Checker (--aov 88.66, the
#     planner's AOV) --json -> this skill. Needs the sibling packages; skipped (and said so) when they're absent.
pk="$(cd .. && pwd)"
if [ -f "$pk/holiday-shipping-cutoffs/scripts/check_cutoffs.py" ] && [ -f "$pk/bfcm-budget-planner/scripts/plan_budget.py" ]; then
  (cd "$pk/bfcm-budget-planner" && python3 scripts/plan_budget.py --history examples/data/history.csv \
     --economics examples/data/economics.json --csv "$tmp/i_plan.csv" > /dev/null)
  (cd "$pk/holiday-shipping-cutoffs" && python3 scripts/check_cutoffs.py --account "Fernhill Goods" \
     --carriers examples/data/carriers.csv --warehouse examples/data/warehouse.json \
     --destinations examples/data/destinations.csv --demand "$tmp/i_plan.csv" --aov 88.66 \
     --promises examples/data/promises.csv --as-of 2026-10-07 --json "$tmp/i_cut.json" > /dev/null)
  hs --policies examples/data/policies.json --tickets examples/data/tickets.csv --orders examples/data/orders.csv \
     --cutoffs "$tmp/i_cut.json" --out-dir "$tmp/int" > "$tmp/int.md"
  python3 - "$tmp" <<'EOF' || fail "shipping JSON -> support handoff lost or changed data"
import csv, datetime as dt, json, sys
t = sys.argv[1]
c = json.load(open(t + "/i_cut.json", encoding="utf-8"))
rep = open(t + "/int.md", encoding="utf-8").read()
prev = open(t + "/int/macros_preview.md", encoding="utf-8").read()
body = {r["name"]: r["body"] for r in csv.DictReader(open(t + "/int/macros.csv", encoding="utf-8"))}
DAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
MONTHS = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
          "November", "December")
def long(s):
    d = dt.date.fromisoformat(s)
    return "%s, %s %d" % (DAYS[d.weekday()], MONTHS[d.month - 1], d.day)
for g in c["checkout_rates"]:
    assert "- %s: order by %s" % (g["rate"], long(g["last_safe_order_date"])) in prev, g
names = [s["name"] for s in c["services"]]
assert "USPS Ground Advantage" in names and "UPS Ground" in names, names
for n in names:  # customers pick a rate, so no carrier appears with its own date
    assert "%s: order by" % n not in prev, n
b = c["backlog"]
bl = body["Holiday 2026 - WISMO: BFCM order not shipped yet"]
assert "ship within %d business days" % b["bfcm_ship_within_business_days"] in bl, bl
assert "caught up by %s." % long(b["bfcm_clears"]) in bl, bl
if (b["bfcm_ship_within_business_days"], b["bfcm_clears"]) != (3, "2026-12-03"):
    assert "disagree on the BFCM backlog: policies.json says orders Nov 26–Nov 30 ship within 3 business days" in rep
    assert "caught up by Thursday, December 3" not in bl
print("    checker backlog: %d business days, clears %s" % (b["bfcm_ship_within_business_days"], b["bfcm_clears"]))
EOF
  ok "planner CSV -> cutoff checker --json -> support: rate names, carrier names and backlog clear date carried over"
else
  ok "planner -> cutoff checker -> support integration (skipped: sibling packages not found next to this one)"
fi

# 19. [QA 2.4] Planner handoff: overlapping dates explain what to do; --prefer-plan lets the plan replace them; the
#     planner's --csv (no orders column) is refused with a pointer to its --json plan.
set +e
hs --policies examples/data/policies.json --tickets examples/data/tickets.csv --orders examples/data/orders.csv \
   --orders "$tmp/plan.json" > /dev/null 2> "$tmp/o1"; o1=$?
hs --policies examples/data/policies.json --tickets examples/data/tickets.csv --orders examples/data/orders.csv \
   --orders "$tmp/planner_plan.csv" > /dev/null 2> "$tmp/o2"; o2=$?
set -e
[ "$o1" = 2 ] && grep -q "remove this season's projection rows from the orders file, or add --prefer-plan" "$tmp/o1" || fail "duplicate-date message (rc=$o1)"
[ "$o2" = 2 ] && grep -q "looks like the BFCM Holiday Budget Planner's --csv plan" "$tmp/o2" || fail "planner CSV message (rc=$o2)"
hs --policies examples/data/policies.json --tickets examples/data/tickets.csv --orders examples/data/orders.csv \
   --orders "$tmp/plan.json" --prefer-plan > "$tmp/pp.md"
has "$tmp/pp.md" "--prefer-plan: the planner's plan replaced 23 days of the orders CSV (Nov 14–Dec 6)." "prefer-plan note"
ok "planner handoff: clear duplicate-date message, --prefer-plan, planner CSV refused"

# 20. [QA 2.5] Weekend cover only inside support_hours.weekend_coverage: other weekends have no one on shift.
hs --policies "$tmp/policies_noweekend.json" --tickets "$tmp/hand_tickets.csv" --orders "$tmp/hand_orders.csv" \
   --agents 2 --weekend-agents 1 --tickets-per-agent 10 --json "$tmp/nw.json" > /dev/null
python3 - "$tmp/nw.json" <<'EOF' || fail "weekend outside the coverage window still had capacity"
import json, sys
a = json.load(open(sys.argv[1]))
assert [d["capacity"] for d in a["forecast"]["days"]] == [20, 0, 0], a["forecast"]["days"]
assert a["short_days"] == ["2026-11-27", "2026-11-28", "2026-11-29"], a["short_days"]
EOF
has "$tmp/out.txt" "1 on weekends from Sat Nov 28 to Sun Dec 20 only (support_hours.weekend_coverage)" "sample weekend window"
ok "weekend capacity only inside the weekend coverage window"

# 21. [QA 2.8] PII masked in report examples (phone, name, street address, email); ticket CSV is formula-safe.
hs --policies "$tmp/policies_full.json" --tickets "$tmp/pii_tickets.csv" --out-dir "$tmp/pii" > "$tmp/pii.md"
has "$tmp/pii.md" "“Call me [phone] - [name] [address]”" "PII masked"
! grep -qE "555-123|John Smith|Elm St|jane@example" "$tmp/pii.md" || fail "PII reached the report"
grep -q "^P002,2025-11-29,other,no,,\"'=HYPERLINK" "$tmp/pii/tickets_classified.csv" || fail "formula not neutralised"
grep -q ",'+1 then @me$" "$tmp/pii/tickets_classified.csv" || fail "formula in body not neutralised"
ok "PII masked in the report; tickets_classified.csv is safe to open in a spreadsheet"

# 22. [QA 2.9] Ambiguous slash dates need --date-format.
set +e
hs --policies "$tmp/policies_full.json" --tickets "$tmp/ambiguous_dates.csv" > /dev/null 2> "$tmp/d1"; d1=$?
set -e
[ "$d1" = 2 ] && grep -q "Pass --date-format mdy" "$tmp/d1" || fail "ambiguous dates accepted (rc=$d1)"
hs --policies "$tmp/policies_full.json" --tickets "$tmp/ambiguous_dates.csv" --date-format mdy > "$tmp/d2.md"
has "$tmp/d2.md" "Last season: 5 tickets (Dec 1–Dec 5, 2025)" "mdy dates"
ok "ambiguous MM/DD vs DD/MM dates need --date-format"


# 23. [QA re-review B-3] While a BFCM backlog exists, no macro promises same-day shipping on its own: every
#     same-day sentence carries the backlog window, its business days and the catch-up date.
bash examples/run.sh "$tmp/b3_sample" > /dev/null 2>&1
grep -q "same-day shipping resumes once we're caught up, which we expect by Thursday, December 3" "$tmp/b3_sample/macros_preview.md" \
  || fail "sample macros missing the backlog-qualified same-day sentence"
python3 - "$tmp" <<'EOF' || fail "a macro promises same-day shipping during the BFCM backlog"
import csv, glob, sys
rows = []
for p in glob.glob(sys.argv[1] + "/**/macros.csv", recursive=True):
    with open(p, encoding="utf-8") as f:
        rows += list(csv.DictReader(f))
assert rows, "no macros.csv produced by earlier checks"
body_col = next(c for c in rows[0] if "body" in c.lower())
bad = []
for r in rows:
    for sent in r[body_col].replace("\n", " ").split(". "):
        if "same day" in sent and "Black Friday rush" not in sent:
            bad.append(sent.strip()[:160])
assert not bad, bad
print("same-day sentences checked in", len(rows), "macros")
EOF
ok "same-day shipping is never promised without the BFCM backlog window and catch-up date"

# 24. [QA re-review S-S1] The same-day cutoff time is reconciled with the Cutoff Checker like the dates: a different
#     time in policies.json is replaced by the checker's (with a WARNING); the same time written differently
#     ("1 pm ET" vs "1:00 pm ET") is not a conflict.
python3 - "$tmp" <<'EOF' || fail "could not write the 3 pm policies"
import json, sys
t = sys.argv[1]
for src, dst in (("examples/data/policies.json", "/pol_3pm.json"), (t + "/policies_full.json", "/pol_full_3pm.json")):
    p = json.load(open(src, encoding="utf-8"))
    p["shipping"]["same_day_cutoff"] = "3 pm ET"
    json.dump(p, open(t + dst, "w", encoding="utf-8"), indent=2)
EOF
hs --policies "$tmp/pol_full_3pm.json" --tickets "$tmp/hand_tickets.csv" --cutoffs "$tmp/cutoffs_checker.json" \
   --out-dir "$tmp/s1" > "$tmp/s1.md"
has "$tmp/s1.md" "**WARNING**: policies.json and cutoffs_checker.json disagree on the same-day cutoff: policies.json says orders placed by 3 pm ET ship the same day; the Cutoff Checker computed a 1:00 pm ET cutoff. The macros use the Cutoff Checker's values." "same-day cutoff conflict warning"
has "$tmp/s1/macros_preview.md" "Orders placed by 1:00 pm ET on a business day usually ship the same day" "checker cutoff used"
! grep -q "3 pm ET" "$tmp/s1/macros.csv" || fail "macros use the policies.json cutoff the checker disagrees with"
hs --policies "$tmp/policies_full.json" --tickets "$tmp/hand_tickets.csv" --cutoffs "$tmp/cutoffs_checker.json" > "$tmp/s1b.md"
! grep -q "disagree on the same-day cutoff" "$tmp/s1b.md" || fail "'1 pm ET' and '1:00 pm ET' reported as a conflict"
if [ -f "$tmp/i_cut.json" ]; then
  # Fresh chain (from check 18) with the sample policies' cutoff set to 3 pm ET.
  hs --policies "$tmp/pol_3pm.json" --tickets examples/data/tickets.csv --orders examples/data/orders.csv \
     --cutoffs "$tmp/i_cut.json" --out-dir "$tmp/s1c" > "$tmp/s1c.md"
  has "$tmp/s1c.md" "disagree on the same-day cutoff: policies.json says orders placed by 3 pm ET ship the same day" "chain cutoff warning"
  ! grep -q "3 pm ET" "$tmp/s1c/macros.csv" || fail "chain macros use the policies.json cutoff"
  grep -q "Orders placed by 1:00 pm ET on a business day usually ship the same day" "$tmp/s1c/macros.csv" || fail "chain macros miss the checker cutoff"
fi
ok "same-day cutoff time reconciled with the Cutoff Checker, with a warning on conflict"

echo "all $pass checks passed"

#!/usr/bin/env bash
# End-to-end checks for BFCM Cohort Quality. No network or credentials needed.
#   bash tests/run_tests.sh
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1   # the checks import the analyzer; leave no __pycache__ behind
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
pass=0
ok() { pass=$((pass + 1)); echo "ok $pass - $1"; }
fail() { echo "FAIL - $1"; exit 1; }
has() { grep -qF -- "$2" "$1" || { echo "--- missing: $2"; cat "$1"; fail "$3"; }; }
cq() { python3 scripts/cohort_quality.py "$@"; }
jq_() { python3 -c "import json,sys; a=json.load(open(sys.argv[1])); sys.exit(0 if ($2) else 1)" "$1"; }

python3 tests/make_cases.py "$tmp"
H="--orders $tmp/hand_orders.csv --economics $tmp/hand_econ.json"

# 1. Sample output is byte-identical to the published expected output, stable, and unchanged by --json.
bash examples/run.sh > "$tmp/out.txt"
cmp -s "$tmp/out.txt" examples/expected_output.txt || { diff "$tmp/out.txt" examples/expected_output.txt | head || true; fail "sample output differs"; }
bash examples/run.sh > "$tmp/out2.txt"
cmp -s "$tmp/out.txt" "$tmp/out2.txt" || fail "output is not deterministic"
cq --orders examples/data/orders.csv --economics examples/data/economics.json --spend examples/data/spend.csv \
   --year 2025 --as-of 2026-10-06 --timezone America/New_York --history-since 2021-03-01 --json "$tmp/sample.json" > "$tmp/out3.txt"
cmp -s "$tmp/out.txt" "$tmp/out3.txt" || fail "--json changed the report"
jq_ "$tmp/sample.json" "a['cohorts'][0]['new_customers'] == 843 and a['compare_horizon'] == 180" || fail "--json content"
ok "sample run matches examples/expected_output.txt (and --json leaves it unchanged)"

# 2. The fixture generator reproduces the bundled sample data exactly.
cp -r examples "$tmp/ex" && python3 "$tmp/ex/make_fixtures.py" > /dev/null
for f in orders.csv spend.csv economics.json; do
  cmp -s "$tmp/ex/data/$f" "examples/data/$f" || fail "make_fixtures.py does not reproduce $f"
done
ok "make_fixtures.py regenerates examples/data byte for byte"

# 3. New-customer identification: D first ordered on Sep 15, so D's Nov 28 order is not a BFCM first order.
cq $H --spend "$tmp/hand_spend.csv" --as-of 2026-07-01 --min-cell 1 --json "$tmp/hand.json" > "$tmp/hand.txt"
has "$tmp/hand.txt" "| New customers | 5 | 1 | 1 |" "customer with an earlier order counted as new"
has "$tmp/hand.txt" "7 customers are in an analysed cohort; 1 other customer first ordered outside the cohort windows and is left out." "data note"
cq --orders "$tmp/hand_new_d.csv" --economics "$tmp/hand_econ.json" --as-of 2026-07-01 > "$tmp/newd.txt"
has "$tmp/newd.txt" "| New customers | 6 | 1 | 1 |" "without the Sep 15 order D should be new"
ok "a customer with an earlier order is not new; without it, they are"

# 4. Repeat rate and CM per customer match the hand calculation (60% margin, \$10 shipping, 2% + \$0.50 fees).
#    Per-order CM = kept net - 40% of pre-discount price x share kept - fees - shipping:
#    A 23.00 (+47.50 on day 22, +18.50 on day 93); B -11.82 (fully refunded); Q 18.50;
#    C 47.50 + 6.90 (same day, not a repeat); E 7.90 ($40 of $80 refunded) (+37.70 on day 60).
#    30 days: 139.48 / 5 = 27.90; 60 and 90 days: 177.18 / 5 = 35.44; 180 days: 195.68 / 5 = 39.14.
#    Repeaters: A (day 22) -> 20% at 30 days; A and E (day 60) -> 40% from 60 days.
f="$tmp/hand.txt"
has "$f" "| Ordered again within 30 days | 20.0% | 0.0% | 0.0% |" "repeat 30"
has "$f" "| Ordered again within 60 days | 40.0% | 100.0% | 0.0% |" "repeat 60"
has "$f" "| CM per customer at 30 days | \$27.90 | \$47.50 | \$24.30 |" "CM 30"
has "$f" "| CM per customer at 90 days | \$35.44 | \$70.50 | \$24.30 |" "CM 90"
has "$f" "| CM per customer at 180 days | \$39.14 | \$70.50 | not mature |" "CM 180"
has "$f" "| First-order AOV (net sales) | \$74.20 | \$100.00 | \$60.00 |" "AOV"
has "$f" "| First-order net sales refunded | 28.6% | 0.0% | 0.0% |" "refund rate 106/371"
has "$f" "**BFCM 2025 new customers were worth \$39.14 in contribution each at 180 days, vs \$70.50 for October 2025 (−44%).**" "headline"
has "$f" "customers who placed another order on their first day: 1 (BFCM 2025)" "same-day order"
jq_ "$tmp/hand.json" "abs(a['cohorts'][0]['horizons']['180']['cm_per_customer'] - 39.136) < 1e-6 and a['cohorts'][0]['horizons']['60']['repeaters'] == 2 and abs(a['cohorts'][0]['at']['cm_after_first_day'] - 20.74) < 1e-6" \
  || fail "--json values differ from the hand calculation"
ok "repeat rate and CM per customer match the hand-computed case"

# 5. Right-censoring: a horizon shows only when every member has reached it; later orders are ignored.
cq $H --spend "$tmp/hand_spend.csv" --as-of 2026-01-15 --json "$tmp/jan.json" > "$tmp/jan.txt"
has "$tmp/jan.txt" "| | BFCM 2025 | October 2025 | Jan–Feb 2026 (still acquiring) |" "acquiring cohort not marked"
has "$tmp/jan.txt" "| Ordered again within 30 days | 20.0% | 0.0% | not mature |" "30-day row"
has "$tmp/jan.txt" "| Ordered again within 60 days | not mature | 100.0% | not mature |" "60 days should not be mature for BFCM"
has "$tmp/jan.txt" "30 days is the longest horizon both BFCM 2025 and October 2025 have fully reached. BFCM 2025's 60-day horizon matures on Jan 31, 2026: until then, treat this as an early read." "early-read line"
has "$tmp/jan.txt" "Also: 2 orders after the as-of date ignored." "orders after as-of"
jq_ "$tmp/jan.json" "a['cohorts'][0]['horizons']['60']['mature'] is False and a['cohorts'][0]['horizons']['60']['matures_on'] == '2026-01-31' and a['cohorts'][0]['max_day'] == 44" \
  || fail "maturity in --json"
cq $H --as-of 2025-12-20 > "$tmp/dec.txt"
has "$tmp/dec.txt" "**Too early to judge BFCM 2025: its first horizon (30 days) matures on Jan 1, 2026." "too-early headline"
has "$tmp/dec.txt" "Skipped: no horizon is mature for BFCM 2025 yet" "cuts not skipped"
has "$tmp/dec.txt" "**Left out:** Jan–Feb 2026 (Jan 1–Feb 28, 2026) starts after the as-of date." "future cohort"
ok "immature horizons print as not mature (right-censoring), with the date each matures"

# 6. Discount depth is banded on whole percents of the pre-discount price: 0, 1–15, 16–25, 26–35, >35.
python3 - <<'EOF' || fail "discount banding"
import sys
sys.path.insert(0, "scripts")
import cohort_quality as cq
cases = [((100, 0), 0), ((99.7, 0.3), 1), ((85, 15), 1), ((84.6, 15.4), 1), ((84.5, 15.5), 2), ((75, 25), 2),
         ((74.6, 25.4), 2), ((67.5, 32.5), 3), ((64.6, 35.4), 3), ((64.5, 35.5), 4), ((60, 40), 4), ((0, 50), 4)]
for (net, disc), want in cases:
    got = cq.band_of(net, disc)
    assert got == want, ((net, disc), cq.depth_pct(net, disc), got, want)
assert cq.depth_pct(67.5, 32.5) == 33 and cq.depth_pct(75, 25) == 25
EOF
has "$f" "| 0% | 2 | 40.0% | \$75.00 |" "0% band"
has "$f" "| 16–25% (planned depth) | 2 | 40.0% | \$77.50 | 100.0% (34–100%) | \$67.30 |" "16–25% band"
has "$f" "| >35% | 1 | 20.0% | \$66.00 | 0.0% (0–79%) | −\$11.82 |" ">35% band"
has "$f" "Discount codes on first orders above 25% off: MAYA20 1." "codes on deep first orders"
ok "discount-depth banding (32.5% -> 26–35%, 35.5% -> >35%) and the band table"

# 7. utm_source spellings are normalized; a blank utm_source falls back to the landing URL.
cq --orders "$tmp/variant_orders.csv" --economics "$tmp/hand_econ.json" --as-of 2026-07-01 --min-cell 1 > "$tmp/var.txt"
has "$tmp/var.txt" '| Meta | 11 | `facebook` 4, `fb` 3, `FB` 2, `Instagram` 1, `ig` 1 |' "Meta spellings not merged"
has "$tmp/var.txt" '| Google | 3 | `adwords` 2, `Google` 1 |' "Google spellings not merged"
has "$tmp/var.txt" '| TikTok | 1 | `tt` 1 |' "TikTok alias"
has "$tmp/var.txt" "utm_source was spelled 7 ways for 2 channels; spellings were merged before cutting." "merge note"
has "$tmp/var.txt" "(from the landing URL when the column was blank)" "landing URL fallback"
python3 -c "
import sys; sys.path.insert(0, 'scripts'); import cohort_quality as cq
assert cq.source_key('L.Facebook.com') == 'facebook' and cq.source_key(' ADWORDS ') == 'google'
assert cq.source_key('newsletter') == 'newsletter' and cq.source_key('') == ''
assert cq.utm_from_landing('/p/x?utm_medium=cpc&utm_source=TikTok') == 'TikTok'
" || fail "source_key / utm_from_landing"
ok "utm_source normalization and landing-URL fallback"

# 8. Small cells: cuts under --min-cell customers show counts but no rates.
cq $H --spend "$tmp/hand_spend.csv" --as-of 2026-07-01 > "$tmp/small.txt"
has "$tmp/small.txt" '| Meta | 2 | `facebook` 1, `fb` 1 | 0% | fewer than 30 customers | — | — |' "small source cell not suppressed"
has "$tmp/small.txt" "| 0% | 2 | 40.0% | \$75.00 | fewer than 30 customers | — | — |" "small band cell not suppressed"
has "$f" '| Meta | 2 | `facebook` 1, `fb` 1 | 0% | 100.0% (34–100%) | within noise | $67.30 |' "--min-cell 1 should show the rate"
has examples/expected_output.txt '| Pinterest | 5 | `pinterest` 5 | 20% | fewer than 30 customers | — | — |' "sample small cell"
ok "small cells are suppressed below --min-cell (default 30)"

# 9. Payback day: blended cost \$175 / 5 = \$35.00. Cumulative CM per customer: 18.40 on day 0, 27.90 from
#    day 22, 35.44 from day 60 -> paid back on day 60. As of Jan 15, 2026 only day 44 is readable: 27.90 / 35 = 80%.
has "$f" "| Payback (CM per customer ≥ blended cost) | day 60 | — | — |" "payback day"
has "$f" "Blended new-customer cost \$35.00. The average BFCM 2025 customer covered it on day 60." "payback finding"
has "$tmp/jan.txt" "| Payback (CM per customer ≥ blended cost) | not by day 44 (80% recovered) | — | — |" "censored payback"
jq_ "$tmp/hand.json" "a['cohorts'][0]['payback_day'] == 60 and a['cohorts'][0]['cost'] == 35" || fail "payback in --json"
ok "payback day against blended new-customer cost"

# 10. Optional spend: without it the report still runs; a spend file with a missing day is named, not guessed.
cq $H --as-of 2026-07-01 > "$tmp/nospend.txt"
has "$tmp/nospend.txt" "| Blended new-customer cost | — | — | — |" "cost without spend"
has "$tmp/nospend.txt" "Blended new-customer cost and payback: not computed (no --spend file)." "no-spend note"
! grep -q "Acquisition cost and payback" "$tmp/nospend.txt" || fail "payback finding without spend"
cq $H --spend "$tmp/hand_spend_gap.csv" --as-of 2026-07-01 > "$tmp/gap.txt"
has "$tmp/gap.txt" "Blended new-customer cost not computed for BFCM 2025 (spend file is missing 1 of 9 days)" "spend gap"
ok "missing optional spend handled (no file, or missing days)"

# 11. Invalid input exits 2 with the reason.
run2() { set +e; cq "$@" > /dev/null 2> "$tmp/err.txt"; rc=$?; set -e; [ "$rc" = 2 ] || fail "exit $rc, expected 2 for: $*"; }
run2 --orders "$tmp/bad_nocol.csv" --economics "$tmp/hand_econ.json"
has "$tmp/err.txt" "is missing required column(s): net_sales" "missing column"
run2 --orders "$tmp/bad_negative.csv" --economics "$tmp/hand_econ.json"
has "$tmp/err.txt" "line 5: net_sales is negative (-5.00)" "negative"
run2 --orders "$tmp/bad_dup.csv" --economics "$tmp/hand_econ.json"
has "$tmp/err.txt" "line 16: duplicate order_id '#1003' (first on line 4)" "duplicate"
run2 --orders "$tmp/bad_date.csv" --economics "$tmp/hand_econ.json"
has "$tmp/err.txt" "line 7: created_at '29/11/2025' is not a date" "bad date"
run2 --orders "$tmp/hand_orders.csv" --economics "$tmp/bad_econ.json"
has "$tmp/err.txt" "economics.json is missing required key 'gross_margin_pct'" "economics key"
run2 --orders "$tmp/bad_nobfcm.csv" --economics "$tmp/hand_econ.json"
has "$tmp/err.txt" "no customer's first order falls in the BFCM 2025 window" "empty BFCM cohort"
run2 $H --compare "Late Nov=2025-11-20:2025-11-25"
has "$tmp/err.txt" "cohort windows overlap: BFCM 2025" "overlapping windows"
run2 --orders "$tmp/nope.csv" --economics "$tmp/hand_econ.json"
has "$tmp/err.txt" "cannot read orders file" "missing file"
run2 $H --timezone Mars/Olympus
has "$tmp/err.txt" "--timezone 'Mars/Olympus' is not supported" "bad time zone"
ok "invalid input exits 2 with the reason"

# 12. Time zones: UTC timestamps are converted to the store's day, with daylight saving.
cq --orders "$tmp/tz_orders.csv" --economics "$tmp/hand_econ.json" --as-of 2026-07-01 --timezone America/New_York --json "$tmp/tz1.json" > /dev/null
jq_ "$tmp/tz1.json" "a['cohorts'][0]['new_customers'] == 2 and len(a['cohorts']) == 1" || fail "Dec 3 04:30Z is Dec 2 in New York"
cq --orders "$tmp/tz_orders.csv" --economics "$tmp/hand_econ.json" --as-of 2026-07-01 --json "$tmp/tz2.json" > "$tmp/tz2.txt"
jq_ "$tmp/tz2.json" "a['cohorts'][0]['new_customers'] == 1" || fail "without --timezone the date is read as written"
has "$tmp/tz2.txt" "3 timestamps written in UTC were read as written." "UTC warning"
cq --orders "$tmp/tz_orders.csv" --economics "$tmp/hand_econ.json" --as-of 2026-07-01 --timezone=-05:00 --json "$tmp/tz3.json" > /dev/null
jq_ "$tmp/tz3.json" "[c['new_customers'] for c in a['cohorts']] == [2, 1]" || fail "a fixed -05:00 puts Nov 1 04:30Z in October"
ok "time zones: UTC converted to store days (Nov 1 04:30Z is still EDT, not October)"

# 13. A raw Shopify admin export loads: header aliases, one row per line item, cancelled orders dropped.
cq --orders "$tmp/shopify_export.csv" --economics "$tmp/hand_econ.json" --as-of 2026-07-01 --min-cell 1 --json "$tmp/sh.json" > "$tmp/sh.txt"
has "$tmp/sh.txt" "Also: 1 cancelled order dropped; 1 extra line-item row merged into their orders." "export rows"
has "$tmp/sh.txt" '| `Stoneware Mug Set (4)` | 1 |' "second line item lost"
has "$tmp/sh.txt" "Skipped: the orders file has no utm_source or landing_site column." "source skip"
jq_ "$tmp/sh.json" "a['cohorts'][0]['new_customers'] == 2" || fail "export cohort size"
ok "raw Shopify admin export (Name, Email, Subtotal, Lineitem name, Cancelled at) works"

# 14. Scripts parse under Python 3.8 grammar.
python3 - <<'EOF' || fail "not Python 3.8 compatible"
import ast
for p in ("scripts/cohort_quality.py", "examples/make_fixtures.py", "tests/make_cases.py"):
    ast.parse(open(p, encoding="utf-8").read(), p, feature_version=(3, 8))
EOF
ok "scripts parse as Python 3.8"

# 15. [Blocker 5.1 regression] QA repro: 100 customers at 20% off and 100 at 40% off with identical 20% repeat
#     behaviour. The deeper discount lowers first-order CM by itself; that must not produce a "weaker customers"
#     finding or a discount cap. Repeat rate and repeat-order CM are equal, so: no finding, no cap.
cq --orders "$tmp/equal_repeat.csv" --economics "$tmp/hand_econ.json" --as-of 2026-07-01 --json "$tmp/eq.json" > "$tmp/eq.txt"
! grep -qi "bought weaker customers\*\*\|repeated less\|worth less after their first order\|cap the first-order discount\|test a first-order discount cap" "$tmp/eq.txt" \
  || { grep -i "weaker\|repeated less\|cap" "$tmp/eq.txt"; fail "discount mechanics produced a weaker-customers finding"; }
has "$tmp/eq.txt" "**Deeper first-order discounts: no clear difference after the first order.** 100 customers (50.0%) took more than 25% off their first order: 20.0% ordered again within 180 days (13–29%) vs 20.0% (13–29%) at 16–25%, and CM from repeat orders was \$9.50 vs \$9.50 per customer. First-order CM was \$8.30 vs \$27.90, lower by construction at a deeper discount." "neutral finding"
has "$tmp/eq.txt" "**New customers: last year's data doesn't show deeper first-order discounts buying weaker customers.**" "neutral recommendation"
jq_ "$tmp/eq.json" "a['cuts']['deep_vs_ref'] == {'repeat': 0, 'later_cm': 0, 'verdict': 'unclear'}" || fail "deep_vs_ref in --json"
has examples/expected_output.txt "**Customers acquired at deeper discounts repeated less.** 186 customers (22.1%)" "sample finding should still fire on a real repeat gap"
has examples/expected_output.txt "This may reflect who the discount attracts; confirm with a holdout test before setting a cap." "non-causal wording"
ok "deep-discount finding needs a lower repeat rate or repeat-order CM, not the discount's own first-order effect"

# 16. [Shared fix 0.1] Strict numbers: comma decimals, nan, inf, European files and invalid negatives exit 2
#     naming file, line and column; $ / € / thousands and (50.00) refunds still parse.
run2 --orders "$tmp/comma_orders.csv" --economics "$tmp/hand_econ.json"
has "$tmp/err.txt" "comma_orders.csv line 4, column 'net_sales'; export with '.' as the decimal separator" "comma decimal"
run2 --orders "$tmp/nan_orders.csv" --economics "$tmp/hand_econ.json"
has "$tmp/err.txt" "nan_orders.csv line 5, column 'discount_amount'" "nan"
run2 --orders "$tmp/inf_orders.csv" --economics "$tmp/hand_econ.json"
has "$tmp/err.txt" "inf_orders.csv line 7, column 'refunded_amount'" "inf"
run2 --orders "$tmp/euro_orders.csv" --economics "$tmp/hand_econ.json"
has "$tmp/err.txt" "column 'net_sales' looks European-formatted (1.234,56)" "European file"
run2 --orders "$tmp/paren_orders.csv" --economics "$tmp/hand_econ.json"
has "$tmp/err.txt" "line 6: net_sales is negative ((50.00))" "negative net sales"
cq --orders "$tmp/fmt_orders.csv" --economics "$tmp/hand_econ.json" --as-of 2026-07-01 --min-cell 1 > "$tmp/fmt.txt"
cq $H --as-of 2026-07-01 --min-cell 1 > "$tmp/fmt_ref.txt"
cmp -s "$tmp/fmt.txt" "$tmp/fmt_ref.txt" || fail "\$75.00 / €100.00 / (66.00) / -40.00 refunds parse differently"
python3 -c "
import sys; sys.path.insert(0, 'scripts'); import cohort_quality as cq
for raw, want in (('\$1,234.56', 1234.56), ('1234.56', 1234.56), ('€1,234.56', 1234.56), ('1,234', 1234.0)):
    assert cq.parse_money(raw, 'f', 2, 'net_sales', None) == want, raw
assert cq.parse_money('(50.00)', 'f', 2, 'refunded_amount', 0.0) == 50.0
for bad in ('2255,74', '28.320,00', 'nan', 'inf', '1e9', '12abc'):
    try:
        cq.parse_money(bad, 'f', 2, 'net_sales', None)
    except cq.InputError:
        continue
    raise SystemExit('accepted ' + bad)
" || fail "parse_money unit cases"
ok "strict number parsing (comma decimal, nan, inf, European file, negatives; \$/€/thousands and refund signs parse)"

# 17. [5.3] Recommendation 2 needs >= 50 returning BFCM orders and gives a range; fraction-typed *_pct keys;
#     currency symbol from economics.json; all-zero spend treated as missing.
! grep -q "come back for the event anyway\|returning customers made" "$tmp/hand.txt" || fail "rec 2 cites 1 returning order"
cq --orders "$tmp/returning.csv" --economics "$tmp/hand_econ.json" --as-of 2026-07-01 > "$tmp/ret.txt"
has "$tmp/ret.txt" "October 2025's returning customers made 60 orders during BFCM 2025; 50% used a discount (95% range 38–62%). Test whether they'd buy without one." "rec 2 with enough orders"
run2 --orders "$tmp/hand_orders.csv" --economics "$tmp/pct_econ.json"
has "$tmp/err.txt" "'gross_margin_pct' is 0.64, which means 0.64%, not 64% (did you mean 64?)" "fraction margin accepted"
cq --orders "$tmp/hand_orders.csv" --economics "$tmp/fee_econ.json" --as-of 2026-07-01 > "$tmp/fee.txt"
has "$tmp/fee.txt" "**Check economics.json:** 'payment_fee_pct' is 0.029, which means 0.029%, not 2.9% (did you mean 2.9?)." "fee warning"
cq --orders "$tmp/hand_orders.csv" --economics "$tmp/eur_econ.json" --as-of 2026-07-01 > "$tmp/eur.txt"
has "$tmp/eur.txt" "**BFCM 2025 new customers were worth €39.14 in contribution each at 180 days, vs €70.50 for October 2025 (−44%).**" "EUR symbol"
! grep -q '\$[0-9]' "$tmp/eur.txt" || fail "\$ printed for a EUR store"
cq $H --spend "$tmp/hand_spend_zero.csv" --as-of 2026-07-01 > "$tmp/zero.txt"
has "$tmp/zero.txt" "Blended new-customer cost not computed for BFCM 2025 (spend file has 0 spend on all 9 days, treated as missing)" "zero spend"
! grep -q "with the first order\|Acquisition cost and payback\|acquire," "$tmp/zero.txt" || fail "zero spend reported as payback"
ok "rec 2 minimum count, *_pct fractions, currency symbol, all-zero spend"

echo "all $pass checks passed"

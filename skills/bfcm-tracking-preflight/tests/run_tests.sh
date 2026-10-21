#!/usr/bin/env bash
# End-to-end checks for BFCM Tracking & Attribution Preflight. No network or credentials needed.
#   bash tests/run_tests.sh
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
pass=0
ok() { pass=$((pass + 1)); echo "ok $pass - $1"; }
fail() { echo "FAIL - $1"; exit 1; }
has() { grep -qF -- "$2" "$1" || { echo "--- missing: $2"; cat "$1"; fail "$3"; }; }
pf() { python3 scripts/preflight.py "$@"; }

python3 tests/make_cases.py "$tmp"

# 1. Sample output is byte-identical to the published expected output, and stable across runs.
bash examples/run.sh > "$tmp/out.txt"
cmp -s "$tmp/out.txt" examples/expected_output.txt || { diff "$tmp/out.txt" examples/expected_output.txt | head; fail "sample output differs"; }
bash examples/run.sh > "$tmp/out2.txt"
cmp -s "$tmp/out.txt" "$tmp/out2.txt" || fail "output is not deterministic"
ok "sample run matches examples/expected_output.txt"

# 2. The fixture generator reproduces the bundled sample data exactly.
cp -r examples "$tmp/ex" && python3 "$tmp/ex/make_fixtures.py" > /dev/null
for f in events_daily.csv orders_utm.csv last_bfcm.csv; do
  cmp -s "$tmp/ex/data/$f" "examples/data/$f" || fail "make_fixtures.py does not reproduce $f"
done
ok "make_fixtures.py regenerates examples/data byte for byte"

# 3. Clean account: READY, no fix items above PLAN.
pf --account Clean --events "$tmp/clean_events.csv" --orders "$tmp/clean_orders.csv" \
   --last-bfcm "$tmp/hand_bfcm.csv" --json "$tmp/clean.json" > "$tmp/clean.txt"
has "$tmp/clean.txt" "**Verdict: READY.** No tracking issues found." "clean account not READY"
! grep -qE "\| (CRITICAL|WARNING) \|" "$tmp/clean.txt" || fail "clean account has fix items"
python3 -c "import json,sys; a=json.load(open(sys.argv[1])); sys.exit(0 if a['verdict']=='READY' and a['critical']==0 else 1)" "$tmp/clean.json" \
  || fail "--json verdict wrong"
ok "clean account is READY (text and --json)"

# 4. Optional files missing: still runs, says what wasn't checked.
pf --account Clean --events "$tmp/clean_events.csv" > "$tmp/min.txt"
has "$tmp/min.txt" "Not checked: UTM hygiene (no --orders file); last BFCM claim gap (no --last-bfcm file)." "missing optional files not reported"
has "$tmp/min.txt" "Skipped: no orders export (--orders)." "UTM section not skipped"
has "$tmp/min.txt" "Skipped: no last-BFCM export (--last-bfcm)." "BFCM section not skipped"
ok "missing optional files are skipped and named"

# 5. Invalid input exits 2 with the reason.
set +e
pf --events "$tmp/bad_events.csv" > /dev/null 2> "$tmp/err1.txt"; rc1=$?
pf --events "$tmp/gap_events.csv" > /dev/null 2> "$tmp/err2.txt"; rc2=$?
pf --events "$tmp/clean_events.csv" --orders "$tmp/nope.csv" > /dev/null 2> "$tmp/err3.txt"; rc3=$?
set -e
[ "$rc1" = 2 ] && grep -q "line 7: 'shopify_orders' is negative" "$tmp/err1.txt" || fail "negative count accepted (rc=$rc1)"
[ "$rc2" = 2 ] && grep -q "skips from 2026-09-17 to 2026-09-19" "$tmp/err2.txt" || fail "date gap accepted (rc=$rc2)"
[ "$rc3" = 2 ] && grep -q "cannot read orders file" "$tmp/err3.txt" || fail "missing file accepted (rc=$rc3)"
ok "invalid input exits 2 with the reason"

# 6. Broken Meta deduplication is detected and dated.
pf --events "$tmp/dedup_events.csv" > "$tmp/dedup.txt"
has "$tmp/dedup.txt" "**Verdict: NOT READY.**" "dedup case verdict"
has "$tmp/dedup.txt" "Meta deduplication appears to have failed since Sep 26 (10 days; server and browser events both counted). In the last 7 days Meta counted 404 purchases for 216 Shopify orders (1.87x)." "dedup not detected"
has "$tmp/dedup.txt" "Browser pixel 195 + Conversions API 209 = 404 events. Meta merged none as duplicates (0% of the 195 it could have)." "dedup arithmetic"
has "$tmp/dedup.txt" "Before Sep 26 it merged 96% of possible duplicates and counted 1.00x Shopify orders." "dedup baseline"
ok "dedup failure detected with onset date"

# 7. A platform dropping to zero is detected as ongoing.
pf --events "$tmp/zero_events.csv" > "$tmp/zero.txt"
has "$tmp/zero.txt" "TikTok sent 0 purchase events on Oct 3–Oct 5 (3 days) while Shopify booked 93 orders. It is still at 0." "zero-event days not detected"
has "$tmp/zero.txt" "**Verdict: NOT READY.**" "ongoing zero should be critical"
ok "zero-event days detected"

# 8. utm_source spelling variants are normalized and counted.
pf --events "$tmp/clean_events.csv" --orders "$tmp/variant_orders.csv" > "$tmp/var.txt"
has "$tmp/var.txt" 'Meta: 5 spellings across 40 orders: `facebook` 20, `Facebook` 8, `fb` 6, `FB` 4, `ig` 2. Use `facebook`.' "variants not normalized"
has "$tmp/var.txt" "**Verdict: FIX BEFORE BFCM.**" "variants should be a warning"
ok "UTM source variants normalized"

# 9. Claim-gap math on a hand-computed case.
#    Actual 1000+2000+1000 = 4000. Claims (1200+600)+(1800+900)+(600+300) = 5400 -> 1.35x.
#    Per day 1800/1000 = 1.80x, 2700/2000 = 1.35x, 900/1000 = 0.90x.
#    Meta 3600 vs UTM sales 2500 -> 1.44x; ROAS 3600/1000 = 3.60, matched 2500/1000 = 2.50.
#    Spend 1400 -> MER 4000/1400 = 2.86. Summed platform ROAS (5400/1400) must NOT be printed (test 13).
f="$tmp/clean.txt"
has "$f" 'Platforms together reported **$5,400** of attributed sales vs **$4,000** in Shopify net sales (1.35x). This is a reporting gap, not a return.' "claim total"
has "$f" '| Fri Nov 28 (Black Friday) | $1,000 | $1,200 | $600 | $1,800 | 1.80x |' "day 1 ratio"
has "$f" '| Sat Nov 29 | $2,000 | $1,800 | $900 | $2,700 | 1.35x |' "day 2 ratio"
has "$f" '| Mon Dec 1 (Cyber Monday) | $1,000 | $600 | $300 | $900 | 0.90x |' "day 3 ratio"
has "$f" 'so Meta attribution-gap ratio is **1.44x** (platform revenue / UTM revenue). Reported ROAS 3.60 on $1,000 spend; Shopify UTM (last-click) ROAS 2.50.' "descriptive attribution gap"
has "$f" '**Blended MER: 2.86** ($4,000 Shopify net sales ÷ $1,400 total ad spend).' "MER"
ok "claim-gap math matches the hand-computed case"

# 10. Black Friday date and fix-by deadlines are computed, and past deadlines say so.
has "$tmp/out.txt" "Black Friday is Nov 27, 2026 (53 days away) and Cyber Monday is Nov 30." "BFCM dates"
pf --events "$tmp/dedup_events.csv" --as-of 2026-11-12 > "$tmp/late.txt"
has "$tmp/late.txt" "| now (Nov 10 has passed) |" "overdue deadline not flagged"
ok "BFCM calendar and overdue deadlines"

# 11. Scripts parse under Python 3.8 grammar.
python3 - <<'EOF' || fail "not Python 3.8 compatible"
import ast, sys
for p in ("scripts/preflight.py", "examples/make_fixtures.py", "tests/make_cases.py"):
    ast.parse(open(p, encoding="utf-8").read(), p, feature_version=(3, 8))
EOF
ok "scripts parse as Python 3.8"

# 12. Diagnostic differences must not become automatic attribution/value corrections.
has "$tmp/out.txt" "not a planning discount, correction multiplier" "unsafe attribution guidance"
has "$tmp/out.txt" "This ratio cannot establish a tax/shipping error" "unsafe value diagnosis"
has "$tmp/out.txt" "Do not divide ROAS by the aggregate ratio." "missing value guardrail"
ok "attribution and value differences remain diagnostic"

# 13. [Blocker 4.1 regression] Summed platform ROAS is never printed or exported.
for f in "$tmp/out.txt" "$tmp/clean.txt"; do
  ! grep -qi "summed attributed revenue" "$f" || fail "summed platform ROAS printed in $f"
done
! grep -q '"claimed_roas"' "$tmp/clean.json" || fail "claimed_roas key still in --json"
has "$tmp/out.txt" "Platforms together reported **\$252,000** of attributed sales vs **\$120,000** in Shopify net sales (2.10x). This is a reporting gap, not a return." "descriptive claim ratio"
ok "summed platform ROAS is not printed (text or --json)"

# 14. [Shared fix 0.1] Strict numbers: comma decimals, nan, inf, European files and negatives exit 2
#     naming file, line and column; $1,234.56 / €1,234.56 / 1234.56 still parse.
set +e
pf --events "$tmp/comma_events.csv" > /dev/null 2> "$tmp/e_comma.txt"; r1=$?
pf --events "$tmp/nan_events.csv" > /dev/null 2> "$tmp/e_nan.txt"; r2=$?
pf --events "$tmp/inf_events.csv" > /dev/null 2> "$tmp/e_inf.txt"; r3=$?
pf --events "$tmp/euro_events.csv" > /dev/null 2> "$tmp/e_euro.txt"; r4=$?
pf --events "$tmp/paren_events.csv" > /dev/null 2> "$tmp/e_paren.txt"; r5=$?
set -e
[ "$r1" = 2 ] || fail "comma decimal accepted (rc=$r1)"
has "$tmp/e_comma.txt" "comma_events.csv line 5, column 'shopify_net_sales'; export with '.' as the decimal separator" "comma-decimal message"
[ "$r2" = 2 ] || fail "nan accepted (rc=$r2)"
has "$tmp/e_nan.txt" "nan_events.csv line 6, column 'meta_purchase_value'" "nan message"
[ "$r3" = 2 ] || fail "inf accepted (rc=$r3)"
has "$tmp/e_inf.txt" "inf_events.csv line 4, column 'google_conversion_value'" "inf message"
[ "$r4" = 2 ] || fail "European file accepted (rc=$r4)"
has "$tmp/e_euro.txt" "column 'shopify_net_sales' looks European-formatted (1.234,56)" "European-file message"
[ "$r5" = 2 ] || fail "(50.00) accepted where negatives are invalid (rc=$r5)"
has "$tmp/e_paren.txt" "line 3: 'shopify_net_sales' is negative" "negative message"
pf --events "$tmp/fmt_events.csv" > "$tmp/fmt.txt"
pf --events "$tmp/clean_events.csv" > "$tmp/min2.txt"
cmp -s "$tmp/fmt.txt" "$tmp/min2.txt" || fail "currency/thousands formatted numbers parse differently"
ok "strict number parsing (comma decimal, nan, inf, European file, negatives; currency formats parse)"

# 15. [4.4] Missing spend for a platform with attributed revenue: MER flagged as overstated, not used for budgets.
#     Meta spend only: 4000 / 1000 = 4.00.
pf --events "$tmp/clean_events.csv" --last-bfcm "$tmp/metaonly_bfcm.csv" > "$tmp/meta_only.txt"
has "$tmp/meta_only.txt" "**Blended MER: 4.00, overstated** (\$4,000 Shopify net sales ÷ \$1,000 ad spend from Meta only). Spend is missing for Google Ads, which reported attributed sales" "missing spend not flagged"
has "$tmp/meta_only.txt" "Add last year's spend for Google Ads to the last-BFCM file and re-run." "fix list uses incomplete MER"
! grep -q "blended MER (4.00 last year)" "$tmp/meta_only.txt" || fail "overstated MER used in the fix list"
ok "missing spend channels named and MER not used for budgets"

# 16. [4.5] Input checks: duplicate order_id, junk dates, non-Nov/Dec BFCM dates and bad --json exit 2;
#     order dates outside the events window are reported; ISO timestamps with offsets are accepted.
set +e
pf --events "$tmp/clean_events.csv" --orders "$tmp/dup_orders.csv" > /dev/null 2> "$tmp/e_dup.txt"; r1=$?
pf --events "$tmp/clean_events.csv" --orders "$tmp/junkdate_orders.csv" > /dev/null 2> "$tmp/e_junk.txt"; r2=$?
pf --events "$tmp/clean_events.csv" --last-bfcm "$tmp/march_bfcm.csv" > /dev/null 2> "$tmp/e_march.txt"; r3=$?
pf --events "$tmp/clean_events.csv" --json "$tmp/no/such/dir/a.json" > "$tmp/badjson.txt" 2> "$tmp/e_json.txt"; r4=$?
set -e
[ "$r1" = 2 ] && grep -q "line 102: duplicate order_id '#1007' (first on line 9)" "$tmp/e_dup.txt" || fail "duplicate order_id accepted (rc=$r1)"
[ "$r2" = 2 ] && grep -q "line 6: date must be YYYY-MM-DD or an ISO timestamp" "$tmp/e_junk.txt" || fail "junk date accepted (rc=$r2)"
[ "$r3" = 2 ] && grep -q "line 3: last-BFCM date 2025-03-29 is not in November or December" "$tmp/e_march.txt" || fail "March BFCM date accepted (rc=$r3)"
[ "$r4" = 2 ] && grep -q "cannot write --json file" "$tmp/e_json.txt" && ! grep -q Traceback "$tmp/e_json.txt" || fail "bad --json path (rc=$r4)"
pf --events "$tmp/clean_events.csv" --orders "$tmp/window_orders.csv" > "$tmp/window.txt"
has "$tmp/window.txt" "**INFO**: 3 orders (3.0%) are dated outside the events window (Sep 8–Oct 5), from 2019-11-29 to 2019-11-29." "out-of-window orders not reported"
ok "input checks: duplicates, dates, BFCM months, --json path, analysis window"

echo "all $pass checks passed"

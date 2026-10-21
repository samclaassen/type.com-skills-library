#!/usr/bin/env bash
# End-to-end checks for BFCM Offer & Discount Config QA. No network or credentials needed.
#   bash tests/run_tests.sh
set -euo pipefail
export PYTHONDONTWRITEBYTECODE=1
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
pass=0
ok() { pass=$((pass + 1)); echo "ok $pass - $1"; }
fail() { echo "FAIL - $1"; exit 1; }
has() { grep -qF -- "$2" "$1" || { echo "--- missing: $2"; cat "$1"; fail "$3"; }; }
qa() { local c="$1"; shift; python3 scripts/offer_qa.py --plan "$tmp/$c/plan.json" --discounts "$tmp/$c/discounts.json" \
         --products "$tmp/$c/products.csv" "$@"; }

python3 tests/make_cases.py "$tmp" > /dev/null

# 1. Sample output is byte-identical to the published expected output, and stable across runs.
bash examples/run.sh > "$tmp/out.txt"
cmp -s "$tmp/out.txt" examples/expected_output.txt || { diff "$tmp/out.txt" examples/expected_output.txt | head; fail "sample output differs"; }
bash examples/run.sh > "$tmp/out2.txt"
cmp -s "$tmp/out.txt" "$tmp/out2.txt" || fail "output is not deterministic"
ok "sample run matches examples/expected_output.txt"

# 2. The fixture generator reproduces the bundled sample data exactly.
cp -r examples "$tmp/ex" && python3 "$tmp/ex/make_fixtures.py" > /dev/null
for f in offer_plan.json discounts.json shopify_discount_nodes.json products.csv price_history.csv; do
  cmp -s "$tmp/ex/data/$f" "examples/data/$f" || fail "make_fixtures.py does not reproduce $f"
done
ok "make_fixtures.py regenerates examples/data byte for byte"

# 3. The sample reproduces the store's known findings.
f="$tmp/out.txt"
has "$f" "**Verdict: NOT READY.** 5 critical issues and 3 warnings." "sample verdict"
has "$f" "BFCM 25% off sitewide applies to All products, so 5 SKUs the plan leaves out get 25% off: Last Chance (3) and New Arrivals (2)." "all-products targeting"
has "$f" "so all 11 discountable SKUs can go 32.5% off during Nov 26 – Dec 1 (plan max 25%)." "32.5% stack"
has "$f" "MAYA20 (20% off products) has no usage limit (plan: 300 in total), isn't limited to one use per customer (plan: once per customer) and can combine with BFCM 25% off sitewide (plan: must not)." "MAYA20 settings"
has "$f" 'Free shipping on orders $75+ has no minimum purchase ($0 instead of $75), so every order ships free.' "free shipping minimum"
has "$f" "VIPEARLY ends Wed Nov 25 at 6:59 pm ET (23:59 UTC) instead of 11:59 pm ET, leaving a 5-hour gap" "VIPEARLY UTC slip"
has "$f" "SUMMER15 (15% off products) is still active with no end date; the plan says it must not be active." "SUMMER15"
has "$f" '| 1 | Thu Nov 26 12:05 am | segment Never purchased | Merino Wool Throw — Oat ($77.00) | WELCOME10 | $69.30 (WELCOME10) | $51.97 (BFCM 25% off sitewide + WELCOME10) |' "worst-case test cart"
ok "sample findings match the Fernhill story"

# 4. The normalizer turns the GraphQL response into the sample discounts.json, and handles edge cases.
python3 scripts/normalize_shopify_discounts.py --in examples/data/shopify_discount_nodes.json \
  --exported-at 2026-10-06T14:05:00-04:00 --shop "Fernhill Goods" --out "$tmp/norm.json" 2> /dev/null
cmp -s "$tmp/norm.json" examples/data/discounts.json || { diff "$tmp/norm.json" examples/data/discounts.json | head; fail "normalizer output differs"; }
python3 scripts/normalize_shopify_discounts.py --in "$tmp/gql_edge.json" --exported-at 2026-10-06T14:05:00-04:00 \
  --out "$tmp/edge.json" 2> /dev/null
python3 - "$tmp/edge.json" <<'EOF' || fail "normalizer edge cases"
import json, sys
d = {x["title"]: x for x in json.load(open(sys.argv[1]))["discounts"]}
b = d["Buy 2 get 1 free mugs"]           # edges form, BXGY, UnsignedInt64 as strings
assert (b["type"], b["class"], b["value"], b["codes"]) == ("bxgy", "product", 100, ["MUGS3"]), b
assert b["bxgy"]["buys"] == {"quantity": 2} and b["bxgy"]["gets"] == {"quantity": 1, "percentage": 100}, b
assert b["target"] == {"type": "products", "products": ["stoneware-mug-set"], "variants": []}, b
f = d["$10 off $100"]                    # deprecated discountClass / customerSelection / usageCount
assert (f["class"], f["type"], f["value"], f["appliesOnEachItem"], f["timesUsed"]) == ("order", "fixed_amount", 10, False, 12), f
assert f["minimum"] == {"subtotal": 100} and f["eligibility"] == {"type": "all"}, f
a = d["Tiered app discount"]
assert (a["type"], a["class"], a["classes"], a["appTitle"]) == ("app", "multi", ["product", "order"], "Volume tiers"), a
c = d["Canada loyalty"]                  # 0.15 -> 15, bulk codes, markets, quantity minimum, same-line tags
assert (c["value"], c["codesCount"], c["minimum"]) == (15, 1200, {"quantity": 2}), c
assert c["eligibility"] == {"type": "markets", "names": ["Canada"]} and c["combinesWith"]["sameLineTags"] == ["loyalty"], c
EOF
set +e
python3 scripts/normalize_shopify_discounts.py --in "$tmp/gql_errors.json" > /dev/null 2> "$tmp/n1.txt"; r1=$?
python3 scripts/normalize_shopify_discounts.py --in "$tmp/gql_partial.json" > /dev/null 2> "$tmp/n2.txt"; r2=$?
set -e
[ "$r1" = 2 ] && grep -q "the API returned errors: Field 'productDiscountsWithTagsOnSameCartLine'" "$tmp/n1.txt" || fail "API errors accepted (rc=$r1)"
[ "$r2" = 2 ] && grep -q 'hasNextPage is true: fetch the next page with after: "c1"' "$tmp/n2.txt" || fail "partial export accepted (rc=$r2)"
ok "normalizer: GraphQL fixture to discounts.json, edge cases, API errors and unfinished pages"

# 5. Clean config: READY, no fix items above PLAN, and --json agrees.
qa clean --price-history "$tmp/clean/history.csv" --json "$tmp/clean.json" > "$tmp/clean.txt"
has "$tmp/clean.txt" "**Verdict: READY.** No configuration issues found." "clean config not READY"
! grep -qE "\| (CRITICAL|WARNING) \|" "$tmp/clean.txt" || fail "clean config has fix items"
has "$tmp/clean.txt" "No test carts needed" "clean config generated test carts"
python3 -c "import json,sys; a=json.load(open(sys.argv[1])); sys.exit(0 if a['verdict']=='READY' and a['critical']==0 and a['warnings']==0 else 1)" "$tmp/clean.json" \
  || fail "--json verdict wrong"
ok "clean config is READY (text and --json)"

# 6. A start time entered in UTC is caught, with the overlap it creates.
qa utc_start > "$tmp/utc.txt"
has "$tmp/utc.txt" "BFCM 25% off sitewide starts Wed Nov 25 at 7:00 pm ET (00:00 UTC) instead of 12:00 am ET Thu Nov 26 (5 h early), so VIPEARLY and BFCM 25% off sitewide overlap for 5 h." "UTC slip not detected"
has "$tmp/utc.txt" "| — | *both live* | Wed Nov 25 7:00 pm | Thu Nov 26 12:00 am | **5 h overlap** (plan: none) |" "overlap row"
ok "UTC slip on a phase start detected"

# 7. A gap between phases (not a time-zone slip) is caught and sized.
qa gap > "$tmp/gap.txt"
has "$tmp/gap.txt" "VIPEARLY ends Wed Nov 25 at 9:00 pm ET instead of 11:59 pm ET, leaving a 3-hour gap (Wed Nov 25, 9:00 pm to midnight) before BFCM 25% off sitewide starts." "gap not detected"
has "$tmp/gap.txt" "| 1 | Wed Nov 25 9:30 pm | segment SMS subscribers | Merino Wool Throw — Charcoal (\$140.00) | VIPEARLY | \$105.00 (VIPEARLY) | \$140.00 (VIPEARLY refused) |" "gap test cart"
ok "gap between phases detected, with a test cart inside it"

# 8. Combinations need both sides: one side alone stacks nothing; both sides stack to 32.5%.
qa oneside > "$tmp/one.txt"
has "$tmp/one.txt" "**Verdict: READY.**" "one-sided setting should not stack"
has "$tmp/one.txt" "| Merino Wool Throw — Charcoal | \$140.00 | \$52.00 | \$105.00 (25%) | \$105.00 (25%) |" "one-sided worst case"
has "$tmp/one.txt" "INFO**: BFCM 25% off sitewide is set to combine with order discounts (plan: no). No live order discount allows product discounts back, so nothing stacks today" "latent setting not noted"
qa bothsides > "$tmp/both.txt"
has "$tmp/both.txt" "BFCM 25% off sitewide and WELCOME10 stack: BFCM 25% off sitewide is set to combine with order discounts and WELCOME10 with product discounts, so 6 SKUs can go 32.5% off during Nov 26 – Dec 1 (plan max 25%)." "two-sided stack"
has "$tmp/both.txt" "| Merino Wool Throw — Charcoal | \$140.00 | \$52.00 | \$105.00 (25%) | **\$94.50 (32.5%)** |" "two-sided worst case"
ok "a pair combines only when both allow it"

# 9. Worst-case math on a hand-computed case (Chicago time):
#    HAND-1 $50: 20% -> $40; 10% + 5% of $40 -> $34; $10 off $100+ shared by a $100 cart: 10 x 40/100 = $4 -> $30.00 (40%).
#    HAND-2 $200: -> $160; $16 + $8 -> $136; $10 -> $126.00 (37%).
qa hand > "$tmp/hand.txt"
has "$tmp/hand.txt" "| Hand One | \$50.00 | \$20.00 | \$30.00 (40%) | \$30.00 (40%) | Sitewide 20 + FIVEOFF + SPEND100 + TENOFF | Nov 24 – Nov 30 | 33.3% |" "hand case HAND-1"
has "$tmp/hand.txt" "| Hand Two | \$200.00 | \$60.00 | \$126.00 (37%) | \$126.00 (37%) | Sitewide 20 + FIVEOFF + SPEND100 + TENOFF | Nov 24 – Nov 30 | 52.4% |" "hand case HAND-2"
has "$tmp/hand.txt" "Times are store time, America/Chicago (CT)." "store time zone"
ok "worst-case stack math matches the hand-computed case"

# 10. Shopify Help Center examples (Combining discounts) reproduce to the cent.
python3 - <<'EOF' || fail "Help Center examples"
import datetime as dt, importlib.util, sys
from decimal import Decimal as D
spec = importlib.util.spec_from_file_location("qa", "scripts/offer_qa.py"); qa = importlib.util.module_from_spec(spec); spec.loader.exec_module(qa)
tz = qa.load_tz("America/New_York", None)
cat = {s: {"sku": s, "price": D(p), "gift_card": False} for s, p in (("pants", 100), ("shirts", 50), ("boots", 200))}
def d(key, cls, typ, value, skus=("pants", "shirts", "boots"), code=True, minimum=None):
    return {"key": key, "label": key, "method": "code" if code else "automatic", "codes": [key] if code else [], "cls": cls,
            "type": typ, "value": D(value) if value is not None else None, "each_item": True, "skus": set(skus),
            "min_subtotal": D(minimum) if minimum else None, "min_qty": None, "elig": {"type": "all", "names": []},
            "combines": {"product": True, "order": True, "shipping": True}, "starts": qa.EARLIEST, "ends": None,
            "status": "ACTIVE", "modeled": True}
cart = [("pants", 1), ("shirts", 1), ("boots", 1)]
now = dt.datetime(2026, 10, 6, tzinfo=dt.timezone.utc)
def total(discs):
    r = qa.simulate(discs, cat, cart, now, set(), [x["key"] for x in discs if x["method"] == "code"], tz)
    return r["total"], r["free_ship"]
pants20, shirts10 = d("20offPants", "product", "fixed_amount", 20, ["pants"]), d("10offShirts", "product", "fixed_amount", 10, ["shirts"])
ship100 = d("Freeship100", "shipping", "free_shipping", None, [], code=False, minimum=100)
assert total([pants20, shirts10, ship100]) == (D(320), True)
assert total([pants20, d("10offOrder", "order", "percentage", 10)]) == (D(297), False)
assert total([d("10offOrder", "order", "percentage", 10), d("15offOver200", "order", "percentage", 15, code=False, minimum=200)]) == (D("262.50"), False)
assert total([d("10offOrder", "order", "percentage", 10), d("20offOver200", "order", "fixed_amount", 20, code=False, minimum=200)]) == (D(295), False)
assert total([pants20, d("10offBoots", "product", "percentage", 10, ["boots"]), d("10offOrder", "order", "percentage", 10),
              d("15offOver200", "order", "percentage", 15, code=False, minimum=200), ship100]) == (D("232.50"), True)
# Two uncombinable discounts: the customer gets the better one.
a, b = d("A20", "product", "percentage", 20), d("B30", "order", "fixed_amount", 30)
a["combines"] = b["combines"] = {"product": False, "order": False, "shipping": False}
assert total([a, b]) == (D(280), False)
EOF
ok "Shopify Help Center combination examples reproduce to the cent"

# 11. Below unit cost and below the margin floor are caught.
qa cost > "$tmp/cost.txt"
has "$tmp/cost.txt" '1 SKU sells below unit cost at the worst-case stack (Sale 25, Nov 27 – Nov 30): Cost One at $22.50 (unit cost $25.00).' "below cost not detected"
has "$tmp/cost.txt" '1 SKU sells below the 15% margin floor at the worst-case stack (Sale 25, Nov 27 – Nov 30): Cost Two at $30.00 (unit cost $26.00).' "margin floor not detected"
python3 scripts/offer_qa.py --plan "$tmp/cost/plan.json" --discounts "$tmp/cost/discounts.json" --products "$tmp/cost/products.csv" \
  --margin-floor 10 > "$tmp/cost10.txt"
! grep -q "margin floor at the worst-case" "$tmp/cost10.txt" || fail "--margin-floor not applied"
ok "below-cost and margin-floor detection (and --margin-floor)"

# 12. Usage limit and once-per-customer mismatches.
qa limits > "$tmp/limits.txt"
has "$tmp/limits.txt" "MAYA20 (20% off products) has a usage limit of 500 (plan: 300 in total) and isn't limited to one use per customer (plan: once per customer)." "usage mismatch"
has "$tmp/limits.txt" "| MAYA20 | 20% off products | 118 | **500** (300) | **no** (yes) |" "codes table"
ok "usage-limit and once-per-customer mismatches"

# 13. Missing optional price history: still runs and says what wasn't checked.
qa clean > "$tmp/nohist.txt"
has "$tmp/nohist.txt" "Not checked: compare-at raises (no --price-history file)." "missing history not named"
has "$tmp/nohist.txt" "Skipped: no price history (--price-history), so compare-at raises were not checked." "reference section not skipped"
has "$tmp/nohist.txt" "**Verdict: READY.**" "missing optional file changed the verdict"
ok "missing optional price history is skipped and named"

# 14. Invalid input exits 2 with the reason.
set +e
python3 scripts/offer_qa.py --plan examples/data/offer_plan.json --discounts "$tmp/bad_offset.json" --products examples/data/products.csv > /dev/null 2> "$tmp/e1.txt"; e1=$?
python3 scripts/offer_qa.py --plan examples/data/offer_plan.json --discounts examples/data/discounts.json --products "$tmp/bad_products.csv" > /dev/null 2> "$tmp/e2.txt"; e2=$?
python3 scripts/offer_qa.py --plan "$tmp/bad_tz.json" --discounts examples/data/discounts.json --products examples/data/products.csv > /dev/null 2> "$tmp/e3.txt"; e3=$?
python3 scripts/offer_qa.py --plan "$tmp/bad_class.json" --discounts examples/data/discounts.json --products examples/data/products.csv > /dev/null 2> "$tmp/e4.txt"; e4=$?
python3 scripts/offer_qa.py --plan examples/data/offer_plan.json --discounts examples/data/discounts.json --products "$tmp/nope.csv" > /dev/null 2> "$tmp/e5.txt"; e5=$?
set -e
[ "$e1" = 2 ] && grep -q "discounts\[0\] (WELCOME10): startsAt: '2025-02-01T05:00:00' has no UTC offset" "$tmp/e1.txt" || fail "time without offset accepted (rc=$e1)"
[ "$e2" = 2 ] && grep -q "line 2: 'price' is negative (-48.00)" "$tmp/e2.txt" || fail "negative price accepted (rc=$e2)"
[ "$e3" = 2 ] && grep -q "unknown timezone 'Mars/Olympus'" "$tmp/e3.txt" || fail "unknown timezone accepted (rc=$e3)"
[ "$e4" = 2 ] && grep -q "offers\[0\] (VIPEARLY): 'class' must be product (amount off products) or order (amount off order)" "$tmp/e4.txt" || fail "offer without class accepted (rc=$e4)"
[ "$e5" = 2 ] && grep -q "cannot read products file" "$tmp/e5.txt" || fail "missing file accepted (rc=$e5)"
ok "invalid input exits 2 with the reason"

# 15. Built-in time-zone rules: known offsets, DST edges, and (where available) agreement with zoneinfo.
python3 - <<'EOF' || fail "time-zone rules"
import datetime as dt, importlib.util
spec = importlib.util.spec_from_file_location("qa", "scripts/offer_qa.py"); qa = importlib.util.module_from_spec(spec); spec.loader.exec_module(qa)
U = dt.timezone.utc
ny = qa.load_tz("America/New_York", None)
assert ny.to_utc(dt.datetime(2026, 11, 26, 0, 0)) == dt.datetime(2026, 11, 26, 5, 0, tzinfo=U)
assert ny.to_utc(dt.datetime(2026, 10, 6, 14, 5)) == dt.datetime(2026, 10, 6, 18, 5, tzinfo=U)
assert ny.to_utc(dt.datetime(2026, 11, 1, 1, 30)) == dt.datetime(2026, 11, 1, 5, 30, tzinfo=U)     # ambiguous: first one
assert ny.to_local(dt.datetime(2026, 11, 1, 5, 59, 59, tzinfo=U)) == dt.datetime(2026, 11, 1, 1, 59, 59)
assert ny.to_local(dt.datetime(2026, 11, 1, 6, 0, tzinfo=U)) == dt.datetime(2026, 11, 1, 1, 0)
assert ny.to_local(dt.datetime(2026, 3, 8, 7, 0, tzinfo=U)) == dt.datetime(2026, 3, 8, 3, 0)
syd = qa.load_tz("Australia/Sydney", None)
assert syd.to_local(dt.datetime(2026, 11, 27, 0, 0, tzinfo=U)) == dt.datetime(2026, 11, 27, 11, 0)
try:
    import zoneinfo
    zones = [z for z in qa.TZ_RULES if "/" in z]
    zoneinfo.ZoneInfo("America/New_York")
except Exception:
    zones = []
missing = []
for z in zones:
    try:
        zi = zoneinfo.ZoneInfo(z)
    except Exception:          # e.g. legacy US/* links absent from the system tz database
        missing.append(z)
        continue
    tz = qa.load_tz(z, None)
    t = dt.datetime(2025, 1, 1, tzinfo=U)
    while t < dt.datetime(2028, 1, 1, tzinfo=U):
        want = t.astimezone(zi).replace(tzinfo=None)
        assert tz.to_local(t) == want, (z, t, tz.to_local(t), want)
        t += dt.timedelta(hours=7, minutes=13)
print("zoneinfo cross-check:", f"{len(zones) - len(missing)} zones" + (f", {len(missing)} not in the system tz database" if missing else "")
      if zones else "skipped (no zoneinfo on this Python)")
EOF
ok "time-zone rules (DST edges; zoneinfo cross-check where available)"

# 16. Regression (QA 6.1): a buy X get Y whose "get" is a fixed amount used to crash the analyzer.
python3 scripts/normalize_shopify_discounts.py --in "$tmp/gql_bxgy_amount.json" --exported-at 2026-10-06T14:05:00-04:00 \
  --out "$tmp/bxamt.json" 2> /dev/null || fail "normalizer rejected a fixed-amount buy X get Y"
python3 -c "import json,sys; b=[d for d in json.load(open(sys.argv[1]))['discounts'] if d['title']=='SUMMER15'][0]['bxgy']['gets']; assert b == {'quantity': 1, 'percentage': None, 'amount': 5}, b" "$tmp/bxamt.json" \
  || fail "normalizer should keep the get amount"
python3 scripts/offer_qa.py --plan examples/data/offer_plan.json --discounts "$tmp/bxamt.json" --products examples/data/products.csv \
  --price-history examples/data/price_history.csv > "$tmp/bxamt.txt" 2> "$tmp/bxamt.err" || { cat "$tmp/bxamt.err"; fail "fixed-amount buy X get Y crashed the analyzer"; }
has "$tmp/bxamt.txt" "SUMMER15 (buy X get 1, \$5 off the get item) is still active with no end date" "fixed-amount get described"
has "$tmp/bxamt.txt" "Not checked: stacking and below-cost for SUMMER15 (buy X get 1, \$5 off the get item): not modeled" "fixed-amount get marked not modeled"
ok "buy X get Y with a fixed-amount get: no crash, described, marked not modeled for stacking/below-cost"

# 17. Split fixed-amount product discount with a minimum: the item's share follows the minimum cart (QA 6.2).
qa split > "$tmp/split.txt"
has "$tmp/split.txt" "| Split One | \$30.00 | \$20.00 | \$24.00 (20%) | \$24.00 (20%) | Twenty off 100 |" "split share \$24.00"
! grep -q "below unit cost" "$tmp/split.txt" || fail "false below-cost for a split amount with a minimum"
has "$tmp/split.txt" "**Verdict: READY.**" "split case should be READY"
ok "split fixed-amount discount with a \$100 minimum: \$30 item ends at \$24.00, no false CRITICAL"

# 18. App discounts: targeting isn't visible, so eligibility is INFO "check manually", not All products (QA 6.3).
qa app > "$tmp/app.txt"
has "$tmp/app.txt" "- **INFO**: BFCM 25% off sitewide: App discount: targeting not visible via API; check manually." "app INFO"
! grep -q "applies to All products" "$tmp/app.txt" || fail "app discount reported as All products"
python3 -c "import json,sys; d={x['title']: x for x in json.load(open(sys.argv[1]))['discounts']}; t=d['Tiered app discount']['target']; assert t == {'type': 'unknown'}, t" "$tmp/edge.json" \
  || fail "normalizer should write target unknown for app discounts"
ok "app and multi-class discounts: target unknown, eligibility checked manually"

# 19. A compare-at never seen as a selling price is flagged; the report states the US-only scope (QA 6.4).
qa never --price-history "$tmp/never/history.csv" > "$tmp/never.txt"
has "$tmp/never.txt" "Walnut Serving Board: compare-at \$96.00 never appears as a selling price in its price history (from Thu Jul 9); it sold at \$72.00." "never-seen compare-at"
has "$tmp/never.txt" "This check follows US (FTC 16 CFR 233.1) guidance. It does not apply the EU/UK rule that the reference price must be the lowest price in the prior 30 days." "US-only note"
ok "compare-at never seen as a selling price is flagged; US/EU scope stated"

# 20. Target lists cut off at 50 are flagged by the normalizer and skipped by the analyzer (QA 6.5).
python3 scripts/normalize_shopify_discounts.py --print-query | grep -q "products(first: 50) { pageInfo { hasNextPage } nodes { handle } }" \
  || fail "query should ask for pageInfo on nested target connections"
python3 scripts/normalize_shopify_discounts.py --in "$tmp/gql_truncated.json" --out "$tmp/trunc.json" 2> "$tmp/trunc.err"
has "$tmp/trunc.err" "target truncated at 50; check Applies to in the admin" "truncation warning"
grep -q '"truncated": true' "$tmp/trunc.json" || fail "truncated flag missing"
qa truncated > "$tmp/trunc.txt"
has "$tmp/trunc.txt" "eligibility for VIPEARLY (its product or collection list was cut off at 50 in the export; check Applies to in the admin)" "truncated target skipped"
ok "truncated target lists warned and skipped"

# 21. Polish (QA 6.6): non-numeric percentage exits 2; no-end overlap; test-cart reason; fraction warning.
set +e
python3 scripts/normalize_shopify_discounts.py --in "$tmp/gql_bad_pct.json" > /dev/null 2> "$tmp/n3.txt"; r3=$?
set -e
[ "$r3" = 2 ] && grep -q "percentage 'fifteen' is not a number" "$tmp/n3.txt" || { cat "$tmp/n3.txt"; fail "non-numeric percentage (rc=$r3)"; }
qa noend > "$tmp/noend.txt"
has "$tmp/noend.txt" "overlap for all of BFCM 25% off sitewide." "no-end overlap wording"
has "$tmp/noend.txt" "| — | *both live* | Thu Nov 26 12:00 am | no end date | **overlap: VIPEARLY has no end date** (plan: none) |" "no-end overlap row"
! grep -q "26698 days" "$tmp/noend.txt" || fail "no-end overlap printed as days"
qa vipboth > "$tmp/vipboth.txt"
has "$tmp/vipboth.txt" "| \$105.00 (VIPEARLY) | \$140.00 (VIPEARLY refused) | VIPEARLY ends at 6:59 pm ET |" "test-cart reason names the early end"
qa fraction > /dev/null 2> "$tmp/frac.err"
has "$tmp/frac.err" "percentage 'value' 0.2 is read as 0.2%" "fraction warning"
ok "normalizer exits 2 on a bad percentage; no-end overlap, test-cart reason and fraction warning"

# 22. Regression (QA re-review B-1): a discount that takes an item to $0.00 used to crash (margin None < floor).
python3 - "$tmp" <<'EOF'
import json, sys
d = json.load(open("examples/data/discounts.json"))
def walk(o):
    if isinstance(o, dict):
        if o.get("title") == "VIPEARLY":
            o["value"] = 100
            return True
        return any(walk(v) for v in o.values())
    return isinstance(o, list) and any(walk(v) for v in o)
assert walk(d)
json.dump(d, open(sys.argv[1] + "/free.json", "w"), indent=2)
EOF
set +e
python3 scripts/offer_qa.py --plan examples/data/offer_plan.json --discounts "$tmp/free.json" \
  --products examples/data/products.csv --price-history examples/data/price_history.csv > "$tmp/free.txt" 2> "$tmp/free.err"
rc=$?; set -e
[ "$rc" = 0 ] || { cat "$tmp/free.err"; fail "a 100% discount crashed the analyzer (exit $rc)"; }
has "$tmp/free.txt" "Ceramic Pour-Over Set at \$0.00 (unit cost \$21.00)" "free item reported below cost"
has "$tmp/free.txt" "VIPEARLY is 100% off products (plan: 25% off products)" "free item value mismatch"
# One date window per stack in the title (the BFCM + WELCOME10 SKUs bottom out Nov 26 – Dec 1, not VIPEARLY's dates).
has "$tmp/free.txt" "at the worst-case stacks (BFCM 25% off sitewide + WELCOME10, Nov 26 – Dec 1; VIPEARLY, Nov 24 – Nov 25)" "one window per stack"
ok "a discount that takes items to \$0.00 is reported below unit cost instead of crashing"

# 23. Scripts parse under Python 3.8 grammar.
python3 - <<'EOF' || fail "not Python 3.8 compatible"
import ast
for p in ("scripts/offer_qa.py", "scripts/normalize_shopify_discounts.py", "examples/make_fixtures.py", "tests/make_cases.py"):
    ast.parse(open(p, encoding="utf-8").read(), p, feature_version=(3, 8))
EOF
ok "scripts parse as Python 3.8"

echo "all $pass checks passed"

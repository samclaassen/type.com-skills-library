#!/usr/bin/env bash
# Run from the skill directory: bash tests.sh
# Offline checks for gsc_analyze.py and gsc_api_to_csv.py using only bundled sample data.
set -euo pipefail

TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
PASS=0
A="python3 gsc_analyze.py"
P="--property sc-domain:harborandvine.example --as-of 2026-09-28"

ok() { PASS=$((PASS + 1)); echo "ok   $1"; }
fail() { echo "FAIL $1"; exit 1; }

# expect_fail NAME EXPECTED_STDERR_SUBSTRING CMD...
expect_fail() {
  local name="$1" want="$2"; shift 2
  set +e
  "$@" > "$TMP/out" 2> "$TMP/err"
  local code=$?
  set -e
  [ "$code" -eq 2 ] || fail "$name: expected exit 2, got $code"
  grep -qF -- "$want" "$TMP/err" || fail "$name: stderr lacks '$want': $(cat "$TMP/err")"
  ok "$name"
}

# 1. sample run is byte-exact and deterministic
bash run.sh > "$TMP/a.txt"
bash run.sh > "$TMP/b.txt"
diff -q "$TMP/a.txt" expected_output.txt > /dev/null || fail "run.sh differs from expected_output.txt"
diff -q "$TMP/a.txt" "$TMP/b.txt" > /dev/null || fail "run.sh is not deterministic"
ok "run.sh matches expected_output.txt (twice)"

# 2. non-numeric clicks -> exit 2 naming file, row, column
sed '3s/,[0-9]*,\([0-9]*\),/,lots,\1,/' sample_current.csv > "$TMP/bad_clicks.csv"
expect_fail "non-numeric clicks" "row 3, column 'clicks'" \
  $A --current "$TMP/bad_clicks.csv" --prior sample_prior.csv $P

# 3. clicks greater than impressions -> exit 2
printf 'query,page,clicks,impressions,ctr,position\nwine,https://harborandvine.example/,90,40,,3.0\n' > "$TMP/gt.csv"
expect_fail "clicks exceed impressions" "row 2, column 'clicks': 90 clicks exceed 40 impressions" \
  $A --current "$TMP/gt.csv" --prior "$TMP/gt.csv" $P

# 4. missing required column -> exit 2
cut -d, -f1-4 sample_current.csv > "$TMP/nopos.csv"
expect_fail "missing position column" "missing required column 'position'" \
  $A --current "$TMP/nopos.csv" --prior sample_prior.csv $P

# 5. missing value is reported, never guessed
printf 'query,page,clicks,impressions,ctr,position\nwine,https://harborandvine.example/,5,40,,\n' > "$TMP/blank.csv"
expect_fail "blank position" "row 2, column 'position': value is missing" \
  $A --current "$TMP/blank.csv" --prior "$TMP/blank.csv" $P

# 6. duplicate query x page row -> exit 2
{ cat sample_current.csv; sed -n '2p' sample_current.csv; } > "$TMP/dup.csv"
expect_fail "duplicate row" "duplicate of row 2" \
  $A --current "$TMP/dup.csv" --prior sample_prior.csv $P

# 7. periods with different shapes -> exit 2
expect_fail "mixed export shapes" "both periods must use the same export shape" \
  $A --current sample_current.csv --prior sample_ui_queries_prior.csv $P

# 8. no property configured -> exit 2
expect_fail "missing property" "set gsc_property" \
  $A --current sample_current.csv --prior sample_prior.csv --as-of 2026-09-28

# 9. ctr_benchmark custom without a curve -> exit 2
sed 's/"ctr_benchmark": "auto"/"ctr_benchmark": "custom"/' settings.json > "$TMP/custom.json"
expect_fail "custom benchmark without curve" "needs at least 2 position/CTR points" \
  $A --current sample_current.csv --prior sample_prior.csv --config "$TMP/custom.json" $P

# 10. guardrail: under 1,000 clicks -> low-volume warning, no priorities
$A --current sample_lowvol_current.csv --prior sample_lowvol_prior.csv \
  --property https://tidepoolpottery.example/ --as-of 2026-09-28 > "$TMP/low.txt"
grep -qF "Low data volume:** 345 clicks this period (under 1,000)" "$TMP/low.txt" || fail "low-volume warning missing"
grep -qF "Priorities not assigned (low data volume)." "$TMP/low.txt" || fail "low-volume summary missing"
if grep -qF "**[critical]**" "$TMP/low.txt" || grep -qF "**[high]**" "$TMP/low.txt"; then fail "low-volume run assigned priorities"; fi
ok "low-volume guardrail"

# 11. reduced mode (UI export): cannibalization not evaluated, declining pages still work
$A --current sample_ui_queries_current.csv --prior sample_ui_queries_prior.csv \
  --current-pages sample_ui_pages_current.csv --prior-pages sample_ui_pages_prior.csv $P > "$TMP/red.txt"
grep -qF "Not evaluated: the Search Console UI export has no query x page data" "$TMP/red.txt" || fail "reduced: cannibalization line"
grep -qF "| /wine-club | 218 | 450 | −52% | n/a (no query x page data) | Avg position 3.7→6.7 |" "$TMP/red.txt" || fail "reduced: declining row"
ok "reduced mode (UI export)"

# 12. impressions-weighted position when rows are combined: (100*2 + 300*6) / 400 = 5.0
printf 'query,page,clicks,impressions,ctr,position\nq,https://x.example/a,10,100,,2\nq,https://x.example/b,3,300,,6\n' > "$TMP/w.csv"
$A --current "$TMP/w.csv" --prior "$TMP/w.csv" --property sc-domain:x.example --as-of 2026-09-28 --json > "$TMP/w.json"
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert d['summary']['position']==5.0, d['summary']" "$TMP/w.json" \
  || fail "weighted position"
ok "impressions-weighted position"

# 13. API JSON -> CSV converter reproduces the contract rows
python3 gsc_api_to_csv.py --dimensions query,page --out "$TMP/conv.csv" sample_api_response.json
[ "$(head -1 "$TMP/conv.csv")" = "query,page,clicks,impressions,ctr,position" ] || fail "converter header"
grep -qF "wine club membership,https://harborandvine.example/wine-club,120,3900," "$TMP/conv.csv" || fail "converter row"
expect_fail "converter dimension mismatch" "has 2 values but --dimensions has 1" \
  python3 gsc_api_to_csv.py --dimensions query sample_api_response.json
ok "API converter"

# 14. --plan: read-only scope, encoded property, lag-adjusted windows
$A --plan $P > "$TMP/plan.json"
python3 - "$TMP/plan.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
assert d["scope"] == "https://www.googleapis.com/auth/webmasters.readonly"
assert d["endpoint"].endswith("/sites/sc-domain%3Aharborandvine.example/searchAnalytics/query")
assert d["periods"]["current"] == {"startDate": "2026-08-29", "endDate": "2026-09-25"}
assert d["periods"]["prior"] == {"startDate": "2026-08-01", "endDate": "2026-08-28"}
assert all(r["body"]["dataState"] == "final" and r["body"]["type"] == "web" for r in d["requests"])
PY
ok "--plan request bodies"

# 15. new property: empty prior period -> exit 2 with a helpful message, never a comparison against nothing
head -1 sample_prior.csv > "$TMP/empty_prior.csv"
expect_fail "empty prior period explains itself" "has no Search Console data" \
  $A --current sample_current.csv --prior "$TMP/empty_prior.csv" $P
grep -qF "shorter compare_period" "$TMP/err" || fail "empty prior: no compare_period suggestion"
expect_fail "empty prior with --first-data-date" "largest compare_period that fits is 18 days" \
  $A --current sample_current.csv --prior "$TMP/empty_prior.csv" $P --first-data-date 2026-08-20
grep -qF -- "--as-of 2026-10-17" "$TMP/err" || fail "first-data-date: no full-window date"
expect_fail "--plan refuses a prior window before first data" "starts before the first date with data" \
  $A --plan $P --first-data-date 2026-08-20
$A --plan $P --first-data-date 2026-08-01 > "$TMP/plan2.json"
python3 -c "import json,sys; d=json.load(open(sys.argv[1])); assert d['first_data_date_probe']['body']['dimensions']==['date']" "$TMP/plan2.json" \
  || fail "plan: first_data_date_probe"
ok "--first-data-date fits"

# 16. reduced mode: fallback phrases are not wrapped in quotes in action text
$A --current sample_ui_queries_current.csv --prior sample_ui_queries_prior.csv \
  --current-pages sample_ui_pages_current.csv --prior-pages sample_ui_pages_prior.csv $P > "$TMP/red2.txt"
grep -qF "refresh content for its top query." "$TMP/red2.txt" || fail "reduced: unquoted fallback missing"
if grep -qF '"its top query"' "$TMP/red2.txt" || grep -qF '"the ranking page"' "$TMP/red2.txt"; then
  fail "reduced: fallback phrase is quoted"
fi
ok "reduced mode: fallback text unquoted"

# 17. Top countries leaves out zero-click and tiny-share countries
cat > "$TMP/qp.csv" <<'CSV'
query,page,clicks,impressions,ctr,position
q,https://x.example/a,200,1000,,2
CSV
cat > "$TMP/ctry.csv" <<'CSV'
query,country,clicks,impressions,ctr,position
q,deu,200,900,,2
q,and,0,50,,3
q,are,0,50,,4
CSV
$A --current "$TMP/qp.csv" --prior "$TMP/qp.csv" --countries "$TMP/ctry.csv" --property sc-domain:x.example --as-of 2026-09-28 > "$TMP/ctry.txt"
grep -qF '| DEU 100% |' "$TMP/ctry.txt" || fail "countries: expected only DEU 100%: $(grep '"q"' "$TMP/ctry.txt")"
grep -qF 'AND 0%' "$TMP/ctry.txt" && fail "countries: zero-click country shown"
printf 'q,fra,1,10,,5\n' >> "$TMP/ctry.csv"
sed -i 's/q,deu,200,/q,deu,199,/' "$TMP/ctry.csv"
$A --current "$TMP/qp.csv" --prior "$TMP/qp.csv" --countries "$TMP/ctry.csv" --property sc-domain:x.example --as-of 2026-09-28 > "$TMP/ctry2.txt"
grep -qF '| DEU 100% |' "$TMP/ctry2.txt" || fail "countries: share under breakdown_min_country_share shown"
ok "Top countries omits zero-click and tiny-share countries"

# 18. brand_terms / exclude_query_patterns keep brand and typo queries out of CTR and page-1 lists
python3 - "$TMP/brand.json" "$TMP/badre.json" <<'PY2'
import json, sys
cfg = json.load(open("settings.json"))
cfg["brand_terms"] = ["pinot noir"]
cfg["exclude_query_patterns"] = ["^wine gift (ideas|baskets)$"]
json.dump(cfg, open(sys.argv[1], "w"))
cfg["exclude_query_patterns"] = ["(unclosed"]
json.dump(cfg, open(sys.argv[2], "w"))
PY2
$A --current sample_current.csv --prior sample_prior.csv --config "$TMP/brand.json" $P --json > "$TMP/brand.out"
python3 - "$TMP/brand.out" <<'PY2'
import json, sys
d = json.load(open(sys.argv[1]))
ctr = [c["query"] for c in d["ctr_opportunities"]]
p1 = [c["query"] for c in d["page1_opportunities"]]
assert "pinot noir under $30" not in ctr and "wine gift ideas" not in ctr, ctr
assert "wine gift baskets" not in p1, p1
assert "how long does opened wine last" in ctr and "best wine for thanksgiving" in p1
ex = d["excluded_by_query_filter"]
assert "pinot noir under $30" in ex["ctr_opportunity"] and "wine gift baskets" in ex["page1_opportunity"], ex
PY2
$A --current sample_current.csv --prior sample_prior.csv --config "$TMP/brand.json" $P > "$TMP/brand.md"
grep -qF "left out by brand_terms/exclude_query_patterns" "$TMP/brand.md" || fail "brand: exclusion note missing"
grep -qF "left out by brand_terms" expected_output.txt && fail "brand: default settings should exclude nothing"
expect_fail "invalid exclude_query_patterns regex" "is not a valid regex" \
  $A --current sample_current.csv --prior sample_prior.csv --config "$TMP/badre.json" $P
ok "brand_terms / exclude_query_patterns"

# 19. regression: brand_terms match whole words, so a common-word brand ("type") does not hide "typeform"
PYTHONDONTWRITEBYTECODE=1 python3 - <<'PY2'
import gsc_analyze as g
cfg = {"_brand_res": [g.brand_term_re(t) for t in ["type", "harbor and vine"]], "_exclude_res": []}
for q in ["type.com", "type com", "Type", "TYPE.com pricing", "what is type", "harbor-and-vine", "Harbor and Vine wines"]:
    assert g.query_excluded(cfg, q), f"should be excluded: {q}"
for q in ["typeform slack", "typescript", "prototype", "font types", "harborandvine"]:
    assert not g.query_excluded(cfg, q), f"should NOT be excluded: {q}"
PY2
ok "brand_terms whole-word matching (typeform kept, type.com excluded)"

# 20. regression: empty-prior message without --first-data-date gives no arbitrary window sizes
expect_fail "empty prior: generic advice" "fits the data you have" \
  $A --current sample_current.csv --prior "$TMP/empty_prior.csv" $P
grep -qF -- "--first-data-date YYYY-MM-DD to compute it" "$TMP/err" || fail "empty prior: no --first-data-date pointer"
grep -qE "for example 7|7 or 14" "$TMP/err" && fail "empty prior: hard-coded 7/14 suggestion still present"
ok "empty prior: no hard-coded 7/14 suggestion"

echo "$PASS checks passed"
echo "ALL TESTS PASSED"

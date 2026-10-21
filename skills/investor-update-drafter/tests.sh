#!/usr/bin/env bash
# Guardrail, edge-case and regression tests. Run from the skill directory: bash tests.sh
# Builds variants of the fictional sample bundle in a temp dir; never touches real data.
# Set PYTHON=/path/to/python3.8 to run the suite on another interpreter.
set -euo pipefail

PY="${PYTHON:-python3}"
export PYTHONDONTWRITEBYTECODE=1  # some tests import build_update; leave no __pycache__ in the skill folder
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
SCRIPT=build_update.py
ASOF=2026-10-02T09:00:00-04:00
BASE=(--metrics-csv sample_metrics.csv --as-of "$ASOF")
PASS=0

ok() { PASS=$((PASS + 1)); echo "ok $PASS - $1"; }
fail() { echo "FAIL - $1"; exit 1; }

# variant NAME PYTHON_CODE : writes $TMP/NAME.json from the sample after running the code on `b`
variant() {
  "$PY" - "$TMP/$1.json" "$2" <<'PY'
import json, sys
b = json.load(open("sample_bundle.json", encoding="utf-8"))
def item(text_start):
    return next(i for i in b["items"] if i["text"].startswith(text_start))
def meeting(mid):
    return next(m for m in b["meetings"] if m["meeting_id"] == mid)
def entity(key):
    return next(e for e in b["entities"] if e["key"] == key)
def add(section, text, cites=None, **extra):
    it = {"section": section, "text": text, "citations": cites or [{"meeting_id": "sample-iu1"}]}
    it.update(extra)
    b["items"].append(it)
    return it
exec(sys.argv[2])
json.dump(b, open(sys.argv[1], "w", encoding="utf-8"), ensure_ascii=False)
PY
}
# csvfile NAME CONTENT : writes $TMP/NAME.csv
csvfile() { printf '%s' "$2" >"$TMP/$1.csv"; }

# expect CODE PATTERN STREAM NAME -- args...   (STREAM is out or err)
expect() {
  local code="$1" pattern="$2" stream="$3" name="$4"; shift 4
  set +e
  "$PY" "$SCRIPT" "$@" >"$TMP/out" 2>"$TMP/err"
  local got=$?
  set -e
  [[ "$got" == "$code" ]] || { cat "$TMP/out" "$TMP/err" | head -40; fail "$name: exit $got, expected $code"; }
  [[ "$code" == 0 || ! -s "$TMP/out" ]] || { head -5 "$TMP/out"; fail "$name: printed to stdout before failing"; }
  ! grep -q "Traceback" "$TMP/err" || { cat "$TMP/err"; fail "$name: traceback"; }
  grep -qF -- "$pattern" "$TMP/$stream" || { cat "$TMP/out" "$TMP/err" | head -60; fail "$name: '$pattern' not in std$stream"; }
  ok "$name"
}
has() { grep -qF -- "$1" "$TMP/out" || { cat "$TMP/out"; fail "$2: '$1' missing"; }; ok "$2"; }
lacks() { ! grep -qiF -- "$1" "$TMP/out" || { grep -inF -- "$1" "$TMP/out" | head; fail "$2: '$1' present"; }; ok "$2"; }

# ---------------------------------------------------------------- sample, sections, determinism
bash run.sh >"$TMP/run_out"
cmp -s "$TMP/run_out" expected_output.txt || { diff "$TMP/run_out" expected_output.txt | head -20; fail "run.sh output differs from expected_output.txt"; }
ok "run.sh matches expected_output.txt byte for byte"

expect 0 "# Wrenhallow \\(fictional\\) investor update — September 2026" out "sample renders the investor draft" --bundle sample_bundle.json "${BASE[@]}"
cp "$TMP/out" "$TMP/draft"
for h in "## TL;DR" "## Key metrics" "## Highlights" "## Lowlights" "## Product" "## Customers & go-to-market" \
         "## Team & hiring" "## Asks" "## What we said last time vs what happened" "## Before you send (delete this section)"; do
  grep -qxF -- "$h" "$TMP/out" || fail "section missing: $h"
done
ok "all ten sections are present in the draft, in order"
[[ "$(grep -n '^## ' "$TMP/out" | cut -d: -f2- | tr '\n' '|')" == "## TL;DR|## Key metrics|## Highlights|## Lowlights|## Product|## Customers & go-to-market|## Team & hiring|## Asks|## What we said last time vs what happened|## Before you send (delete this section)|" ]] || fail "section order"
ok "section order is fixed"
has "_Draft — not sent." "the draft says it is not sent"
"$PY" "$SCRIPT" --bundle sample_bundle.json "${BASE[@]}" >"$TMP/second"
cmp -s "$TMP/draft" "$TMP/second" || fail "two runs differ"
ok "output is deterministic"
lacks "## Board only" "investor draft has no board-only section"
for n in Quorrelind Ostermoss "Jordan Pike" "Avery Lund" "term sheet" "performance plan"; do
  for args in "--format draft" "--format email" "--format json" "--audience board --format draft" "--audience board --format email" "--audience board --format json"; do
    # shellcheck disable=SC2086
    "$PY" "$SCRIPT" --bundle sample_bundle.json "${BASE[@]}" $args >"$TMP/out"
    case "$n" in "term sheet"|"performance plan") [[ "$args" == *board* ]] && continue;; esac
    ! grep -qiF -- "$n" "$TMP/out" || fail "'$n' leaked with $args"
  done
done
ok "non-public names and held-back items never appear (6 renders x 6 strings)"
expect 0 "Tamberlind Foods renewed for two years" out "public customer name is printed" --bundle sample_bundle.json "${BASE[@]}"
has "Signed a referral partnership with Drovemere Payments" "public partner name is printed"
has "Tomas Reyes joined as a senior backend engineer." "public person is printed"
has "- The carrier-portal beta went live with a mid-market logistics customer;" "non-public customer is printed as its descriptor"
has "- A regional home-goods retailer ended its pilot" "descriptor at the start of a sentence is capitalised"
expect 0 '"sent": false' out "json says sent: false" --bundle sample_bundle.json "${BASE[@]}" --format json
"$PY" - "$TMP/out" "$TMP/draft" <<'PY' || fail "json markdown differs from the draft"
import json, sys
d = json.load(open(sys.argv[1], encoding="utf-8"))
assert d["markdown"] + "\n" == open(sys.argv[2], encoding="utf-8").read()
assert d["status"] == "draft" and d["audience"] == "investors" and d["period"] == "2026-09"
assert d["held_back"] == {"board_only": 1, "personnel": 1}, d["held_back"]
PY
ok "json carries the same markdown as the draft and the held-back counts"
expect 0 "Subject: Wrenhallow \\(fictional\\) investor update — September 2026" out "email format has a subject line" --bundle sample_bundle.json "${BASE[@]}" --format email
lacks "Before you send" "email body has no review section"
lacks "(M1)" "email body has no meeting references"
has "_Confidential — please do not forward._" "email body ends with the confidentiality line"
has "| Gross MRR churn | not available | not available | n/a | — |" "email shows a withheld metric as not available, without a number"
lacks "1.9" "the withheld figure itself never appears"

# ---------------------------------------------------------------- metrics: table, rounding, change
expect 0 "| MRR | \\\$41,250 | \\\$37,600 | +\\\$3,650 (+9.7%) | Stripe, 2026-10-01 |" out "metric row with change and source" --bundle sample_bundle.json "${BASE[@]}"
has "| Runway | 12.7 months | 15.0 months | -2.3 months | Finance sheet \\(sample\\), 2026-09-30 |" "months unit and change"
has "| Churned customers | 1 | 0 | +1 | Stripe, 2026-10-01 |" "no percent change from zero"
has "- **Numbers:** MRR \\\$41,250 (+\\\$3,650, +9.7% MoM) · Paying customers 38 (+3, +8.6% MoM) · Runway 12.7 months (-2.3 months MoM)" "TL;DR numbers come from the headline metrics"
has "| Gross MRR churn | withheld (see Before you send) | not supplied | n/a | — |" "metric without a source is withheld"
has "Gross MRR churn (Sep 2026) not shown: no source \\(sample\\_metrics\\.csv line 10\\)." "withheld metric is explained with its CSV line"
has "MRR for Aug 2026: the last update reported \\\$37,500; Stripe now says \\\$37,600." "restatement against the previous update is flagged"
has "Moved the wrong way: Churned customers, Cash, Net burn and Runway." "metrics moving the wrong way are listed"

variant r_half 'b["metrics"] = [{"metric":"mrr","period":"2026-09","value":40100.5,"source":"S","as_of":"2026-10-01"},{"metric":"mrr","period":"2026-08","value":40000,"source":"S","as_of":"2026-09-01"},{"metric":"customers","period":"2026-09","value":401,"source":"S","as_of":"2026-10-01"},{"metric":"customers","period":"2026-08","value":400,"source":"S","as_of":"2026-09-01"}]'
expect 0 "| MRR | \\\$40,101 | \\\$40,000 | +\\\$101 (+0.3%) | S, 2026-10-01 |" out "currency rounds half up and the change uses the rounded figures" --bundle "$TMP/r_half.json" --as-of "$ASOF"
has "| Paying customers | 401 | 400 | +1 (+0.3%) | S, 2026-10-01 |" "percent change rounds half up (0.25% -> 0.3%)"
variant r_round 'b["metrics"] = [{"metric":"mrr","period":"2026-09","value":100.6,"source":"S","as_of":"2026-10-01"},{"metric":"mrr","period":"2026-08","value":99.4,"source":"S","as_of":"2026-09-01"}]'
expect 0 "| MRR | \\\$101 | \\\$99 | +\\\$2 (+2.0%) | S, 2026-10-01 |" out "change is computed from displayed figures, so the table adds up" --bundle "$TMP/r_round.json" --as-of "$ASOF"
variant r_neg 'b["metrics"] = [{"metric":"net_new_mrr","period":"2026-09","value":-500,"source":"S","as_of":"2026-10-01"},{"metric":"net_new_mrr","period":"2026-08","value":2900,"source":"S","as_of":"2026-09-01"}]'
expect 0 "| Net new MRR | -\\\$500 | \\\$2,900 | -\\\$3,400 | S, 2026-10-01 |" out "negative figures and changes; no percent change when a figure is negative" --bundle "$TMP/r_neg.json" --as-of "$ASOF"
variant r_negprev 'b["metrics"] = [{"metric":"net_new_mrr","period":"2026-09","value":500,"source":"S","as_of":"2026-10-01"},{"metric":"net_new_mrr","period":"2026-08","value":-200,"source":"S","as_of":"2026-09-01"}]'
expect 0 "| Net new MRR | \\\$500 | -\\\$200 | +\\\$700 | S, 2026-10-01 |" out "no percent change from a negative base" --bundle "$TMP/r_negprev.json" --as-of "$ASOF"
variant r_pct 'b["metrics"] += [{"metric":"gross_mrr_churn","period":"2026-09","value":1.94,"source":"S","as_of":"2026-10-01"},{"metric":"gross_mrr_churn","period":"2026-08","value":1.25,"source":"S","as_of":"2026-09-01"}]'
expect 0 "| Gross MRR churn | 1.9% | 1.3% | +0.6 pp | S, 2026-10-01 |" out "percent unit: one decimal, change in percentage points" --bundle "$TMP/r_pct.json" --as-of "$ASOF"
variant r_eur 'b["company"]["currency"] = "EUR"'
expect 0 "| MRR | €41,250 | €37,600 | +€3,650 (+9.7%) | Stripe, 2026-10-01 |" out "EUR symbol" --bundle "$TMP/r_eur.json" --as-of "$ASOF"
variant r_chf 'b["company"]["currency"] = "CHF"'
expect 0 "| MRR | CHF 41,250 | CHF 37,600 | +CHF 3,650 (+9.7%) | Stripe, 2026-10-01 |" out "currency without a symbol uses its code" --bundle "$TMP/r_chf.json" --as-of "$ASOF"
"$PY" - "$TMP/cfg2.json" <<'PY'
import json, sys
c = json.load(open("update.json", encoding="utf-8")); c["currency_decimals"] = 2
json.dump(c, open(sys.argv[1], "w", encoding="utf-8"))
PY
expect 0 "| MRR | \\\$40,100.50 | \\\$40,000.00 | +\\\$100.50 (+0.3%) | S, 2026-10-01 |" out "currency_decimals: 2" --bundle "$TMP/r_half.json" --as-of "$ASOF" --config "$TMP/cfg2.json"
variant r_cur 'b["metrics"][0]["currency"] = "EUR"'
expect 0 "in EUR, not USD; the script does not convert currencies" out "metric in another currency is withheld" --bundle "$TMP/r_cur.json" "${BASE[@]}"
has "| MRR | withheld (see Before you send) | \\\$37,600 | n/a | Aug 2026: Stripe, 2026-09-01 |" "withheld current figure keeps the previous one with its own source"
lacks "41,250" "the withheld figure is not printed"
variant r_count 'b["metrics"][6]["value"] = 38.5'
expect 0 "Paying customers (Sep 2026) not shown: not a whole number" out "fractional count is withheld" --bundle "$TMP/r_count.json" "${BASE[@]}"
variant r_nosrc 'b["metrics"][0]["source"] = "  "'
expect 0 "MRR (Sep 2026) not shown: no source" out "blank source is withheld" --bundle "$TMP/r_nosrc.json" "${BASE[@]}"
has "- **Numbers:** MRR withheld" "TL;DR says withheld, not a number"
variant r_nosrc2 'del b["metrics"][0]["source"]'
expect 0 "MRR (Sep 2026) not shown: no source" out "missing source key is withheld" --bundle "$TMP/r_nosrc2.json" "${BASE[@]}"
variant r_noasof 'del b["metrics"][0]["as_of"]'
expect 0 "MRR (Sep 2026) not shown: no as-of date" out "missing as-of date is withheld" --bundle "$TMP/r_noasof.json" "${BASE[@]}"
variant r_future 'b["metrics"][0]["as_of"] = "2026-10-03"'
expect 0 "MRR (Sep 2026) not shown: as-of date 2026-10-03 is after today" out "future as-of date is withheld" --bundle "$TMP/r_future.json" "${BASE[@]}"
variant r_old 'b["metrics"][0]["as_of"] = "2026-08-31"'
expect 0 "MRR (Sep 2026) not shown: as-of date 2026-08-31 is before Sep 2026 began" out "as-of before the period is withheld" --bundle "$TMP/r_old.json" "${BASE[@]}"
variant r_stale 'b["metrics"][0]["as_of"] = "2026-09-15"'
expect 0 "MRR is as of 2026-09-15, before Sep 2026 ended." out "stock metric dated mid-period is flagged" --bundle "$TMP/r_stale.json" "${BASE[@]}"
variant r_null 'b["metrics"][0]["value"] = None'
expect 0 "blank value \\(treated as missing, not zero\\)" out "null value is missing, not zero" --bundle "$TMP/r_null.json" "${BASE[@]}"
lacks "| MRR | \\\$0" "null is never shown as zero"
variant r_conf 'b["metrics"].append({"metric":"mrr","period":"2026-09","value":41000,"source":"Finance sheet","as_of":"2026-10-01"})'
expect 0 "| MRR | withheld: sources disagree |" out "conflicting sources withhold the figure" --bundle "$TMP/r_conf.json" "${BASE[@]}"
has 'MRR (Sep 2026): sources disagree: \$41,250 from Stripe; \$41,000 from Finance sheet. Neither is shown' "conflict lists both sources"
variant r_same 'b["metrics"].append({"metric":"mrr","period":"2026-09","value":41250.2,"source":"Finance sheet","as_of":"2026-09-30"})'
expect 0 "| MRR | \\\$41,250 | \\\$37,600 | +\\\$3,650 (+9.7%) | Stripe; Finance sheet, 2026-10-01; Aug 2026: Stripe, 2026-09-01 |" out "agreeing sources are merged" --bundle "$TMP/r_same.json" "${BASE[@]}"
variant r_prevfb 'b["metrics"] = [m for m in b["metrics"] if not (m["metric"] == "mrr" and m["period"] == "2026-08")]'
expect 0 "| MRR | \\\$41,250 | \\\$37,500 | +\\\$3,750 (+10.0%) | Stripe, 2026-10-01; Aug 2026: previous update, 2026-09-03 |" out "previous figure falls back to the previous update, with that source" --bundle "$TMP/r_prevfb.json" "${BASE[@]}"
variant r_jump 'b["metrics"][0]["value"] = 412500'
expect 0 "MRR changed 10x or more (\\\$37,600 to \\\$412,500); check it once before using it." out "10x jump is flagged" --bundle "$TMP/r_jump.json" "${BASE[@]}"
csvfile norunway "metric,period,value,source,as_of
cash,2026-09,1840000,Finance sheet,2026-09-30
"
expect 0 "- **Numbers:** MRR \\\$41,250 (+\\\$3,650, +9.7% MoM) · Paying customers 38 (+3, +8.6% MoM) · Runway not supplied" out "missing headline metric is said plainly" --bundle sample_bundle.json --metrics-csv "$TMP/norunway.csv" --as-of "$ASOF"
has "the last update reported Runway but this draft has no figure for it" "metric reported last time but missing now is flagged"
has "Metrics — no Runway figure for Sep 2026." "missing headline metric is a check"
csvfile otherp "metric,period,value,source,as_of
cash,2026-07,1,Finance sheet,2026-07-31
"
expect 0 "Metrics — 1 metrics row for other periods ignored." out "rows for other periods are counted, not used" --bundle sample_bundle.json --metrics-csv "$TMP/otherp.csv" --as-of "$ASOF"
csvfile bom $'\xef\xbb\xbfmetric,period,value,source,as_of,currency\r\ncash,2026-09,1840000,Bank export,2026-09-30,USD\r\n\r\n'
expect 0 "| Cash | \\\$1,840,000 | not supplied | n/a | Bank export, 2026-09-30 |" out "CSV with BOM, CRLF, a currency column and a blank line" --bundle sample_bundle.json --metrics-csv "$TMP/bom.csv" --as-of "$ASOF"
variant r_q 'b["period"] = "2026-Q3"; b["metrics"] = [{"metric":"mrr","period":"2026-09","value":41250,"source":"Stripe","as_of":"2026-10-01"},{"metric":"mrr","period":"2026-06","value":30000,"source":"Stripe","as_of":"2026-07-01"},{"metric":"net_new_mrr","period":"2026-09","value":3650,"source":"Stripe","as_of":"2026-10-01"},{"metric":"net_new_mrr","period":"2026-Q3","value":11250,"source":"Stripe","as_of":"2026-10-01"}]; b["previous_update"] = None; b["commitment_reviews"] = []'
expect 0 "# Wrenhallow \\(fictional\\) investor update — Q3 2026" out "quarterly update" --bundle "$TMP/r_q.json" --as-of "$ASOF"
has "| MRR | \\\$41,250 | \\\$30,000 | +\\\$11,250 (+37.5%) | Stripe, 2026-10-01 |" "stock metric: the quarter's last month counts for the quarter"
has "| Net new MRR | \\\$11,250 | not supplied | n/a | Stripe, 2026-10-01 |" "flow metric: only a quarter figure counts, never a month"
has "| Metric | Q3 2026 | Q2 2026 | Change (QoQ) | Source, as of |" "quarter columns and QoQ"
variant r_mp 'b["items"][0]["text"] = "MRR {metric:mrr}, was {metric:mrr.previous}, change {metric:mrr.change}."'
expect 0 "- MRR \\\$41,250, was \\\$37,600, change +\\\$3,650 (+9.7%). (metrics)" out "metric placeholders: current, previous, change" --bundle "$TMP/r_mp.json" "${BASE[@]}"
expect 0 "uses Gross MRR churn (this period), which is withheld or not supplied" out "placeholder for a withheld metric withholds the text" --bundle "$(variant r_mw 'b["items"][0]["text"] = "Churn was {metric:gross_mrr_churn}."'; echo "$TMP/r_mw.json")" "${BASE[@]}"
lacks "Churn was" "the text is not printed"
variant r_mu 'b["items"][0]["text"] = "Value {metric:nosuchmetric}."'
expect 0 "a {metric:...} placeholder names no defined metric" out "unknown metric placeholder withholds the text" --bundle "$TMP/r_mu.json" "${BASE[@]}"
variant r_custom 'b["metric_definitions"] = {"orders": {"label": "Orders", "unit": "count", "kind": "flow", "better": "up"}}; b["metrics"] += [{"metric":"orders","period":"2026-09","value":1200,"source":"Shopify","as_of":"2026-10-01"}]'
expect 0 "| Orders | 1,200 | not supplied | n/a | Shopify, 2026-10-01 |" out "custom metric definition (DTC example)" --bundle "$TMP/r_custom.json" "${BASE[@]}"

# ---------------------------------------------------------------- names (pattern-based)
for payload in "Quorrelind asked for an API." "QUORRELIND asked." $'Qu\u00f6rrelind asked.' $'Quor\u200brelind asked.' "Q u o r r e l i n d asked." \
               $'Qu\u043errelind asked.' $'\uff31\uff55\uff4f\uff52\uff52\uff45\uff4c\uff49\uff4e\uff44 asked.' "Quorrelind's team asked." "Quorre-lind asked." "quorrelind logistics asked." \
               "See quorrelind.example for details." "Ostermoss asked."; do
  variant n_direct "add('customers', '''$payload''')"
  expect 0 "writes the name of non-public entities[" out "direct name is withheld: $payload" --bundle "$TMP/n_direct.json" "${BASE[@]}"
done
has "(shown as “a regional home-goods retailer”)" "the reason names the descriptor, never the name"
variant n_suffix 'entity("quorrelind")["name"] = "Quorrelind Logistics Inc"; entity("quorrelind")["aliases"] = []; add("customers", "Quorrelind Logistics renewed.")'
expect 0 "writes the name of non-public entities[1]" out "name without its legal suffix is still matched" --bundle "$TMP/n_suffix.json" "${BASE[@]}"
variant n_first 'entity("quorrelind")["aliases"] = []; entity("jordan")["aliases"] = []; add("customers", "Quorrelind asked again."); add("team", "Pike is our pick.")'
expect 0 "Customers & go-to-market #5 (items[18]): writes the name of non-public entities[1]" out "first word of an organisation name is matched without an alias" --bundle "$TMP/n_first.json" "${BASE[@]}"
has "Team & hiring #4 (items[19]): writes the name of non-public entities[5]" "any word of a person's name is matched without an alias"
variant n_person 'add("team", "Jordan Pike is our top VP Sales finalist.")'
expect 0 "writes the name of non-public entities[5] (shown as “a VP Sales finalist”) directly; use its {person:...} placeholder" out "non-public person named directly is withheld" --bundle "$TMP/n_person.json" "${BASE[@]}"
variant n_ph 'add("team", "{person:jordan} is in the final round."); add("team", "{org:jordan} x."); add("team", "{person:quorrelind} x."); add("team", "{org:nosuchkey} x.")'
expect 0 "- A VP Sales finalist is in the final round." out "person placeholder prints the descriptor" --bundle "$TMP/n_ph.json" "${BASE[@]}"
has "a {org:...} placeholder names no organisation in \$.entities" "org placeholder pointing at a person is refused"
has "a {person:...} placeholder names no person in \$.entities" "person placeholder pointing at an organisation is refused"
lacks "nosuchkey" "placeholder keys are never echoed"
variant n_quote 'it = item("{org:tamberlind} renewed"); it["quote"]["text"] = "Quorrelind told us the same."; meeting("sample-iu1")["notes_text"] += " Quorrelind told us the same."'
expect 0 "quote left out: writes the name of non-public" out "name inside a quote drops the quote" --bundle "$TMP/n_quote.json" "${BASE[@]}"
variant n_attr 'it = item("{org:tamberlind} renewed"); it["quote"]["attribution"] = "Ops lead at Ostermoss"'
expect 0 "quote left out: attribution writes the name of non-public" out "name inside a quote attribution drops the quote" --bundle "$TMP/n_attr.json" "${BASE[@]}"
variant n_commit 'b["previous_update"]["commitments"][3]["text"] = "Convert the Ostermoss pilot."'
expect 0 "| (withheld: see Before you send) | Missed |" out "name in last update's commitment text is withheld" --bundle "$TMP/n_commit.json" "${BASE[@]}"
has "Commitment 4 (previous_update.commitments[3]): text withheld: writes the name" "and explained"
variant n_note 'b["commitment_reviews"][3]["note"] = "Ostermoss walked away."'
expect 0 "Commitment 4 (previous_update.commitments[3]): note withheld: writes the name" out "name in a review note is withheld" --bundle "$TMP/n_note.json" "${BASE[@]}"
expect 0 "M2 · 2026-09-08 · customer call · [(title withheld: it names a non-public entity)](https://notes.granola.ai/d/sample-iu2)" out "meeting title naming a non-public customer is masked" --bundle sample_bundle.json "${BASE[@]}"
has "M4 · 2026-09-15 · hiring meeting · [(title withheld: it names a non-public entity)]" "meeting title naming a non-public person is masked"
variant n_url 'meeting("sample-iu8")["url"] = "https://notes.example/quorrelind-retro"'
expect 0 "- M8 · 2026-09-29 · team meeting · Engineering retro" out "link whose URL names a non-public entity is dropped" --bundle "$TMP/n_url.json" "${BASE[@]}"
lacks "quorrelind" "and the URL is not printed"
variant n_src 'b["metrics"][0]["source"] = "Stripe (Quorrelind account)"'
expect 0 "\\(source label withheld\\: it names a non-public entity\\), 2026-10-01" out "metric source label naming a non-public entity is masked" --bundle "$TMP/n_src.json" "${BASE[@]}"
variant n_detail 'b["sources"]["billing"]["detail"] = "Ostermoss sub-account"; b["sources"]["meetings"]["provider"] = "Granola for Ostermoss"'
expect 0 "Billing: Stripe (ok) — (detail withheld: it names a non-public entity)" out "source detail naming a non-public entity is masked" --bundle "$TMP/n_detail.json" "${BASE[@]}"
has "Meeting notes: (provider withheld: it names a non-public entity) (ok)" "source provider naming a non-public entity is masked"
variant n_doc 'item("A referral for")["citations"] = [{"source": "document", "label": "Ostermoss pricing memo", "url": "https://docs.example/memo"}]'
expect 0 "- D1 · [(document label withheld: it names a non-public entity)](https://docs.example/memo)" out "document label naming a non-public entity is masked" --bundle "$TMP/n_doc.json" "${BASE[@]}"
variant n_label 'b["metric_definitions"] = {"mrr": {"label": "Quorrelind MRR", "unit": "currency", "kind": "stock", "better": "up"}}'
expect 3 "refusing to print: line" err "a non-public name in fixed text (metric label) refuses to print (exit 3)" --bundle "$TMP/n_label.json" "${BASE[@]}"
for fmt in email json; do
  expect 3 "refusing to print" err "exit 3 in $fmt format too" --bundle "$TMP/n_label.json" "${BASE[@]}" --format "$fmt"
done
variant n_company 'b["company"]["name"] = "Ostermoss"'
expect 2 "\$.company.name: contains the name or an alias of a non-public entity" err "company name equal to a non-public name exits 2" --bundle "$TMP/n_company.json" "${BASE[@]}"
variant n_desc 'entity("quorrelind")["descriptor"] = "the Quorrelind account"'
expect 2 "descriptor: contains the name or an alias of non-public entity" err "descriptor containing the name exits 2" --bundle "$TMP/n_desc.json" "${BASE[@]}"
variant n_pub 'entity("tamberlind")["name"] = "Ostermoss Tamberlind Foods"'
expect 2 "make the names distinct" err "public name containing a non-public name exits 2" --bundle "$TMP/n_pub.json" "${BASE[@]}"
variant n_nodesc 'del entity("quorrelind")["descriptor"]'
expect 2 "descriptor: is required when public_ok is false" err "non-public entity needs a descriptor" --bundle "$TMP/n_nodesc.json" "${BASE[@]}"
variant n_boolstr 'entity("quorrelind")["public_ok"] = "false"'
expect 2 "public_ok: must be true or false" err "public_ok must be a real boolean" --bundle "$TMP/n_boolstr.json" "${BASE[@]}"
variant n_dupkey 'b["entities"].append(dict(entity("tomas")))'
expect 2 "duplicate key" err "duplicate entity key exits 2" --bundle "$TMP/n_dupkey.json" "${BASE[@]}"

# ---------------------------------------------------------------- confidentiality
for tag in board_only legal fundraising_terms personnel; do
  variant c_tag "add('product', 'Signed item for ${tag//_/ }.', confidential='$tag')"
  expect 0 "Held back as confidential" out "tag $tag is held back from investors" --bundle "$TMP/c_tag.json" "${BASE[@]}"
  lacks "Signed item for" "tag $tag: text not in the investor draft"
  expect 0 "Signed item for ${tag//_/ }." out "tag $tag appears in the board version" --bundle "$TMP/c_tag.json" "${BASE[@]}" --audience board
done
expect 0 "- **Personnel / HR:** Signed item for personnel. (M1)" out "board section labels the tag" --bundle "$TMP/c_tag.json" "${BASE[@]}" --audience board
has "## Board only — do not forward" "board version has the board-only section"
expect 0 "Held back as confidential: 2 items (1 board only, 1 personnel / HR)." out "held-back count, by tag, without the text" --bundle sample_bundle.json "${BASE[@]}"
variant c_meet 'meeting("sample-iu8")["confidential"] = "legal"'
expect 0 "5 items (1 board only, 3 legal, 1 personnel / HR)" out "items citing a meeting tagged legal inherit the tag" --bundle "$TMP/c_meet.json" "${BASE[@]}"
lacks "Rebuilt the dispatch board" "and are left out of the investor draft"
for phrase in "We received a term sheet from a fund." "Our valuation came up in the call." "A lawsuit was threatened by a vendor." \
              "One engineer is on a performance improvement plan." "Please do not share this with anyone." "We discussed severance terms." \
              "Two people were laid off." "We met with our attorneys about the contract."; do
  variant c_scan "add('product', '''$phrase''')"
  expect 0 "may be confidential (" out "untagged confidential wording is withheld: $phrase" --bundle "$TMP/c_scan.json" "${BASE[@]}"
done
variant c_clear 'add("customers", "We settled the billing dispute with a vendor.", cleared_by_founder=True)'
expect 0 "- We settled the billing dispute with a vendor. (M1)" out "an item the founder cleared is shown" --bundle "$TMP/c_clear.json" "${BASE[@]}"
has "shown because the founder cleared it although it matched a confidential term" "and the clearance is listed for review"
variant c_clear2 'add("customers", "Quorrelind settled.", cleared_by_founder=True)'
expect 0 "writes the name of non-public" out "founder clearance never unlocks a non-public name" --bundle "$TMP/c_clear2.json" "${BASE[@]}"
expect 0 "Subject: Wrenhallow \\(fictional\\) investor update" out "investor email has no board section" --bundle sample_bundle.json "${BASE[@]}" --format email
lacks "Board only" "no board-only section in the investor email"

# ---------------------------------------------------------------- self-QA regressions (leak paths found in review)
variant x_quote 'item("{org:tamberlind} renewed")["quote"]["meeting_id"] = "sample-iu7"; meeting("sample-iu7")["notes_text"] = "The dock calendar is the first thing my team opens every morning."'
expect 0 "Held back as confidential: 3 items" out "an item whose quote comes from a board meeting inherits board-only" --bundle "$TMP/x_quote.json" "${BASE[@]}"
lacks "first thing my team opens" "and the quote never reaches the investor draft"
variant x_stated 'item("We will open")["founder_stated"]["meeting_id"] = "sample-iu7"; meeting("sample-iu7")["notes_text"] = "We will open the carrier portal to all customers in November."'
expect 0 "Held back as confidential: 3 items" out "an item whose founder statement comes from a board meeting inherits board-only" --bundle "$TMP/x_stated.json" "${BASE[@]}"
lacks "We will open the carrier portal" "and is left out of the investor draft"
variant x_review 'b["commitment_reviews"][3]["citations"] = [{"meeting_id": "sample-iu7"}]; b["commitment_reviews"][3]["note"] = "Discussed at the board prep."'
expect 0 "Commitment 4 (previous_update.commitments[3]): note and references left out: the review cites a meeting tagged confidential." out "a review citing a board meeting keeps its note out of the investor draft" --bundle "$TMP/x_review.json" "${BASE[@]}"
lacks "Board prep" "and the board meeting's title is not listed under Sources cited"
lacks "Discussed at the board prep" "and the note is not printed"
expect 0 "Discussed at the board prep. (M7)" out "the board version shows the note" --bundle "$TMP/x_review.json" "${BASE[@]}" --audience board
csvfile ostermoss_metrics "metric,period,value,source,as_of
cash,2026-09,1840000,,2026-09-30
"
expect 0 "Cash (Sep 2026) not shown: no source \\(a row whose file name is withheld\\)." out "a CSV file name that names a non-public customer is not printed" --bundle sample_bundle.json --metrics-csv "$TMP/ostermoss_metrics.csv" --as-of "$ASOF"
lacks "ostermoss" "the file name does not appear"
for d in "a customer with 500 stores" "a customer we are suing" "a customer that will expand"; do
  variant x_desc "entity('quorrelind')['descriptor'] = \"$d\""
  expect 2 "descriptor: must not contain a figure, a confidential term or forward-looking wording" err "descriptor is checked: $d" --bundle "$TMP/x_desc.json" "${BASE[@]}"
done
for src in unknown TBD "n/a" "-" "?" "Source"; do
  variant x_src "b['metrics'][0]['source'] = \"$src\""
  expect 0 "MRR (Sep 2026) not shown: source is a placeholder, not a source" out "placeholder source '$src' is withheld" --bundle "$TMP/x_src.json" "${BASE[@]}"
done
variant x_half 'add("customers", "Half of our customers asked for SSO."); add("product", "We expect a strong quarter for the portal.")'
expect 0 "Customers & go-to-market #5 (items[18]): has a figure that is not from the metrics data" out "'half of our customers' is a figure" --bundle "$TMP/x_half.json" "${BASE[@]}"
has "Product #4 (items[19]): has forward-looking wording without a founder statement" "'we expect' is forward-looking"

variant x_month 'b["entities"].append({"key": "augh", "name": "August Health", "kind": "customer", "public_ok": False, "descriptor": "a health-care customer", "aliases": []}); add("customers", "August Health signed a pilot."); add("product", "Shipped the Outlook calendar sync.")'
expect 0 "MRR reached \\\$41,250, against \\\$37,600 in August." out "a non-public name starting with a month does not block the month in other text" --bundle "$TMP/x_month.json" "${BASE[@]}"
has "writes the name of non-public entities[7]" "but the full name is still withheld"
has "- Shipped the Outlook calendar sync. (M1)" "'Outlook' (the product) is not forward-looking wording"

# ---------------------------------------------------------------- figures in agent-written text
for phrase in 'Pipeline grew to $310k.' 'We closed 14 deals.' 'Churn fell 12%.' 'Usage grew 3x.' 'Bookings hit 1.2M.' 'We added two new customers.' \
              'Revenue doubled.' 'We now serve 2000 customers.' 'A million rows a day.' 'Saved €5 per order.' 'NPS is 62.' 'Usage up \u0663 times.' \
              'Thirty logos signed.' 'We hit 40k MRR.'; do
  variant f_num "add('product', '''$phrase''')"
  expect 0 "has a figure that is not from the metrics data" out "figure in text is withheld: $phrase" --bundle "$TMP/f_num.json" "${BASE[@]}"
done
variant f_ok 'add("product", "SOC 2 Type II audit started in Q3 for our B2B and 3PL customers; v2.1 shipped on Oct 3 and 2026-09-30, per ISO 27001.")'
expect 0 "- SOC 2 Type II audit started in Q3 for our B2B and 3PL customers; v2\\.1 shipped on Oct 3 and 2026-09-30, per ISO 27001. (M1)" out "years, dates, quarters, versions, standards and 3PL/B2B are not figures" --bundle "$TMP/f_ok.json" "${BASE[@]}"

# ---------------------------------------------------------------- forward-looking wording
for phrase in "We will launch analytics." "We'll double down on 3PLs." "We plan to hire a VP Sales." "Enterprise SSO is on track." \
              "The portal opens next quarter." "Partners are expected to sign." "We are going to raise prices."; do
  variant w_fw "add('product', '''$phrase''')"
  expect 0 "has forward-looking wording without a founder statement" out "forward-looking text is withheld: $phrase" --bundle "$TMP/w_fw.json" "${BASE[@]}"
done
expect 0 "Product #2 (items[6]): forward-looking wording kept because the founder said it (M3)." out "founder statement found in the meeting notes keeps the item" --bundle sample_bundle.json "${BASE[@]}"
has "- We will open the carrier portal to all customers in November. (M3)" "and the item is printed"
variant w_bad 'item("We will open")["founder_stated"]["excerpt"] = "We will open the portal to everyone in October."'
expect 0 "its founder statement is not whole sentences from M3's notes or transcript text (not found in the meeting text)" out "founder statement not found in the notes withholds the item" --bundle "$TMP/w_bad.json" "${BASE[@]}"
variant w_user 'add("product", "We will add SSO.", founder_stated={"source": "user", "excerpt": "We will add SSO."})'
expect 0 "its founder statement was typed in chat (source: user), which the script can't check against meeting text" out "founder statement typed in chat no longer unlocks forward-looking wording (round 5)" --bundle "$TMP/w_user.json" "${BASE[@]}"
lacks "We will add SSO." "and the item is not printed"
variant w_unread 'add("product", "We will add SSO.", founder_stated={"meeting_id": "sample-iu9", "excerpt": "x"})'
expect 0 "its founder statement cites M9 whose notes were not read" out "founder statement citing unread notes withholds the item" --bundle "$TMP/w_unread.json" "${BASE[@]}"
variant w_both 'add("product", "x", founder_stated={"source": "user", "meeting_id": "sample-iu3", "excerpt": "x"})'
expect 2 "needs exactly one of meeting_id or source" err "founder_stated with both sources exits 2" --bundle "$TMP/w_both.json" "${BASE[@]}"
variant w_q 'it = item("{org:tamberlind} renewed"); it["quote"]["text"] = "We will roll this out everywhere."; meeting("sample-iu1")["notes_text"] += " We will roll this out everywhere."'
expect 0 "quote left out: has forward-looking wording" out "forward-looking customer quote is dropped" --bundle "$TMP/w_q.json" "${BASE[@]}"

# ---------------------------------------------------------------- quotes
expect 0 "Highlights #3 (items[2]): quote left out: no permission to quote (permission is false)." out "quote without permission is left out" --bundle sample_bundle.json "${BASE[@]}"
lacks "We stopped chasing carriers" "and its text is not printed"
has '  > “The dock calendar is the first thing my team opens every morning.” — Head of Logistics, Tamberlind Foods (M1)' "quote with permission, found in the notes, is printed with its meeting"
# Round 10 (helper 1.6.0, QA round 9 SH9-1): a negation before an unlisted abbreviation ("asst. mgr.", "SVP.", "Ambass.")
# does not end the turn, so the customer quote after "Head of Logistics:" is left out; twins with a clear sentence end
# before the label print it.
for atk in "Not even our asst. mgr. " "Nobody except the SVP. " "No one but Ambass. "; do
  variant sh10_atk 'm = meeting("sample-iu1"); m["notes_text"] = m["notes_text"].replace("Head of Logistics: The dock", "'"$atk"'Head of Logistics: The dock")'
  expect 0 "(not a complete sentence in the meeting text)" out "SH9-1 (r10): '$atk' before the label: the quote is left out" --bundle "$TMP/sh10_atk.json" "${BASE[@]}"
  lacks "first thing my team opens" "SH9-1 (r10): '$atk': the quote text is not printed"
done
for twin in "Sounds good. " "Nobody except the SVP agreed. "; do
  variant sh10_safe 'm = meeting("sample-iu1"); m["notes_text"] = m["notes_text"].replace("Head of Logistics: The dock", "'"$twin"'Head of Logistics: The dock")'
  expect 0 "“The dock calendar is the first thing my team opens every morning.” — Head of Logistics, Tamberlind Foods (M1)" out "SH9-1 (r10) twin: '$twin' then the label prints the quote" --bundle "$TMP/sh10_safe.json" "${BASE[@]}"
done
variant q_nv 'item("{org:tamberlind} renewed")["quote"]["text"] = "The dock calendar is the only thing my team opens."'
expect 0 "quote left out: it is not whole sentences from one speaker's turn in M1's notes or transcript text (not found in the meeting text)" out "a quote that is not verbatim is dropped" --bundle "$TMP/q_nv.json" "${BASE[@]}"
variant q_nonotes 'del meeting("sample-iu1")["notes_text"]'
expect 0 "quote not checked against the meeting (M1's notes text is not in the bundle)." out "unchecked quote is printed with a review note" --bundle "$TMP/q_nonotes.json" "${BASE[@]}"
variant q_unread 'meeting("sample-iu1")["notes_read"] = False'
expect 0 "Highlights #2 (items[1]): cites M1 whose notes were not read." out "item citing unread notes is withheld" --bundle "$TMP/q_unread.json" "${BASE[@]}"
variant q_noperm 'del item("{org:tamberlind} renewed")["quote"]["permission"]'
expect 2 "quote.permission: is required" err "quote without a permission flag exits 2" --bundle "$TMP/q_noperm.json" "${BASE[@]}"
variant q_email 'it = item("{org:tamberlind} renewed"); it["quote"]["text"] = "Write to ops@tamberlind.example any time."; meeting("sample-iu1")["notes_text"] += " Write to ops@tamberlind.example any time."'
expect 0 "“Write to \\[email removed\\] any time.”" out "email address in a quote is removed (pattern-based)" --bundle "$TMP/q_email.json" "${BASE[@]}"

# ---------------------------------------------------------------- last update's commitments
expect 0 "| Reach \\\$40,000 in MRR by the end of September. | Done | MRR \\\$41,250 vs a target of at least \\\$40,000 (met) (metrics) |" out "commitment with a metric target is checked" --bundle sample_bundle.json "${BASE[@]}"
has "| Keep net burn at or below \\\$130,000. | Missed | Net burn \\\$145,000 vs a target of at most \\\$130,000 (not met) (metrics) |" "a 'done' status that contradicts the figure becomes Missed"
has "Commitment 5 (previous_update.commitments[4]): status changed from Done to Missed because Net burn is \\\$145,000 against a target of at most \\\$130,000." "the change is explained"
has "| Publish the security whitepaper. | Not reviewed | — |" "unreviewed commitment is shown as Not reviewed"
has "Commitment 6 (previous_update.commitments[5]): not reviewed; say what happened." "and flagged"
variant k_met 'b["commitment_reviews"][0]["status"] = "missed"'
expect 0 "status changed from Missed to Done because MRR is \\\$41,250" out "a 'missed' status that the figure contradicts becomes Done" --bundle "$TMP/k_met.json" "${BASE[@]}"
variant k_changed 'b["commitment_reviews"][4]["status"] = "changed"; b["commitment_reviews"][4]["note"] = "We chose to hire early."'
expect 0 "| Keep net burn at or below \\\$130,000. | Changed | Net burn \\\$145,000 vs a target of at most \\\$130,000 (not met); We chose to hire early. (metrics) |" out "Changed/Blocked/Dropped are kept, with the figure beside them" --bundle "$TMP/k_changed.json" "${BASE[@]}"
csvfile noburn "metric,period,value,source,as_of
cash,2026-09,1840000,Finance sheet,2026-09-30
"
expect 0 "Net burn target of at most \\\$130,000: can't check, this period's figure is not supplied" out "target on a missing metric can't be checked" --bundle sample_bundle.json --metrics-csv "$TMP/noburn.csv" --as-of "$ASOF"
variant k_none 'b["previous_update"] = None; b["commitment_reviews"] = []'
expect 0 "No previous update was supplied, so there is nothing to compare." out "no previous update is said plainly" --bundle "$TMP/k_none.json" "${BASE[@]}"
variant k_period 'b["previous_update"]["period"] = "2026-07"'
expect 0 "The previous update covered Jul 2026, not Aug 2026." out "previous update for another period is flagged" --bundle "$TMP/k_period.json" "${BASE[@]}"
variant k_unknown 'b["commitment_reviews"].append({"commitment_id": "c9", "status": "done", "citations": [{"source": "user", "detail": "x"}]})'
expect 2 "'c9' is not a commitment in \$.previous_update" err "review of an unknown commitment exits 2" --bundle "$TMP/k_unknown.json" "${BASE[@]}"
variant k_dup 'b["commitment_reviews"].append(dict(b["commitment_reviews"][0]))'
expect 2 "is reviewed twice" err "duplicate review exits 2" --bundle "$TMP/k_dup.json" "${BASE[@]}"
variant k_nocite 'b["commitment_reviews"][0]["citations"] = []'
expect 2 "needs at least one citation" err "review without evidence exits 2" --bundle "$TMP/k_nocite.json" "${BASE[@]}"
variant k_nopu 'b["previous_update"] = None'
expect 2 "is not a commitment in \$.previous_update" err "reviews without a previous update exit 2" --bundle "$TMP/k_nopu.json" "${BASE[@]}"

# ---------------------------------------------------------------- lowlights are never omitted
variant l_none 'b["items"] = [i for i in b["items"] if i["section"] != "lowlight"]'
expect 0 "- None recorded. An update with no lowlights is rarely believed; add at least one before sending." out "empty Lowlights section is still printed" --bundle "$TMP/l_none.json" "${BASE[@]}"
has "- **Lowlight:** none recorded; add one before sending" "TL;DR says no lowlight"
has "Lowlights is empty. Say what went wrong this period. These metrics moved the wrong way:" "the check points at the metrics that moved the wrong way"
expect 0 "## Lowlights" out "Lowlights is in the email body too" --bundle "$TMP/l_none.json" "${BASE[@]}" --format email
variant l_withheld 'b["items"] = [i for i in b["items"] if i["section"] != "lowlight"]; add("lowlight", "We will miss Q4.")'
expect 0 "- None recorded." out "Lowlights stays when every lowlight was withheld" --bundle "$TMP/l_withheld.json" "${BASE[@]}"
variant l_empty 'b["items"] = []; b["meetings"] = []; b["metrics"] = []; b["previous_update"] = None; b["commitment_reviews"] = []; b["entities"] = []'
expect 0 "## Lowlights" out "an empty bundle still renders every section" --bundle "$TMP/l_empty.json" --as-of "$ASOF"
has "Meetings: 0 in the bundle, 0 in Sep 2026; notes read for 0" "and says nothing was read"

# ---------------------------------------------------------------- coverage and banners
variant s_denied 'b["sources"]["meetings"]["status"] = "denied"'
expect 0 "> **Incomplete:** meeting notes could not be read (Granola: denied). Highlights may be missing." out "denied meeting source gives an Incomplete banner" --bundle "$TMP/s_denied.json" "${BASE[@]}"
expect 0 "> **Period not over:** Oct 2026 ends 2026-10-31; figures and highlights are partial." out "period not over is flagged" --bundle "$(variant s_open 'b["period"] = "2026-10"'; echo "$TMP/s_open.json")" "${BASE[@]}"
expect 0 "Granola's free plan may not return older notes" out "plan-window note when the period is more than 30 days back" --bundle sample_bundle.json "${BASE[@]}"
expect 0 "transcripts: 3 read, 1 denied, 1 unavailable." out "transcript coverage is counted" --bundle sample_bundle.json "${BASE[@]}"
has "a missing detail does not mean it was not discussed" "denied transcripts carry the not-discussed caveat"
variant s_out 'meeting("sample-iu8")["date"] = "2026-08-20"'
expect 0 "Product #1 (items[5]) cites M1 (2026-08-20), outside Sep 2026." out "citing a meeting outside the period is flagged" --bundle "$TMP/s_out.json" "${BASE[@]}"

# ---------------------------------------------------------------- validation: exit 2, nothing on stdout, no traceback
printf '{"schema_version": 1,' >"$TMP/bad.json"
expect 2 "is not valid JSON (line 1" err "malformed JSON exits 2" --bundle "$TMP/bad.json" "${BASE[@]}"
printf '{"schema_version": NaN}' >"$TMP/nan.json"
expect 2 "is not valid JSON (NaN is not a valid JSON number)" err "NaN exits 2" --bundle "$TMP/nan.json" "${BASE[@]}"
printf '{"schema_version": Infinity}' >"$TMP/inf.json"
expect 2 "Infinity is not a valid JSON number" err "Infinity exits 2" --bundle "$TMP/inf.json" "${BASE[@]}"
variant v_sur 'b["items"][0]["text"] = "SURR x"'; sed -i 's/SURR/\\ud800/' "$TMP/v_sur.json"
expect 2 "unpaired surrogate" err "lone surrogate exits 2" --bundle "$TMP/v_sur.json" "${BASE[@]}"
printf '\xff\xfe{}' >"$TMP/latin.json"
expect 2 "is not UTF-8 text" err "non-UTF-8 bundle exits 2" --bundle "$TMP/latin.json" "${BASE[@]}"
expect 2 "file not found" err "missing bundle exits 2" --bundle "$TMP/nope.json" "${BASE[@]}"
expect 2 "is not a readable file (folder)" err "folder as bundle exits 2" --bundle "$TMP" "${BASE[@]}"
: >"$TMP/empty.json"
expect 2 "is not valid JSON" err "empty bundle file exits 2" --bundle "$TMP/empty.json" "${BASE[@]}"
"$PY" -c 'print("[" * 100000 + "]" * 100000)' >"$TMP/deep.json"
expect 2 "bundle" err "deeply nested JSON exits 2" --bundle "$TMP/deep.json" "${BASE[@]}"
variant v_key 'b["itmes"] = []'
expect 2 "unknown key(s): itmes" err "typo in a top-level key exits 2" --bundle "$TMP/v_key.json" "${BASE[@]}"
variant v_ikey 'b["items"][14]["confidental"] = b["items"][14].pop("confidential")'
expect 2 "unknown key(s): confidental" err "typo in an item's confidential key exits 2 (never silently public)" --bundle "$TMP/v_ikey.json" "${BASE[@]}"
variant v_tag 'b["items"][14]["confidential"] = "secret"'
expect 2 "confidential: must be one of board_only, legal, fundraising_terms, personnel" err "unknown tag exits 2" --bundle "$TMP/v_tag.json" "${BASE[@]}"
for p in 2026-9 2026-13 2026-Q5 2026-W40 "\uff12\uff10\uff12\uff16-09" 1900-01 3000-01 "2026-09 " 202609; do
  variant v_per "b['period'] = '''$p'''"
  expect 2 "\$.period:" err "bad period '$p' exits 2" --bundle "$TMP/v_per.json" "${BASE[@]}"
done
for d in 2026-02-30 20260930 2026-W40-1 "\u0662\u0660\u0662\u0666-\u0660\u0669-\u0663\u0660" 2026-09-30T24:00:00Z 2026-09-30T10:00:00+14:01 2026-09-30T10:00:00+00:60 2026-09-30T10:00 1899-12-31 "2026-9-3"; do
  variant v_date "meeting('sample-iu1')['date'] = '''$d'''"
  expect 2 "\$.meetings[0].date:" err "bad date '$d' exits 2" --bundle "$TMP/v_date.json" "${BASE[@]}"
done
variant v_dtok 'meeting("sample-iu1")["date"] = "2026-09-03T23:30:00-07:00"'
expect 0 "M1 · 2026-09-03 · customer call" out "a datetime with offset is taken as its local date" --bundle "$TMP/v_dtok.json" "${BASE[@]}"
for a in 2026-10-02T09:00:00 2026-10-02 20261002T090000Z 2026-10-02T09:00:00+15:00; do
  expect 2 "--as-of:" err "bad --as-of '$a' exits 2" --bundle sample_bundle.json --metrics-csv sample_metrics.csv --as-of "$a"
done
for v in '"41250"' 'true' '[1]' '1e15' '-1e15' '1e999'; do
  variant v_val "b['metrics'][0]['value'] = 'VALX'"; "$PY" - "$TMP/v_val.json" "$v" <<'PY2'
import sys
p, v = sys.argv[1], sys.argv[2]
t = open(p, encoding="utf-8").read().replace('"VALX"', v, 1)
open(p, "w", encoding="utf-8").write(t)
PY2
  expect 2 "\$.metrics[0].value:" err "bad metric value $v exits 2" --bundle "$TMP/v_val.json" "${BASE[@]}"
done
variant v_metric 'b["metrics"][0]["metric"] = "mrrr"'
expect 2 "'mrrr' is not a defined metric; add it to metric_definitions" err "unknown metric key exits 2" --bundle "$TMP/v_metric.json" "${BASE[@]}"
variant v_cur 'b["company"]["currency"] = "usd"'
expect 2 "must be a 3-letter currency code" err "bad currency code exits 2" --bundle "$TMP/v_cur.json" "${BASE[@]}"
variant v_mid 'b["meetings"].append(dict(meeting("sample-iu1")))'
expect 2 "duplicate meeting id" err "duplicate meeting id exits 2" --bundle "$TMP/v_mid.json" "${BASE[@]}"
variant v_cite 'b["items"][1]["citations"] = [{"meeting_id": "sample-zz"}]'
expect 2 "'sample-zz' is not a meeting in \$.meetings" err "citation to an unknown meeting exits 2" --bundle "$TMP/v_cite.json" "${BASE[@]}"
variant v_nocite 'b["items"][1]["citations"] = []'
expect 2 "needs at least one citation" err "item without a citation exits 2" --bundle "$TMP/v_nocite.json" "${BASE[@]}"
variant v_long 'b["items"][1]["text"] = "x " * 2000'
expect 2 "is longer than 1500 characters" err "over-long text exits 2" --bundle "$TMP/v_long.json" "${BASE[@]}"
variant v_secret 'b["items"][1]["text"] = 7'
expect 2 "\$.items[1].text: must be a string, got a number" err "wrong type exits 2 without echoing the value" --bundle "$TMP/v_secret.json" "${BASE[@]}"
variant v_noecho 'b["items"][1]["section"] = "Quorrelind secret plan"'
expect 2 "section: must be one of" err "bad enum exits 2" --bundle "$TMP/v_noecho.json" "${BASE[@]}"
for c in "metric,period,value,source
cash,2026-09,1,S
" "metric,period,value,source,as_of,extra
cash,2026-09,1,S,2026-09-30,x
" "metric,period,value,source,as_of
cash,2026-09,\"1,840,000\",S,2026-09-30
" "metric,period,value,source,as_of
cash,2026-09,\$1840000,S,2026-09-30
" "metric,period,value,source,as_of
cash,2026-09,12%,S,2026-09-30
" "metric,period,value,source,as_of
cash,2026-9,1,S,2026-09-30
" "metric,period,value,source,as_of
cassh,2026-09,1,S,2026-09-30
" "metric,period,value,source,as_of
cash,2026-09,1,S
" "metric,period,value,source,as_of
cash,2026-09,1,S,30/09/2026
" ""; do
  csvfile v_csv "$c"
  expect 2 "metrics CSV" err "bad CSV exits 2: $(printf '%s' "$c" | tail -n1 | head -c 40)" --bundle sample_bundle.json --metrics-csv "$TMP/v_csv.csv" --as-of "$ASOF"
done
printf 'metric,period,value,source,as_of\ncash,2026-09,1,S\x00,2026-09-30\n' >"$TMP/nul.csv"
expect 2 "contains a NUL character" err "NUL in CSV exits 2" --bundle sample_bundle.json --metrics-csv "$TMP/nul.csv" --as-of "$ASOF"
for mutation in 'del c["metrics"]' 'c["forward_terms"] = None' 'c["confidential_terms"]["legal"] = "lawsuit"' 'c["currency_decimals"] = 5' \
                'c["headline_metrics"] = ["nope"]' 'c["metrics"]["mrr"]["unit"] = "dollars"' 'c["confidential_terms"] = {}' 'c["jump_factor"] = True'; do
  "$PY" - "$TMP/cfg.json" "$mutation" <<'PY'
import json, sys
c = json.load(open("update.json", encoding="utf-8"))
exec(sys.argv[2])
json.dump(c, open(sys.argv[1], "w", encoding="utf-8"))
PY
  expect 2 "config" err "bad config ($mutation) exits 2" --bundle sample_bundle.json "${BASE[@]}" --config "$TMP/cfg.json"
done

# ---------------------------------------------------------------- escaping: no markup from input
variant e_bs 'b["items"][1]["text"] = r"Signed \[click\](https://phish.example/a) {org:tamberlind}"; meeting("sample-iu1")["title"] = r"\[t\](https://phish.example/t)"; b["metrics"][0]["source"] = "[s](https://phish.example/s)"'
expect 0 '\\\[click\\\]\(https\://phish\.example/a\)' out "backslash is escaped first in item text" --bundle "$TMP/e_bs.json" "${BASE[@]}"
"$PY" - "$TMP/out" <<'PY' || fail "a working injected link in the draft"
import re, sys
t = open(sys.argv[1], encoding="utf-8").read()
for m in re.finditer(r"\]\(([^)\s]*)", t):
    pre = t[:m.start()]
    assert (len(pre) - len(pre.rstrip("\\"))) % 2 == 1 or "phish" not in m.group(1), m.group(0)
PY
ok "no unescaped ]( leads to an injected URL"
has '[\\\[t\\\]\(https\://phish\.example/t\)](https://notes.granola.ai/d/sample-iu1)' "a meeting title cannot replace the real Granola link"
variant e_url 'meeting("sample-iu1")["url"] = "javascript:alert(1)"; meeting("sample-iu3")["url"] = "https://user:pw@notes.example/x"; meeting("sample-iu5")["url"] = "https://notes.example:99999/x"'
expect 0 "3 links dropped (not an http(s) URL)." out "javascript:, credentials and bad ports are dropped and counted" --bundle "$TMP/e_url.json" "${BASE[@]}"
lacks "javascript" "no javascript: link"
variant e_url2 'meeting("sample-iu1")["url"] = "https://notes.granola.ai/d/a b)(c<d>"'
expect 0 "(https://notes.granola.ai/d/a%20b%29%28c%3Cd%3E)" out "unsafe URL characters are percent-encoded" --bundle "$TMP/e_url2.json" "${BASE[@]}"

# ---------------------------------------------------------------- self-QA regressions (round 4): run in-process
cat >"$TMP/rq.py" <<'PY'
"""Each case prints 'ok <name>' or 'FAIL <name>: <why>'."""
import copy, json, os, sys, tempfile
sys.path.insert(0, os.getcwd())
import build_update as h
from pathlib import Path

ASOF = "2026-10-02T09:00:00-04:00"
SAMPLE = json.load(open("sample_bundle.json", encoding="utf-8"))
TMPD = tempfile.mkdtemp()
CSV = [Path("sample_metrics.csv")]
CFG = Path("update.json")


def build(b, audience="investors", fmt="draft"):
    p = Path(TMPD) / "b.json"
    p.write_text(json.dumps(b), encoding="utf-8")
    try:
        return 0, h.build(p, CSV, CFG, ASOF, audience, fmt)
    except h.InputError as exc:
        return exc.code, str(exc)


def body(text):
    return text.split("## Before you send")[0]


def report(name, good, why=""):
    print("ok {}".format(name) if good else "FAIL {}: {}".format(name, why))


def with_item(text, cites=None, **extra):
    b = copy.deepcopy(SAMPLE)
    it = {"section": "product", "text": text, "citations": cites or [{"meeting_id": "sample-iu5"}]}
    it.update(extra)
    b["items"].append(it)
    return b


# figures glued to metric nouns or multipliers, and figure-like phrases, are withheld; letter-number words are kept
for text, shown in [("We hit 40kMRR this month.", "40kMRR"), ("Usage grew x10 since launch.", "x10"),
                    ("We closed 14deals.", "14deals"), ("Now 100customers live.", "100customers"),
                    ("ARR2M reached in the quarter.", "ARR2M"), ("A seven-figure contract is close.", "seven"),
                    ("Our MRR is in the high five figures.", "high five"), ("Margins improved by ten points.", "ten points"),
                    ("Growth was triple digits.", "triple digits")]:
    code, out = build(with_item(text))
    report("figure withheld from text: " + text, code == 0 and shown not in body(out)
           and "has a figure that is not from the metrics data" in out, out[:200])
for text in ["Shipped the 1Password integration.", "Added Web3 wallets.", "3PL partners use the portal.",
             "B2B demand is up.", "SOC 2 audit passed.", "Q3 planning started."]:
    code, out = build(with_item(text))
    report("letter-number word kept: " + text, code == 0 and text[:12] in body(out), out[:200])

# personnel, M&A, legal and board-only phrasings are withheld when untagged
for text in ["{person:tomas} left the company.", "{person:tomas} quit.", "We parted ways with {person:tomas}.",
             "{person:tomas} is leaving for a competitor.", "{person:tomas} is out on leave.",
             "{person:tomas} gave notice.", "{person:tomas} stepped down as CTO.", "{person:tomas} was dismissed.",
             "{person:tomas} got a raise.", "{person:tomas} had a bad review.", "We are in talks to be acquired.",
             "A strategic acquirer approached us.", "We got an offer to buy the company.", "We may do a down round.",
             "The convertible note was extended.", "Counsel is reviewing the claim.", "We received a demand letter.",
             "A customer threatened legal proceedings.", "An SEC inquiry started.", "We were hacked.",
             "A security incident exposed data.", "Strictly private: the founders disagree.", "Keep this between us.",
             "Not for the investor email.", "The auditor raised a going concern question."]:
    code, out = build(with_item(text))
    probe = text.replace("{person:tomas}", "Tomas Reyes")[:16]
    report("confidential wording withheld: " + text, code == 0 and probe not in body(out) and "may be confidential (" in out,
           out[:200])

# a metric row whose source is a meeting, call or notes (or a meeting title) is withheld: not a metric
for src in ["Granola notes", "Weekly all-hands", "CEO on the sales call", "Mentioned in standup", "Transcript of the retro"]:
    b = copy.deepcopy(SAMPLE)
    for m in b["metrics"]:
        if m["metric"] == "customers" and m["period"] == "2026-09":
            m["source"] = src
    code, out = build(b)
    report("meeting-sourced metric withheld: " + src, code == 0 and "| Paying customers | withheld" in out
           and "source is a meeting, call or notes" in out and "| 38 |" not in out, out[:300])
# ... in the email too, without the figure
code, out = build(b, fmt="email")
report("meeting-sourced metric: the email shows no figure", code == 0 and "| Paying customers | not available" in out, out[:300])

# a source label with a figure or confidential wording is withheld (it would print in the table)
for src in ["Stripe (real MRR 55000)", "Stripe, lawsuit reserve excluded", "Finance sheet v2"]:
    b = copy.deepcopy(SAMPLE)
    for m in b["metrics"]:
        if m["metric"] == "mrr" and m["period"] == "2026-09":
            m["source"] = src
    code, out = build(b, fmt="email")
    if src == "Finance sheet v2":
        report("plain source label with a version is kept", code == 0 and "| MRR | \\$41,250 |" in out, out[:300])
    else:
        report("source label with a figure or confidential wording is withheld: " + src,
               code == 0 and "| MRR | not available |" in out and "55000" not in out and "lawsuit" not in out, out[:300])

# metric labels and the company name are fixed text: checked, exit 2
for label in ["Valuation at term sheet", "Pipeline (310k in call)", "Forecast bookings"]:
    b = copy.deepcopy(SAMPLE)
    b["metric_definitions"] = {"x1": {"label": label, "unit": "currency", "kind": "stock", "better": "up"}}
    code, out = build(b)
    report("metric label checked (exit 2): " + label, code == 2 and "$.metric_definitions.x1.label" in out, out[:300])
b = copy.deepcopy(SAMPLE)
b["metric_definitions"] = {"orders": {"label": "Orders", "unit": "count", "kind": "flow", "better": "up"},
                           "aov": {"label": "Average order value", "unit": "currency", "kind": "flow", "better": "up"},
                           "repeat": {"label": "Repeat purchase rate", "unit": "percent", "kind": "flow", "better": "up"}}
code, out = build(b)
report("DTC metric labels pass the label check", code == 0, out[:300])
b = copy.deepcopy(SAMPLE)
b["company"]["name"] = "Wrenhallow (acquisition offer pending)"
code, out = build(b, fmt="email")
report("company name with a confidential term exits 2", code == 2 and "$.company.name" in out, out[:300])

# confidential meeting titles and document labels are masked in an investor draft, kept in the board draft
b = with_item("Shipped SSO.", cites=[{"source": "document", "label": "Term sheet v3", "url": "https://docs.example/ts"}])
next(m for m in b["meetings"] if m["meeting_id"] == "sample-iu8")["title"] = "Lawsuit prep with counsel"
code, out = build(b)
report("confidential title and document label masked in the investor draft",
       code == 0 and "Term sheet" not in out and "Lawsuit" not in out
       and "(title withheld: it may be confidential)" in out and "(document label withheld: it may be confidential)" in out,
       out[-600:])
code, out = build(b, audience="board")
report("and kept in the board draft", code == 0 and "Term sheet v3" in out and "Lawsuit prep" in out, out[-600:])

# delta maths edge cases
def table(rows):
    b = copy.deepcopy(SAMPLE)
    b["metrics"], b["items"], b["previous_update"], b["commitment_reviews"] = rows, [], None, []
    p = Path(TMPD) / "d.json"
    p.write_text(json.dumps(b), encoding="utf-8")
    try:
        return h.build(p, [], CFG, ASOF, "investors", "email")
    except h.InputError as exc:
        return str(exc)


def row(metric, cur, prev):
    out = [{"metric": metric, "period": "2026-09", "value": cur, "source": "S", "as_of": "2026-09-30"}]
    if prev is not None:
        out.append({"metric": metric, "period": "2026-08", "value": prev, "source": "S", "as_of": "2026-08-31"})
    return out


for name, rows, want in [
        ("half-up on .5", row("mrr", 100.5, 99.5), "| MRR | \\$101 | \\$100 | +\\$1 (+1.0%) |"),
        ("negative base: no percent", row("mrr", 500, -200), "| MRR | \\$500 | -\\$200 | +\\$700 |"),
        ("positive to negative: no percent", row("net_new_mrr", -300, 200), "| Net new MRR | -\\$300 | \\$200 | -\\$500 |"),
        ("zero to zero", row("churned_customers", 0, 0), "| Churned customers | 0 | 0 | no change |"),
        ("to zero is -100%", row("customers", 0, 5), "| Paying customers | 0 | 5 | -5 (-100.0%) |"),
        ("from zero: no percent", row("customers", 5, 0), "| Paying customers | 5 | 0 | +5 |"),
        ("rounds to zero, no -0", row("net_new_mrr", -0.4, 0.3), "| Net new MRR | \\$0 | \\$0 | no change |"),
        ("percent in pp", row("gross_mrr_churn", 2.05, 1.95), "| Gross MRR churn | 2.1% | 2.0% | +0.1 pp |"),
        ("missing prior", row("mrr", 10, None), "| MRR | \\$10 | not available | n/a |")]:
    out = table(rows)
    report("change maths: " + name, want in out, [l for l in out.splitlines() if l.startswith("| ")][1:4])
PY
"$PY" "$TMP/rq.py" >"$TMP/rq" 2>&1 || { tail -30 "$TMP/rq"; fail "self-QA regressions crashed"; }
! grep -q "Traceback" "$TMP/rq" || { cat "$TMP/rq"; fail "self-QA regressions: traceback"; }
! grep -q "^FAIL" "$TMP/rq" || { grep "^FAIL" "$TMP/rq" | head; fail "self-QA regressions"; }
while IFS= read -r line; do ok "${line#ok }"; done < <(grep "^ok " "$TMP/rq")

# ---------------------------------------------------------------- Skills QA round 4 regressions (V1-V7, B1, B2): in-process
cat >"$TMP/r4.py" <<'PY'
"""Each case prints 'ok <name>' or 'FAIL <name>: <why>'."""
import copy, json, os, re, sys, tempfile
sys.path.insert(0, os.getcwd())
import build_update as h
from pathlib import Path

ASOF = "2026-10-02T09:00:00-04:00"
SAMPLE = json.load(open("sample_bundle.json", encoding="utf-8"))
CONFIG = json.load(open("update.json", encoding="utf-8"))
TMPD = tempfile.mkdtemp()
CSV = [Path("sample_metrics.csv")]
RENDERS = [("investors", "draft"), ("investors", "email"), ("investors", "json"), ("board", "draft"), ("board", "email")]


def build(b, audience="investors", fmt="draft", csv=None, config=None):
    p = Path(TMPD) / "b.json"
    p.write_text(json.dumps(b), encoding="utf-8")
    cfg = Path("update.json")
    if config is not None:
        cfg = Path(TMPD) / "c.json"
        cfg.write_text(json.dumps(config), encoding="utf-8")
    try:
        return 0, h.build(p, CSV if csv is None else csv, cfg, ASOF, audience, fmt)
    except h.InputError as exc:
        return exc.code, str(exc)


def report(name, good, why=""):
    print("ok {}".format(name) if good else "FAIL {}: {}".format(name, str(why)[:400]))


def meeting(b, mid):
    return next(m for m in b["meetings"] if m["meeting_id"] == mid)


def quoted(notes=None, quote="", transcript=None):
    """The sample with the Tamberlind quote replaced (permission true) and M1's text replaced."""
    b = copy.deepcopy(SAMPLE)
    m = meeting(b, "sample-iu1")
    m.pop("notes_text", None)
    if notes is not None:
        m["notes_text"] = notes
    if transcript is not None:
        m["transcript_text"] = transcript
    it = next(i for i in b["items"] if i.get("quote") and i["quote"]["meeting_id"] == "sample-iu1")
    it["quote"]["text"] = quote
    return b


def printed_anywhere(b, needle):
    """Whether `needle` (markdown-escaped as the script prints it) reaches any render's body."""
    esc = h.md(needle)
    for aud, fmt in RENDERS:
        code, out = build(b, aud, fmt)
        if code != 0:
            return "exit {}".format(code)
        body = out.split("## Before you send")[0] if fmt != "json" else json.loads(out)["email_body"]
        if "“" + esc in body or "“" + needle in body:
            return "{}/{}".format(aud, fmt)
    return None


# V1 / B2: quotes match at word boundaries, never cross a speaker turn or line, never carry a speaker label
REJECT = [
    ("helpful inside unhelpful (QA)", "Head of Logistics: The dock calendar is unhelpful for my team.", "helpful for my team"),
    ("reversed renewal (QA)", "Buyer: Nobody will renew next quarter.", "Customers will renew next quarter"),
    ("satisfied inside dissatisfied (QA)", "Customer: We are dissatisfied with the rollout.", "satisfied with the rollout"),
    ("crosses a speaker turn and keeps the label (QA)",
     "Interviewer: Will you renew? System audio (Customer): We will not renew.",
     "Will you renew? System audio (Customer): We will"),
    ("crosses a speaker turn without the label", "Me: Is the dock calendar useful?\nThem: Not for my team.",
     "useful? Not for my team"),
    ("spans two lines", "- The dock calendar is great\n- for nobody on my team", "great for nobody on my team"),
    ("verbatim but carries a speaker label", SAMPLE["meetings"][0]["notes_text"],
     "Head of Logistics: The dock calendar is the first thing my team opens every morning."),
    ("stitched with an ellipsis", SAMPLE["meetings"][0]["notes_text"], "The dock calendar … every morning."),
    ("invented", SAMPLE["meetings"][0]["notes_text"], "The dock calendar is the only thing my team opens."),
    ("word cut at the end", "Lead: The dock calendar is helpfulness itself.", "The dock calendar is helpful"),
    ("a quote that ends mid-sentence (round 5: whole sentences only)", SAMPLE["meetings"][0]["notes_text"],
     "The dock calendar is the first thing"),
]
for name, notes, quote in REJECT:
    b = quoted(notes, quote)
    where = printed_anywhere(b, quote)
    code, out = build(b)
    report("quote rejected: " + name, where is None and code == 0 and "Highlights #2 (items[1]): quote left out" in out,
           where or out[-900:])
# the same rules for transcript text
b = quoted(None, "helpful for my team", transcript="Them: honestly it is unhelpful for my team")
report("quote rejected: helpful inside unhelpful, transcript text", printed_anywhere(b, "helpful for my team") is None)
code, out = build(quoted(SAMPLE["meetings"][0]["notes_text"], "Will you renew? System audio (Customer): We will"))
report("a quote with a speaker label names the reason", "speaker label" in out, out[-900:])

ACCEPT = [
    ("markdown emphasis, link and escapes in the notes",
     "- **Head of Logistics:** The **dock calendar** is [the first thing](https://x.example/a) my team opens every "
     "morning\\.", "The dock calendar is the first thing my team opens every morning."),
    ("markdown emphasis in the quote", SAMPLE["meetings"][0]["notes_text"],
     "The *dock calendar* is the first thing my team opens every morning."),
    ("Granola escapes and typographic apostrophe", "Ops lead: We stopped chasing carriers by phone\\. It\\'s done\\!",
     "We stopped chasing carriers by phone. It’s done!"),
    ("case and spacing differences", "Lead:   the dock calendar  is the first thing my team opens every morning.",
     "The dock calendar is the first thing my team opens every morning."),
]
for name, notes, quote in ACCEPT:
    code, out = build(quoted(notes, quote))
    report("quote accepted: " + name, code == 0 and "Highlights #2 (items[1]): quote left out" not in out
           and "  > “" in out, out[-900:])


# V2 (round 5 rules): a founder statement unlocks only an item that is one of the founder's own sentences
def stated(text, excerpt, mid="sample-iu3", notes_extra=None, speaker="Maren"):
    b = copy.deepcopy(SAMPLE)
    if notes_extra:
        meeting(b, "sample-iu3")["notes_text"] += notes_extra
    fs = {"meeting_id": mid, "excerpt": excerpt} if mid else {"source": "user", "excerpt": excerpt}
    if mid and speaker:
        fs["speaker"] = speaker
    b["items"].append({"section": "product", "text": text, "citations": [{"meeting_id": "sample-iu5"}],
                       "founder_stated": fs})
    return b


def unlocked(b, text):
    code, out = build(b)
    return code == 0 and "- " + text in out.split("## Before you send")[0], out


for name, text, excerpt, mid, extra in [
        ("an unrelated real excerpt (QA)", "We will be profitable next year.", "Both engineering hires started this week.",
         "sample-iu3", None),
        ("'ok' told directly (QA)", "We will be profitable next year.", "ok", None, None),
        ("'OK' found in the notes (QA)", "We will be profitable next year.", "OK", "sample-iu1", None),
        ("a reversed statement told directly (QA)", "Customers will renew next quarter.", "Nobody will renew next quarter.",
         None, None),
        ("a reversed statement in the notes", "Customers will renew next quarter.", "Nobody will renew next quarter.",
         "sample-iu3", "\nMaren: Nobody will renew next quarter."),
        ("a negated statement", "We will hit our hiring plan.", "We will not hit our hiring plan.", "sample-iu3",
         "\nMaren: We will not hit our hiring plan."),
        ("a negated statement (contraction)", "We will hit our hiring plan.", "We won't hit our hiring plan.",
         "sample-iu3", "\nMaren: We won't hit our hiring plan."),
        ("a weaker forward word", "We will be profitable in Q3.", "We might be profitable in Q3.", "sample-iu3",
         "\nMaren: We might be profitable in Q3."),
        ("the claim inside a negation", "We will be profitable in Q3.", "It is not true that we will be profitable in Q3.",
         "sample-iu3", "\nMaren: It is not true that we will be profitable in Q3."),
        ("a close paraphrase (allowed before round 5)", "We will open the carrier portal to every customer in November.",
         "We will open the carrier portal to all customers in November.", "sample-iu3", None),
        ("the claim inside a longer sentence (allowed before round 5)", "We will add SSO.",
         "Honestly, we will add SSO, it is the top request.", "sample-iu3",
         "\nMaren: Honestly, we will add SSO, it is the top request."),
        ("a neighbour sentence about the claim's subject (round 9: the context check fails closed)", "We will add SSO.",
         "SSO is the top request. We will add SSO.", "sample-iu3", "\nMaren: SSO is the top request. We will add SSO.")]:
    good, out = unlocked(stated(text, excerpt, mid, extra), text)
    report("founder statement does not unlock the item: " + name,
           not good and ("its founder statement" in out or "founder's sentences" in out),
           out[-900:])
for name, text, excerpt, extra in [
        ("the same words", "We will add SSO.", "We will add SSO.", "\nMaren: We will add SSO."),
        ("one sentence of a two-sentence excerpt", "We will add SSO.", "The security review passed. We will add SSO.",
         "\nMaren: The security review passed. We will add SSO."),
        ("case, a dropped full stop and markdown in the notes", "we will add SSO",
         "We will add SSO.", "\n**Maren (CEO):** We will add *SSO*.")]:
    good, out = unlocked(stated(text, excerpt, notes_extra=extra), text)
    report("founder statement unlocks the item: " + name, good, out[-900:])
code, out = build(SAMPLE)
report("sample founder statement still unlocks its item",
       "- We will open the carrier portal to all customers in November. (M3)" in out, out[:300])

# V3 / B1: $...$ math, ==highlight== and autolinks are escaped in every format
LIVE_DOLLAR = re.compile(r"(?<!\\)(?:\\\\)*\$")
LIVE_EQ = re.compile(r"(?<!\\)(?:\\\\)*==")
LIVE_URL = re.compile(r"(?<!\\)://|(?<![\\\w])www\.|phish\.example")
b = copy.deepcopy(SAMPLE)
meeting(b, "sample-iu1")["title"] = "cost $5 to $9 and hi ==hl== https://phish.example/a www.phish.example phish.example/b"
b["items"].append({"section": "product", "text": "Shipped ==dock analytics== and $beta$ for www.phish.example and "
                   "https://phish.example/x and phish.example.", "citations": [{"meeting_id": "sample-iu1"}]})
for aud, fmt in RENDERS:
    code, out = build(b, aud, fmt)
    texts = [out] if fmt != "json" else [json.dumps(json.loads(out), ensure_ascii=False)]
    if fmt == "json":
        d = json.loads(out)
        texts = [d["markdown"], d["email_body"]]
    stripped = [re.sub(r"\]\([^)\s]*\)", "]", t) for t in texts]  # script-built link targets are not input
    bad = [rx.pattern for rx in (LIVE_DOLLAR, LIVE_EQ, LIVE_URL) for t in stripped if rx.search(t)]
    report("B1/V3 no live $, ==, URL, www. or domain from input: {}/{}".format(aud, fmt), code == 0 and not bad,
           bad or out[:300])
code, out = build(SAMPLE, "investors", "email")
report("currency figures print as \\$ (two on one table row are not math)", "\\$41,250" in out
       and not LIVE_DOLLAR.search(out), out[:600])
report("md() escapes $, == and breaks autolinks (QA example)",
       h.md("cost $5 to $9 and hi") == "cost \\$5 to \\$9 and hi" and "==" not in h.md("a ==b== c")
       and "https://" not in h.md("https://phish.example") and "www." not in h.md("www.phish.example"),
       [h.md("cost $5 to $9 and hi"), h.md("a ==b== c"), h.md("https://phish.example www.x.example")])

# V4: metric_order and headline_metrics are validated in load_config (exit 2, not a crash)
for name, mutate in [
        ("metric_order missing a commitment's target metric (QA)",
         lambda c: c.__setitem__("metric_order", [k for k in c["metric_order"] if k != "net_burn"])),
        ("metric_order with a duplicate", lambda c: c["metric_order"].append("mrr")),
        ("non-string headline_metrics entry (QA)", lambda c: c.__setitem__("headline_metrics", [["mrr"]])),
        ("non-string metric_order entry", lambda c: c["metric_order"].append({"k": 1})),
        ("headline_metrics with a duplicate", lambda c: c["headline_metrics"].append("mrr"))]:
    cfg = copy.deepcopy(CONFIG)
    mutate(cfg)
    try:
        code, out = build(SAMPLE, config=cfg)
    except Exception as exc:  # the round-4 crash
        code, out = 1, repr(exc)
    report("config rejected (exit 2): " + name, code == 2 and "config" in out
           and ("metric_order" in out or "headline_metrics" in out), out)

# V5: a commitment whose target figure is missing is "Not verified", not Done
csv = Path(TMPD) / "noburn.csv"
csv.write_text("".join(l for l in open("sample_metrics.csv", encoding="utf-8") if not l.startswith("net_burn")),
               encoding="utf-8")
b = copy.deepcopy(SAMPLE)
code, out = build(b, csv=[csv])
row = [l for l in out.split("\n") if "net burn" in l.lower() and l.startswith("| ") and "130,000" in l]
report("commitment marked done with its target figure missing shows Not verified (QA)",
       code == 0 and row and "| Not verified |" in row[0] and "| Done |" not in row[0]
       and "status changed from Done to Not verified" in out, row or out[-1500:])
b = copy.deepcopy(SAMPLE)
for r in b["commitment_reviews"]:
    if r["commitment_id"] == "c1":
        r["status"] = "done"
b["metrics"] = [m for m in b["metrics"] if not (m["metric"] == "mrr" and m["period"] == "2026-09")]
code, out = build(b)
row = [l for l in out.split("\n") if "40,000 in MRR" in l]
report("and for a >= target (c1, MRR missing)", code == 0 and row and "| Not verified |" in row[0], row or out[-900:])

# V6: listing and docs carry the pattern-based caveat for the figure check
listing = json.load(open("_listing.json", encoding="utf-8"))
contract = open("DATA_CONTRACT.md", encoding="utf-8").read()
skill = open("SKILL.md", encoding="utf-8").read()
report("listing benefit 2 says the figure check is pattern-based (QA)", "pattern-based" in listing["benefits"][1]
       and "review" in listing["benefits"][1], listing["benefits"][1])
report("docs name the figure-check misses (spelled-out amounts, year-like numbers)",
       all("forty-two grand" in t and "2025 new docks" in t for t in (contract, skill)))
for text in ("We received forty-two grand from a customer.", "We added 2025 new docks."):
    b = copy.deepcopy(SAMPLE)
    b["items"].append({"section": "product", "text": text, "citations": [{"meeting_id": "sample-iu5"}]})
    code, out = build(b)
    report("documented miss is real (so the caveat is needed): " + text, code == 0 and text in out, out[-300:])

# V7: sample commitment wording agrees with its target operator
WORDS = {"<=": ("at or below", "or less", "at most"), "<": ("below", "under", "less than"),
         ">=": ("at least", "or more", "reach"), ">": ("above", "more than", "over"), "=": ("exactly",)}
for c in SAMPLE["previous_update"]["commitments"]:
    t = c.get("metric_target")
    if t:
        low = c["text"].lower()
        good = any(w in low for w in WORDS[t["op"]])
        if t["op"] == "<":
            good = good and "at or below" not in low and "or less" not in low
        if t["op"] == "<=":
            good = good and not re.search(r"(?<!or )below", low)
        report("sample commitment {} wording matches {} (QA)".format(c["id"], t["op"]), good, c["text"])
PY
"$PY" "$TMP/r4.py" >"$TMP/r4" 2>&1 || { tail -20 "$TMP/r4"; fail "round-4 regressions crashed"; }
! grep -q "^FAIL" "$TMP/r4" || { grep "^FAIL" "$TMP/r4" | head -40; fail "Skills QA round-4 regressions"; }
while IFS= read -r line; do ok "${line#ok }"; done < <(grep "^ok " "$TMP/r4")

# ---------------------------------------------------------------- Skills QA round 5 regressions (V8, V9, X1): in-process
cat >"$TMP/r5.py" <<'PY'
"""Each case prints 'ok <name>' or 'FAIL <name>: <why>'."""
import copy, hashlib, json, os, sys, tempfile
sys.path.insert(0, os.getcwd())
import build_update as h
from pathlib import Path

ASOF = "2026-10-02T09:00:00-04:00"
SAMPLE = json.load(open("sample_bundle.json", encoding="utf-8"))
TMPD = tempfile.mkdtemp()
RENDERS = [("investors", "draft"), ("investors", "email"), ("investors", "json"), ("board", "draft"), ("board", "email"),
           ("board", "json")]


def build(b, audience="investors", fmt="draft"):
    p = Path(TMPD) / "b.json"
    p.write_text(json.dumps(b), encoding="utf-8")
    try:
        return 0, h.build(p, [Path("sample_metrics.csv")], Path("update.json"), ASOF, audience, fmt)
    except h.InputError as exc:
        return exc.code, str(exc)


def report(name, good, why=""):
    print("ok {}".format(name) if good else "FAIL {}: {}".format(name, str(why)[:400]))


def meeting(b, mid):
    return next(m for m in b["meetings"] if m["meeting_id"] == mid)


def body(out, fmt):
    return json.loads(out)["email_body"] + json.loads(out)["markdown"].split("## Before you send")[0] \
        if fmt == "json" else out.split("## Before you send")[0]


def printed(b, text):
    """The render where `text` (or its escaped form) reaches an update body, or None."""
    for aud, fmt in RENDERS:
        code, out = build(b, aud, fmt)
        if code != 0:
            return "exit {}".format(code)
        low = body(out, fmt).lower()
        if text.lower() in low or h.md(text).lower() in low:
            return "{}/{}".format(aud, fmt)
    return None


def founder(item_text, excerpt, said=None, speaker="Maren", mid="sample-iu3"):
    """The sample plus a product item unlocked by `excerpt`; `said` is appended to M3's notes as its own line."""
    b = copy.deepcopy(SAMPLE)
    if said is not None:
        meeting(b, "sample-iu3")["notes_text"] += "\n" + said
    fs = {"meeting_id": mid, "excerpt": excerpt}
    if speaker is not None:
        fs["speaker"] = speaker
    b["items"].append({"section": "product", "text": item_text, "citations": [{"meeting_id": "sample-iu5"}],
                       "founder_stated": fs})
    return b


def withheld(b, item_text, name, reason=None):
    where = printed(b, item_text)
    code, out = build(b)
    good = where is None and code == 0 and "Withheld from the draft — Product #4" in out
    if reason is not None:
        good = good and reason in out
    report(name, good, where or out[-900:])


# V8 (HIGH): the sample's "November" excerpt no longer supports a different claim
NOV = "We will open the carrier portal to all customers in November."
for name, text in [("March instead of November (QA)", "We will open the carrier portal to all customers in March."),
                   ("close instead of open (QA)", "We will close the carrier portal to all customers in November."),
                   ("a dropped qualifier", "We will open the carrier portal to customers in November."),
                   ("an added claim", "We will open the carrier portal to all customers in November and raise prices.")]:
    withheld(founder(text, NOV), text, "V8 sample excerpt does not unlock: " + name, "founder's sentences")
withheld(founder("We will be profitable next year.", "We will be unprofitable next year.",
                 "Maren: We will be unprofitable next year."), "We will be profitable next year.",
         "V8 'unprofitable' excerpt does not unlock 'profitable' (QA)")
withheld(founder("We will be profitable next year.", "We will be profitable next year.",
                 "Maren: We will be unprofitable next year."), "We will be profitable next year.",
         "V8 'profitable' is not found inside 'unprofitable'", "not found in the meeting text")
b = founder(NOV, NOV)
report("V8 the same sentence still unlocks (control)", printed(b, NOV) is not None)

# V9 (Med): questions and conditionals never support an item
for name, item_text, excerpt, said, reason in [
        ("a question as excerpt (QA)", "We will be profitable next year.", "Will we be profitable next year?",
         "Maren: Will we be profitable next year?", None),
        ("the question itself as the item", "Will we be profitable next year?", "Will we be profitable next year?",
         "Maren: Will we be profitable next year?", "question or a conditional"),
        ("question mark dropped from the excerpt", "Will we be profitable next year.", "Will we be profitable next year.",
         "Maren: Will we be profitable next year?", "final question mark"),
        ("a conditional (QA)", "If we close the round, we will be profitable.",
         "If we close the round, we will be profitable.", "Maren: If we close the round, we will be profitable.",
         "question or a conditional"),
        ("the consequence cut from a conditional (QA)", "We will be profitable.", "we will be profitable",
         "Maren: If we close the round, we will be profitable.", "not a complete sentence"),
        ("the consequence as item, conditional as excerpt", "We will be profitable.",
         "If we close the round, we will be profitable.", "Maren: If we close the round, we will be profitable.", None),
        ("unless", "We will hit plan.", "We will hit plan unless churn rises.", "Maren: We will hit plan unless churn rises.",
         None),
        ("assuming, as one sentence of two", "We will raise in spring.",
         "We will raise in spring. Assuming the market holds, we will close fast.",
         "Maren: We will raise in spring. Assuming the market holds, we will close fast.", "question or a conditional")]:
    withheld(founder(item_text, excerpt, said), item_text, "V9 " + name, reason)

# Round 6: attribution must be anchored and context must remain attached.
for said in [
    "Board member (Kestrel VC): Here is what I want to tell LPs, Maren: we will be profitable by March.",
    "Maren (CEO): If the bridge closes this month, sure. We will be profitable by March.",
    "Maren (CEO): Best case. We will be profitable by March.",
    "Maren (CEO): Will the portal ship to everyone? Yes. We will be profitable by March.",
    "Board: Maren: We will be profitable by March.",
]:
    withheld(founder("We will be profitable by March.", "We will be profitable by March.", said),
             "We will be profitable by March.", "R6 founder context " + said)
b = founder("We will be profitable by March.", "We will be profitable by March.",
            "Maren (CEO): We will be profitable by March.")
report("R6 line-start founder control", printed(b, "We will be profitable by March.") is not None)

# X1: a leading negation can't be dropped from a quote or a founder statement
X1 = [("None of our drivers trust the dock calendar.", "our drivers trust the dock calendar"),
      ("Nobody will renew next quarter.", "will renew next quarter"),
      ("Nobody will renew next quarter.", "Will renew next quarter."),
      ("Our drivers will never use the dock calendar.", "Our drivers will"),
      ("I would not pay for this.", "I would ... pay for this"),
      ("Not one of the drivers will use the portal.", "one of the drivers will use the portal")]
for said, cut in X1:
    b = copy.deepcopy(SAMPLE)
    meeting(b, "sample-iu1")["notes_text"] = "Head of Logistics: " + said
    it = next(i for i in b["items"] if i.get("quote") and i["quote"]["meeting_id"] == "sample-iu1")
    it["quote"]["text"] = cut
    where = printed(b, cut)
    code, out = build(b)
    report("X1 quote rejected: {!r} from {!r} (QA)".format(cut, said),
           where is None and code == 0 and "Highlights #2 (items[1]): quote left out" in out, where or out[-900:])
    withheld(founder(cut, cut, "Maren: " + said), cut, "X1 founder excerpt rejected: {!r} from {!r}".format(cut, said))
b = copy.deepcopy(SAMPLE)
meeting(b, "sample-iu1")["notes_text"] = "Head of Logistics: None of our drivers trust the dock calendar."
it = next(i for i in b["items"] if i.get("quote") and i["quote"]["meeting_id"] == "sample-iu1")
it["quote"]["text"] = "None of our drivers trust the dock calendar."
report("X1 the whole sentence is still quotable (control)", printed(b, "None of our drivers trust the dock calendar") is not None)

SSO = "We will add SSO for every customer."
# whose turn: the excerpt must come from the founder's own turn
withheld(founder("We will expand to every warehouse.", "We will expand to every warehouse.",
                 "Head of Logistics: We will expand to every warehouse."), "We will expand to every warehouse.",
         "a customer's sentence is not a founder statement", "not from a turn of the speaker")
withheld(founder(SSO, SSO, "Maren: " + SSO, speaker=None), SSO, "a founder statement without a speaker is withheld", "has no speaker")
withheld(founder(SSO, SSO, "Maren: " + SSO, speaker="Head of Logistics"), SSO, "the wrong speaker is withheld",
         "not from a turn of the speaker")
b = founder(SSO, SSO, "Maren: " + SSO)
meeting(b, "sample-iu3").pop("notes_text")
withheld(b, SSO, "a founder statement whose meeting text is not in the bundle is withheld", "is not in the bundle")
withheld(founder(SSO, "Maren (CEO): " + SSO, "Maren (CEO): " + SSO), SSO, "an excerpt with a speaker label is withheld", "speaker label")
withheld(founder(SSO, "We will add SSO … customer.", "Maren: " + SSO), SSO, "an excerpt with an ellipsis is withheld",
         "ellipsis")
withheld(founder(SSO, SSO + " It is the top", "Maren: " + SSO + " It is the top request."), SSO,
         "an excerpt that ends mid-sentence is withheld", "not a complete sentence")
withheld(founder(SSO, SSO, "Them: " + SSO, speaker="Me"), SSO, "Granola's Me/Them labels: Them is not Me")
b = founder("We will ship SSO in Q4.", "We will ship SSO in Q4.", "Me: We will ship SSO in Q4.", speaker="Me")
report("Granola's Me label works as the founder's speaker", printed(b, "We will ship SSO in Q4.") is not None)

# the shared helper: byte-for-byte copy, loaded from the script's own folder
digest = hashlib.sha256(open("granola_text.py", "rb").read()).hexdigest()
report("granola_text.py is the shared v1.6.0 file (SHA-256)", digest == os.environ["GT_SHA256"], digest)
shared = Path("..") / "shared" / "granola_text.py"
if shared.is_file():
    report("granola_text.py equals ../shared/granola_text.py byte for byte",
           shared.read_bytes() == Path("granola_text.py").read_bytes())
report("build_update uses the copy next to it", Path(h.gt.__file__).resolve() == Path("granola_text.py").resolve()
       and h.gt.VERSION == "1.6.0", h.gt.__file__)
for name in ("found_in", "plain", "supports", "claim_words", "redact", "is_phone", "MD_SPECIAL_RE", "SPEAKER_RE"):
    report("the package's own helper {} is gone".format(name), not hasattr(h, name))
report("md() is shared escaping after shared redaction",
       h.md("mail a@b.example: $5 ==x== https://e.example") == h.gt.md(h.gt.redact_contacts("mail a@b.example: $5 ==x== https://e.example")))
PY
GT_SHA256=00ec63cdf7126c247832276f77f55f2a60fb8b58b9a9dc6fd4fecbe2317df168 "$PY" "$TMP/r5.py" >"$TMP/r5" 2>&1 || { tail -20 "$TMP/r5"; fail "round-5 regressions crashed"; }
! grep -q "^FAIL" "$TMP/r5" || { grep "^FAIL" "$TMP/r5" | head -40; fail "Skills QA round-5 regressions"; }
while IFS= read -r line; do ok "${line#ok }"; done < <(grep "^ok " "$TMP/r5")
# the script runs from any working directory and in isolated mode, and writes no bytecode next to itself
SKILL_DIR="$PWD"
(cd "$TMP" && "$PY" "$SKILL_DIR/$SCRIPT" --bundle "$SKILL_DIR/sample_bundle.json" --metrics-csv "$SKILL_DIR/sample_metrics.csv" --as-of "$ASOF" >"$TMP/cwd_out") || fail "run from another directory"
cmp -s "$TMP/cwd_out" <(sed -n '/^=== 1\./,/^=== 2\./p' expected_output.txt | sed '1d;$d' | sed '$d') || fail "run from another directory differs"
ok "runs from another working directory with the same output"
(cd "$TMP" && env -u PYTHONDONTWRITEBYTECODE "$PY" -I "$SKILL_DIR/$SCRIPT" --bundle "$SKILL_DIR/sample_bundle.json" --metrics-csv "$SKILL_DIR/sample_metrics.csv" --as-of "$ASOF" >/dev/null) || fail "python -I"
[[ ! -d __pycache__ ]] || fail "running the script wrote __pycache__"
ok "runs with python -I and without PYTHONDONTWRITEBYTECODE, leaving no __pycache__"

# ---------------------------------------------------------------- round-5 blind attack (written from the docs' claims only)
cat >"$TMP/attack.py" <<'PY'
"""Blind attack on investor-update-drafter, written only from SKILL.md, DATA_CONTRACT.md and _listing.json claims.

Usage: python3 /tmp/iu_r6_attack.py SKILL_DIR   (prints 'ok'/'FAIL' per attack and a summary; exit 1 on any FAIL)
"""
import copy, json, os, re, subprocess, sys, tempfile

SKILL = os.path.abspath(sys.argv[1] if len(sys.argv) > 1 else ".")
PY = os.environ.get("PYTHON", sys.executable)
ASOF = "2026-10-02T09:00:00-04:00"
SAMPLE = json.load(open(os.path.join(SKILL, "sample_bundle.json"), encoding="utf-8"))
CSV = os.path.join(SKILL, "sample_metrics.csv")
TMP = tempfile.mkdtemp(prefix="iu_r6_attack_")
RENDERS = [("investors", "draft"), ("investors", "email"), ("investors", "json"),
           ("board", "draft"), ("board", "email"), ("board", "json")]
INVESTOR = RENDERS[:3]
results = []


def run(b, audience="investors", fmt="draft", csv_text=None):
    p = os.path.join(TMP, "b.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump(b, f, ensure_ascii=False)
    args = [PY, os.path.join(SKILL, "build_update.py"), "--bundle", p, "--as-of", ASOF, "--audience", audience,
            "--format", fmt]
    if csv_text is None:
        args += ["--metrics-csv", CSV]
    else:
        c = os.path.join(TMP, "m.csv")
        with open(c, "w", encoding="utf-8") as f:
            f.write(csv_text)
        args += ["--metrics-csv", c]
    r = subprocess.run(args, cwd=TMP, stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=dict(os.environ, PYTHONDONTWRITEBYTECODE="1"))
    return r.returncode, r.stdout.decode("utf-8"), r.stderr.decode("utf-8")


def bodies(out, fmt):
    """The parts of the output that reach investors: the email body, or the draft above 'Before you send'."""
    if fmt == "json":
        d = json.loads(out)
        return [d["email_body"], d["markdown"].split("## Before you send")[0]]
    return [out.split("## Before you send")[0]]


def plain(text):
    """What a reader sees: backslash escapes and entities undone, case folded."""
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    return re.sub(r"\\(.)", r"\1", text).lower()


def report(name, good, why=""):
    results.append((name, good))
    print(("ok   " if good else "FAIL ") + name + ("" if good else ": " + str(why)[:300]))


def absent(name, b, needles, renders=RENDERS, anywhere=False, csv_text=None):
    """None of `needles` reaches an investor-facing body (or anywhere in stdout when anywhere=True) in any render;
    every run exits 0, or exits 2/3 with nothing on stdout."""
    for aud, fmt in renders:
        code, out, err = run(b, aud, fmt, csv_text)
        if "Traceback" in err:
            return report(name, False, "traceback " + err[-200:])
        if code != 0:
            if code in (2, 3) and not out:
                continue
            return report(name, False, "exit {} {}".format(code, err[-200:]))
        texts = [out] if anywhere else bodies(out, fmt)
        for t in texts:
            for n in needles:
                if n.lower() in plain(t) or n.lower() in t.lower():
                    return report(name, False, "{!r} printed in {}/{}".format(n, aud, fmt))
    report(name, True)


def present(name, b, needle, aud="investors", fmt="draft"):
    code, out, err = run(b, aud, fmt)
    report(name, code == 0 and any(needle.lower() in plain(t) for t in bodies(out, fmt)), (code, err[-200:], out[-400:]))


def meeting(b, mid):
    return next(m for m in b["meetings"] if m["meeting_id"] == mid)


def with_item(text, cites=None, **extra):
    b = copy.deepcopy(SAMPLE)
    it = {"section": "product", "text": text, "citations": cites or [{"meeting_id": "sample-iu5"}]}
    it.update(extra)
    b["items"].append(it)
    return b


def with_metric_row(**row):
    b = copy.deepcopy(SAMPLE)
    base = {"metric": "mrr", "period": "2026-09", "value": 987654, "source": "Stripe", "as_of": "2026-10-01"}
    base.update(row)
    for k in [k for k, v in base.items() if v is None]:
        del base[k]
    b["metrics"] = [m for m in b["metrics"] if not (m["metric"] == "mrr" and m["period"] == "2026-09")] + [base]
    return b


# ---------------------------------------------------------------- unsourced metrics (DATA_CONTRACT metrics[] footnote)
for label, row in [("no source", {"source": None}), ("source 'unknown'", {"source": "unknown"}),
                   ("source 'TBD'", {"source": "TBD"}), ("source 'n/a'", {"source": "n/a"}),
                   ("source '-'", {"source": "-"}), ("source '?'", {"source": "?"}),
                   ("no as_of", {"as_of": None}), ("as_of after --as-of", {"as_of": "2026-10-03"}),
                   ("as_of before the period began", {"as_of": "2026-08-15"}),
                   ("another currency", {"currency": "EUR"}),
                   ("source label with a figure", {"source": "Stripe export 2 of 3 (987654)"})]:
    absent("unsourced metric withheld everywhere: " + label, with_metric_row(**row), ["987,654", "987654"],
           anywhere=True)
csv_blank = open(CSV, encoding="utf-8").read().replace("headcount,2026-09,14,Finance sheet (sample),2026-09-30",
                                                       "headcount,2026-09,77,,2026-09-30")
absent("CSV row with a blank source is withheld", copy.deepcopy(SAMPLE), ["| 77 |", " 77 "], anywhere=True,
       csv_text=csv_blank)
for text in ["We closed 37 new deals in September.", "MRR hit 55k this month.", "We are at 40kMRR.",
             "Revenue doubled since the spring.", "We added two new customers in Ohio.", "Pipeline grew to $310k."]:
    absent("figure not from metrics data withholds the item: " + text, with_item(text), [text.rstrip(".")],
           renders=INVESTOR)

# ---------------------------------------------------------------- meeting-derived metrics
for src in ["Granola", "all-hands notes", "customer call with Tamberlind", "Weekly all-hands", "Board prep notes"]:
    absent("metric sourced from a meeting is withheld: " + src, with_metric_row(source=src), ["987,654", "987654"],
           anywhere=True)

# ---------------------------------------------------------------- non-public names
NAMES = ["quorrelind", "ostermoss", "jordan pike", "avery lund"]
for text in ["Quorrelind expanded to a second warehouse.", "QUORRELIND expanded to a second warehouse.",
             "Q u o r r e l i n d expanded to a second warehouse.", "Quörrelind expanded to a second warehouse.",
             "Quorrel​ind expanded to a second warehouse.", "Quоrrelind expanded to a second warehouse.",
             "Ostermoss came back for a second pilot.", "Jordan accepted the VP Sales offer.",
             "Avery is leading the dispatch rebuild."]:
    absent("non-public name in item text is withheld: " + text, with_item(text), NAMES + ["q u o r r", "quörrelind"])
b = copy.deepcopy(SAMPLE)
it = next(i for i in b["items"] if i.get("quote") and i["quote"]["permission"])
it["quote"]["attribution"] = "Ops lead at Quorrelind Logistics"
absent("non-public name in a quote attribution is not printed", b, NAMES)
b = copy.deepcopy(SAMPLE)
b["previous_update"]["commitments"][1]["text"] = "Open the portal beta to Quorrelind and two others."
absent("non-public name in a previous commitment is not printed", b, NAMES)
b = copy.deepcopy(SAMPLE)
b["commitment_reviews"][3]["note"] = "Ostermoss went with a competitor."
absent("non-public name in a review note is not printed", b, NAMES)
b = copy.deepcopy(SAMPLE)
b["metric_definitions"] = {"q_orders": {"label": "Quorrelind orders", "unit": "count", "kind": "flow", "better": "up"}}
b["metrics"].append({"metric": "q_orders", "period": "2026-09", "value": 5, "source": "Stripe", "as_of": "2026-10-01"})
absent("non-public name in a metric label refuses (exit 2/3, nothing printed)", b, NAMES, anywhere=True)
b = with_item("Shipped the dispatch board.", [{"source": "document", "label": "Ostermoss rollout plan",
                                                "url": "https://docs.example/sample/plan"}])
absent("non-public name in a document label is not printed", b, NAMES, anywhere=True)
b = with_item("The carrier portal now supports {org:quorrelind}-style bulk booking.")
present("placeholder for a non-public org prints its descriptor (control)", b, "a mid-market logistics customer")

# ---------------------------------------------------------------- confidential items
for tag in ["fundraising_terms", "legal", "personnel", "board_only"]:
    b = with_item("Zephyrine item tagged {} for the board.".format(tag), confidential=tag)
    absent("item tagged {} is left out of every investor render".format(tag), b, ["zephyrine"], renders=INVESTOR,
           anywhere=True)
b = with_item("Zephyrine joined the board prep meeting.", [{"meeting_id": "sample-iu7"}])
absent("untagged item citing the board meeting is left out of investor renders", b, ["zephyrine"], renders=INVESTOR,
       anywhere=True)
present("and it reaches the board version (control)", b, "zephyrine", "board", "draft")
for text in ["Two funds sent a term sheet for the bridge.", "Our head of sales stepped down last week."]:
    absent("untagged confidential wording is withheld from investors: " + text, with_item(text), [text.rstrip(".")],
           renders=INVESTOR)

# ---------------------------------------------------------------- founder statements: reversed, negated, conditional
NOV = "We will open the carrier portal to all customers in November."


def founder(item_text, excerpt, said=None, speaker="Maren", mid="sample-iu3"):
    b = copy.deepcopy(SAMPLE)
    if said:
        meeting(b, "sample-iu3")["notes_text"] += "\n" + said
    fs = {"meeting_id": mid, "excerpt": excerpt, "speaker": speaker} if mid else {"source": "user", "excerpt": excerpt}
    return with_item(item_text, founder_stated=fs) if not said else (
        lambda bb: (bb["items"].append({"section": "product", "text": item_text,
                                        "citations": [{"meeting_id": "sample-iu5"}], "founder_stated": fs}), bb)[1])(b)


for name, item_text, excerpt, said in [
        ("March for November", "We will open the carrier portal to all customers in March.", NOV, None),
        ("close for open", "We will close the carrier portal to all customers in November.", NOV, None),
        ("profitable from unprofitable", "We will be profitable next year.", "We will be unprofitable next year.",
         "Maren: We will be unprofitable next year."),
        ("profitable cut out of unprofitable", "We will be profitable next year.", "We will be profitable next year.",
         "Maren: We will be unprofitable next year."),
        ("a question", "We will be profitable next year.", "Will we be profitable next year?",
         "Maren: Will we be profitable next year?"),
        ("a question as the item", "Will we be profitable next year?", "Will we be profitable next year?",
         "Maren: Will we be profitable next year?"),
        ("a conditional", "If we close the round, we will be profitable.", "If we close the round, we will be profitable.",
         "Maren: If we close the round, we will be profitable."),
        ("the tail of a conditional", "We will be profitable.", "we will be profitable",
         "Maren: If we close the round, we will be profitable."),
        ("will renew cut from Nobody will renew", "Customers will renew next quarter.", "will renew next quarter",
         "Maren: Nobody will renew next quarter."),
        ("a negated statement", "We will hit the hiring plan.", "We will not hit the hiring plan.",
         "Maren: We will not hit the hiring plan."),
        ("an unrelated real sentence", "We will double the sales team.", "Both engineering hires started this week.",
         None),
        ("'ok' in chat", "We will double the sales team.", "ok", "CHAT"),
        ("the item typed in chat", "We will double the sales team.", "We will double the sales team.", "CHAT"),
        ("a customer's sentence passed off as the founder's", "We will roll the calendar out to every site.",
         "We will roll the calendar out to every site.", "Head of Logistics: We will roll the calendar out to every site."),
        ("an ellipsis-stitched excerpt", "We will open the portal in November.", "We will open … in November.", None),
        ("an excerpt across two speakers", "We will ship SSO next quarter.", "We will ship SSO. Next quarter.",
         "Maren: We will ship SSO.\nThem: Next quarter."),
        ("a paraphrase with every word kept", "In November we will open the carrier portal to all customers.", NOV, None)]:
    if said == "CHAT":
        b = founder(item_text, excerpt, mid=None)
    else:
        b = founder(item_text, excerpt, said)
    absent("founder statement does not unlock: " + name, b, [item_text.rstrip(".?")], renders=INVESTOR)
present("the founder's own sentence still unlocks (control)", founder(NOV, NOV), NOV.rstrip("."))
present("case and a dropped full stop are ignored (control)",
        founder("we will open the carrier portal to all customers in november", NOV), "carrier portal to all customers in november")

# founder statements: smuggling other things through the founder's own sentence
for name, item_text, said, needles in [
        ("a figure", "We will reach 60k MRR in December.", "Maren: We will reach 60k MRR in December.", ["60k"]),
        ("a non-public name", "Quorrelind will sign the annual contract next month.",
         "Maren: Quorrelind will sign the annual contract next month.", NAMES),
        ("confidential wording", "We will sign the term sheet for the bridge in October.",
         "Maren: We will sign the term sheet for the bridge in October.", ["term sheet"]),
        ("a conditional in the second sentence", "We will hire two engineers.",
         "Maren: We will hire two engineers. If revenue holds, we will hire four.", ["we will hire two engineers"]),
        ("a mid-line label (X2)", "We will renew next quarter.", "Maren: will we renew Riley: we will renew next quarter.",
         ["we will renew next quarter"]),
        ("a note heading posing as the speaker", "We will ship SSO in Q4.", "Decision: We will ship SSO in Q4.",
         ["we will ship sso"])]:
    excerpt = item_text if name != "a conditional in the second sentence" else "We will hire two engineers. If revenue holds, we will hire four."
    speaker = "Decision" if "heading" in name else "Maren"
    absent("founder sentence does not smuggle " + name, founder(item_text, excerpt, said, speaker=speaker), needles,
           renders=INVESTOR)
b = founder("{org:quorrelind} will sign the annual contract next month.",
            "Quorrelind will sign the annual contract next month.",
            "Maren: Quorrelind will sign the annual contract next month.")
absent("a non-public placeholder can't stand in for the founder's sentence", b, NAMES + ["will sign the annual contract"],
       renders=INVESTOR)
present("a public placeholder filled in equals the founder's sentence (control)",
        founder("{org:tamberlind} will move every warehouse onto the calendar by December.",
                "Tamberlind Foods will move every warehouse onto the calendar by December.",
                "Maren: Tamberlind Foods will move every warehouse onto the calendar by December."),
        "tamberlind foods will move every warehouse")
b = copy.deepcopy(SAMPLE)
meeting(b, "sample-iu3")["transcript_text"] = "Me: We will publish the security whitepaper in October."
b["items"].append({"section": "product", "text": "We will publish the security whitepaper in October.",
                   "citations": [{"meeting_id": "sample-iu3"}],
                   "founder_stated": {"meeting_id": "sample-iu3", "speaker": "Me",
                                      "excerpt": "\u201cWe will publish the security whitepaper in October.\u201d"}})
present("an excerpt from the transcript in curly quotes with the Me label (control)", b, "security whitepaper in october")
b = copy.deepcopy(SAMPLE)
meeting(b, "sample-iu3")["notes_text"] += "\nZZSECRETNOTE: the meeting text itself is never printed."
absent("meeting text is never printed", b, ["zzsecretnote", "never printed"], anywhere=True)
b = copy.deepcopy(SAMPLE)
meeting(b, "sample-iu1")["notes_text"] = "Head of Logistics: Call me on +44 20 7946 0958 about the rollout."
next(i for i in b["items"] if i.get("quote") and i["quote"]["permission"])["quote"]["text"] = "Call me on +44 20 7946 0958 about the rollout."
absent("a phone number inside a checked quote is redacted", b, ["7946 0958", "7946"], anywhere=True)

# quotes (X1)
for said, cut in [("None of our drivers trust the dock calendar.", "our drivers trust the dock calendar"),
                  ("Nobody will renew next quarter.", "will renew next quarter"),
                  ("I would not pay for this.", "I would ... pay for this"),
                  ("The dock calendar is unhelpful for my team.", "helpful for my team"),
                  ("The dock calendar is the first thing my team opens every morning.", "The dock calendar is the first thing")]:
    b = copy.deepcopy(SAMPLE)
    meeting(b, "sample-iu1")["notes_text"] = "Head of Logistics: " + said
    next(i for i in b["items"] if i.get("quote") and i["quote"]["permission"])["quote"]["text"] = cut
    absent("partial or reversed quote is not printed: {!r}".format(cut), b, ["“" + cut.lower(), "“" + cut.lower()])
b = copy.deepcopy(SAMPLE)
next(i for i in b["items"] if i.get("quote") and not i["quote"]["permission"])["quote"]["text"] = "We stopped chasing carriers by phone."
absent("quote without permission is not printed", b, ["stopped chasing carriers"])

# ---------------------------------------------------------------- injection
LINKY = re.compile(r"(?<!\\)\]\((?!https://notes\.granola\.ai/d/sample-|https://mail\.example/sample)|<script|<!channel>|"
                   r"(?<!\\)://(?!notes\.granola\.ai/d/sample-|mail\.example/sample)|(?<![\\\w])www\.|(?<!\\)\$[^$\n]+(?<!\\)\$|"
                   r"(?<!\\)==|javascript(?<!\\):|evil\.example")
for name, text in [("markdown link", "See [our deck](https://evil.example/x) for details."),
                   ("raw HTML", "Shipped <script>alert(1)</script> fixes."),
                   ("Slack-style mention", "Ping <!channel> about the launch."),
                   ("autolink", "Docs at https://evil.example/a and www.evil.example."),
                   ("bare domain", "Docs moved to evil.example/docs."),
                   ("math", "Prices from $5 to $9 per dock."),
                   ("highlight", "Shipped ==dock analytics== to all."),
                   ("javascript scheme", "Click javascript:alert(1) now."),
                   ("image", "![x](https://evil.example/p.png) shipped."),
                   ("heading via newline", "Shipped it.\n## Fake heading"),
                   ("table break", "Shipped | the | portal."),
                   ("bidi override", "Shipped the ‮portal‬ today.")]:
    b = with_item(text)
    bad = None
    for aud, fmt in RENDERS:
        code, out, err = run(b, aud, fmt)
        if code != 0 or "Traceback" in err:
            bad = "exit {} {}".format(code, err[-200:])
            break
        texts = bodies(out, fmt) if fmt == "json" else [out]
        for t in texts:
            m = LINKY.search(t)
            if m or "\n## Fake" in t or "‮" in t or re.search(r"(?<!\\)\| the (?<!\\)\|", t):
                bad = "{}/{}: {!r}".format(aud, fmt, (m.group(0) if m else "structure"))
                break
        if bad:
            break
    report("injection is inert in every render: " + name, bad is None, bad)
b = copy.deepcopy(SAMPLE)
meeting(b, "sample-iu8")["title"] = "Retro [x](https://evil.example) <b>bold</b> ==hl=="
code, out, err = run(b)
report("injection in a meeting title is inert", code == 0 and not LINKY.search(out) and "<b>" not in out, out[-300:])
for text, needle in [("Email ops@tamberlind.example for access.", "ops@tamberlind.example"),
                     ("Call +44 20 7946 0958 for the beta.", "7946 0958")]:
    absent("contact detail is redacted: " + needle, with_item(text), [needle], anywhere=True)

n_ok = sum(1 for _, g in results if g)
print("ATTACKS {}/{} held".format(n_ok, len(results)))
sys.exit(0 if n_ok == len(results) else 1)
PY
TMPDIR="$TMP" "$PY" "$TMP/attack.py" "$PWD" >"$TMP/attack" 2>&1 || { grep -v "^ok " "$TMP/attack" | head -40; fail "blind attack"; }
while IFS= read -r line; do ok "blind attack: ${line#ok   }"; done < <(grep "^ok " "$TMP/attack")

cat >"$TMP/fz.py" <<'PY'
"""Fuzzers (run in-process). Modes: inject, inject-one, names, mutate."""
import contextlib, copy, io, json, os, re, sys, tempfile, unicodedata
sys.path.insert(0, os.getcwd())
import build_update as h
from pathlib import Path

ASOF = "2026-10-02T09:00:00-04:00"
SAMPLE = json.load(open("sample_bundle.json", encoding="utf-8"))
TMPD = tempfile.mkdtemp()
CSV = [Path("sample_metrics.csv")]
RENDERS = [("investors", "draft"), ("investors", "email"), ("investors", "json"), ("board", "draft"), ("board", "email")]
FAST = [("investors", "draft"), ("investors", "email"), ("board", "draft")]
PAYLOADS = [
    r"\[click\](https://phish.example/a)", "[x](https://phish.example/b)", "<https://phish.example/c>",
    "<https://phish.example/d|click>", "<!channel> <!here> <@U123> <#C123>", "<img src=x onerror=alert(1)>",
    "*bold* _it_ ~st~ `code`", r"\\\[y\\\](https://phish.example/e)", "[ref]: https://phish.example/f [ref]",
    r"\<https://phish.example/h\>", "![i](https://phish.example/i.png)", r"a | b \| c",
    '<a href="https://phish.example/j">x</a>', "[x]<https://phish.example/k>", "&lt;https://phish.example/g&gt; &#60;b&#62;",
    "x\\", "**[z](https://phish.example/z)**", "# heading\n- list",
    "cost $5 to $9 and hi", "$x$ ==hl== a=b", "https://phish.example/l www.phish.example phish.example/m",
    r"\$y\$ \==z\== \https://phish.example/n",
]
HEADINGS = {"TL;DR", "Key metrics", "Highlights", "Lowlights", "Product", "Customers & go-to-market", "Team & hiring", "Asks",
            "What we said last time vs what happened", "Board only — do not forward", "Before you send (delete this section)"}
SKIP_KEYS = {"meeting_id", "url", "commitment_id", "key", "period", "date", "as_of", "sent_on", "metric", "currency",
             "section", "kind", "transcript", "status", "op", "confidential", "id", "notes_text", "transcript_text"}


def strings(obj):
    stack, out = [obj], []
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, list):
            stack.extend(item)
    return "\n".join(sorted(out))


def build(bundle, audience, fmt):
    path = os.path.join(TMPD, "b.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(bundle, fh, ensure_ascii=False)
    try:
        text = h.build(Path(path), CSV, h.DEFAULT_CONFIG, ASOF, audience, fmt)
    except h.InputError as exc:
        return exc.code, ""
    if fmt == "json":
        d = json.loads(text)
        # the two markdown bodies as they are; every other string value indented, as it is a JSON field, not markdown
        text = "\n".join([d.pop("markdown"), d.pop("email_body")] + ["  " + v for v in strings(d).split("\n")])
    return 0, text


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


def value_at(path):
    t = SAMPLE
    for p in path:
        t = t[p]
    return t


def text_paths(entities=True):
    return [p for p in leaves(SAMPLE) if p[-1] not in SKIP_KEYS and (entities or p[0] != "entities")
            and not (p[-1] == "source" and value_at(p) in ("user", "metric", "document"))]


def neutral(payload):
    return re.sub(r"[^A-Za-z0-9\s]", " ", payload)


def unescaped(text, chars):
    out, i = [], 0
    while i < len(text):
        c = text[i]
        if c == "\\":
            i += 2
            continue
        if c in chars:
            out.append(c)
        i += 1
    return out


def skeleton(text):
    live = unescaped(text, "[]<>*_~`|$=")
    live += ["](" for _ in re.finditer(r"(?<!\\)(?:\\\\)*\]\(", text)]
    return sorted(live)


def problems(text):
    bad = []
    if "<http" in text or re.search(r"<[A-Za-z/!?@#]", text):
        bad.append("raw < tag, autolink or mention")
    if re.search(r"&(?!amp;|lt;|gt;)#?[A-Za-z0-9]+;", text):
        bad.append("live HTML entity from input")
    for m in re.finditer(r"\]\(([^)\s]*)", text):
        pre = text[:m.start()]
        if (len(pre) - len(pre.rstrip("\\"))) % 2 == 0 and "phish" in m.group(1):
            bad.append("working markdown link to " + m.group(1))
    if re.search(r"(?m)^\s*\[[^\]\\]*\]:", text):
        bad.append("link reference definition")
    plain = re.sub(r"\]\([^)\s]*\)", "]", text)  # link targets are built by the script from checked URLs
    if re.search(r"(?<!\\)(?:\\\\)*\$", plain):
        bad.append("live $ (math)")
    if re.search(r"(?<!\\)(?:\\\\)*==", plain):
        bad.append("live == (highlight)")
    if re.search(r"(?<!\\)://|(?<![\\\w])www\.|phish\.example", plain):
        bad.append("autolinkable URL, www. or domain")
    for line in text.split("\n"):
        if line.startswith("#") and not (line.startswith("# ") and " update — " in line) and line[3:] not in HEADINGS:
            bad.append("heading from input: " + line[:60])
    return bad


def check(bp, bn, label, failures, renders=RENDERS):
    ran = 0
    for aud, fmt in renders:
        cp, tp = build(bp, aud, fmt)
        if cp != 0:
            continue
        cn, tn = build(bn, aud, fmt)
        probs = problems(tp)
        if cn == 0 and skeleton(tp) != skeleton(tn):
            probs.append("input added markup")
        failures.extend("{} {}/{}: {}".format(label, aud, fmt, p) for p in probs)
        ran += 1
    return ran


NAME = "quorrelind"
VARIANTS = ["QUORRELIND", "Qu\u00f6rrelind", "Quor\u200brelind", "Q u o r r e l i n d", "Qu\u043errelind",
            "\uff31\uff55\uff4f\uff52\uff52\uff45\uff4c\uff49\uff4e\uff44", "Jordan Pike"]
TARGETS = ["quorrelind", "ostermoss", "jordanpike", "averylund"]


def reader_fold(text):
    """An independent reader's view: NFKC, no invisible characters, casefold, Cyrillic o -> o, letters only."""
    t = unicodedata.normalize("NFKC", text)
    t = "".join(ch for ch in t if unicodedata.category(ch) != "Cf").casefold().replace("\u043e", "o")
    t = unicodedata.normalize("NFKD", t)
    return "".join(ch for ch in t if ch.isalnum() and not unicodedata.combining(ch))


mode = sys.argv[1]
failures = []
if mode == "inject":
    for i, payload in enumerate(PAYLOADS):
        bp, bn = copy.deepcopy(SAMPLE), copy.deepcopy(SAMPLE)
        for path in text_paths(entities=False):
            bp, bn = put(bp, path, payload), put(bn, path, neutral(payload))
        if check(bp, bn, "all fields = payload {}".format(i), failures) != len(RENDERS):
            failures.append("all fields = payload {}: a render failed".format(i))
    print("payloads", len(PAYLOADS), "renders", len(RENDERS))
elif mode == "inject-one":
    picks = [int(x) for x in sys.argv[2].split(",")]
    runs = 0
    for i in picks:
        for path in text_paths():
            runs += check(put(SAMPLE, path, "ok " + PAYLOADS[i]), put(SAMPLE, path, "ok " + neutral(PAYLOADS[i])),
                          "payload {} at {}".format(i, path), failures, FAST)
    print("renders", runs)
elif mode == "names":
    runs = refused = 0
    for path in list(leaves(SAMPLE)):
        for v in VARIANTS:
            original = SAMPLE
            for p in path:
                original = original[p]
            b = put(SAMPLE, path, "{} {}".format(original, v))
            for aud, fmt in (("investors", "json"), ("board", "json")):
                code, text = build(b, aud, fmt)
                runs += 1
                if code != 0:
                    refused += 1
                    if code not in (2, 3):
                        failures.append("exit {} for {} at {}".format(code, v, path))
                    continue
                for line in text.split("\n"):
                    folded = reader_fold(line)
                    for target in TARGETS:
                        if target in folded:
                            failures.append("{!r} at {} printed in {}/{}: {}".format(v, path, aud, fmt, line[:120]))
    print("runs", runs, "refused or rejected", refused)
elif mode == "mutate":
    def paths(node, prefix=()):
        yield prefix
        if isinstance(node, dict):
            for k in node:
                yield from paths(node[k], prefix + (k,))
        elif isinstance(node, list):
            for i, v in enumerate(node):
                yield from paths(v, prefix + (i,))
    MUTATIONS = [None, 7, 1.5, "x", "", [], {}, True, KeyError, "A" * 3000, "\u202e\x00<b>", "Quorrelind"]
    runs = exits = 0
    for path in list(paths(SAMPLE))[1:]:
        for value in MUTATIONS:
            b = copy.deepcopy(SAMPLE)
            parent = b
            for key in path[:-1]:
                parent = parent[key]
            if value is KeyError:
                del parent[path[-1]]
            else:
                parent[path[-1]] = copy.deepcopy(value)
            for aud, fmt in ((("investors", "draft"),) if len(path) % 2 else (("board", "json"),)):
                runs += 1
                p = os.path.join(TMPD, "m.json")
                json.dump(b, open(p, "w", encoding="utf-8"), ensure_ascii=False)
                out, err = io.StringIO(), io.StringIO()
                try:
                    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                        code = h.main(["--bundle", p, "--metrics-csv", "sample_metrics.csv", "--as-of", ASOF,
                                       "--audience", aud, "--format", fmt])
                except SystemExit as exc:
                    code = exc.code
                except Exception as exc:  # any traceback is a failure
                    failures.append("CRASH {} = {!r}: {!r}".format(path, value, exc))
                    continue
                if code not in (0, 2, 3):
                    failures.append("exit {} for {} = {!r}".format(code, path, value))
                if code != 0:
                    exits += 1
                    if out.getvalue():
                        failures.append("printed before exit {}: {} = {!r}".format(code, path, value))
                if "Traceback" in err.getvalue():
                    failures.append("traceback text for {} = {!r}".format(path, value))
    print("runs", runs, "non-zero", exits)
elif mode == "quotes":
    # reversed, cross-turn, labelled and cross-line quotes (cases_q) and founder excerpts (cases_f: forward-looking
    # items whose only support is the excerpt) never reach any render
    cases_q, cases_f = [], []
    for word, neg in [("helpful", "unhelpful"), ("satisfied", "dissatisfied"), ("happy", "unhappy"), ("able", "unable"),
                      ("reliable", "unreliable"), ("clear", "unclear"), ("aware", "unaware"), ("likely", "unlikely")]:
        cases_q.append(("Lead: The portal is {} for my team.".format(neg), "{} for my team".format(word)))
        cases_q.append(("Lead: The portal is *{}* for my team\\.".format(neg), "{} for my team.".format(word)))
        cases_f.append(("Maren: The portal will be {} for my team next quarter.".format(neg),
                        "{} for my team next quarter".format(word.capitalize())))
    for label in ["Customer", "System audio (Customer)", "Them", "Speaker 2", "Head of Logistics", "Ops lead"]:
        cases_q.append(("Rep: Do you keep the portal? {}: We do not keep it.".format(label),
                        "Do you keep the portal? {}: We do".format(label)))
        cases_q.append(("Rep: Is the portal useful?\n{}: Not for my team.".format(label), "useful? Not for my team"))
        cases_f.append(("Rep: Will you keep the portal next quarter? {}: We will not keep it.".format(label),
                        "Will you keep the portal next quarter? {}: We will".format(label)))
        cases_f.append(("Rep: Will you keep the portal?\n{}: Next quarter, for my team.".format(label),
                        "Will you keep the portal? Next quarter, for my team"))
    cases_q.append(("- The portal is great\n- for nobody on my team", "great for nobody on my team"))
    cases_f.append(("- We will keep the portal\n- next quarter for nobody", "We will keep the portal next quarter"))
    cases_f.append(("Maren: Nobody will renew next quarter.", "Customers will renew next quarter"))
    runs = 0
    for kind, cases in (("quote", cases_q), ("founder", cases_f)):
        for notes, quote in cases:
            for where in ("notes_text", "transcript_text"):
                b = copy.deepcopy(SAMPLE)
                m = next(x for x in b["meetings"] if x["meeting_id"] == "sample-iu1")
                m.pop("notes_text", None)
                m[where] = notes
                if kind == "quote":
                    it = next(i for i in b["items"] if i.get("quote") and i["quote"]["meeting_id"] == "sample-iu1")
                    it["quote"]["text"] = quote
                    needle = "\u201c" + h.md(quote)
                else:
                    b["items"].append({"section": "product", "text": quote + ".", "citations": [{"meeting_id": "sample-iu1"}],
                                       "founder_stated": {"meeting_id": "sample-iu1", "excerpt": quote}})
                    needle = "- " + h.md(quote + ".")
                for aud, fmt in RENDERS:
                    code, text = build(b, aud, fmt)
                    runs += 1
                    if code != 0 or needle in text.split("## Before you send")[0]:
                        failures.append("{} {!r} vs {!r} ({}) printed or failed in {}/{}".format(kind, quote, notes, where,
                                                                                               aud, fmt))
    print("cases", 2 * (len(cases_q) + len(cases_f)), "renders", runs)
for f in failures[:25]:
    print("FAIL:", f)
print("failures", len(failures))
sys.exit(1 if failures else 0)
PY
"$PY" "$TMP/fz.py" inject >"$TMP/fz1" 2>&1 || { tail -30 "$TMP/fz1"; fail "injection fuzz (every text field at once)"; }
ok "injection fuzz: 22 payloads (incl. \$…\$, ==…==, URLs) in every text field at once x 5 renders: no link, autolink, raw HTML, entity, math, highlight or formatting from input"
"$PY" "$TMP/fz.py" inject-one 0,5,6,18,20 >"$TMP/fz2" 2>&1 || { tail -30 "$TMP/fz2"; fail "injection fuzz (one field at a time)"; }
ok "injection fuzz: 5 payloads in each text field alone, 3 renders ($(head -1 "$TMP/fz2")): no markup from input"
"$PY" "$TMP/fz.py" quotes >"$TMP/fz5" 2>&1 || { tail -30 "$TMP/fz5"; fail "quote-reversal fuzz"; }
ok "quote fuzz: reversed (un-/dis-), cross-turn, labelled and cross-line quotes and founder excerpts, in notes and transcript text ($(head -1 "$TMP/fz5")): never printed"
"$PY" "$TMP/fz.py" names >"$TMP/fz3" 2>&1 || { tail -30 "$TMP/fz3"; fail "name-leak fuzz"; }
ok "name-leak fuzz: 7 spellings of non-public names appended to every string field, investor and board JSON (draft + email) ($(head -1 "$TMP/fz3")): never printed"
"$PY" "$TMP/fz.py" mutate >"$TMP/fz4" 2>&1 || { tail -30 "$TMP/fz4"; fail "mutation fuzz"; }
ok "mutation fuzz: 12 values (null, wrong types, missing, huge, control chars, a name) in every field ($(head -1 "$TMP/fz4")): exit 0/2/3, no traceback, nothing printed on error"

# ---------------------------------------------------------------- date and period parity corpus (same answers on every Python)
"$PY" - >"$TMP/corpus" <<'PY' || fail "date corpus"
import build_update as h
class E(Exception):
    pass
good_days = ["2026-09-30", "1900-01-01", "2999-12-31", "2024-02-29", "2026-09-30T23:59:59Z", "2026-09-30T00:00:00.123+14:00",
             "2026-09-30T10:00:00.123456-13:59", "2026-09-30T10:00+05:30"]
bad_days = ["2026-02-29", "2026-13-01", "2026-00-10", "2026-09-31", "20260930", "2026-W40-1", "2026-273", "2026-09-30T24:00:00Z",
            "2026-09-30T10:00:00+14:01", "2026-09-30T10:00:00+00:60", "2026-09-30T10:60:00Z", "2026-09-30T10:00:60Z",
            "2026-09-30T10:00:00", "2026-09-30 10:00:00Z", "2026-09-30T10Z", "2026-09-30T1000Z", "2026-09-30T10:00:00.1Z",
            "2026-09-30T10:00:00,123Z", "1899-12-31", "3000-01-01", "\uff12\uff10\uff12\uff16-09-30", "\u0662\u0660\u0662\u0666-09-30",
            "2026-09-30Z", " 2026-09-30", "2026-9-30", "+2026-09-30", "2026-09-30T10:00:00+0530", "2026-09-30T10:00:00+05"]
for v in good_days:
    h.parse_day(v, E)
for v in bad_days:
    try:
        h.parse_day(v, E)
    except E:
        continue
    raise SystemExit("accepted bad date " + repr(v))
good_periods = ["2026-01", "2026-12", "2026-Q1", "2026-Q4", "1901-01", "2998-Q4"]
bad_periods = ["2026-1", "2026-00", "2026-13", "2026-Q0", "2026-Q5", "2026-q3", "2026Q3", "2026-W40", "1900-12", "2999-01", "26-09", ""]
for v in good_periods:
    h.parse_period(v, E)
for v in bad_periods:
    try:
        h.parse_period(v, E)
    except E:
        continue
    raise SystemExit("accepted bad period " + repr(v))
print(len(good_days) + len(bad_days) + len(good_periods) + len(bad_periods))
PY
ok "date/period corpus ($(cat "$TMP/corpus") strings): the expected accept/reject on this Python"

# ---------------------------------------------------------------- output robustness
set +e
"$PY" "$SCRIPT" --bundle sample_bundle.json "${BASE[@]}" >&- 2>"$TMP/closed.err"
code=$?
set -e
[[ "$code" == 6 ]] && ! grep -q Traceback "$TMP/closed.err" && grep -q "Nothing was sent anywhere" "$TMP/closed.err" || { cat "$TMP/closed.err"; fail "closed stdout: exit $code"; }
ok "closed stdout exits 6 without a traceback"
set +e
"$PY" "$SCRIPT" --bundle sample_bundle.json "${BASE[@]}" 2>"$TMP/pipe.err" | head -1 >/dev/null
set -e
! grep -q Traceback "$TMP/pipe.err" || { cat "$TMP/pipe.err"; fail "broken pipe traceback"; }
ok "broken pipe exits without a traceback"
PYTHONIOENCODING=ascii LC_ALL=C "$PY" "$SCRIPT" --bundle sample_bundle.json "${BASE[@]}" >"$TMP/ascii.out" 2>"$TMP/ascii.err" || { cat "$TMP/ascii.err"; fail "ascii locale"; }
cmp -s "$TMP/ascii.out" "$TMP/draft" || fail "ascii locale output differs"
ok "an ASCII-only locale still prints the same UTF-8 draft"

# ---------------------------------------------------------------- docs and package consistency
"$PY" - <<'PY' || fail "SKILL.md frontmatter"
import re
t = open("SKILL.md", encoding="utf-8").read()
m = re.match(r"---\nname: investor-update-drafter\ndescription: ([^\n|>][^\n]*)\n---\n", t)
assert m, "frontmatter must be exactly name + one-line description"
assert not m.group(1).startswith(("|", ">")) and len(m.group(1)) < 1024
PY
ok "SKILL.md frontmatter: exact name, one-line description, no block scalar"
"$PY" - <<'PY' || fail "docs mention only real config keys and bundle keys"
import json, re
import build_update as h
cfg = json.load(open("update.json", encoding="utf-8"))
docs = open("SKILL.md", encoding="utf-8").read() + open("DATA_CONTRACT.md", encoding="utf-8").read()
for key in h.CONFIG_KEYS:
    assert "`{}`".format(key) in docs, "config key not documented: " + key
for keys in (h.BUNDLE_KEYS, h.ITEM_KEYS, h.MEETING_KEYS, h.ENTITY_KEYS, h.OBS_KEYS, h.QUOTE_KEYS, h.STATED_KEYS, h.PREV_KEYS,
             h.COMMIT_KEYS, h.TARGET_KEYS, h.REVIEW_KEYS, h.CITATION_KEYS, h.REPORTED_KEYS):
    for key in keys:
        assert "`{}`".format(key) in docs, "bundle key not documented: " + key
for flag in ["--bundle", "--metrics-csv", "--config", "--as-of", "--audience", "--format"]:
    assert flag in docs, flag
for m in re.finditer(r"`(--[a-z-]+)`", docs):
    assert m.group(1) in ("--bundle", "--metrics-csv", "--config", "--as-of", "--audience", "--format"), m.group(1)
PY
ok "every config key, bundle key and flag is documented, and the docs name no other flags"
"$PY" - <<'PY' || fail "README file table"
import os, re
t = open("README.md", encoding="utf-8").read()
named = set(re.findall(r"`([A-Za-z0-9_.-]+\.(?:md|py|json|csv|sh|txt))`", t))
for f in named - {"investor-update-drafter-settings.md"}:  # the user's settings file is not shipped
    assert os.path.isfile(f), f
shipped = {f for f in os.listdir(".") if os.path.isfile(f) and f != "_listing.json"}
assert shipped <= named | {"README.md"}, shipped - named
PY
ok "README names every shipped file and every file it names exists"
[[ ! -d __pycache__ ]] || fail "__pycache__ in the skill folder"
ok "no __pycache__ in the skill folder"
"$PY" - <<'PY' || fail "package size"
import os
files = [f for f in os.listdir(".") if os.path.isfile(f) and f != "_listing.json"]
sizes = [os.path.getsize(f) for f in files]
# round 10: the package-internal total cap went from 512 KB to 768 KB (the r10 regression cases and the r10 checks
# did not fit in 512 KB); the platform limit is 8 MiB per package (evidence-r9/library-skills.ts)
assert len(files) <= 40 and max(sizes) <= 256 * 1024 and sum(sizes) <= 768 * 1024, (len(files), max(sizes), sum(sizes))
PY
ok "size limits: at most 40 files, 256 KB per file, 768 KB in total"
"$PY" - <<'PY' || fail "sample names"
import json, re
text = "".join(open(f, encoding="utf-8").read() for f in ["sample_bundle.json", "sample_metrics.csv"])
for email in re.findall(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", text):
    assert email.endswith(".example"), email
for url in re.findall(r"https?://[^\s\"]+", text):
    assert "sample" in url and (".example" in url or "notes.granola.ai/d/sample-" in url), url
PY
ok "sample data uses .example addresses and sample Granola links only"
if [[ -f _listing.json ]]; then
  "$PY" - <<'PY' || fail "_listing.json sampleOutput differs from section 1 of run.sh output"
import json
listing = json.load(open("_listing.json", encoding="utf-8"))
run = open("expected_output.txt", encoding="utf-8").read().split("\n", 1)[1].split("\n\n=== 2.", 1)[0]
assert listing["sampleOutput"] == run + "\n\n_Produced by running `bash run.sh` on the fictional sample data bundled with this skill._"
assert listing["slug"] == "investor-update-drafter" and listing["roles"] == ["finance-operations"]
assert listing["businessTypes"] == ["b2b", "dtc"] and len(listing["benefits"]) == 5
PY
  ok "_listing.json sampleOutput matches run.sh section 1, plus the Produced-by footer"
fi

"$PY" test_founder_context.py >"$TMP/founder-context" || { cat "$TMP/founder-context"; fail "R8 founder final-output regressions"; }
ok "R8: 51 founder context/attribution final-output regressions and retained controls"
"$PY" test_founder_context_r9.py >"$TMP/founder-context-r9" || { cat "$TMP/founder-context-r9"; fail "R9 founder-context regressions"; }
grep -q "^PASS: 418 round-9 founder-context cases and 4 safe twins" "$TMP/founder-context-r9" || { cat "$TMP/founder-context-r9"; fail "R9 founder-context case count"; }
ok "R9: 418 founder-context cases (163 disclosed QA r8 strings, 255 owner tuning cases) and 4 safe twins, each in JSON email_body, JSON markdown, --format draft and --format email"
"$PY" test_founder_context_r10.py >"$TMP/founder-context-r10" || { cat "$TMP/founder-context-r10"; fail "R10 founder-context regressions"; }
grep -q "^PASS: 238 round-10 founder-context cases and 168 eligible twins" "$TMP/founder-context-r10" || { cat "$TMP/founder-context-r10"; fail "R10 founder-context case count"; }
ok "R10: 238 founder-context cases (111 QA r9 IV9 repro strings and IV9-8 controls, 127 owner fresh cases incl. 8 pinned over-withheld controls) and 168 eligible twins, each in JSON email_body, JSON markdown, --format draft and --audience board --format email"

"$PY" test_founder_context_r12.py
ok "R12: positive founder grammar in four output representations"

echo "PASS $PASS/$PASS"

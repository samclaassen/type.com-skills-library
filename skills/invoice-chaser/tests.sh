#!/usr/bin/env bash
# Run from the skill directory: bash tests.sh
# Negative and guardrail tests against the bundled FICTIONAL sample data.
# Everything runs on temp copies; the bundled sample files are never modified.
set -euo pipefail

S="python3 invoice.py"
CFG=sample_config.json
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
pass=0

ok() { pass=$((pass + 1)); echo "ok $pass - $1"; }
die() { echo "FAIL - $1"; exit 1; }

# expect_exit2 NAME PATTERN CMD...  -> command must exit 2 and stderr must contain PATTERN
expect_exit2() {
  local name="$1" pattern="$2"; shift 2
  local code=0
  "$@" >"$tmp/out" 2>"$tmp/err" || code=$?
  [ "$code" -eq 2 ] || die "$name: expected exit 2, got $code ($(cat "$tmp/err"))"
  grep -qF -- "$pattern" "$tmp/err" || die "$name: stderr lacks '$pattern': $(cat "$tmp/err")"
  ok "$name"
}
fresh() { cp sample_ledger.md "$tmp/ledger.md"; }
brief() { printf '%s\n' "$1" >"$tmp/brief.json"; }
cfg_with() { python3 -c "import json,sys; c=json.load(open('$CFG')); c.update(json.loads(sys.argv[1])); json.dump(c, open('$tmp/cfg.json','w'))" "$1"; }

fresh
# --- create: never guess amounts ------------------------------------------
brief '{"client":"Acme Corp","items":[{"description":"Workshop","qty":1}]}'
expect_exit2 "missing rate -> ask the user" "items[1].rate is missing. Ask the user" \
  $S create --brief "$tmp/brief.json" --ledger "$tmp/ledger.md" --config $CFG --as-of 2026-10-02
brief '{"client":"Acme Corp","items":[{"description":"Workshop","qty":1,"rate":0}]}'
expect_exit2 "zero rate rejected" "must be greater than zero" \
  $S create --brief "$tmp/brief.json" --ledger "$tmp/ledger.md" --config $CFG --as-of 2026-10-02
brief '{"client":"Acme Corp","items":[{"description":"Workshop","qty":1,"rate":-50}]}'
expect_exit2 "negative rate rejected" "must be greater than zero" \
  $S create --brief "$tmp/brief.json" --ledger "$tmp/ledger.md" --config $CFG --as-of 2026-10-02
brief '{"client":"Acme Corp","items":[]}'
expect_exit2 "no line items -> never invent" "never invent line items" \
  $S create --brief "$tmp/brief.json" --ledger "$tmp/ledger.md" --config $CFG --as-of 2026-10-02
brief '{"client":"Acme Corp","currency":"USD","items":[{"description":"A","qty":1,"rate":100},{"description":"B","qty":1,"rate":100,"currency":"EUR"}]}'
expect_exit2 "mixed currencies in one invoice" "cannot mix currencies" \
  $S create --brief "$tmp/brief.json" --ledger "$tmp/ledger.md" --config $CFG --as-of 2026-10-02
brief '{"client":"Brand New Client LLC","items":[{"description":"A","qty":1,"rate":100}]}'
expect_exit2 "new client without email -> ask" "Ask the user who should receive" \
  $S create --brief "$tmp/brief.json" --ledger "$tmp/ledger.md" --config $CFG --as-of 2026-10-02
expect_exit2 "bad --as-of" "--as-of: expected a YYYY-MM-DD date" \
  $S status --ledger "$tmp/ledger.md" --config $CFG --as-of 02/10/2026

# --- ledger is the source of truth: corrupted -> stop ---------------------
sed 's/| INV-2026-036 | Kestrel Outdoor GmbH |/| INV-2026-036 |/' sample_ledger.md >"$tmp/bad1.md"
expect_exit2 "ledger row with missing cell names the line" "line 12: expected 16 cells, found 15" \
  $S chase --ledger "$tmp/bad1.md" --config $CFG --as-of 2026-10-02
sed 's/| total | amount_paid |/| amount | amount_paid |/' sample_ledger.md >"$tmp/bad2.md"
expect_exit2 "ledger header drift" "header does not match the data contract" \
  $S status --ledger "$tmp/bad2.md" --config $CFG --as-of 2026-10-02
sed 's/| 2026-09-25 | USD | 8000.00 |/| 2026-09-31 | USD | 8000.00 |/' sample_ledger.md >"$tmp/bad3.md"
expect_exit2 "impossible due date" "not a real calendar date" \
  $S chase --ledger "$tmp/bad3.md" --config $CFG --as-of 2026-10-02
grep -q "ledger is unreadable — stop, don't guess." "$tmp/err" && ok "corrupt-ledger message says stop"

# --- secrets / legal-language guardrails in config ------------------------
cfg_with '{"bank_details":"ACME BANK 12345678"}'
expect_exit2 "bank_details key refused" "bank_details\` is not accepted" \
  $S status --ledger "$tmp/ledger.md" --config "$tmp/cfg.json" --as-of 2026-10-02
cfg_with '{"payment_instructions_ref":"https://pay.example/fernhill"}'
expect_exit2 "payment link in ref refused" "looks like actual payment details" \
  $S status --ledger "$tmp/ledger.md" --config "$tmp/cfg.json" --as-of 2026-10-02
python3 -c "import json; t=json.load(open('chase_templates.json')); t['tones']['final']['body'].append('A late fee of 5% now applies.'); json.dump(t, open('$tmp/tpl.json','w'))"
expect_exit2 "late-fee wording without late_fee config" "only allowed when late_fee is explicitly configured" \
  $S chase --ledger "$tmp/ledger.md" --config $CFG --templates "$tmp/tpl.json" --as-of 2026-10-02
cfg_with '{"billing_source":""}'
expect_exit2 "unset billing_source -> Step 0" "run Step 0" \
  $S status --ledger "$tmp/ledger.md" --config "$tmp/cfg.json" --as-of 2026-10-02

# --- default chase output: no late fees / legal language -------------------
$S chase --ledger "$tmp/ledger.md" --config $CFG --as-of 2026-10-02 >"$tmp/chase.txt"
if grep -qiE "late fee|interest|legal|collections|attorney|court" "$tmp/chase.txt"; then die "legal language leaked"; fi
ok "no late-fee or legal language by default"
cfg_with '{"late_fee":{"note":"As agreed in our contract, balances over 30 days past due incur a 1.5% monthly late fee.","from_tier":4}}'
$S chase --ledger "$tmp/ledger.md" --config "$tmp/cfg.json" --as-of 2026-10-02 >"$tmp/chase_lf.txt"
[ "$(grep -c '1.5% monthly late fee' "$tmp/chase_lf.txt")" -eq 1 ] || die "configured late fee should appear once (final tier only)"
ok "configured late-fee note appears only on the final-notice tier"

# --- tax ------------------------------------------------------------------
cfg_with '{"tax_rate":0.0825,"tax_label":"Sales tax"}'
brief '{"client":"Quill & Co","items":[{"description":"Illustration","qty":3.5,"rate":"112.35"}]}'
$S create --brief "$tmp/brief.json" --ledger "$tmp/ledger.md" --config "$tmp/cfg.json" --as-of 2026-10-02 >"$tmp/tax.txt"
# 3.5 x 112.35 = 393.225 -> 393.23 (half up); tax 8.25% = 32.441... -> 32.44; total 425.67
grep -qF '| 1 | Illustration | 3.5 | $112.35 | $393.23 |' "$tmp/tax.txt" || die "line rounding"
grep -qF '**Sales tax (8.25%):** $32.44' "$tmp/tax.txt" || die "tax line"
grep -qF '**Total:** $425.67' "$tmp/tax.txt" || die "tax total"
ok "fixed tax rate: half-up rounding per line and on tax"

# --- write path: sequence, mark-sent, partial + full payment ---------------
fresh
$S create --brief sample_brief.json --ledger "$tmp/ledger.md" --config $CFG --as-of 2026-10-02 --write >/dev/null
grep -q '^| INV-2026-042 | Acme Corp | Sarah Chen | sarah@acmecorp.example |.*| 8000.00 | 0.00 | draft |' "$tmp/ledger.md" || die "draft row not appended"
$S create --brief sample_brief.json --ledger "$tmp/ledger.md" --config $CFG --as-of 2026-10-02 >"$tmp/c2.txt"
grep -qF '#### Invoice INV-2026-043' "$tmp/c2.txt" || die "sequence did not advance"
ok "--write appends a draft row; next number is INV-2026-043"
$S create --brief sample_brief.json --ledger "$tmp/ledger.md" --config $CFG --as-of 2027-01-04 >"$tmp/c3.txt"
grep -qF '#### Invoice INV-2027-001' "$tmp/c3.txt" || die "sequence should restart per year"
ok "numbering restarts each year"
expect_exit2 "cannot pay a draft" "only sent or partial invoices" \
  $S mark-paid --ledger "$tmp/ledger.md" --config $CFG --number INV-2026-042 --date 2026-10-05
$S mark-sent --ledger "$tmp/ledger.md" --config $CFG --number INV-2026-042 --date 2026-10-02 >/dev/null
expect_exit2 "overpayment refused" "Overpayments need a human decision" \
  $S mark-paid --ledger "$tmp/ledger.md" --config $CFG --number INV-2026-042 --date 2026-10-10 --amount 9000
$S mark-paid --ledger "$tmp/ledger.md" --config $CFG --number INV-2026-042 --date 2026-10-10 --amount 3000 >"$tmp/p1.txt"
grep -qF 'Balance: $5,000 of $8,000' "$tmp/p1.txt" || die "partial balance"
grep -q '^| INV-2026-042 |.*| 8000.00 | 3000.00 | partial |' "$tmp/ledger.md" || die "partial status"
$S mark-paid --ledger "$tmp/ledger.md" --config $CFG --number INV-2026-042 --date 2026-10-20 >/dev/null
grep -q '^| INV-2026-042 |.*| 8000.00 | 8000.00 | paid | 2026-10-02 | 2026-10-20 |' "$tmp/ledger.md" || die "paid status"
ok "mark-sent, partial payment, then remaining balance -> paid"
head -3 "$tmp/ledger.md" | grep -q "FICTIONAL sample data" || die "preamble lost on rewrite"
ok "ledger preamble preserved on rewrite"

# --- chase --record: no repeat of the same tier -----------------------------
fresh
expect_exit2 "cannot record a skipped invoice" "no drafted follow-up in this run" \
  $S chase --ledger "$tmp/ledger.md" --config $CFG --as-of 2026-10-02 --record INV-2026-037
$S chase --ledger "$tmp/ledger.md" --config $CFG --as-of 2026-10-02 --record INV-2026-038 >/dev/null
$S chase --ledger "$tmp/ledger.md" --config $CFG --as-of 2026-10-09 >"$tmp/w2.txt"
grep -qF '| INV-2026-038 | Acme Corp | $8,000 | Sep 25 | 14 | 3 · escalation | tier 2 · Oct 2 | Draft below |' "$tmp/w2.txt" || die "week 2 should escalate to tier 3"
$S chase --ledger "$tmp/ledger.md" --config $CFG --as-of 2026-10-03 >"$tmp/w1.txt"
grep -qF '| INV-2026-038 | Acme Corp | $8,000 | Sep 25 | 8 | 2 · firmer follow-up | tier 2 · Oct 2 | Skip — tier 2 already sent; escalation due Oct 9 |' "$tmp/w1.txt" || die "same tier repeated"
ok "recorded tier is not repeated; next run moves up a tier"
$S chase --ledger "$tmp/ledger.md" --config $CFG --as-of 2026-10-02 --record INV-2026-034 >/dev/null
$S chase --ledger "$tmp/ledger.md" --config $CFG --as-of 2026-10-30 >"$tmp/w5.txt"
grep -qF 'All 4 scheduled reminders sent — needs a personal call or a decision' "$tmp/w5.txt" || die "exhausted tiers"
ok "after the final notice the skill stops drafting and hands back to a human"

# --- Stripe mode (read-only JSON) ------------------------------------------
cfg_with '{"billing_source":"stripe"}'
$S status --stripe-invoices sample_stripe_invoices.json --config "$tmp/cfg.json" --as-of 2026-10-02 >"$tmp/st.txt"
grep -qF '| FERN-0008 | Sakura Tea House KK | overdue · partial | Aug 25 | Sep 24 | ¥120,000 of ¥180,000 | 8 | — |' "$tmp/st.txt" || die "JPY zero-decimal conversion"
grep -qF '$2,400' "$tmp/st.txt" || die "USD minor-unit conversion"
ok "Stripe minor units converted (USD /100, JPY zero-decimal)"
$S chase --stripe-invoices sample_stripe_invoices.json --config "$tmp/cfg.json" --as-of 2026-10-02 --chase-log "$tmp/chase-log.md" --record FERN-0007 >/dev/null
$S chase --stripe-invoices sample_stripe_invoices.json --config "$tmp/cfg.json" --as-of 2026-10-03 --chase-log "$tmp/chase-log.md" >"$tmp/sc.txt"
grep -qF '| FERN-0007 | Bluefin Labs | $2,400 | Sep 14 | 19 | 3 · escalation | tier 3 · Oct 2 | Skip' "$tmp/sc.txt" || die "stripe chase log"
ok "Stripe mode keeps chase history in a local chase log"
expect_exit2 "--ledger with billing_source stripe" "billing_source is 'stripe' but --ledger was given" \
  $S chase --ledger "$tmp/ledger.md" --config "$tmp/cfg.json" --as-of 2026-10-02
expect_exit2 "create --write refused in stripe mode" "never creates Stripe invoices" \
  $S create --brief sample_brief.json --ledger "$tmp/ledger.md" --config "$tmp/cfg.json" --as-of 2026-10-02 --write
echo '[{"status":"open","number":"X-1","currency":"usd","amount_due":"100.00","amount_remaining":100}]' >"$tmp/bad_stripe.json"
expect_exit2 "Stripe amount must be integer minor units" "[0].amount_due: expected a non-negative integer" \
  $S chase --stripe-invoices "$tmp/bad_stripe.json" --config "$tmp/cfg.json" --as-of 2026-10-02

# --- regression: Stripe-mode create works without a ledger -----------------
cfg_with '{"billing_source":"stripe"}'
brief '{"client":"Acme Corp","contact_email":"sarah@acmecorp.example","items":[{"description":"Workshop","qty":2,"rate":1500}]}'
$S create --brief "$tmp/brief.json" --config "$tmp/cfg.json" --as-of 2026-10-02 >"$tmp/sc1.txt" || die "stripe create preview without a ledger failed"
grep -qF '#### Invoice (number assigned by Stripe)' "$tmp/sc1.txt" || die "stripe create: number placeholder"
grep -qF 'Stripe: nothing was written' "$tmp/sc1.txt" || die "stripe create: not-written note"
if grep -qF 'mark-sent' "$tmp/sc1.txt"; then die "stripe create should not suggest mark-sent"; fi
ok "stripe mode: create preview works with no ledger (Stripe assigns the number)"
brief '{"client":"Bluefin Labs","items":[{"description":"Sprint","qty":1,"rate":1200}]}'
$S create --brief "$tmp/brief.json" --config "$tmp/cfg.json" --stripe-invoices sample_stripe_invoices.json --as-of 2026-10-02 >"$tmp/sc2.txt"
grep -qF '#### Invoice FERN-0010' "$tmp/sc2.txt" || die "stripe next number from export"
grep -qF '**To:** Bluefin Labs — priya@bluefinlabs.example' "$tmp/sc2.txt" || die "stripe email auto-fill"
ok "stripe mode: next number and contact email derived from a Stripe export"
$S create --brief "$tmp/brief.json" --config "$tmp/cfg.json" --stripe-invoices sample_stripe_invoices.json --number FERN-0042 --as-of 2026-10-02 >"$tmp/sc3.txt"
grep -qF '#### Invoice FERN-0042' "$tmp/sc3.txt" || die "--number override"
ok "stripe mode: --number sets the shown number"
expect_exit2 "stripe create refuses a ledger" "create does not read a ledger" \
  $S create --brief "$tmp/brief.json" --ledger "$tmp/ledger.md" --config "$tmp/cfg.json" --as-of 2026-10-02
expect_exit2 "--number refused in manual mode" "only for billing_source: stripe" \
  $S create --brief sample_brief.json --ledger "$tmp/ledger.md" --config $CFG --number X-1 --as-of 2026-10-02

# --- regression: symbol-less currencies print their code once ---------------
printf '%s\n' '# t' '' \
  '| number | client | contact_name | contact_email | client_address | issue_date | terms_days | due_date | currency | total | amount_paid | status | sent_date | paid_date | last_chase_tier | last_chase_date |' \
  '|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|' \
  '| C-1 | Alpen AG | Jo Muster | jo@alpen.example |  | 2026-08-01 | 30 | 2026-08-31 | CHF | 234.56 | 0.00 | sent | 2026-08-01 |  | 0 |  |' \
  '| K-1 | Gulf Co | Sam Ali | sam@gulf.example |  | 2026-08-01 | 30 | 2026-08-31 | KWD | 1234.567 | 0.00 | sent | 2026-08-01 |  | 0 |  |' \
  '| U-1 | Acme Corp | Sarah Chen | sarah@acmecorp.example |  | 2026-08-01 | 30 | 2026-08-31 | USD | 100.00 | 0.00 | sent | 2026-08-01 |  | 0 |  |' >"$tmp/cur.md"
$S chase --ledger "$tmp/cur.md" --config $CFG --as-of 2026-10-02 >"$tmp/cur_chase.txt"
$S status --ledger "$tmp/cur.md" --config $CFG --as-of 2026-10-02 >"$tmp/cur_status.txt"
if grep -qE 'CHF CHF|KWD KWD' "$tmp/cur_chase.txt" "$tmp/cur_status.txt"; then die "currency code printed twice"; fi
grep -qF '234.56 CHF · 1,234.567 KWD · $100 USD' "$tmp/cur_chase.txt" || die "chase totals by currency"
grep -qF '**Outstanding:** 234.56 CHF · 1,234.567 KWD · $100 USD' "$tmp/cur_status.txt" || die "status totals by currency"
ok "totals by currency: no doubled code for symbol-less currencies"

# --- regression: account numbers split by separators are refused ------------
for ref in "acct 1234-5678" "acct 1234.5678" "acct 12/34/56/78" "GB29 NWBK 6016 1331 9268 19"; do
  cfg_with "{\"payment_instructions_ref\":\"$ref\"}"
  expect_exit2 "account-like ref refused: $ref" "looks like actual payment details" \
    $S status --ledger "$tmp/ledger.md" --config "$tmp/cfg.json" --as-of 2026-10-02
done

for ref in "12-34-56" "sort code 12-34-56" "acct no. 1234 5678" "acct 2026-10-02" "routing 021000021"; do
  cfg_with "{\"payment_instructions_ref\":\"$ref\"}"
  expect_exit2 "account-like ref refused: $ref" "looks like actual payment details" \
    $S status --ledger "$tmp/ledger.md" --config "$tmp/cfg.json" --as-of 2026-10-02
done

# --- regression: dates and financial-year ranges in a note name are fine ----
for ref in "Payments FY2024-2025" "Invoice note 2026-10-02" "Bank note 2026/10/02" "FY2024-25 payment details"; do
  cfg_with "{\"payment_instructions_ref\":\"$ref\"}"
  $S status --ledger "$tmp/ledger.md" --config "$tmp/cfg.json" --as-of 2026-10-02 >/dev/null 2>"$tmp/err" \
    || die "harmless note name refused: $ref ($(cat "$tmp/err"))"
  ok "date-like note name accepted: $ref"
done

# --- regression: banned terms match whole words only -----------------------
python3 -c "import json; t=json.load(open('chase_templates.json')); t['tones']['gentle']['body'].append('Let me know if you are interested in a call.'); json.dump(t, open('$tmp/tpl_ok.json','w'))"
$S chase --ledger "$tmp/ledger.md" --config $CFG --templates "$tmp/tpl_ok.json" --as-of 2026-10-02 >/dev/null || die "'interested' wrongly treated as 'interest'"
ok "'interested' is not a banned term"
for phrase in "Interest charges may apply." "We may add interest." "This may go to debt collectors."; do
  python3 -c "import json,sys; t=json.load(open('chase_templates.json')); t['tones']['final']['body'].append(sys.argv[1]); json.dump(t, open('$tmp/tpl_bad.json','w'))" "$phrase"
  expect_exit2 "banned term still caught: $phrase" "only allowed when late_fee is explicitly configured" \
    $S chase --ledger "$tmp/ledger.md" --config $CFG --templates "$tmp/tpl_bad.json" --as-of 2026-10-02
done

# --- regression: benign "interest" phrases pass; late-fee senses still refused --
for phrase in "Interest-free instalments are fine." "No interest, no fees." "A 0% interest plan works too." "You can pay in parts without interest."; do
  python3 -c "import json,sys; t=json.load(open('chase_templates.json')); t['tones']['final']['body'].append(sys.argv[1]); json.dump(t, open('$tmp/tpl_ok2.json','w'))" "$phrase"
  $S chase --ledger "$tmp/ledger.md" --config $CFG --templates "$tmp/tpl_ok2.json" --as-of 2026-10-02 >/dev/null 2>"$tmp/err" \
    || die "benign phrase refused: $phrase ($(cat "$tmp/err"))"
  ok "benign phrase allowed: $phrase"
done
for phrase in "Interest will be added." "Interest-free for now, then interest charges apply." "No fees, but without interest charges waived we add them."; do
  python3 -c "import json,sys; t=json.load(open('chase_templates.json')); t['tones']['final']['body'].append(sys.argv[1]); json.dump(t, open('$tmp/tpl_bad2.json','w'))" "$phrase"
  expect_exit2 "late-fee sense still caught: $phrase" "only allowed when late_fee is explicitly configured" \
    $S chase --ledger "$tmp/ledger.md" --config $CFG --templates "$tmp/tpl_bad2.json" --as-of 2026-10-02
done
cfg_with '{"allowed_phrases_without_late_fee":"interest-free"}'
expect_exit2 "allowed_phrases must be a list" "allowed_phrases_without_late_fee must be a list" \
  $S status --ledger "$tmp/ledger.md" --config "$tmp/cfg.json" --as-of 2026-10-02

# --- bundled sample ledger untouched --------------------------------
grep -q '^| INV-2026-038 |.*| sent | 2026-08-26 |  | 1 | 2026-09-26 |$' sample_ledger.md || die "sample ledger was modified"
ok "sample ledger untouched"

# --- shipped default config allows the same benign interest phrases as the sample
python3 -c 'import json,sys; a=json.load(open("invoice.json")).get("allowed_phrases_without_late_fee"); b=json.load(open("sample_config.json")).get("allowed_phrases_without_late_fee"); sys.exit(0 if a and a==b else 1)' || die "invoice.json allowed_phrases_without_late_fee missing or differs from sample_config.json"
ok "default invoice.json allows interest-free wording"

echo "ALL TESTS PASSED ($pass checks)"

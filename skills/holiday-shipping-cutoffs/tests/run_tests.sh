#!/usr/bin/env bash
# End-to-end checks for the Holiday Shipping Cutoff Checker. No network or credentials needed.
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
hasnt() { ! grep -qF -- "$2" "$1" || { echo "--- unexpected: $2"; fail "$3"; }; }
cc() { python3 scripts/check_cutoffs.py "$@"; }
C="$tmp"
D=examples/data
SAMPLE=(--account "Fernhill Goods" --carriers $D/carriers.csv --warehouse $D/warehouse.json
        --destinations $D/destinations.csv --promises $D/promises.csv --as-of 2026-10-07)

python3 tests/make_cases.py "$tmp"

# 1. Sample output is byte-identical to the published expected output, and stable across runs.
bash examples/run.sh > "$tmp/out.txt" 2> "$tmp/run.err" || { cat "$tmp/run.err"; fail "examples/run.sh exited non-zero"; }
cmp -s "$tmp/out.txt" examples/expected_output.txt || { diff "$tmp/out.txt" examples/expected_output.txt | head; fail "sample output differs"; }
bash examples/run.sh > "$tmp/out2.txt"
cmp -s "$tmp/out.txt" "$tmp/out2.txt" || fail "output is not deterministic"
ok "sample run matches examples/expected_output.txt"

# 2. The fixture generator reproduces the bundled sample data exactly.
cp -r examples "$tmp/ex" && python3 "$tmp/ex/make_fixtures.py" > /dev/null
for f in carriers.csv destinations.csv demand.csv promises.csv warehouse.json; do
  cmp -s "$tmp/ex/data/$f" "$D/$f" || fail "make_fixtures.py does not reproduce $f"
done
ok "make_fixtures.py regenerates examples/data byte for byte"

# 3. The sample tells the shared Fernhill story: last safe dates, backlog, risky banner (text and --json).
f="$tmp/out.txt"
has "$f" "- USPS Ground Advantage (Standard): **Mon Dec 14**, from the carrier ship-by date (source: sample)" "USPS date"
has "$f" "- UPS Ground (Standard): **Tue Dec 15**, from transit by zone (source: sample)" "UPS Ground date"
has "$f" "- UPS 2nd Day Air (Express): **Mon Dec 21**, from transit by zone (source: sample)" "2nd Day Air date"
has "$f" "**BFCM backlog:** orders placed Thu Nov 26 – Mon Nov 30 ship within 3 business days; the backlog clears Thu Dec 3." "BFCM backlog"
has "$f" '| 1 | Site banner | "Order by Dec 18 for Christmas delivery" | Standard | Fri Dec 18 | RISKY |' "banner not RISKY"
cc "${SAMPLE[@]}" --demand $D/demand.csv --json "$tmp/s.json" > /dev/null
python3 - "$tmp/s.json" <<'EOF' || fail "--json result wrong"
import json, sys
a = json.load(open(sys.argv[1]))
got = {s["name"]: s["last_safe_order_date"] for s in a["services"]}
assert got == {"USPS Ground Advantage": "2026-12-14", "UPS Ground": "2026-12-15", "UPS 2nd Day Air": "2026-12-21"}, got
assert a["target_date"] == "2026-12-24" and a["threshold_pct"] == 95.0
b = a["backlog"]
assert b["bfcm_window"] == ["2026-11-26", "2026-11-30"] and b["bfcm_ship_within_business_days"] == 3, b
assert b["bfcm_clears"] == "2026-12-03", b
assert [p["status"] for p in a["promises"]] == ["RISKY", "OK", "WARNING", "WARNING", "INFO"]
assert a["verdict"] == "NOT READY"
EOF
ok "sample: Dec 14 / Dec 15 / Dec 21, BFCM backlog clears Thu Dec 3, banner RISKY (text and --json)"

# 4. Cutoff time, weekends and warehouse closures.
#    3:30 pm ET cutoff, closed Wed Dec 23: Tue after 3:30 pm and all of Wed ship Thu Dec 24 and miss.
cc --carriers $C/courier.csv --warehouse $C/wh_cutoff.json --destinations $C/dest_one.csv --as-of 2026-12-01 > "$tmp/cut.txt"
f="$tmp/cut.txt"
has "$f" "**Last safe order dates** (orders placed by 3:30 pm ET):" "cutoff time label"
has "$f" "- Test Courier: **Tue Dec 22**, from transit by zone (source: sample)" "last safe with closure"
has "$f" "| Fri Dec 18 | Fri Dec 18 | Mon Dec 21 | 100% |" "Friday after cutoff should ship Monday"
has "$f" "| Sat Dec 19 (closed) | Mon Dec 21 | Mon Dec 21 | 100% |" "weekend should ship Monday"
has "$f" "| Tue Dec 22 | Tue Dec 22 | Thu Dec 24 | 100% |" "after cutoff before a closure should skip it"
has "$f" "| Wed Dec 23 (closed: Inventory count) | Thu Dec 24 | Thu Dec 24 | 0% |" "closed day should ship next working day"
has "$f" "\"We're closed Wed, Dec 23. Orders placed after 3:30 pm ET Tue, Dec 22 ship Thu, Dec 24.\"" "closure notice"
ok "cutoff time, weekends and closures move ship dates"

# 5. FIFO backlog on a hand-computed case (capacity 100, every order before the cutoff).
cc --carriers $C/courier.csv --warehouse $C/wh_fifo.json --destinations $C/dest_one.csv --demand $C/demand_fifo.csv \
   --promises $C/promises_fifo.csv --as-of 2026-11-20 > "$tmp/fifo.txt"
f="$tmp/fifo.txt"
has "$f" "**Backlog Fri Dec 4 – Wed Dec 9:** orders placed Fri Dec 4 – Tue Dec 8 ship up to 2 business days later than usual; it clears Wed Dec 9. Peak backlog: 120 orders at close on Mon Dec 7." "backlog summary"
has "$f" "| Fri Dec 4 | 180 | 180 | 100 | 80 | Fri Dec 4 – Mon Dec 7 | 0–1 |" "Fri row"
has "$f" "| Sat Dec 5 (closed) | 40 | — | closed | 80 | Mon Dec 7 – Tue Dec 8 | 1–2 |" "Sat row"
has "$f" "| Sun Dec 6 (closed) | 10 | — | closed | 80 | Tue Dec 8 | 2 |" "Sun row"
has "$f" "| Mon Dec 7 | 90 | 220 | 100 | 120 | Tue Dec 8 – Wed Dec 9 | 1–2 |" "Mon row"
has "$f" "| Tue Dec 8 | 30 | 150 | 100 | 50 | Wed Dec 9 | 1 |" "Tue row"
has "$f" "| Wed Dec 9 | 20 | 70 | 70 | 0 | Wed Dec 9 | 0 |" "Wed row (clears)"
has "$f" "**BFCM backlog:** not modeled: the demand file has no rows for Thu Nov 26 – Mon Nov 30." "BFCM not covered"
has "$f" "Says orders ship within 1 business day. Orders placed Sat Dec 5 – Mon Dec 7 take up to 2 business days." "next-day claim vs backlog"
cc --carriers $C/courier.csv --warehouse $C/wh_fifo.json --destinations $C/dest_one.csv --demand $C/demand_planner.csv \
   --aov 85 --promises $C/promises_fifo.csv --as-of 2026-11-20 > "$tmp/plan.txt"
diff <(sed '/^Demand:/d' "$tmp/fifo.txt") <(sed '/^Demand:/d' "$tmp/plan.txt") > /dev/null || fail "Budget Planner plan with --aov should match the orders file"
ok "FIFO backlog matches the hand-computed queue (and a Budget Planner plan with --aov gives the same)"

# 6. Coverage on a hand-computed zone mix: zone 1 50% (1 day), zone 3 30% (2-4 days), zone 8 20% (5 days).
R=(--carriers $C/acme.csv --warehouse $C/wh_cov.json --destinations $C/dest_cov.csv --as-of 2026-12-01)
cc "${R[@]}" > "$tmp/cov.txt"
f="$tmp/cov.txt"
has "$f" "| Thu Dec 17 | Thu Dec 17 | Fri Dec 18 | 100% |" "5 days left: everyone"
has "$f" "| Fri Dec 18 | Fri Dec 18 | Mon Dec 21 | 80.0% |" "4 days left: 50 + 30"
has "$f" "| Mon Dec 21 | Mon Dec 21 | Tue Dec 22 | 70.0% |" "3 days left: 50 + 30 x 2/3"
has "$f" "| Tue Dec 22 | Tue Dec 22 | Wed Dec 23 | 60.0% |" "2 days left: 50 + 30 x 1/3"
has "$f" "| Wed Dec 23 | Wed Dec 23 | Thu Dec 24 | 50.0% |" "1 day left: zone 1 only"
has "$f" "| Acme Ground | — | **Thu Dec 17** | Fri Dec 18 | Thu Dec 17 | Thu Dec 17 | — | transit by zone |" "80/95/99 dates"
#    No Acme delivery or pickup on Mon Dec 21: one fewer delivery day, and Monday shipments go out Tuesday.
cc --carriers $C/acme_holiday.csv --warehouse $C/wh_cov.json --destinations $C/dest_cov.csv --as-of 2026-12-01 > "$tmp/hol.txt"
f="$tmp/hol.txt"
has "$f" "- Acme Ground: **Wed Dec 16**, from transit by zone" "carrier holiday should move the last safe date"
has "$f" "| Thu Dec 17 | Thu Dec 17 | Fri Dec 18 | 80.0% |" "4 delivery days left around the holiday"
has "$f" "| Fri Dec 18 | Fri Dec 18 | Mon Dec 21 | 70.0% |" "3 delivery days left around the holiday"
has "$f" "| Mon Dec 21 | Mon Dec 21 | Tue Dec 22 | 60.0% |" "Monday shipment picked up Tuesday"
has "$f" "no delivery Mon Dec 21" "carrier holiday listed"
ok "coverage math matches the hand-computed zone mix, including a carrier non-delivery day"

# 7. Changing the threshold moves the last safe date (case above and the sample).
cc "${R[@]}" --threshold 70 > "$tmp/t70.txt"
has "$tmp/t70.txt" "- Acme Ground: **Mon Dec 21**, from transit by zone" "70% should allow Mon Dec 21"
cc "${R[@]}" --threshold 60 > "$tmp/t60.txt"
has "$tmp/t60.txt" "- Acme Ground: **Tue Dec 22**, from transit by zone" "60% should allow Tue Dec 22"
cc "${SAMPLE[@]}" --demand $D/demand.csv --threshold 80 > "$tmp/s80.txt"
has "$tmp/s80.txt" "- UPS Ground (Standard): **Wed Dec 16**, from transit by zone (source: sample)" "sample at 80%"
ok "threshold change moves the last safe date"

# 8. A carrier ship-by date overrides a later transit-based date; a later ship-by date does not.
cc --carriers $C/acme_shipby.csv --warehouse $C/wh_cov.json --destinations $C/dest_cov.csv --as-of 2026-12-01 > "$tmp/sb.txt"
has "$tmp/sb.txt" "- Acme Ground: **Tue Dec 15**, from the carrier ship-by date (source: https://example.com/acme-holiday-dates)" "ship-by should win"
has "$tmp/sb.txt" "| Acme Ground | — | **Tue Dec 15** | Tue Dec 15 | Tue Dec 15 | Thu Dec 17 | Tue Dec 15 (source below) | carrier ship-by date |" "ship-by row"
cc --carriers $C/acme_late_shipby.csv --warehouse $C/wh_cov.json --destinations $C/dest_cov.csv --as-of 2026-12-01 > "$tmp/lsb.txt"
has "$tmp/lsb.txt" "- Acme Ground: **Thu Dec 17**, from transit by zone" "later ship-by should not win"
ok "carrier ship-by date overrides a later transit date, not an earlier one"

# 9. Promise classes: RISKY late date, missing cutoff time, OK, INFO conservative, RISKY sent late, WARNING unqualified.
cc "${R[@]}" --promises $C/promises_cls.csv > "$tmp/cls.txt"
f="$tmp/cls.txt"
has "$f" "**Verdict: NOT READY.** 2 risky promises, 2 warnings and 1 note." "promise counts"
has "$f" '| 1 | Banner A | "Order by Dec 18 for Christmas" | Acme Ground | Fri Dec 18 | RISKY | Dec 18 is 1 day after the last safe order date for Acme Ground (Thu Dec 17).' "late date"
has "$f" '| 2 | Banner B | "Order by Dec 17 for Christmas" | Acme Ground | Thu Dec 17 | WARNING | Matches the last safe order date for Acme Ground (Thu Dec 17) but gives no time' "no cutoff time"
has "$f" '| 3 | Banner C | "Order by 12 pm PT Dec 17 for Christmas" | Acme Ground | Thu Dec 17 | OK |' "with time is OK"
has "$f" '| 4 | Product page | "Order by Dec 14 for Christmas" | Acme Ground | Mon Dec 14 | INFO | 3 days earlier than needed' "conservative"
has "$f" '| 5 | Cart note | "Order by Dec 15 for Christmas" | any (none named) | Tue Dec 15 | OK |' "2 days early is OK"
has "$f" '| 6 | SMS (sends Sat Dec 19) | "Still time for Christmas delivery!" | any (none named) | — | RISKY | Sends Sat Dec 19, after the last safe order date' "sent late"
has "$f" '| 7 | Email (sends Thu Dec 10) | "Guaranteed delivery by Christmas." | any (none named) | — | WARNING | Promises holiday delivery with no order-by date or shipping method and says guaranteed.' "unqualified"
has "$f" "Mail, Internet, or Telephone Order Merchandise Rule (16 CFR Part 435)" "FTC note"
has "$f" "Not legal advice." "not legal advice"
ok "promises: RISKY, missing cutoff time, OK, INFO, sent late and unqualified are classified"

# 10. The report says where every date came from and never calls sample data carrier-published.
has "$tmp/out.txt" "Source: sample (fictional carrier data)." "sample transit source"
has "$tmp/out.txt" "Ship-by date Mon Dec 14. Source: sample (fictional, not a date USPS published)." "sample ship-by source"
has "$tmp/out.txt" "Replace the \`sample\` rows in carriers.csv" "sample replacement fix"
has "$tmp/cov.txt" "Source: https://example.com/acme-transit-map." "URL source"
has "$tmp/lsb.txt" "Ship-by date Tue Dec 22. Source: Acme holiday flyer (as given; no URL)." "text source"
hasnt "$tmp/cov.txt" "Replace the \`sample\` rows" "no sample note for real sources"
ok "output states the source of every date"

# 11. Invalid input exits 2 with the reason.
bad() {  # bad "expected text" args...
  local want="$1"; shift
  set +e; cc "$@" > /dev/null 2> "$tmp/err.txt"; local rc=$?; set -e
  [ "$rc" = 2 ] && grep -qF -- "$want" "$tmp/err.txt" || { cat "$tmp/err.txt"; fail "expected exit 2 with: $want (rc=$rc)"; }
}
bad "line 2: 'source' is blank" --carriers $C/bad_nosource.csv --warehouse $C/wh_cov.json --destinations $C/dest_one.csv
bad "share_pct adds up to 80, not 100" --carriers $C/acme.csv --warehouse $C/wh_cov.json --destinations $C/bad_dest_sum.csv
bad "skips from 2026-12-03 to 2026-12-05" --carriers $C/courier.csv --warehouse $C/wh_cov.json --destinations $C/dest_one.csv --demand $C/bad_demand_gap.csv
bad "'orders' must be at least 0, got -5" --carriers $C/courier.csv --warehouse $C/wh_cov.json --destinations $C/dest_one.csv --demand $C/bad_demand_neg.csv
bad "service 'FedEx Ground' is not in the carriers file" --carriers $C/acme.csv --warehouse $C/wh_cov.json --destinations $C/dest_cov.csv --promises $C/bad_promise_service.csv
bad "'order_cutoff' must be a 24-hour time" --carriers $C/courier.csv --warehouse $C/bad_wh_cutoff.json --destinations $C/dest_one.csv
bad "has no transit days for zone 8, but 20.0% of orders go there" --carriers $C/bad_zone_gap.csv --warehouse $C/wh_cov.json --destinations $C/dest_cov.csv
bad "cannot read promises file" --carriers $C/courier.csv --warehouse $C/wh_cov.json --destinations $C/dest_one.csv --promises $C/nope.csv
bad "pass --aov" --carriers $C/courier.csv --warehouse $C/wh_fifo.json --destinations $C/dest_one.csv --demand $C/demand_planner.csv
ok "invalid input exits 2 with the reason"

# 12. Zero backlog: demand under capacity every day; the next-business-day FAQ holds.
cc "${SAMPLE[@]}" --demand $C/demand_flat.csv > "$tmp/flat.txt"
f="$tmp/flat.txt"
has "$f" "**BFCM backlog:** none. Orders placed Thu Nov 26 – Mon Nov 30 ship on the normal schedule." "no BFCM backlog"
has "$f" "No backlog: on every working day, the orders ready to ship fit within capacity" "no backlog text"
grep -F '| 4 | FAQ: Shipping |' "$f" | grep -qF "| OK | Holds: every order ships within 1 business day. |" || fail "FAQ should hold with no backlog"
hasnt "$f" "shipping-time note" "no shipping-time note without a backlog"
has "$f" "- UPS Ground (Standard): **Tue Dec 15**" "last safe dates unchanged"
ok "zero-backlog case"

# 13. Temporary peak capacity and handling days.
cc --carriers $C/courier.csv --warehouse $C/wh_peak.json --destinations $C/dest_one.csv --demand $C/demand_fifo.csv \
   --as-of 2026-11-20 > "$tmp/peak.txt"
f="$tmp/peak.txt"
has "$f" "**Backlog Fri Dec 4 – Tue Dec 8:** orders placed Fri Dec 4, Mon Dec 7 ship up to 1 business day later than usual; it clears Tue Dec 8. Peak backlog: 30 orders at close on Fri Dec 4." "peak capacity backlog"
has "$f" "| Mon Dec 7 | 90 | 170 | 150 | 20 | Mon Dec 7 – Tue Dec 8 | 0–1 |" "peak capacity Monday"
has "$f" "Capacity 100 orders per working day (150 on Fri Dec 4 – Mon Dec 7)." "peak capacity listed"
cc --carriers $C/courier.csv --warehouse $C/wh_handling.json --destinations $C/dest_one.csv --as-of 2026-12-01 > "$tmp/hand.txt"
f="$tmp/hand.txt"
has "$f" "- Test Courier: **Mon Dec 21**, from transit by zone" "handling day should move the last safe date"
has "$f" "| Mon Dec 21 | Tue Dec 22 | Thu Dec 24 | 100% |" "handling day plus closure"
has "$f" "| Tue Dec 22 | Thu Dec 24 | Fri Dec 25 | 0% |" "handling day skips the closed Wednesday"
has "$f" "\"We're closed Wed, Dec 23. Orders placed after 3:30 pm ET Tue, Dec 22 ship Fri, Dec 25.\"" "closure notice counts handling days"
has "$f" "ship 1 working day later" "handling days described"
ok "temporary peak capacity and handling days"

# 14. Regression (QA 7.1): a backlog that spans BFCM with no orders forecast inside the window used to crash.
cc "${SAMPLE[@]}" --warehouse $C/wh_bfcm_gap.json --demand $C/demand_bfcm_gap.csv --json "$tmp/gap.json" > "$tmp/gap.txt" 2> "$tmp/gap.err" \
  || { cat "$tmp/gap.err"; fail "backlog spanning BFCM with no BFCM orders should not crash"; }
f="$tmp/gap.txt"
has "$f" "**BFCM backlog:** no orders forecast in the BFCM window (Thu Nov 26 – Mon Nov 30), but orders placed Wed Nov 25, Tue Dec 1 build a backlog that clears" "gap headline"
has "$f" "**No orders are forecast in the BFCM window (Thu Nov 26 – Mon Nov 30), but the backlog from orders placed Wed Nov 25, Tue Dec 1 runs through it" "gap section 2"
python3 - "$tmp/gap.json" <<'EOF' || fail "gap --json result wrong"
import json, sys
b = json.load(open(sys.argv[1]))["backlog"]
assert b["bfcm_ship_within_business_days"] is None and b["bfcm_orders_forecast"] is False, b
assert b["bfcm_clears"] == b["periods"][0]["clears"], b
EOF
ok "backlog spanning BFCM with no orders forecast in the window: no crash, says so (text and --json)"

# 15. A stated time later than the cutoff on the last safe day is RISKY (converted to the warehouse zone).
cc "${R[@]}" --promises $C/promises_time.csv > "$tmp/time.txt"
f="$tmp/time.txt"
has "$f" '| 1 | Banner late PT | "Order by 11:59 pm PT Dec 17 for Christmas" | Acme Ground | Thu Dec 17 | RISKY | Matches the last safe order date for Acme Ground (Thu Dec 17) but says 11:59 pm PT, later than the 12:00 pm PT cutoff' "11:59 pm PT"
has "$f" '| 2 | Banner late ET | "Order by 5 pm ET Dec 17 for Christmas" | Acme Ground | Thu Dec 17 | RISKY | Matches the last safe order date for Acme Ground (Thu Dec 17) but says 5 pm ET, later than the 12:00 pm PT cutoff' "5 pm ET"
has "$f" '| 3 | Banner early ET | "Order by 9 am ET Dec 17 for Christmas" | Acme Ground | Thu Dec 17 | OK | Matches the last safe order date for Acme Ground (Thu Dec 17). Says 9 am ET, before the 12:00 pm PT cutoff. |' "9 am ET"
has "$f" '| 4 | Banner noon | "Order by noon PT Dec 17 for Christmas" | Acme Ground | Thu Dec 17 | OK | Matches the last safe order date for Acme Ground (Thu Dec 17). Gives the cutoff time. |' "noon PT"
has "$f" "Banner late PT: change the time to the 12:00 pm PT cutoff:" "late-time fix"
ok "stated times are converted to the warehouse zone and checked against the cutoff"

# 16. Ship-by after the target and a missing warehouse time zone exit 2.
bad "ship_by_date 2026-12-31, after the target date 2026-12-24" --carriers $C/bad_shipby_late.csv --warehouse $C/wh_cov.json --destinations $C/dest_cov.csv --as-of 2026-12-01
bad "'timezone' is required" --carriers $C/courier.csv --warehouse $C/bad_wh_notz.json --destinations $C/dest_one.csv
ok "ship-by after the target and a missing time zone exit 2"

# 17. USPS zone 9 is accepted (optional zone_9 column), and required when orders go there.
cc --carriers $C/acme_zone9.csv --warehouse $C/wh_cov.json --destinations $C/dest_zone9.csv --as-of 2026-12-01 --json "$tmp/z9.json" > "$tmp/z9.txt"
has "$tmp/z9.txt" "zone 9 10.0%" "zone 9 in the mix"
has "$tmp/z9.txt" "1, —, 2–4, —, —, —, —, 5, 5 business days for zones 1–9" "zone 9 transit listed"
python3 -c "import json,sys; z=json.load(open(sys.argv[1]))['zone_mix']; assert z['9'] == 0.1, z" "$tmp/z9.json" || fail "zone 9 in --json"
bad "has no transit days for zone 9, but 10.0% of orders go there (add a zone_9 column)" --carriers $C/acme.csv --warehouse $C/wh_cov.json --destinations $C/dest_zone9.csv
ok "USPS zone 9 accepted"

# 18. After every cutoff has passed the verdict says so instead of "Nothing to fix".
cc --carriers $C/acme.csv --warehouse $C/wh_cov.json --destinations $C/dest_cov.csv --as-of 2026-12-22 > "$tmp/late.txt"
has "$tmp/late.txt" "**Verdict: READY.** No promise or backlog issues, but all cutoffs have passed: orders placed now arrive after Thu Dec 24." "all cutoffs passed"
hasnt "$tmp/late.txt" "Nothing to fix." "no 'Nothing to fix' after the cutoffs"
ok "all cutoffs passed is stated"

# 19. Scripts parse under Python 3.8 grammar (run this suite with python3.8 first on PATH for the full check).
python3 - <<'EOF' || fail "not Python 3.8 compatible"
import ast
for p in ("scripts/check_cutoffs.py", "examples/make_fixtures.py", "tests/make_cases.py"):
    ast.parse(open(p, encoding="utf-8").read(), p, feature_version=(3, 8))
EOF
ok "scripts parse as Python 3.8 ($(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])') ran this suite)"

echo "all $pass checks passed"

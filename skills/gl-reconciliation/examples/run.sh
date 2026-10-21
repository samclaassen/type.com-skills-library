#!/usr/bin/env bash
# Runnable example. Working directory is the skill directory.
# Every invocation prints its own exit status so the failure paths are visible.
set -u
cd "$(dirname "$0")/.."

echo "### 1. Operating bank account vs GL 1000, August 2026"
python3 scripts/reconcile.py \
  --ledger-a examples/bank_august.csv \
  --ledger-b examples/gl_cash_1000_august.csv \
  --label-a "Meridian Bank 8841" \
  --label-b "GL 1000 Cash - operating" \
  --as-of 2026-08-31 \
  --balance-a 1081449.52 \
  --balance-b 1083430.75 \
  --date-tolerance 5 \
  --batch-window 5 \
  --amount-tolerance 0.05
echo "exit status: $?"

echo
echo "### 2. Same data, GL control total overstated by a prior-period break"
python3 scripts/reconcile.py \
  --ledger-a examples/bank_august.csv \
  --ledger-b examples/gl_cash_1000_august.csv \
  --label-a "Meridian Bank 8841" \
  --label-b "GL 1000 Cash - operating" \
  --as-of 2026-08-31 \
  --balance-a 1081449.52 \
  --balance-b 1084635.30 \
  --date-tolerance 5 \
  --batch-window 5 \
  --amount-tolerance 0.05 \
  | tail -n 14
echo "exit status: ${PIPESTATUS[0]}"

echo
echo "### 3. The bridge as JSON"
python3 scripts/reconcile.py \
  --ledger-a examples/bank_august.csv \
  --ledger-b examples/gl_cash_1000_august.csv \
  --as-of 2026-08-31 \
  --balance-a 1081449.52 \
  --balance-b 1083430.75 \
  --date-tolerance 5 --batch-window 5 --amount-tolerance 0.05 --json \
  | python3 -c 'import json,sys; print(json.dumps(json.load(sys.stdin)["bridge"], indent=2))'
echo "exit status: ${PIPESTATUS[0]}"

echo
echo "### 4. Bad input is rejected loudly"
python3 scripts/reconcile.py \
  --ledger-a examples/bank_august.csv \
  --ledger-b examples/broken_gl.csv \
  --as-of 2026-08-31 2>&1
echo "exit status: $?"

exit 0

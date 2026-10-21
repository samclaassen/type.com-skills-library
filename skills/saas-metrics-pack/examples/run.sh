#!/usr/bin/env bash
# Runnable example. Working directory is the skill directory.
# Every invocation prints its own exit status so the failure paths are visible.
set -u
cd "$(dirname "$0")/.."

echo "### 1. August 2026 MRR movement, tied to the billing system's closing MRR"
python3 scripts/mrr_waterfall.py \
  --subscriptions examples/subscriptions_august.csv \
  --period-start 2026-08-01 \
  --as-of 2026-08-31 \
  --sales-marketing-spend 18500 \
  --gross-margin-pct 80 \
  --expected-closing-mrr 24380.00 \
  --top 6
echo "exit status: $?"

echo
echo "### 2. Billing system reports a different closing MRR"
python3 scripts/mrr_waterfall.py \
  --subscriptions examples/subscriptions_august.csv \
  --period-start 2026-08-01 \
  --as-of 2026-08-31 \
  --expected-closing-mrr 25100.00 \
  | tail -n 8
echo "exit status: ${PIPESTATUS[0]}"

echo
echo "### 3. Machine-readable movements"
python3 scripts/mrr_waterfall.py \
  --subscriptions examples/subscriptions_august.csv \
  --period-start 2026-08-01 --as-of 2026-08-31 --json \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print(json.dumps({k: d[k] for k in ("openingMrr","movements","closingMrr","nrrPct","grrPct","quickRatio")}, indent=2))'
echo "exit status: ${PIPESTATUS[0]}"

echo
echo "### 4. Bad input is rejected loudly"
python3 scripts/mrr_waterfall.py \
  --subscriptions examples/broken_subscriptions.csv \
  --period-start 2026-08-01 --as-of 2026-08-31 2>&1
echo "exit status: $?"

exit 0

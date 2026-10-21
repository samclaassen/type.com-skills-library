#!/usr/bin/env bash
# Runnable example. Working directory is the skill directory.
# Every invocation prints its own exit status so the failure paths are visible.
set -u
cd "$(dirname "$0")/.."

echo "### 1. August 2026, material = 5,000 and 10% of budget, tied to the P&L"
python3 scripts/variance_commentary.py \
  --actuals examples/august_actuals.csv \
  --as-of 2026-08-31 \
  --materiality-abs 5000 \
  --materiality-pct 10 \
  --control-operating-variance -93420.00
echo "exit status: $?"

echo
echo "### 2. Wider net: either threshold is enough (note Engineering salaries appears)"
python3 scripts/variance_commentary.py \
  --actuals examples/august_actuals.csv \
  --as-of 2026-08-31 \
  --materiality-abs 15000 \
  --materiality-pct 25 \
  --materiality-rule or \
  | sed -n '/^MATERIAL VARIANCES/,/^Classification mix/p'
echo "exit status: ${PIPESTATUS[0]}"

echo
echo "### 3. Control total from the P&L disagrees with the detail"
python3 scripts/variance_commentary.py \
  --actuals examples/august_actuals.csv \
  --as-of 2026-08-31 \
  --control-operating-variance -80000.00 \
  | sed -n '/^VERDICT/,$p'
echo "exit status: ${PIPESTATUS[0]}"

echo
echo "### 4. Bad input is rejected loudly"
python3 scripts/variance_commentary.py \
  --actuals examples/broken_actuals.csv \
  --as-of 2026-08-31 2>&1
echo "exit status: $?"

exit 0

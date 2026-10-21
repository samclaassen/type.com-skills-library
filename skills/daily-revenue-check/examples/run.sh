#!/usr/bin/env bash
# Daily revenue check for the bundled synthetic Fernhill Skincare exports.
# Scenario 1 HOLDs (wholesale source total was keyed from retailer sell-through).
# Scenario 2 re-runs with the corrected invoice total and PUBLISHes.
# Runnable from anywhere; resolves paths relative to the skill directory.
set -uo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

echo "### Scenario 1: first run of the morning"
python3 scripts/daily_revenue_check.py \
  --workdir examples/fernhill \
  --date 2026-09-22 \
  --as-of 2026-09-23T07:00:00-04:00 \
  --source-totals examples/fernhill/source_totals.csv \
  --sell-through examples/fernhill/sell_through.csv
first=$?
echo "### exit code: $first (expected 1 = HOLD)"
echo

echo "### Scenario 2: wholesale total re-pulled from invoices in the accounting system"
python3 scripts/daily_revenue_check.py \
  --workdir examples/fernhill \
  --date 2026-09-22 \
  --as-of 2026-09-23T07:10:00-04:00 \
  --source-totals examples/fernhill/source_totals_corrected.csv \
  --sell-through examples/fernhill/sell_through.csv \
  --spend examples/fernhill/spend.csv
second=$?
echo "### exit code: $second (expected 0 = PUBLISH)"

if [ "$first" -eq 1 ] && [ "$second" -eq 0 ]; then
  exit 0
fi
echo "unexpected exit codes: $first, $second" >&2
exit 1

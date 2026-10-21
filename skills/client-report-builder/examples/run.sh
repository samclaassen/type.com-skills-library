#!/usr/bin/env bash
# Regenerate examples/expected_output.txt with:
#   bash examples/run.sh > examples/expected_output.txt
#
# Scenario 1: no finance definitions file and no finance totals. The report
# opens with the skill's defaults and skips MER and new-customer CAC.
# Scenario 2: the same exports plus finance's revenue_definitions.json
# (attribution windows, new-customer definition) and finance_totals.csv
# (net sales, new customers), so MER and new-customer CAC appear.
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."

echo "===== Scenario 1: no finance definitions file ====="
echo
python3 scripts/build_report.py \
  --paid examples/data/paid_performance.csv \
  --analytics examples/data/analytics_channels.csv \
  --budget examples/data/budget.csv \
  --client "Northwind Coffee Co." \
  --month 2026-08 \
  --as-of 2026-09-02

echo
echo "===== Scenario 2: finance definitions and finance totals ====="
echo
python3 scripts/build_report.py \
  --paid examples/data/paid_performance.csv \
  --analytics examples/data/analytics_channels.csv \
  --budget examples/data/budget.csv \
  --client "Northwind Coffee Co." \
  --month 2026-08 \
  --as-of 2026-09-02 \
  --definitions examples/data/revenue_definitions.json \
  --finance-totals examples/data/finance_totals.csv

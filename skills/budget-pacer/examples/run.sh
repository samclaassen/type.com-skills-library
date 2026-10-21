#!/usr/bin/env bash
# Regenerate examples/expected_output.txt with:
#   bash examples/run.sh > examples/expected_output.txt
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."

python3 scripts/pace_budgets.py \
  --campaigns examples/data/campaigns.csv \
  --daily-spend examples/data/daily_spend.csv \
  --commitments examples/data/commitments.csv \
  --month 2026-09 \
  --as-of 2026-09-18

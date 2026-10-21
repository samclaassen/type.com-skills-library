#!/usr/bin/env bash
# Runs the pulse on the bundled sample store (Black Friday, 2:00 pm ET). Output must match
# examples/expected_output.txt exactly. Regenerate it with:
#   bash examples/run.sh > examples/expected_output.txt
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."

python3 scripts/live_pulse.py \
  --plan examples/data/plan.csv \
  --hourly examples/data/hourly.csv \
  --ly-hourly examples/data/ly_hourly.csv \
  --spend examples/data/spend_today.csv \
  --discounts examples/data/discount_usage.csv \
  --inventory examples/data/inventory.csv \
  --config examples/data/config.json

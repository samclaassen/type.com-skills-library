#!/usr/bin/env bash
# Cohort LTV for the bundled synthetic Kindling Coffee Co. order export.
# Runnable from anywhere; resolves paths relative to the skill directory.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Scenario 1: no finance definitions file in the working folder, so the skill
# uses its own defaults and says so in its first lines.
python3 scripts/cohort_ltv.py \
  --orders examples/kindling_orders.csv \
  --spend examples/kindling_spend.csv \
  --as-of 2026-09-01 \
  --horizon 3 \
  --fulfillment-cost 3.25

# Scenario 2: run from a working folder that has finance/revenue_definitions.json,
# which is picked up by default. Exercises timezone day-shifts, a CAD order
# converted at the local day's rate, a refund landing in the month it was
# issued, and excluded test, internal, replacement, voided, cancelled and
# zero-value orders.
echo
echo "=== Scenario 2: with finance/revenue_definitions.json (examples/with_definitions) ==="
echo
cd examples/with_definitions
python3 ../../scripts/cohort_ltv.py \
  --orders orders.csv \
  --as-of 2026-05-01 \
  --horizon 2 \
  --cac 20.00 \
  --fulfillment-cost 3.25

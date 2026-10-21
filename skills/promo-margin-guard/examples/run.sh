#!/usr/bin/env bash
# True promo margin for the bundled synthetic Willow & Ash promotion data.
# Runnable from anywhere; resolves paths relative to the skill directory.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

# Scenario 1: no finance definitions file in the working folder, so the skill
# uses its own defaults and says so in its first lines.
python3 scripts/promo_margin.py \
  --orders examples/promo_orders.csv \
  --lines examples/promo_lines.csv \
  --products examples/products.csv \
  --fulfillment-cost 2.85 \
  --return-shipping-cost 8.50 \
  --restock-recovery-pct 75 \
  --planned-discount-pct 25

# Scenario 2: run from a working folder that has finance/revenue_definitions.json,
# which is picked up by default. Exercises a CAD order whose UTC date and local
# date have different FX rates, a refund issued after the promotion, and
# excluded test, internal, voided and cancelled orders with their line items.
echo
echo "=== Scenario 2: with finance/revenue_definitions.json (examples/with_definitions) ==="
echo
cd examples/with_definitions
python3 ../../scripts/promo_margin.py \
  --orders orders.csv \
  --lines lines.csv \
  --products products.csv \
  --fulfillment-cost 2.85 \
  --return-shipping-cost 8.50 \
  --restock-recovery-pct 75 \
  --planned-discount-pct 25

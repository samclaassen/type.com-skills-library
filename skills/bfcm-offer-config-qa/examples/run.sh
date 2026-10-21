#!/usr/bin/env bash
# Runs the analyzer on the bundled sample store. Output must match
# examples/expected_output.txt exactly. Regenerate it with:
#   bash examples/run.sh > examples/expected_output.txt
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."

python3 scripts/offer_qa.py \
  --plan examples/data/offer_plan.json \
  --discounts examples/data/discounts.json \
  --products examples/data/products.csv \
  --price-history examples/data/price_history.csv

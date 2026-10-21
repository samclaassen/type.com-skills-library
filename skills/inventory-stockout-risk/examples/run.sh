#!/usr/bin/env bash
# Stockout and overstock risk for the bundled synthetic Tidewater Supply Co. data.
# Runnable from anywhere; resolves paths relative to the skill directory.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

python3 scripts/stockout_risk.py \
  --sales examples/sales_history.csv \
  --inventory examples/inventory_snapshot.csv \
  --suppliers examples/suppliers.csv \
  --as-of 2026-09-15

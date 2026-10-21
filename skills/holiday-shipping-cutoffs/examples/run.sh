#!/usr/bin/env bash
# Runs the checker on the bundled sample store. Output must match
# examples/expected_output.txt exactly. Regenerate it with:
#   bash examples/run.sh > examples/expected_output.txt
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."

python3 scripts/check_cutoffs.py \
  --account "Fernhill Goods" \
  --carriers examples/data/carriers.csv \
  --warehouse examples/data/warehouse.json \
  --destinations examples/data/destinations.csv \
  --demand examples/data/demand.csv \
  --promises examples/data/promises.csv \
  --as-of 2026-10-07

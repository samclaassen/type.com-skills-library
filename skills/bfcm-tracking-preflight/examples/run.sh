#!/usr/bin/env bash
# Runs the analyzer on the bundled sample store. Output must match
# examples/expected_output.txt exactly. Regenerate it with:
#   bash examples/run.sh > examples/expected_output.txt
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."

python3 scripts/preflight.py \
  --account "Fernhill Goods" \
  --events examples/data/events_daily.csv \
  --orders examples/data/orders_utm.csv \
  --last-bfcm examples/data/last_bfcm.csv

#!/usr/bin/env bash
# Runs the analyzer on the bundled sample store. Output must match
# examples/expected_output.txt exactly. Regenerate it with:
#   bash examples/run.sh > examples/expected_output.txt
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."

python3 scripts/bounce_audit.py \
  --bundle examples/data/bundle.json \
  --shopify-daily examples/data/shopify_daily.csv

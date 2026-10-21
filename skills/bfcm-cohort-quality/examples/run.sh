#!/usr/bin/env bash
# Runs the analyzer on the bundled sample store. Output must match
# examples/expected_output.txt exactly. Regenerate it with:
#   bash examples/run.sh > examples/expected_output.txt
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."

# The sample file holds only the three cohorts' customers (see make_fixtures.py), so it
# states where the store's complete order history begins with --history-since.
python3 scripts/cohort_quality.py \
  --orders examples/data/orders.csv \
  --economics examples/data/economics.json \
  --spend examples/data/spend.csv \
  --year 2025 \
  --as-of 2026-10-06 \
  --timezone America/New_York \
  --history-since 2021-03-01

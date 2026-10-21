#!/usr/bin/env bash
# Runs the planner on the bundled sample brand (--as-of pins "today" so the
# stale-history check is reproducible). Output must match
# examples/expected_output.txt exactly. Regenerate it with:
#   bash examples/run.sh > examples/expected_output.txt
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."

python3 scripts/plan_budget.py \
  --history examples/data/history.csv \
  --economics examples/data/economics.json \
  --as-of 2026-10-07

#!/usr/bin/env bash
# Runs the analyzer on the bundled sample store (Fernhill Goods, fictional).
# The report on stdout must match examples/expected_output.txt exactly. The macros, the macro preview,
# the daily forecast and the classified tickets go to the folder given as the first argument (default:
# a new temporary folder, named on stderr); macros.csv, macros_preview.md and forecast.csv must match
# examples/expected/. Regenerate the expected report with:
#   bash examples/run.sh > examples/expected_output.txt
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -n "${1:-}" ]; then
  mkdir -p "$1"
  out="$(cd "$1" && pwd)"
else
  out="$(mktemp -d)"
fi
cd "$here/.."

python3 scripts/holiday_support.py \
  --policies examples/data/policies.json \
  --tickets examples/data/tickets.csv \
  --macros examples/data/macros_existing.csv \
  --orders examples/data/orders.csv \
  --agents 2 --weekend-agents 1 --tickets-per-agent 45 \
  --as-of 2026-10-07 \
  --out-dir "$out"
echo "macros.csv, macros_preview.md, forecast.csv and tickets_classified.csv written to $out" >&2

#!/usr/bin/env bash
# Regenerate examples/expected_output.txt with:
#   bash examples/run.sh > examples/expected_output.txt
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."

python3 scripts/track_scope.py \
  --contracts examples/data/contracts.csv \
  --scope examples/data/scope.csv \
  --time-entries examples/data/time_entries.csv \
  --roster examples/data/roster.csv \
  --deliverables examples/data/deliverables.csv \
  --month 2026-09 \
  --as-of 2026-10-05

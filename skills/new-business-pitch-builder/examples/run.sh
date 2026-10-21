#!/usr/bin/env bash
# Regenerate examples/expected_output.txt with:
#   bash examples/run.sh > examples/expected_output.txt
set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$here/.."

python3 scripts/build_pitch.py \
  --paid examples/data/paid_snapshot.csv \
  --seo examples/data/seo_pages.csv \
  --funnel examples/data/funnel_steps.csv \
  --benchmarks examples/data/benchmarks.csv \
  --prospect "Tidewater Outfitters" \
  --aov 85 \
  --gross-margin 55 \
  --as-of 2026-09-22 \
  --proposed-retainer 9500

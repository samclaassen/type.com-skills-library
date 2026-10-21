#!/usr/bin/env bash
# Theme mining over the bundled synthetic Northfell review and ticket export.
# Runnable from anywhere; resolves paths relative to the skill directory.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

python3 scripts/mine_reviews.py \
  --feedback examples/feedback.csv \
  --top 6

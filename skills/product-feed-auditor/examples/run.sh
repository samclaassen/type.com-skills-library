#!/usr/bin/env bash
# Audit the bundled synthetic Harbor Loom feed.
# Runnable from anywhere; resolves paths relative to the skill directory.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."

python3 scripts/audit_feed.py \
  --feed examples/sample_feed.csv \
  --as-of 2026-09-15 \
  --domain shop.harborloom.com

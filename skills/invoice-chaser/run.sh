#!/usr/bin/env bash
# Run from the skill directory: bash run.sh
# Uses only the bundled FICTIONAL sample data. The ledger is copied to a temp
# directory first, so the bundled sample files are never modified.
set -euo pipefail

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
cp sample_ledger.md "$tmp/sample_ledger.md"

echo "=== 1. create (preview, no --write) ==="
python3 invoice.py create \
  --brief sample_brief.json \
  --ledger "$tmp/sample_ledger.md" \
  --config sample_config.json \
  --as-of 2026-10-02

echo
echo "=== 2. chase ==="
python3 invoice.py chase \
  --ledger "$tmp/sample_ledger.md" \
  --config sample_config.json \
  --as-of 2026-10-02

echo
echo "=== 3. status ==="
python3 invoice.py status \
  --ledger "$tmp/sample_ledger.md" \
  --config sample_config.json \
  --as-of 2026-10-02

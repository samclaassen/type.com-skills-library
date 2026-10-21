#!/usr/bin/env bash
# Offline demo on the fictional Northfield Knit store. No network.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 examples/make_fixtures.py examples/fixtures
python3 scripts/aeo_check.py --domain northfield-knit.example --input examples/input.json \
  --fixtures examples/fixtures --out "${OUT:-/tmp/aeo-demo}" --date 2026-09-25

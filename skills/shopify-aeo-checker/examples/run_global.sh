#!/usr/bin/env bash
# Offline demo of the cross-store share-of-shelf check on fictional stores. No network.
set -euo pipefail
cd "$(dirname "$0")/.."
python3 examples/make_global_fixtures.py examples/global_fixtures
python3 scripts/aeo_global.py --product https://northfield-knit.example/products/harbor-merino-crew-oatmeal \
  --query "mens merino sweater" --query "warm crew neck sweater" --query "wool sweater gift" \
  --fixtures examples/global_fixtures --out "${OUT:-/tmp/aeo-global-demo}" --date 2026-09-25

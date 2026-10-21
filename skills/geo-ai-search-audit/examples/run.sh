#!/usr/bin/env bash
# Offline, deterministic demo of the GEO / AI search audit.
# Serves saved fixtures from examples/sites instead of the network and pins the
# audit date, so stdout is byte-identical on every run. Run from the skill directory:
#   bash examples/run.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export PYTHONDONTWRITEBYTECODE=1

AUDIT="python3 scripts/geo_audit.py"
DATE="2026-01-15"

echo "=== 1. Client audit with competitor benchmark (Markdown DRAFT) ==="
$AUDIT --client harbor-and-vine --clients-dir examples/clients \
  --offline-root examples/sites --competitors --date "$DATE"

echo
echo "=== 2. Single saved page: --url + --html-file + --robots-file + --llms-file ==="
$AUDIT --url https://tidewater-goods.example \
  --html-file examples/sites/tidewater-goods.example/index.html \
  --robots-file examples/sites/tidewater-goods.example/robots.txt \
  --llms-file examples/sites/tidewater-goods.example/llms.txt \
  --date "$DATE" | sed -n '/^## Summary/,/^Issues found/p'

echo
echo "=== 3. JSON output (scores only) ==="
$AUDIT --client "Harbor & Vine" --clients-dir examples/clients \
  --offline-root examples/sites --json --date "$DATE" \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); s=d["site"]; print("draft:", d["draft"]); print("overall:", s["overall_score"], s["rating"]); [print(f"  {k}: {v}") for k,v in sorted(s["scores"].items())]; print("issues:", len(s["issues"]))'

echo
echo "=== 4. Client profile without a website field (expected error, exit 2) ==="
set +e
$AUDIT --client no-website-co --clients-dir examples/clients --offline-root examples/sites 2>&1
echo "exit code: $?"
set -e

#!/usr/bin/env bash
# Run from the skill directory: bash run.sh
# Uses only the bundled fictional sample data. Writes nothing outside a temp dir.
set -euo pipefail

tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

echo "===== 1. voice-stats (past issues) ====="
python3 newsletter.py voice-stats --issues past_issue_*.md

echo
echo "===== 2. lint (good draft) ====="
python3 newsletter.py lint \
  --draft sample_draft.json \
  --config sample_config.json \
  --brief sample_brief.md

echo
echo "===== 3. render --format markdown ====="
python3 newsletter.py render \
  --draft sample_draft.json \
  --config sample_config.json \
  --format markdown

echo
echo "===== 4. render --format html ====="
python3 newsletter.py render \
  --draft sample_draft.json \
  --config sample_config.json \
  --format html | tee "$tmp/newsletter.html"

echo
echo "===== 5. check-html (rendered html) ====="
python3 newsletter.py check-html --file "$tmp/newsletter.html"

echo
echo "===== 6. render --format text (plain-text alternative) ====="
python3 newsletter.py render \
  --draft sample_draft.json \
  --config sample_config.json \
  --format text

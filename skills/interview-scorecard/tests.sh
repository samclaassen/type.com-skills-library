#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
PY="${PYTHON:-python3}"
export PYTHONDONTWRITEBYTECODE=1
"$PY" -B test_structured.py
bash test_validation.sh

"$PY" -B test_metadata_r12.py

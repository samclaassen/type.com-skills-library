# Reproduce the synthetic checkout example

This example runs the real analyzer on deterministic synthetic daily aggregates. No customer data, account connection, or credentials are used.

If you downloaded the separate sample evidence ZIP, extract its contents into
`examples/showcase/` inside the full skill download. The sample ZIP alone does not
include the analyzer engine.

Run all commands **from the extracted skill package root**, which contains SKILL.md, scripts/, tests/, and examples/showcase/. Use Python 3.10 or later. The recorded run used Python 3.14 and the versions in requirements-tested.txt. For normal installation, requirements.txt constrains pandas to the supported 2.2–2.3 series.

## 1. Install and validate

macOS/Linux:

```bash
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
python tests/verify.py
```

On Windows PowerShell, activate with `.venv\\Scripts\\Activate.ps1` instead of the dot command.

For the exact recorded dependency versions, install `examples/showcase/requirements-tested.txt` instead of requirements.txt on a compatible Python platform.

## 2. Generate the example inputs

```bash
python examples/showcase/generate_demo.py
```

This writes history.csv, sessions.csv, baseline-meta.json, and scan-meta.json inside examples/showcase/. The generated source data is deterministic and has no random seed or network dependency.

The history ends August 28, 2026. The full scan export adds August 29–September 4. A change is planted only in purchase sessions for /starter-kit traffic from google, with earlier funnel steps and traffic mix held fixed. Values and notification thresholds are demonstration choices, not recommended benchmarks.

## 3. Establish the baseline

```bash
python scripts/analyze.py \
  --data examples/showcase/history.csv \
  --config examples/showcase/config.yaml \
  --meta examples/showcase/baseline-meta.json \
  --mode baseline \
  --out examples/showcase/baseline
```

The engine writes baseline/findings.json and baseline/state.json. The baseline run produces a hygiene and sensitivity report. It does not emit anomaly alerts.

## 4. Scan against saved state

```bash
python scripts/analyze.py \
  --data examples/showcase/sessions.csv \
  --config examples/showcase/config.yaml \
  --meta examples/showcase/scan-meta.json \
  --state examples/showcase/baseline/state.json \
  --mode scan \
  --out examples/showcase/scan
```

Read scan/findings.json first. The affected page/source is /starter-kit × google; purchase conversion moves from 4.00% to 2.00%, and checkout completion from 40.00% to 20.00%. The engine models $28,000 revenue at stake. One root finding explains four overlapping findings. This localizes a change; it does not prove the cause or recovered revenue.

For subsequent real runs, persist and pass the previous scan/state.json. Keep baseline and output state separate from configuration.

## 5. Verify the demonstration and format proof

```bash
python examples/showcase/verify_demo.py
python examples/showcase/build_proof.py
```

Expected verification: 4 passed, 0 failed. The broader tests/verify.py recorded 28 passed, 0 failed. build_proof.py formats engine fields into proof.json; it does not recompute rates from source data. report.md is a human interpretation of the recorded findings.

## What is included

- Input: history.csv, sessions.csv, deterministic generate_demo.py.
- Configuration: config.yaml and both metadata files.
- Output: baseline/findings.json, baseline/state.json, scan/findings.json, scan/state.json.
- Presentation: report.md and proof.json.
- Verification: verify_demo.py, tests.txt, showcase-tests.txt, and requirements-tested.txt.
- Provenance: checksums.json and PUBLIC-EDITION.md at package root.

The recorded JSON files are exact engine outputs. Reproduced file hashes may differ because runtime and run timestamps are intentionally recorded; verify the numeric findings with verify_demo.py rather than requiring byte-identical runtime output. Generated CSV hashes should remain identical.

## Your own data

Start with DATA_CONTRACT.md. Map a settled read-only CSV or Parquet export to daily session counts, confirm the funnel and notification policy, reconcile the export, then create a fresh baseline. Do not reuse the demo's state, dates, event choices, or thresholds for your account. This package does not include live analytics connectors, storage, hosting, or scheduling.

#!/usr/bin/env python3
"""Regression assertions for the public synthetic showcase."""
import json
import subprocess
import sys
import tempfile
import yaml
from pathlib import Path

root_dir = Path(__file__).resolve().parent
out = json.loads((root_dir/"scan/findings.json").read_text())
# Also reproduce the headline when the business conversion is site-only.
with tempfile.TemporaryDirectory(prefix="showcase-site-only-") as temporary:
    temp = Path(temporary)
    config = yaml.safe_load((root_dir/"config.yaml").read_text())
    config["funnel"]["steps"][-1]["alert_level"] = "site"
    (temp/"config.yaml").write_text(yaml.safe_dump(config))
    command = [sys.executable, str(root_dir.parents[1]/"scripts/analyze.py"),
               "--data",str(root_dir/"sessions.csv"),"--config",str(temp/"config.yaml"),
               "--meta",str(root_dir/"scan-meta.json"),"--state",str(root_dir/"baseline/state.json"),
               "--mode","scan","--out",str(temp/"scan")]
    result = subprocess.run(command, capture_output=True, text=True)
    site_only = json.loads((temp/"scan/findings.json").read_text()) if result.returncode == 0 else {}
checks = []
checks.append(("Headline uses the configured purchase conversion, not the first funnel step",
               out["site"]["step"] == "step_purchase" and out["site"]["cur_rate"] == 0.03667
               and site_only.get("site",{}).get("step") == "step_purchase"
               and site_only.get("site",{}).get("cur_rate") == 0.03667))
roots = [a for a in out["alerts"] if a["is_root"]]
checks.append(("Finds the planted page/source segment with its expected rate and modeled impact",
               len(roots) == 1 and roots[0]["dims"] == {"landing_page":"/starter-kit","utm_source":"google"}
               and roots[0]["cur_rate"] == 0.02 and roots[0]["base_rate_adj"] == 0.04
               and roots[0]["revenue_at_stake"] == 28000.0 and roots[0]["cur_sessions"] == 14000.0))
a = roots[0] if roots else {}
checks.append(("Localizes the change to checkout and leaves preceding transitions unchanged",
               a.get("funnel",{}).get("worst_transition") == "step_begin_checkout -> step_purchase"
               and a["funnel"]["transitions"][-1]["base_rate"] == 0.4
               and a["funnel"]["transitions"][-1]["cur_rate"] == 0.2
               and all(t["rel_change"] == 0 for t in a["funnel"]["transitions"][:-1])))
checks.append(("Explains overlapping aggregates without inventing mix or tracking findings",
               out["scan_stats"]["explained_by_a_root"] == 4 and out["scan_stats"]["roots"] == 1
               and out["mix_shifts"] == [] and out["tracking_breaks"] == []
               and all(x["explained_by"] == a.get("fingerprint") for x in out["alerts"] if not x["is_root"])))
for label, success in checks:
    print(("PASS " if success else "FAIL ")+label)
print(f"{sum(ok for _,ok in checks)} passed, {sum(not ok for _,ok in checks)} failed")
raise SystemExit(0 if all(ok for _,ok in checks) else 1)

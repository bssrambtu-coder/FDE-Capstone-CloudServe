"""Derive the auto-provisioned Grafana dashboard from the importable one.

    python scripts/sync_grafana_dashboard.py

monitoring/grafana_dashboard.json is the source of truth and stays importable
by hand (Dashboards → Import), which is why it carries a `${DS_PROMETHEUS}`
placeholder: Grafana fills that in from a dropdown during import. Provisioning
does not go through that screen, so the placeholder would be passed through
literally and every panel would report "datasource not found". This writes the
copy docker-compose loads, with the placeholder bound to the provisioned
datasource and a time window suited to a live demo.

A test fails if the copy drifts from the source, so edit the source and rerun
this rather than editing the copy.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "monitoring/grafana_dashboard.json"
TARGET = ROOT / "monitoring/grafana/dashboards/cloudserve_triage.json"
DATASOURCE_UID = "cloudserve-prometheus"  # matches monitoring/grafana/provisioning/datasources


def provisioned(source: dict) -> dict:
    text = json.dumps(source).replace("${DS_PROMETHEUS}", DATASOURCE_UID)
    d = json.loads(text)
    d.pop("__inputs", None)
    d.pop("__requires", None)
    # A demo is minutes long; the source's six-hour window would show a sliver.
    d["time"] = {"from": "now-15m", "to": "now"}
    d["refresh"] = "5s"
    return d


def render() -> str:
    return json.dumps(provisioned(json.loads(SOURCE.read_text(encoding="utf-8"))), indent=2) + "\n"


def main() -> int:
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(render(), encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

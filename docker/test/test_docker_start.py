#!/usr/bin/env python3
# Copyright (C) 2026 Donnie V. Savage
"""Run real docker-start daemon validation against a YAML startup UUT matrix.

Use a stub for FRR's daemon_list and watchfrr executable, without needing Docker.
The production validation block is executed unchanged; startup is tested in
full by tools/docker-test.sh against a built image.
"""

import os
from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[2]
START = ROOT / "docker/docker-start"
SCENARIO = ROOT / "docker/test/uut/scenario-daemon-start.yaml"
MARKER = 'read -r -a running_daemons <<< "$(daemon_list)"'


def run():
    source = START.read_text()
    assert source.count(MARKER) == 1, "docker-start daemon_list entrypoint changed"
    guard = source[source.index(MARKER):]
    scenario = yaml.safe_load(SCENARIO.read_text())
    assert scenario["schema"] == "docker-daemon-uut/v1"
    assert "eigrpd=yes" in (ROOT / "docker/daemons").read_text().splitlines(), \
        "bundled FRR config must enable eigrpd by default"

    harness = '''set -eo pipefail
    daemon_list() { printf '%s\\n' "$TEST_DAEMONS"; }
    exec() { printf 'WATCHFRR:%s\\n' "$*"; }
    '''
    cases = scenario["cases"]
    for case in cases:
        env = dict(os.environ, TEST_DAEMONS=" ".join(case["daemons"]))
        completed = subprocess.run(["bash", "-c", harness + guard], env=env,
                                   text=True, capture_output=True, check=False)
        if case["result"] == "start":
            assert completed.returncode == 0, (case["name"], completed.stderr)
            assert completed.stdout.strip() == "WATCHFRR:/usr/lib/frr/watchfrr " + env["TEST_DAEMONS"], \
                (case["name"], completed.stdout)
            warning = "warning: eigrpd is disabled" in completed.stderr
            assert warning == (not case["eigrp"]), (case["name"], completed.stderr)
        elif case["result"] == "error":
            assert completed.returncode == 1, (case["name"], completed.returncode)
            assert f"error: FRR daemon {case['missing']} is not enabled or not installed" in completed.stderr, \
                (case["name"], completed.stderr)
            assert "WATCHFRR:" not in completed.stdout, case["name"]
        else:
            raise AssertionError(f"unknown expected result: {case['result']}")
        print(f"PASS: {case['name']}")
    print(f"PASS: {len(cases)} Docker daemon startup UUT cases")


if __name__ == "__main__":
    try:
        run()
    except (AssertionError, OSError, KeyError, ValueError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        sys.exit(1)

#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise the scale build dependency through actual make recipes."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def build_tree(tmp_path):
    root = tmp_path / "openeigrp"
    scale = root / "eigrp/test/scale"
    scale.mkdir(parents=True)
    shutil.copy2(ROOT / "Makefile", root / "Makefile")
    shutil.copy2(ROOT / "eigrp/test/scale/Makefile", scale / "Makefile")

    for directory, event, target in (
        ("eigrp/code", "core", "all"),
        ("libunix/code", "unix", "all"),
        ("eigrp/test/uut", "node", "eigrp-uut-node"),
    ):
        child = root / directory
        child.mkdir(parents=True)
        (child / "Makefile").write_text(
            f".PHONY: {target}\n"
            f"{target}:\n"
            f"\t@printf '{event}\\n' >> '$(LOG_FILE)'\n"
            f"\t@if [ '$(FAIL_TARGET)' = '{event}' ]; then exit 17; fi\n"
        )

    runner = root / "runner"
    runner.write_text(
        "#!/bin/sh\n"
        "printf 'run %s\\n' \"$*\" >> \"$LOG_FILE\"\n"
    )
    runner.chmod(0o755)
    return scale, root / "events", runner


def run_scale(build_tree, target, fail_target=""):
    scale, log, runner = build_tree
    env = os.environ.copy()
    for name in ("MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS", "MAKEOVERRIDES"):
        env.pop(name, None)
    env["LOG_FILE"] = str(log)
    env["FAIL_TARGET"] = fail_target
    result = subprocess.run(
        ["make", "--no-print-directory", "-C", str(scale), target,
         f"PYTHON={runner}"],
        env=env, capture_output=True, text=True,
    )
    events = log.read_text().splitlines() if log.exists() else []
    return result, events


def test_internal_builds_portable_libraries_before_node_and_routes(build_tree):
    result, events = run_scale(build_tree, "internal")
    assert result.returncode == 0, result.stdout + result.stderr
    assert events[:3] == ["core", "unix", "node"]
    assert len(events) == 4
    assert events[3].endswith("routes_100k.py 1")


@pytest.mark.parametrize("failure,expected", [
    ("core", ["core"]),
    ("unix", ["core", "unix"]),
    ("node", ["core", "unix", "node"]),
])
def test_internal_stops_before_route_injection_on_build_failure(build_tree, failure, expected):
    result, events = run_scale(build_tree, "internal", fail_target=failure)
    assert result.returncode != 0
    assert events == expected


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-q"]))

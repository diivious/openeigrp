# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Run actual multi-scenario Make recipes with a failing test command."""
from pathlib import Path
import subprocess


UUT = Path(__file__).resolve().parents[1]


def test_named_rtp_gate_stops_after_first_failure(tmp_path):
    lines = (UUT / "Makefile").read_text().splitlines(keepends=True)
    start = next(i for i, line in enumerate(lines) if line.startswith("rtp-core:"))
    end = next(i for i in range(start + 1, len(lines))
               if lines[i].startswith("all-scenarios:"))
    recipe = "".join(lines[start:end])

    stub = tmp_path / "fake-python"
    log = tmp_path / "calls"
    stub.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' \"$*\" >> '{log}'\n"
        "case \"$*\" in *lost-reliable-packet.yaml*) exit 9 ;; esac\n"
    )
    stub.chmod(0o755)
    makefile = tmp_path / "Makefile"
    makefile.write_text(
        f"PYTHON := {stub}\nLOG_TEST := test-log\n"
        ".PHONY: eigrp-uut-node rtp-core\n"
        "eigrp-uut-node:\n\t@:\n" + recipe
    )
    result = subprocess.run(["make", "--no-print-directory", "rtp-core"], cwd=tmp_path,
                            text=True, capture_output=True)
    assert result.returncode != 0
    calls = log.read_text().splitlines()
    assert len(calls) == 2
    assert "srtt-rto.yaml" in calls[0]
    assert "lost-reliable-packet.yaml" in calls[1]
    assert "lost-ack.yaml" not in "\n".join(calls)

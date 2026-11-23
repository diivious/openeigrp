#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise scale MD5 startup, route exchange, withdrawal, and recovery.

Run after building eigrp/test/uut/eigrp-uut-node. Uses real UUT processes,
portable authentication, RTP, redistribution and Unix virtual wire.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
import tempfile
import time


_spec = importlib.util.spec_from_file_location(
    "eigrp_scale_auth_md5_startup", Path(__file__).with_name("stress_extended.py"))
if _spec is None or _spec.loader is None:
    raise ImportError("cannot load scale stress driver")
scale = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(scale)


def test_auth_md5_startup_withdraw_recover():
    # A missing UUT binary is a build prerequisite failure, not a skip.
    assert scale.NODE.is_file(), "build eigrp/test/uut/eigrp-uut-node first"
    with tempfile.TemporaryDirectory(prefix="eigrp-scale-md5-regress-") as tmp:
        with scale.paired_nodes(Path(tmp), 3, md5=True) as (nodes, broker):
            for name in ("r1", "r2"):
                config = (Path(tmp) / f"{name}.conf").read_text()
                assert "auth-md5 eth0 scale-test-keychain scale-md5-secret 7\n" in config
            def wait(routes: int, label: str):
                _, up, queues = scale.wait_for(
                    nodes["r2"], routes + 1, time.monotonic() + 30, label,
                    maximum=routes + 1, min_peers=1,
                    min_rib=routes, max_rib=routes)
                assert len(up) == 1
                assert all(not oq and not rq for _, oq, rq in queues)
                scale.check_live(nodes, broker=broker)
            wait(3, "authenticated initial routes")
            scale.command(nodes["r1"], "SOURCE-DEL " + scale.prefix(0))
            wait(2, "authenticated withdrawal")
            scale.command(nodes["r1"], "SOURCE-ADD " + scale.prefix(0) + " 1")
            wait(3, "authenticated recovery")

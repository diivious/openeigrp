#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise virtual-wire endpoint retirement across a peer restart."""

import importlib.util
import socket
import tempfile
import time
from pathlib import Path

import pytest


UUT = Path(__file__).resolve().parents[1]


def load_runner():
    spec = importlib.util.spec_from_file_location("uut_broker_detach", UUT / "run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def register(module, sock, address, interface="eth0"):
    segment = b"transit"
    iface = interface.encode("ascii")
    src = socket.inet_pton(socket.AF_INET, address).ljust(16, b"\0")
    frame = module.HDR.pack(1, module.REGISTER, 4, 0, 1,
                            len(segment), len(iface), 0, 0, src, bytes(16))
    sock.sendall(frame + segment + iface)


@pytest.fixture
def short_wire_path():
    # macOS limits AF_UNIX paths to 103 bytes; pytest's tmp_path may exceed it.
    with tempfile.TemporaryDirectory(prefix="eigrp-wire-", dir="/tmp") as root:
        yield str(Path(root) / "wire.sock")


def test_stop_waits_until_old_wire_endpoint_is_retired(tmp_path, short_wire_path):
    module = load_runner()
    owner = {(4, socket.inet_pton(socket.AF_INET, "10.0.0.2")): "r2"}
    broker = module.Broker(short_wire_path, owner)
    broker.start()
    assert broker.ready.wait(2)
    old = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old.connect(broker.path)
    register(module, old, "10.0.0.2")
    register(module, old, "10.0.0.2", "eth1")
    # A following packet is processed only after both registrations.
    segment, iface = b"transit", b"eth1"
    src = socket.inet_pton(socket.AF_INET, "10.0.0.2").ljust(16, b"\0")
    payload = bytes([2, 5]) + bytes(18)
    old.sendall(module.HDR.pack(1, module.PACKET, 4, 0, 1,
                                len(segment), len(iface), len(payload), 0,
                                src, bytes(16)) + segment + iface + payload)
    try:
        limit = time.monotonic() + 2
        while not any(p.get("event") == "sent" and p.get("source_interface") == "eth1"
                      for p in broker.journal):
            assert time.monotonic() < limit, "registrations not observed"
            time.sleep(.01)
        assert not broker.wait_node_closed("r2", 0)

        class StoppedNode:
            def stop(self):
                old.close()

        runner = module.Runner({}, {}, tmp_path)
        runner.broker = broker
        runner.nodes["r2"] = StoppedNode()
        runner.step("stop", "r2")
        assert broker.wait_node_closed("r2", 0)

        # Reused address registration must describe only the new endpoint.
        new = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        new.connect(broker.path)
        register(module, new, "10.0.0.2")
        try:
            limit = time.monotonic() + 2
            while broker.wait_node_closed("r2", 0):
                assert time.monotonic() < limit, "restart registration not observed"
                time.sleep(.01)
            new.close()
            assert broker.wait_node_closed("r2", 2)
        finally:
            new.close()
    finally:
        old.close()
        broker.close()
    assert broker.error is None


def test_shutdown_ack_fault_is_removed_before_restart():
    module = load_runner()
    scenario = module.validate_scenario(module.load_yaml(UUT.parent / "rtp/peer-restart.yaml"))
    broker = module.Broker("/nonexistent", {})
    ack = {"source_uut": "r1", "destination_uut": "r2", "opcode": "ack"}
    for step in scenario["steps"]:
        op, value = next(iter(step.items()))
        if op == "fault-add":
            broker.add_fault(value)
        elif op == "fault-remove":
            broker.remove_fault(value)
        elif op == "stop":
            assert any(a["action"] == "drop" for a in broker._actions(ack))
        elif op == "start" and value == "r2":
            assert broker._actions(ack) == []


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__]))

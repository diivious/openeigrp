# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise the UUT broker's deterministic REPLY gate for concurrent ACTIVE."""

from pathlib import Path
import importlib.util


RUN = Path(__file__).resolve().parents[1] / "run.py"
SPEC = importlib.util.spec_from_file_location("uut_run_k6_gate", RUN)
uut = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(uut)


SCENARIO = RUN.parent / "mesh-topology" / "k6-b-concurrent-multiprefix.yaml"


def test_concurrent_active_reply_gate_blocks_only_expected_direction(tmp_path):
    scenario = uut.validate_scenario(uut.load_yaml(SCENARIO))
    steps = scenario["steps"]
    gates = [step["fault-add"] for step in steps if "fault-add" in step]
    assert len(gates) == 1
    gate = gates[0]
    assert gate == {
        "name": "hold-r3-replies", "action": "drop",
        "source_uut": "r3", "destination_uut": "r1", "opcode": "reply",
    }
    begin = next(i for i, step in enumerate(steps)
                 if "links-down" in step)
    active = next(i for i, step in enumerate(steps)
                  if "validate-convergence" in step
                  and any(e.get("route", {}).get("active") is True
                          for e in step["validate-convergence"]["expect"]))
    release = next(i for i, step in enumerate(steps)
                   if step.get("fault-remove") == gate["name"])
    assert next(i for i, step in enumerate(steps) if "fault-add" in step) < begin < active < release

    broker = uut.Broker(str(tmp_path / "wire.sock"), {})
    broker.add_fault(gate)
    reply = {"source_uut": "r3", "destination_uut": "r1",
             "opcode": "reply", "prefixes": ["10.120.0.0/24"]}
    assert any(f["action"] == "drop" for f in broker._actions(reply))
    assert broker.fault_state(gate["name"])["hits"] == 1
    for attrs in ({"source_uut": "r4"}, {"destination_uut": "r2"},
                  {"opcode": "query"}):
        assert not broker._actions({**reply, **attrs})
    assert broker.remove_fault(gate["name"])
    assert not broker._actions(reply)

# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Donnie V. Savage
"""Actual validate-event operator under deterministic test-clock scheduling."""
import importlib.util
from pathlib import Path

import pytest

UUT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('eigrp_uut_event_poll', UUT / 'run.py')
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)


def runner(clock):
    obj = mod.Runner({}, {}, Path('.'), clock=clock)
    obj.verbose = False
    return obj


def test_delayed_event_eventually_passes_without_wallclock():
    clock = mod.ManualClock()
    uut = runner(clock)
    uut.event_ok = lambda e: clock.now() >= .15
    uut.step('validate-event', {'uut': 'r1', 'contains': 'update', 'timeout': .5})
    assert .15 <= clock.now() < .5


def test_absence_is_observed_through_entire_window():
    clock = mod.ManualClock()
    uut = runner(clock)
    uut.event_ok = lambda e: True
    uut.step('validate-event', {'uut': 'r1', 'contains': 'fault',
                                'min': 0, 'max': 0, 'timeout': .2})
    assert clock.now() == pytest.approx(.2)


def test_late_event_does_not_pass_after_deadline():
    clock = mod.ManualClock()
    uut = runner(clock)
    def slow(e):
        clock.advance(.31)
        return True
    uut.event_ok = slow
    with pytest.raises(AssertionError, match='timed out'):
        uut.step('validate-event', {'uut': 'r1', 'contains': 'late', 'timeout': .3})


def test_late_bad_event_is_rejected_in_negative_window():
    clock = mod.ManualClock()
    uut = runner(clock)
    uut.event_ok = lambda e: clock.now() < .2
    with pytest.raises(AssertionError, match='upper bound'):
        uut.step('validate-event', {'uut': 'r1', 'contains': 'bad',
                                    'min': 0, 'max': 0, 'timeout': .3})


@pytest.mark.parametrize('bad', [
    {'timeout': 0}, {'timeout': -1}, {'timeout': float('inf')},
    {'timeout': False}, {'min': 3, 'max': 1}, {'min': -1},
    {'contains': ''},
])
def test_invalid_event_observation_rejected_at_scenario_validation(bad):
    event = {'uut': 'r1', 'contains': 'start', **bad}
    with pytest.raises(mod.SpecError):
        mod.validate_scenario({'schema': mod.TEST_SCHEMA,
                               'topology': 'two-router.yaml',
                               'steps': [{'validate-event': event}]})

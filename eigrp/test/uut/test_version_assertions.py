#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise real UUT capability decoding and strict assertion validation."""

import pytest

from eigrp.test.uut.run import (SpecError, capability_matches,
                                capability_record, validate_capability_assertion)


RECORD = ("CAPABILITY|release=diivious-0.5.0-dev|tlv1=1|tlv2=1|"
          "wide_metrics=1|ipv4=1|ipv6=1|bfd=0|snmp=0")


def test_decodes_real_tech_support_record():
    actual = capability_record(["PROTOCOL|instance=x", RECORD])
    assert capability_matches(actual, {"uut": "r1", "release": "diivious-0.5.0-dev",
                                       "tlv1": True, "snmp": False})
    assert not capability_matches(actual, {"uut": "r1", "release": "0.5.0"})
    assert not capability_matches(actual, {"uut": "r1", "ipv6": False})


@pytest.mark.parametrize("input", [
    {"uut": "r1", "ipv4": 1}, {"uut": "r1", "release": ""},
    {"uut": "r1", "unknown": True}, {"uut": "r1"},
])
def test_invalid_assertion_is_rejected(input):
    with pytest.raises(SpecError):
        validate_capability_assertion(input, "test")


@pytest.mark.parametrize("record", [
    "CAPABILITY|release=0.1.0|ipv4=1",
    RECORD.replace("|ipv6=1", "|ipv6=yes"),
    RECORD.replace("release=diivious-0.5.0-dev", "release="),
])
def test_malformed_capability_record_is_rejected(record):
    with pytest.raises(AssertionError):
        capability_record([record])

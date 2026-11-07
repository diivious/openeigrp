#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Independent Cisco default-K route-metric oracle for captured EIGRP TLVs.

Inputs are decoded on-wire fields, not the portable core's internal metric
representation. Keep this independent from eigrp_metric_calculate() so a
portable arithmetic regression cannot make its own interoperability test pass.
RFC 7868 section 5.6 provides the scales; observed Cisco TLV1/TLV2 captures
establish which representation is present in each field.
"""

CLASSIC_SCALE = 256
WIDE_SCALE = 65536
BANDWIDTH_REFERENCE_KBPS = 10000000
PS_PER_US = 1000000
PS_PER_CLASSIC_DELAY = 10 * PS_PER_US
TLV1_INACCESSIBLE = (1 << 32) - 1
TLV2_INACCESSIBLE = (1 << 48) - 1
TLV2_INTERNAL = 0x0602
TLV2_EXTERNAL = 0x0603

# Values specific to the 2R redistribution qualification, not protocol defaults.
REDIST_BW_KBPS = 100000
REDIST_DELAY = 10
REDIST_DELAY_PS = REDIST_DELAY * PS_PER_CLASSIC_DELAY
REDIST_MTU = 1500


def cisco_wide_metric(metric):
    """Default K1=K3=1 metric from TLV2 kbit/s and picosecond fields."""
    bandwidth, delay = metric['bandwidth'], metric['delay']
    if (type(bandwidth) is not int or not 0 < bandwidth < TLV2_INACCESSIBLE):
        raise ValueError('invalid/reachable TLV2 bandwidth')
    if (type(delay) is not int or not 0 <= delay < TLV2_INACCESSIBLE):
        raise ValueError('invalid/reachable TLV2 delay')
    return ((BANDWIDTH_REFERENCE_KBPS * WIDE_SCALE) // bandwidth
            + (delay * WIDE_SCALE) // PS_PER_US)


def cisco_classic_metric(metric):
    """Default K1=K3=1 metric from already-scaled TLV1 vector fields."""
    bandwidth, delay = metric['bandwidth'], metric['delay']
    if (type(bandwidth) is not int or not 0 < bandwidth < TLV1_INACCESSIBLE):
        raise ValueError('invalid/reachable TLV1 bandwidth')
    if (type(delay) is not int or not 0 <= delay < TLV1_INACCESSIBLE):
        raise ValueError('invalid/reachable TLV1 delay')
    return min(TLV1_INACCESSIBLE, bandwidth + delay)


def require_tlv2_types(route, kind):
    """Refuse to apply TLV2 mathematics without captured TLV2 type evidence."""
    expected = TLV2_INTERNAL if kind == 'internal' else TLV2_EXTERNAL
    if route.get('types') != [expected] or not route.get('metrics'):
        raise ValueError(f'{kind} TLV2 codec evidence missing or mixed')


def redist_seed_matches(metric):
    return (metric.get('bandwidth') == REDIST_BW_KBPS
            and metric.get('delay') == REDIST_DELAY_PS
            and metric.get('mtu') == REDIST_MTU)

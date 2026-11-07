#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Cisco classic/wide arithmetic and multi-gig TLV2 metric regressions."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'common'))
from metric import (cisco_classic_metric, cisco_wide_metric,
                    redist_seed_matches, require_tlv2_types,
                    TLV1_INACCESSIBLE, TLV2_INACCESSIBLE)


class MetricTests(unittest.TestCase):
    def test_classic_scaled_wire_representation(self):
        # TLV1 carries inverse bandwidth and classic delay *already scaled by 256*.
        self.assertEqual(cisco_classic_metric({'bandwidth': 25600, 'delay': 2560}),
                         28160)
        self.assertEqual(cisco_classic_metric({'bandwidth': 256000, 'delay': 25600}),
                         281600)
        self.assertEqual(cisco_classic_metric({'bandwidth': 1, 'delay': 0}), 1)
        for metric in ({'bandwidth': 0, 'delay': 256},
                       {'bandwidth': TLV1_INACCESSIBLE, 'delay': 256},
                       {'bandwidth': 256, 'delay': TLV1_INACCESSIBLE}):
            with self.subTest(metric=metric), self.assertRaises(ValueError):
                cisco_classic_metric(metric)

    def test_wide_cisco_observed_vectors(self):
        # 8 Gbit/s / 1.25 us is present in IOS XE 17.3.6 TLV2 captures.
        self.assertEqual(cisco_wide_metric({'bandwidth': 8000000,
                                            'delay': 1250000}), 163840)
        # 25600/2560 is an old erroneous OpenEIGRP TLV2 encoding. Its
        # calculated value must remain visible as 25600167, not be normalized.
        self.assertEqual(cisco_wide_metric({'bandwidth': 25600,
                                            'delay': 2560}), 25600167)

    def test_multigig_default_delay_and_fractional_precision(self):
        # Fixed independent expected distances; never calculate the expected
        # value with the same formula being tested. Truncation matters at 50G+.
        distances = {1: 1310720, 2: 655360, 5: 262144,
                     10: 131072, 20: 65536, 50: 26214,
                     100: 13106, 200: 6552, 500: 2620}
        for gbps, expected in distances.items():
            with self.subTest(gbps=gbps):
                bandwidth = gbps * 1000000
                delay_ps = 10000000 // gbps
                self.assertEqual(cisco_wide_metric({'bandwidth': bandwidth,
                                                    'delay': delay_ps}), expected)
        # 2G delay contributes half a microsecond *before* multiplication.
        self.assertEqual(cisco_wide_metric({'bandwidth': 2000000,
                                            'delay': 5000000}), 655360)
        self.assertEqual(cisco_wide_metric({'bandwidth': 100000000,
                                            'delay': 100000}), 13106)

    def test_reject_invalid_wide_components_and_infinity(self):
        invalid = ({'bandwidth': 0, 'delay': 1},
                   {'bandwidth': TLV2_INACCESSIBLE, 'delay': 1},
                   {'bandwidth': 1, 'delay': TLV2_INACCESSIBLE},
                   {'bandwidth': True, 'delay': 100},
                   {'bandwidth': 1000000, 'delay': -1})
        for item in invalid:
            with self.subTest(item=item), self.assertRaises(ValueError):
                cisco_wide_metric(item)

    def test_metric_origin_requires_correct_tlv_type(self):
        for types in ([], [0x0102], [0x0602, 0x0102], [0x0603]):
            with self.subTest(types=types), self.assertRaises(ValueError):
                require_tlv2_types({'types': types, 'metrics': [1]}, 'internal')
        require_tlv2_types({'types': [0x0602], 'metrics': [1]}, 'internal')
        require_tlv2_types({'types': [0x0603], 'metrics': [1]}, 'external')

    def test_exact_redist_seed_and_corrected_mtu(self):
        self.assertTrue(redist_seed_matches({'bandwidth': 100000,
                                             'delay': 100000000, 'mtu': 1500}))
        self.assertFalse(redist_seed_matches({'bandwidth': 100000,
                                              'delay': 100000000, 'mtu': 14419200}))


if __name__ == '__main__':
    unittest.main()

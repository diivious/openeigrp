#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Regression checks for Cisco wide-metric report validation."""

import importlib.util
import unittest
from pathlib import Path


SOURCE = Path(__file__).with_name('metric_parity_report.py')
spec = importlib.util.spec_from_file_location('metric_parity_report', SOURCE)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class MetricParityReportTest(unittest.TestCase):
    def test_cisco_wide_metric_preserves_fractional_latency(self):
        self.assertEqual(module.cisco_wide_metric(
            {'bandwidth': 25600, 'delay': 2560}), 25600167)
        self.assertEqual(module.cisco_wide_metric(
            {'bandwidth': 8000000, 'delay': 1250000}), 163840)

    def test_cisco_wide_metric_uses_multiply_before_divide(self):
        self.assertEqual(module.cisco_wide_metric(
            {'bandwidth': 100000, 'delay': 100000000}), 13107200)
        self.assertEqual(module.cisco_wide_metric(
            {'bandwidth': 100000, 'delay': 1000000000}), 72089600)

    def test_invalid_bandwidth_rejected(self):
        with self.assertRaisesRegex(ValueError, 'bandwidth'):
            module.cisco_wide_metric({'bandwidth': 0, 'delay': 5})

    def test_regression_capture_is_rejected(self):
        import json
        fixture = Path(__file__).parent / 'fixtures' / 'metric-parity-20261105.json'
        report = json.loads(fixture.read_text(encoding='utf-8'))
        failures = module.audit_report(report)
        self.assertTrue(any('ce->oe' in f and 'reported distance' in f
                            for f in failures), failures)
        self.assertTrue(any('external seed TLV2 units' in f
                            for f in failures), failures)
        self.assertTrue(any('non-increasing RD/CD' in f
                            for f in failures), failures)

    def test_corrected_reference_report_passes(self):
        import copy
        import json
        fixture = Path(__file__).parent / 'fixtures' / 'metric-parity-20261105.json'
        report = copy.deepcopy(json.loads(fixture.read_text(encoding='utf-8')))
        for af in ('ipv4', 'ipv6'):
            topology = report['objectives'][f'DUAL-04/{af}']['single_path_distances']
            topology['oe']['rd'] = 163840
            external = report['objectives'][f'DUAL-02/{af}']['wire']['oe']
            for vector in external['metrics']:
                vector['bandwidth'] = 100000
                vector['delay'] = 100000000
            cycle = report['objectives'][f'DUAL-03/{af}']['ce']
            cycle['after_distance']['rd'] = cycle['before_distance']['rd'] + 1
            cycle['after_distance']['cd'] = cycle['before_distance']['cd'] + 1
        self.assertEqual(module.audit_report(report), [])


if __name__ == '__main__':
    unittest.main()

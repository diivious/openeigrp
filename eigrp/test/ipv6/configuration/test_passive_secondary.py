#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Exercise portable IPv6 secondary/passive admission in compiled C code."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


class PassiveSecondaryTests(unittest.TestCase):
    def test_actual_network_implementation(self):
        root = Path(__file__).resolve().parents[4]
        compiler = shutil.which('cc')
        self.assertIsNotNone(compiler, 'C compiler required for real implementation test')
        harness = Path(__file__).with_name('passive_secondary_harness.c')
        linker_gc = '-Wl,-dead_strip' if sys.platform == 'darwin' else '-Wl,--gc-sections'
        with tempfile.TemporaryDirectory() as td:
            binary = Path(td) / 'passive-secondary'
            command = [compiler, '-std=gnu11', '-D_GNU_SOURCE',
                       '-ffunction-sections', '-fdata-sections',
                       '-I', str(root / 'eigrp/code'),
                       linker_gc, '-o', str(binary),
                       str(harness), '-lpthread']
            built = subprocess.run(command, text=True, capture_output=True)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            result = subprocess.run([str(binary)], text=True, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertIn('PASS secondary IPv6 passive', result.stdout)

    def test_macos_linker_flag(self):
        commands = []

        def capture(command, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0,
                                               'PASS secondary IPv6 passive', '')

        with mock.patch.object(sys, 'platform', 'darwin'), \
                mock.patch('subprocess.run', side_effect=capture):
            self.test_actual_network_implementation()

        self.assertEqual(len(commands), 2)
        self.assertIn('-Wl,-dead_strip', commands[0])
        self.assertNotIn('-Wl,--gc-sections', commands[0])


if __name__ == '__main__':
    unittest.main()

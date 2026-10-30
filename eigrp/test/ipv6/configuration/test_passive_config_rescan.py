#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Compile and execute the actual passive-configuration handler."""
import pathlib
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = pathlib.Path(__file__).resolve().parents[4]
SOURCE = ROOT / 'eigrp/code/eigrp_interface.c'
HARNESS = pathlib.Path(__file__).with_name('passive_config_rescan_harness.c')


def handler(source):
    marker = 'eigrp_result_t eigrp_intf_passive_update('
    start = source.index(marker)
    opening = source.index('{', start)
    depth = 1
    index = opening + 1
    while depth:
        depth += (source[index] == '{') - (source[index] == '}')
        index += 1
    return source[start:index] + '\n'


def run_handler(code):
    compiler = shutil.which('cc')
    if compiler is None:
        raise RuntimeError('C compiler required')
    linker_gc = '-Wl,-dead_strip' if sys.platform == 'darwin' else '-Wl,--gc-sections'
    with tempfile.TemporaryDirectory() as temp:
        temp = pathlib.Path(temp)
        (temp / 'passive_config_function.inc').write_text(code)
        (temp / HARNESS.name).write_text(HARNESS.read_text())
        binary = temp / 'test-passive-config'
        command = [compiler, '-std=gnu11', '-Wall', '-Wextra', '-Werror',
                   '-ffunction-sections', '-fdata-sections', linker_gc,
                   '-I', str(ROOT / 'eigrp/code'), '-I', str(temp),
                   str(temp / HARNESS.name), '-o', str(binary)]
        built = subprocess.run(command, text=True, capture_output=True)
        if built.returncode:
            raise AssertionError('C compilation failed:\n' + built.stderr)
        return subprocess.run([str(binary)], text=True, capture_output=True)


class PassiveConfigRescanTests(unittest.TestCase):
    def test_real_handler_rescans(self):
        result = run_handler(handler(SOURCE.read_text()))
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('PASS IPv6 passive configuration', result.stdout)

    def test_macos_linker_flag(self):
        commands = []

        def capture(command, **kwargs):
            commands.append(command)
            return subprocess.CompletedProcess(command, 0,
                                               'PASS IPv6 passive configuration', '')

        with mock.patch.object(sys, 'platform', 'darwin'), \
                mock.patch('subprocess.run', side_effect=capture):
            run_handler(handler(SOURCE.read_text()))

        self.assertEqual(len(commands), 2)
        self.assertIn('-Wl,-dead_strip', commands[0])
        self.assertNotIn('-Wl,--gc-sections', commands[0])


if __name__ == '__main__':
    unittest.main()

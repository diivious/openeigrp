#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Verify the actual capture process lifecycle with an isolated tcpdump stub."""
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('capture', ROOT / 'common/capture.py')
capture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(capture)


class CaptureTests(unittest.TestCase):
    def test_start_stop_and_packet_prerequisite(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp)/'tcpdump'
            exe.write_text('#!/usr/bin/env python3\n'
                           'import pathlib,signal,sys,time\n'
                           'p=pathlib.Path(sys.argv[sys.argv.index("-w")+1])\n'
                           'p.write_bytes(bytes.fromhex("d4c3b2a1020004000000000000000000ffff000001000000"))\n'
                           'signal.signal(signal.SIGINT,lambda *a: sys.exit(0))\n'
                           'while True: time.sleep(.01)\n')
            exe.chmod(0o755)
            state = Path(tmp)/'case.capture.json'
            pcap = Path(tmp)/'wire.pcap'
            with mock.patch.dict(os.environ, {'PATH': tmp + ':' + os.environ['PATH']}):
                capture.start('veth1', pcap, state)
                self.assertTrue(state.exists())
                with self.assertRaisesRegex(RuntimeError, 'no packets'):
                    capture.stop(state, require_packets=True)
                self.assertTrue(state.exists())
                capture.cleanup(tmp)
                self.assertFalse(state.exists())

    def test_failed_start_leaves_no_recorded_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp)/'tcpdump'
            exe.write_text('#!/bin/sh\nexit 3\n')
            exe.chmod(0o755)
            state = Path(tmp)/'case.capture.json'
            with mock.patch.dict(os.environ, {'PATH': tmp + ':' + os.environ['PATH']}):
                with self.assertRaisesRegex(RuntimeError, 'failed to start'):
                    capture.start('veth1', Path(tmp)/'wire.pcap', state)
            self.assertFalse(state.exists())

    def test_refuse_invalid_interface(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                capture.start('x;touch /tmp/hijack', Path(tmp)/'a.pcap', Path(tmp)/'b.capture.json')


if __name__ == '__main__':
    unittest.main()

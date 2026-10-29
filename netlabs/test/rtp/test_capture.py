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
                self.assertFalse(state.exists())
                capture.cleanup(tmp)

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

    def test_unexpected_capture_exit_is_not_pid_identity_mismatch(self):
        """Exercise a real exited child even when procfs has reaped it."""
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'tcpdump'
            exe.write_text('#!/usr/bin/env python3\n'
                           'import pathlib,sys,time\n'
                           'p=pathlib.Path(sys.argv[sys.argv.index("-w")+1])\n'
                           'p.write_bytes(bytes.fromhex("d4c3b2a1020004000000000000000000ffff000001000000"))\n'
                           'time.sleep(.3)\n'
                           'sys.exit(9)\n')
            exe.chmod(0o755)
            state = Path(tmp) / 'dead.capture.json'
            pcap = Path(tmp) / 'dead.pcap'
            with mock.patch.dict(os.environ, {'PATH': tmp + ':' + os.environ['PATH']}):
                capture.start('veth1', pcap, state)
                # Ensure the child really exited before exercising stop().
                import json
                pid = json.loads(state.read_text())['pid']
                self.assertEqual(capture._PROCESSES[pid].wait(timeout=3), 9)
                with mock.patch.object(capture.os, 'killpg') as kill:
                    with self.assertRaisesRegex(RuntimeError,
                                                r'capture exited before stop: .*exit 9'):
                        capture.stop(state, require_packets=True)
                    kill.assert_not_called()
            self.assertFalse(state.exists())
            self.assertTrue((Path(tmp) / 'dead.pcap.tcpdump.log').exists())
            capture.cleanup(tmp)

    def test_zombie_capture_is_reported_without_signal(self):
        import json
        import time
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'tcpdump'
            exe.write_text('#!/usr/bin/env python3\n'
                           'import pathlib,sys,time\n'
                           'pathlib.Path(sys.argv[sys.argv.index("-w")+1]).write_bytes('
                           'bytes.fromhex("d4c3b2a1020004000000000000000000ffff000001000000"))\n'
                           'time.sleep(.2)\n'
                           'sys.exit(11)\n')
            exe.chmod(0o755)
            state = Path(tmp) / 'zombie.capture.json'
            with mock.patch.dict(os.environ, {'PATH': tmp + ':' + os.environ['PATH']}):
                capture.start('veth1', Path(tmp) / 'zombie.pcap', state)
                pid = json.loads(state.read_text())['pid']
                for _ in range(150):
                    stat = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
                    if stat[0] == 'Z':
                        break
                    time.sleep(.02)
                else:
                    self.fail('capture child never became a zombie')
                with mock.patch.object(capture.os, 'killpg') as kill:
                    with self.assertRaisesRegex(RuntimeError, r'zombie \(exit 11\)'):
                        capture.stop(state, require_packets=True)
                    kill.assert_not_called()
            self.assertFalse(state.exists())

    def test_pid_reuse_guard_never_signals_unrelated_process(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            exe = Path(tmp) / 'tcpdump'
            exe.write_text('#!/usr/bin/env python3\n'
                           'import pathlib,signal,sys,time\n'
                           'pathlib.Path(sys.argv[sys.argv.index("-w")+1]).write_bytes('
                           'bytes.fromhex("d4c3b2a1020004000000000000000000ffff000001000000"))\n'
                           'signal.signal(signal.SIGINT, lambda *a: sys.exit(0))\n'
                           'while True: time.sleep(.01)\n')
            exe.chmod(0o755)
            state = Path(tmp) / 'guard.capture.json'
            with mock.patch.dict(os.environ, {'PATH': tmp + ':' + os.environ['PATH']}):
                capture.start('veth1', Path(tmp) / 'wire.pcap', state)
                valid = json.loads(state.read_text())
                forged = dict(valid, starttime=valid['starttime'] + 1)
                state.write_text(json.dumps(forged))
                with mock.patch.object(capture.os, 'killpg') as kill:
                    with self.assertRaisesRegex(RuntimeError, 'PID identity mismatch'):
                        capture.stop(state)
                    kill.assert_not_called()
                self.assertTrue(state.exists())
                state.write_text(json.dumps(valid))
                capture.cleanup(tmp)
                self.assertFalse(state.exists())

    def test_refuse_invalid_interface(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(ValueError):
                capture.start('x;touch /tmp/hijack', Path(tmp)/'a.pcap', Path(tmp)/'b.capture.json')


if __name__ == '__main__':
    unittest.main()

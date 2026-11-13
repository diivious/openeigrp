#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Real AF_UNIX IPC, authorization, public-state, and lifecycle tests."""

import concurrent.futures
import json
import os
from pathlib import Path
import shutil
import socket
import struct
import subprocess
import sys
import time
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
CODE = ROOT / "linux" / "code"
TEST = ROOT / "linux" / "test"
MAGIC, VERSION = 0x45474D54, 2
HEADER = "!IHHHHI"
SCOPE = "!HHIHHI"
SELECTOR = struct.pack(SCOPE, 4, 4453, 0, 0, 0, 0)


def frame(op, body=b"", magic=MAGIC, version=VERSION, status=0, flags=0,
          size=None):
    return struct.pack(HEADER, magic, version, op, status, flags,
                       len(body) if size is None else size) + body


def recv_exact(sock, size):
    buf = bytearray()
    while len(buf) < size:
        chunk = sock.recv(size - len(buf))
        if not chunk:
            raise EOFError("unexpected management disconnect")
        buf.extend(chunk)
    return bytes(buf)


def get_reply(sock):
    raw = recv_exact(sock, 16)
    magic, version, op, status, flags, length = struct.unpack(HEADER, raw)
    assert (magic, version) == (MAGIC, VERSION)
    assert length <= 16384
    content = recv_exact(sock, length)
    return op, status, flags, [json.loads(s) for s in content.splitlines()]


def identify(conn, release=b"development"):
    conn.sendall(frame(11, release.ljust(128, b"\x00")))
    return get_reply(conn)


def talk(path, op, body=None):
    if body is None:
        body = SELECTOR if op >= 3 else b""
    with socket.socket(socket.AF_UNIX) as conn:
        conn.settimeout(2)
        conn.connect(str(path))
        assert identify(conn)[1] == 0
        conn.sendall(frame(op, body))
        return get_reply(conn)


class LinuxManagementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.platform != "linux":
            raise unittest.SkipTest("Linux SO_PEERCRED and AF_UNIX required")
        cls.tmp = tempfile.TemporaryDirectory(prefix="eigrp-linux-mgnt-")
        os.chmod(cls.tmp.name, 0o755)
        cls.binary = Path(cls.tmp.name) / "mgnt-fixture"
        built = subprocess.run([
            os.environ.get("CC", "cc"), "-std=c11", "-D_GNU_SOURCE",
            "-D_FORTIFY_SOURCE=2", "-O2",
            "-Wall", "-Wextra", "-Werror", "-pthread", "-g",
            "-I", str(ROOT / "eigrp" / "code"), "-I", str(CODE),
            str(TEST / "mgnt_fixture.c"), str(CODE / "eigrp_linux_mgnt.c"),
            str(CODE / "eigrp_linux_version.c"),
            "-o", str(cls.binary),
        ], capture_output=True, text=True)
        if built.returncode:
            raise AssertionError(built.stdout + built.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def setUp(self):
        self.path = Path(self.tmp.name) / ("mgnt-%s.sock" % self._testMethodName)
        self.proc = subprocess.Popen([str(self.binary), str(self.path)],
                                     stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
        self.assertEqual(self.proc.stdout.readline(), "READY\n")
        self.assertTrue(self.path.is_socket())

    def tearDown(self):
        if self.proc.poll() is None:
            try:
                self.proc.stdin.write("q\n")
                self.proc.stdin.flush()
            except (BrokenPipeError, OSError):
                pass
        try:
            out, err = self.proc.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            out, err = self.proc.communicate(timeout=3)
            self.fail("daemon shutdown hung: " + err)
        self.assertEqual(self.proc.returncode, 0, out + err)
        self.assertIn("STOPPED", out)

    def test_all_readonly_state_and_json_safety(self):
        for op, kind in ((1, "capability"), (2, "protocol"),
                         (3, "neighbor"), (4, "interface"),
                         (5, "prefix"), (6, "prefix"),
                         (7, "traffic"), (8, "event_summary"),
                         (9, "capability")):
            with self.subTest(op=op):
                returned_op, status, flags, rows = talk(self.path, op)
                self.assertEqual((returned_op, status), (op, 0))
                self.assertTrue(rows)
                self.assertEqual(rows[0]["kind"], kind)
        self.assertEqual(talk(self.path, 3)[3][0]["address"], "192.0.2.2")
        self.assertEqual(talk(self.path, 4)[3][0]["name"], 'eth"0\nsecond')
        self.assertEqual(talk(self.path, 7)[3][0]["sent_hello"], 900)
        self.assertEqual(talk(self.path, 8)[3][1]["message"], 'event"\nnewline')
        self.assertEqual([r["kind"] for r in talk(self.path, 9)[3]],
                         ["capability", "debug", "timer"])

    def test_identity_required_and_wrong_build_blocked(self):
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(2)
            conn.connect(str(self.path))
            conn.sendall(frame(10, SELECTOR))
            self.assertEqual(get_reply(conn)[1], 2)
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(2)
            conn.connect(str(self.path))
            self.assertEqual(identify(conn, b"other-0.10.0-dev")[1], 2)
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(2)
            conn.connect(str(self.path))
            _, status, _, rows = identify(conn)
            self.assertEqual(status, 0)
            self.assertEqual(rows[0]["kind"], "identity")
            self.assertEqual(rows[0]["daemon"], "openeigrpd")
            self.assertEqual(rows[0]["release"], "development")
            self.assertEqual(rows[0]["os"], "Linux")
            conn.sendall(frame(1))
            self.assertEqual(get_reply(conn)[1], 0)

    def test_pagination_and_scope_validation(self):
        rows = []
        offset = 0
        while True:
            scope = struct.pack(SCOPE, 4, 4453, 0, 0, 0, offset)
            _, status, flags, page = talk(self.path, 5, scope)
            self.assertEqual(status, 0)
            self.assertLessEqual(len(page), 32)
            rows.extend(page)
            if not flags:
                break
            offset += len(page)
        self.assertEqual(len(rows), 96)
        self.assertEqual(rows[0]["prefix"], "198.51.100.1/32")
        self.assertEqual(rows[-1]["prefix"], "198.51.100.48/32")
        self.assertEqual(talk(self.path, 3, struct.pack(SCOPE, 6, 4453, 0, 0, 0, 0))[1], 4)
        self.assertEqual(talk(self.path, 3, struct.pack(SCOPE, 4, 0, 0, 0, 0, 0))[1], 1)
        self.assertEqual(talk(self.path, 5, struct.pack(SCOPE, 4, 4453, 0, 0, 0, 8193))[1], 1)
        self.assertEqual(talk(self.path, 4, struct.pack(SCOPE, 4, 4453, 0, 0, 1, 0))[1], 1)

    def test_framing_version_partial_reads_and_sequential_clients(self):
        with socket.socket(socket.AF_UNIX) as conn:
            conn.settimeout(3)
            conn.connect(str(self.path))
            self.assertEqual(identify(conn)[1], 0)
            query = frame(1)
            for part in (query[:1], query[1:7], query[7:15], query[15:]):
                conn.sendall(part)
            self.assertEqual(get_reply(conn)[1], 0)
            conn.sendall(frame(3, SELECTOR) + frame(7, SELECTOR))
            self.assertEqual(get_reply(conn)[3][0]["kind"], "neighbor")
            self.assertEqual(get_reply(conn)[3][0]["kind"], "traffic")
            conn.sendall(frame(2, version=1))
            self.assertEqual(get_reply(conn)[1], 2)
            self.assertEqual(conn.recv(1), b"")

    def test_malformed_frames_and_recovery(self):
        for corrupt in (
            frame(1, magic=0), frame(1, status=1), frame(1, flags=1),
            frame(1, size=2**32-1), frame(42), frame(3, b"abc"),
            frame(1, SELECTOR),
        ):
            with self.subTest(corrupt=corrupt[:16]):
                with socket.socket(socket.AF_UNIX) as conn:
                    conn.settimeout(2)
                    conn.connect(str(self.path))
                    conn.sendall(corrupt)
                    self.assertEqual(get_reply(conn)[1], 1)
                    try:
                        self.assertEqual(conn.recv(1), b"")
                    except ConnectionResetError:
                        # A rejected frame may leave unread bytes on a stream.
                        pass
            self.assertEqual(talk(self.path, 1)[1], 0)

    def test_authorization_and_live_clear_semantic(self):
        if os.geteuid() == 0:
            child = '''import os, socket, struct, sys
s=socket.socket(socket.AF_UNIX);s.settimeout(3);s.connect(sys.argv[1]);
p=lambda op,b: struct.pack("!IHHHHI",0x45474d54,2,op,0,0,len(b))+b
body=struct.pack("!HHIHHI",4,4453,0,0,0,0)
s.sendall(p(11,b"development".ljust(128,bytes(1))));h=s.recv(16);length=struct.unpack("!IHHHHI",h)[-1];s.recv(length)
s.sendall(p(8,body));h=s.recv(16);length=struct.unpack("!IHHHHI",h)[-1];s.recv(length)
s.sendall(p(10,body));h=s.recv(16);print(struct.unpack("!IHHHHI",h)[3])
'''
            def readonly():
                os.setgroups([65534])
                os.setgid(65534)
                os.setuid(65534)
            checked = subprocess.run([sys.executable, "-c", child,
                                      str(self.path)], preexec_fn=readonly,
                                     text=True, capture_output=True, timeout=4)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(checked.stdout.strip(), "3")
        self.assertEqual(talk(self.path, 8)[3][0]["count"], 1)
        self.assertEqual(talk(self.path, 10)[1], 0)
        self.assertEqual(talk(self.path, 8)[3][0]["count"], 0)
        self.assertEqual(talk(self.path, 10, struct.pack(SCOPE, 4, 4453, 0, 0, 0, 1))[1], 1)

    def test_concurrent_connections_disconnects_and_backpressure(self):
        def get(i):
            op = [1, 2, 3, 4, 5, 7, 8, 9][i % 8]
            return talk(self.path, op)[1]
        with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
            self.assertTrue(all(code == 0 for code in pool.map(get, range(100))))
        # A slow reader pipelines replies and never drains them. The server
        # must continue servicing other clients, not block in send(2).
        with socket.socket(socket.AF_UNIX) as slow:
            slow.settimeout(2)
            slow.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1024)
            slow.connect(str(self.path))
            slow.sendall(frame(5, SELECTOR) * 160)
            for _ in range(25):
                self.assertEqual(talk(self.path, 1)[1], 0)
            slow.close()
        self.assertEqual(talk(self.path, 1)[1], 0)
        with socket.socket(socket.AF_UNIX) as partial:
            partial.connect(str(self.path))
            partial.sendall(frame(3, SELECTOR)[:18])
        self.assertEqual(talk(self.path, 3)[1], 0)

    def test_shutdown_restart_and_path_ownership(self):
        old = self.path
        self.proc.stdin.write("q\n")
        self.proc.stdin.flush()
        out, err = self.proc.communicate(timeout=5)
        self.assertEqual(self.proc.returncode, 0, out + err)
        self.assertFalse(old.exists())
        # Start again on the same path, then replace the socket pathname.
        self.proc = subprocess.Popen([str(self.binary), str(old)],
                                     stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
        self.assertEqual(self.proc.stdout.readline(), "READY\n")
        self.assertEqual(talk(old, 1)[1], 0)
        old.unlink()
        old.write_text("replaced, do not unlink")
        self.proc.stdin.write("q\n")
        self.proc.stdin.flush()
        out, err = self.proc.communicate(timeout=5)
        self.assertEqual(self.proc.returncode, 0, out + err)
        self.assertEqual(old.read_text(), "replaced, do not unlink")
        old.unlink()
        # tearDown expects a running fixture.
        self.proc = subprocess.Popen([str(self.binary), str(old)],
                                     stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, text=True)
        self.assertEqual(self.proc.stdout.readline(), "READY\n")

    def test_shutdown_with_partial_client_and_socket_reuse(self):
        # The management poll loop must wake and retire outstanding clients
        # even when a client has only sent part of a request header.
        for _ in range(3):
            with socket.socket(socket.AF_UNIX) as pending:
                pending.connect(str(self.path))
                pending.sendall(frame(3, SELECTOR)[:5])
                self.proc.stdin.write("q\n")
                self.proc.stdin.flush()
                out, err = self.proc.communicate(timeout=5)
                self.assertEqual(self.proc.returncode, 0, out + err)
                self.assertIn("STOPPED", out)
                self.assertFalse(self.path.exists())
            self.proc = subprocess.Popen([str(self.binary), str(self.path)],
                                         stdin=subprocess.PIPE,
                                         stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, text=True)
            self.assertEqual(self.proc.stdout.readline(), "READY\n")
            self.assertEqual(talk(self.path, 1)[1], 0)

    def test_archive_boundary(self):
        built = subprocess.run(["make", "-C", str(CODE),
                                "libeigrp_linux_mgnt.a"],
                               text=True, capture_output=True)
        self.assertEqual(built.returncode, 0, built.stderr)
        symbols = subprocess.run(["nm", "--defined-only",
                                  str(CODE / "libeigrp_linux_mgnt.a")],
                                 text=True, capture_output=True)
        self.assertEqual(symbols.returncode, 0, symbols.stderr)
        self.assertIn("eigrp_linux_mgnt_start", symbols.stdout)
        self.assertIn("eigrp_linux_mgnt_stop", symbols.stdout)
        self.assertNotIn("eigrp_uut_", symbols.stdout)

    def test_format(self):
        formatter = shutil.which("clang-format")
        if not formatter:
            self.skipTest("clang-format unavailable; mandatory style gate unverified")
        formatted = subprocess.run([formatter, "--dry-run", "--Werror",
                                    str(CODE / "eigrp_linux_mgnt.c"),
                                    str(CODE / "eigrp_linux_version.c"),
                                    str(CODE / "eigrp_linux_mgnt.h"),
                                    str(TEST / "mgnt_fixture.c")],
                                   text=True, capture_output=True)
        self.assertEqual(formatted.returncode, 0, formatted.stderr)

    def test_existing_path_and_symlink_are_not_removed(self):
        for content in ("file", "link"):
            path = Path(self.tmp.name) / ("conflict-" + content)
            if content == "file":
                path.write_text("sensitive")
            else:
                path.symlink_to(self.path)
            result = subprocess.run([str(self.binary), str(path)],
                                    capture_output=True, text=True, timeout=3)
            self.assertNotEqual(result.returncode, 0)
            if content == "file":
                self.assertEqual(path.read_text(), "sensitive")
            else:
                self.assertTrue(path.is_symlink())
            path.unlink()
        self.assertEqual(talk(self.path, 1)[1], 0)

    def test_socket_mode_and_group_are_applied_to_created_inode(self):
        # The regular fixture requests 0660. Root requests a different GID
        # to exercise descriptor-bound fchownat(AT_EMPTY_PATH).
        state = self.path.stat()
        self.assertEqual(state.st_mode & 0o777, 0o660)
        self.assertEqual(state.st_gid, 65534 if os.geteuid() == 0
                         else os.getegid())

        # A private socket remains inaccessible to other users and groups.
        path = Path(self.tmp.name) / "private-control.sock"
        child = subprocess.Popen([str(self.binary), str(path), "600"],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, text=True)
        try:
            self.assertEqual(child.stdout.readline(), "READY\n")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(talk(path, 1)[1], 0)
        finally:
            if child.poll() is None:
                child.stdin.write("q\n")
                child.stdin.flush()
            output, errors = child.communicate(timeout=5)
            self.assertEqual(child.returncode, 0, output + errors)
            self.assertFalse(path.exists())

    def test_socket_group_failure_leaves_no_path(self):
        if os.geteuid() == 0:
            self.skipTest("root is permitted to change socket group")
        path = Path(self.tmp.name) / "unprivileged-group.sock"
        gid = 65534
        while gid in os.getgroups() or gid == os.getegid():
            gid -= 1
        child = subprocess.run([str(self.binary), str(path), "660", str(gid)],
                               text=True, capture_output=True, timeout=5)
        self.assertNotEqual(child.returncode, 0)
        self.assertFalse(path.exists())
        self.assertEqual(talk(self.path, 1)[1], 0)

    def test_client_limit_and_idle_timeout(self):
        clients = []
        try:
            for _ in range(32):
                client = socket.socket(socket.AF_UNIX)
                client.settimeout(2)
                client.connect(str(self.path))
                clients.append(client)
            time.sleep(0.15)
            with socket.socket(socket.AF_UNIX) as rejected:
                rejected.settimeout(2)
                rejected.connect(str(self.path))
                try:
                    rejected.sendall(frame(1))
                    self.assertEqual(rejected.recv(1), b"")
                except (ConnectionResetError, BrokenPipeError):
                    pass
        finally:
            for client in clients:
                client.close()
        # The manager needs one poll cycle to retire disconnected slots.
        time.sleep(0.25)
        self.assertEqual(talk(self.path, 1)[1], 0)
        with socket.socket(socket.AF_UNIX) as idle:
            idle.settimeout(7)
            idle.connect(str(self.path))
            idle.sendall(frame(3, SELECTOR)[:5])
            self.assertEqual(idle.recv(1), b"")
        self.assertEqual(talk(self.path, 1)[1], 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)

#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Executable Unix/UUT archive separation and runtime link regressions."""

import os
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[2]
UNIX = ROOT / "libunix" / "code"
CORE = ROOT / "eigrp" / "code"
TEST = ROOT / "libunix" / "test"
UNIX_LIB = UNIX / "libeigrp_unix.a"
UUT_LIB = UNIX / "libeigrp_uut.a"
CORE_LIB = CORE / "libeigrp-core.a"


def run(*command, timeout=60):
    return subprocess.run(command, cwd=ROOT, text=True,
                          capture_output=True, timeout=timeout)


def build_archives():
    for directory in ("eigrp/code", "libunix/code"):
        result = run("make", "-C", directory)
        assert result.returncode == 0, result.stdout + result.stderr


def members(path):
    result = run(os.environ.get("AR", "ar"), "t", str(path))
    assert result.returncode == 0, result.stderr
    # BSD ar exposes its symbol-table index as a member (e.g., on macOS).
    # Ignore only recognized metadata names; all objects must still be checked.
    bsd_indexes = {"__.SYMDEF", "__.SYMDEF SORTED",
                   "__.SYMDEF_64", "__.SYMDEF_64 SORTED"}
    return [name for name in result.stdout.splitlines()
            if name not in bsd_indexes]


def test_members_ignores_bsd_archive_symbol_table(monkeypatch):
    expected = ["eigrp_unix_sys.o", "eigrp_unix_format.o",
                "eigrp_unix_keychain.o"]
    listing = ""

    def archive_list(*_args, **_kwargs):
        return subprocess.CompletedProcess([], 0, stdout=listing, stderr="")

    monkeypatch.setattr(__import__(__name__), "run", archive_list)
    for index in ("", "__.SYMDEF", "__.SYMDEF SORTED",
                  "__.SYMDEF_64", "__.SYMDEF_64 SORTED"):
        listing = "\n".join(([index] if index else []) + expected) + "\n"
        assert members(UNIX_LIB) == expected
    # Do not let a new or unexpected archive entry escape membership checking.
    listing = "\n".join(expected + ["__.SYMDEF_unexpected.o"]) + "\n"
    assert members(UNIX_LIB) == expected + ["__.SYMDEF_unexpected.o"]


def exported(path):
    result = run(os.environ.get("NM", "nm"), "-g", str(path))
    assert result.returncode == 0, result.stderr
    symbols = set()
    for line in result.stdout.splitlines():
        # BSD nm may decorate external C symbols with a leading underscore.
        match = re.search(r"\b[TDBCSVW]\s+(_?eigrp_[A-Za-z0-9_]+)$", line)
        if match:
            symbols.add(match.group(1).lstrip("_"))
    return symbols


def undefined(path):
    result = run(os.environ.get("NM", "nm"), "-u", str(path))
    assert result.returncode == 0, result.stderr
    symbols = set()
    for line in result.stdout.splitlines():
        # GNU nm prints "U symbol"; Darwin nm -u may print just "_symbol".
        # -u already restricts output to undefined references on both.
        match = re.fullmatch(r"(?:U\s+)?(_?eigrp_[A-Za-z0-9_]+)",
                             line.strip())
        if match:
            symbols.add(match.group(1).lstrip("_"))
    return symbols


def test_undefined_parses_gnu_and_darwin_nm_output(monkeypatch):
    listing = ""

    def nm_undefined(*command, **_kwargs):
        assert command[1:] == ("-u", str(UNIX_LIB))
        return subprocess.CompletedProcess([], 0, stdout=listing, stderr="")

    monkeypatch.setattr(__import__(__name__), "run", nm_undefined)
    expected = {"eigrp_log_start", "eigrp_log_stop"}
    # GNU nm reports undefined symbols with the U type marker.
    listing = ("eigrp_unix_sys.o:\n"
               "                 U eigrp_log_start\n"
               "                 U eigrp_log_stop\n"
               "                 U pthread_create\n")
    assert undefined(UNIX_LIB) == expected
    # Darwin nm -u may omit the type marker, and Mach-O C symbols have _.
    listing = ("libeigrp_unix.a(eigrp_unix_sys.o):\n"
               "                 _eigrp_log_start\n"
               "                 _eigrp_log_stop\n"
               "                 _pthread_create\n")
    assert undefined(UNIX_LIB) == expected
    # The Darwin output can also include the U marker before the C underscore.
    listing = ("eigrp_unix_sys.o:\n"
               "                 U _eigrp_log_start\n"
               "                 U _eigrp_log_stop\n")
    assert undefined(UNIX_LIB) == expected
    # An additional UUT callback must still be visible to the boundary check.
    listing += "                 _eigrp_sys_socket_open\n"
    assert undefined(UNIX_LIB) == expected | {"eigrp_sys_socket_open"}
    # A non-U record is not an undefined reference.
    listing += "                 T _eigrp_unix_extra\n"
    assert "eigrp_unix_extra" not in undefined(UNIX_LIB)


def executable(tmp_path, harness, archives):
    binary = tmp_path / harness.stem
    command = [os.environ.get("CC", "cc"), "-std=c11",
               "-D_POSIX_C_SOURCE=200809L", "-Wall", "-Wextra", "-Werror",
               "-pthread", f"-I{CORE}", f"-I{UNIX}",
               str(harness), *(str(p) for p in archives), "-o", str(binary)]
    result = run(*command)
    assert result.returncode == 0, result.stdout + result.stderr
    return binary


def test_archive_membership_and_symbol_boundary():
    build_archives()
    assert members(UNIX_LIB) == [
        "eigrp_unix_sys.o", "eigrp_unix_format.o", "eigrp_unix_keychain.o"
    ]
    assert members(UUT_LIB) == [
        "eigrp_uut_interface.o", "eigrp_uut_rib.o", "eigrp_uut_segment.o",
        "eigrp_uut_wire.o", "eigrp_uut_policy.o"
    ]
    shared = exported(UNIX_LIB)
    virtual = exported(UUT_LIB)
    assert {"eigrp_sys_runtime_init", "eigrp_sys_timer_add",
            "eigrp_sys_auth_key_lookup", "eigrp_unix_keychain_reset",
            "eigrp_snprintf"} <= shared
    assert {"eigrp_sys_interface_walk", "eigrp_sys_packet_send",
            "eigrp_rib_route_add", "eigrp_sys_filter_evaluate",
            "eigrp_unix_policy_uut_deny_set"} <= virtual
    assert shared.isdisjoint(virtual), shared & virtual
    # Undefined references are equally important: a production consumer must
    # never need to satisfy a UUT host callback just to use common services.
    assert undefined(UNIX_LIB) == {"eigrp_log_start", "eigrp_log_stop"}
    assert not any(name.startswith(("eigrp_uut_", "eigrp_unix_interface_",
                                    "eigrp_unix_rib_", "eigrp_unix_segment_",
                                    "eigrp_unix_policy_", "eigrp_unix_config_",
                                    "eigrp_rib_", "eigrp_sys_socket_",
                                    "eigrp_sys_packet_", "eigrp_sys_ipv4_packet_",
                                    "eigrp_sys_ipv6_packet_", "eigrp_sys_filter_",
                                    "eigrp_sys_multicast_"))
                   for name in shared)
    assert not any(name.startswith("eigrp_unix_keychain_") for name in virtual)
    assert "eigrp_log" not in shared  # Portable core already owns logging.


def test_shared_runtime_archive_executes_without_uut(tmp_path):
    build_archives()
    binary = executable(tmp_path, TEST / "runtime_harness.c",
                        (UNIX_LIB, CORE_LIB))
    result = run(str(binary), timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr
    assert "unix runtime tests: PASS" in result.stdout


def test_uut_rib_archive_executes_with_mock_core_callbacks(tmp_path):
    build_archives()
    binary = executable(tmp_path, TEST / "rib_harness.c", (UUT_LIB, UNIX_LIB))
    result = run(str(binary))
    assert result.returncode == 0, result.stdout + result.stderr
    assert "unix rib tests: PASS" in result.stdout


def test_shared_archive_cannot_satisfy_uut_policy(tmp_path):
    build_archives()
    source = tmp_path / "virtual-only.c"
    source.write_text("extern void eigrp_unix_policy_uut_deny_set(void);\n"
                      "int main(void) { eigrp_unix_policy_uut_deny_set(); return 0; }\n")
    result = run(os.environ.get("CC", "cc"), str(source), str(UNIX_LIB),
                 "-pthread", "-o", str(tmp_path / "virtual-only"))
    assert result.returncode != 0, "UUT test policy resolved from the production library"
    assert "eigrp_unix_policy_uut_deny_set" in result.stderr
    assert "undefined" in result.stderr.lower()


def test_shared_archive_cannot_satisfy_virtual_transport(tmp_path):
    build_archives()
    source = tmp_path / "virtual-socket.c"
    source.write_text('#include "eigrp_sys.h"\n'
                      'int main(void) { return (int)eigrp_sys_socket_open(0); }\n')
    result = run(os.environ.get("CC", "cc"), f"-I{CORE}", str(source),
                 str(UNIX_LIB), "-pthread", "-o", str(tmp_path / "virtual-socket"))
    assert result.returncode != 0, "UUT virtual socket resolved from the production library"
    assert "eigrp_sys_socket_open" in result.stderr
    assert "undefined" in result.stderr.lower()


def test_uut_binary_links_to_both_archives():
    build_archives()
    result = run("make", "-C", "eigrp/test/uut", "eigrp-uut-node", timeout=180)
    assert result.returncode == 0, result.stdout + result.stderr
    symbols = exported(ROOT / "eigrp" / "test" / "uut" / "eigrp-uut-node")
    assert {"eigrp_unix_policy_uut_deny_set", "eigrp_unix_runtime_enter",
            "eigrp_sys_packet_send", "eigrp_rib_route_add"} <= symbols

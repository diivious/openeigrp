#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Compile the FRR tech-support CLI body and verify its rendered versions."""

from pathlib import Path
import os
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[3]
SCENARIO = ROOT / "frr/test/uut/features/tech-support-version.yaml"
CLI = ROOT / "frr/code/eigrp_frr_named.c"

DRIVER_START = r'''
#include <stdbool.h>
#include <stdarg.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "eigrp_build_version.h"
#include "eigrp_integration_version.h"

enum { EIGRP_RESULT_SUCCESS = 0, EIGRP_RESULT_NOT_FOUND = 1, CMD_SUCCESS = 0 };
typedef int eigrp_result_t;
typedef struct {
    const char *release;
    bool bfd, ipv4, ipv6, manet, mtr, evn, snmp;
} eigrp_status_capability_state_t;
typedef struct eigrp_status_protocol_state eigrp_status_protocol_state_t;
struct vty { char text[4096]; size_t used; };

static void vty_out(struct vty *vty, const char *format, ...)
{
    va_list args;
    int n;
    va_start(args, format);
    n = vsnprintf(vty->text + vty->used, sizeof(vty->text) - vty->used,
                  format, args);
    va_end(args);
    if (n < 0 || (size_t)n >= sizeof(vty->text) - vty->used)
        exit(2);
    vty->used += (size_t)n;
}

static eigrp_result_t eigrp_status_capability_state_read(
    eigrp_status_capability_state_t *state)
{
    memset(state, 0, sizeof(*state));
    state->release = EIGRP_RELEASE;
    state->ipv4 = state->ipv6 = true;
    return EIGRP_RESULT_SUCCESS;
}

static void eigrp_frr_version_read(eigrp_integration_version_t *version)
{
    memset(version, 0, sizeof(*version));
    version->integration = "FRRouting EIGRP";
    version->integration_version = "adapter-build-independent";
    version->framework = "FRRouting";
    version->framework_version = "99.7-test";
    strcpy(version->operating_system, "Linux");
}

static eigrp_result_t eigrp_vty_tech_support_context(
    const eigrp_status_protocol_state_t *state, void *arg)
{
    (void)state;
    (void)arg;
    return EIGRP_RESULT_SUCCESS;
}

static eigrp_result_t eigrp_status_tech_support_iterate(
    eigrp_result_t (*callback)(const eigrp_status_protocol_state_t *, void *),
    void *arg)
{
    (void)callback;
    (void)arg;
    return EIGRP_RESULT_NOT_FOUND;
}

static int eigrp_cli_result_render(struct vty *vty, const char *command,
                                   eigrp_result_t result)
{
    (void)vty;
    (void)command;
    return result;
}

static int show_eigrp_tech_support(struct vty *vty)
'''
DRIVER_END = r'''

int main(void)
{
    struct vty vty = {0};
    if (show_eigrp_tech_support(&vty) != CMD_SUCCESS)
        return 3;
    fputs(vty.text, stdout);
    return 0;
}
'''


def cli_body():
    """Compile the actual DEFPY implementation body, not a duplicate formatter."""
    source = CLI.read_text()
    begin = source.index("DEFPY(show_eigrp_tech_support,")
    opening = source.index("\n{", begin) + 1
    closing = source.index("\n}\n\n\nstatic bool eigrp_vty_ipv4_mask_prefix_length", opening) + 2
    return source[opening:closing]


class TechSupportVersionTests(unittest.TestCase):
    def test_rendered_version_fields(self):
        scenario = yaml.safe_load(SCENARIO.read_text())
        self.assertEqual(scenario["schema"], "frr-tech-support-version-uut/v1")
        with tempfile.TemporaryDirectory(prefix="eigrp-frr-tech-support-") as directory:
            temp = Path(directory)
            (temp / "driver.c").write_text(DRIVER_START + cli_body() + DRIVER_END)
            for case in scenario["cases"]:
                with self.subTest(case=case["name"]):
                    generated = subprocess.run(
                        ["python3", str(ROOT / "tools/gen-version.py"),
                         "--version", case["version"], "--output",
                         str(temp / "eigrp_build_version.h")],
                        capture_output=True, text=True)
                    self.assertEqual(generated.returncode, 0, generated.stderr)
                    binary = temp / "tech-support-test"
                    compiled = subprocess.run(
                        [os.environ.get("CC", "cc"), "-std=c11", "-Wall",
                         "-Wextra", "-Werror", "-pedantic",
                         "-I", str(temp), "-I", str(ROOT / "eigrp/code"),
                         str(temp / "driver.c"), "-o", str(binary)],
                        capture_output=True, text=True)
                    self.assertEqual(compiled.returncode, 0, compiled.stderr)
                    run = subprocess.run([str(binary)], capture_output=True, text=True)
                    self.assertEqual(run.returncode, 0, run.stderr)
                    lines = run.stdout.splitlines()
                    self.assertIn("  release              : " + case["version"], lines)
                    self.assertIn("  vendor-version       : " + case["vendor"], lines)
                    self.assertIn("  integration          : FRRouting EIGRP", lines)
                    self.assertIn("  framework            : FRRouting 99.7-test", lines)
                    self.assertIn("  operating-system     : Linux", lines)
                    self.assertNotIn("integration-version", run.stdout)
                    self.assertNotIn("adapter-build-independent", run.stdout)
                    self.assertIn("No named EIGRP address families are configured", run.stdout)


if __name__ == "__main__":
    unittest.main()

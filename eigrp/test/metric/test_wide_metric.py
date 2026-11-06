# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Execute the portable C metric implementation against Cisco reference vectors."""

from pathlib import Path
import os
import subprocess
import sys

ROOT = next(p for p in Path(__file__).resolve().parents
            if (p / 'eigrp' / 'code').is_dir())


def test_cisco_wide_metric_arithmetic(tmp_path):
    binary = tmp_path / 'wide_metric'
    cmd = [os.environ.get('CC', 'cc'), '-std=c11', '-D_POSIX_C_SOURCE=200809L',
           '-ffunction-sections', '-fdata-sections', '-I' + str(ROOT / 'eigrp/code'),
           str(Path(__file__).with_name('wide_metric.c')),
           str(ROOT / 'eigrp/code/eigrp_metric.c'),
           '-Wl,-dead_strip' if sys.platform == 'darwin' else '-Wl,--gc-sections',
           '-o', str(binary)]
    built = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    assert built.returncode == 0, built.stderr
    executed = subprocess.run([str(binary)], cwd=ROOT, text=True,
                              capture_output=True, timeout=15)
    assert executed.returncode == 0, executed.stderr


def test_rfc_constants_and_interface_vectors_execute(tmp_path):
    """Compile the actual metric converter with the RFC and UUT constants."""
    source = tmp_path / 'rfc_metric_defs.c'
    binary = tmp_path / 'rfc_metric_defs'
    source.write_text(r'''
        #include <assert.h>
        #include <stdbool.h>
        #include <stdint.h>
        #include "eigrp/code/eigrp_const.h"
        #include "eigrp/code/eigrp_metric.h"
        #include "eigrp/test/uut/eigrp_uut_defs.h"

        /* RFC 7868, sections 5.6.2.2 and 6.9.2. */
        _Static_assert(EIGRP_BANDWIDTH == 10000000ULL, "RFC bandwidth");
        _Static_assert(EIGRP_DELAY_PICO == 1000000ULL, "RFC delay");
        _Static_assert(EIGRP_CLASSIC_SCALE == 256, "RFC classic scale");
        _Static_assert(EIGRP_WIDE_SCALE == 65536, "RFC wide scale");
        _Static_assert(EIGRP_INACCESSIBLE == UINT64_MAX, "RFC infinity");
        _Static_assert(EIGRP_OPAQUE_SRCWD == 0x01, "source withdraw");
        _Static_assert(EIGRP_OPAQUE_CD == 0x02, "candidate default");
        _Static_assert(EIGRP_OPAQUE_ACTIVE == 0x04, "active route");
        _Static_assert(EIGRP_OPAQUE_REPL == 0x08, "replicated route");
        _Static_assert(EIGRP_INTF_MTU_1500 == 1500, "test MTU");
        _Static_assert(EIGRP_INTF_LOAD_50 == 127, "RFC 50 percent load");
        _Static_assert(EIGRP_INTF_LOAD_100 == 255, "full load");
        _Static_assert(EIGRP_INTF_REL_20 == 51, "20 percent reliability");
        _Static_assert(EIGRP_INTF_REL_100 == 255, "full reliability");
        bool eigrp_intf_delay_is_default(eigrp_intf_t *ei)
        {
            (void)ei;
            return false;
        }
        int main(void)
        {
            assert(eigrp_metric_delay_classic2wide(
                       EIGRP_INTF_1G_DELAY, EIGRP_INTF_10G_BW, true)
                   == EIGRP_INTF_10G_WIDE_NS * 1000);
            assert(eigrp_metric_delay_classic2wide(
                       EIGRP_INTF_1G_DELAY, EIGRP_INTF_10G_BW, false)
                   == EIGRP_CLASSIC_DELAY_PICO);
            assert(eigrp_metric_delay_wide2classic(
                       EIGRP_INTF_10G_WIDE_NS * 1000,
                       EIGRP_INTF_10G_BW) == EIGRP_INTF_1G_DELAY);
            return 0;
        }
    ''')
    cmd = [os.environ.get('CC', 'cc'), '-std=c11', '-Werror',
           '-ffunction-sections', '-fdata-sections', '-I' + str(ROOT),
           '-I' + str(ROOT / 'eigrp/code'), str(source),
           str(ROOT / 'eigrp/code/eigrp_metric.c'),
           '-Wl,-dead_strip' if sys.platform == 'darwin' else '-Wl,--gc-sections',
           '-o', str(binary)]
    built = subprocess.run(cmd, cwd=ROOT, text=True, capture_output=True)
    assert built.returncode == 0, built.stderr
    executed = subprocess.run([str(binary)], cwd=ROOT, text=True,
                              capture_output=True, timeout=15)
    assert executed.returncode == 0, executed.stderr

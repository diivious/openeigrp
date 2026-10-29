# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Execute the portable EIGRP packet budget with IPv4/IPv6 IP envelopes."""
from pathlib import Path
import subprocess
import textwrap

ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'eigrp' / 'code').is_dir())


def test_af_packet_budget_compiled(tmp_path):
    source = tmp_path / 'packet_budget.c'
    binary = tmp_path / 'packet_budget'
    source.write_text(textwrap.dedent(r'''
        #include <assert.h>
        #include <stddef.h>
        #include "eigrp/code/eigrp.h"
        #include "eigrp/code/eigrp_types.h"
        #include "eigrp/code/eigrp_structs.h"
        #include "eigrp/code/eigrp_packet.h"
        #include "eigrp/code/eigrp_const.h"

        int main(void)
        {
            size_t mtu = 1500;
            size_t v4 = eigrp_packet_payload_limit(mtu, EIGRP_IPV4_HEADER_LEN);
            size_t v6 = eigrp_packet_payload_limit(mtu, EIGRP_IPV6_HEADER_LEN);
            assert(v4 == 1480);
            assert(v6 == 1460);
            assert(v4 + EIGRP_IPV4_HEADER_LEN <= mtu);
            assert(v6 + EIGRP_IPV6_HEADER_LEN <= mtu);
            assert(eigrp_packet_payload_limit(1280, EIGRP_IPV6_HEADER_LEN) == 1240);
            assert(eigrp_packet_payload_limit(40, EIGRP_IPV6_HEADER_LEN) == 0);
            assert(eigrp_packet_payload_limit(39, EIGRP_IPV6_HEADER_LEN) == 0);
            assert(eigrp_packet_payload_limit(1500, 0) == 0);
            return 0;
        }
    '''))
    subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror',
                    '-I', str(ROOT), str(source), '-o', str(binary)],
                   check=True)
    subprocess.run([str(binary)], check=True)


def test_af_budget_bound_for_all_emission_paths():
    # Wiring coverage supplements the executed C boundary test: all emission
    # sites must use the selected AF envelope, not a hard-coded IPv4 header.
    code = ROOT / 'eigrp' / 'code'
    for af, header in [('ipv4', 'EIGRP_IPV4_HEADER_LEN'),
                       ('ipv6', 'EIGRP_IPV6_HEADER_LEN')]:
        assert f'vectors->packet_ip_header_bytes = {header};' in (
            code / f'eigrp_{af}.c').read_text()
    for name in ('eigrp_packetizer.c', 'eigrp_update.c', 'eigrp_hello.c'):
        content = (code / name).read_text()
        assert 'af_vectors.packet_ip_header_bytes' in content
        assert 'eigrp_packet_payload_limit(ei->curr_mtu)' not in content

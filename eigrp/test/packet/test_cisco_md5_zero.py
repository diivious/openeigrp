#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Run the portable MD5 validator on a captured Cisco CSR1000v Hello.

Fixture: IOS XE 17.3.6, 2R-md5-interop, 2026-11-22, key ID 7,
lab-only secret oe03-lab-md5, MD5 key sequence zero.
"""

from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = next(p for p in Path(__file__).resolve().parents if (p / 'eigrp' / 'code').is_dir())
CODE = ROOT / 'eigrp' / 'code'
FIXTURE = Path(__file__).with_name('fixtures') / 'cisco-md5-zero-hello.bin'


def test_cisco_zero_sequence_hello_digest_and_discovery():
    source = r'''
#include <arpa/inet.h>
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "eigrp_auth.c"

bool eigrp_sys_auth_key_lookup(const char *name, uint32_t *id,
                               char *key, size_t capacity)
{
    static const char secret[] = "oe03-lab-md5";
    if (strcmp(name, "OE03C2OE") != 0 || sizeof(secret) > capacity)
        return false;
    if (id) *id = 7;
    memcpy(key, secret, sizeof(secret));
    return true;
}
bool eigrp_sys_auth_key_accept(const char *name, uint32_t id,
                               char *key, size_t capacity)
{
    return id == 7 && eigrp_sys_auth_key_lookup(name, NULL, key, capacity);
}
void eigrp_log(eigrp_log_level_t level, const char *format, ...)
{
    (void)level; (void)format;
}
const char *eigrp_intf_name_string(eigrp_intf_t *ei)
{
    return ei->name;
}
int main(int argc, char **argv)
{
    uint8_t wire[128];
    FILE *f;
    eigrp_intf_t ei = {0};
    eigrp_nbr_t nbr = {0};
    eigrp_stream_t *s;
    struct eigrp_header *header;
    struct TLV_MD5_Authentication_Type *auth;
    uint8_t saved;

    assert(argc == 2);
    f = fopen(argv[1], "rb");
    assert(f);
    assert(fread(wire, 1, sizeof(wire), f) == 80);
    assert(feof(f));
    fclose(f);
    ei.name = "csr-md5";
    ei.params.auth_type = EIGRP_AUTH_TYPE_MD5;
    ei.params.auth_keychain = "OE03C2OE";
    nbr.ei = &ei;
    s = eigrp_stream_create(sizeof(wire));
    assert(s && eigrp_stream_put(s, wire, 80) == 80);
    header = (void *)s->data;
    auth = (void *)(s->data + EIGRP_HEADER_LEN);
    assert(header->opcode == EIGRP_OPC_HELLO);
    assert(auth->key_sequence == 0 && ntohl(auth->key_id) == 7);

    /* Cisco digest is valid, and the zero-sequence discovery path must
     * accept subsequent Cisco Hellos without consuming a nonzero watermark. */
    assert(eigrp_check_md5_digest(s, auth, &nbr, EIGRP_AUTH_BASIC_HELLO_FLAG));
    assert(nbr.crypt_seqnum == 0);
    assert(eigrp_auth_discovery_sequence_accept(&nbr, header));
    assert(eigrp_check_md5_digest(s, auth, &nbr, EIGRP_AUTH_BASIC_HELLO_FLAG));

    /* A nonzero sequence peer cannot downgrade to the replayable profile. */
    nbr.crypt_seqnum = htonl(10);
    assert(!eigrp_check_md5_digest(s, auth, &nbr, EIGRP_AUTH_BASIC_HELLO_FLAG));
    assert(!eigrp_auth_discovery_sequence_accept(&nbr, header));
    assert(ntohl(nbr.crypt_seqnum) == 10);

    /* A corrupt digest must be rejected even for the compatibility profile. */
    nbr.crypt_seqnum = 0;
    saved = auth->digest[0];
    auth->digest[0] ^= 1;
    assert(!eigrp_check_md5_digest(s, auth, &nbr, EIGRP_AUTH_BASIC_HELLO_FLAG));
    assert(nbr.crypt_seqnum == 0);
    auth->digest[0] = saved;
    assert(eigrp_check_md5_digest(s, auth, &nbr, EIGRP_AUTH_BASIC_HELLO_FLAG));

    /* Also verify a zero-sequence MD5 INIT can reach digest validation.
     * This locally signed UPDATE does not assert that Cisco's captured INIT
     * follows OpenEIGRP's currently implemented MD5 UPDATE digest profile. */
    header->opcode = EIGRP_OPC_UPDATE;
    header->flags = htonl(EIGRP_INIT_FLAG);
    header->sequence = htonl(1);
    header->checksum = 0;
    memset(auth->digest, 0, sizeof(auth->digest));
    assert(eigrp_make_md5_digest(&ei, s, EIGRP_AUTH_UPDATE_INIT_FLAG) == 16);
    header->checksum = htons(eigrp_checksum(header, s->endp));
    assert(eigrp_check_md5_digest(s, auth, &nbr, EIGRP_AUTH_UPDATE_INIT_FLAG));
    assert(nbr.crypt_seqnum == 0);

    /* SHA-256 must continue rejecting zero on discovery. */
    ei.params.auth_type = EIGRP_AUTH_TYPE_SHA256;
    assert(!eigrp_auth_discovery_sequence_accept(&nbr, header));
    eigrp_stream_free(s);
    return 0;
}
'''
    linker_gc = '-Wl,-dead_strip' if sys.platform == 'darwin' else '-Wl,--gc-sections'
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / 'cisco_md5_zero.c'
        binary = Path(tmp) / 'cisco_md5_zero'
        src.write_text(source)
        subprocess.run([
            'cc', '-std=c11', '-D_POSIX_C_SOURCE=200809L',
            '-DEIGRP_DISABLE_SNMP', '-Wall', '-Wextra', '-Werror',
            '-Wno-unused-parameter', '-ffunction-sections', '-fdata-sections',
            f'-I{CODE}', str(src), str(CODE / 'eigrp_md5.c'),
            str(CODE / 'eigrp_sha256.c'), str(CODE / 'eigrp_memory.c'),
            str(CODE / 'eigrp_stream.c'), str(CODE / 'eigrp_checksum.c'),
            linker_gc, '-pthread', '-o', str(binary),
        ], check=True, cwd=ROOT)
        subprocess.run([str(binary), str(FIXTURE)], check=True, cwd=ROOT)

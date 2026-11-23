#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
#
# Validate MD5 transmit and receive against Cisco IOS XE 17.3.6 packets
# captured in live Netlabs scenarios on November 22, 2026.

from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = next(p for p in Path(__file__).resolve().parents if (p / "eigrp" / "code").is_dir())
CODE = ROOT / "eigrp" / "code"
FIXTURES = ROOT / "eigrp" / "test" / "packet" / "fixtures"


def test_cisco_md5_wire_authentication():
    source = r'''
#include <arpa/inet.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "eigrp_auth.c"

static const char *key_value = "oe03-lab-md5";

bool eigrp_sys_auth_key_lookup(const char *chain, uint32_t *id,
                               char *key, size_t capacity)
{
    if (strcmp(chain, "test-chain") || strlen(key_value) >= capacity)
        return false;
    *id = 7;
    memcpy(key, key_value, strlen(key_value) + 1);
    return true;
}

bool eigrp_sys_auth_key_accept(const char *chain, uint32_t id,
                               char *key, size_t capacity)
{
    uint32_t selected;
    return id == 7
           && eigrp_sys_auth_key_lookup(chain, &selected, key, capacity);
}

void eigrp_log(eigrp_log_level_t level, const char *fmt, ...)
{
    (void)level;
    (void)fmt;
}

const char *eigrp_intf_name_string(eigrp_intf_t *ei)
{
    return ei->name;
}

static int check(const char *filename, unsigned int opcode)
{
    uint8_t packet[256];
    uint8_t original[16];
    eigrp_stream_t *stream;
    eigrp_intf_t intf = {0};
    eigrp_nbr_t nbr = {0};
    struct eigrp_header *header;
    struct TLV_MD5_Authentication_Type *auth;
    uint16_t checksum;
    uint8_t flags;
    size_t len;
    FILE *file = fopen(filename, "rb");
    int status = 0;

    if (!file)
        return 1;
    len = fread(packet, 1, sizeof(packet), file);
    int complete = !ferror(file) && feof(file);
    if (fclose(file) != 0 || !complete || len < 60)
        return 2;
    stream = eigrp_stream_create(len);
    if (!stream)
        return 3;
    if (eigrp_stream_put(stream, packet, len) != len)
        return 4;
    header = (struct eigrp_header *)stream->data;
    auth = (struct TLV_MD5_Authentication_Type *)(stream->data + 20);
    memcpy(original, auth->digest, sizeof(original));
    checksum = header->checksum;
    flags = opcode == 5 ? EIGRP_AUTH_BASIC_HELLO_FLAG
            : ntohl(header->flags) & EIGRP_INIT_FLAG
                ? EIGRP_AUTH_UPDATE_INIT_FLAG : EIGRP_AUTH_UPDATE_FLAG;
    if (header->opcode != opcode)
        status = 5;
    intf.name = "cisco-md5";
    intf.params.auth_type = EIGRP_AUTH_TYPE_MD5;
    intf.params.auth_keychain = "test-chain";
    nbr.ei = &intf;
    if (!status && !eigrp_check_md5_digest(stream, auth, &nbr, flags))
        status = 6;
    if (!status && (header->checksum != checksum
                    || memcmp(auth->digest, original, sizeof(original)) != 0))
        status = 7;
    /* The authenticated header must still reject corruption. */
    nbr.crypt_seqnum = 0;
    stream->data[19] ^= 1;
    if (!status && eigrp_check_md5_digest(stream, auth, &nbr, flags))
        status = 8;
    stream->data[19] ^= 1;
    /* A modified digest must not verify even for a keyless Cisco INIT. */
    auth->digest[0] ^= 1;
    if (!status && eigrp_check_md5_digest(stream, auth, &nbr, flags))
        status = 9;
    auth->digest[0] ^= 1;

    /* Signing the same packet with the configured key reproduces Cisco. */
    header->checksum = 0;
    memset(auth->digest, 0, sizeof(auth->digest));
    if (!status && eigrp_make_md5_digest(&intf, stream, flags) != 16)
        status = 10;
    if (!status && memcmp(auth->digest, original, sizeof(original)) != 0)
        status = 11;
    eigrp_stream_free(stream);
    return status;
}

int main(int argc, char **argv)
{
    int i, failed;
    const unsigned int opcode[] = {5, 5, 1, 1};
    if (argc != 5)
        return 1;
    for (i = 0; i < 4; i++) {
        failed = check(argv[i + 1], opcode[i]);
        if (failed) {
            fprintf(stderr, "Cisco fixture %d: error %d\n", i, failed);
            return failed;
        }
    }
    return 0;
}
'''
    names = (
        "cisco-md5-zero-hello.bin",
        "cisco-md5-extended-hello.bin",
        "cisco-md5-init.bin",
        "cisco-md5-route-update.bin",
    )
    linker_gc = "-Wl,-dead_strip" if sys.platform == "darwin" else "-Wl,--gc-sections"
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "cisco_md5_wire.c"
        exe = Path(tmp) / "cisco_md5_wire"
        src.write_text(source)
        subprocess.run([
            "cc", "-std=c11", "-D_POSIX_C_SOURCE=200809L",
            "-DEIGRP_DISABLE_SNMP", "-Wall", "-Wextra", "-Werror",
            "-Wno-unused-parameter", "-ffunction-sections", "-fdata-sections",
            f"-I{CODE}", str(src), str(CODE / "eigrp_md5.c"),
            str(CODE / "eigrp_stream.c"), linker_gc, "-pthread", "-o", str(exe),
        ], check=True, cwd=ROOT)
        subprocess.run([str(exe), *(str(FIXTURES / n) for n in names)],
                       check=True, cwd=ROOT)

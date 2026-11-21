// SPDX-License-Identifier: ISC
/* Copyright (C) 2026 Donnie V. Savage */
/* Real MD5/SHA packet signing and verification with a rotating host keychain.
 */
#include <arpa/inet.h>
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "eigrp_auth.c"

struct test_key {
	uint32_t id;
	const char *secret;
	bool send;
	bool accept;
};

static struct test_key keys[] = {
	{1, "same-secret", true, true},
	{2, "same-secret", false, true},
	{3, "different-secret", false, false},
};
static unsigned int accepts;

bool eigrp_sys_auth_key_lookup(const char *name, uint32_t *id, char *key,
			       size_t capacity)
{
	size_t i, length;

	if (strcmp(name, "rollover"))
		return false;
	for (i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i) {
		if (!keys[i].send)
			continue;
		length = strlen(keys[i].secret);
		if (length >= capacity)
			return false;
		memcpy(key, keys[i].secret, length + 1);
		*id = keys[i].id;
		return true;
	}
	return false;
}

bool eigrp_sys_auth_key_accept(const char *name, uint32_t id, char *key,
			       size_t capacity)
{
	size_t i, length;

	++accepts;
	if (strcmp(name, "rollover"))
		return false;
	for (i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i) {
		if (keys[i].id != id || !keys[i].accept)
			continue;
		length = strlen(keys[i].secret);
		if (length >= capacity)
			return false;
		memcpy(key, keys[i].secret, length + 1);
		return true;
	}
	return false;
}

void eigrp_log(eigrp_log_level_t level, const char *format, ...)
{
	(void)level;
	(void)format;
}

const char *eigrp_intf_name_string(eigrp_intf_t *ei)
{
	return ei->name;
}

static eigrp_stream_t *packet_create(eigrp_intf_t *ei)
{
	struct eigrp_header header = {0};
	static const uint8_t payload[] = {0, 1, 0, 8, 1, 2, 3, 4};
	eigrp_stream_t *s = eigrp_stream_create(256);

	assert(s);
	header.opcode = EIGRP_OPC_UPDATE;
	assert(eigrp_stream_put(s, &header, EIGRP_HEADER_LEN)
	       == EIGRP_HEADER_LEN);
	assert((ei->params.auth_type == EIGRP_AUTH_TYPE_MD5
			? eigrp_auth_tlv_md5_encode(s, ei)
			: eigrp_auth_tlv_sha256_encode(s, ei))
	       != 0);
	assert(eigrp_stream_put(s, payload, sizeof(payload))
	       == sizeof(payload));
	return s;
}

static struct TLV_MD5_Authentication_Type *md5_tlv(eigrp_stream_t *s)
{
	return (void *)(s->data + EIGRP_HEADER_LEN);
}

static void packet_sign(eigrp_intf_t *ei, eigrp_stream_t *s, uint32_t id)
{
	assert(eigrp_auth_transmit_finalize(ei, s, s->endp));
	assert(ntohl(md5_tlv(s)->key_id) == id);
	assert(eigrp_checksum(s->data, s->endp) == 0);
}

static void accept_md5(eigrp_nbr_t *nbr, eigrp_stream_t *s, bool allowed)
{
	uint32_t previous = nbr->crypt_seqnum;
	uint32_t seq = md5_tlv(s)->key_sequence;
	unsigned int before = accepts;
	int result = eigrp_check_md5_digest(s, md5_tlv(s), nbr,
					    EIGRP_AUTH_UPDATE_FLAG);

	assert(result == allowed);
	assert(accepts == before + 1);
	assert(nbr->crypt_seqnum == (allowed ? seq : previous));
}

int main(void)
{
	eigrp_intf_t ei = {0};
	eigrp_nbr_t nbr = {0};
	eigrp_stream_t *s;
	struct TLV_SHA256_Authentication_Type *sha;
	unsigned int before;

	ei.name = "rollover-test";
	ei.params.auth_type = EIGRP_AUTH_TYPE_MD5;
	ei.params.auth_keychain = "rollover";
	nbr.ei = &ei;

	/* The key used for a queued packet may change before paced transmit. */
	s = packet_create(&ei);
	assert(ntohl(md5_tlv(s)->key_id) == 1);
	keys[0].send = false;
	keys[1].send = true;
	packet_sign(&ei, s, 2);
	accept_md5(&nbr, s, true);
	assert(!eigrp_check_md5_digest(s, md5_tlv(s), &nbr,
				       EIGRP_AUTH_UPDATE_FLAG));
	eigrp_stream_free(s);

	/* Accept key 1 without allowing it to transmit. */
	keys[0].send = true;
	keys[1].send = false;
	s = packet_create(&ei);
	packet_sign(&ei, s, 1);
	accept_md5(&nbr, s, true);
	eigrp_stream_free(s);

	/* A revoked accept lifetime blocks the packet, even if its key is
	 * still permitted for sending and its digest is authentic. */
	s = packet_create(&ei);
	packet_sign(&ei, s, 1);
	keys[0].accept = false;
	accept_md5(&nbr, s, false);
	keys[0].accept = true;
	accept_md5(&nbr, s, true); /* Reject did not consume watermark. */
	eigrp_stream_free(s);

	/* A valid digest is insufficient when the packet ID is unknown or
	 * not currently accepted. */
	keys[0].send = false;
	keys[2].send = true;
	s = packet_create(&ei);
	packet_sign(&ei, s, 3);
	accept_md5(&nbr, s, false);
	keys[2].accept = true;
	accept_md5(&nbr, s, true);
	eigrp_stream_free(s);

	/* No currently valid send key must fail closed. */
	keys[2].send = false;
	s = eigrp_stream_create(128);
	assert(s);
	assert(eigrp_auth_tlv_md5_encode(s, &ei) == 0);
	eigrp_stream_free(s);

	/* Direct-password SHA256 still uses ID zero, independent of the
	 * configured (but ignored) keychain, and accepts that profile only. */
	ei.params.auth_type = EIGRP_AUTH_TYPE_SHA256;
	ei.params.auth_password = "direct-sha-secret";
	ei.address.address.afi = EIGRP_AFI_IPV4;
	assert(inet_pton(AF_INET, "192.0.2.10", ei.address.address.bytes) == 1);
	nbr.src.afi = AF_INET;
	assert(inet_pton(AF_INET, "192.0.2.10", &nbr.src.ip.v4) == 1);
	s = packet_create(&ei);
	sha = (void *)md5_tlv(s);
	packet_sign(&ei, s, 0);
	assert(eigrp_check_sha256_digest(s, sha, &nbr, EIGRP_AUTH_UPDATE_FLAG));
	eigrp_stream_free(s);

	/* ID is signed for SHA as well: verify receive stays fail-closed. */
	s = packet_create(&ei);
	packet_sign(&ei, s, 0);
	sha = (void *)md5_tlv(s);
	before = nbr.crypt_seqnum;
	sha->key_id = htonl(7);
	assert(!eigrp_check_sha256_digest(s, sha, &nbr,
					  EIGRP_AUTH_UPDATE_FLAG));
	assert(nbr.crypt_seqnum == before);
	eigrp_stream_free(s);

	puts("authentication key ID rollover: PASS");
	return 0;
}

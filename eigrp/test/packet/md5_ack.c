/* SPDX-License-Identifier: ISC */
/* Cisco MD5 header-only RTP ACK receive regression. */
#include <arpa/inet.h>
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "eigrp_packet.c"

static eigrp_intf_t *test_ei;
static eigrp_nbr_t *test_nbr;
static unsigned int hold_updates;

void eigrp_log(eigrp_log_level_t level, const char *format, ...)
{
	(void)level;
	(void)format;
}
const char *eigrp_intf_name_string(eigrp_intf_t *ei)
{
	return ei->name;
}
eigrp_intf_t *eigrp_intf_lookup_by_ifindex(eigrp_instance_t *instance,
					   eigrp_ifindex_t index)
{
	(void)instance;
	return index == 1 ? test_ei : NULL;
}
eigrp_nbr_t *eigrp_nbr_lookup(eigrp_intf_t *ei, struct eigrp_header *h,
			      eigrp_addr_t *source)
{
	(void)ei;
	(void)h;
	return source->ip.v4.s_addr == inet_addr("10.44.53.2") ? test_nbr
							       : NULL;
}
bool eigrp_nbr_is_eligible(const eigrp_nbr_t *nbr)
{
	return nbr->state == EIGRP_NEIGHBOR_UP;
}
void eigrp_nbr_holddown_update(eigrp_nbr_t *nbr)
{
	(void)nbr;
	hold_updates++;
}
bool eigrp_auth_material_available(const eigrp_intf_t *ei)
{
	return ei->params.auth_keychain != NULL;
}
uint16_t eigrp_checksum(const void *data, size_t len)
{
	const uint8_t *p = data;
	uint32_t sum = 0;
	for (size_t i = 0; i < len; i += 2) {
		sum += ((uint16_t)p[i] << 8) | (i + 1 < len ? p[i + 1] : 0);
		while (sum >> 16)
			sum = (sum & 0xffff) + (sum >> 16);
	}
	return (uint16_t)~sum;
}
static int addr_string(char *dst, size_t len, const eigrp_addr_t *a)
{
	(void)a;
	if (len)
		dst[0] = 0;
	return 0;
}
static bool link_ok(eigrp_intf_t *ei, const eigrp_addr_t *src)
{
	(void)ei;
	return src->ip.v4.s_addr == inet_addr("10.44.53.2")
	       || src->ip.v4.s_addr == inet_addr("10.44.53.3");
}

bool eigrp_intf_is_passive(eigrp_intf_t *ei)
{
	(void)ei;
	return false;
}

bool eigrp_nbr_admit_locked(const eigrp_intf_t *ei)
{
	(void)ei;
	return true;
}
bool eigrp_hello_tlvs_validate(eigrp_intf_t *ei, const uint8_t *payload,
			       uint16_t length)
{
	(void)ei;
	(void)payload;
	(void)length;
	return false;
}
bool eigrp_nbr_static_source_allowed(eigrp_intf_t *ei, const eigrp_addr_t *src)
{
	(void)ei;
	(void)src;
	return false;
}
void eigrp_eventlog_addr_from_legacy(eigrp_prefix_t *p, const eigrp_addr_t *a)
{
	(void)p;
	(void)a;
}
eigrp_result_t eigrp_eventlog_msg_add(eigrp_instance_t *e, uint16_t op,
				      const eigrp_prefix_t *p, eventmsg_arg_t a,
				      eventmsg_arg_t b, eventmsg_arg_t c,
				      eventmsg_arg_t d)
{
	(void)e;
	(void)op;
	(void)p;
	(void)a;
	(void)b;
	(void)c;
	(void)d;
	return 0;
}
int eigrp_snprintf(char *dst, size_t len, const char *fmt, ...)
{
	(void)fmt;
	if (len)
		dst[0] = 0;
	return 0;
}
eigrp_stream_t *eigrp_stream_create(size_t len)
{
	(void)len;
	return NULL;
}
void eigrp_stream_free(eigrp_stream_t *s)
{
	(void)s;
}
size_t eigrp_stream_put(eigrp_stream_t *s, const void *src, size_t n)
{
	(void)s;
	(void)src;
	(void)n;
	return 0;
}
int eigrp_check_md5_digest(eigrp_stream_t *s,
			   struct TLV_MD5_Authentication_Type *auth,
			   eigrp_nbr_t *nbr, uint8_t flags)
{
	(void)s;
	(void)auth;
	(void)nbr;
	(void)flags;
	return 0;
}
int eigrp_check_sha256_digest(eigrp_stream_t *s,
			      struct TLV_SHA256_Authentication_Type *auth,
			      eigrp_nbr_t *nbr, uint8_t flags)
{
	(void)s;
	(void)auth;
	(void)nbr;
	(void)flags;
	return 0;
}

static void packet_prepare(uint8_t *buf, size_t len, uint32_t ack, uint32_t seq,
			   uint32_t flags, uint8_t opcode)
{
	struct eigrp_header *h = (void *)buf;
	memset(buf, 0, len);
	h->version = EIGRP_HEADER_VERSION;
	h->opcode = opcode;
	h->sequence = htonl(seq);
	h->ack = htonl(ack);
	h->flags = htonl(flags);
	h->ASNumber = htons(4473);
	h->checksum = htons(eigrp_checksum(buf, len));
}

static bool receive(eigrp_instance_t *instance, uint8_t *buf, size_t len,
		    bool multicast, bool known)
{
	eigrp_stream_t s = {.data = buf, .size = len, .endp = len};
	eigrp_packet_input_t input = {0};
	input.stream = &s;
	input.ifindex = 1;
	input.source.afi = AF_INET;
	input.source.ip.v4.s_addr =
		inet_addr(known ? "10.44.53.2" : "10.44.53.3");
	input.meta.eigrp_length = len;
	input.meta.destination_multicast = multicast;
	return eigrp_packet_ingress_authenticate(instance, &input);
}

int main(int argc, char **argv)
{
	eigrp_instance_t instance = {0};
	eigrp_virt_router_t vr = {0};
	eigrp_intf_t ei = {0};
	eigrp_nbr_t nbr = {0};
	eigrp_packet_queue_t queue = {0};
	eigrp_packet_t pending = {.sequence_number = 2};
	uint8_t buf[80];
	FILE *fixture;

	instance.AS = 4473;
	instance.virt_router = &vr;
	instance.af_vectors.packet_source_on_link = link_ok;
	instance.af_vectors.addr_snprintf = addr_string;
	pthread_mutex_init(&instance.peer_lock, NULL);
	ei.eigrp = &instance;
	ei.name = "eth1";
	ei.params.auth_type = EIGRP_AUTH_TYPE_MD5;
	ei.params.auth_keychain = "test";
	nbr.ei = &ei;
	nbr.state = EIGRP_NEIGHBOR_UP;
	nbr.published = true;
	nbr.generation = 1;
	nbr.retrans_queue = &queue;
	queue.tail = &pending;
	queue.count = 1;
	test_ei = &ei;
	test_nbr = &nbr;
	assert(argc == 2);
	fixture = fopen(argv[1], "rb");
	assert(fixture);
	assert(fread(buf, 1, 20, fixture) == 20);
	assert(fgetc(fixture) == EOF);
	fclose(fixture);
	assert(ntohl(((struct eigrp_header *)buf)->ack) == 2);
	assert(receive(&instance, buf, 20, false, true));
	assert(hold_updates == 0);
	buf[3] ^= 1; /* Correct ACK with a corrupt checksum must fail. */
	assert(!receive(&instance, buf, 20, false, true));
	buf[3] ^= 1;
	assert(!receive(&instance, buf, 20, true, true));
	assert(!receive(&instance, buf, 20, false, false));
	packet_prepare(buf, 20, 3, 0, 0, EIGRP_OPC_HELLO);
	/* Ingress accepts a bare ACK from a known peer; RTP checks the
	 * outstanding sequence on the owning AF thread. */
	assert(receive(&instance, buf, 20, false, true));
	assert(queue.tail == &pending);
	packet_prepare(buf, 21, 2, 0, 0, EIGRP_OPC_HELLO);
	assert(!receive(&instance, buf, 21, false, true));
	packet_prepare(buf, 20, 0, 0, 0, EIGRP_OPC_HELLO);
	assert(!receive(&instance, buf, 20, false, true));
	packet_prepare(buf, 20, 2, 0, 1, EIGRP_OPC_HELLO);
	assert(!receive(&instance, buf, 20, false, true));
	packet_prepare(buf, 20, 2, 1, 0, EIGRP_OPC_HELLO);
	assert(!receive(&instance, buf, 20, false, true));
	packet_prepare(buf, 20, 2, 0, 0, EIGRP_OPC_UPDATE);
	assert(!receive(&instance, buf, 20, false, true));
	packet_prepare(buf, 20, 2, 0, 0, EIGRP_OPC_HELLO);
	nbr.state = EIGRP_NEIGHBOR_DOWN;
	assert(!receive(&instance, buf, 20, false, true));
	nbr.state = EIGRP_NEIGHBOR_UP;
	queue.tail = NULL;
	assert(receive(&instance, buf, 20, false, true));
	queue.tail = &pending;
	ei.params.auth_type = EIGRP_AUTH_TYPE_SHA256;
	assert(!receive(&instance, buf, 20, false, true));
	ei.params.auth_type = EIGRP_AUTH_TYPE_MD5;
	ei.params.auth_keychain = NULL;
	assert(!receive(&instance, buf, 20, false, true));
	assert(hold_updates == 0);
	pthread_mutex_destroy(&instance.peer_lock);
	puts("Cisco MD5 bare ACK ingress passed");
	return 0;
}

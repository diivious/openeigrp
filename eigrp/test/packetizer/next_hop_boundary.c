// SPDX-License-Identifier: ISC
/* Copyright (C) 2026 Donnie V. Savage */
/* Execute the production packetizer's packet-full retry with explicit hops. */
#include <assert.h>
#include <arpa/inet.h>
#include <stdarg.h>
#include <stdlib.h>
#include <string.h>

/* Include the implementation so its private builder can be exercised. */
#include "../../code/eigrp_packetizer.c"

static int attempts;
static int queued;
static int offsets;
static bool policy;
static int family;
static uint8_t original_hop[16];

static void hop_set(eigrp_addr_t *addr, int af)
{
	const char *literal = af == AF_INET ? "10.0.0.77" : "fe80::77";
	memset(addr, 0, sizeof(*addr));
	addr->afi = af;
	assert(inet_pton(af, literal, &addr->ip) == 1);
}

uint32_t eigrp_packet_sequence_reserve(eigrp_instance_t *eigrp)
{
	(void)eigrp;
	return (uint32_t)(queued + 10);
}

eigrp_packet_t *eigrp_packet_create(size_t size, eigrp_nbr_t *nbr)
{
	eigrp_packet_t *packet = calloc(1, sizeof(*packet));
	assert(packet);
	packet->nbr = nbr;
	packet->s = calloc(1, sizeof(*packet->s));
	assert(packet->s);
	packet->s->data = calloc(1, size);
	assert(packet->s->data);
	packet->s->size = size;
	return packet;
}

void eigrp_packet_free(eigrp_packet_t *packet)
{
	free(packet->s->data);
	free(packet->s);
	free(packet);
}

void eigrp_packet_header_init(int opcode, eigrp_instance_t *instance,
			      eigrp_stream_t *stream, uint32_t flags,
			      uint32_t sequence, uint32_t ack)
{
	(void)opcode;
	(void)instance;
	(void)flags;
	(void)sequence;
	(void)ack;
	stream->endp = 20;
}

bool eigrp_auth_material_available(const eigrp_intf_t *ei)
{
	(void)ei;
	return false;
}
uint16_t eigrp_auth_tlv_md5_encode(eigrp_stream_t *s, eigrp_intf_t *ei)
{
	(void)s;
	(void)ei;
	return 0;
}
uint16_t eigrp_auth_tlv_sha256_encode(eigrp_stream_t *s, eigrp_intf_t *ei)
{
	(void)s;
	(void)ei;
	return 0;
}

bool eigrp_packet_multicast_reliable_enqueue(eigrp_instance_t *instance,
					     eigrp_intf_t *ei,
					     eigrp_packet_t *packet)
{
	(void)instance;
	(void)ei;
	assert(packet->length > 20);
	queued++;
	eigrp_packet_free(packet);
	return true;
}

void eigrp_packet_enqueue(eigrp_packet_queue_t *queue, eigrp_packet_t *packet)
{
	(void)queue;
	(void)packet;
	assert(!"unexpected unicast queue");
}

void eigrp_debug_transmit_event(unsigned long category,
				const eigrp_instance_t *instance,
				const eigrp_intf_t *ei, const eigrp_nbr_t *nbr,
				const char *format, ...)
{
	(void)category;
	(void)instance;
	(void)ei;
	(void)nbr;
	(void)format;
}

void eigrp_packet_send_reliably(eigrp_instance_t *instance, eigrp_nbr_t *nbr)
{
	(void)instance;
	(void)nbr;
	assert(!"unexpected unicast transmission");
}

void eigrp_offset_metric_update(eigrp_instance_t *instance, eigrp_intf_t *ei,
				int direction, const eigrp_prefix_t *prefix,
				eigrp_metrics_t *metric)
{
	(void)instance;
	(void)ei;
	(void)direction;
	(void)prefix;
	metric->delay += 3;
	offsets++;
}

eigrp_result_t eigrp_eventlog_msg_add(eigrp_instance_t *eigrp, uint16_t opcode,
				      const eigrp_prefix_t *prefix,
				      eventmsg_arg_t a, eventmsg_arg_t b,
				      eventmsg_arg_t c, eventmsg_arg_t d)
{
	(void)eigrp;
	(void)opcode;
	(void)prefix;
	(void)a;
	(void)b;
	(void)c;
	(void)d;
	return 0;
}

void eigrp_log(eigrp_log_level_t level, const char *format, ...)
{
	(void)level;
	(void)format;
	assert(!"unexpected encode failure");
}

int eigrp_packet_route_encode_append(eigrp_instance_t *instance,
				     eigrp_intf_t *ei, eigrp_nbr_t *nbr,
				     eigrp_packet_encoder_t encoder,
				     eigrp_stream_t *stream,
				     eigrp_route_descriptor_t *route,
				     uint16_t limit)
{
	const size_t count = family == AF_INET ? 4 : 16;
	(void)instance;
	(void)ei;
	(void)nbr;
	(void)encoder;
	(void)limit;
	assert(route->metric.delay == 13);
	if (policy)
		assert(!route->nexthop.afi
		       && memcmp(&route->nexthop.ip, (uint8_t[16]){0}, count)
				  == 0);
	else
		assert(route->nexthop.afi == family
		       && memcmp(&route->nexthop.ip, original_hop, count) == 0);
	attempts++;
	if (attempts == 2)
		return -1; /* Next TLV does not fit the first packet. */
	stream->endp += 40;
	return 40;
}

static void run_case(int af, bool next_hop_self)
{
	eigrp_instance_t instance = {0};
	eigrp_intf_t ei = {0};
	eigrp_route_descriptor_t route = {0};
	eigrp_packetizer_builder_t builder = {0};

	attempts = queued = offsets = 0;
	family = af;
	policy = next_hop_self;
	instance.af_vectors.packet_ip_header_bytes = af == AF_INET ? 20 : 40;
	ei.eigrp = &instance;
	ei.next_hop_self = next_hop_self;
	ei.name = "eth0";
	hop_set(&route.nexthop, af);
	memcpy(original_hop, &route.nexthop.ip, sizeof(original_hop));
	route.metric.delay = 10;

	builder.eigrp = &instance;
	builder.ei = &ei;
	builder.encoder = (eigrp_packet_encoder_t)1;
	builder.opcode = EIGRP_OPC_UPDATE;
	builder.packet_limit = 100;
	assert(eigrp_packetizer_builder_route_add(&builder, &route) > 0);
	assert(eigrp_packetizer_builder_route_add(&builder, &route) > 0);
	eigrp_packetizer_builder_flush(&builder);
	assert(attempts == 3 && queued == 2 && offsets == 3);
	assert(route.nexthop.afi == af);
	assert(memcmp(&route.nexthop.ip, original_hop, af == AF_INET ? 4 : 16)
	       == 0);
	assert(route.metric.delay == 10);
}

int main(void)
{
	run_case(AF_INET, true);
	run_case(AF_INET, false);
	run_case(AF_INET6, true);
	run_case(AF_INET6, false);
	return 0;
}

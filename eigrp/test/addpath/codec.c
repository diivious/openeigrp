/* Compiled codec AddPath regression; RFC wire layout provisional pending Cisco
 * captures. */
#include <arpa/inet.h>
#include <assert.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdarg.h>
#include <stdint.h>
#include <string.h>

#include "eigrp/code/eigrp.h"
#include "eigrp/code/eigrp_log.h"
#include "eigrp/code/eigrp_filter.h"
#include "eigrp/code/eigrp_fsm.h"
#include "eigrp/code/eigrp_metric.h"
#include "eigrp/code/eigrp_prefix.h"
#include "eigrp/code/eigrp_stream.h"
#include "eigrp/code/eigrp_structs.h"
#include "eigrp/code/eigrp_packet.h"
#include "eigrp/code/eigrp_neighbor.h"
#include "eigrp/code/eigrp_tlv1.h"
#include "eigrp/code/eigrp_tlv2.h"

static eigrp_route_descriptor_t decoded_route;
static unsigned int route_free_count;

eigrp_route_descriptor_t *eigrp_topology_route_create(eigrp_intf_t *ei)
{
	memset(&decoded_route, 0, sizeof(decoded_route));
	decoded_route.ei = ei;
	return &decoded_route;
}

void eigrp_topology_route_free(eigrp_route_descriptor_t *route)
{
	assert(route == &decoded_route);
	route_free_count++;
}

bool eigrp_filter_prefix_update(eigrp_instance_t *eigrp, eigrp_intf_t *ei,
				int direction, const eigrp_prefix_t *prefix)
{
	(void)eigrp;
	(void)ei;
	(void)direction;
	(void)prefix;
	return false;
}

bool eigrp_debug_packet_any_enabled(uint8_t direction)
{
	(void)direction;
	return false;
}

void eigrp_log(eigrp_log_level_t level, const char *format, ...)
{
	(void)level;
	(void)format;
}

const char *eigrp_print_addr(const eigrp_addr_t *address)
{
	(void)address;
	return "test";
}

eigrp_scaled_t eigrp_delay_to_scaled(eigrp_delay_t delay)
{
	delay = delay ? delay : EIGRP_DELAY_MIN;
	return delay * EIGRP_CLASSIC_SCALER;
}

eigrp_delay_t eigrp_scaled_to_delay(eigrp_scaled_t scaled)
{
	eigrp_delay_t delay = scaled / EIGRP_CLASSIC_SCALER;
	return delay ? delay : EIGRP_DELAY_MIN;
}

int eigrp_sys_packet_send(eigrp_instance_t *eigrp, eigrp_intf_t *ei,
			  const eigrp_address_t *destination,
			  const uint8_t *payload, size_t length)
{
	(void)eigrp;
	(void)ei;
	(void)destination;
	(void)payload;
	(void)length;
	return -1;
}

bool eigrp_sys_packet_receive(eigrp_instance_t *eigrp, uint8_t *buffer,
			      size_t capacity, size_t *received_length,
			      eigrp_ifindex_t *ifindex, eigrp_address_t *source,
			      eigrp_address_t *destination,
			      eigrp_packet_rx_meta_t *meta)
{
	(void)eigrp;
	(void)buffer;
	(void)capacity;
	(void)received_length;
	(void)ifindex;
	(void)source;
	(void)destination;
	(void)meta;
	return false;
}

eigrp_intf_t *eigrp_intf_lookup_by_ifindex(eigrp_instance_t *eigrp,
					   eigrp_ifindex_t ifindex)
{
	(void)eigrp;
	(void)ifindex;
	return NULL;
}

eigrp_intf_t *eigrp_intf_lookup_by_local_addr(eigrp_instance_t *eigrp,
					      const eigrp_addr_t *address)
{
	(void)eigrp;
	(void)address;
	return NULL;
}


static void address_set(eigrp_addr_t *addr, int af, unsigned id)
{
	memset(addr, 0, sizeof(*addr));
	addr->afi = af;
	if (af == AF_INET) {
		unsigned char octets[] = {192, 0, 2, (unsigned char)id};
		memcpy(&addr->ip.v4, octets, 4);
	} else {
		assert(inet_pton(AF_INET6, "2001:db8::1", &addr->ip.v6) == 1);
		addr->ip.v6.s6_addr[15] = (unsigned char)id;
	}
}

static void route_set(eigrp_route_descriptor_t *route, int af, bool external,
		      unsigned count)
{
	memset(route, 0, sizeof(*route));
	route->type = external ? EIGRP_EXT : EIGRP_INT;
	route->dest.address.afi =
		af == AF_INET ? EIGRP_AFI_IPV4 : EIGRP_AFI_IPV6;
	route->dest.prefix_length = af == AF_INET ? 32 : 128;
	if (af == AF_INET)
		assert(inet_pton(AF_INET, "203.0.113.3",
				 route->dest.address.bytes)
		       == 1);
	else
		assert(inet_pton(AF_INET6, "2001:db8:abcd::3",
				 route->dest.address.bytes)
		       == 1);
	address_set(&route->nexthop, af, 100);
	route->metric.delay = 100;
	route->metric.bandwidth = 10000;
	route->metric.reliability = 255;
	route->metric.load = 1;
	route->extdata.orig = 0x0a000001;
	route->extdata.as = 4453;
	route->extdata.tag = 0x87654321;
	route->extdata.protocol = 6;
	route->extdata.flags = 2;
	route->addpath_count = (uint8_t)count;
	for (unsigned i = 0; i < count; i++) {
		eigrp_addpath_t *p = &route->addpaths[i];
		address_set(&p->nexthop, af, i + 11);
		p->router_id = 0x0a0b0c00 + i;
		p->tag = 0x12345600 + i;
		p->protocol = 5 + i;
		p->flags = 6 + i;
	}
}

static void check_case(int af, bool external, unsigned count)
{
	eigrp_instance_t instance = {0};
	eigrp_intf_t interface = {0};
	eigrp_nbr_t neighbor = {0};
	eigrp_tlv_codec_t codec = {0};
	eigrp_route_descriptor_t route;
	eigrp_route_descriptor_t *decoded;
	eigrp_stream_t *stream = eigrp_stream_create(512);
	size_t attr_size = (af == AF_INET ? 12U : 24U) + (external ? 2U : 0U);
	size_t encoded_length, hdr_offset = 12;
	assert(stream);
	if (af == AF_INET)
		eigrp_ipv4_init(&instance.af_vectors);
	else
		eigrp_ipv6_init(&instance.af_vectors);
	eigrp_tlv2_init(&codec);
	interface.eigrp = &instance;
	neighbor.ei = &interface;
	address_set(&neighbor.src, af, 20);
	route_set(&route, af, external, count);
	encoded_length =
		codec.encoder(&instance, &interface, &neighbor, stream, &route);
	assert(encoded_length == stream->endp);
	assert(encoded_length > 0);
	assert(stream->data[hdr_offset] == count * (attr_size + 2U) / 2U);
	/* Check real opcode 7 framing, word count, and each complete payload.
	 */
	for (unsigned i = 0; i < count; i++) {
		size_t start = hdr_offset + 24U + i * (2U + attr_size);
		assert(stream->data[start] == 7);
		assert(stream->data[start + 1] == attr_size / 2U);
	}
	printf("VECTOR|%u|%u|%u|", af == AF_INET ? 4U : 6U, external ? 1U : 0U,
	       count);
	for (size_t i = 0; i < stream->endp; i++)
		printf("%02x", stream->data[i]);
	printf("\n");
	decoded = codec.decoder(&instance, &neighbor, stream,
				(uint16_t)encoded_length);
	assert(decoded == &decoded_route);
	assert(decoded->addpath_count == count);
	for (unsigned i = 0; i < count; i++) {
		eigrp_addpath_t *a = &route.addpaths[i];
		eigrp_addpath_t *b = &decoded->addpaths[i];
		assert(a->nexthop.afi == b->nexthop.afi);
		assert(memcmp(&a->nexthop.ip, &b->nexthop.ip,
			      af == AF_INET ? 4U : 16U)
		       == 0);
		assert(a->router_id == b->router_id);
		assert(a->tag == b->tag);
		if (external) {
			assert(a->protocol == b->protocol);
			assert(a->flags == b->flags);
		}
	}
	if (count) {
		/* Corrupt word count; the real decoder must reject the TLV. */
		eigrp_stream_set_getp(stream, 0);
		stream->data[hdr_offset + 25] ^= 1;
		assert(codec.decoder(&instance, &neighbor, stream,
				     (uint16_t)encoded_length)
		       == NULL);
		assert(stream->getp == stream->endp);
	}
	eigrp_stream_free(stream);
}

static void check_withdrawal(int af, bool external)
{
	eigrp_instance_t instance = {0};
	eigrp_intf_t interface = {0};
	eigrp_nbr_t neighbor = {0};
	eigrp_tlv_codec_t codec = {0};
	eigrp_route_descriptor_t route;
	eigrp_stream_t *stream = eigrp_stream_create(512);
	assert(stream);
	if (af == AF_INET)
		eigrp_ipv4_init(&instance.af_vectors);
	else
		eigrp_ipv6_init(&instance.af_vectors);
	eigrp_tlv2_init(&codec);
	interface.eigrp = &instance;
	neighbor.ei = &interface;
	address_set(&neighbor.src, af, 20);
	route_set(&route, af, external, EIGRP_ADDPATH_MAX);
	route.metric.delay = EIGRP_MAX_METRIC;
	assert(codec.encoder(&instance, &interface, &neighbor, stream, &route));
	assert(stream->data[12] == 0);
	assert(codec.decoder(&instance, &neighbor, stream,
			     (uint16_t)stream->endp)
	       == &decoded_route);
	assert(decoded_route.addpath_count == 0);
	eigrp_stream_free(stream);
}

int main(void)
{
	for (int af = 0; af < 2; af++)
		for (int ext = 0; ext < 2; ext++) {
			for (unsigned n = 0; n <= EIGRP_ADDPATH_MAX; n++)
				check_case(af ? AF_INET6 : AF_INET, ext != 0,
					   n);
			check_withdrawal(af ? AF_INET6 : AF_INET, ext != 0);
		}
	return 0;
}

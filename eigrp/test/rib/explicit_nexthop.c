// SPDX-License-Identifier: ISC
/* Copyright (C) 2026 Donnie V. Savage */
/* Exercise real DUAL successor export through the portable RIB boundary. */
#include <assert.h>
#include <arpa/inet.h>
#include <stdarg.h>
#include <stdint.h>
#include <string.h>

#include "eigrp.h"
#include "eigrp_log.h"
#include "eigrp_structs.h"
#include "eigrp_debug.h"
#include "eigrp_eventlog.h"
#include "eigrp_neighbor.h"
#include "eigrp_rib.h"
#include "eigrp_topology.h"

unsigned long term_debug_eigrp;
unsigned long term_debug_eigrp_nei;
unsigned long term_debug_eigrp_packet[EIGRP_DEBUG_PACKET_CATEGORY_MAX];
unsigned long term_debug_eigrp_transmit;
unsigned long term_debug_eigrp_notifications;
static unsigned adds, dels;
static size_t count;
static eigrp_rib_nexthop_t last[2];

void eigrp_sys_event_cancel(eigrp_event_t **event)
{
	if (event)
		*event = NULL;
}
void eigrp_log(eigrp_log_level_t level, const char *format, ...)
{
	(void)level;
	(void)format;
}
bool eigrp_debug_af_enabled(eigrp_instance_t *eigrp,
			    eigrp_debug_af_category_t category,
			    const eigrp_addr_t *neighbor)
{
	(void)eigrp;
	(void)category;
	(void)neighbor;
	return false;
}
eigrp_afi_t eigrp_instance_afi(const eigrp_instance_t *runtime)
{
	return runtime ? runtime->af_vectors.afi : 0;
}
eigrp_result_t eigrp_eventlog_msg_add(eigrp_instance_t *eigrp, uint16_t opcode,
				      const eigrp_prefix_t *addr,
				      eventmsg_arg_t a, eventmsg_arg_t b,
				      eventmsg_arg_t c, eventmsg_arg_t d)
{
	(void)eigrp;
	(void)opcode;
	(void)addr;
	(void)a;
	(void)b;
	(void)c;
	(void)d;
	return EIGRP_RESULT_SUCCESS;
}
eigrp_result_t eigrp_rib_route_del(eigrp_instance_t *eigrp,
				   const eigrp_prefix_t *prefix)
{
	(void)eigrp;
	(void)prefix;
	dels++;
	return EIGRP_RESULT_SUCCESS;
}
eigrp_result_t eigrp_rib_route_add(eigrp_instance_t *eigrp,
				   const eigrp_rib_route_t *route)
{
	(void)eigrp;
	assert(route && route->nexthop_count <= 2);
	adds++;
	count = route->nexthop_count;
	memcpy(last, route->nexthops, count * sizeof(*last));
	return EIGRP_RESULT_SUCCESS;
}

static void set_addr(eigrp_addr_t *address, int family, const char *name)
{
	memset(address, 0, sizeof(*address));
	address->afi = family;
	assert(inet_pton(family, name, &address->ip) == 1);
}

static void test_forwarding(int family, const char *peer,
			    const char *third_party, const char *invalid)
{
	eigrp_instance_t runtime = {0};
	eigrp_intf_t ei = {0}, other = {0};
	eigrp_nbr_t nbr = {0};
	eigrp_prefix_descriptor_t *prefix;
	eigrp_route_descriptor_t *route;
	uint8_t expected[16];
	size_t bytes = family == AF_INET ? 4 : 16;

	runtime.af_vectors.afi =
		family == AF_INET ? EIGRP_AFI_IPV4 : EIGRP_AFI_IPV6;
	runtime.max_paths = 2;
	runtime.distance_internal = EIGRP_DISTANCE_INTERNAL_DEFAULT;
	ei.ifindex = 7;
	ei.eigrp = &runtime;
	ei.address.address.afi = runtime.af_vectors.afi;
	other.ifindex = 8;
	nbr.ei = &ei;
	set_addr(&nbr.src, family, peer);
	prefix = eigrp_topo_pfx_create();
	assert(prefix);
	prefix->state = EIGRP_FSM_STATE_PASSIVE;
	prefix->fdistance = 20;
	prefix->distance = 20;
	route = eigrp_topo_route_create(&ei);
	assert(route);
	route->adv_router = &nbr;
	route->distance = 20;
	route->reported_distance = 10;
	route->type =
		family == AF_INET ? EIGRP_TLV_IPv4_INT : EIGRP_TLV_IPv6_INT;
	route->flags |= EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG;

	/* First installation uses the advertised gateway, not the sender. */
	set_addr(&route->nexthop, family, third_party);
	assert(inet_pton(family, third_party, expected) == 1);
	adds = dels = 0;
	eigrp_topo_route_add(&runtime, prefix, route);
	assert(adds == 1 && dels == 0 && count == 1);
	assert(last[0].ifindex == 7 && last[0].gateway_present);
	assert(last[0].gateway.afi == runtime.af_vectors.afi);
	assert(memcmp(last[0].gateway.bytes, expected, bytes) == 0);

	/* Refresh of the same DUAL successor must replace the gateway. */
	set_addr(&route->nexthop, family, peer);
	eigrp_topo_rib_update(&runtime, prefix);
	assert(count == 1 && adds == 2);
	assert(memcmp(last[0].gateway.bytes, &nbr.src.ip, bytes) == 0);

	/* Classic TLV zero means use the advertising peer. */
	set_addr(&route->nexthop, family, family == AF_INET ? "0.0.0.0" : "::");
	eigrp_topo_rib_update(&runtime, prefix);
	assert(count == 1 && adds == 3);
	assert(memcmp(last[0].gateway.bytes, &nbr.src.ip, bytes) == 0);

	/* Invalid received addresses must not fall back to the sender. */
	set_addr(&route->nexthop, family, invalid);
	eigrp_topo_rib_update(&runtime, prefix);
	assert(adds == 3 && dels == 1);
	assert(!(route->flags & EIGRP_ROUTE_DESCRIPTOR_INTABLE_FLAG));

	/* An explicit opposite-family address cannot be installed. */
	set_addr(&route->nexthop, family == AF_INET ? AF_INET6 : AF_INET,
		 family == AF_INET ? "2001:db8::1" : "10.0.0.77");
	eigrp_topo_rib_update(&runtime, prefix);
	assert(adds == 3 && dels == 1);

	/* Ingress interface identity and ifindex are prerequisites. */
	set_addr(&route->nexthop, family, third_party);
	nbr.ei = &other;
	eigrp_topo_rib_update(&runtime, prefix);
	assert(adds == 3 && dels == 1);
	nbr.ei = &ei;
	ei.ifindex = 0;
	eigrp_topo_rib_update(&runtime, prefix);
	assert(adds == 3 && dels == 1);
	ei.ifindex = 7;
	eigrp_topo_rib_update(&runtime, prefix);
	assert(adds == 4 && dels == 1);
	assert(memcmp(last[0].gateway.bytes, expected, bytes) == 0);

	/* DUAL withdrawal retires the installed successor. */
	route->flags &= ~EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG;
	eigrp_topo_rib_update(&runtime, prefix);
	assert(dels == 2);
	eigrp_topo_pfx_free(prefix);
}

static void test_ecmp(void)
{
	eigrp_instance_t runtime = {0};
	eigrp_intf_t ei = {0};
	eigrp_nbr_t peers[2] = {{0}};
	eigrp_route_descriptor_t *paths[2];
	eigrp_prefix_descriptor_t *prefix;
	const char *gates[] = {"10.0.0.77", "10.0.0.78"};
	uint8_t expected[2][4];
	unsigned i;

	runtime.af_vectors.afi = EIGRP_AFI_IPV4;
	runtime.max_paths = 2;
	runtime.traffic_share_balanced = true;
	ei.ifindex = 7;
	ei.address.address.afi = EIGRP_AFI_IPV4;
	ei.eigrp = &runtime;
	prefix = eigrp_topo_pfx_create();
	assert(prefix);
	prefix->state = EIGRP_FSM_STATE_PASSIVE;
	prefix->distance = prefix->fdistance = 20;
	for (i = 0; i < 2; i++) {
		peers[i].ei = &ei;
		set_addr(&peers[i].src, AF_INET, i ? "10.0.0.2" : "10.0.0.1");
		paths[i] = eigrp_topo_route_create(&ei);
		assert(paths[i]);
		paths[i]->adv_router = &peers[i];
		paths[i]->distance = i ? 60 : 20;
		paths[i]->reported_distance = 10;
		paths[i]->type = EIGRP_TLV_IPv4_INT;
		paths[i]->flags = EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG;
		set_addr(&paths[i]->nexthop, AF_INET, gates[i]);
		assert(inet_pton(AF_INET, gates[i], expected[i]) == 1);
		eigrp_topo_route_add(&runtime, prefix, paths[i]);
	}

	adds = dels = 0;
	eigrp_topo_rib_update(&runtime, prefix);
	assert(adds == 1 && count == 2);
	assert(memcmp(last[0].gateway.bytes, expected[0], 4) == 0);
	assert(memcmp(last[1].gateway.bytes, expected[1], 4) == 0);
	assert(last[0].weight == 3 && last[1].weight == 1);
	assert(paths[0]->flags & EIGRP_ROUTE_DESCRIPTOR_INTABLE_FLAG);
	assert(paths[1]->flags & EIGRP_ROUTE_DESCRIPTOR_INTABLE_FLAG);

	/* One invalid ECMP path must not poison the good path or its weight. */
	set_addr(&paths[1]->nexthop, AF_INET, "224.0.0.1");
	eigrp_topo_rib_update(&runtime, prefix);
	assert(adds == 2 && dels == 0 && count == 1);
	assert(memcmp(last[0].gateway.bytes, expected[0], 4) == 0);
	assert(last[0].weight == 1);
	assert(paths[0]->flags & EIGRP_ROUTE_DESCRIPTOR_INTABLE_FLAG);
	assert(!(paths[1]->flags & EIGRP_ROUTE_DESCRIPTOR_INTABLE_FLAG));

	set_addr(&paths[1]->nexthop, AF_INET, gates[1]);
	eigrp_topo_rib_update(&runtime, prefix);
	assert(adds == 3 && count == 2 && last[0].weight == 3);
	assert(paths[1]->flags & EIGRP_ROUTE_DESCRIPTOR_INTABLE_FLAG);
	paths[0]->flags &= ~EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG;
	paths[1]->flags &= ~EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG;
	eigrp_topo_rib_update(&runtime, prefix);
	assert(dels == 1);
	eigrp_topo_pfx_free(prefix);
}

int main(void)
{
	test_forwarding(AF_INET, "10.0.0.1", "10.0.0.77", "224.0.0.1");
	test_forwarding(AF_INET6, "fe80::1", "fe80::77", "ff02::1");
	test_ecmp();
	return 0;
}

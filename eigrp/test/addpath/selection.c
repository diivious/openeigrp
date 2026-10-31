/* Portable AddPath selection tests, executing production eigrp_addpath.c. */
#include <assert.h>
#include <arpa/inet.h>
#include <stdbool.h>
#include <stdint.h>
#include <string.h>

#include "eigrp/code/eigrp.h"
#include "eigrp/code/eigrp_structs.h"
#include "eigrp/code/eigrp_topology.h"
#include "eigrp/code/eigrp_addpath.h"

static eigrp_route_descriptor_t candidate[6];
static unsigned candidate_count;

eigrp_route_descriptor_t *
eigrp_topology_route_read(eigrp_prefix_descriptor_t *prefix)
{
	(void)prefix;
	return candidate_count ? &candidate[0] : NULL;
}

eigrp_route_descriptor_t *
eigrp_topology_route_iterator_first(eigrp_prefix_descriptor_t *prefix,
				    eigrp_topology_route_iterator_t *it)
{
	(void)prefix;
	it->queue = 0;
	return candidate_count ? &candidate[0] : NULL;
}

eigrp_route_descriptor_t *
eigrp_topology_route_iterator_next(eigrp_topology_route_iterator_t *it)
{
	it->queue++;
	return it->queue < candidate_count ? &candidate[it->queue] : NULL;
}

static void addr(eigrp_addr_t *a, int af, unsigned last)
{
	memset(a, 0, sizeof(*a));
	a->afi = af;
	if (af == AF_INET) {
		const uint8_t v[] = {192, 0, 2, (uint8_t)last};
		memcpy(&a->ip.v4, v, sizeof(v));
	} else {
		assert(inet_pton(AF_INET6, "2001:db8::1", &a->ip.v6) == 1);
		a->ip.v6.s6_addr[15] = (uint8_t)last;
	}
}

static void verify(int af)
{
	eigrp_instance_t instance = {0};
	eigrp_intf_t ei = {0};
	eigrp_prefix_descriptor_t prefix = {0};
	eigrp_route_descriptor_t wire = {0};
	prefix.state = EIGRP_FSM_STATE_PASSIVE;
	ei.add_paths = EIGRP_ADDPATH_MAX;
	ei.next_hop_self = false;
	candidate_count = 6;
	for (unsigned i = 0; i < candidate_count; i++) {
		memset(&candidate[i], 0, sizeof(candidate[i]));
		candidate[i].prefix = &prefix;
		candidate[i].distance = 100;
		candidate[i].flags = EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG;
		addr(&candidate[i].nexthop, af, 10 + i);
		candidate[i].extdata.orig = 0x10203040 + i;
		candidate[i].extdata.tag = 30 + i;
	}
	wire = candidate[0];
	/* Five eligible next hops, but never more than four extras. */
	eigrp_addpath_select(&instance, &ei, &wire);
	assert(wire.addpath_count == 4);
	for (unsigned i = 0; i < 4; i++) {
		assert(wire.addpaths[i].tag == 31 + i);
		assert(wire.addpaths[i].router_id == 0x10203041 + i);
	}
	ei.add_paths = 1;
	eigrp_addpath_select(&instance, &ei, &wire);
	assert(wire.addpath_count == 1);
	ei.add_paths = 2;
	candidate[1].nexthop = candidate[0].nexthop;
	candidate[2].distance = 101;
	candidate[3].flags = 0;
	eigrp_addpath_select(&instance, &ei, &wire);
	assert(wire.addpath_count == 2);
	assert(wire.addpaths[0].tag == 34);
	assert(wire.addpaths[1].tag == 35);
	/* Received hub attributes are deduplicated by next-hop, not peer. */
	candidate_count = 2;
	candidate[0].addpath_count = 3;
	addr(&candidate[0].addpaths[0].nexthop, af, 80);
	addr(&candidate[0].addpaths[1].nexthop, af, 80);
	addr(&candidate[0].addpaths[2].nexthop, af, 81);
	candidate[0].addpaths[0].tag = 800;
	candidate[0].addpaths[2].tag = 810;
	wire = candidate[0];
	eigrp_addpath_select(&instance, &ei, &wire);
	assert(wire.addpath_count == 2);
	assert(wire.addpaths[0].tag == 800);
	assert(wire.addpaths[1].tag == 810);
	/* No stale attributes when output is disabled, poisoned, or Active. */
	ei.add_paths = 0;
	eigrp_addpath_select(&instance, &ei, &wire);
	assert(wire.addpath_count == 0);
	ei.add_paths = 4;
	ei.next_hop_self = true;
	eigrp_addpath_route_prepare(&instance, &ei, &wire);
	assert(wire.addpath_count == 0);
	assert(memcmp(&wire.nexthop, &(eigrp_addr_t){0}, sizeof(wire.nexthop))
	       == 0);
	ei.next_hop_self = false;
	wire = candidate[0];
	candidate[1].nexthop = candidate[0].nexthop;
	addr(&candidate[1].nexthop, af, 99);
	candidate[1].flags = EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG;
	eigrp_addpath_route_prepare(&instance, &ei, &wire);
	assert(wire.addpath_count >= 1);
	assert(wire.addpaths[0].nexthop.afi == af);
	wire.metric.delay = EIGRP_MAX_METRIC;
	eigrp_addpath_select(&instance, &ei, &wire);
	assert(wire.addpath_count == 0);
	wire.metric.delay = 100;
	prefix.state = EIGRP_FSM_STATE_ACTIVE_0;
	eigrp_addpath_select(&instance, &ei, &wire);
	assert(wire.addpath_count == 0);
}

int main(void)
{
	verify(AF_INET);
	verify(AF_INET6);
	return 0;
}

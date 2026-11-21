// SPDX-License-Identifier: ISC
/* Copyright (C) 2026 Donnie V. Savage */
/* Exercise the portable next-hop conversion and wire-policy implementation. */
#include <assert.h>
#include <arpa/inet.h>
#include <string.h>

#include "eigrp_structs.h"
#include "eigrp_neighbor.h"
#include "eigrp_nexthop.h"

static void addr_set(eigrp_addr_t *addr, int family, const char *name)
{
	memset(addr, 0, sizeof(*addr));
	addr->afi = family;
	assert(inet_pton(family, name, &addr->ip) == 1);
}

static void family_check(int family, eigrp_afi_t afi, const char *peer,
			 const char *forwarding, const char *invalid)
{
	eigrp_intf_t intf = {0}, other = {0};
	eigrp_nbr_t nbr = {0};
	eigrp_route_descriptor_t route = {0}, wire;
	eigrp_rib_nexthop_t rib = {0}, host[2] = {{0}};
	eigrp_rib_route_t source = {0};
	const eigrp_addr_t *addr;
	const size_t bytes = family == AF_INET ? 4 : 16;

	intf.ifindex = 7;
	intf.address.address.afi = afi;
	nbr.ei = &intf;
	addr_set(&nbr.src, family, peer);
	route.ei = &intf;
	route.adv_router = &nbr;

	/* An unspecified topology next hop is resolved to the advertising peer.
	 */
	addr_set(&route.nexthop, family, family == AF_INET ? "0.0.0.0" : "::");
	assert(eigrp_nexthop_topo_get(&route) == &nbr.src);
	assert(eigrp_nexthop_rib_set(&route, &rib));
	assert(rib.ifindex == 7 && rib.gateway_present
	       && rib.gateway.afi == afi);
	assert(memcmp(rib.gateway.bytes, &nbr.src.ip, bytes) == 0);

	/* Explicit next hops remain distinct from the advertising peer. */
	addr_set(&route.nexthop, family, forwarding);
	addr = eigrp_nexthop_topo_get(&route);
	assert(addr == &route.nexthop);
	assert(eigrp_nexthop_rib_set(&route, &rib));
	assert(memcmp(rib.gateway.bytes, &route.nexthop.ip, bytes) == 0);

	/* Interface or address mismatches reject the path. */
	other.ifindex = 8;
	nbr.ei = &other;
	assert(!eigrp_nexthop_rib_set(&route, &rib));
	nbr.ei = &intf;
	intf.ifindex = 0;
	assert(!eigrp_nexthop_rib_set(&route, &rib));
	intf.ifindex = 7;
	addr_set(&route.nexthop, family, invalid);
	assert(!eigrp_nexthop_rib_set(&route, &rib));
	addr_set(&route.nexthop, family, forwarding);

	/* next-hop-self acts on the outgoing copy, not topology ownership. */
	wire = route;
	intf.next_hop_self = false;
	eigrp_nexthop_self_set(&intf, &wire);
	assert(memcmp(&wire.nexthop, &route.nexthop, sizeof(route.nexthop))
	       == 0);
	intf.next_hop_self = true;
	eigrp_nexthop_self_set(&intf, &wire);
	assert(memcmp(&wire.nexthop, &(eigrp_addr_t){0}, sizeof(wire.nexthop))
	       == 0);
	assert(memcmp(&route.nexthop, &wire.nexthop, sizeof(route.nexthop))
	       != 0);

	/* Redistribution selects the first gateway-bearing RIB path. */
	source.nexthops = host;
	source.nexthop_count = 2;
	host[1].gateway_present = true;
	host[1].gateway.afi = afi;
	memcpy(host[1].gateway.bytes, &route.nexthop.ip, bytes);
	eigrp_nexthop_redist_set(&route, &source);
	assert(route.nexthop.afi == family);
	assert(memcmp(&route.nexthop.ip, host[1].gateway.bytes, bytes) == 0);

	/* Withdrawal/no gateway clears stale learned forwarding state. */
	host[1].gateway_present = false;
	eigrp_nexthop_redist_set(&route, &source);
	assert(memcmp(&route.nexthop, &(eigrp_addr_t){0}, sizeof(route.nexthop))
	       == 0);
	source.nexthops = NULL;
	eigrp_nexthop_redist_set(&route, &source);
	assert(route.nexthop.afi == 0);
}

int main(void)
{
	family_check(AF_INET, EIGRP_AFI_IPV4, "10.0.0.1", "10.0.0.77",
		     "224.0.0.1");
	family_check(AF_INET6, EIGRP_AFI_IPV6, "fe80::1", "fe80::77",
		     "ff02::1");
	return 0;
}

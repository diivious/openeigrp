// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * EIGRP next-hop interpretation and forwarding conversion.
 * Copyright (C) 2013-2016, 2026
 * Authors:
 *   Donnie Savage
 *   Jan Janovic
 *   Matej Perina
 *   Peter Orsag
 *   Peter Paluch
 *   Frantisek Gazo
 *   Tomas Hvorkovy
 *   Martin Kontsek
 *   Lukas Koribsky
 */
#include <string.h>

#include "eigrp_structs.h"
#include "eigrp_nexthop.h"
#include "eigrp_neighbor.h"

void eigrp_nexthop_redist_set(eigrp_route_descriptor_t *route,
			      const eigrp_rib_route_t *source_route)
{
	const eigrp_rib_nexthop_t *nexthop = NULL;
	size_t index;

	memset(&route->nexthop, 0, sizeof(route->nexthop));
	if (!source_route->nexthops)
		return;
	for (index = 0; index < source_route->nexthop_count; index++) {
		if (source_route->nexthops[index].gateway_present) {
			nexthop = &source_route->nexthops[index];
			break;
		}
	}
	if (!nexthop)
		return;

	if (nexthop->gateway.afi == EIGRP_AFI_IPV4) {
		route->nexthop.afi = AF_INET;
		memcpy(&route->nexthop.ip.v4, nexthop->gateway.bytes,
		       sizeof(route->nexthop.ip.v4));
	} else if (nexthop->gateway.afi == EIGRP_AFI_IPV6) {
		route->nexthop.afi = AF_INET6;
		memcpy(&route->nexthop.ip.v6, nexthop->gateway.bytes,
		       sizeof(route->nexthop.ip.v6));
	}
}

/* TLV1 retains a zero forwarding address; TLV2 normalizes it to the peer.
 * In either case, only an unspecified next hop means "use the advertiser".
 */
const eigrp_addr_t *
eigrp_nexthop_topo_get(const eigrp_route_descriptor_t *route)
{
	const eigrp_addr_t *addr;

	if (!route)
		return NULL;
	addr = &route->nexthop;
	if (addr->afi == AF_INET) {
		if (addr->ip.v4.s_addr != INADDR_ANY)
			return addr;
	} else if (addr->afi == AF_INET6) {
		if (!IN6_IS_ADDR_UNSPECIFIED(&addr->ip.v6))
			return addr;
	} else if (addr->afi != 0) {
		/* An invalid advertised family must not silently use the peer.
		 */
		return addr;
	}
	return route->adv_router ? &route->adv_router->src : NULL;
}

bool eigrp_nexthop_rib_set(const eigrp_route_descriptor_t *route,
			   eigrp_rib_nexthop_t *nexthop)
{
	const eigrp_addr_t *addr;
	eigrp_afi_t afi;

	if (!route || !route->ei || !route->ei->ifindex || !nexthop)
		return false;
	/* The forwarding interface is the route's received interface, not
	 * an inferred interface for the gateway. Never export a stale path
	 * whose advertising neighbor has moved to another interface. */
	if (route->adv_router && route->adv_router->ei
	    && route->adv_router->ei != route->ei)
		return false;

	addr = eigrp_nexthop_topo_get(route);
	memset(nexthop, 0, sizeof(*nexthop));
	nexthop->ifindex = route->ei->ifindex;
	if (!addr || addr->afi == 0)
		/* Never treat a learned route with an unknown peer address as
		 * directly connected. Local routes can use the interface alone.
		 */
		return !route->adv_router || !route->adv_router->ei;

	afi = route->ei->address.address.afi;
	if (addr->afi == AF_INET && afi == EIGRP_AFI_IPV4) {
		uint32_t ip = ntohl(addr->ip.v4.s_addr);

		/* A zero TLV next hop may fall back to the peer, but an
		 * invalid nonzero forwarding address must never do so. */
		if (!ip || (ip >> 24) == 0 || (ip >> 24) == 127
		    || (ip & 0xe0000000U) == 0xe0000000U)
			return false;
		nexthop->gateway_present = true;
		nexthop->gateway.afi = EIGRP_AFI_IPV4;
		memcpy(nexthop->gateway.bytes, &addr->ip.v4,
		       sizeof(addr->ip.v4));
	} else if (addr->afi == AF_INET6 && afi == EIGRP_AFI_IPV6) {
		if (IN6_IS_ADDR_UNSPECIFIED(&addr->ip.v6)
		    || IN6_IS_ADDR_MULTICAST(&addr->ip.v6)
		    || IN6_IS_ADDR_LOOPBACK(&addr->ip.v6))
			return false;
		/* Link-local addresses are scoped by the required ifindex. */
		nexthop->gateway_present = true;
		nexthop->gateway.afi = EIGRP_AFI_IPV6;
		memcpy(nexthop->gateway.bytes, &addr->ip.v6,
		       sizeof(addr->ip.v6));
	} else {
		return false;
	}

	return true;
}

/* Mutate only a private wire descriptor. The topology-owned route must
 * retain its next hop for subsequent advertisements on other interfaces. */
void eigrp_nexthop_self_set(const eigrp_intf_t *ei,
			    eigrp_route_descriptor_t *route)
{
	if (ei->next_hop_self)
		memset(&route->nexthop, 0, sizeof(route->nexthop));
}

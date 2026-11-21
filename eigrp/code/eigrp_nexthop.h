// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Portable EIGRP next-hop interpretation and forwarding conversion.
 * Copyright (C) 2026 Donnie V. Savage
 */
#ifndef EIGRP_NEXTHOP_H
#define EIGRP_NEXTHOP_H

#include "eigrp_structs.h"
#include "eigrp_rib.h"

/* Effective topology next hop: an unspecified TLV next hop uses the peer. */
const eigrp_addr_t *
eigrp_nexthop_topo_get(const eigrp_route_descriptor_t *route);

/* Convert a topology route into a validated infrastructure RIB next hop. */
bool eigrp_nexthop_rib_set(const eigrp_route_descriptor_t *route,
			   eigrp_rib_nexthop_t *nexthop);

/* Import a redistributed host RIB gateway into the topology route. */
void eigrp_nexthop_redist_set(eigrp_route_descriptor_t *route,
			      const eigrp_rib_route_t *source_route);

/* Apply next-hop-self to an outgoing copy, never the stored topology route. */
void eigrp_nexthop_self_set(const eigrp_intf_t *ei,
			    eigrp_route_descriptor_t *route);

#endif /* EIGRP_NEXTHOP_H */

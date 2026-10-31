/*
 * EIGRP AddPath best-path selection.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <string.h>
#include "eigrp.h"
#include "eigrp_structs.h"
#include "eigrp_topology.h"
#include "eigrp_table.h"
#include "eigrp_packet.h"
#include "eigrp_fsm.h"
#include "eigrp_addpath.h"

/* Path identity on the wire is the next-hop address, not the advertising
 * neighbor: a hub can supply several paths learned from different spokes.
 */
static bool eigrp_addpath_nexthop_match(const eigrp_addr_t *a,
					const eigrp_addr_t *b)
{
	return a->afi == b->afi && memcmp(&a->ip, &b->ip, sizeof(a->ip)) == 0;
}

static void eigrp_addpath_nexthop_add(eigrp_route_descriptor_t *wire,
				      const eigrp_addpath_t *candidate,
				      uint8_t maximum)
{
	if (wire->addpath_count >= maximum || !candidate->nexthop.afi
	    || eigrp_addpath_nexthop_match(&wire->nexthop, &candidate->nexthop))
		return;
	for (uint8_t n = 0; n < wire->addpath_count; n++)
		if (eigrp_addpath_nexthop_match(&wire->addpaths[n].nexthop,
						&candidate->nexthop))
			return;
	wire->addpaths[wire->addpath_count++] = *candidate;
}

void eigrp_addpath_select(eigrp_instance_t *eigrp, eigrp_intf_t *ei,
			  eigrp_route_descriptor_t *wire)
{
	eigrp_route_descriptor_t *candidate, *primary;
	eigrp_topology_route_iterator_t iterator;
	eigrp_prefix_descriptor_t *prefix;
	eigrp_addpath_t addpath;
	uint8_t maximum;

	if (!wire)
		return;
	/* Wire snapshots must never re-encode inbound attributes by accident.
	 * QUERY, REPLY and poisoned UPDATEs leave this count at zero.
	 */
	wire->addpath_count = 0;
	if (!eigrp || !ei || !ei->add_paths || ei->next_hop_self
	    || wire->metric.delay == EIGRP_MAX_METRIC
	    || !(prefix = wire->prefix)
	    || prefix->state != EIGRP_FSM_STATE_PASSIVE)
		return;
	primary = eigrp_topology_route_read(prefix);
	if (!primary || primary->distance == EIGRP_MAX_METRIC)
		return;
	maximum = ei->add_paths < EIGRP_ADDPATH_MAX ? ei->add_paths
						    : EIGRP_ADDPATH_MAX;
	/* The base route is advertised first, and only equal-cost successors
	 * are eligible for extra AddPath attributes.  Exclude the base itself.
	 */
	for (candidate = eigrp_topology_route_iterator_first(prefix, &iterator);
	     candidate && wire->addpath_count < maximum;
	     candidate = eigrp_topology_route_iterator_next(&iterator)) {
		if (!(candidate->flags & EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG)
		    || candidate->distance != primary->distance)
			continue;
		if (candidate != primary) {
			memset(&addpath, 0, sizeof(addpath));
			addpath.nexthop = candidate->nexthop;
			addpath.router_id = candidate->extdata.orig;
			addpath.tag = candidate->extdata.tag;
			addpath.protocol = candidate->extdata.protocol;
			addpath.flags = candidate->extdata.flags;
			eigrp_addpath_nexthop_add(wire, &addpath, maximum);
		}
		/* Additional paths received from a hub share the primary
		 * metric. Preserve their next-hop identities when forwarding is
		 * enabled.
		 */
		for (uint8_t n = 0; n < candidate->addpath_count
				    && wire->addpath_count < maximum;
		     n++)
			eigrp_addpath_nexthop_add(wire, &candidate->addpaths[n],
						  maximum);
	}
}

void eigrp_addpath_refresh(eigrp_instance_t *eigrp)
{
	eigrp_table_node_t *node;
	eigrp_prefix_descriptor_t *prefix;

	if (!eigrp || !eigrp->topology_table || !eigrp->topology_changes)
		return;
	(void)eigrp_list_index_enable(eigrp->topology_changes);
	for (node = eigrp_table_first(eigrp->topology_table); node;
	     node = eigrp_table_next(node)) {
		prefix = node->info;
		if (!prefix || prefix->state != EIGRP_FSM_STATE_PASSIVE
		    || !eigrp_topology_route_read(prefix))
			continue;
		prefix->req_action |= EIGRP_FSM_NEED_UPDATE;
		if (!eigrp_list_lookup(eigrp->topology_changes, prefix))
			(void)eigrp_list_add(eigrp->topology_changes, prefix);
	}
	eigrp_update_send_all(eigrp, NULL);
}

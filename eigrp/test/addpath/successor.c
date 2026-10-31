/* SPDX-License-Identifier: ISC
 * Copyright (C) 2026 Donnie V. Savage
 * Exercise production successor-change detection without changing DUAL metrics.
 */
#include <assert.h>
#include <string.h>

/* The topology selector and node-flags implementation are deliberately used
 * directly, not reproduced in the fixture.
 */
#include "eigrp_topology.c"

/* Logging is not under test. Real topology functions remain linked. */
unsigned long term_debug_eigrp;
int eigrp_prefix_snprintf(char *buf, size_t len, const eigrp_prefix_t *prefix)
{
	(void)buf;
	(void)len;
	(void)prefix;
	return 0;
}
void eigrp_log(eigrp_log_level_t level, const char *format, ...)
{
	(void)level;
	(void)format;
}

int main(void)
{
	eigrp_instance_t eigrp = {0};
	eigrp_prefix_descriptor_t prefix = {0};
	eigrp_route_descriptor_t routes[3] = {{0}};
	uint32_t distance = 500;

	eigrp.max_paths = 4;
	eigrp.variance = 1;
	prefix.state = EIGRP_FSM_STATE_PASSIVE;
	prefix.distance = distance;
	prefix.fdistance = 600;
	prefix.internal_routes = eigrp_list_create();
	prefix.external_routes = eigrp_list_create();
	assert(prefix.internal_routes && prefix.external_routes);

	for (int i = 0; i < 3; ++i) {
		routes[i].type = EIGRP_TLV_IPv4_INT;
		routes[i].distance = i == 2 ? EIGRP_MAX_METRIC : distance;
		routes[i].reported_distance = i == 2 ? EIGRP_MAX_METRIC : 100;
		assert(eigrp_list_add(prefix.internal_routes, &routes[i]));
	}
	/* First evaluation elects two equal-cost successors. */
	assert(eigrp_topology_update_node_flags(&eigrp, &prefix));
	assert((routes[0].flags & EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG));
	assert((routes[1].flags & EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG));
	assert(!eigrp_topology_update_node_flags(&eigrp, &prefix));

	/* Withdraw only the second path. Distance and FD remain unchanged. */
	routes[1].distance = EIGRP_MAX_METRIC;
	routes[1].reported_distance = EIGRP_MAX_METRIC;
	assert(eigrp_topology_update_node_flags(&eigrp, &prefix));
	assert((routes[0].flags & EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG));
	assert(!(routes[1].flags & EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG));
	assert(prefix.distance == distance && prefix.fdistance == 600);
	assert(!eigrp_topology_update_node_flags(&eigrp, &prefix));

	/* Replace that path with an equal-cost alternative: the count remains
	 * two, but the advertised path identities change. */
	routes[2].distance = distance;
	routes[2].reported_distance = 100;
	assert(eigrp_topology_update_node_flags(&eigrp, &prefix));
	assert((routes[0].flags & EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG));
	assert((routes[2].flags & EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG));
	assert(!eigrp_topology_update_node_flags(&eigrp, &prefix));

	/* Withdraw the final successor and do not retain its flags. */
	routes[0].distance = EIGRP_MAX_METRIC;
	routes[0].reported_distance = EIGRP_MAX_METRIC;
	routes[2].distance = EIGRP_MAX_METRIC;
	routes[2].reported_distance = EIGRP_MAX_METRIC;
	assert(eigrp_topology_update_node_flags(&eigrp, &prefix));
	assert(!eigrp_topology_update_node_flags(&eigrp, &prefix));
	for (int i = 0; i < 3; ++i)
		assert(!(routes[i].flags
			 & EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG));

	eigrp_list_delete(&prefix.internal_routes);
	eigrp_list_delete(&prefix.external_routes);
	return 0;
}

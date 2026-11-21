// SPDX-License-Identifier: GPL-2.0-or-later
/* Runtime topology snapshot origin and vector regression.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <assert.h>
#include <arpa/inet.h>
#include <string.h>
#include <stdio.h>
#include <stdlib.h>
#include "eigrp.h"
#include "eigrp_mgnt.h"
#include "eigrp_structs.h"
#include "eigrp_neighbor.h"
#include "eigrp_topology.h"
#include "eigrp_table.h"
#include "eigrp_list.h"

struct observed {
	unsigned count;
	unsigned connected;
	unsigned redistributed;
	unsigned learned_external;
};

static eigrp_result_t prefix_read(const eigrp_topology_prefix_state_t *state,
				  void *arg)
{
	(void)arg;
	assert(!state->active);
	assert(state->successor_count == 3);
	return EIGRP_RESULT_SUCCESS;
}

static eigrp_result_t route_read(const eigrp_topology_route_state_t *state,
				 void *arg)
{
	struct observed *seen = arg;
	seen->count++;
	assert(state->distance == 25600);
	assert(state->reported_distance == 0);
	assert(state->successor);
	assert(state->total_metric.bandwidth == 12345);
	assert(state->reported_metric.delay == 67890);
	if (state->connected) {
		assert(!state->external && !state->redistributed);
		seen->connected++;
	} else if (state->redistributed) {
		assert(state->external);
		assert(state->next_hop.afi == 0);
		seen->redistributed++;
	} else {
		assert(state->external);
		assert(state->next_hop.afi != 0);
		seen->learned_external++;
	}
	return EIGRP_RESULT_SUCCESS;
}

static void check(eigrp_afi_t afi, uint16_t internal_type,
		  uint16_t external_type)
{
	eigrp_instance_t runtime = {0};
	eigrp_nbr_t self = {0}, neighbor = {0};
	eigrp_prefix_descriptor_t *prefix;
	eigrp_route_descriptor_t *route[3];
	struct observed seen = {0};
	const char *dest =
		afi == EIGRP_AFI_IPV4 ? "198.51.100.0" : "2001:db8:100::";
	const char *peer = afi == EIGRP_AFI_IPV4 ? "10.0.0.2" : "fe80::2";
	int family = afi == EIGRP_AFI_IPV4 ? AF_INET : AF_INET6;

	runtime.topology_table = eigrp_table_create();
	assert(runtime.topology_table);
	runtime.neighbor_self = &self;
	runtime.af_vectors.afi = afi;
	neighbor.src.afi = family;
	assert(inet_pton(family, peer,
			 family == AF_INET ? (void *)&neighbor.src.ip.v4
					   : (void *)&neighbor.src.ip.v6)
	       == 1);
	prefix = eigrp_topo_pfx_create();
	assert(prefix);
	prefix->destination.address.afi = afi;
	prefix->destination.prefix_length = afi == EIGRP_AFI_IPV4 ? 24 : 64;
	assert(inet_pton(family, dest, prefix->destination.address.bytes) == 1);
	prefix->fdistance = 25600;
	for (unsigned i = 0; i < 3; i++) {
		route[i] = eigrp_topo_route_create(NULL);
		assert(route[i]);
		route[i]->type = i == 0 ? internal_type : external_type;
		route[i]->adv_router = i == 2 ? &neighbor : &self;
		route[i]->flags = EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG;
		route[i]->distance = 25600;
		route[i]->reported_distance = 0;
		route[i]->total_metric.bandwidth = 12345;
		route[i]->reported_metric.delay = 67890;
		assert(eigrp_list_add(i == 0 ? prefix->internal_routes
					     : prefix->external_routes,
				      route[i]));
	}
	eigrp_topo_pfx_add(runtime.topology_table, prefix);
	assert(eigrp_topo_pfx_routes_iterate(NULL, &runtime, NULL, true,
					     prefix_read, route_read, &seen)
	       == EIGRP_RESULT_SUCCESS);
	assert(seen.count == 3 && seen.connected == 1 && seen.redistributed == 1
	       && seen.learned_external == 1);
	for (unsigned i = 0; i < 3; i++)
		free(route[i]);
	/* The descriptor lists have no element destructor in this fixture. */
	eigrp_list_delete(&prefix->internal_routes);
	eigrp_list_delete(&prefix->external_routes);
	eigrp_list_delete(&prefix->rij);
	free(prefix);
	eigrp_table_free(runtime.topology_table);
}

int main(void)
{
	check(EIGRP_AFI_IPV4, EIGRP_TLV_IPv4_INT, EIGRP_TLV_IPv4_EXT);
	check(EIGRP_AFI_IPV6, EIGRP_TLV_IPv6_INT, EIGRP_TLV_MP_EXT);
	puts("topology snapshot origin IPv4/IPv6: PASS");
	return 0;
}

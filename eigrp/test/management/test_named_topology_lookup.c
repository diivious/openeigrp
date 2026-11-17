// SPDX-License-Identifier: GPL-2.0-or-later
/* Exact selected-prefix topology snapshots for FRR named EIGRP output.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <assert.h>
#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "eigrp.h"
#include "eigrp_mgnt.h"
#include "eigrp_structs.h"
#include "eigrp_topology.h"
#include "eigrp_table.h"
#include "eigrp_list.h"

struct observed {
	unsigned prefixes;
	unsigned routes;
	bool inaccessible;
};

static eigrp_result_t prefix_observe(const eigrp_topology_prefix_state_t *state,
				     void *arg)
{
	struct observed *seen = arg;
	assert(state->destination.prefix_length <= 128);
	seen->prefixes++;
	return EIGRP_RESULT_SUCCESS;
}

static eigrp_result_t route_observe(const eigrp_topology_route_state_t *state,
				    void *arg)
{
	struct observed *seen = arg;
	seen->routes++;
	seen->inaccessible = state->reported_distance == EIGRP_MAX_METRIC;
	return EIGRP_RESULT_SUCCESS;
}

static eigrp_prefix_t prefix_make(eigrp_afi_t afi, const char *address,
				  uint8_t length)
{
	eigrp_prefix_t prefix = {0};
	prefix.address.afi = afi;
	prefix.prefix_length = length;
	assert(inet_pton(afi == EIGRP_AFI_IPV4 ? AF_INET : AF_INET6, address,
			 prefix.address.bytes)
	       == 1);
	return prefix;
}

static eigrp_prefix_descriptor_t *prefix_add(eigrp_instance_t *runtime,
					     eigrp_prefix_t destination)
{
	eigrp_prefix_descriptor_t *desc = eigrp_topology_prefix_create();
	assert(desc);
	desc->destination = destination;
	eigrp_prefix_descriptor_add(runtime->topology_table, desc);
	return desc;
}

static void check(eigrp_afi_t afi, const char *cover, uint8_t cover_len,
		  const char *network, uint8_t network_len, const char *inside,
		  const char *outside)
{
	eigrp_instance_t runtime = {0};
	eigrp_prefix_descriptor_t *wide, *narrow;
	eigrp_route_descriptor_t *withdrawn;
	struct observed seen = {0};
	eigrp_prefix_t request;

	runtime.af_vectors.afi = afi;
	runtime.topology_table = eigrp_table_create();
	assert(runtime.topology_table);
	wide = prefix_add(&runtime, prefix_make(afi, cover, cover_len));
	narrow = prefix_add(&runtime, prefix_make(afi, network, network_len));

	/* A covering route is NOT a valid answer to a selected-prefix query. */
	request = prefix_make(afi, inside, afi == EIGRP_AFI_IPV4 ? 32 : 128);
	assert(eigrp_topology_state_iterate(NULL, &runtime, &request, true,
					    prefix_observe, route_observe,
					    &seen)
	       == EIGRP_RESULT_NOT_FOUND);
	assert(seen.prefixes == 0 && seen.routes == 0);

	/* Prefix addresses with host bits are normalized for exact matching. */
	request = prefix_make(afi, inside, network_len);
	assert(eigrp_topology_state_iterate(NULL, &runtime, &request, true,
					    prefix_observe, route_observe,
					    &seen)
	       == EIGRP_RESULT_SUCCESS);
	assert(seen.prefixes == 1 && seen.routes == 0);

	/* An unrelated address is never displayed as the covering route. */
	request = prefix_make(afi, outside, afi == EIGRP_AFI_IPV4 ? 32 : 128);
	assert(eigrp_topology_state_iterate(NULL, &runtime, &request, true,
					    prefix_observe, route_observe,
					    &seen)
	       == EIGRP_RESULT_NOT_FOUND);

	/* A retained inaccessible path exists in detail/all-links but not in
	 * the ordinary successor/feasible-successor listing. */
	withdrawn = eigrp_topology_route_create(NULL);
	assert(withdrawn);
	withdrawn->reported_distance = EIGRP_MAX_METRIC;
	withdrawn->distance = EIGRP_MAX_METRIC;
	withdrawn->flags = EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG;
	assert(eigrp_list_add(narrow->internal_routes, withdrawn));
	request = prefix_make(afi, network, network_len);
	seen = (struct observed){0};
	assert(eigrp_topology_state_iterate(NULL, &runtime, &request, false,
					    prefix_observe, route_observe,
					    &seen)
	       == EIGRP_RESULT_SUCCESS);
	assert(seen.prefixes == 1 && seen.routes == 0);
	seen = (struct observed){0};
	assert(eigrp_topology_state_iterate(NULL, &runtime, &request, true,
					    prefix_observe, route_observe,
					    &seen)
	       == EIGRP_RESULT_SUCCESS);
	assert(seen.prefixes == 1 && seen.routes == 1 && seen.inaccessible);

	free(withdrawn);
	eigrp_list_delete(&wide->internal_routes);
	eigrp_list_delete(&wide->external_routes);
	eigrp_list_delete(&wide->rij);
	eigrp_list_delete(&narrow->internal_routes);
	eigrp_list_delete(&narrow->external_routes);
	eigrp_list_delete(&narrow->rij);
	free(wide);
	free(narrow);
	eigrp_table_free(runtime.topology_table);
}

int main(void)
{
	check(EIGRP_AFI_IPV4, "198.51.0.0", 16, "198.51.100.0", 24,
	      "198.51.100.73", "203.0.113.73");
	check(EIGRP_AFI_IPV6, "2001:db8::", 32, "2001:db8:100::", 64,
	      "2001:db8:100::73", "2001:db8:ffff::73");
	puts("named topology exact lookup and withdrawn path IPv4/IPv6: PASS");
	return 0;
}

// SPDX-License-Identifier: ISC
/*
 * Base topology runtime admission regression.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <assert.h>
#include <stdint.h>

#include "eigrp_structs.h"
#include "eigrp_topology.h"

int main(void)
{
	eigrp_route_descriptor_t route = {0};

	/* Classic routes have implicit base topology identity. */
	route.type = EIGRP_TLV_IPv4_INT;
	assert(eigrp_topo_route_supported(&route));
	route.type = EIGRP_TLV_IPv6_EXT;
	assert(eigrp_topo_route_supported(&route));

	/* MP route identities are valid in the decoder, but only the base
	 * identity has an owning runtime table today. */
	route.type = EIGRP_TLV_MP_INT;
	route.topology_id = 1;
	assert(!eigrp_topo_route_supported(&route));
	route.topology_id = 7;
	assert(!eigrp_topo_route_supported(&route));
	route.type = EIGRP_TLV_MP_EXT;
	route.topology_id = UINT16_MAX;
	assert(!eigrp_topo_route_supported(&route));
	route.topology_id = EIGRP_TOPOLOGY_ID_BASE;
	assert(eigrp_topo_route_supported(&route));
	assert(!eigrp_topo_route_supported(NULL));
	return 0;
}

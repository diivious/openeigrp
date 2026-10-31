/* SPDX-License-Identifier: ISC
 * Copyright (C) 2026 Donnie V. Savage
 * Exercise the production AF-neutral RIB next-hop conversion.
 */
#include <assert.h>
#include <arpa/inet.h>
#include <string.h>

/* The helper is private to topology.c. Include the implementation so this
 * executable exercises production code rather than duplicating it.
 */
#include "eigrp_topology.c"

int main(void)
{
	eigrp_instance_t eigrp = {0};
	eigrp_addr_t addr = {0};
	eigrp_rib_nexthop_t nh = {0};
	uint8_t expected[16] = {0};

	eigrp.af_vectors.afi = EIGRP_AFI_IPV4;
	eigrp.af_vectors.packet_address_afi = AF_INET;
	eigrp.af_vectors.packet_address_bytes = 4;
	addr.afi = AF_INET;
	assert(inet_pton(AF_INET, "192.0.2.9", &addr.ip) == 1);
	assert(eigrp_topology_nexthop_addr_set(&eigrp, &addr, &nh));
	assert(nh.gateway_present && nh.gateway.afi == EIGRP_AFI_IPV4);
	assert(memcmp(nh.gateway.bytes, &addr.ip, 4) == 0);
	memcpy(expected, &addr.ip, 4);
	assert(memcmp(nh.gateway.bytes, expected, sizeof(expected)) == 0);

	/* Reject a missing/mismatched/unspecified address, not a partial
	 * gateway. */
	memset(&nh, 0, sizeof(nh));
	addr.afi = AF_INET6;
	assert(!eigrp_topology_nexthop_addr_set(&eigrp, &addr, &nh));
	assert(!nh.gateway_present);
	memset(&addr, 0, sizeof(addr));
	addr.afi = AF_INET;
	assert(!eigrp_topology_nexthop_addr_set(&eigrp, &addr, &nh));
	assert(!nh.gateway_present);

	eigrp.af_vectors.afi = EIGRP_AFI_IPV6;
	eigrp.af_vectors.packet_address_afi = AF_INET6;
	eigrp.af_vectors.packet_address_bytes = 16;
	addr.afi = AF_INET6;
	assert(inet_pton(AF_INET6, "fe80::1234", &addr.ip) == 1);
	assert(eigrp_topology_nexthop_addr_set(&eigrp, &addr, &nh));
	assert(nh.gateway_present && nh.gateway.afi == EIGRP_AFI_IPV6);
	assert(memcmp(nh.gateway.bytes, &addr.ip, 16) == 0);
	addr.afi = AF_INET;
	assert(!eigrp_topology_nexthop_addr_set(&eigrp, &addr, &nh));

	addr.afi = AF_INET6;
	eigrp.af_vectors.packet_address_bytes = 0;
	assert(!eigrp_topology_nexthop_addr_set(&eigrp, &addr, &nh));
	eigrp.af_vectors.packet_address_bytes = 17;
	assert(!eigrp_topology_nexthop_addr_set(&eigrp, &addr, &nh));
	return 0;
}

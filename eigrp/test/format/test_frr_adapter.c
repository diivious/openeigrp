// SPDX-License-Identifier: GPL-2.0-or-later
/* Executable FRR host interface adaptation contract. */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <zebra.h>

#include "eigrp_frr.h"
#include "eigrp_const.h"

int main(void)
{
	struct interface host = {0};
	struct prefix address = {0};
	eigrp_intf_runtime_state_t state;
	eigrp_result_t result;

	_Static_assert(sizeof(host.name) >= sizeof("eth12"),
		       "FRR interface name buffer too small");
	memcpy(host.name, "eth12", sizeof("eth12"));
	host.ifindex = 12;
	host.bandwidth = 100000;
	host.mtu = 1500;
	address.family = AF_INET6;
	address.prefixlen = 64;
	address.u.prefix6.s6_addr[0] = 0x20;
	address.u.prefix6.s6_addr[1] = 0x01;
	assert(eigrp_frr_intf_type(NULL) == EIGRP_IFTYPE_BROADCAST);
	assert(eigrp_frr_intf_type(&host) == EIGRP_IFTYPE_BROADCAST);
	result = eigrp_frr_intf_state_import(&host, &address, true, &state);
	assert(result == EIGRP_RESULT_SUCCESS);
	assert(state.interface_name == host.name);
	assert(state.ifindex == 12);
	assert(state.address.address.afi == EIGRP_AFI_IPV6);
	assert(state.address.prefix_length == 64);
	assert(state.address.address.bytes[0] == 0x20);
	assert(state.secondary);
	assert(state.operative);
	assert(state.bandwidth == 100000);
	assert(state.mtu == 1500);
	result = eigrp_frr_intf_state_import(NULL, &address, false, &state);
	assert(result == EIGRP_RESULT_INVALID_ARGUMENT);
	result = eigrp_frr_intf_state_import(&host, NULL, false, &state);
	assert(result == EIGRP_RESULT_INVALID_ARGUMENT);
	result = eigrp_frr_intf_state_import(&host, &address, false, NULL);
	assert(result == EIGRP_RESULT_INVALID_ARGUMENT);
	address.prefixlen = 129;
	result = eigrp_frr_intf_state_import(&host, &address, false, &state);
	assert(result == EIGRP_RESULT_INVALID_ARGUMENT);
	puts("FRR interface adapter: PASS");
	return 0;
}

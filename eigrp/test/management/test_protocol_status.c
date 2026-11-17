// SPDX-License-Identifier: GPL-2.0-or-later
/* Live named-AF protocol snapshot and shutdown regression.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include "eigrp.h"
#include "eigrp_instance.h"
#include "eigrp_metric.h"
#include "eigrp_mgnt.h"
#include "eigrp_sys.h"
#include "eigrp_structs.h"
#include "eigrp_neighbor.h"
#include "eigrp_interface.h"
#include "eigrp_list.h"

struct observed {
	unsigned ipv4;
	unsigned ipv6;
	bool active;
	bool modified;
	bool parent_shutdown;
};

static eigrp_result_t state_check(const eigrp_status_protocol_state_t *state,
				  void *arg)
{
	struct observed *seen = arg;
	assert(!strcmp(state->instance_name, "showcase"));
	assert(!strcmp(state->vrf_name, "default"));
	assert(state->asn == (state->afi == EIGRP_AFI_IPV4 ? 4453 : 6473));
	if (state->afi == EIGRP_AFI_IPV4)
		seen->ipv4++;
	else if (state->afi == EIGRP_AFI_IPV6)
		seen->ipv6++;
	else
		assert(0);
	assert(state->parent_shutdown == seen->parent_shutdown);
	if (!seen->active) {
		assert(state->shutdown == !seen->parent_shutdown);
		assert(!state->runtime_present);
		return EIGRP_RESULT_SUCCESS;
	}
	assert(state->runtime_present && !state->shutdown);
	assert(state->k_values[0] == 1 && state->k_values[2] == 1);
	assert(state->max_paths == 4 && state->max_hops == 100);
	assert(state->active_time_seconds == 180);
	if (state->afi == EIGRP_AFI_IPV4 && seen->modified) {
		assert(state->variance == 7);
		assert(state->distance_internal == 91);
		assert(state->distance_external == 171);
	} else {
		assert(state->variance == 1);
		assert(state->distance_internal == 90);
		assert(state->distance_external == 170);
	}
	return EIGRP_RESULT_SUCCESS;
}

static void check(bool active, bool modified, bool parent_shutdown)
{
	struct observed seen = {
		.active = active,
		.modified = modified,
		.parent_shutdown = parent_shutdown,
	};
	assert(eigrp_status_protocol_iterate(state_check, &seen)
	       == EIGRP_RESULT_SUCCESS);
	assert(seen.ipv4 == 1 && seen.ipv6 == 1);
}

struct interface_seen {
	uint32_t value;
	bool valid;
};

static eigrp_result_t interface_check(const eigrp_intf_state_t *state,
				      void *arg)
{
	struct interface_seen *expected = arg;
	assert(!strcmp(state->interface_name, "test0"));
	assert(state->runtime_present);
	assert(state->peer_count == 4);
	assert(state->up_peer_count == 3);
	assert(state->mean_srtt_valid == expected->valid);
	if (expected->valid)
		assert(state->mean_srtt_msec == expected->value);
	return EIGRP_RESULT_SUCCESS;
}

static void interface_srtt_check(void)
{
	eigrp_instance_t runtime = {0};
	eigrp_intf_t interface = {0};
	eigrp_nbr_t neighbor[4] = {{0}};
	struct interface_seen expected = {.valid = true, .value = 20};

	runtime.eiflist = eigrp_list_create();
	interface.name = "test0";
	interface.eigrp = &runtime;
	interface.nbrs = eigrp_list_create();
	assert(runtime.eiflist && interface.nbrs);
	assert(eigrp_list_add(runtime.eiflist, &interface));
	for (unsigned i = 0; i < 4; i++) {
		neighbor[i].ei = &interface;
		neighbor[i].state =
			i == 2 ? EIGRP_NEIGHBOR_DOWN : EIGRP_NEIGHBOR_UP;
		neighbor[i].srtt_valid = i != 3;
		neighbor[i].srtt_msec = (i == 0 ? 10 : i == 1 ? 30 : 100);
		assert(eigrp_list_add(interface.nbrs, &neighbor[i]));
	}
	assert(eigrp_intf_state_iterate(NULL, &runtime, NULL, interface_check,
					&expected)
	       == EIGRP_RESULT_SUCCESS);
	neighbor[0].srtt_valid = false;
	neighbor[1].srtt_valid = false;
	expected.valid = false;
	assert(eigrp_intf_state_iterate(NULL, &runtime, NULL, interface_check,
					&expected)
	       == EIGRP_RESULT_SUCCESS);
	eigrp_list_delete(&interface.nbrs);
	eigrp_list_delete(&runtime.eiflist);
}

int main(void)
{
	eigrp_af_config_t *ipv4;
	eigrp_af_config_t *ipv6;
	eigrp_named_config_t *parent;
	eigrp_instance_context_t ctx = {0};

	eigrp_sys_runtime_init();
	eigrp_init();
	assert(eigrp_af_config_create("showcase", EIGRP_AFI_IPV4, "default",
				      4453)
	       == EIGRP_RESULT_SUCCESS);
	assert(eigrp_af_config_create("showcase", EIGRP_AFI_IPV6, "default",
				      6473)
	       == EIGRP_RESULT_SUCCESS);
	ipv4 = eigrp_af_config_read("showcase", EIGRP_AFI_IPV4, "default",
				    4453);
	ipv6 = eigrp_af_config_read("showcase", EIGRP_AFI_IPV6, "default",
				    6473);
	assert(ipv4 && ipv6);
	parent = eigrp_named_config_read("showcase");
	assert(parent);
	check(true, false, false);
	ctx.config = ipv4;
	ctx.runtime = ipv4->runtime;
	ctx.topology_id = EIGRP_TOPOLOGY_ID_BASE;
	assert(eigrp_metric_variance_update(EIGRP_SET, &ctx, 7)
	       == EIGRP_RESULT_SUCCESS);
	assert(eigrp_af_config_distance_update(EIGRP_SET, ipv4, 91, 171)
	       == EIGRP_RESULT_SUCCESS);
	check(true, true, false);
	assert(eigrp_af_config_shutdown_update(EIGRP_SET, ipv4)
	       == EIGRP_RESULT_SUCCESS);
	assert(eigrp_af_config_shutdown_update(EIGRP_SET, ipv6)
	       == EIGRP_RESULT_SUCCESS);
	check(false, true, false);
	assert(eigrp_af_config_shutdown_update(EIGRP_RESET, ipv4)
	       == EIGRP_RESULT_SUCCESS);
	assert(eigrp_af_config_shutdown_update(EIGRP_RESET, ipv6)
	       == EIGRP_RESULT_SUCCESS);
	check(true, true, false);
	assert(eigrp_named_config_shutdown_update(EIGRP_SET, parent)
	       == EIGRP_RESULT_SUCCESS);
	check(false, true, true);
	assert(eigrp_named_config_shutdown_update(EIGRP_RESET, parent)
	       == EIGRP_RESULT_SUCCESS);
	check(true, true, false);
	eigrp_terminate();
	interface_srtt_check();
	puts("named protocol state and interface SRTT: PASS");
	return 0;
}

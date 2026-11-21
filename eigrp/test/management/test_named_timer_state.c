// SPDX-License-Identifier: GPL-2.0-or-later
/* Named show timer state export regression.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <assert.h>
#include <arpa/inet.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "eigrp.h"
#include "eigrp_const.h"
#include "eigrp_interface.h"
#include "eigrp_list.h"
#include "eigrp_mgnt.h"
#include "eigrp_neighbor.h"
#include "eigrp_structs.h"
#include "eigrp_fsm.h"
#include "eigrp_sys.h"
#include "eigrp_table.h"
#include "eigrp_timer.h"
#include "eigrp_topology.h"

struct observed {
	eigrp_afi_t afi;
	unsigned count;
	bool active;
	bool expired;
};

static eigrp_result_t timer_check(const eigrp_timer_state_t *state, void *arg)
{
	static const eigrp_timer_state_type_t order[] = {
		EIGRP_TIMER_STATE_HELLO,       EIGRP_TIMER_STATE_PEER_HOLD,
		EIGRP_TIMER_STATE_ACTIVE,      EIGRP_TIMER_STATE_LIMIT_RESTART,
		EIGRP_TIMER_STATE_LIMIT_RESET,
	};
	struct observed *seen = arg;

	assert(seen->count < sizeof(order) / sizeof(order[0]));
	assert(state->type
	       == order[seen->count
			+ ((seen->count >= 2 && !seen->active) ? 1U : 0U)]);
	assert(state->expiration_seconds
	       == (state->expiration_msec + 999U) / 1000U);
	if (state->type != EIGRP_TIMER_STATE_ACTIVE || !seen->expired)
		assert(state->expiration_msec > 500);
	if (state->type == EIGRP_TIMER_STATE_ACTIVE) {
		assert(state->destination.address.afi == seen->afi);
		assert(state->destination.prefix_length
		       == (seen->afi == EIGRP_AFI_IPV4 ? 24 : 64));
		assert(!state->neighbor_present);
		if (seen->expired)
			assert(state->expiration_msec == 0);
	} else if (state->type == EIGRP_TIMER_STATE_HELLO) {
		assert(!strcmp(state->interface_name, "test0"));
		assert(!state->neighbor_present);
	} else {
		assert(state->neighbor_present);
		assert(state->neighbor_address.afi == seen->afi);
	}
	seen->count++;
	return EIGRP_RESULT_SUCCESS;
}

static void check(eigrp_afi_t afi)
{
	eigrp_instance_t runtime = {0};
	eigrp_instance_context_t context = {.runtime = &runtime};
	eigrp_intf_t intf = {0};
	eigrp_nbr_t nbr = {0};
	eigrp_prefix_descriptor_t *prefix;
	eigrp_timer_event_t hello = {0}, hold = {0}, restart = {0};
	eigrp_timer_event_t reset = {0}, active = {0};
	struct observed seen = {.afi = afi, .active = true};
	uint64_t now = eigrp_sys_monotime_msec();
	const char *dest =
		afi == EIGRP_AFI_IPV4 ? "198.51.100.0" : "2001:db8:100::";
	const char *peer = afi == EIGRP_AFI_IPV4 ? "10.0.0.2" : "fe80::2";
	int family = afi == EIGRP_AFI_IPV4 ? AF_INET : AF_INET6;

	assert(pthread_mutex_init(&runtime.work_lock, NULL) == 0);
	runtime.eiflist = eigrp_list_create();
	runtime.topology_table = eigrp_table_create();
	assert(runtime.eiflist && runtime.topology_table);
	intf.eigrp = &runtime;
	intf.name = "test0";
	intf.nbrs = eigrp_list_create();
	assert(intf.nbrs);
	assert(eigrp_list_add(runtime.eiflist, &intf));
	assert(eigrp_list_add(intf.nbrs, &nbr));
	nbr.ei = &intf;
	nbr.state = EIGRP_NEIGHBOR_UP;
	nbr.src.afi = family;
	assert(inet_pton(family, peer,
			 family == AF_INET ? (void *)&nbr.src.ip.v4
					   : (void *)&nbr.src.ip.v6)
	       == 1);
	hello.due_msec = now + 2500;
	hold.due_msec = now + 3500;
	restart.due_msec = now + 4500;
	reset.due_msec = now + 5500;
	active.due_msec = now + 6500;
	intf.t_hello = &hello;
	nbr.t_holddown = &hold;
	nbr.t_limit_restart = &restart;
	nbr.t_limit_reset = &reset;
	nbr.limit_restart_due_msec = restart.due_msec;
	nbr.limit_reset_due_msec = reset.due_msec;
	prefix = eigrp_topo_pfx_create();
	assert(prefix);
	prefix->destination.address.afi = afi;
	prefix->destination.prefix_length = afi == EIGRP_AFI_IPV4 ? 24 : 64;
	assert(inet_pton(family, dest, prefix->destination.address.bytes) == 1);
	prefix->state = EIGRP_FSM_STATE_ACTIVE_1;
	prefix->t_active = &active;
	eigrp_topo_pfx_add(runtime.topology_table, prefix);

	assert(eigrp_timer_state_iterate(&context, timer_check, &seen)
	       == EIGRP_RESULT_SUCCESS);
	assert(seen.count == 5);
	seen.count = 0;
	seen.active = false;
	prefix->t_active = NULL;
	assert(eigrp_timer_state_iterate(&context, timer_check, &seen)
	       == EIGRP_RESULT_SUCCESS);
	assert(seen.count == 4);
	prefix->t_active = &active;
	prefix->state = EIGRP_FSM_STATE_PASSIVE;
	seen.count = 0;
	assert(eigrp_timer_state_iterate(&context, timer_check, &seen)
	       == EIGRP_RESULT_SUCCESS);
	assert(seen.count == 4);
	prefix->state = EIGRP_FSM_STATE_ACTIVE_1;
	active.due_msec = now - 1;
	seen.active = true;
	seen.expired = true;
	seen.count = 0;
	assert(eigrp_timer_state_iterate(&context, timer_check, &seen)
	       == EIGRP_RESULT_SUCCESS);
	assert(seen.count == 5);

	eigrp_list_delete(&prefix->internal_routes);
	eigrp_list_delete(&prefix->external_routes);
	eigrp_list_delete(&prefix->rij);
	free(prefix);
	eigrp_table_free(runtime.topology_table);
	eigrp_list_delete(&intf.nbrs);
	eigrp_list_delete(&runtime.eiflist);
	assert(pthread_mutex_destroy(&runtime.work_lock) == 0);
}

int main(void)
{
	check(EIGRP_AFI_IPV4);
	check(EIGRP_AFI_IPV6);
	puts("named timer export IPv4/IPv6: PASS");
	return 0;
}

// SPDX-License-Identifier: GPL-2.0-or-later
/* Interface retirement cancels interface-owned callbacks and output.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <assert.h>
#include <arpa/inet.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "eigrp.h"
#include "eigrp_auth.h"
#include "eigrp_filter.h"
#include "eigrp_interface.h"
#include "eigrp_list.h"
#include "eigrp_neighbor.h"
#include "eigrp_packet.h"
#include "eigrp_packetizer.h"
#include "eigrp_structs.h"
#include "eigrp_table.h"
#include "eigrp_timer.h"
#include "eigrp_topology.h"

#if defined(EIGRP_TEST_WRAP_TOPOLOGY)
static unsigned queued_on_down;

/* Model output queued by synchronous DUAL processing during neighbor loss.
 * This runs at the real connected-route teardown point, after the first
 * interface write-queue purge. */
void __real_eigrp_topology_connected_interface_down(eigrp_instance_t *,
						    eigrp_intf_t *);
void __wrap_eigrp_topology_connected_interface_down(eigrp_instance_t *eigrp,
						    eigrp_intf_t *ei)
{
	eigrp_packet_t *packet;

	__real_eigrp_topology_connected_interface_down(eigrp, ei);
	if (!ei->obuf)
		return;
	packet = eigrp_packet_create(64, NULL);
	assert(packet);
	eigrp_packet_enqueue(ei->obuf, packet);
	assert(eigrp_list_add(eigrp->oi_write_q, ei));
	ei->on_write_q = 1;
	queued_on_down++;
}
#endif

static void check(bool link_down_first, eigrp_intf_remove_reason_t reason)
{
	eigrp_instance_t eigrp = {0};
	eigrp_intf_runtime_state_t state = {0};
	eigrp_intf_config_t auth = {
		.authentication_mode_configured = true,
		.authentication_mode = EIGRP_AUTHENTICATION_HMAC_SHA256,
		.authentication_password = "retire-me",
	};
	eigrp_intf_t *ei;
	eigrp_timer_event_t *timer;
	eigrp_packetizer_work_t *broadcast, *peer_work, *work;
	eigrp_nbr_t *nbr;
	unsigned count = 0;

	assert(pthread_mutex_init(&eigrp.peer_lock, NULL) == 0);
	assert(pthread_mutex_init(&eigrp.work_lock, NULL) == 0);
	assert(pthread_cond_init(&eigrp.work_cond, NULL) == 0);
	eigrp.eiflist = eigrp_list_create();
	eigrp.oi_write_q = eigrp_list_create();
	eigrp.topology_table = eigrp_table_create();
	assert(eigrp.eiflist && eigrp.oi_write_q && eigrp.topology_table);
	eigrp.thread_running = true;

	state.interface_name = "eth0";
	state.ifindex = 2;
	state.address.address.afi = EIGRP_AFI_IPV4;
	state.address.prefix_length = 24;
	assert(inet_pton(AF_INET, "10.0.0.1", state.address.address.bytes)
	       == 1);
	ei = eigrp_intf_runtime_create(&eigrp, &state);
	assert(ei);
	assert(eigrp_auth_runtime_apply(ei, &auth, "retire-chain")
	       == EIGRP_RESULT_SUCCESS);
	assert(ei->params.auth_password && ei->params.auth_keychain);
	ei->obuf = eigrp_packet_queue_create();
	assert(ei->obuf);

	eigrp_timer_add(&eigrp, &ei->t_distribute, eigrp_distribute_intf_timer,
			ei, 10000U);
	timer = ei->t_distribute;
	assert(timer && eigrp.timer_head == timer && timer->arg == ei);

	/* Keep AF-wide advertisements but discard work for the retiring peer.
	 */
	broadcast = eigrp_packetizer_work_create(EIGRP_OPC_UPDATE);
	assert(broadcast);
	broadcast->exception = ei;
	eigrp_packetizer_enqueue(&eigrp, broadcast);
	nbr = calloc(1, sizeof(*nbr));
	assert(nbr);
	nbr->ei = ei;
	nbr->state = EIGRP_NEIGHBOR_DOWN;
	nbr->published = true;
	nbr->retrans_queue = eigrp_packet_queue_create();
	assert(nbr->retrans_queue && eigrp_list_add(ei->nbrs, nbr));
	peer_work = eigrp_packetizer_work_create(EIGRP_OPC_REPLY);
	assert(peer_work);
	peer_work->nbr = nbr;
	eigrp_packetizer_enqueue(&eigrp, peer_work);
	assert(eigrp.packetizer_count == 2);

	if (link_down_first) {
		assert(eigrp_intf_down(ei) == 1);
		/* Link-down must preserve the live interface's policy timer. */
		assert(ei->t_distribute == timer);
		assert(eigrp.oi_write_q->count == 0);
	}

	eigrp_intf_free(&eigrp, ei, reason);
	assert(eigrp.packetizer_head == broadcast);
	assert(eigrp.packetizer_tail);
	for (work = eigrp.packetizer_head; work; work = work->next) {
		assert(work->nbr == NULL);
		assert(work->exception == NULL);
		count++;
	}
	assert(count == eigrp.packetizer_count);
	/* No queued timer or output may retain the freed interface. */
	assert(eigrp.timer_head == NULL);
	assert(eigrp.eiflist->count == 0);
	assert(eigrp.oi_write_q->count == 0);

	eigrp_packetizer_delete(&eigrp);
	eigrp_table_free(eigrp.topology_table);
	eigrp_list_delete(&eigrp.oi_write_q);
	eigrp_list_delete(&eigrp.eiflist);
	assert(pthread_cond_destroy(&eigrp.work_cond) == 0);
	assert(pthread_mutex_destroy(&eigrp.work_lock) == 0);
	assert(pthread_mutex_destroy(&eigrp.peer_lock) == 0);
}

int main(void)
{
	check(false, EIGRP_INTERFACE_REMOVE_HOST);
	check(true, EIGRP_INTERFACE_REMOVE_FINAL);
#if defined(EIGRP_TEST_WRAP_TOPOLOGY)
	assert(queued_on_down == 3);
#endif
	puts("EIGRP interface timer and output retirement passed");
	return 0;
}

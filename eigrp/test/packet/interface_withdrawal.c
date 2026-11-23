// SPDX-License-Identifier: GPL-2.0-or-later
/* Connected route withdrawal must survive interface retirement.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <assert.h>
#include <arpa/inet.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>

#include "eigrp.h"
#include "eigrp_interface.h"
#include "eigrp_list.h"
#include "eigrp_neighbor.h"
#include "eigrp_packet.h"
#include "eigrp_packetizer.h"
#include "eigrp_structs.h"
#include "eigrp_table.h"
#include "eigrp_topology.h"

static eigrp_prefix_t target;

int main(void)
{
	eigrp_instance_t eigrp = {0};
	eigrp_intf_runtime_state_t state = {0};
	eigrp_intf_t *ei;
	eigrp_prefix_descriptor_t *prefix;
	eigrp_route_descriptor_t *route;
	eigrp_packetizer_work_t *work;
	unsigned count = 0;

	assert(pthread_mutex_init(&eigrp.peer_lock, NULL) == 0);
	assert(pthread_mutex_init(&eigrp.work_lock, NULL) == 0);
	assert(pthread_cond_init(&eigrp.work_cond, NULL) == 0);
	eigrp.eiflist = eigrp_list_create();
	eigrp.oi_write_q = eigrp_list_create();
	eigrp.topology_changes = eigrp_list_create();
	eigrp.topology_table = eigrp_table_create();
	eigrp.neighbor_self = calloc(1, sizeof(*eigrp.neighbor_self));
	assert(eigrp.eiflist && eigrp.oi_write_q && eigrp.topology_changes);
	assert(eigrp.topology_table && eigrp.neighbor_self);
	eigrp.thread_running = true;
	eigrp.af_vectors.afi = EIGRP_AFI_IPV4;
	eigrp.k_values[0] = eigrp.k_values[2] = 1;

	state.interface_name = "md5rt0";
	state.ifindex = 5;
	state.address.address.afi = EIGRP_AFI_IPV4;
	state.address.prefix_length = 32;
	assert(inet_pton(AF_INET, "198.18.253.1", state.address.address.bytes)
	       == 1);
	ei = eigrp_intf_runtime_create(&eigrp, &state);
	assert(ei);
	ei->obuf = eigrp_packet_queue_create();
	assert(ei->obuf);
	target = state.address;

	prefix = eigrp_topo_pfx_create();
	route = eigrp_topo_route_create(ei);
	assert(prefix && route);
	prefix->destination = target;
	prefix->nt = EIGRP_TOPOLOGY_TYPE_CONNECTED;
	prefix->state = EIGRP_FSM_STATE_PASSIVE;
	prefix->distance = prefix->fdistance = prefix->rdistance = 100;
	route->adv_router = eigrp.neighbor_self;
	route->type = EIGRP_TLV_IPv4_INT;
	route->distance = 100;
	route->metric.bandwidth = 10;
	route->metric.delay = 10;
	route->reported_metric = route->total_metric = route->metric;
	route->flags = EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG;
	prefix->reported_metric = route->metric;
	assert(eigrp_topo_pfx_add(eigrp.topology_table, prefix));
	assert(eigrp_topo_route_add(&eigrp, prefix, route));

	eigrp_intf_free(&eigrp, ei, EIGRP_INTERFACE_REMOVE_HOST);
	/* DUAL must retain the poisoned destination until advertisement. */
	prefix = eigrp_topo_table_lookup(eigrp.topology_table, &target);
	assert(prefix);
	route = eigrp_topo_route_lookup(prefix, eigrp.neighbor_self);
	assert(route && route->ei == NULL);
	assert(route->distance == EIGRP_MAX_METRIC);
	assert(prefix->req_action & EIGRP_FSM_NEED_UPDATE);
	assert(eigrp.packetizer_count > 0);

	while ((work = eigrp.packetizer_head) != NULL) {
		assert(++count < 20);
		eigrp.packetizer_head = work->next;
		if (!eigrp.packetizer_head)
			eigrp.packetizer_tail = NULL;
		eigrp.packetizer_count--;
		work->next = NULL;
		eigrp_packetizer_work_process(&eigrp, work);
		eigrp_packetizer_work_free(work);
	}
	eigrp_packetizer_deferred_collect(&eigrp);
	assert(eigrp_topo_table_lookup(eigrp.topology_table, &target) == NULL);
	eigrp_packetizer_delete(&eigrp);
	eigrp_topo_table_delete(&eigrp, eigrp.topology_table);
	eigrp_list_delete(&eigrp.topology_changes);
	eigrp_list_delete(&eigrp.oi_write_q);
	eigrp_list_delete(&eigrp.eiflist);
	free(eigrp.neighbor_self);
	assert(pthread_cond_destroy(&eigrp.work_cond) == 0);
	assert(pthread_mutex_destroy(&eigrp.work_lock) == 0);
	assert(pthread_mutex_destroy(&eigrp.peer_lock) == 0);
	puts("Interface removal preserves poison until packetization");
	return 0;
}

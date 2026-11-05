// SPDX-License-Identifier: ISC
// Copyright (C) 2026 Donnie V. Savage
/* Production metric configuration and received HELLO hard-reset regression. */
#include <assert.h>
#include <arpa/inet.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "eigrp.h"
#include "eigrp_structs.h"
#include "eigrp_neighbor.h"
#include "eigrp_interface.h"
#include "eigrp_packet.h"
#include "eigrp_metric.h"
#include "eigrp_topology.h"
#include "eigrp_tlv1.h"
#include "eigrp_tlv2.h"
#include "eigrp_list.h"
#include "eigrp_sys.h"

bool eigrp_sys_auth_key_lookup(const char *name, uint32_t *id, char *key,
			       size_t size)
{
	(void)name;
	(void)id;
	(void)key;
	(void)size;
	return false;
}

const char *eigrp_message_lookup(const eigrp_message_t *messages, int value,
				 const char *fallback)
{
	const eigrp_message_t *entry;
	if (!messages)
		return fallback;
	for (entry = messages; entry->name; ++entry)
		if (entry->value == value)
			return entry->name;
	return fallback;
}

eigrp_result_t eigrp_sys_filter_evaluate(eigrp_instance_t *runtime,
					 eigrp_distribute_list_type_t type,
					 const char *name,
					 const eigrp_prefix_t *prefix,
					 eigrp_filter_decision_t *decision)
{
	(void)runtime;
	(void)type;
	(void)name;
	(void)prefix;
	if (!decision)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	*decision = EIGRP_FILTER_DECISION_PERMIT;
	return EIGRP_RESULT_SUCCESS;
}

static int record_packet(eigrp_instance_t *runtime, eigrp_intf_t *ei,
			 eigrp_packet_t *packet)
{
	(void)runtime;
	(void)ei;
	return packet->length;
}

static void check_encoder(eigrp_intf_t *ei, uint16_t tlv1, uint16_t tlv2)
{
	assert(ei->tlv1_peer_count == tlv1);
	assert(ei->tlv2_peer_count == tlv2);
	if (tlv1 && tlv2)
		assert(ei->encoder == eigrp_packet_encoder_both);
	else if (tlv1)
		assert(ei->encoder == ei->eigrp->tlv1_codec.encoder);
	else if (tlv2)
		assert(ei->encoder == ei->eigrp->tlv2_codec.encoder);
	else
		assert(ei->encoder == eigrp_packet_encoder_safe);
}

static void peer_address(eigrp_addr_t *addr, uint8_t host)
{
	memset(addr, 0, sizeof(*addr));
	addr->afi = AF_INET;
	addr->ip.v4.s_addr = htonl(UINT32_C(0x0a000000) | host);
}

static eigrp_nbr_t *peer_lookup(eigrp_intf_t *ei, uint8_t host)
{
	eigrp_addr_t address;
	peer_address(&address, host);
	return eigrp_nbr_lookup(ei, NULL, &address);
}

static eigrp_nbr_t *peer_add(eigrp_intf_t *ei, uint8_t host, uint8_t announced,
			     bool up)
{
	eigrp_addr_t addr;
	eigrp_nbr_t *nbr;
	peer_address(&addr, host);
	nbr = eigrp_nbr_create(ei, &addr);
	assert(nbr);
	nbr->tlv_rel_major = announced;
	eigrp_nbr_codec_select(
		nbr, eigrp_metric_version_select(ei->eigrp, announced));
	eigrp_nbr_state_update(EIGRP_SET, nbr,
			       up ? EIGRP_NEIGHBOR_UP : EIGRP_NEIGHBOR_PENDING);
	assert(nbr->state == (up ? EIGRP_NEIGHBOR_UP : EIGRP_NEIGHBOR_PENDING));
	return nbr;
}

static void peer_hello(eigrp_instance_t *runtime, eigrp_intf_t *ei,
		       uint8_t host, uint8_t version, uint8_t k1,
		       bool malformed)
{
	uint8_t packet[EIGRP_HEADER_LEN + EIGRP_TLV_PARAMETER_LEN
		       + EIGRP_TLV_SW_VERSION_LEN] = {0};
	uint8_t *parameter = packet + EIGRP_HEADER_LEN;
	uint8_t *software = parameter + EIGRP_TLV_PARAMETER_LEN;
	eigrp_addr_t source;

	parameter[0] = 0;
	parameter[1] = EIGRP_TLV_PARAMETER;
	parameter[2] = 0;
	parameter[3] = EIGRP_TLV_PARAMETER_LEN;
	parameter[4] = k1;
	parameter[5] = EIGRP_K2_DEFAULT;
	parameter[6] = EIGRP_K3_DEFAULT;
	parameter[7] = EIGRP_K4_DEFAULT;
	parameter[8] = EIGRP_K5_DEFAULT;
	parameter[9] = EIGRP_K6_DEFAULT;
	parameter[10] = 0;
	parameter[11] = 15;

	software[0] = 0;
	software[1] = EIGRP_TLV_SW_VERSION;
	software[2] = 0;
	software[3] = malformed ? EIGRP_TLV_SW_VERSION_LEN - 1
				: EIGRP_TLV_SW_VERSION_LEN;
	software[4] = 1;
	software[5] = 0;
	software[6] = version;
	software[7] = 0;

	peer_address(&source, host);
	eigrp_hello_receive(runtime, (eigrp_header_t *)packet, &source, ei,
			    NULL, sizeof(packet));
}

static void runtime_init(eigrp_instance_t *runtime, eigrp_intf_t *ei)
{
	static eigrp_virt_router_t vr;
	memset(runtime, 0, sizeof(*runtime));
	memset(ei, 0, sizeof(*ei));
	assert(pthread_mutex_init(&runtime->peer_lock, NULL) == 0);
	assert(pthread_mutex_init(&runtime->work_lock, NULL) == 0);
	assert(pthread_cond_init(&runtime->work_cond, NULL) == 0);
	runtime->thread_running = true;
	runtime->virt_router = &vr;
	vr.vrid = 1;
	runtime->AS = 4453;
	runtime->metric_version = EIGRP_TLV_64B_VERSION;
	/* Initialize the actual AF vectors: Peer Termination HELLOs invoke
	 * packet_address_encode(), not merely the version TLV parser. */
	eigrp_ipv4_init(&runtime->af_vectors);
	runtime->af_vectors.packet_send = record_packet;
	runtime->eiflist = eigrp_list_create();
	runtime->oi_write_q = eigrp_list_create();
	runtime->topology_table = eigrp_topology_table_create();
	runtime->topology_changes = eigrp_list_create();
	assert(runtime->eiflist && runtime->oi_write_q
	       && runtime->topology_table && runtime->topology_changes);
	runtime->k_values[0] = EIGRP_K1_DEFAULT;
	runtime->k_values[1] = EIGRP_K2_DEFAULT;
	runtime->k_values[2] = EIGRP_K3_DEFAULT;
	runtime->k_values[3] = EIGRP_K4_DEFAULT;
	runtime->k_values[4] = EIGRP_K5_DEFAULT;
	runtime->k_values[5] = EIGRP_K6_DEFAULT;
	eigrp_tlv1_init(&runtime->tlv1_codec);
	eigrp_tlv2_init(&runtime->tlv2_codec);

	ei->eigrp = runtime;
	ei->ifindex = 1;
	ei->name = "peer-version-test";
	ei->curr_mtu = 1500;
	ei->nbrs = eigrp_list_create();
	ei->obuf = eigrp_packet_queue_create();
	assert(ei->nbrs && ei->obuf);
	assert(eigrp_list_add(runtime->eiflist, ei));
	eigrp_intf_encoder_clear(ei);
}

static void runtime_finish(eigrp_instance_t *runtime, eigrp_intf_t *ei)
{
	while (ei->nbrs->head)
		eigrp_nbr_delete(ei->nbrs->head->data);
	eigrp_topology_table_delete(runtime, runtime->topology_table);
	eigrp_packet_interface_output_clear(ei);
	eigrp_packet_queue_free(ei->obuf);
	eigrp_list_delete(&runtime->topology_changes);
	eigrp_list_delete(&ei->nbrs);
	eigrp_list_delete(&runtime->eiflist);
	eigrp_list_delete(&runtime->oi_write_q);
	eigrp_nbr_warning_state_clear(runtime);
	pthread_cond_destroy(&runtime->work_cond);
	pthread_mutex_destroy(&runtime->work_lock);
	pthread_mutex_destroy(&runtime->peer_lock);
}

int main(void)
{
	eigrp_instance_t runtime;
	eigrp_intf_t ei;
	eigrp_instance_context_t context = {0};
	eigrp_metric_weights_t weights = {0};
	eigrp_nbr_t *nbr;

	runtime_init(&runtime, &ei);
	context.runtime = &runtime;
	check_encoder(&ei, 0, 0);

	peer_add(&ei, 2, EIGRP_TLV_64B_VERSION, true);
	peer_add(&ei, 3, EIGRP_TLV_64B_VERSION, true);
	check_encoder(&ei, 0, 2);
	peer_hello(&runtime, &ei, 2, EIGRP_TLV_64B_VERSION, 1, false);
	check_encoder(&ei, 0, 2);

	/* Unknown or malformed version bytes cannot alter adjacency state. */
	peer_hello(&runtime, &ei, 2, EIGRP_TLV_32B_VERSION, 1, true);
	check_encoder(&ei, 0, 2);
	assert(peer_lookup(&ei, 2));

	/* Remote switch: old descriptor is retired, not recounted while UP. */
	peer_hello(&runtime, &ei, 2, EIGRP_TLV_32B_VERSION, 1, false);
	assert(!peer_lookup(&ei, 2));
	check_encoder(&ei, 0, 1);
	nbr = peer_add(&ei, 2, EIGRP_TLV_32B_VERSION, false);
	peer_hello(&runtime, &ei, 2, EIGRP_TLV_32B_VERSION, 1, false);
	assert(peer_lookup(&ei, 2) == nbr);
	assert(nbr->state == EIGRP_NEIGHBOR_PENDING);
	eigrp_nbr_state_update(EIGRP_SET, nbr, EIGRP_NEIGHBOR_UP);
	check_encoder(&ei, 1, 1);

	/* Preserve a locked DOWN peer's penalty across the configuration
	 * transition. There is no live adjacency to restart for that peer. */
	nbr = peer_add(&ei, 10, EIGRP_TLV_64B_VERSION, false);
	eigrp_nbr_state_update(EIGRP_SET, nbr, EIGRP_NEIGHBOR_DOWN);
	nbr->limit_state = EIGRP_NBR_LIMIT_LOCKED;

	/* A local policy change resets even peers already using TLV1. It must
	 * discard pending multicast work encoded under the previous policy. */
	{
		eigrp_packet_t *stale = eigrp_packet_create(128, NULL);
		assert(stale);
		eigrp_packet_enqueue(&ei.multicast_pending, stale);
		assert(ei.multicast_pending.count == 1);
	}
	assert(eigrp_metric_version_update(EIGRP_SET, &context)
	       == EIGRP_RESULT_SUCCESS);
	assert(!peer_lookup(&ei, 2) && !peer_lookup(&ei, 3));
	assert(peer_lookup(&ei, 10) == nbr
	       && nbr->limit_state == EIGRP_NBR_LIMIT_LOCKED);
	assert(nbr->tlv_version == EIGRP_TLV_32B_VERSION);
	assert(ei.multicast_pending.count == 0 && ei.obuf->count == 0);
	check_encoder(&ei, 0, 0);
	assert(runtime.metric_version == EIGRP_TLV_32B_VERSION);

	peer_add(&ei, 4, EIGRP_TLV_32B_VERSION, true);
	assert(eigrp_metric_version_update(EIGRP_SET, &context)
	       == EIGRP_RESULT_SUCCESS);
	assert(peer_lookup(&ei, 4)); /* no-op reapply must not flap */
	check_encoder(&ei, 1, 0);

	/* Even if selected TLV1 is unchanged, a peer's advertised capability
	 * change requires a new INIT epoch, not stale wide-metric topology. */
	peer_add(&ei, 5, EIGRP_TLV_64B_VERSION, true);
	check_encoder(&ei, 2, 0);
	peer_hello(&runtime, &ei, 5, EIGRP_TLV_32B_VERSION, 1, false);
	assert(!peer_lookup(&ei, 5));
	check_encoder(&ei, 1, 0);

	assert(eigrp_metric_version_update(EIGRP_RESET, &context)
	       == EIGRP_RESULT_SUCCESS);
	assert(!peer_lookup(&ei, 4));
	check_encoder(&ei, 0, 0);
	assert(runtime.metric_version == EIGRP_TLV_64B_VERSION);

	/* K1..K6 configuration resets all peers; no-op updates do not. */
	peer_add(&ei, 6, EIGRP_TLV_64B_VERSION, true);
	peer_add(&ei, 9, EIGRP_TLV_32B_VERSION, true);
	check_encoder(&ei, 1, 1);
	weights.tos = 0;
	weights.k1 = 2;
	weights.k3 = 1;
	assert(eigrp_metric_weights_update(EIGRP_SET, &context, &weights)
	       == EIGRP_RESULT_SUCCESS);
	assert(!peer_lookup(&ei, 6) && !peer_lookup(&ei, 9));
	check_encoder(&ei, 0, 0);
	peer_add(&ei, 7, EIGRP_TLV_64B_VERSION, true);
	peer_add(&ei, 9, EIGRP_TLV_32B_VERSION, true);
	assert(eigrp_metric_weights_update(EIGRP_SET, &context, &weights)
	       == EIGRP_RESULT_SUCCESS);
	assert(peer_lookup(&ei, 7) && peer_lookup(&ei, 9));
	check_encoder(&ei, 1, 1);
	assert(eigrp_metric_weights_update(EIGRP_RESET, &context, NULL)
	       == EIGRP_RESULT_SUCCESS);
	assert(!peer_lookup(&ei, 7) && !peer_lookup(&ei, 9));
	check_encoder(&ei, 0, 0);

	/* A mismatched peer HELLO must retire DUAL state and unpublish the
	 * peer. */
	peer_add(&ei, 8, EIGRP_TLV_64B_VERSION, true);
	peer_hello(&runtime, &ei, 8, EIGRP_TLV_64B_VERSION, 2, false);
	assert(!peer_lookup(&ei, 8));
	check_encoder(&ei, 0, 0);

	runtime_finish(&runtime, &ei);
	puts("Peer version and metric restart lifecycle passed");
	return 0;
}

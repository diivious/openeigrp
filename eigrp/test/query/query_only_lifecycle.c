// SPDX-License-Identifier: ISC
/* Copyright (C) 2026 Donnie V. Savage */
/* Execute real QUERY/UPDATE receive, DUAL, topology and neighbor-down paths. */
#define main admission_fixture_main
#include "neighbor_path_admission.c"
#undef main

/* Keep the regression buildable against the defective branch as well. */
#ifndef EIGRP_ROUTE_DESCRIPTOR_QUERY_ONLY_FLAG
#define EIGRP_ROUTE_DESCRIPTOR_QUERY_ONLY_FLAG (1 << 4)
#endif

void eigrp_update_receive(eigrp_instance_t *, eigrp_nbr_t *,
			  struct eigrp_header *, eigrp_stream_t *,
			  eigrp_intf_t *, int);

static eigrp_nbr_t peer2;

static void receive_peer(unsigned opcode, eigrp_nbr_t *nbr, unsigned id,
			 unsigned delay)
{
	eigrp_header_t hdr = {.sequence = htonl(77)};
	eigrp_stream_t stream = {.getp = 0, .endp = 1, .size = 1};
	incoming = wire(id, false);
	incoming->metric.delay = delay == UINT32_MAX
					 ? EIGRP_MAX_METRIC
					 : eigrp_delay_to_scaled(delay);
	if (opcode == EIGRP_OPC_QUERY)
		eigrp_query_receive(&runtime, nbr, &hdr, &stream, &link, 1);
	else if (opcode == EIGRP_OPC_UPDATE)
		eigrp_update_receive(&runtime, nbr, &hdr, &stream, &link, 1);
	else
		eigrp_reply_receive(&runtime, nbr, &hdr, &stream, &link, 1);
	assert(incoming == NULL);
}

static void init(void)
{
	eigrp_instance_context_t ctx = {.config = &policy};
	eigrp_prefix_limit_t limit = {.maximum = 100};
	start(false);
	assert(eigrp_nbr_max_prefix_all_update(EIGRP_SET, &ctx, &limit)
	       == EIGRP_RESULT_SUCCESS);
	memset(&peer2, 0, sizeof(peer2));
	peer2 = peer;
	peer2.src.ip.v4.s_addr = htonl(0x0a000003);
	peer2.limit_prefix_count = 0;
	peer2.topo_refs = 0;
}

static eigrp_prefix_descriptor_t *get(unsigned id)
{
	eigrp_prefix_t p = dest(id);
	return eigrp_topology_table_lookup(runtime.topology_table, &p);
}

static void retained(eigrp_prefix_descriptor_t *p, eigrp_nbr_t *n)
{
	eigrp_route_descriptor_t *r = eigrp_prefix_descriptor_lookup(p, n);
	assert(r);
	assert(r->flags & EIGRP_ROUTE_DESCRIPTOR_QUERY_ONLY_FLAG);
	assert(!(r->flags
		 & (EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG
		    | EIGRP_ROUTE_DESCRIPTOR_INTABLE_FLAG)));
}

int main(void)
{
	eigrp_prefix_descriptor_t *p;
	eigrp_route_descriptor_t *selected;
	init();
	/* Two QUERYs carry different finite vectors but neither is a route. */
	receive_peer(EIGRP_OPC_QUERY, &peer, 10, 10);
	receive_peer(EIGRP_OPC_QUERY, &peer2, 10, 20);
	p = get(10);
	assert(p && p->distance == EIGRP_MAX_METRIC
	       && p->fdistance == EIGRP_MAX_METRIC);
	retained(p, &peer);
	retained(p, &peer2);
	assert(!eigrp_topology_route_select(p));
	assert(!eigrp_topology_route_read(p));
	assert(rib_installs == 0);
	assert(peer.limit_prefix_count == 1 && peer2.limit_prefix_count == 1);

	/* Poisoned UPDATE removes only the sender's un-elected observation. */
	receive_peer(EIGRP_OPC_UPDATE, &peer2, 10, UINT32_MAX);
	p = get(10);
	assert(p && !eigrp_prefix_descriptor_lookup(p, &peer2));
	retained(p, &peer);
	assert(!eigrp_topology_route_select(p));
	assert(!rib_installs);
	assert(peer2.limit_prefix_count == 0 && peer2.topo_refs == 0);

	/* A lower-metric QUERY-only route must not beat a valid UPDATE. */
	receive_peer(EIGRP_OPC_QUERY, &peer2, 10, 20);
	receive_peer(EIGRP_OPC_UPDATE, &peer2, 10, 50);
	p = get(10);
	retained(p, &peer);
	selected = eigrp_prefix_descriptor_lookup(p, &peer2);
	assert(selected
	       && !(selected->flags & EIGRP_ROUTE_DESCRIPTOR_QUERY_ONLY_FLAG));
	assert(eigrp_topology_route_select(p) == selected);
	assert(selected->flags & EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG);
	assert(p->distance == selected->distance);
	assert(rib_installs > 0);

	/* An unrelated QUERY-only neighbor cannot replace an UPDATE
	 * successor on withdrawal. */
	receive_peer(EIGRP_OPC_UPDATE, &peer2, 10, UINT32_MAX);
	p = get(10);
	if (p) {
		retained(p, &peer);
		assert(!eigrp_topology_route_select(p));
		assert(!(eigrp_prefix_descriptor_lookup(p, &peer)->flags
			 & EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG));
	}

	finish();
	init();
	/* Poisoned and filtered QUERYs must retire the cached DRDB and DNDB. */
	receive_peer(EIGRP_OPC_QUERY, &peer, 11, 10);
	receive_peer(EIGRP_OPC_QUERY, &peer, 11, UINT32_MAX);
	assert(!get(11));
	assert(peer.limit_prefix_count == 0 && peer.topo_refs == 0);
	receive_peer(EIGRP_OPC_QUERY, &peer, 12, 10);
	deny_inbound = true;
	receive_peer(EIGRP_OPC_QUERY, &peer, 12, 10);
	deny_inbound = false;
	assert(!get(12));
	assert(peer.limit_prefix_count == 0 && peer.topo_refs == 0);

	/* An accepted REPLY during ACTIVE can turn retained QUERY evidence into
	 * a real path. An unreachable REPLY cannot. */
	receive_peer(EIGRP_OPC_QUERY, &peer, 13, 10);
	p = get(13);
	assert(p);
	p->state = EIGRP_FSM_STATE_ACTIVE_1;
	eigrp_fsm_reply_status_add(p, &peer);
	receive_peer(EIGRP_OPC_REPLY, &peer, 13, 25);
	p = get(13);
	assert(p && p->state == EIGRP_FSM_STATE_PASSIVE);
	selected = eigrp_prefix_descriptor_lookup(p, &peer);
	assert(selected
	       && !(selected->flags & EIGRP_ROUTE_DESCRIPTOR_QUERY_ONLY_FLAG));
	assert(selected->flags & EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG);
	assert(p->distance == selected->distance);

	receive_peer(EIGRP_OPC_QUERY, &peer2, 14, 10);
	p = get(14);
	assert(p);
	p->state = EIGRP_FSM_STATE_ACTIVE_1;
	eigrp_fsm_reply_status_add(p, &peer2);
	receive_peer(EIGRP_OPC_REPLY, &peer2, 14, UINT32_MAX);
	p = get(14);
	if (p) {
		selected = eigrp_prefix_descriptor_lookup(p, &peer2);
		if (selected)
			retained(p, &peer2);
		assert(!eigrp_topology_route_select(p));
	}
	finish();
	init();

	/* Exercise neighbor-down traversal where removing the first node frees
	 * its table-node and must not invalidate iteration to the second. */
	receive_peer(EIGRP_OPC_QUERY, &peer, 20, 10);
	receive_peer(EIGRP_OPC_QUERY, &peer, 21, 10);
	receive_peer(EIGRP_OPC_QUERY, &peer2, 21, 20);
	p = get(21);
	assert(p);
	retained(p, &peer2);
	eigrp_topology_neighbor_down(&runtime, &peer);
	assert(!get(20));
	p = get(21);
	assert(p && !eigrp_prefix_descriptor_lookup(p, &peer));
	retained(p, &peer2);
	assert(peer.limit_prefix_count == 0 && peer.topo_refs == 0);
	assert(rib_installs == 0);
	eigrp_topology_neighbor_down(&runtime, &peer2);
	assert(!get(21));
	assert(peer2.limit_prefix_count == 0 && peer2.topo_refs == 0);
	finish();
	puts("QUERY-only lifecycle: no false successor, cleanup and recovery OK");
	return 0;
}

/* Platform/timer/wire boundaries are intentionally inert in this portable
 * receive-path regression. Production receive, DUAL and storage run intact. */
void eigrp_hello_send_ack_sequence(eigrp_nbr_t *n, uint32_t sequence)
{
	(void)n;
	(void)sequence;
}
uint32_t eigrp_timer_remaining_seconds(eigrp_instance_t *e,
				       const eigrp_timer_event_t *t)
{
	(void)e;
	(void)t;
	return 0;
}
void eigrp_timer_add(eigrp_instance_t *e, eigrp_timer_event_t **owner,
		     eigrp_event_callback_t cb, void *arg, uint32_t delay)
{
	(void)e;
	(void)owner;
	(void)cb;
	(void)arg;
	(void)delay;
}
void eigrp_debug_nsf_event(const eigrp_instance_t *e, const eigrp_nbr_t *n,
			   uint32_t flags, const char *event)
{
	(void)e;
	(void)n;
	(void)flags;
	(void)event;
}
void eigrp_offset_metric_update(eigrp_instance_t *e, eigrp_intf_t *ei,
				int direction, const eigrp_prefix_t *p,
				eigrp_metrics_t *m)
{
	(void)e;
	(void)ei;
	(void)direction;
	(void)p;
	(void)m;
}
uint32_t eigrp_packet_sequence_reserve(eigrp_instance_t *e)
{
	(void)e;
	return 1;
}
eigrp_packet_t *eigrp_packet_create(size_t size, eigrp_nbr_t *n)
{
	(void)size;
	(void)n;
	return NULL;
}
void eigrp_packet_header_init(int type, eigrp_instance_t *e, eigrp_stream_t *s,
			      uint32_t f, uint32_t seq, uint32_t ack)
{
	(void)type;
	(void)e;
	(void)s;
	(void)f;
	(void)seq;
	(void)ack;
}
bool eigrp_auth_material_available(const eigrp_intf_t *ei)
{
	(void)ei;
	return false;
}
uint16_t eigrp_auth_tlv_md5_encode(eigrp_stream_t *s, eigrp_intf_t *ei)
{
	(void)s;
	(void)ei;
	return 0;
}
uint16_t eigrp_auth_tlv_sha256_encode(eigrp_stream_t *s, eigrp_intf_t *ei)
{
	(void)s;
	(void)ei;
	return 0;
}
void eigrp_packet_enqueue(eigrp_packet_queue_t *q, eigrp_packet_t *p)
{
	(void)q;
	(void)p;
}
void eigrp_debug_transmit_event(unsigned long category,
				const eigrp_instance_t *e,
				const eigrp_intf_t *ei, const eigrp_nbr_t *n,
				const char *fmt, ...)
{
	(void)category;
	(void)e;
	(void)ei;
	(void)n;
	(void)fmt;
}
void eigrp_packet_send_reliably(eigrp_instance_t *e, eigrp_nbr_t *n)
{
	(void)e;
	(void)n;
}
void eigrp_packet_free(eigrp_packet_t *p)
{
	(void)p;
}
bool eigrp_summary_route_build(eigrp_instance_t *e, eigrp_intf_t *ei,
			       const eigrp_prefix_descriptor_t *p,
			       const eigrp_route_descriptor_t *r,
			       eigrp_prefix_descriptor_t *sp,
			       eigrp_route_descriptor_t *sr)
{
	(void)e;
	(void)ei;
	(void)p;
	(void)r;
	(void)sp;
	(void)sr;
	return false;
}
bool eigrp_summary_specific_leak(eigrp_instance_t *e, eigrp_intf_t *ei,
				 const eigrp_prefix_t *p)
{
	(void)e;
	(void)ei;
	(void)p;
	return false;
}
int eigrp_packet_route_encode_append(eigrp_instance_t *e, eigrp_intf_t *ei,
				     eigrp_nbr_t *n,
				     eigrp_packet_encoder_t encoder,
				     eigrp_stream_t *s,
				     eigrp_route_descriptor_t *r,
				     uint16_t max_len)
{
	(void)e;
	(void)ei;
	(void)n;
	(void)encoder;
	(void)s;
	(void)r;
	(void)max_len;
	return 0;
}
void eigrp_intf_encoder_peer_remove(eigrp_intf_t *ei, uint8_t version)
{
	(void)ei;
	(void)version;
}
void eigrp_intf_encoder_peer_add(eigrp_intf_t *ei, uint8_t version)
{
	(void)ei;
	(void)version;
}
void eigrp_debug_neighbor_state(eigrp_nbr_t *n, uint8_t old_state,
				uint8_t new_state)
{
	(void)n;
	(void)old_state;
	(void)new_state;
}
void eigrp_packet_queue_clear(eigrp_packet_queue_t *q)
{
	(void)q;
}
eigrp_packet_queue_t *eigrp_packet_queue_create(void)
{
	return calloc(1, sizeof(eigrp_packet_queue_t));
}
void eigrp_packet_multicast_resume(eigrp_intf_t *ei)
{
	(void)ei;
}
void eigrp_packet_neighbor_output_clear(eigrp_nbr_t *n)
{
	(void)n;
}
void eigrp_packet_queue_free(eigrp_packet_queue_t *q)
{
	free(q);
}

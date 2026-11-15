// SPDX-License-Identifier: ISC
/* Copyright (C) 2026 Donnie V. Savage */
/* Exercise actual UPDATE admission, DUAL, and dead-neighbor path retirement. */
#define main admission_fixture_main
#include "neighbor_path_admission.c"
#undef main

void eigrp_update_receive(eigrp_instance_t *, eigrp_nbr_t *,
			  struct eigrp_header *, eigrp_stream_t *,
			  eigrp_intf_t *, int);

static void update(eigrp_nbr_t *nbr, unsigned id, bool poison)
{
	eigrp_header_t hdr = {.sequence = htonl(42)};
	eigrp_stream_t stream = {.getp = 0, .endp = 1, .size = 1};

	incoming = wire(id, poison);
	eigrp_update_receive(&runtime, nbr, &hdr, &stream, &link, 1);
	assert(!incoming);
}

int main(void)
{
	eigrp_nbr_t second;
	eigrp_prefix_descriptor_t *prefix;
	eigrp_route_descriptor_t *first, *other;
	uint64_t fd;

	start(true);
	second = peer;
	second.src.ip.v4.s_addr = htonl(0x0a000003);
	second.topo_refs = 0;
	second.limit_prefix_count = 0;
	update(&peer, 42, false);
	update(&second, 42, false);
	/* Look up the real destination rather than relying on traversal order.
	 */
	eigrp_prefix_t target = dest(42);
	prefix = eigrp_topology_table_lookup(runtime.topology_table, &target);
	assert(prefix && prefix->state == EIGRP_FSM_STATE_PASSIVE);
	first = eigrp_prefix_descriptor_lookup(prefix, &peer);
	other = eigrp_prefix_descriptor_lookup(prefix, &second);
	assert(first && other && first->distance == other->distance);
	assert((first->flags & EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG)
	       && (other->flags & EIGRP_ROUTE_DESCRIPTOR_SUCCESSOR_FLAG));
	fd = prefix->fdistance;
	/* The fixture has no packetizer worker. Emulate completion of the
	 * initial UPDATE bead before injecting the link failure. */
	assert(prefix->req_action & EIGRP_FSM_NEED_UPDATE);
	prefix->req_action = 0;
	eigrp_list_delete_data(runtime.topology_changes, prefix);

	/* A surviving equal-cost successor needs no metric UPDATE. The dead
	 * neighbor's poisoned DRDB still must be retired. */
	eigrp_topology_neighbor_down(&runtime, &second);
	prefix = eigrp_topology_table_lookup(runtime.topology_table, &target);
	assert(prefix && prefix->state == EIGRP_FSM_STATE_PASSIVE);
	assert(prefix->fdistance == fd);
	assert(eigrp_prefix_descriptor_lookup(prefix, &peer));
	assert(!eigrp_prefix_descriptor_lookup(prefix, &second));
	assert(second.topo_refs == 0 && second.limit_prefix_count == 0);

	/* Repeated neighbor teardown must not affect surviving paths. */
	eigrp_topology_neighbor_down(&runtime, &second);
	assert(eigrp_prefix_descriptor_lookup(prefix, &peer));
	assert(!eigrp_prefix_descriptor_lookup(prefix, &second));

	/* A new finite path from the recovered peer must be admitted again. */
	update(&second, 42, false);
	assert(eigrp_prefix_descriptor_lookup(prefix, &second));
	eigrp_topology_neighbor_down(&runtime, &second);
	assert(!eigrp_prefix_descriptor_lookup(prefix, &second));
	finish();
	puts("ECMP neighbor withdrawal, retirement and recovery OK");
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

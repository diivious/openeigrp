// SPDX-License-Identifier: GPL-2.0-or-later
/* Executable FRR named output formatting regression (standalone VTY shim).
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <zebra.h>
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

static char output[16384];
static size_t used;

static int capture_out(struct vty *vty, const char *fmt, ...)
{
	va_list args;
	int length;
	(void)vty;
	va_start(args, fmt);
	length = vsnprintf(output + used, sizeof(output) - used, fmt, args);
	va_end(args);
	assert(length >= 0 && (size_t)length < sizeof(output) - used);
	used += (size_t)length;
	return length;
}
#define vty_out capture_out
#define EIGRP_STANDALONE_BUILD
#include "eigrp_frr_named.c"
#undef vty_out

static void reset(void)
{
	used = 0;
	output[0] = '\0';
}

static void timer_output(void)
{
	struct vty vty = {0};
	struct eigrp_vty_timer_show show = {.vty = &vty};
	eigrp_timer_state_t event = {.type = EIGRP_TIMER_STATE_HELLO,
				     .interface_name = "eth0",
				     .expiration_msec = 1022};
	reset();
	assert(eigrp_vty_timer_state_render(&event, &show)
	       == EIGRP_RESULT_SUCCESS);
	assert(strstr(output,
		      "Hello Process\nExpiration Type\n| 1.022 Hello (eth0)"));
	event.type = EIGRP_TIMER_STATE_PEER_HOLD;
	event.expiration_msec = 14984;
	event.neighbor_address.afi = EIGRP_AFI_IPV4;
	assert(inet_pton(AF_INET, "192.0.2.2", event.neighbor_address.bytes)
	       == 1);
	assert(eigrp_vty_timer_state_render(&event, &show)
	       == EIGRP_RESULT_SUCCESS);
	assert(strstr(
		output,
		"Update Process\nExpiration Type\n| 14.984 Peer holding (192.0.2.2 via eth0)"));
	event.type = EIGRP_TIMER_STATE_LIMIT_RESTART;
	assert(eigrp_vty_timer_state_render(&event, &show)
	       == EIGRP_RESULT_SUCCESS);
	assert(strstr(output, "Prefix-limit Process"));
	event.type = EIGRP_TIMER_STATE_ACTIVE;
	event.expiration_msec = 975;
	event.destination.address.afi = EIGRP_AFI_IPV6;
	event.destination.prefix_length = 128;
	assert(inet_pton(AF_INET6, "2001:db8::1",
			 event.destination.address.bytes)
	       == 1);
	assert(eigrp_vty_timer_state_render(&event, &show)
	       == EIGRP_RESULT_SUCCESS);
	assert(strstr(output, "SIA Process\nExpiration Type for Topo(base)"));
	assert(strstr(output, "| 0.975 ACTIVE/SIA (2001:db8::1/128)"));
}

static void accounting_output(void)
{
	struct vty vty = {0};
	uint32_t total = 17;
	struct eigrp_vty_accounting_show show = {
		.vty = &vty,
		.total_prefix_count = &total,
	};
	eigrp_stat_nbr_limit_t entry = {.status = 'P',
					.interface_name = "eth1",
					.prefix_count = 2,
					.restart_cnt = 3,
					.restart_seconds = 17,
					.reset_seconds = 26};
	reset();
	entry.neighbor_address.afi = EIGRP_AFI_IPV4;
	assert(inet_pton(AF_INET, "192.0.2.3", entry.neighbor_address.bytes)
	       == 1);
	assert(eigrp_vty_accounting_entry_show(&entry, &show)
	       == EIGRP_RESULT_SUCCESS);
	assert(strncmp(output, "Total Prefix Count: 17\n", 23) == 0);
	assert(strstr(output, "P-Suspended, D-Locked"));
	assert(strstr(output, "192.0.2.3"));
	assert(strstr(output, "Prefix Count"));
	assert(strstr(output, "Restart(s)"));
	assert(strstr(output, "Reset(s)"));
	assert(strstr(output, "17") && strstr(output, "26"));
}

static void topology_output(void)
{
	struct vty vty = {0};
	struct eigrp_vty_topology_show show = {.vty = &vty, .detail = true};
	eigrp_topology_prefix_state_t prefix = {.active = true,
						.dual_state = 2,
						.pending_replies = 1,
						.serial_number = 55,
						.successor_count = 1,
						.feasible_distance = 1024};
	eigrp_topology_route_state_t path = {.redistributed = true,
					     .external = true,
					     .distance = 1024,
					     .reported_distance = 0};
	prefix.destination.address.afi = EIGRP_AFI_IPV6;
	prefix.destination.prefix_length = 128;
	assert(inet_pton(AF_INET6, "2001:db8::4",
			 prefix.destination.address.bytes)
	       == 1);
	path.total_metric.wide_bandwidth = 100000;
	path.total_metric.wide_delay = 123456789ULL;
	path.total_metric.reliability = 255;
	path.total_metric.mtu[0] = 0xdc;
	path.total_metric.mtu[1] = 0x05; /* TLV storage: MTU 1500 */
	reset();
	assert(eigrp_vty_topology_prefix_show(&prefix, &show)
	       == EIGRP_RESULT_SUCCESS);
	assert(eigrp_vty_topology_route_show(&path, &show)
	       == EIGRP_RESULT_SUCCESS);
	assert(strstr(output, "DUAL state 2"));
	assert(strstr(output, "Replies pending: 1, serial number: 55"));
	assert(strstr(output, "via Redistributed (1024/0)"));
	assert(strstr(output, "bandwidth 100000 Kbit/s, delay 123 us"));
	assert(strstr(output, "MTU 1500"));
	assert(strstr(output, "load n/a"));
	assert(strstr(output, "Reported vector: n/a"));
	path.distance = EIGRP_METRIC_MAX;
	path.reported_distance = EIGRP_MAX_METRIC;
	path.reported_metric.wide_bandwidth = EIGRP_TLV2_INACCESSIBLE;
	path.reported_metric.wide_delay = EIGRP_TLV2_INACCESSIBLE;
	reset();
	assert(eigrp_vty_topology_route_show(&path, &show)
	       == EIGRP_RESULT_SUCCESS);
	assert(strstr(output, "via Redistributed (Inaccessible/Inaccessible)"));
	assert(strstr(output, "Reported vector: n/a"));
	assert(!strstr(output, "281474976710655"));
}

static void neighbor_output(void)
{
	struct vty vty = {0};
	struct eigrp_vty_neighbor_show show = {.vty = &vty, .detail = true};
	eigrp_nbr_state_t state = {.interface_name = "eth1",
				   .state_name = "UP",
				   .srtt_valid = true,
				   .srtt_msec = 44,
				   .rto_msec = 220,
				   .reliable_queue_count = 2,
				   .uptime_seconds = 65,
				   .hold_time = 7,
				   .sequence_number = 11,
				   .prefix_count = 12,
				   .retransmit_count = 6,
				   .retry_count = 3,
				   .os_major = 2,
				   .os_minor = 1,
				   .tlv_major = 2,
				   .tlv_minor = 2,
				   .tlv_version = 2};
	state.address.afi = EIGRP_AFI_IPV6;
	assert(inet_pton(AF_INET6, "fe80::2", state.address.bytes) == 1);
	reset();
	assert(eigrp_vty_neighbor_state_render(&state, &show)
	       == EIGRP_RESULT_SUCCESS);
	assert(strstr(output, "fe80::2"));
	assert(strstr(output, "State: UP"));
	assert(strstr(output, "Version 2.1/2.2 (TLV 2)"));
	assert(strstr(output, "(ms)"));
	assert(strstr(output, "Retransmissions: 6"));
	assert(strstr(output, "Reliable queue: 2, last received sequence: 11"));
}

static void interface_output(void)
{
	struct vty vty = {0};
	struct eigrp_vty_interface_show show = {.vty = &vty, .detail = true};
	eigrp_intf_state_t state = {.interface_name = "eth0",
				    .runtime_present = true,
				    .up_peer_count = 2,
				    .mean_srtt_valid = true,
				    .mean_srtt_msec = 31,
				    .bandwidth = 100000,
				    .delay = 100,
				    .hello_interval = 5,
				    .hold_time = 15,
				    .pacing_tx_bytes = 128,
				    .pacing_defer_count = 2,
				    .pacing_defer_msec = 45,
				    .authentication_configured = true,
				    .authentication_mode = 2};
	reset();
	assert(eigrp_vty_interface_state_render(&state, &show)
	       == EIGRP_RESULT_SUCCESS);
	assert(strstr(output, "31"));
	assert(strstr(output, "Authentication: configured (mode 2)"));
	assert(strstr(output,
		      "Pacing: 128 bytes sent, 2 deferrals, 45 ms deferred"));
	assert(strstr(output, "100000"));
	assert(strstr(output, "Hello"));
	assert(strstr(output, "Hold"));
	assert(!strstr(output, "Xmit Queue"));
	/* A configured-only interface is absent from the operational table. */
	state.runtime_present = false;
	show.detail = false;
	reset();
	show.printed_header = false;
	assert(eigrp_vty_interface_state_render(&state, &show)
	       == EIGRP_RESULT_SUCCESS);
	assert(used == 0);
	/* Detail retains configuration without masquerading as a live link. */
	show.detail = true;
	reset();
	assert(eigrp_vty_interface_state_render(&state, &show)
	       == EIGRP_RESULT_SUCCESS);
	assert(strstr(output, "Configured interface eth0"));
	assert(!strstr(output, "Bandwidth: 100000"));
}

static void selection_output(void)
{
	eigrp_prefix_t prefix;
	eigrp_af_config_t af = {.shutdown = true};
	eigrp_instance_t runtime = {0};

	assert(eigrp_vty_destination_parse("198.51.100.73/24", EIGRP_AFI_IPV4,
					   &prefix));
	assert(prefix.prefix_length == 24);
	assert(prefix.address.bytes[3] == 0);
	assert(eigrp_vty_destination_parse("2001:db8:100::73/64",
					   EIGRP_AFI_IPV6, &prefix));
	assert(prefix.prefix_length == 64);
	assert(prefix.address.bytes[15] == 0);
	af.runtime = &runtime;
	assert(eigrp_vty_named_runtime_lookup(NULL, &af) == NULL);
	af.shutdown = false;
	assert(eigrp_vty_named_runtime_lookup(NULL, &af) == &runtime);
}

int main(void)
{
	timer_output();
	accounting_output();
	topology_output();
	interface_output();
	neighbor_output();
	selection_output();
	puts("FRR named show formatting: PASS");
	return 0;
}

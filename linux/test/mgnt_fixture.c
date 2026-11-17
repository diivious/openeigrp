// SPDX-License-Identifier: GPL-2.0-or-later
/* Executable public-management API fixture for Linux IPC regression.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <arpa/inet.h>
#include <errno.h>
#include <signal.h>
#include <stdatomic.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#include "eigrp_cli.h"
#include "eigrp_const.h"
#include "eigrp_linux_mgnt.h"
#include "eigrp_mgnt.h"

static int runtime_identity;
static int other_runtime_identity;
static int config_identity;
static atomic_uint clear_count;
static atomic_uint neighbors_cleared;
static atomic_uint topology_cleared;
static volatile sig_atomic_t shutdown_requested;

/* Fixture models the AF-owner dispatch contract without linking a core AF. */
eigrp_result_t eigrp_mgnt_runtime_execute(eigrp_instance_t *runtime,
					  eigrp_mgnt_runtime_cb callback,
					  void *arg)
{
	if (runtime != (eigrp_instance_t *)&runtime_identity
	    && runtime != (eigrp_instance_t *)&other_runtime_identity)
		return EIGRP_RESULT_NOT_FOUND;
	return callback(arg);
}

static eigrp_address_t fixture_addr(const char *str)
{
	eigrp_address_t a = {.afi = EIGRP_AFI_IPV4};
	if (inet_pton(AF_INET, str, a.bytes) != 1)
		abort();
	return a;
}

static void fixture_signal(int signal)
{
	(void)signal;
	shutdown_requested = 1;
}

static eigrp_result_t fixture_execute(eigrp_result_t (*fn)(void *), void *arg,
				      void *host_arg)
{
	(void)host_arg;
	return fn(arg);
}

/* These stubs implement the public state contract. The production IPC code
 * uses the same callbacks; no parser logic or core-private types are mocked. */
eigrp_result_t
eigrp_status_capability_state_read(eigrp_status_capability_state_t *state)
{
	memset(state, 0, sizeof(*state));
	state->release = "test\"fixture";
	state->ipv4 = state->tlv1 = state->tlv2 = true;
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_status_protocol_iterate(eigrp_status_protocol_cb fn,
					     void *arg)
{
	eigrp_status_protocol_state_t state = {
		.instance_name = "eigrprulez",
		.config = (eigrp_af_config_t *)&config_identity,
		.afi = EIGRP_AFI_IPV4,
		.vrf_name = "default",
		.asn = 4453,
		.runtime_present = true,
		.router_id_configured = true,
		.router_id = 0xc0000201,
		.effective_router_id = 0xc0000201,
		.k_values = {1, 0, 1, 0, 0, 0},
		.variance = 2,
		.max_paths = 4,
		.max_hops = 100,
		.distance_internal = 90,
		.distance_external = 170,
		.metric_version = 1,
		.active_time_seconds = 180,
		.topology_count_valid = true,
		.topology_prefix_count = 48,
		.redistributed_prefix_count = 3,
	};
	eigrp_result_t result;

	if (getenv("EIGRP_LINUX_CTL_TEST_NO_CONFIG"))
		return EIGRP_RESULT_NOT_FOUND;
	if (getenv("EIGRP_LINUX_CTL_TEST_INACTIVE")) {
		state.runtime_present = false;
		state.shutdown = true;
		state.parent_shutdown = true;
		state.router_id_configured = false;
		state.router_id = 0;
		state.effective_router_id = 0;
	}
	result = fn(&state, arg);
	if (result != EIGRP_RESULT_SUCCESS)
		return result;
	if (getenv("EIGRP_LINUX_CTL_TEST_VRF")) {
		state.instance_name = "blue-instance";
		state.vrf_name = "blue";
		state.runtime_present =
			getenv("EIGRP_LINUX_CTL_TEST_VRF_RUNTIME") != NULL;
		state.shutdown = !state.runtime_present;
		state.parent_shutdown = false;
		result = fn(&state, arg);
		if (result != EIGRP_RESULT_SUCCESS)
			return result;
		state.instance_name = "red-instance";
		state.vrf_name = "red";
		state.runtime_present = false;
		state.shutdown = true;
		result = fn(&state, arg);
		if (result != EIGRP_RESULT_SUCCESS)
			return result;
	}
	if (getenv("EIGRP_LINUX_CTL_TEST_MANY")) {
		unsigned i;
		char name[32];

		for (i = 0; i < 140; ++i) {
			(void)snprintf(name, sizeof(name), "bulk-%03u", i);
			state.instance_name = name;
			state.vrf_name = "default";
			state.afi = EIGRP_AFI_IPV4;
			state.asn = (uint16_t)(5000 + i);
			state.runtime_present = true;
			result = fn(&state, arg);
			if (result != EIGRP_RESULT_SUCCESS)
				return result;
		}
		return result;
	}
	if (!getenv("EIGRP_LINUX_CTL_TEST_MULTI"))
		return result;
	state.vrf_name = "default";
	state.shutdown = false;
	state.runtime_present = true;
	state.instance_name = "v6-test";
	state.afi = EIGRP_AFI_IPV6;
	state.asn = 6473;
	return fn(&state, arg);
}

eigrp_result_t eigrp_af_config_context_read(const char *name, eigrp_afi_t afi,
					    const char *vrf, uint16_t asn,
					    eigrp_instance_context_t *context)
{
	if (getenv("EIGRP_LINUX_CTL_TEST_MANY") && afi == EIGRP_AFI_IPV4
	    && strcmp(vrf, "default") == 0 && asn >= 5000 && asn < 5140) {
		char expected[32];

		(void)snprintf(expected, sizeof(expected), "bulk-%03u",
			       asn - 5000);
		if (strcmp(name, expected) == 0) {
			*context = (eigrp_instance_context_t){
				.config = (eigrp_af_config_t *)&config_identity,
				.runtime =
					(eigrp_instance_t *)&runtime_identity};
			return EIGRP_RESULT_SUCCESS;
		}
	}
	if (!((strcmp(vrf, "default") == 0
	       && ((strcmp(name, "eigrprulez") == 0 && afi == EIGRP_AFI_IPV4
		    && asn == 4453)
		   || (getenv("EIGRP_LINUX_CTL_TEST_MULTI")
		       && strcmp(name, "v6-test") == 0 && afi == EIGRP_AFI_IPV6
		       && asn == 6473)))
	      || (getenv("EIGRP_LINUX_CTL_TEST_VRF") && afi == EIGRP_AFI_IPV4
		  && asn == 4453
		  && ((strcmp(name, "blue-instance") == 0
		       && strcmp(vrf, "blue") == 0)
		      || (strcmp(name, "red-instance") == 0
			  && strcmp(vrf, "red") == 0)))))
		return EIGRP_RESULT_NOT_FOUND;
	*context = (eigrp_instance_context_t){
		.config = (eigrp_af_config_t *)&config_identity,
		.runtime =
			strcmp(vrf, "default") != 0
				? (strcmp(vrf, "blue") == 0
						   && getenv(
							   "EIGRP_LINUX_CTL_TEST_VRF_RUNTIME")
					   ? (eigrp_instance_t
						      *)&other_runtime_identity
					   : NULL)
				: (getenv("EIGRP_LINUX_CTL_TEST_INACTIVE")
					   ? NULL
					   : (eigrp_instance_t
						      *)&runtime_identity),
	};
	return EIGRP_RESULT_SUCCESS;
}

eigrp_vrf_id_t eigrp_instance_vrf_id(const eigrp_instance_t *instance)
{
	return instance == (eigrp_instance_t *)&runtime_identity ? 0 : 22;
}

eigrp_result_t eigrp_intf_state_iterate(eigrp_af_config_t *config,
					eigrp_instance_t *runtime,
					const char *name,
					eigrp_intf_state_iterate_cb fn,
					void *arg)
{
	eigrp_intf_state_t state = {
		.interface_name = "eth\"0\nsecond",
		.config_present = true,
		.runtime_present = true,
		.multicast_enabled = true,
		.peer_count = 2,
		.up_peer_count = 1,
		.mean_srtt_valid = true,
		.mean_srtt_msec = 42,
		.bandwidth = 100000,
		.delay = 10,
		.bandwidth_percent_configured = true,
		.bandwidth_percent = 60,
		.hello_interval_configured = true,
		.hold_time_configured = true,
		.reliability = 240,
		.load = 5,
		.tlv1_peer_count = 1,
		.tlv2_peer_count = 1,
		.authentication_configured = true,
		.authentication_mode = 2,
		.split_horizon = true,
		.next_hop_self = true,
		.hello_timer_running = true,
		.hello_timer_remaining = 3,
		.unreliable_multicast_sent = 111,
		.reliable_multicast_sent = 222,
		.unreliable_unicast_sent = 333,
		.reliable_unicast_sent = 444,
		.multicast_exceptions = 5,
		.cr_packets_sent = 6,
		.retransmissions_sent = 7,
		.pacing_tx_bytes = 8000,
		.pacing_defer_count = 9,
		.pacing_defer_msec = 10,
		.hello_interval = 5,
		.hold_time = 15,
		.mtu = 1500,
	};
	(void)config;
	if (getenv("EIGRP_LINUX_CTL_TEST_NO_INTERFACES"))
		return EIGRP_RESULT_NOT_FOUND;
	state.runtime_present = runtime != NULL;
	(void)name;
	return fn(&state, arg);
}

eigrp_result_t eigrp_nbr_state_iterate(eigrp_af_config_t *config,
				       eigrp_instance_t *runtime,
				       const char *name, bool static_only,
				       eigrp_nbr_state_iterate_cb fn, void *arg)
{
	eigrp_nbr_state_t state = {
		.interface_name = "eth0",
		.state_name = "up",
		.runtime_present = true,
		.hold_time = 12,
		.uptime_seconds = 600,
		.prefix_count = 2,
		.srtt_valid = true,
		.srtt_msec = 33,
		.rto_msec = 500,
		.sequence_number = 123,
		.retry_count = 3,
		.os_major = 15,
		.os_minor = 2,
		.tlv_major = 1,
		.tlv_minor = 4,
		.tlv_version = 2,
		.retransmit_count = 7,
	};
	(void)config;
	(void)name;
	if (!runtime && !static_only)
		return EIGRP_RESULT_NOT_FOUND;
	state.address = fixture_addr("192.0.2.2");
	if (static_only) {
		state.static_configured = true;
		state.address = fixture_addr("192.0.2.9");
		state.interface_name = "eth1";
		state.state_name = "configured";
	}
	if (getenv("EIGRP_LINUX_CTL_TEST_PENALTY")) {
		state.limit_status = 'P';
		state.limit_restart_cnt = 2;
		state.limit_restart_seconds = 60;
		state.limit_reset_seconds = 120;
	}
	return atomic_load(&neighbors_cleared) ? EIGRP_RESULT_SUCCESS
					       : fn(&state, arg);
}

eigrp_result_t
eigrp_topology_state_iterate(eigrp_af_config_t *config,
			     eigrp_instance_t *runtime,
			     const eigrp_prefix_t *destination, bool all_links,
			     eigrp_topology_prefix_state_cb prefix_fn,
			     eigrp_topology_route_state_cb route_fn, void *arg)
{
	unsigned int i;
	(void)config;
	(void)destination;
	(void)all_links;
	if (!runtime)
		return EIGRP_RESULT_NOT_FOUND;
	if (atomic_load(&topology_cleared))
		return EIGRP_RESULT_SUCCESS;
	for (i = 1; i <= (getenv("EIGRP_LINUX_CTL_TEST_LARGE") ? 4100U : 48U);
	     i++) {
		eigrp_topology_prefix_state_t prefix = {
			.destination = {.address = {.afi = EIGRP_AFI_IPV4},
					.prefix_length = 32},
			.feasible_distance = i * 256,
			.successor_count = 1,
			.serial_number = i,
			.dual_state = 2,
			.pending_replies = 3,
		};
		eigrp_topology_route_state_t route = {
			.next_hop = fixture_addr("192.0.2.2"),
			.interface_name = "eth0",
			.successor = true,
			.distance = i * 256,
			.reported_distance = i * 128,
			.total_metric =
				{
					.wide_bandwidth = 50000,
					.wide_delay = 5000000,
					.mtu = {0xdc, 0x05, 0x00},
					.reliability = 230,
					.load = 7,
					.hop_count = 2,
					.tag = 12,
					.flags = 3,
				},
			.reported_metric =
				{
					.wide_bandwidth = 10000,
					.wide_delay = 1000000,
					.hop_count = 1,
					.tag = 24,
					.flags = 4,
				},
		};
		eigrp_result_t result;
		prefix.destination.address.bytes[0] = 198;
		prefix.destination.address.bytes[1] = 51;
		prefix.destination.address.bytes[2] = 100 + (i - 1) / 254;
		prefix.destination.address.bytes[3] = 1 + (i - 1) % 254;
		result = prefix_fn(&prefix, arg);
		if (result != EIGRP_RESULT_SUCCESS)
			return result;
		result = route_fn(&route, arg);
		if (result != EIGRP_RESULT_SUCCESS)
			return result;
		if (i == 48 && getenv("EIGRP_LINUX_CTL_TEST_PATHS")) {
			route.successor = false;
			route.reported_distance = EIGRP_MAX_METRIC;
			route.next_hop = fixture_addr("192.0.2.3");
			result = route_fn(&route, arg);
			if (result != EIGRP_RESULT_SUCCESS)
				return result;
			route.reported_distance = i * 64;
			route.next_hop = fixture_addr("192.0.2.4");
			result = route_fn(&route, arg);
			if (result != EIGRP_RESULT_SUCCESS)
				return result;
		}
	}
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_stat_traffic_read(const eigrp_instance_context_t *context,
				       eigrp_statistics_traffic_state_t *state)
{
	(void)context;
	memset(state, 0, sizeof(*state));
	state->sent_valid = 127;
	state->sent_hello = 900;
	state->received_update = 12;
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t
eigrp_stat_pfxlimits_summary_read(const eigrp_instance_context_t *context,
				  eigrp_pfxlimits_summary_t *state)
{
	(void)context;
	*state = (eigrp_pfxlimits_summary_t){
		.process = {.state = 1,
			    .prefix_cnt = 48,
			    .restart_cnt = 2,
			    .restart_seconds = 30,
			    .reset_seconds = 45,
			    .penalty_q16 = 1234},
		.redist = {.state = 2,
			   .prefix_cnt = 3,
			   .restart_cnt = 4,
			   .restart_seconds = 50,
			   .reset_seconds = 60,
			   .penalty_q16 = 5678},
	};
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t
eigrp_stat_accounting_iterate(const eigrp_instance_context_t *context,
			      uint32_t *total_prefix_count,
			      eigrp_stat_accounting_cb callback, void *arg)
{
	eigrp_stat_nbr_limit_t state = {
		.interface_name = "eth0",
		.status = 'P',
		.prefix_count = 12,
		.restart_cnt = 2,
		.restart_seconds = 30,
		.reset_seconds = 45,
	};
	(void)context;
	*total_prefix_count = 48;
	state.neighbor_address = fixture_addr("192.0.2.2");
	return callback(&state, arg);
}

eigrp_result_t
eigrp_eventlog_state_read(const eigrp_instance_context_t *context,
			  eigrp_eventlog_state_t *state)
{
	(void)context;
	*state = (eigrp_eventlog_state_t){
		.capacity = 500,
		.count = atomic_load(&clear_count) == 0 ? 1 : 0,
		.written = 1};
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t
eigrp_eventlog_msg_iterate(const eigrp_instance_context_t *context,
			   eigrp_eventlog_msg_cb cb, void *arg)
{
	eigrp_eventlog_msg_t msg = {.timestamp = 123456789, .opcode = 55};
	(void)context;
	if (atomic_load(&clear_count))
		return EIGRP_RESULT_SUCCESS;
	return cb(1, &msg, "event", arg);
}

eigrp_result_t eigrp_eventlog_msg_format(const eigrp_eventlog_msg_t *msg,
					 char *out, size_t len)
{
	(void)msg;
	(void)snprintf(out, len, "event\"\nnewline");
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_eventlog_clear(eigrp_instance_context_t *context)
{
	(void)context;
	atomic_fetch_add(&clear_count, 1);
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_nbr_clear(eigrp_instance_t *runtime,
			       const eigrp_nbr_clear_request_t *request,
			       eigrp_nbr_clear_cb callback, void *arg,
			       size_t *affected_count)
{
	const eigrp_address_t match = fixture_addr("192.0.2.2");
	(void)callback;
	(void)arg;
	if (runtime != (eigrp_instance_t *)&runtime_identity || !request)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	if (request->interface_name
	    && strcmp(request->interface_name, "eth0") != 0)
		return EIGRP_RESULT_NOT_FOUND;
	if (request->address
	    && memcmp(request->address, &match, sizeof(match)) != 0)
		return EIGRP_RESULT_NOT_FOUND;
	if (affected_count)
		*affected_count = 1;
	if (!request->soft)
		atomic_fetch_add(&neighbors_cleared, 1);
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t
eigrp_topology_clear(eigrp_instance_context_t *context,
		     const eigrp_topology_clear_request_t *request,
		     size_t *affected_count)
{
	if (!context || !context->runtime || !request)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	if (request->destination) {
		const eigrp_prefix_t *dest = request->destination;
		if (dest->address.afi != EIGRP_AFI_IPV4
		    || dest->prefix_length != 32
		    || dest->address.bytes[0] != 198
		    || dest->address.bytes[1] != 51
		    || dest->address.bytes[2] != 100
		    || dest->address.bytes[3] != 48)
			return EIGRP_RESULT_NOT_FOUND;
	}
	if (affected_count)
		*affected_count = 1;
	atomic_fetch_add(&topology_cleared, 1);
	return EIGRP_RESULT_SUCCESS;
}

size_t eigrp_debug_af_state_count(void)
{
	return 1;
}

bool eigrp_debug_af_state_read(eigrp_debug_scope_t scope, size_t index,
			       eigrp_debug_af_state_t *state)
{
	if (scope != EIGRP_DEBUG_SCOPE_CONFIG || index != 0)
		return false;
	memset(state, 0, sizeof(*state));
	state->afi = EIGRP_AFI_IPV4;
	state->asn = 4453;
	state->category = EIGRP_DEBUG_AF_NEIGHBOR;
	return true;
}

eigrp_result_t
eigrp_timer_state_iterate(const eigrp_instance_context_t *context,
			  eigrp_timer_state_cb cb, void *arg)
{
	eigrp_timer_state_t state = {.type = EIGRP_TIMER_STATE_HELLO,
				     .interface_name = "eth0",
				     .expiration_seconds = 4,
				     .expiration_msec = 4123};
	eigrp_result_t result;
	(void)context;
	result = cb(&state, arg);
	if (result != EIGRP_RESULT_SUCCESS
	    || !getenv("EIGRP_LINUX_CTL_TEST_TIMERS"))
		return result;
	state.type = EIGRP_TIMER_STATE_PEER_HOLD;
	state.neighbor_present = true;
	state.neighbor_address = fixture_addr("192.0.2.2");
	state.expiration_msec = 2200;
	result = cb(&state, arg);
	if (result != EIGRP_RESULT_SUCCESS)
		return result;
	state.type = EIGRP_TIMER_STATE_ACTIVE;
	state.destination.address = fixture_addr("198.51.100.48");
	state.destination.prefix_length = 32;
	state.expiration_msec = 3250;
	return cb(&state, arg);
}

int main(int argc, char **argv)
{
	int c;
	gid_t group;
	mode_t mode = 0660;
	char *end;
	unsigned long value;

	if (argc < 2 || argc > 4)
		return 2;
	group = geteuid() == 0 ? 65534 : getegid();
	if (argc >= 3) {
		value = strtoul(argv[2], &end, 8);
		if (!argv[2][0] || *end || (value != 0600 && value != 0660))
			return 2;
		mode = (mode_t)value;
	}
	if (argc == 4) {
		value = strtoul(argv[3], &end, 10);
		if (!argv[3][0] || *end || value > UINT32_MAX)
			return 2;
		group = (gid_t)value;
	}
	if (eigrp_linux_mgnt_start(argv[1], group, mode, fixture_execute, NULL)
	    != 0) {
		perror("eigrp_linux_mgnt_start");
		return 1;
	}
	(void)signal(SIGTERM, fixture_signal);
	(void)signal(SIGINT, fixture_signal);
	puts("READY");
	fflush(stdout);
	while (!shutdown_requested && (c = getchar()) != EOF)
		if (c == 'q')
			break;
	eigrp_linux_mgnt_stop();
	eigrp_linux_mgnt_stop();
	puts("STOPPED");
	return 0;
}

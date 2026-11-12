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
#include "eigrp_linux_mgnt.h"
#include "eigrp_mgnt.h"

static int runtime_identity;
static int config_identity;
static atomic_uint clear_count;
static volatile sig_atomic_t shutdown_requested;

/* Fixture models the AF-owner dispatch contract without linking a core AF. */
eigrp_result_t eigrp_mgnt_runtime_execute(eigrp_instance_t *runtime,
					  eigrp_mgnt_runtime_cb callback,
					  void *arg)
{
	if (runtime != (eigrp_instance_t *)&runtime_identity)
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
	};
	return fn(&state, arg);
}

eigrp_result_t eigrp_af_config_context_read(const char *name, eigrp_afi_t afi,
					    const char *vrf, uint16_t asn,
					    eigrp_instance_context_t *context)
{
	if (strcmp(name, "eigrprulez") != 0 || afi != EIGRP_AFI_IPV4
	    || strcmp(vrf, "default") != 0 || asn != 4453)
		return EIGRP_RESULT_NOT_FOUND;
	*context = (eigrp_instance_context_t){
		.config = (eigrp_af_config_t *)&config_identity,
		.runtime = (eigrp_instance_t *)&runtime_identity,
	};
	return EIGRP_RESULT_SUCCESS;
}

eigrp_vrf_id_t eigrp_instance_vrf_id(const eigrp_instance_t *instance)
{
	return instance == (eigrp_instance_t *)&runtime_identity ? 0 : 1;
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
		.peer_count = 1,
		.hello_interval = 5,
		.hold_time = 15,
		.mtu = 1500,
	};
	(void)config;
	(void)runtime;
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
	};
	(void)config;
	(void)runtime;
	(void)name;
	(void)static_only;
	state.address = fixture_addr("192.0.2.2");
	return fn(&state, arg);
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
	(void)runtime;
	(void)destination;
	(void)all_links;
	for (i = 1; i <= 48; i++) {
		eigrp_topology_prefix_state_t prefix = {
			.destination = {.address = {.afi = EIGRP_AFI_IPV4},
					.prefix_length = 32},
			.feasible_distance = i * 256,
			.successor_count = 1,
			.serial_number = i,
		};
		eigrp_topology_route_state_t route = {
			.next_hop = fixture_addr("192.0.2.2"),
			.interface_name = "eth0",
			.successor = true,
			.distance = i * 256,
			.reported_distance = i * 128,
		};
		eigrp_result_t result;
		prefix.destination.address.bytes[0] = 198;
		prefix.destination.address.bytes[1] = 51;
		prefix.destination.address.bytes[2] = 100;
		prefix.destination.address.bytes[3] = i;
		result = prefix_fn(&prefix, arg);
		if (result != EIGRP_RESULT_SUCCESS)
			return result;
		result = route_fn(&route, arg);
		if (result != EIGRP_RESULT_SUCCESS)
			return result;
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
				     .expiration_seconds = 4};
	(void)context;
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

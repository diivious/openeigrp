// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Cross-module EIGRP status aggregation targets.
 *
 * Copyright (C) 2026 Donnie V. Savage
 */

#include <string.h>

#include "eigrp_status.h"
#include "eigrp_features.h"
#include "eigrp_instance.h"
#include "eigrp_structs.h"
#include "eigrp_timer.h"

eigrp_result_t
eigrp_status_capability_state_read(eigrp_status_capability_state_t *state)
{
	if (!state)
		return EIGRP_RESULT_INVALID_ARGUMENT;

	*state = (eigrp_status_capability_state_t){
		.release = EIGRP_RELEASE,
		.tlv1 = true,
		.tlv2 = true,
		.wide_metrics = true,
		.ipv4 = eigrp_feature_supported(EIGRP_FEATURE_AFI_IPV4),
		.ipv6 = eigrp_feature_supported(EIGRP_FEATURE_AFI_IPV6),
		.bfd = eigrp_feature_supported(EIGRP_FEATURE_BFD),
		.snmp = eigrp_feature_supported(EIGRP_FEATURE_SNMP),
	};
	return EIGRP_RESULT_SUCCESS;
}

struct eigrp_status_iterate_context {
	eigrp_status_protocol_cb callback;
	void *arg;
};

struct eigrp_status_runtime_request {
	eigrp_instance_t *runtime;
	eigrp_status_protocol_state_t *state;
};

static eigrp_result_t eigrp_status_runtime_read(void *arg)
{
	struct eigrp_status_runtime_request *request = arg;
	const eigrp_instance_t *runtime = request->runtime;
	eigrp_status_protocol_state_t *state = request->state;

	state->effective_router_id = ntohl(runtime->router_id.s_addr);
	memcpy(state->k_values, runtime->k_values, sizeof(state->k_values));
	state->variance = runtime->variance;
	state->max_paths = runtime->max_paths;
	state->max_hops = runtime->max_hops;
	state->distance_internal = runtime->distance_internal;
	state->distance_external = runtime->distance_external;
	state->metric_version = runtime->metric_version;
	state->active_time_seconds = eigrp_timer_active_time_seconds(runtime);
	state->topology_count_valid = runtime->topology_reachable_count_valid;
	state->topology_prefix_count = runtime->topology_reachable_count;
	state->redistributed_prefix_count = runtime->redist_prefix_count;
	return EIGRP_RESULT_SUCCESS;
}

static eigrp_result_t eigrp_status_af(const char *instance_name,
				      eigrp_af_config_t *af, void *arg)
{
	struct eigrp_status_iterate_context *context = arg;
	eigrp_named_config_t *parent = eigrp_named_config_read(instance_name);
	eigrp_status_protocol_state_t state = {
		.instance_name = instance_name,
		.config = af,
		.afi = af->afi,
		.vrf_name = af->vrf_name,
		.asn = af->asn,
		.shutdown = af->shutdown,
		.parent_shutdown = parent && parent->shutdown,
		.router_id_configured = af->router_id_configured,
		.router_id = af->router_id,
		.runtime_present = false,
	};

	if (af->runtime && !state.shutdown && !state.parent_shutdown) {
		struct eigrp_status_runtime_request request = {
			.runtime = af->runtime,
			.state = &state,
		};
		eigrp_result_t result = eigrp_mgnt_runtime_execute(
			af->runtime, eigrp_status_runtime_read, &request);
		if (result == EIGRP_RESULT_SUCCESS)
			state.runtime_present = true;
		else if (result != EIGRP_RESULT_CONFLICT)
			return result;
	}
	return context->callback(&state, context->arg);
}

static eigrp_result_t eigrp_status_iterate(eigrp_status_protocol_cb callback,
					   void *arg)
{
	struct eigrp_status_iterate_context context = {
		.callback = callback,
		.arg = arg,
	};
	eigrp_state_request_t request = {
		.all_vrfs = true,
	};
	eigrp_result_t ipv4_result = EIGRP_RESULT_NOT_FOUND;
	eigrp_result_t ipv6_result = EIGRP_RESULT_NOT_FOUND;

	if (!callback)
		return EIGRP_RESULT_INVALID_ARGUMENT;

	if (eigrp_afi_supported(EIGRP_AFI_IPV4)) {
		request.afi = EIGRP_AFI_IPV4;
		ipv4_result = eigrp_af_config_iterate(&request, eigrp_status_af,
						      &context);
		if (ipv4_result != EIGRP_RESULT_SUCCESS
		    && ipv4_result != EIGRP_RESULT_NOT_FOUND)
			return ipv4_result;
	}

	if (eigrp_afi_supported(EIGRP_AFI_IPV6)) {
		request.afi = EIGRP_AFI_IPV6;
		ipv6_result = eigrp_af_config_iterate(&request, eigrp_status_af,
						      &context);
		if (ipv6_result != EIGRP_RESULT_SUCCESS
		    && ipv6_result != EIGRP_RESULT_NOT_FOUND)
			return ipv6_result;
	}

	if (ipv4_result == EIGRP_RESULT_NOT_FOUND
	    && ipv6_result == EIGRP_RESULT_NOT_FOUND)
		return EIGRP_RESULT_NOT_FOUND;
	return EIGRP_RESULT_SUCCESS;
}

/*
 * Syntax:
 *   EXEC: `show eigrp protocols`
 * Supported: EXEC
 * Placement:
 *   Operational/read-only
 * Description:
 * Aggregates configured/running EIGRP protocol instances for display.
 * The target walks common address-family state and leaves VTY formatting to
 * FRR.
 */
eigrp_result_t eigrp_status_protocol_iterate(eigrp_status_protocol_cb callback,
					     void *arg)
{
	return eigrp_status_iterate(callback, arg);
}

/*
 * Syntax:
 *   EXEC: `show eigrp tech-support`
 * Supported: EXEC
 * Placement:
 *   Operational/read-only
 * Description:
 * Exports EIGRP protocol state used by the technical-support display.
 * The portable target remains independent of FRR VTY formatting.
 */
eigrp_result_t
eigrp_status_tech_support_iterate(eigrp_status_protocol_cb callback, void *arg)
{
	return eigrp_status_iterate(callback, arg);
}

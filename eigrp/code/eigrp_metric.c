// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * EIGRP Metric Math Functions.
 * Copyright (C) 2013-2016, 2026
 * Authors:
 *   Donnie Savage
 */
#include <stdlib.h>
#include <string.h>

#include "eigrp.h"
#include "eigrp_structs.h"
#include "eigrp_metric.h"
#include "eigrp_redistribute.h"
#include "eigrp_neighbor.h"
#include "eigrp_interface.h"
#include "eigrp_packet.h"
#include "eigrp_eventlog.h"
#include "eigrp_timer.h"
#include "eigrp_topology.h"

struct eigrp_metric_config {
	bool default_metric_configured;
	eigrp_metric_values_t default_metric;
	bool weights_configured;
	eigrp_metric_weights_t weights;
	bool variance_configured;
	uint8_t variance;
	bool traffic_share_balanced;
	bool maximum_hops_configured;
	uint8_t maximum_hops;
	bool version_32bit;
};


void eigrp_metric_values_convert(const eigrp_metric_values_t *values,
				 eigrp_metrics_t *metric)
{
	if (!metric)
		return;
	memset(metric, 0, sizeof(*metric));
	if (!values)
		return;

	/* CLI seeds are expressed in Kbit/s and 10-microsecond delay units.
	 * Route vectors retain scaled classic components, including TLV1.
	 */
	metric->bandwidth = eigrp_bandwidth_to_scaled(values->bandwidth);
	metric->delay = eigrp_delay_to_scaled(values->delay);
	metric->wide_bandwidth = values->bandwidth;
	metric->wide_delay = eigrp_metric_delay_classic2wide(
		values->delay, values->bandwidth, false);
	metric->reliability = values->reliability;
	metric->load = values->load;
	metric->mtu[0] = values->mtu & 0xff;
	metric->mtu[1] = (values->mtu >> 8) & 0xff;
}

void eigrp_metric_link_set(eigrp_metrics_t *metric, eigrp_bandwidth_t bandwidth,
			   eigrp_delay_t delay, bool interface_default)
{
	if (!metric)
		return;
	metric->bandwidth = eigrp_bandwidth_to_scaled(bandwidth);
	metric->delay = eigrp_delay_to_scaled(
		interface_default && bandwidth >= EIGRP_GIGABIT_KBPS
			? EIGRP_GIGABIT_CLASSIC_DELAY
			: delay);
	metric->wide_bandwidth = bandwidth;
	metric->wide_delay = eigrp_metric_delay_classic2wide(delay, bandwidth,
							     interface_default);
}

/* RFC 7868 section 5.6.1.2: the default classic delay is 10,000 ns
 * at 1 Gbit/s and above. Wide delay reflects the actual link speed.
 * Only the interface-default value is translated this way. Explicitly
 * configured (non-default) classic delays retain their requested duration.
 * Keep the picosecond conversion before division to avoid truncation.
 */
eigrp_delay_t eigrp_metric_delay_classic2wide(eigrp_delay_t delay,
					      eigrp_bandwidth_t bandwidth,
					      bool interface_default)
{
	if (interface_default && bandwidth >= EIGRP_GIGABIT_KBPS)
		return (EIGRP_CLASSIC_DELAY_PICO * EIGRP_GIGABIT_KBPS)
		       / bandwidth;
	if (delay > (EIGRP_TLV2_INACCESSIBLE - 1) / EIGRP_CLASSIC_DELAY_PICO)
		return EIGRP_TLV2_INACCESSIBLE;
	return delay * EIGRP_CLASSIC_DELAY_PICO;
}

/* A sub-10-us high-speed default cannot be represented in classic units.
 * Reconstitute the canonical classic default for that physical bandwidth;
 * other values are truncated in 10-us units, consistent with TLV1.
 */
eigrp_delay_t eigrp_metric_delay_wide2classic(eigrp_delay_t delay,
					      eigrp_bandwidth_t bandwidth)
{
	if (bandwidth >= EIGRP_GIGABIT_KBPS
	    && delay
		       == (EIGRP_CLASSIC_DELAY_PICO * EIGRP_GIGABIT_KBPS)
				  / bandwidth)
		return EIGRP_GIGABIT_CLASSIC_DELAY;
	return delay / EIGRP_CLASSIC_DELAY_PICO;
}

eigrp_scaled_t eigrp_bandwidth_to_scaled(eigrp_bandwidth_t bandwidth)
{
	eigrp_bandwidth_t scaled = EIGRP_BANDWIDTH_MAX;

	if (!bandwidth)
		return EIGRP_CLASSIC_MAX;

	if (bandwidth != EIGRP_BANDWIDTH_MAX) {
		/* Cisco classic arithmetic truncates the inverse bandwidth
		 * before applying the 256 scaling factor.
		 */
		scaled = EIGRP_BANDWIDTH / bandwidth;
		scaled *= EIGRP_CLASSIC_SCALE;

		scaled = scaled ? scaled : EIGRP_BANDWIDTH_MIN;
	}

	scaled = (scaled < EIGRP_METRIC_MAX) ? scaled : EIGRP_METRIC_MAX;
	return (eigrp_scaled_t)scaled;
}

eigrp_bandwidth_t eigrp_scaled_to_bandwidth(eigrp_scaled_t scaled)
{
	eigrp_bandwidth_t bandwidth;

	if (!scaled || scaled >= EIGRP_CLASSIC_MAX)
		return EIGRP_BANDWIDTH_MAX;

	/* Invert the scaled classic component before truncation. */
	bandwidth = (EIGRP_CLASSIC_SCALE * EIGRP_BANDWIDTH) / scaled;
	return bandwidth ? bandwidth : EIGRP_BANDWIDTH_MIN;
}

eigrp_scaled_t eigrp_delay_to_scaled(eigrp_delay_t delay)
{
	if (delay == EIGRP_MAX_METRIC || delay == EIGRP_METRIC_MAX)
		return EIGRP_MAX_METRIC;
	delay = delay ? delay : EIGRP_DELAY_MIN;
	return delay * EIGRP_CLASSIC_SCALE;
}

eigrp_delay_t eigrp_scaled_to_delay(eigrp_scaled_t scaled)
{
	if (scaled == EIGRP_MAX_METRIC)
		return EIGRP_MAX_METRIC;
	scaled = scaled / EIGRP_CLASSIC_SCALE;
	scaled = scaled ? scaled : EIGRP_DELAY_MIN;

	return scaled;
}

eigrp_metric_t eigrp_metric_calculate(eigrp_instance_t *eigrp,
				      eigrp_metrics_t metric)
{
	uint64_t throughput = 0;
	uint64_t latency = 0;
	uint64_t composite;
	uint64_t scale;

	if (!eigrp || metric.delay == EIGRP_METRIC_MAX)
		return EIGRP_METRIC_MAX;
	if (metric.delay == EIGRP_MAX_METRIC)
		return EIGRP_MAX_METRIC;
	if (metric.wide_bandwidth
	    && (metric.wide_delay >= EIGRP_TLV2_INACCESSIBLE
		|| metric.wide_bandwidth >= EIGRP_TLV2_INACCESSIBLE))
		return EIGRP_MAX_METRIC;

	/* Cisco TLV2 carries actual kbit/s and picoseconds, not classic
	 * inverse-bandwidth/delay values. Retain each integer quotient's
	 * precision instead of scaling an already-truncated classic vector.
	 */
	if (eigrp->metric_version >= EIGRP_TLV_64B_VERSION
	    && metric.wide_bandwidth) {
		uint64_t bw = (EIGRP_BANDWIDTH * (uint64_t)EIGRP_WIDE_SCALE)
			      / metric.wide_bandwidth;
		uint64_t delay = (metric.wide_delay / EIGRP_DELAY_PICO)
					 * EIGRP_WIDE_SCALE
				 + (metric.wide_delay % EIGRP_DELAY_PICO)
					   * EIGRP_WIDE_SCALE
					   / EIGRP_DELAY_PICO;

		throughput = (uint64_t)eigrp->k_values[0] * bw;
		if (eigrp->k_values[1])
			throughput += ((uint64_t)eigrp->k_values[1] * bw)
				      / (EIGRP_MAX_LOAD - metric.load);
		latency = (uint64_t)eigrp->k_values[2] * delay;
	} else {
		/* Classic vectors already contain scaled bandwidth and delay.
		 */
		scale = eigrp->metric_version >= EIGRP_TLV_64B_VERSION
				? (EIGRP_WIDE_SCALE / EIGRP_CLASSIC_SCALE)
				: 1;
		if (eigrp->k_values[0])
			throughput = (uint64_t)eigrp->k_values[0]
				     * metric.bandwidth * scale;
		if (eigrp->k_values[1]) {
			/* TLV1 bandwidth is already scaled by 256. */
			uint64_t weighted = eigrp->k_values[1];
			weighted *= metric.bandwidth;
			weighted /= EIGRP_MAX_LOAD - metric.load;
			throughput += weighted * scale;
		}
		if (eigrp->k_values[2])
			latency = (uint64_t)eigrp->k_values[2] * metric.delay
				  * scale;
	}

	composite = throughput + latency;
	/* K6 controls RFC 7868 extended attributes. This implementation does
	 * not yet source jitter/energy, so ExtAttr is correctly zero. */
	if (eigrp->k_values[4]) {
		uint64_t denominator =
			(uint64_t)metric.reliability + eigrp->k_values[3];
		if (!denominator || composite > UINT64_MAX / eigrp->k_values[4])
			return EIGRP_MAX_METRIC;
		composite = (composite * eigrp->k_values[4]) / denominator;
	}

	if (eigrp->metric_version < EIGRP_TLV_64B_VERSION
	    && composite > EIGRP_CLASSIC_MAX)
		return EIGRP_CLASSIC_MAX;
	return composite > EIGRP_METRIC_MAX ? EIGRP_METRIC_MAX : composite;
}

eigrp_metric_t eigrp_metric_total_calculate(eigrp_instance_t *eigrp,
					    eigrp_route_descriptor_t *entry)
{
	eigrp_intf_t *ei = entry->ei;
	eigrp_delay_t link_delay;
	eigrp_bandwidth_t bw;
	bool interface_default;

	entry->total_metric = entry->reported_metric;
	if (entry->reported_metric.delay == EIGRP_MAX_METRIC) {
		entry->total_metric.delay = EIGRP_MAX_METRIC;
		return EIGRP_MAX_METRIC;
	}
	if (entry->reported_metric.delay == EIGRP_METRIC_MAX) {
		entry->total_metric.delay = EIGRP_METRIC_MAX;
		return EIGRP_METRIC_MAX;
	}
	interface_default = ei->params.bandwidth >= EIGRP_GIGABIT_KBPS
			    && eigrp_intf_delay_is_default(ei);
	link_delay = eigrp_delay_to_scaled(
		interface_default && ei->params.bandwidth >= EIGRP_GIGABIT_KBPS
			? EIGRP_GIGABIT_CLASSIC_DELAY
			: ei->params.delay);
	if (entry->total_metric.delay >= EIGRP_METRIC_MAX - link_delay)
		entry->total_metric.delay = EIGRP_METRIC_MAX;
	else
		entry->total_metric.delay += link_delay;

	if (entry->total_metric.hop_count == UINT8_MAX)
		entry->total_metric.delay = EIGRP_METRIC_MAX;
	else
		entry->total_metric.hop_count++;
	if (entry->total_metric.hop_count > eigrp->max_hops)
		entry->total_metric.delay = EIGRP_METRIC_MAX;

	bw = eigrp_bandwidth_to_scaled(ei->params.bandwidth);
	entry->total_metric.bandwidth = (bw > entry->total_metric.bandwidth)
						? bw
						: entry->total_metric.bandwidth;
	if (eigrp->metric_version >= EIGRP_TLV_64B_VERSION
	    && entry->total_metric.wide_bandwidth) {
		eigrp_delay_t link = eigrp_metric_delay_classic2wide(
			ei->params.delay, ei->params.bandwidth,
			interface_default);

		if (link >= EIGRP_TLV2_INACCESSIBLE
		    || entry->total_metric.wide_delay
			       >= EIGRP_TLV2_INACCESSIBLE - link) {
			entry->total_metric.delay = EIGRP_MAX_METRIC;
			return EIGRP_MAX_METRIC;
		}
		entry->total_metric.wide_delay += link;
		if (ei->params.bandwidth < entry->total_metric.wide_bandwidth)
			entry->total_metric.wide_bandwidth =
				ei->params.bandwidth;
	}

	return eigrp_metric_calculate(eigrp, entry->total_metric);
}

bool eigrp_metrics_match(eigrp_metrics_t metric1, eigrp_metrics_t metric2)
{
	if ((metric1.bandwidth == metric2.bandwidth)
	    && (metric1.delay == metric2.delay)
	    && (metric1.wide_bandwidth == metric2.wide_bandwidth)
	    && (metric1.wide_delay == metric2.wide_delay)
	    && (metric1.hop_count == metric2.hop_count)
	    && (metric1.load == metric2.load)
	    && (metric1.reliability == metric2.reliability)
	    && (metric1.mtu[0] == metric2.mtu[0])
	    && (metric1.mtu[1] == metric2.mtu[1])
	    && (metric1.mtu[2] == metric2.mtu[2])) {
		return TRUE;
	}

	return FALSE; // if different
}

static bool eigrp_metric_context_valid(const eigrp_instance_context_t *context)
{
	return context && (context->config || context->runtime);
}

static bool eigrp_metric_values_valid(const eigrp_metric_values_t *metric)
{
	return metric && metric->bandwidth && metric->load && metric->mtu;
}

static eigrp_metric_config_t *eigrp_metric_config_create(eigrp_af_config_t *af)
{
	if (!af)
		return NULL;
	if (!af->metric_config) {
		af->metric_config = calloc(1, sizeof(*af->metric_config));
		if (!af->metric_config)
			return NULL;
		af->metric_config->traffic_share_balanced = true;
	}
	return af->metric_config;
}

typedef enum eigrp_metric_message_type {
	EIGRP_METRIC_MESSAGE_DEFAULT,
	EIGRP_METRIC_MESSAGE_WEIGHTS,
	EIGRP_METRIC_MESSAGE_VARIANCE,
	EIGRP_METRIC_MESSAGE_TRAFFIC_SHARE,
	EIGRP_METRIC_MESSAGE_MAXIMUM_HOPS,
	EIGRP_METRIC_MESSAGE_VERSION,
} eigrp_metric_message_type_t;

typedef struct eigrp_metric_message_args {
	eigrp_metric_message_type_t type;
	eigrp_operation_t operation;
	eigrp_instance_context_t *context;
	union {
		const eigrp_metric_values_t *metric;
		const eigrp_metric_weights_t *weights;
		uint8_t value;
	} data;
} eigrp_metric_message_args_t;

static eigrp_result_t eigrp_metric_message_process(eigrp_instance_t *eigrp,
						   void *arg);

/*
 * Syntax:
 *   Named: `default-metric BW DELAY RELIABILITY LOAD MTU` / `no default-metric
 * ...` Supported: Named Placement: Named: topology base mode Description: Sets
 * or resets the default seed metric used by redistribution when a
 * source-specific metric is not supplied. Runtime redistribution fallback
 * remains owned by the metric/redistribution path.
 */
eigrp_result_t eigrp_metric_default_update(eigrp_operation_t operation,
					   eigrp_instance_context_t *context,
					   const eigrp_metric_values_t *metric)
{
	eigrp_metric_message_args_t message = {
		.type = EIGRP_METRIC_MESSAGE_DEFAULT,
		.operation = operation,
		.context = context,
		.data.metric = metric,
	};

	if (context && eigrp_instance_thread_dispatch_needed(context->runtime))
		return eigrp_instance_message_call(context->runtime,
						   eigrp_metric_message_process,
						   &message);
	if (operation == EIGRP_RESET) {
		if (!eigrp_metric_context_valid(context))
			return EIGRP_RESULT_NOT_FOUND;
		bool changed = context->config && context->config->metric_config
			       && context->config->metric_config
					  ->default_metric_configured;
		if (context->config && context->config->metric_config) {
			context->config->metric_config
				->default_metric_configured = false;
			memset(&context->config->metric_config->default_metric,
			       0,
			       sizeof(context->config->metric_config
					      ->default_metric));
		}
		return changed && context->runtime
			       ? eigrp_redist_default_refresh(context->runtime)
			       : EIGRP_RESULT_SUCCESS;
	}

	if (operation != EIGRP_SET)
		return EIGRP_RESULT_INVALID_ARGUMENT;

	eigrp_metric_config_t *config;

	if (!eigrp_metric_values_valid(metric))
		return EIGRP_RESULT_INVALID_ARGUMENT;
	if (!eigrp_metric_context_valid(context))
		return EIGRP_RESULT_NOT_FOUND;
	bool changed = false;
	if (context->config) {
		config = eigrp_metric_config_create(context->config);
		if (!config)
			return EIGRP_RESULT_INTERNAL_FAILURE;
		changed =
			!config->default_metric_configured
			|| config->default_metric.bandwidth != metric->bandwidth
			|| config->default_metric.delay != metric->delay
			|| config->default_metric.reliability
				   != metric->reliability
			|| config->default_metric.load != metric->load
			|| config->default_metric.mtu != metric->mtu;
		config->default_metric = *metric;
		config->default_metric_configured = true;
	}
	return changed && context->runtime
		       ? eigrp_redist_default_refresh(context->runtime)
		       : EIGRP_RESULT_SUCCESS;
}

/*
 * Syntax:
 *   Named: `default-metric BW DELAY RELIABILITY LOAD MTU` / `no default-metric
 * ...` Supported: Named Placement: Named: topology base mode Description: Sets
 * or resets the default seed metric used by redistribution when a
 * source-specific metric is not supplied. Runtime redistribution fallback
 * remains owned by the metric/redistribution path.
 */


bool eigrp_metric_default(const eigrp_af_config_t *af,
			  eigrp_metric_values_t *metric)
{
	if (!af || !metric || !af->metric_config
	    || !af->metric_config->default_metric_configured)
		return false;

	*metric = af->metric_config->default_metric;
	return true;
}

/*
 * Syntax:
 *   Classic: `metric weights TOS K1 K2 K3 K4 K5` / `no metric weights ...`
 *   Named: `metric weights TOS K1 K2 K3 K4 K5 [K6]` / `no metric weights ...`
 * Supported: Classic / Named
 * Placement:
 *   Classic: router mode
 *   Named: address-family mode
 * Description:
 * Sets or restores the EIGRP metric coefficients after validating the
 * coefficient set. Named mode converges on the EIGRP-owned metric target used
 * for protocol state instead of carrying metric behavior in the parser.
 */
/* A metric-version or K-value change invalidates the existing adjacency's
 * route and RTP context. Send Goodbye using the old metric configuration,
 * then retire each peer through the regular DOWN/DUAL/delete lifecycle. */
static void eigrp_metric_peers_restart(eigrp_instance_t *eigrp)
{
	eigrp_list_item_t *if_node, *nbr_node, *next_node;
	eigrp_intf_t *ei;
	eigrp_nbr_t *nbr;

	if (!eigrp || !eigrp->eiflist)
		return;

	for (EIGRP_LIST_ITERATE_RO(eigrp->eiflist, if_node, ei)) {
		if (!ei || !ei->nbrs || !ei->nbrs->count)
			continue;
		eigrp_hello_send(ei, EIGRP_HELLO_GRACEFUL_SHUTDOWN, NULL);
		for (EIGRP_LIST_ITERATE(ei->nbrs, nbr_node, next_node, nbr)) {
			/* DOWN has no live adjacency. Preserve suspended/locked
			 * neighbor identities and their prefix-limit penalties.
			 */
			if (nbr->state == EIGRP_NEIGHBOR_DOWN)
				continue;
			eigrp_nbr_state_update_reason(
				EIGRP_SET, nbr, EIGRP_NEIGHBOR_DOWN,
				EIGRP_EVENTLOG_NBR_PEER_RESTART);
			eigrp_nbr_delete(nbr);
		}
	}

	/* Teardown may have enqueued multicast and unicast packets on other
	 * interfaces before their neighbors were retired. Discard every old
	 * encoded wire image only after the entire AF has lost its peers. */
	for (EIGRP_LIST_ITERATE_RO(eigrp->eiflist, if_node, ei)) {
		if (!ei)
			continue;
		eigrp_timer_cancel(eigrp, &ei->t_multicast_retry);
		eigrp_packet_queue_clear(&ei->multicast_pending);
		eigrp_packet_interface_output_clear(ei);
		ei->multicast_backoff = false;
		ei->multicast_waiting = false;
	}
}

eigrp_result_t
eigrp_metric_weights_update(eigrp_operation_t operation,
			    eigrp_instance_context_t *context,
			    const eigrp_metric_weights_t *weights)
{
	eigrp_metric_message_args_t message = {
		.type = EIGRP_METRIC_MESSAGE_WEIGHTS,
		.operation = operation,
		.context = context,
		.data.weights = weights,
	};

	if (context && eigrp_instance_thread_dispatch_needed(context->runtime))
		return eigrp_instance_message_call(context->runtime,
						   eigrp_metric_message_process,
						   &message);
	if (operation == EIGRP_RESET) {
		if (!eigrp_metric_context_valid(context))
			return EIGRP_RESULT_NOT_FOUND;
		if (context->config && context->config->metric_config) {
			context->config->metric_config->weights_configured =
				false;
			memset(&context->config->metric_config->weights, 0,
			       sizeof(context->config->metric_config->weights));
		}
		if (context->runtime) {
			const uint8_t defaults[6] = {
				EIGRP_K1_DEFAULT, EIGRP_K2_DEFAULT,
				EIGRP_K3_DEFAULT, EIGRP_K4_DEFAULT,
				EIGRP_K5_DEFAULT, EIGRP_K6_DEFAULT,
			};

			if (memcmp(context->runtime->k_values, defaults,
				   sizeof(defaults))
			    != 0) {
				eigrp_metric_peers_restart(context->runtime);
				memcpy(context->runtime->k_values, defaults,
				       sizeof(defaults));
				eigrp_topology_metric_update(context->runtime);
			}
		}
		return EIGRP_RESULT_SUCCESS;
	}

	if (operation != EIGRP_SET)
		return EIGRP_RESULT_INVALID_ARGUMENT;

	eigrp_metric_config_t *config;

	if (!weights || weights->tos != 0)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	if (!eigrp_metric_context_valid(context))
		return EIGRP_RESULT_NOT_FOUND;
	if (context->config) {
		config = eigrp_metric_config_create(context->config);
		if (!config)
			return EIGRP_RESULT_INTERNAL_FAILURE;
		config->weights = *weights;
		config->weights_configured = true;
	}
	if (context->runtime) {
		const uint8_t updated[6] = {
			weights->k1, weights->k2, weights->k3,
			weights->k4, weights->k5, weights->k6,
		};

		if (memcmp(context->runtime->k_values, updated, sizeof(updated))
		    != 0) {
			eigrp_metric_peers_restart(context->runtime);
			memcpy(context->runtime->k_values, updated,
			       sizeof(updated));
			eigrp_topology_metric_update(context->runtime);
		}
	}
	return EIGRP_RESULT_SUCCESS;
}

/*
 * Syntax:
 *   Classic: `metric weights TOS K1 K2 K3 K4 K5` / `no metric weights ...`
 *   Named: `metric weights TOS K1 K2 K3 K4 K5 [K6]` / `no metric weights ...`
 * Supported: Classic / Named
 * Placement:
 *   Classic: router mode
 *   Named: address-family mode
 * Description:
 * Sets or restores the EIGRP metric coefficients after validating the
 * coefficient set. Named mode converges on the EIGRP-owned metric target used
 * for protocol state instead of carrying metric behavior in the parser.
 */


/*
 * Syntax:
 *   Classic: `variance MULTIPLIER` / `no variance`
 *   Named: `variance MULTIPLIER` / `no variance`
 * Supported: Classic / Named
 * Placement:
 *   Classic: router mode
 *   Named: topology base mode
 * Description:
 * Sets or resets the unequal-cost load-sharing variance multiplier.
 * DUAL feasibility remains authoritative; variance does not make an infeasible
 * path a successor.
 */
eigrp_result_t eigrp_metric_variance_update(eigrp_operation_t operation,
					    eigrp_instance_context_t *context,
					    uint8_t variance)
{
	eigrp_metric_message_args_t message = {
		.type = EIGRP_METRIC_MESSAGE_VARIANCE,
		.operation = operation,
		.context = context,
		.data.value = variance,
	};

	if (context && eigrp_instance_thread_dispatch_needed(context->runtime))
		return eigrp_instance_message_call(context->runtime,
						   eigrp_metric_message_process,
						   &message);
	if (operation == EIGRP_RESET) {
		if (!eigrp_metric_context_valid(context))
			return EIGRP_RESULT_NOT_FOUND;
		if (context->config && context->config->metric_config) {
			context->config->metric_config->variance_configured =
				false;
			context->config->metric_config->variance = 0;
		}
		if (context->runtime) {
			context->runtime->variance = EIGRP_VARIANCE_DEFAULT;
			eigrp_topology_multipath_update(context->runtime);
		}
		return EIGRP_RESULT_SUCCESS;
	}

	if (operation != EIGRP_SET)
		return EIGRP_RESULT_INVALID_ARGUMENT;

	eigrp_metric_config_t *config;

	if (variance < 1 || variance > 128)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	if (!eigrp_metric_context_valid(context))
		return EIGRP_RESULT_NOT_FOUND;
	if (context->config) {
		config = eigrp_metric_config_create(context->config);
		if (!config)
			return EIGRP_RESULT_INTERNAL_FAILURE;
		config->variance = variance;
		config->variance_configured = true;
	}
	if (context->runtime) {
		context->runtime->variance = variance;
		eigrp_topology_multipath_update(context->runtime);
	}
	return EIGRP_RESULT_SUCCESS;
}

/*
 * Syntax:
 *   Classic: `variance MULTIPLIER` / `no variance`
 *   Named: `variance MULTIPLIER` / `no variance`
 * Supported: Classic / Named
 * Placement:
 *   Classic: router mode
 *   Named: topology base mode
 * Description:
 * Sets or resets the unequal-cost load-sharing variance multiplier.
 * DUAL feasibility remains authoritative; variance does not make an infeasible
 * path a successor.
 */


/*
 * Syntax:
 *   Named: `traffic-share balanced` / `no traffic-share balanced`
 * Supported: Named
 * Placement:
 *   Named: topology base mode
 * Description:
 * Selects proportional forwarding weights for the already-selected successor
 * set. Feasibility, variance, maximum-paths, and DUAL successor selection are
 * unchanged.
 */
eigrp_result_t
eigrp_traffic_share_balanced_update(eigrp_operation_t operation,
				    eigrp_instance_context_t *context)
{
	eigrp_metric_message_args_t message = {
		.type = EIGRP_METRIC_MESSAGE_TRAFFIC_SHARE,
		.operation = operation,
		.context = context,
	};

	if (context && eigrp_instance_thread_dispatch_needed(context->runtime))
		return eigrp_instance_message_call(context->runtime,
						   eigrp_metric_message_process,
						   &message);
	bool enabled;
	eigrp_metric_config_t *config;

	if (operation != EIGRP_SET && operation != EIGRP_RESET)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	if (!eigrp_metric_context_valid(context))
		return EIGRP_RESULT_NOT_FOUND;

	/* SET selects balanced sharing; RESET is the explicit `no` form. The
	 * northbound destroy path restores the default by calling SET. */
	enabled = operation == EIGRP_SET;
	if (context->config) {
		config = eigrp_metric_config_create(context->config);
		if (!config)
			return EIGRP_RESULT_INTERNAL_FAILURE;
		config->traffic_share_balanced = enabled;
	}
	if (context->runtime) {
		context->runtime->traffic_share_balanced = enabled;
		eigrp_topology_traffic_share_update(context->runtime);
	}
	return EIGRP_RESULT_SUCCESS;
}


/*
 * Syntax:
 *   Named: `metric maximum-hops HOPS` / `no metric maximum-hops`
 * Supported: Named
 * Placement:
 *   Named: topology base mode
 * Description:
 * Sets or resets the configured EIGRP maximum-hop metric constraint.
 * The target retains the real feature endpoint even while live enforcement is
 * incomplete.
 */
eigrp_result_t
eigrp_metric_maximum_hops_update(eigrp_operation_t operation,
				 eigrp_instance_context_t *context,
				 uint8_t maximum_hops)
{
	eigrp_metric_message_args_t message = {
		.type = EIGRP_METRIC_MESSAGE_MAXIMUM_HOPS,
		.operation = operation,
		.context = context,
		.data.value = maximum_hops,
	};

	if (context && eigrp_instance_thread_dispatch_needed(context->runtime))
		return eigrp_instance_message_call(context->runtime,
						   eigrp_metric_message_process,
						   &message);
	if (operation == EIGRP_RESET) {
		if (!eigrp_metric_context_valid(context))
			return EIGRP_RESULT_NOT_FOUND;
		if (context->config && context->config->metric_config) {
			context->config->metric_config
				->maximum_hops_configured = false;
			context->config->metric_config->maximum_hops = 0;
		}
		if (context->runtime)
			context->runtime->max_hops = EIGRP_MAX_HOPS;
		return EIGRP_RESULT_SUCCESS;
	}

	if (operation != EIGRP_SET)
		return EIGRP_RESULT_INVALID_ARGUMENT;

	eigrp_metric_config_t *config;

	if (!maximum_hops)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	if (!eigrp_metric_context_valid(context))
		return EIGRP_RESULT_NOT_FOUND;
	if (context->config) {
		config = eigrp_metric_config_create(context->config);
		if (!config)
			return EIGRP_RESULT_INTERNAL_FAILURE;
		config->maximum_hops = maximum_hops;
		config->maximum_hops_configured = true;
	}
	if (context->runtime)
		context->runtime->max_hops = maximum_hops;
	return EIGRP_RESULT_SUCCESS;
}

/*
 * Syntax:
 *   Named: `metric maximum-hops HOPS` / `no metric maximum-hops`
 * Supported: Named
 * Placement:
 *   Named: topology base mode
 * Description:
 * Sets or resets the configured EIGRP maximum-hop metric constraint.
 * The target retains the real feature endpoint even while live enforcement is
 * incomplete.
 */


/* Select the highest route TLV version supported by both local policy and peer.
 */
uint8_t eigrp_metric_version_select(const eigrp_instance_t *eigrp,
				    uint8_t peer_version)
{
	uint8_t local_version =
		eigrp ? eigrp->metric_version : EIGRP_MAJOR_VERSION;

	if (local_version >= EIGRP_TLV_64B_VERSION
	    && peer_version >= EIGRP_TLV_64B_VERSION)
		return EIGRP_TLV_64B_VERSION;
	return EIGRP_TLV_32B_VERSION;
}

/* `metric version 32bit`: constrain this address family to classic metrics. */
eigrp_result_t eigrp_metric_version_update(eigrp_operation_t operation,
					   eigrp_instance_context_t *context)
{
	eigrp_metric_message_args_t message = {
		.type = EIGRP_METRIC_MESSAGE_VERSION,
		.operation = operation,
		.context = context,
	};

	if (context && eigrp_instance_thread_dispatch_needed(context->runtime))
		return eigrp_instance_message_call(context->runtime,
						   eigrp_metric_message_process,
						   &message);
	if (operation == EIGRP_RESET) {
		if (!eigrp_metric_context_valid(context))
			return EIGRP_RESULT_NOT_FOUND;
		if (context->config && context->config->metric_config)
			context->config->metric_config->version_32bit = false;
		if (context->runtime
		    && context->runtime->metric_version
			       != EIGRP_MAJOR_VERSION) {
			eigrp_metric_peers_restart(context->runtime);
			context->runtime->metric_version = EIGRP_MAJOR_VERSION;
			/* Only already-DOWN peers remain; rebind their codecs.
			 */
			eigrp_nbr_codec_update(context->runtime);
			eigrp_topology_metric_update(context->runtime);
		}
		return EIGRP_RESULT_SUCCESS;
	}

	if (operation != EIGRP_SET)
		return EIGRP_RESULT_INVALID_ARGUMENT;

	eigrp_metric_config_t *config;

	if (!eigrp_metric_context_valid(context))
		return EIGRP_RESULT_NOT_FOUND;
	if (context->config) {
		config = eigrp_metric_config_create(context->config);
		if (!config)
			return EIGRP_RESULT_INTERNAL_FAILURE;
		config->version_32bit = true;
	}
	if (context->runtime
	    && context->runtime->metric_version != EIGRP_TLV_32B_VERSION) {
		eigrp_metric_peers_restart(context->runtime);
		context->runtime->metric_version = EIGRP_TLV_32B_VERSION;
		/* Only already-DOWN peers remain; rebind their codecs. */
		eigrp_nbr_codec_update(context->runtime);
		eigrp_topology_metric_update(context->runtime);
	}
	return EIGRP_RESULT_SUCCESS;
}

/* `no metric version 32bit`: restore Release 2 / Wide Metrics default. */


static eigrp_result_t eigrp_metric_message_process(eigrp_instance_t *eigrp,
						   void *arg)
{
	eigrp_metric_message_args_t *message = arg;

	(void)eigrp;
	switch (message->type) {
	case EIGRP_METRIC_MESSAGE_DEFAULT:
		return eigrp_metric_default_update(message->operation,
						   message->context,
						   message->data.metric);
	case EIGRP_METRIC_MESSAGE_WEIGHTS:
		return eigrp_metric_weights_update(message->operation,
						   message->context,
						   message->data.weights);
	case EIGRP_METRIC_MESSAGE_VARIANCE:
		return eigrp_metric_variance_update(message->operation,
						    message->context,
						    message->data.value);
	case EIGRP_METRIC_MESSAGE_TRAFFIC_SHARE:
		return eigrp_traffic_share_balanced_update(message->operation,
							   message->context);
	case EIGRP_METRIC_MESSAGE_MAXIMUM_HOPS:
		return eigrp_metric_maximum_hops_update(message->operation,
							message->context,
							message->data.value);
	case EIGRP_METRIC_MESSAGE_VERSION:
		return eigrp_metric_version_update(message->operation,
						   message->context);
	}
	return EIGRP_RESULT_INVALID_ARGUMENT;
}

void eigrp_metric_config_delete_all(eigrp_af_config_t *af)
{
	if (!af)
		return;
	free(af->metric_config);
	af->metric_config = NULL;
}

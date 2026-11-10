// SPDX-License-Identifier: GPL-2.0-or-later
/* Native Unix EIGRP semantic configuration dispatch.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <arpa/inet.h>
#include <string.h>

#include "eigrp_cli.h"
#include "eigrp_interface.h"
#include "eigrp_unix.h"
#include "eigrp_unix_config_private.h"
#include "eigrp_unix_keychain.h"

/* Native policy definitions require an explicit host provider. The UUT and
 * portable harnesses must never inherit permissive policy behavior. */
static eigrp_unix_config_policy_provider_t policy_provider;

void eigrp_unix_config_policy_register(
	eigrp_unix_config_policy_provider_t provider)
{
	policy_provider = provider;
}

static eigrp_metric_values_t
eigrp_unix_config_metric_value(const eigrp_unix_config_command_t *cmd)
{
	eigrp_metric_values_t metric = {
		.bandwidth = cmd->numbers[0],
		.delay = cmd->numbers[1],
		.reliability = (uint8_t)cmd->numbers[2],
		.load = (uint8_t)cmd->numbers[3],
		.mtu = (uint16_t)cmd->numbers[4],
	};

	return metric;
}

static eigrp_result_t
eigrp_unix_config_command_commit(const eigrp_unix_config_command_t *cmd)
{
	eigrp_af_config_t *af;
	eigrp_instance_context_t context = {0};
	eigrp_intf_context_t intf = {0};
	eigrp_metric_values_t metric;
	eigrp_metric_weights_t weights;
	eigrp_redist_source_t source;
	uint32_t router_id;
	eigrp_result_t result;

	/* Host-owned objects are installed through the Linux policy/keychain
	 * services, not through the virtual-wire UUT placeholder. */
	switch (cmd->action) {
	case EIGRP_UNIX_CONFIG_RULE:
	case EIGRP_UNIX_CONFIG_MAP:
		return policy_provider ? policy_provider(cmd)
				       : EIGRP_RESULT_NOT_IMPLEMENTED;
	case EIGRP_UNIX_CONFIG_KEYCHAIN:
		return eigrp_unix_keychain_add(cmd->policy_name,
					       (uint32_t)cmd->numbers[0],
					       cmd->secret);
	default:
		break;
	}
	if (cmd->action == EIGRP_UNIX_CONFIG_PARENT)
		return eigrp_named_config_create(cmd->name);
	if (cmd->mode == EIGRP_UNIX_MODE_ROUTER) {
		if (cmd->action != EIGRP_UNIX_CONFIG_SHUTDOWN)
			return EIGRP_RESULT_INVALID_ARGUMENT;
		return eigrp_named_config_shutdown_update(
			cmd->operation, eigrp_named_config_read(cmd->name));
	}
	if (cmd->action == EIGRP_UNIX_CONFIG_AF)
		return eigrp_af_config_create(cmd->name, cmd->afi,
					      EIGRP_UNIX_DEFAULT_VRF_NAME,
					      cmd->asn);
	af = eigrp_af_config_read(cmd->name, cmd->afi,
				  EIGRP_UNIX_DEFAULT_VRF_NAME, cmd->asn);
	if (!af)
		return EIGRP_RESULT_NOT_FOUND;
	if (cmd->action == EIGRP_UNIX_CONFIG_SHUTDOWN)
		return eigrp_af_config_shutdown_update(cmd->operation, af);
	result = eigrp_af_config_context_read(cmd->name, cmd->afi,
					      EIGRP_UNIX_DEFAULT_VRF_NAME,
					      cmd->asn, &context);
	if (result != EIGRP_RESULT_SUCCESS)
		return result;
	if (cmd->mode == EIGRP_UNIX_MODE_INTERFACE) {
		if (cmd->action == EIGRP_UNIX_CONFIG_INTERFACE)
			return eigrp_intf_config_create(af, cmd->ifname);
		intf.address_family = af;
		intf.config = eigrp_intf_config_read(af, cmd->ifname);
		intf.runtime =
			eigrp_intf_lookup_by_name(context.runtime, cmd->ifname);
		if (!intf.config)
			return EIGRP_RESULT_NOT_FOUND;
		if (cmd->action == EIGRP_UNIX_CONFIG_AUTH_MODE)
			return eigrp_auth_mode_update(EIGRP_SET, &intf,
						      EIGRP_AUTHENTICATION_MD5,
						      NULL);
		if (cmd->action == EIGRP_UNIX_CONFIG_AUTH_KEYCHAIN)
			return eigrp_auth_keychain_update(EIGRP_SET, &intf,
							  cmd->policy_name);
		switch (cmd->key) {
		case EIGRP_UNIX_INTF_BANDWIDTH_PERCENT:
			return eigrp_intf_bandwidth_percent_update(
				cmd->operation, &intf, cmd->numbers[0]);
		case EIGRP_UNIX_INTF_BANDWIDTH:
			return eigrp_intf_bandwidth_update(
				cmd->operation, &intf, cmd->numbers[0]);
		case EIGRP_UNIX_INTF_DELAY:
			return eigrp_intf_delay_update(cmd->operation, &intf,
						       cmd->numbers[0]);
		case EIGRP_UNIX_INTF_HELLO:
			return eigrp_intf_hello_interval_update(
				cmd->operation, &intf,
				(uint16_t)cmd->numbers[0]);
		case EIGRP_UNIX_INTF_HOLD:
			return eigrp_intf_hold_time_update(
				cmd->operation, &intf,
				(uint16_t)cmd->numbers[0]);
		case EIGRP_UNIX_INTF_PASSIVE:
			return eigrp_intf_passive_update(cmd->operation, &intf);
		case EIGRP_UNIX_INTF_SPLIT_HORIZON:
			return eigrp_intf_split_horizon_update(cmd->operation,
							       &intf);
		case EIGRP_UNIX_INTF_NEXT_HOP_SELF:
			return eigrp_intf_nexthop_self_update(cmd->operation,
							      &intf);
		case EIGRP_UNIX_INTF_SHUTDOWN:
			return eigrp_intf_shutdown_update(cmd->operation,
							  &intf);
		}
		return EIGRP_RESULT_INVALID_ARGUMENT;
	}
	switch (cmd->action) {
	case EIGRP_UNIX_CONFIG_ROUTER_ID:
		memcpy(&router_id, cmd->address.bytes, sizeof(router_id));
		return eigrp_instance_router_id_update(EIGRP_SET, &context,
						       ntohl(router_id));
	case EIGRP_UNIX_CONFIG_NETWORK:
		return eigrp_network_create(&context, &cmd->prefix);
	case EIGRP_UNIX_CONFIG_NEIGHBOR:
		return eigrp_nbr_static_create(af, &cmd->address, cmd->ifname);
	case EIGRP_UNIX_CONFIG_TOPOLOGY:
		return eigrp_topology_create(&context);
	case EIGRP_UNIX_CONFIG_DISTANCE:
		return eigrp_af_config_distance_update(
			EIGRP_SET, af, (uint8_t)cmd->numbers[0],
			(uint8_t)cmd->numbers[1]);
	case EIGRP_UNIX_CONFIG_METRIC:
		metric = eigrp_unix_config_metric_value(cmd);
		return eigrp_metric_default_update(EIGRP_SET, &context,
						   &metric);
	case EIGRP_UNIX_CONFIG_WEIGHTS:
		weights = (eigrp_metric_weights_t){
			.tos = cmd->numbers[0],
			.k1 = cmd->numbers[1],
			.k2 = cmd->numbers[2],
			.k3 = cmd->numbers[3],
			.k4 = cmd->numbers[4],
			.k5 = cmd->numbers[5],
			.k6 = cmd->numbers[6],
		};
		return eigrp_metric_weights_update(EIGRP_SET, &context,
						   &weights);
	case EIGRP_UNIX_CONFIG_VARIANCE:
		return eigrp_metric_variance_update(EIGRP_SET, &context,
						    (uint8_t)cmd->numbers[0]);
	case EIGRP_UNIX_CONFIG_MAXIMUM_HOPS:
		return eigrp_metric_maximum_hops_update(
			EIGRP_SET, &context, (uint8_t)cmd->numbers[0]);
	case EIGRP_UNIX_CONFIG_MAXIMUM_PATHS:
		return eigrp_topology_maximum_paths_update(
			EIGRP_SET, &context, (uint8_t)cmd->numbers[0]);
	case EIGRP_UNIX_CONFIG_DISTRIBUTE:
		return eigrp_distribute_add(
			&context, cmd->filter_type, cmd->policy_name,
			cmd->direction, cmd->ifname[0] ? cmd->ifname : NULL);
	case EIGRP_UNIX_CONFIG_REDISTRIBUTE:
		metric = eigrp_unix_config_metric_value(cmd);
		source = (eigrp_redist_source_t){
			.protocol = (eigrp_redist_protocol_t)cmd->numbers[5],
		};
		return eigrp_redist_add(
			&context, &source, cmd->numbers[0] ? &metric : NULL,
			cmd->policy_name[0] ? cmd->policy_name : NULL);
	default:
		return EIGRP_RESULT_INVALID_ARGUMENT;
	}
}

eigrp_result_t eigrp_unix_config_commit(eigrp_unix_config_t *config,
					unsigned *error_line)
{
	eigrp_unix_config_command_t *cmd;
	eigrp_result_t result;

	if (error_line)
		*error_line = 0;
	if (!config || config->committed)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	/* A failed runtime operation is NEVER silently treated as success.
	 * An attempted commit is one-shot: replay after partial failure is
	 * unsafe.
	 */
	config->committed = true;
	/* Install host definitions before any portable consumer, regardless of
	 * file order. Otherwise references to later policy/keychain objects can
	 * fail partway through startup and leave a partial protocol runtime. */
	/* Defer enabling MD5 until after key-chain attachment, even when the
	 * file lists mode before key-chain. The remaining portable commands
	 * retain their original relative order. */
	for (unsigned phase = 0; phase < 3; ++phase) {
		for (cmd = config->head; cmd; cmd = cmd->next) {
			bool definition =
				cmd->action == EIGRP_UNIX_CONFIG_RULE
				|| cmd->action == EIGRP_UNIX_CONFIG_MAP
				|| cmd->action == EIGRP_UNIX_CONFIG_KEYCHAIN;
			bool auth_mode =
				cmd->action == EIGRP_UNIX_CONFIG_AUTH_MODE;
			if (phase != (definition ? 0U : auth_mode ? 2U : 1U))
				continue;
			result = eigrp_unix_config_command_commit(cmd);
			if (result != EIGRP_RESULT_SUCCESS) {
				if (error_line)
					*error_line = cmd->line;
				return result;
			}
		}
	}
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_unix_config_apply_file(eigrp_unix_config_t *config,
					    FILE *input, unsigned *error_line)
{
	eigrp_result_t result =
		eigrp_unix_config_parse_file(config, input, error_line);

	return result == EIGRP_RESULT_SUCCESS
		       ? eigrp_unix_config_commit(config, error_line)
		       : result;
}

eigrp_result_t eigrp_unix_config_apply_text(eigrp_unix_config_t *config,
					    const char *text,
					    unsigned *error_line)
{
	eigrp_result_t result =
		eigrp_unix_config_parse_text(config, text, error_line);

	return result == EIGRP_RESULT_SUCCESS
		       ? eigrp_unix_config_commit(config, error_line)
		       : result;
}

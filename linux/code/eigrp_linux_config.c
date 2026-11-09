// SPDX-License-Identifier: GPL-2.0-or-later
/* Native Linux policy configuration binding.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include "eigrp_linux_config.h"
#include "eigrp_linux_policy.h"
#include "eigrp_unix_config_private.h"

static eigrp_result_t
eigrp_linux_config_policy_commit(const eigrp_unix_config_command_t *cmd)
{
	eigrp_linux_policy_match_t match;

	switch (cmd->action) {
	case EIGRP_UNIX_CONFIG_RULE:
		return eigrp_linux_policy_rule_set(
			cmd->filter_type, cmd->policy_name, cmd->numbers[0],
			cmd->decision, &cmd->prefix, (uint8_t)cmd->numbers[1],
			(uint8_t)cmd->numbers[2]);
	case EIGRP_UNIX_CONFIG_MAP:
		switch (cmd->match) {
		case EIGRP_UNIX_MATCH_ANY:
			match = EIGRP_LINUX_MATCH_ANY;
			break;
		case EIGRP_UNIX_MATCH_PREFIX_LIST:
			match = EIGRP_LINUX_MATCH_PREFIX_LIST;
			break;
		case EIGRP_UNIX_MATCH_ACCESS_LIST:
			match = EIGRP_LINUX_MATCH_ACCESS_LIST;
			break;
		case EIGRP_UNIX_MATCH_TAG:
			match = EIGRP_LINUX_MATCH_TAG;
			break;
		default:
			return EIGRP_RESULT_UNSUPPORTED;
		}
		return eigrp_linux_policy_map_set(
			cmd->policy_name, cmd->numbers[0], cmd->decision, match,
			cmd->reference[0] ? cmd->reference : NULL,
			cmd->numbers[1]);
	default:
		return EIGRP_RESULT_INVALID_ARGUMENT;
	}
}

void eigrp_linux_config_policy_register(void)
{
	eigrp_unix_config_policy_register(eigrp_linux_config_policy_commit);
}

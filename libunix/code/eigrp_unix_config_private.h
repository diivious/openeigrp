// SPDX-License-Identifier: GPL-2.0-or-later
/* Private validated configuration plan; never exposed to portable EIGRP.
 * Copyright (C) 2026 Donnie V. Savage
 */
#ifndef EIGRP_UNIX_EIGRP_UNIX_CONFIG_PRIVATE_H_
#define EIGRP_UNIX_EIGRP_UNIX_CONFIG_PRIVATE_H_

#include <stdbool.h>
#include <stdint.h>
#include "eigrp_unix_config.h"

#define EIGRP_UNIX_CONFIG_NAME_MAX 255U

typedef enum eigrp_unix_config_mode {
	EIGRP_UNIX_MODE_GLOBAL,
	EIGRP_UNIX_MODE_ROUTER,
	EIGRP_UNIX_MODE_AF,
	EIGRP_UNIX_MODE_INTERFACE,
	EIGRP_UNIX_MODE_TOPOLOGY,
} eigrp_unix_config_mode_t;

typedef enum eigrp_unix_config_action {
	EIGRP_UNIX_CONFIG_PARENT,
	EIGRP_UNIX_CONFIG_AF,
	EIGRP_UNIX_CONFIG_SHUTDOWN,
	EIGRP_UNIX_CONFIG_ROUTER_ID,
	EIGRP_UNIX_CONFIG_NETWORK,
	EIGRP_UNIX_CONFIG_INTERFACE,
	EIGRP_UNIX_CONFIG_INTERFACE_VALUE,
	EIGRP_UNIX_CONFIG_INTERFACE_FLAG,
	EIGRP_UNIX_CONFIG_NEIGHBOR,
	EIGRP_UNIX_CONFIG_TOPOLOGY,
	EIGRP_UNIX_CONFIG_DISTANCE,
	EIGRP_UNIX_CONFIG_METRIC,
	EIGRP_UNIX_CONFIG_WEIGHTS,
	EIGRP_UNIX_CONFIG_VARIANCE,
	EIGRP_UNIX_CONFIG_MAXIMUM_HOPS,
	EIGRP_UNIX_CONFIG_MAXIMUM_PATHS,
	EIGRP_UNIX_CONFIG_REDISTRIBUTE,
} eigrp_unix_config_action_t;

typedef enum eigrp_unix_config_interface_key {
	EIGRP_UNIX_INTF_BANDWIDTH_PERCENT,
	EIGRP_UNIX_INTF_BANDWIDTH,
	EIGRP_UNIX_INTF_DELAY,
	EIGRP_UNIX_INTF_HELLO,
	EIGRP_UNIX_INTF_HOLD,
	EIGRP_UNIX_INTF_PASSIVE,
	EIGRP_UNIX_INTF_SPLIT_HORIZON,
	EIGRP_UNIX_INTF_NEXT_HOP_SELF,
	EIGRP_UNIX_INTF_SHUTDOWN,
} eigrp_unix_config_interface_key_t;

typedef struct eigrp_unix_config_command {
	struct eigrp_unix_config_command *next;
	eigrp_unix_config_action_t action;
	eigrp_unix_config_mode_t mode;
	eigrp_unix_config_interface_key_t key;
	eigrp_operation_t operation;
	unsigned line;
	char name[EIGRP_UNIX_CONFIG_NAME_MAX + 1U];
	char ifname[EIGRP_UNIX_CONFIG_NAME_MAX + 1U];
	eigrp_afi_t afi;
	uint16_t asn;
	uint32_t numbers[7];
	eigrp_prefix_t prefix;
	eigrp_address_t address;
} eigrp_unix_config_command_t;

struct eigrp_unix_config {
	eigrp_unix_config_command_t *head;
	eigrp_unix_config_command_t *tail;
	size_t count;
	bool committed;
};

typedef struct eigrp_unix_config_parse {
	eigrp_unix_config_mode_t mode;
	char name[EIGRP_UNIX_CONFIG_NAME_MAX + 1U];
	char ifname[EIGRP_UNIX_CONFIG_NAME_MAX + 1U];
	eigrp_afi_t afi;
	uint16_t asn;
} eigrp_unix_config_parse_t;

#endif /* EIGRP_UNIX_EIGRP_UNIX_CONFIG_PRIVATE_H_ */

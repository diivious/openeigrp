// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Standalone Unix EIGRP configuration parser.
 *
 * Copyright (C) 2026 Donnie V. Savage
 */
#define _POSIX_C_SOURCE 200809L

#include <arpa/inet.h>
#include <ctype.h>
#include <errno.h>
#include <limits.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/stat.h>
#include <unistd.h>

#include "eigrp_unix.h"
#include "eigrp_unix_config_private.h"

#define EIGRP_UNIX_CONFIG_LINE_MAX 512U
#define EIGRP_UNIX_CONFIG_TOKENS_MAX 16U
#define EIGRP_UNIX_CONFIG_COMMANDS_MAX 4096U

static void eigrp_unix_config_scrub(void *memory, size_t length)
{
	volatile unsigned char *p = memory;
	while (length--)
		*p++ = 0;
}

static void eigrp_unix_config_clear(eigrp_unix_config_t *config)
{
	eigrp_unix_config_command_t *cmd;
	eigrp_unix_config_command_t *next;

	for (cmd = config->head; cmd; cmd = next) {
		next = cmd->next;
		eigrp_unix_config_scrub(cmd->secret, sizeof(cmd->secret));
		free(cmd);
	}
	config->head = NULL;
	config->tail = NULL;
	config->count = 0;
	config->committed = false;
}

static bool eigrp_unix_config_number(const char *text, uint32_t minimum,
				     uint32_t maximum, uint32_t *number)
{
	unsigned long value;
	char *end;

	if (!text || !text[0] || !isdigit((unsigned char)text[0]))
		return false;
	errno = 0;
	value = strtoul(text, &end, 10);
	if (errno || *end || value < minimum || value > maximum)
		return false;
	*number = (uint32_t)value;
	return true;
}

static bool eigrp_unix_config_name(const char *text)
{
	size_t i;

	if (!text || !text[0] || strlen(text) > EIGRP_UNIX_CONFIG_NAME_MAX)
		return false;
	for (i = 0; text[i]; ++i) {
		if (!isalnum((unsigned char)text[i]) && text[i] != '_'
		    && text[i] != '-' && text[i] != '.')
			return false;
	}
	return true;
}

static bool eigrp_unix_config_address(const char *text, eigrp_afi_t afi,
				      eigrp_address_t *address)
{
	struct in_addr ipv4;
	struct in6_addr ipv6;

	memset(address, 0, sizeof(*address));
	address->afi = afi;
	if (afi == EIGRP_AFI_IPV4) {
		if (inet_pton(AF_INET, text, &ipv4) != 1)
			return false;
		memcpy(address->bytes, &ipv4, sizeof(ipv4));
		return true;
	}
	if (afi == EIGRP_AFI_IPV6 && inet_pton(AF_INET6, text, &ipv6) == 1) {
		memcpy(address->bytes, &ipv6, sizeof(ipv6));
		return true;
	}
	return false;
}

static bool eigrp_unix_config_prefix(const char *text, eigrp_afi_t afi,
				     eigrp_prefix_t *prefix)
{
	char input[INET6_ADDRSTRLEN + 8];
	char *slash;
	uint32_t length;
	unsigned bit;

	if (strlen(text) >= sizeof(input))
		return false;
	memcpy(input, text, strlen(text) + 1U);
	slash = strchr(input, '/');
	if (!slash || strchr(slash + 1, '/'))
		return false;
	*slash++ = '\0';
	if (!eigrp_unix_config_number(slash, 0,
				      afi == EIGRP_AFI_IPV4 ? 32 : 128, &length)
	    || !eigrp_unix_config_address(input, afi, &prefix->address))
		return false;
	prefix->prefix_length = (uint8_t)length;
	/* Normalize without depending on a private portable representation. */
	for (bit = length; bit < (afi == EIGRP_AFI_IPV4 ? 32U : 128U); ++bit)
		prefix->address.bytes[bit / 8] &= ~(0x80U >> (bit % 8));
	return true;
}

static bool eigrp_unix_config_network(char **argv, size_t argc,
				      eigrp_prefix_t *prefix)
{
	struct in_addr address;
	struct in_addr wildcard;
	uint32_t mask;
	uint32_t plen;
	uint32_t host;
	unsigned bit;
	char text[INET_ADDRSTRLEN + 5];

	if (argc != 2 && argc != 3)
		return false;
	if (strchr(argv[1], '/'))
		return argc == 2
		       && eigrp_unix_config_prefix(argv[1], EIGRP_AFI_IPV4,
						   prefix);
	if (inet_pton(AF_INET, argv[1], &address) != 1)
		return false;
	host = ntohl(address.s_addr);
	if (argc == 2) {
		/* Cisco/FRR classful default when the wildcard is omitted. */
		plen = host == 0			     ? 0
		       : (host & 0x80000000U) == 0	     ? 8
		       : (host & 0xc0000000U) == 0x80000000U ? 16
							     : 24;
	} else {
		if (inet_pton(AF_INET, argv[2], &wildcard) != 1)
			return false;
		mask = ~ntohl(wildcard.s_addr);
		plen = 0;
		for (bit = 0; bit < 32; ++bit) {
			if (!(mask & (0x80000000U >> bit)))
				break;
			plen++;
		}
		if ((mask & (plen == 32 ? 0 : (0xffffffffU >> plen))) != 0)
			return false;
	}
	if (snprintf(text, sizeof(text), "%s/%u", argv[1], plen)
	    >= (int)sizeof(text))
		return false;
	return eigrp_unix_config_prefix(text, EIGRP_AFI_IPV4, prefix);
}

static bool eigrp_unix_config_metric(char **argv, size_t argc,
				     uint32_t *numbers)
{
	static const uint32_t min[] = {1, 0, 0, 1, 1};
	static const uint32_t max[] = {UINT32_MAX, UINT32_MAX, 255, 255, 65535};
	size_t i;

	if (argc != 5)
		return false;
	for (i = 0; i < 5; ++i) {
		if (!eigrp_unix_config_number(argv[i], min[i], max[i],
					      &numbers[i]))
			return false;
	}
	return true;
}

static bool eigrp_unix_config_weights(char **argv, size_t argc,
				      uint32_t *numbers)
{
	size_t i;

	if (argc != 6 && argc != 7)
		return false;
	for (i = 0; i < argc; ++i) {
		if (!eigrp_unix_config_number(argv[i], 0, 255, &numbers[i]))
			return false;
	}
	return true;
}

static eigrp_result_t
eigrp_unix_config_command_add(eigrp_unix_config_t *config,
			      const eigrp_unix_config_parse_t *parse,
			      eigrp_unix_config_command_t *cmd)
{
	if (config->count >= EIGRP_UNIX_CONFIG_COMMANDS_MAX)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	cmd->mode = parse->mode;
	cmd->afi = parse->afi;
	cmd->asn = parse->asn;
	memcpy(cmd->name, parse->name, strlen(parse->name) + 1U);
	if (!cmd->ifname[0])
		memcpy(cmd->ifname, parse->ifname, strlen(parse->ifname) + 1U);
	if (config->tail)
		config->tail->next = cmd;
	else
		config->head = cmd;
	config->tail = cmd;
	config->count++;
	return EIGRP_RESULT_SUCCESS;
}

static eigrp_result_t
eigrp_unix_config_line_parse(eigrp_unix_config_t *config,
			     eigrp_unix_config_parse_t *parse, char *line,
			     unsigned line_number)
{
	char *argv[EIGRP_UNIX_CONFIG_TOKENS_MAX];
	char *save;
	char *token;
	char *comment;
	eigrp_unix_config_command_t *cmd;
	eigrp_result_t result = EIGRP_RESULT_UNSUPPORTED;
	size_t argc = 0;
	uint32_t n;
	bool negated = false;

	/* A # or ! embedded in a key is data, not a comment. Only
	 * comment markers at token boundaries begin a comment. */
	for (comment = line; *comment; ++comment) {
		if ((*comment == '#' || *comment == '!')
		    && (comment == line
			|| isspace((unsigned char)comment[-1]))) {
			*comment = '\0';
			break;
		}
	}
	for (token = strtok_r(line, " \t\r\n", &save); token;
	     token = strtok_r(NULL, " \t\r\n", &save)) {
		if (argc >= EIGRP_UNIX_CONFIG_TOKENS_MAX)
			return EIGRP_RESULT_INVALID_ARGUMENT;
		argv[argc++] = token;
	}
	if (!argc)
		return EIGRP_RESULT_SUCCESS;
	if (argc == 1 && strcmp(argv[0], "exit") == 0) {
		if (parse->mode != EIGRP_UNIX_MODE_ROUTER)
			return EIGRP_RESULT_INVALID_ARGUMENT;
		parse->mode = EIGRP_UNIX_MODE_GLOBAL;
		parse->name[0] = '\0';
		return EIGRP_RESULT_SUCCESS;
	}
	if (argc == 1 && strcmp(argv[0], "exit-address-family") == 0) {
		if (parse->mode != EIGRP_UNIX_MODE_AF)
			return EIGRP_RESULT_INVALID_ARGUMENT;
		parse->mode = EIGRP_UNIX_MODE_ROUTER;
		parse->afi = 0;
		parse->asn = 0;
		return EIGRP_RESULT_SUCCESS;
	}
	if (argc == 1 && strcmp(argv[0], "exit-af-interface") == 0) {
		if (parse->mode != EIGRP_UNIX_MODE_INTERFACE)
			return EIGRP_RESULT_INVALID_ARGUMENT;
		parse->mode = EIGRP_UNIX_MODE_AF;
		parse->ifname[0] = '\0';
		return EIGRP_RESULT_SUCCESS;
	}
	if (argc == 1 && strcmp(argv[0], "exit-af-topology") == 0) {
		if (parse->mode != EIGRP_UNIX_MODE_TOPOLOGY)
			return EIGRP_RESULT_INVALID_ARGUMENT;
		parse->mode = EIGRP_UNIX_MODE_AF;
		return EIGRP_RESULT_SUCCESS;
	}
	cmd = calloc(1, sizeof(*cmd));
	if (!cmd)
		return EIGRP_RESULT_INTERNAL_FAILURE;
	cmd->line = line_number;
	cmd->operation = EIGRP_SET;

	if (parse->mode == EIGRP_UNIX_MODE_GLOBAL && argc >= 6
	    && (strcmp(argv[0], "ip") == 0 || strcmp(argv[0], "ipv6") == 0)
	    && (strcmp(argv[1], "prefix-list") == 0
		|| strcmp(argv[1], "access-list") == 0)
	    && eigrp_unix_config_name(argv[2]) && strcmp(argv[3], "seq") == 0
	    && eigrp_unix_config_number(argv[4], 1, UINT32_MAX,
					&cmd->numbers[0])
	    && (strcmp(argv[5], "permit") == 0 || strcmp(argv[5], "deny") == 0)
	    && argc >= 7) {
		uint32_t ge = 0, le = 0;
		cmd->action = EIGRP_UNIX_CONFIG_RULE;
		cmd->filter_type = strcmp(argv[1], "prefix-list") == 0
					   ? EIGRP_DIST_PREFIX_LIST
					   : EIGRP_DIST_ACCESS_LIST;
		cmd->decision = strcmp(argv[5], "permit") == 0
					? EIGRP_FILTER_DECISION_PERMIT
					: EIGRP_FILTER_DECISION_DENY;
		memcpy(cmd->policy_name, argv[2], strlen(argv[2]) + 1);
		if (!eigrp_unix_config_prefix(argv[6],
					      strcmp(argv[0], "ipv6") == 0
						      ? EIGRP_AFI_IPV6
						      : EIGRP_AFI_IPV4,
					      &cmd->prefix)) {
			result = EIGRP_RESULT_INVALID_ARGUMENT;
			goto done;
		}
		if (cmd->filter_type == EIGRP_DIST_ACCESS_LIST) {
			result = argc == 7 ? EIGRP_RESULT_SUCCESS
					   : EIGRP_RESULT_UNSUPPORTED;
			goto done;
		}
		for (size_t j = 7; j < argc; j += 2) {
			uint32_t *value;
			if (j + 1 == argc) {
				result = EIGRP_RESULT_INVALID_ARGUMENT;
				goto done;
			}
			if (strcmp(argv[j], "ge") == 0 && ge == 0)
				value = &ge;
			else if (strcmp(argv[j], "le") == 0 && le == 0)
				value = &le;
			else {
				result = EIGRP_RESULT_UNSUPPORTED;
				goto done;
			}
			if (!eigrp_unix_config_number(
				    argv[j + 1], 1,
				    cmd->prefix.address.afi == EIGRP_AFI_IPV6
					    ? 128
					    : 32,
				    value)) {
				result = EIGRP_RESULT_INVALID_ARGUMENT;
				goto done;
			}
		}
		cmd->numbers[1] = ge;
		cmd->numbers[2] = le;
		if ((ge && ge < cmd->prefix.prefix_length)
		    || (le && le < cmd->prefix.prefix_length)
		    || (ge && le && ge > le)) {
			result = EIGRP_RESULT_INVALID_ARGUMENT;
			goto done;
		}
		result = EIGRP_RESULT_SUCCESS;
	} else if (parse->mode == EIGRP_UNIX_MODE_GLOBAL && argc >= 4
		   && strcmp(argv[0], "route-map") == 0
		   && eigrp_unix_config_name(argv[1])
		   && (strcmp(argv[2], "permit") == 0
		       || strcmp(argv[2], "deny") == 0)
		   && eigrp_unix_config_number(argv[3], 1, UINT32_MAX,
					       &cmd->numbers[0])) {
		cmd->action = EIGRP_UNIX_CONFIG_MAP;
		cmd->decision = strcmp(argv[2], "permit") == 0
					? EIGRP_FILTER_DECISION_PERMIT
					: EIGRP_FILTER_DECISION_DENY;
		memcpy(cmd->policy_name, argv[1], strlen(argv[1]) + 1);
		if (argc == 4) {
			result = EIGRP_RESULT_SUCCESS;
		} else if (argc == 7 && strcmp(argv[4], "match") == 0
			   && (strcmp(argv[5], "prefix-list") == 0
			       || strcmp(argv[5], "access-list") == 0)
			   && eigrp_unix_config_name(argv[6])) {
			if (strcmp(argv[5], "prefix-list") == 0)
				cmd->match = EIGRP_UNIX_MATCH_PREFIX_LIST;
			else if (strcmp(argv[5], "access-list") == 0)
				cmd->match = EIGRP_UNIX_MATCH_ACCESS_LIST;
			else
				goto done;
			memcpy(cmd->reference, argv[6], strlen(argv[6]) + 1);
			result = EIGRP_RESULT_SUCCESS;
		} else if (argc == 7 && strcmp(argv[4], "match") == 0
			   && strcmp(argv[5], "tag") == 0
			   && eigrp_unix_config_number(argv[6], 0, UINT32_MAX,
						       &cmd->numbers[1])) {
			cmd->match = EIGRP_UNIX_MATCH_TAG;
			result = EIGRP_RESULT_SUCCESS;
		}
	} else if (parse->mode == EIGRP_UNIX_MODE_GLOBAL && argc == 6
		   && strcmp(argv[0], "key-chain") == 0
		   && eigrp_unix_config_name(argv[1])
		   && strcmp(argv[2], "key") == 0
		   && eigrp_unix_config_number(argv[3], 1, UINT32_MAX,
					       &cmd->numbers[0])
		   && strcmp(argv[4], "secret") == 0) {
		if (!argv[5][0] || strlen(argv[5]) >= sizeof(cmd->secret)
		    || strlen(argv[1]) >= 64) {
			result = EIGRP_RESULT_INVALID_ARGUMENT;
			goto done;
		}
		cmd->action = EIGRP_UNIX_CONFIG_KEYCHAIN;
		memcpy(cmd->policy_name, argv[1], strlen(argv[1]) + 1);
		memcpy(cmd->secret, argv[5], strlen(argv[5]) + 1);
		result = EIGRP_RESULT_SUCCESS;
	} else if (parse->mode == EIGRP_UNIX_MODE_GLOBAL && argc == 3
		   && strcmp(argv[0], "router") == 0
		   && strcmp(argv[1], "eigrp") == 0
		   && eigrp_unix_config_name(argv[2])) {
		memcpy(parse->name, argv[2], strlen(argv[2]) + 1U);
		parse->mode = EIGRP_UNIX_MODE_ROUTER;
		cmd->action = EIGRP_UNIX_CONFIG_PARENT;
		result = EIGRP_RESULT_SUCCESS;
	} else if (parse->mode == EIGRP_UNIX_MODE_ROUTER
		   && (argc >= 4 && argc <= 7)
		   && strcmp(argv[0], "address-family") == 0) {
		size_t i = 1;
		if (strcmp(argv[i], "ipv4") == 0)
			parse->afi = EIGRP_AFI_IPV4;
		else if (strcmp(argv[i], "ipv6") == 0)
			parse->afi = EIGRP_AFI_IPV6;
		else
			goto done;
		i++;
		if (i < argc && strcmp(argv[i], "unicast") == 0)
			i++;
		/* Native Linux currently exposes only the default VRF. */
		if (i + 1 < argc && strcmp(argv[i], "vrf") == 0) {
			if (strcmp(argv[i + 1], EIGRP_UNIX_DEFAULT_VRF_NAME)
			    != 0)
				goto done;
			i += 2;
		}
		if (i + 2 != argc || strcmp(argv[i], "autonomous-system") != 0
		    || !eigrp_unix_config_number(argv[i + 1], 1, UINT16_MAX,
						 &n)) {
			result = EIGRP_RESULT_INVALID_ARGUMENT;
			goto done;
		}
		parse->asn = (uint16_t)n;
		parse->mode = EIGRP_UNIX_MODE_AF;
		cmd->action = EIGRP_UNIX_CONFIG_AF;
		result = EIGRP_RESULT_SUCCESS;
	} else if (parse->mode == EIGRP_UNIX_MODE_AF && argc == 2
		   && strcmp(argv[0], "af-interface") == 0
		   && eigrp_unix_config_name(argv[1])) {
		memcpy(parse->ifname, argv[1], strlen(argv[1]) + 1U);
		parse->mode = EIGRP_UNIX_MODE_INTERFACE;
		cmd->action = EIGRP_UNIX_CONFIG_INTERFACE;
		result = EIGRP_RESULT_SUCCESS;
	} else if (parse->mode == EIGRP_UNIX_MODE_AF && argc == 2
		   && strcmp(argv[0], "topology") == 0
		   && strcmp(argv[1], "base") == 0) {
		parse->mode = EIGRP_UNIX_MODE_TOPOLOGY;
		cmd->action = EIGRP_UNIX_CONFIG_TOPOLOGY;
		result = EIGRP_RESULT_SUCCESS;
	} else {
		size_t i = 0;
		if (argc > 1 && strcmp(argv[0], "no") == 0) {
			negated = true;
			cmd->operation = EIGRP_RESET;
			i++;
		}
		if (!negated && parse->mode == EIGRP_UNIX_MODE_AF && argc >= 4
		    && argc <= 5 && strcmp(argv[0], "distribute-list") == 0
		    && (strcmp(argv[1], "prefix") == 0
			|| strcmp(argv[1], "access") == 0)
		    && eigrp_unix_config_name(argv[2])
		    && (strcmp(argv[3], "in") == 0
			|| strcmp(argv[3], "out") == 0)
		    && (argc == 4 || eigrp_unix_config_name(argv[4]))) {
			cmd->action = EIGRP_UNIX_CONFIG_DISTRIBUTE;
			cmd->filter_type = strcmp(argv[1], "prefix") == 0
						   ? EIGRP_DIST_PREFIX_LIST
						   : EIGRP_DIST_ACCESS_LIST;
			cmd->direction = strcmp(argv[3], "in") == 0
						 ? EIGRP_OFFSET_IN
						 : EIGRP_OFFSET_OUT;
			memcpy(cmd->policy_name, argv[2], strlen(argv[2]) + 1);
			if (argc == 5)
				memcpy(cmd->ifname, argv[4],
				       strlen(argv[4]) + 1);
			result = EIGRP_RESULT_SUCCESS;
		} else if (argc == i + 1 && strcmp(argv[i], "shutdown") == 0
			   && parse->mode != EIGRP_UNIX_MODE_TOPOLOGY
			   && parse->mode != EIGRP_UNIX_MODE_GLOBAL) {
			cmd->action = parse->mode == EIGRP_UNIX_MODE_INTERFACE
					      ? EIGRP_UNIX_CONFIG_INTERFACE_FLAG
					      : EIGRP_UNIX_CONFIG_SHUTDOWN;
			cmd->key = EIGRP_UNIX_INTF_SHUTDOWN;
			result = EIGRP_RESULT_SUCCESS;
		} else if (!negated && parse->mode == EIGRP_UNIX_MODE_AF
			   && argc == 3 && strcmp(argv[0], "eigrp") == 0
			   && strcmp(argv[1], "router-id") == 0) {
			cmd->action = EIGRP_UNIX_CONFIG_ROUTER_ID;
			result = eigrp_unix_config_address(
					 argv[2], EIGRP_AFI_IPV4, &cmd->address)
					 ? EIGRP_RESULT_SUCCESS
					 : EIGRP_RESULT_INVALID_ARGUMENT;
		} else if (!negated && parse->mode == EIGRP_UNIX_MODE_AF
			   && argc >= 2 && argc <= 3
			   && strcmp(argv[0], "network") == 0
			   && parse->afi == EIGRP_AFI_IPV4) {
			cmd->action = EIGRP_UNIX_CONFIG_NETWORK;
			result = eigrp_unix_config_network(argv, argc,
							   &cmd->prefix)
					 ? EIGRP_RESULT_SUCCESS
					 : EIGRP_RESULT_INVALID_ARGUMENT;
		} else if (!negated && parse->mode == EIGRP_UNIX_MODE_AF
			   && argc == 3 && strcmp(argv[0], "neighbor") == 0
			   && eigrp_unix_config_name(argv[2])) {
			cmd->action = EIGRP_UNIX_CONFIG_NEIGHBOR;
			memcpy(cmd->ifname, argv[2], strlen(argv[2]) + 1U);
			result = eigrp_unix_config_address(argv[1], parse->afi,
							   &cmd->address)
					 ? EIGRP_RESULT_SUCCESS
					 : EIGRP_RESULT_INVALID_ARGUMENT;
		} else if (!negated && parse->mode == EIGRP_UNIX_MODE_AF
			   && argc == 4 && strcmp(argv[0], "distance") == 0
			   && strcmp(argv[1], "eigrp") == 0) {
			cmd->action = EIGRP_UNIX_CONFIG_DISTANCE;
			result = eigrp_unix_config_number(argv[2], 1, 255,
							  &cmd->numbers[0])
						 && eigrp_unix_config_number(
							 argv[3], 1, 255,
							 &cmd->numbers[1])
					 ? EIGRP_RESULT_SUCCESS
					 : EIGRP_RESULT_INVALID_ARGUMENT;
		} else if (!negated && parse->mode == EIGRP_UNIX_MODE_AF
			   && (argc == 8 || argc == 9)
			   && strcmp(argv[0], "metric") == 0
			   && strcmp(argv[1], "weights") == 0) {
			cmd->action = EIGRP_UNIX_CONFIG_WEIGHTS;
			result = eigrp_unix_config_weights(&argv[2], argc - 2,
							   cmd->numbers)
					 ? EIGRP_RESULT_SUCCESS
					 : EIGRP_RESULT_INVALID_ARGUMENT;
		} else if (!negated && parse->mode == EIGRP_UNIX_MODE_TOPOLOGY
			   && argc == 6
			   && strcmp(argv[0], "default-metric") == 0) {
			cmd->action = EIGRP_UNIX_CONFIG_METRIC;
			result = eigrp_unix_config_metric(&argv[1], 5,
							  cmd->numbers)
					 ? EIGRP_RESULT_SUCCESS
					 : EIGRP_RESULT_INVALID_ARGUMENT;
		} else if (!negated && parse->mode == EIGRP_UNIX_MODE_TOPOLOGY
			   && argc == 2 && strcmp(argv[0], "variance") == 0) {
			cmd->action = EIGRP_UNIX_CONFIG_VARIANCE;
			result = eigrp_unix_config_number(argv[1], 1, 128,
							  &cmd->numbers[0])
					 ? EIGRP_RESULT_SUCCESS
					 : EIGRP_RESULT_INVALID_ARGUMENT;
		} else if (!negated && parse->mode == EIGRP_UNIX_MODE_TOPOLOGY
			   && argc == 3 && strcmp(argv[0], "metric") == 0
			   && strcmp(argv[1], "maximum-hops") == 0) {
			cmd->action = EIGRP_UNIX_CONFIG_MAXIMUM_HOPS;
			result = eigrp_unix_config_number(argv[2], 1, 255,
							  &cmd->numbers[0])
					 ? EIGRP_RESULT_SUCCESS
					 : EIGRP_RESULT_INVALID_ARGUMENT;
		} else if (!negated && parse->mode == EIGRP_UNIX_MODE_TOPOLOGY
			   && argc == 2
			   && strcmp(argv[0], "maximum-paths") == 0) {
			cmd->action = EIGRP_UNIX_CONFIG_MAXIMUM_PATHS;
			result = eigrp_unix_config_number(argv[1], 1, 32,
							  &cmd->numbers[0])
					 ? EIGRP_RESULT_SUCCESS
					 : EIGRP_RESULT_INVALID_ARGUMENT;
		} else if (!negated && parse->mode == EIGRP_UNIX_MODE_TOPOLOGY
			   && (argc == 2 || argc == 4 || argc == 8
			       || argc == 10)
			   && strcmp(argv[0], "redistribute") == 0
			   && (strcmp(argv[1], "connected") == 0
			       || strcmp(argv[1], "static") == 0)) {
			cmd->action = EIGRP_UNIX_CONFIG_REDISTRIBUTE;
			cmd->numbers[5] =
				strcmp(argv[1], "connected") == 0
					? EIGRP_REDIST_PROTOCOL_CONNECTED
					: EIGRP_REDIST_PROTOCOL_STATIC;
			if (argc == 8 || argc == 10) {
				if (strcmp(argv[2], "metric") != 0
				    || !eigrp_unix_config_metric(
					    &argv[3], 5, cmd->numbers)) {
					result = EIGRP_RESULT_INVALID_ARGUMENT;
					goto done;
				}
			}
			if (argc == 4 || argc == 10) {
				size_t at = argc == 4 ? 2 : 8;
				if (strcmp(argv[at], "route-map") != 0
				    || !eigrp_unix_config_name(argv[at + 1])) {
					result = EIGRP_RESULT_UNSUPPORTED;
					goto done;
				}
				memcpy(cmd->policy_name, argv[at + 1],
				       strlen(argv[at + 1]) + 1);
			}
			result = EIGRP_RESULT_SUCCESS;
		} else if (parse->mode == EIGRP_UNIX_MODE_INTERFACE
			   && parse->afi == EIGRP_AFI_IPV4 && !negated
			   && argc == 3
			   && strcmp(argv[0], "authentication") == 0
			   && strcmp(argv[1], "mode") == 0
			   && strcmp(argv[2], "md5") == 0) {
			cmd->action = EIGRP_UNIX_CONFIG_AUTH_MODE;
			result = EIGRP_RESULT_SUCCESS;
		} else if (parse->mode == EIGRP_UNIX_MODE_INTERFACE
			   && parse->afi == EIGRP_AFI_IPV4 && !negated
			   && argc == 3
			   && strcmp(argv[0], "authentication") == 0
			   && strcmp(argv[1], "key-chain") == 0
			   && eigrp_unix_config_name(argv[2])) {
			cmd->action = EIGRP_UNIX_CONFIG_AUTH_KEYCHAIN;
			memcpy(cmd->policy_name, argv[2], strlen(argv[2]) + 1);
			result = EIGRP_RESULT_SUCCESS;
		} else if (parse->mode == EIGRP_UNIX_MODE_INTERFACE) {
			static const struct {
				const char *name;
				eigrp_unix_config_interface_key_t key;
				uint32_t low;
				uint32_t high;
				bool flag;
			} keys[] = {
				{"bandwidth-percent",
				 EIGRP_UNIX_INTF_BANDWIDTH_PERCENT, 1, 100,
				 false},
				{"bandwidth", EIGRP_UNIX_INTF_BANDWIDTH, 1,
				 10000000, false},
				{"delay", EIGRP_UNIX_INTF_DELAY, 1, 16777215,
				 false},
				{"hello-interval", EIGRP_UNIX_INTF_HELLO, 1,
				 65535, false},
				{"hold-time", EIGRP_UNIX_INTF_HOLD, 1, 65535,
				 false},
				{"passive-interface", EIGRP_UNIX_INTF_PASSIVE,
				 0, 0, true},
				{"split-horizon", EIGRP_UNIX_INTF_SPLIT_HORIZON,
				 0, 0, true},
				{"next-hop-self", EIGRP_UNIX_INTF_NEXT_HOP_SELF,
				 0, 0, true},
			};
			size_t j;

			for (j = 0; j < sizeof(keys) / sizeof(keys[0]); ++j) {
				if (strcmp(argv[i], keys[j].name) != 0)
					continue;
				cmd->key = keys[j].key;
				if (keys[j].flag) {
					if (argc == i + 1) {
						cmd->action =
							EIGRP_UNIX_CONFIG_INTERFACE_FLAG;
						result = EIGRP_RESULT_SUCCESS;
					}
				} else if (!negated && argc == i + 2
					   && eigrp_unix_config_number(
						   argv[i + 1], keys[j].low,
						   keys[j].high,
						   &cmd->numbers[0])) {
					cmd->action =
						EIGRP_UNIX_CONFIG_INTERFACE_VALUE;
					result = EIGRP_RESULT_SUCCESS;
				}
				break;
			}
		}
	}

done:
	if (result == EIGRP_RESULT_SUCCESS) {
		result = eigrp_unix_config_command_add(config, parse, cmd);
		if (result == EIGRP_RESULT_SUCCESS)
			return result;
	}
	eigrp_unix_config_scrub(cmd->secret, sizeof(cmd->secret));
	free(cmd);
	return result;
}

eigrp_unix_config_t *eigrp_unix_config_create(void)
{
	return calloc(1, sizeof(eigrp_unix_config_t));
}

void eigrp_unix_config_delete(eigrp_unix_config_t *config)
{
	if (config) {
		eigrp_unix_config_clear(config);
		free(config);
	}
}

eigrp_result_t eigrp_unix_config_parse_file(eigrp_unix_config_t *config,
					    FILE *input, unsigned *error_line)
{
	eigrp_unix_config_t staged = {0};
	eigrp_unix_config_parse_t parse = {0};
	char line[EIGRP_UNIX_CONFIG_LINE_MAX];
	unsigned line_number = 0;
	eigrp_result_t result = EIGRP_RESULT_SUCCESS;

	if (error_line)
		*error_line = 0;
	if (!config || !input || config->committed)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	while (fgets(line, sizeof(line), input)) {
		line_number++;
		if (!strchr(line, '\n') && !feof(input)) {
			result = EIGRP_RESULT_INVALID_ARGUMENT;
			eigrp_unix_config_scrub(line, sizeof(line));
			break;
		}
		result = eigrp_unix_config_line_parse(&staged, &parse, line,
						      line_number);
		eigrp_unix_config_scrub(line, sizeof(line));
		if (result != EIGRP_RESULT_SUCCESS)
			break;
	}
	if (result == EIGRP_RESULT_SUCCESS) {
		eigrp_unix_config_command_t *cmd;
		struct stat st;
		for (cmd = staged.head; cmd; cmd = cmd->next) {
			if (cmd->action != EIGRP_UNIX_CONFIG_KEYCHAIN)
				continue;
			if (fstat(fileno(input), &st) != 0
			    || !S_ISREG(st.st_mode)
			    || (st.st_mode & 077) != 0) {
				result = EIGRP_RESULT_UNSUPPORTED;
				line_number = cmd->line;
				break;
			}
		}
	}
	/* Reject missing policy objects at validation time rather than relying
	 * on a permissive UUT placeholder or a partial runtime commit. */
	if (result == EIGRP_RESULT_SUCCESS) {
		eigrp_unix_config_command_t *cmd, *other;
		for (cmd = staged.head; cmd; cmd = cmd->next) {
			bool found = false;
			/* MD5 must have a configured chain on the same
			 * interface, AF, and instance. Otherwise startup
			 * silently loses peers. */
			if (cmd->action == EIGRP_UNIX_CONFIG_AUTH_MODE) {
				for (other = staged.head; other;
				     other = other->next)
					if (other->action
						    == EIGRP_UNIX_CONFIG_AUTH_KEYCHAIN
					    && !strcmp(cmd->name, other->name)
					    && !strcmp(cmd->ifname,
						       other->ifname)
					    && cmd->asn == other->asn
					    && cmd->afi == other->afi)
						found = true;
				if (!found) {
					result = EIGRP_RESULT_UNSUPPORTED;
					line_number = cmd->line;
					break;
				}
			}
			/* A key-chain directive without MD5 would create an
			 * unauthenticated adjacency despite apparent security
			 * config. */
			if (cmd->action == EIGRP_UNIX_CONFIG_AUTH_KEYCHAIN) {
				for (other = staged.head; other;
				     other = other->next)
					if (other->action
						    == EIGRP_UNIX_CONFIG_AUTH_MODE
					    && !strcmp(cmd->name, other->name)
					    && !strcmp(cmd->ifname,
						       other->ifname)
					    && cmd->asn == other->asn
					    && cmd->afi == other->afi)
						found = true;
				if (!found) {
					result = EIGRP_RESULT_UNSUPPORTED;
					line_number = cmd->line;
					break;
				}
			}
			/* Reject conflicting duplicate rules/keychain
			 * registrations instead of applying a surprising
			 * last-writer-wins plan. */
			for (other = staged.head; other && other != cmd;
			     other = other->next) {
				if (((cmd->action == EIGRP_UNIX_CONFIG_RULE
				      && other->action == EIGRP_UNIX_CONFIG_RULE
				      && cmd->filter_type == other->filter_type)
				     || (cmd->action == EIGRP_UNIX_CONFIG_MAP
					 && other->action
						    == EIGRP_UNIX_CONFIG_MAP)
				     || (cmd->action
						 == EIGRP_UNIX_CONFIG_KEYCHAIN
					 && other->action
						    == EIGRP_UNIX_CONFIG_KEYCHAIN))
				    && !strcmp(cmd->policy_name,
					       other->policy_name)
				    && (cmd->action
						== EIGRP_UNIX_CONFIG_KEYCHAIN
					|| cmd->numbers[0]
						   == other->numbers[0])) {
					result = EIGRP_RESULT_INVALID_ARGUMENT;
					line_number = cmd->line;
					break;
				}
			}
			if (result != EIGRP_RESULT_SUCCESS)
				break;
			if (cmd->action != EIGRP_UNIX_CONFIG_DISTRIBUTE
			    && !(cmd->action == EIGRP_UNIX_CONFIG_REDISTRIBUTE
				 && cmd->policy_name[0])
			    && cmd->action != EIGRP_UNIX_CONFIG_AUTH_KEYCHAIN
			    && !(cmd->action == EIGRP_UNIX_CONFIG_MAP
				 && (cmd->match == EIGRP_UNIX_MATCH_PREFIX_LIST
				     || cmd->match
						== EIGRP_UNIX_MATCH_ACCESS_LIST)))
				continue;
			for (other = staged.head; other; other = other->next) {
				if (cmd->action
					    == EIGRP_UNIX_CONFIG_AUTH_KEYCHAIN
				    && other->action
					       == EIGRP_UNIX_CONFIG_KEYCHAIN
				    && !strcmp(cmd->policy_name,
					       other->policy_name))
					found = true;
				else if (
					cmd->action
						== EIGRP_UNIX_CONFIG_REDISTRIBUTE
					&& other->action
						   == EIGRP_UNIX_CONFIG_MAP
					&& !strcmp(cmd->policy_name,
						   other->policy_name))
					found = true;
				else if (cmd->action
						 == EIGRP_UNIX_CONFIG_DISTRIBUTE
					 && other->action
						    == EIGRP_UNIX_CONFIG_RULE
					 && cmd->filter_type
						    == other->filter_type
					 && cmd->afi
						    == other->prefix.address.afi
					 && !strcmp(cmd->policy_name,
						    other->policy_name))
					found = true;
				else if (
					cmd->action == EIGRP_UNIX_CONFIG_MAP
					&& other->action
						   == EIGRP_UNIX_CONFIG_RULE
					&& other->filter_type
						   == (cmd->match == EIGRP_UNIX_MATCH_PREFIX_LIST
							       ? EIGRP_DIST_PREFIX_LIST
							       : EIGRP_DIST_ACCESS_LIST)
					&& !strcmp(cmd->reference,
						   other->policy_name))
					found = true;
			}
			if (!found) {
				result = EIGRP_RESULT_UNSUPPORTED;
				line_number = cmd->line;
				break;
			}
		}
	}
	if (result == EIGRP_RESULT_SUCCESS && ferror(input))
		result = EIGRP_RESULT_INTERNAL_FAILURE;
	if (result == EIGRP_RESULT_SUCCESS
	    && parse.mode != EIGRP_UNIX_MODE_GLOBAL)
		result = EIGRP_RESULT_INVALID_ARGUMENT;
	if (result != EIGRP_RESULT_SUCCESS) {
		if (error_line)
			*error_line = line_number;
		eigrp_unix_config_clear(&staged);
		return result;
	}
	eigrp_unix_config_clear(config);
	*config = staged;
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_unix_config_parse_text(eigrp_unix_config_t *config,
					    const char *text,
					    unsigned *error_line)
{
	FILE *input;
	eigrp_result_t result;

	if (!config || !text)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	input = tmpfile();
	if (!input)
		return EIGRP_RESULT_INTERNAL_FAILURE;
	if (fputs(text, input) == EOF || fflush(input) != 0
	    || fseek(input, 0, SEEK_SET) != 0) {
		fclose(input);
		return EIGRP_RESULT_INTERNAL_FAILURE;
	}
	result = eigrp_unix_config_parse_file(config, input, error_line);
	fclose(input);
	return result;
}

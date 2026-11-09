// SPDX-License-Identifier: GPL-2.0-or-later
/* Native Linux host policy definitions, independent of the virtual-wire UUT.
 * Copyright (C) 2026 Donnie V. Savage
 */
#ifndef EIGRP_LINUX_POLICY_H_
#define EIGRP_LINUX_POLICY_H_

#include "eigrp_sys.h"

typedef enum eigrp_linux_policy_match {
	EIGRP_LINUX_MATCH_ANY,
	EIGRP_LINUX_MATCH_PREFIX_LIST,
	EIGRP_LINUX_MATCH_ACCESS_LIST,
	EIGRP_LINUX_MATCH_TAG,
} eigrp_linux_policy_match_t;

/* Rules are ordered by sequence, first match wins, implicit deny.
 * ge/le both zero means an exact prefix-list length; ACLs compare destination
 * addresses in a CIDR (like a standard destination-network ACL).
 */
eigrp_result_t eigrp_linux_policy_rule_set(eigrp_distribute_list_type_t type,
					   const char *name, uint32_t sequence,
					   eigrp_filter_decision_t decision,
					   const eigrp_prefix_t *prefix,
					   uint8_t ge, uint8_t le);
eigrp_result_t eigrp_linux_policy_map_set(const char *name, uint32_t sequence,
					  eigrp_filter_decision_t decision,
					  eigrp_linux_policy_match_t match,
					  const char *reference, uint32_t tag);
/* Remove one rule; return NOT_FOUND if the sequence does not exist. */
eigrp_result_t eigrp_linux_policy_rule_remove(eigrp_distribute_list_type_t type,
					      const char *name,
					      uint32_t sequence);
eigrp_result_t eigrp_linux_policy_map_remove(const char *name,
					     uint32_t sequence);
bool eigrp_linux_policy_rule_exists(eigrp_distribute_list_type_t type,
				    const char *name);
bool eigrp_linux_policy_map_exists(const char *name);
/* Release all owned objects. Never used by the virtual-wire UUT. */
void eigrp_linux_policy_reset(void);

#endif /* EIGRP_LINUX_POLICY_H_ */

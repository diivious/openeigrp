// SPDX-License-Identifier: ISC
/* Native Linux policy behavior and refresh contract tests.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <assert.h>
#include <arpa/inet.h>
#include <stdio.h>
#include <string.h>

#include "eigrp_linux_policy.h"

static unsigned filter_updates, redist_updates, summary_updates;
void eigrp_sys_policy_runtime_update(void)
{
	filter_updates++;
}
void eigrp_redist_policy_update_all(void)
{
	redist_updates++;
}
void eigrp_summary_policy_update_all(void)
{
	summary_updates++;
}

static eigrp_prefix_t prefix(const char *cidr)
{
	eigrp_prefix_t p = {0};
	char ip[128];
	unsigned length;
	int family;
	assert(sscanf(cidr, "%127[^/]/%u", ip, &length) == 2);
	family = strchr(ip, ':') ? AF_INET6 : AF_INET;
	p.address.afi = family == AF_INET ? EIGRP_AFI_IPV4 : EIGRP_AFI_IPV6;
	assert(inet_pton(family, ip, p.address.bytes) == 1);
	assert(length <= (family == AF_INET ? 32U : 128U));
	p.prefix_length = (uint8_t)length;
	return p;
}

static bool permitted(eigrp_distribute_list_type_t type, const char *name,
		      const char *cidr)
{
	eigrp_filter_decision_t decision = EIGRP_FILTER_DECISION_PERMIT;
	eigrp_prefix_t p = prefix(cidr);
	assert(eigrp_sys_filter_evaluate(NULL, type, name, &p, &decision)
	       == EIGRP_RESULT_SUCCESS);
	return decision == EIGRP_FILTER_DECISION_PERMIT;
}

static bool mapped(const char *name, const char *cidr, uint32_t tag,
		   bool summary)
{
	eigrp_filter_decision_t decision = EIGRP_FILTER_DECISION_PERMIT;
	eigrp_rib_route_t r = {0};
	r.prefix = prefix(cidr);
	r.tag = tag;
	if (summary)
		assert(eigrp_sys_summary_leak_map_evaluate(NULL, name,
							   &r.prefix, &decision)
		       == EIGRP_RESULT_SUCCESS);
	else
		assert(eigrp_sys_redistribute_route_map_evaluate(NULL, name, &r,
								 &decision)
		       == EIGRP_RESULT_SUCCESS);
	return decision == EIGRP_FILTER_DECISION_PERMIT;
}

int main(void)
{
	eigrp_prefix_t net = prefix("10.0.0.0/8");
	eigrp_prefix_t host = prefix("10.1.0.0/16");
	eigrp_prefix_t v6 = prefix("2001:db8::/32");
	unsigned previous;

	eigrp_sys_policy_init();
	/* A configured reference cannot become permit-all when absent. */
	assert(!permitted(EIGRP_DIST_PREFIX_LIST, "not-there", "10.1.0.0/16"));
	assert(!permitted(EIGRP_DIST_ACCESS_LIST, "not-there", "10.1.0.0/16"));
	assert(!mapped("missing", "10.1.0.0/16", 0, false));
	assert(!mapped("missing", "10.1.0.0/16", 0, true));
	assert(eigrp_linux_policy_rule_set(EIGRP_DIST_PREFIX_LIST, "allow", 10,
					   EIGRP_FILTER_DECISION_PERMIT, &net,
					   16, 24)
	       == EIGRP_RESULT_SUCCESS);
	assert(!permitted(EIGRP_DIST_PREFIX_LIST, "allow", "10.0.0.0/8"));
	assert(permitted(EIGRP_DIST_PREFIX_LIST, "allow", "10.1.0.0/16"));
	assert(permitted(EIGRP_DIST_PREFIX_LIST, "allow", "10.1.2.0/24"));
	assert(!permitted(EIGRP_DIST_PREFIX_LIST, "allow", "10.1.2.0/25"));
	assert(!permitted(EIGRP_DIST_PREFIX_LIST, "allow", "11.0.0.0/16"));
	assert(eigrp_linux_policy_rule_set(EIGRP_DIST_PREFIX_LIST, "allow", 5,
					   EIGRP_FILTER_DECISION_DENY, &host, 0,
					   0)
	       == EIGRP_RESULT_SUCCESS);
	assert(!permitted(EIGRP_DIST_PREFIX_LIST, "allow", "10.1.0.0/16"));
	assert(permitted(EIGRP_DIST_PREFIX_LIST, "allow", "10.1.2.0/24"));
	/* A zero-length prefix list entry without ge/le matches only /0. */
	{
		eigrp_prefix_t zero = prefix("0.0.0.0/0");
		assert(eigrp_linux_policy_rule_set(
			       EIGRP_DIST_PREFIX_LIST, "default-only", 1,
			       EIGRP_FILTER_DECISION_PERMIT, &zero, 0, 0)
		       == EIGRP_RESULT_SUCCESS);
		assert(permitted(EIGRP_DIST_PREFIX_LIST, "default-only",
				 "0.0.0.0/0"));
		assert(!permitted(EIGRP_DIST_PREFIX_LIST, "default-only",
				  "1.0.0.0/8"));
	}
	assert(eigrp_linux_policy_rule_set(EIGRP_DIST_ACCESS_LIST, "dest", 10,
					   EIGRP_FILTER_DECISION_PERMIT, &net,
					   0, 0)
	       == EIGRP_RESULT_SUCCESS);
	assert(permitted(EIGRP_DIST_ACCESS_LIST, "dest", "10.25.0.0/24"));
	assert(!permitted(EIGRP_DIST_ACCESS_LIST, "dest", "192.0.2.0/24"));
	assert(eigrp_linux_policy_rule_set(EIGRP_DIST_PREFIX_LIST, "v6", 2,
					   EIGRP_FILTER_DECISION_PERMIT, &v6,
					   64, 64)
	       == EIGRP_RESULT_SUCCESS);
	assert(permitted(EIGRP_DIST_PREFIX_LIST, "v6", "2001:db8:1::/64"));
	assert(!permitted(EIGRP_DIST_PREFIX_LIST, "v6", "2001:db9::/64"));
	assert(eigrp_linux_policy_map_set(
		       "static-map", 10, EIGRP_FILTER_DECISION_PERMIT,
		       EIGRP_LINUX_MATCH_PREFIX_LIST, "allow", 0)
	       == EIGRP_RESULT_SUCCESS);
	assert(mapped("static-map", "10.1.2.0/24", 0, false));
	assert(!mapped("static-map", "10.1.0.0/16", 0, false));
	assert(mapped("static-map", "10.1.2.0/24", 0, true));
	assert(eigrp_linux_policy_map_set("static-map", 5,
					  EIGRP_FILTER_DECISION_DENY,
					  EIGRP_LINUX_MATCH_TAG, NULL, 42)
	       == EIGRP_RESULT_SUCCESS);
	assert(!mapped("static-map", "10.1.2.0/24", 42, false));
	assert(mapped("static-map", "10.1.2.0/24", 42, true));
	assert(!mapped("static-map", "192.0.2.0/24", 0, false));
	assert(eigrp_linux_policy_map_set("all", 1,
					  EIGRP_FILTER_DECISION_PERMIT,
					  EIGRP_LINUX_MATCH_ANY, NULL, 0)
	       == EIGRP_RESULT_SUCCESS);
	assert(mapped("all", "192.0.2.0/24", 0, false));
	assert(eigrp_linux_policy_rule_set(EIGRP_DIST_PREFIX_LIST, "allow", 10,
					   EIGRP_FILTER_DECISION_DENY, &net, 16,
					   24)
	       == EIGRP_RESULT_SUCCESS);
	assert(!mapped("static-map", "10.1.2.0/24", 0, false));
	assert(eigrp_linux_policy_rule_remove(EIGRP_DIST_PREFIX_LIST, "allow",
					      10)
	       == EIGRP_RESULT_SUCCESS);
	assert(!permitted(EIGRP_DIST_PREFIX_LIST, "allow", "10.1.2.0/24"));
	assert(eigrp_linux_policy_rule_remove(EIGRP_DIST_PREFIX_LIST, "allow",
					      10)
	       == EIGRP_RESULT_NOT_FOUND);
	assert(eigrp_linux_policy_map_remove("all", 1) == EIGRP_RESULT_SUCCESS);
	assert(!mapped("all", "192.0.2.0/24", 0, false));
	assert(eigrp_linux_policy_rule_set(EIGRP_DIST_PREFIX_LIST, "bad", 1,
					   EIGRP_FILTER_DECISION_PERMIT, &net,
					   25, 24)
	       == EIGRP_RESULT_INVALID_ARGUMENT);
	assert(eigrp_linux_policy_rule_set(EIGRP_DIST_ACCESS_LIST, "bad", 1,
					   EIGRP_FILTER_DECISION_PERMIT, &net,
					   1, 32)
	       == EIGRP_RESULT_UNSUPPORTED);
	assert(!eigrp_linux_policy_map_exists("not-there"));
	assert(eigrp_linux_policy_map_exists("static-map"));
	assert(eigrp_linux_policy_rule_exists(EIGRP_DIST_PREFIX_LIST, "allow"));
	assert(filter_updates > 8 && filter_updates == redist_updates
	       && filter_updates == summary_updates);
	/* Bound the database, then prove replacement works at capacity and a
	 * deletion releases one slot without changing default-deny behavior. */
	{
		uint32_t sequence;
		unsigned added = 0;
		eigrp_result_t result = EIGRP_RESULT_SUCCESS;
		for (sequence = 1; sequence <= 4096; ++sequence) {
			result = eigrp_linux_policy_rule_set(
				EIGRP_DIST_PREFIX_LIST, "capacity", sequence,
				EIGRP_FILTER_DECISION_PERMIT, &net, 16, 24);
			if (result != EIGRP_RESULT_SUCCESS)
				break;
			added++;
		}
		assert(result == EIGRP_RESULT_CONFLICT);
		assert(added >= 4000 && added < 4096);
		assert(eigrp_linux_policy_rule_set(
			       EIGRP_DIST_PREFIX_LIST, "capacity", 1,
			       EIGRP_FILTER_DECISION_DENY, &net, 16, 24)
		       == EIGRP_RESULT_SUCCESS);
		assert(!permitted(EIGRP_DIST_PREFIX_LIST, "capacity",
				  "10.1.2.0/24"));
		assert(eigrp_linux_policy_rule_remove(EIGRP_DIST_PREFIX_LIST,
						      "capacity", 1)
		       == EIGRP_RESULT_SUCCESS);
		assert(eigrp_linux_policy_rule_set(
			       EIGRP_DIST_PREFIX_LIST, "capacity", sequence,
			       EIGRP_FILTER_DECISION_PERMIT, &net, 16, 24)
		       == EIGRP_RESULT_SUCCESS);
		assert(filter_updates == redist_updates
		       && filter_updates == summary_updates);
	}
	previous = filter_updates;
	eigrp_sys_policy_finish();
	assert(!permitted(EIGRP_DIST_ACCESS_LIST, "dest", "10.25.0.0/24"));
	assert(!mapped("static-map", "10.1.2.0/24", 0, false));
	assert(filter_updates == previous);
	puts("linux policy tests: PASS");
	return 0;
}

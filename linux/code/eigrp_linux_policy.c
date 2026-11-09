// SPDX-License-Identifier: GPL-2.0-or-later
/* Linux EIGRP host policy evaluation. No DUAL or TLV processing here.
 * Copyright (C) 2026 Donnie V. Savage
 */
#define _POSIX_C_SOURCE 200809L
#include <pthread.h>
#include <stdlib.h>
#include <string.h>

#include "eigrp_linux_policy.h"
#include "eigrp_redistribute.h"
#include "eigrp_summary.h"

#define POLICY_NAME_MAX 255U
#define POLICY_RULE_MAX 4096U

typedef struct linux_policy_rule {
	struct linux_policy_rule *next;
	char name[POLICY_NAME_MAX + 1];
	uint32_t sequence;
	eigrp_filter_decision_t decision;
	eigrp_prefix_t prefix;
	uint8_t ge;
	uint8_t le;
	eigrp_linux_policy_match_t match;
	char reference[POLICY_NAME_MAX + 1];
	uint32_t tag;
} linux_policy_rule_t;

static pthread_mutex_t policy_lock = PTHREAD_MUTEX_INITIALIZER;
static linux_policy_rule_t *access_rules;
static linux_policy_rule_t *prefix_rules;
static linux_policy_rule_t *map_rules;
static size_t policy_count;

static bool policy_name_valid(const char *name)
{
	return name && name[0] && strlen(name) <= POLICY_NAME_MAX;
}

static bool policy_prefix_valid(const eigrp_prefix_t *prefix)
{
	return prefix
	       && (prefix->address.afi == EIGRP_AFI_IPV4
		   || prefix->address.afi == EIGRP_AFI_IPV6)
	       && prefix->prefix_length
			  <= (prefix->address.afi == EIGRP_AFI_IPV4 ? 32 : 128);
}

static bool policy_prefix_contains(const eigrp_prefix_t *network,
				   const eigrp_prefix_t *prefix)
{
	unsigned full, rem;

	if (network->address.afi != prefix->address.afi)
		return false;
	full = network->prefix_length / 8;
	rem = network->prefix_length % 8;
	if (memcmp(network->address.bytes, prefix->address.bytes, full))
		return false;
	return !rem
	       || !((network->address.bytes[full] ^ prefix->address.bytes[full])
		    & (0xffU << (8 - rem)));
}

static linux_policy_rule_t **policy_head(eigrp_distribute_list_type_t type)
{
	return type == EIGRP_DIST_ACCESS_LIST	? &access_rules
	       : type == EIGRP_DIST_PREFIX_LIST ? &prefix_rules
						: NULL;
}

static bool policy_rules_evaluate(const linux_policy_rule_t *rule,
				  const char *name,
				  const eigrp_prefix_t *prefix)
{
	for (; rule; rule = rule->next) {
		if (strcmp(rule->name, name)
		    || !policy_prefix_contains(&rule->prefix, prefix))
			continue;
		if (rule->match == EIGRP_LINUX_MATCH_PREFIX_LIST
		    && prefix->prefix_length < rule->ge)
			continue;
		if (rule->match == EIGRP_LINUX_MATCH_PREFIX_LIST
		    && prefix->prefix_length > rule->le)
			continue;
		return rule->decision == EIGRP_FILTER_DECISION_PERMIT;
	}
	return false;
}

static bool policy_map_evaluate(const char *name, const eigrp_prefix_t *prefix,
				uint32_t tag, bool have_tag)
{
	const linux_policy_rule_t *rule;

	for (rule = map_rules; rule; rule = rule->next) {
		bool match = false;
		if (strcmp(rule->name, name))
			continue;
		switch (rule->match) {
		case EIGRP_LINUX_MATCH_ANY:
			match = true;
			break;
		case EIGRP_LINUX_MATCH_PREFIX_LIST:
			match = policy_rules_evaluate(prefix_rules,
						      rule->reference, prefix);
			break;
		case EIGRP_LINUX_MATCH_ACCESS_LIST:
			match = policy_rules_evaluate(access_rules,
						      rule->reference, prefix);
			break;
		case EIGRP_LINUX_MATCH_TAG:
			match = have_tag && tag == rule->tag;
			break;
		}
		if (match)
			return rule->decision == EIGRP_FILTER_DECISION_PERMIT;
	}
	return false;
}

/* The EIGRP core's re-evaluation entry points handle withdrawals, pending
 * advertisements and cached redistribution candidates. No policy hook may
 * call these while holding the host policy mutex. */
static void policy_changed(void)
{
	eigrp_sys_policy_runtime_update();
	eigrp_redist_policy_update_all();
	eigrp_summary_policy_update_all();
}

static eigrp_result_t policy_rule_store(linux_policy_rule_t **head,
					const linux_policy_rule_t *value)
{
	linux_policy_rule_t **cursor, *rule;

	pthread_mutex_lock(&policy_lock);
	cursor = head;
	while (*cursor
	       && ((*cursor)->sequence < value->sequence
		   || ((*cursor)->sequence == value->sequence
		       && strcmp((*cursor)->name, value->name) < 0)))
		cursor = &(*cursor)->next;
	if (*cursor && (*cursor)->sequence == value->sequence
	    && !strcmp((*cursor)->name, value->name)) {
		/* Updating an existing rule must not retire an object used by a
		 * concurrent evaluator. All reads also acquire policy_lock. */
		rule = *cursor;
		linux_policy_rule_t *next = rule->next;
		*rule = *value;
		rule->next = next;
	} else {
		if (policy_count >= POLICY_RULE_MAX) {
			pthread_mutex_unlock(&policy_lock);
			return EIGRP_RESULT_CONFLICT;
		}
		rule = malloc(sizeof(*rule));
		if (!rule) {
			pthread_mutex_unlock(&policy_lock);
			return EIGRP_RESULT_INTERNAL_FAILURE;
		}
		*rule = *value;
		rule->next = *cursor;
		*cursor = rule;
		policy_count++;
	}
	pthread_mutex_unlock(&policy_lock);
	policy_changed();
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_linux_policy_rule_set(eigrp_distribute_list_type_t type,
					   const char *name, uint32_t sequence,
					   eigrp_filter_decision_t decision,
					   const eigrp_prefix_t *prefix,
					   uint8_t ge, uint8_t le)
{
	linux_policy_rule_t value = {0};
	linux_policy_rule_t **head = policy_head(type);
	unsigned max;

	if (!head || !policy_name_valid(name) || !sequence
	    || !policy_prefix_valid(prefix)
	    || (decision != EIGRP_FILTER_DECISION_PERMIT
		&& decision != EIGRP_FILTER_DECISION_DENY))
		return EIGRP_RESULT_INVALID_ARGUMENT;
	max = prefix->address.afi == EIGRP_AFI_IPV4 ? 32 : 128;
	if (type == EIGRP_DIST_ACCESS_LIST) {
		if (ge || le)
			return EIGRP_RESULT_UNSUPPORTED;
	} else {
		if (!ge && !le)
			ge = le = prefix->prefix_length;
		else {
			if (!ge)
				ge = prefix->prefix_length;
			if (!le)
				le = max;
		}
		if (ge < prefix->prefix_length || ge > le || le > max)
			return EIGRP_RESULT_INVALID_ARGUMENT;
	}
	memcpy(value.name, name, strlen(name) + 1);
	value.sequence = sequence;
	value.decision = decision;
	value.prefix = *prefix;
	value.match = type == EIGRP_DIST_PREFIX_LIST
			      ? EIGRP_LINUX_MATCH_PREFIX_LIST
			      : EIGRP_LINUX_MATCH_ACCESS_LIST;
	value.ge = ge;
	value.le = le;
	return policy_rule_store(head, &value);
}

eigrp_result_t eigrp_linux_policy_map_set(const char *name, uint32_t sequence,
					  eigrp_filter_decision_t decision,
					  eigrp_linux_policy_match_t match,
					  const char *reference, uint32_t tag)
{
	linux_policy_rule_t value = {0};

	if (!policy_name_valid(name) || !sequence
	    || (decision != EIGRP_FILTER_DECISION_PERMIT
		&& decision != EIGRP_FILTER_DECISION_DENY)
	    || (unsigned)match > EIGRP_LINUX_MATCH_TAG
	    || ((match == EIGRP_LINUX_MATCH_PREFIX_LIST
		 || match == EIGRP_LINUX_MATCH_ACCESS_LIST)
		&& !policy_name_valid(reference))
	    || ((match == EIGRP_LINUX_MATCH_ANY
		 || match == EIGRP_LINUX_MATCH_TAG)
		&& reference))
		return EIGRP_RESULT_INVALID_ARGUMENT;
	memcpy(value.name, name, strlen(name) + 1);
	value.sequence = sequence;
	value.decision = decision;
	value.match = match;
	value.tag = tag;
	if (reference)
		memcpy(value.reference, reference, strlen(reference) + 1);
	return policy_rule_store(&map_rules, &value);
}

static eigrp_result_t policy_rule_delete(linux_policy_rule_t **head,
					 const char *name, uint32_t sequence)
{
	linux_policy_rule_t **cursor, *removed;
	if (!policy_name_valid(name) || !sequence)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	pthread_mutex_lock(&policy_lock);
	for (cursor = head; *cursor; cursor = &(*cursor)->next)
		if ((*cursor)->sequence == sequence
		    && !strcmp((*cursor)->name, name))
			break;
	if (!*cursor) {
		pthread_mutex_unlock(&policy_lock);
		return EIGRP_RESULT_NOT_FOUND;
	}
	removed = *cursor;
	*cursor = removed->next;
	free(removed);
	policy_count--;
	pthread_mutex_unlock(&policy_lock);
	policy_changed();
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_linux_policy_rule_remove(eigrp_distribute_list_type_t type,
					      const char *name,
					      uint32_t sequence)
{
	linux_policy_rule_t **head = policy_head(type);
	return head ? policy_rule_delete(head, name, sequence)
		    : EIGRP_RESULT_INVALID_ARGUMENT;
}

eigrp_result_t eigrp_linux_policy_map_remove(const char *name,
					     uint32_t sequence)
{
	return policy_rule_delete(&map_rules, name, sequence);
}

static bool policy_rules_exist(const linux_policy_rule_t *rules,
			       const char *name)
{
	for (; rules; rules = rules->next)
		if (!strcmp(rules->name, name))
			return true;
	return false;
}

bool eigrp_linux_policy_rule_exists(eigrp_distribute_list_type_t type,
				    const char *name)
{
	linux_policy_rule_t **head = policy_head(type);
	bool exists = false;
	if (!head || !policy_name_valid(name))
		return false;
	pthread_mutex_lock(&policy_lock);
	exists = policy_rules_exist(*head, name);
	pthread_mutex_unlock(&policy_lock);
	return exists;
}

bool eigrp_linux_policy_map_exists(const char *name)
{
	bool exists = false;
	if (!policy_name_valid(name))
		return false;
	pthread_mutex_lock(&policy_lock);
	exists = policy_rules_exist(map_rules, name);
	pthread_mutex_unlock(&policy_lock);
	return exists;
}

static void policy_rules_free(linux_policy_rule_t **head)
{
	linux_policy_rule_t *rule, *next;
	for (rule = *head; rule; rule = next) {
		next = rule->next;
		free(rule);
	}
	*head = NULL;
}

void eigrp_linux_policy_reset(void)
{
	pthread_mutex_lock(&policy_lock);
	policy_rules_free(&access_rules);
	policy_rules_free(&prefix_rules);
	policy_rules_free(&map_rules);
	policy_count = 0;
	pthread_mutex_unlock(&policy_lock);
}

void eigrp_sys_policy_init(void)
{
}

void eigrp_sys_policy_finish(void)
{
	eigrp_linux_policy_reset();
}

eigrp_result_t eigrp_sys_policy_instance_create(eigrp_instance_t *eigrp)
{
	return eigrp ? EIGRP_RESULT_SUCCESS : EIGRP_RESULT_INVALID_ARGUMENT;
}

void eigrp_sys_policy_instance_delete(eigrp_instance_t *eigrp)
{
	(void)eigrp;
}

eigrp_result_t eigrp_sys_filter_evaluate(eigrp_instance_t *eigrp,
					 eigrp_distribute_list_type_t type,
					 const char *name,
					 const eigrp_prefix_t *prefix,
					 eigrp_filter_decision_t *decision)
{
	linux_policy_rule_t **head = policy_head(type);
	bool permit;
	(void)eigrp;
	if (!head || !policy_name_valid(name) || !policy_prefix_valid(prefix)
	    || !decision)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	pthread_mutex_lock(&policy_lock);
	permit = policy_rules_evaluate(*head, name, prefix);
	pthread_mutex_unlock(&policy_lock);
	*decision = permit ? EIGRP_FILTER_DECISION_PERMIT
			   : EIGRP_FILTER_DECISION_DENY;
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_sys_redistribute_route_map_evaluate(
	eigrp_instance_t *eigrp, const char *name,
	const eigrp_rib_route_t *route, eigrp_filter_decision_t *decision)
{
	bool permit;
	(void)eigrp;
	if (!policy_name_valid(name) || !route
	    || !policy_prefix_valid(&route->prefix) || !decision)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	pthread_mutex_lock(&policy_lock);
	permit = policy_map_evaluate(name, &route->prefix, route->tag, true);
	pthread_mutex_unlock(&policy_lock);
	*decision = permit ? EIGRP_FILTER_DECISION_PERMIT
			   : EIGRP_FILTER_DECISION_DENY;
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t
eigrp_sys_summary_leak_map_evaluate(eigrp_instance_t *eigrp, const char *name,
				    const eigrp_prefix_t *prefix,
				    eigrp_filter_decision_t *decision)
{
	bool permit;
	(void)eigrp;
	if (!policy_name_valid(name) || !policy_prefix_valid(prefix)
	    || !decision)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	pthread_mutex_lock(&policy_lock);
	permit = policy_map_evaluate(name, prefix, 0, false);
	pthread_mutex_unlock(&policy_lock);
	*decision = permit ? EIGRP_FILTER_DECISION_PERMIT
			   : EIGRP_FILTER_DECISION_DENY;
	return EIGRP_RESULT_SUCCESS;
}

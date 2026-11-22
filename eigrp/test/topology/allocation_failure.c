// SPDX-License-Identifier: ISC
/* Copyright (C) 2026 Donnie V. Savage */
/* OE-08: Execute real topology constructors and list/table ownership under
 * deterministic allocation faults, without substituting implementation code. */
#include <assert.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

#include "eigrp.h"
#include "eigrp_structs.h"
#include "eigrp_topology.h"
#include "eigrp_list.h"
#include "eigrp_table.h"
#include "eigrp_log.h"
#include "eigrp_timer.h"

unsigned long term_debug_eigrp;
static unsigned allocation_index;
static unsigned fail_index;

/* The test compilation renames calloc only in the implementation objects.
 * Use the real allocator here so fault injection is deterministic across
 * Linux and macOS toolchains (which differ in linker wrap support). */
#undef calloc
void *calloc(size_t count, size_t size);
void *eigrp_test_calloc(size_t count, size_t size)
{
	if (fail_index && ++allocation_index == fail_index)
		return NULL;
	return calloc(count, size);
}

int eigrp_snprintf(char *dst, size_t size, const char *fmt, ...)
{
	va_list args;
	int result;
	va_start(args, fmt);
	result = vsnprintf(dst, size, fmt, args);
	va_end(args);
	return result;
}

void eigrp_log(eigrp_log_level_t level, const char *fmt, ...)
{
	(void)level;
	(void)fmt;
}

/* No timers are armed for newly constructed descriptors. */
void eigrp_timer_cancel(eigrp_instance_t *eigrp, eigrp_timer_event_t **timer)
{
	(void)eigrp;
	if (timer)
		*timer = NULL;
}

static void inject(unsigned index)
{
	allocation_index = 0;
	fail_index = index;
}

static eigrp_prefix_t prefix_key(void)
{
	eigrp_prefix_t key = {0};
	key.address.afi = EIGRP_AFI_IPV4;
	key.address.bytes[0] = 10;
	key.address.bytes[1] = 99;
	key.prefix_length = 24;
	return key;
}

static void test_constructor_failures(void)
{
	for (unsigned i = 1; i <= 4; i++) {
		eigrp_prefix_descriptor_t *prefix;
		inject(i);
		prefix = eigrp_topo_pfx_create();
		assert(prefix == NULL);
		inject(0);
	}
	inject(5);
	eigrp_prefix_descriptor_t *prefix = eigrp_topo_pfx_create();
	assert(prefix && prefix->internal_routes && prefix->external_routes
	       && prefix->rij);
	assert(prefix->rij->del == free);
	assert(prefix->distance == EIGRP_MAX_METRIC);
	eigrp_topo_pfx_free(prefix);
	inject(1);
	assert(eigrp_topo_route_create(NULL) == NULL);
	inject(0);
	eigrp_route_descriptor_t *route = eigrp_topo_route_create(NULL);
	assert(route && route->distance == EIGRP_MAX_METRIC);
	eigrp_topo_route_free(route);
}

static void test_publication_failure(void)
{
	eigrp_table_t *table;
	eigrp_prefix_descriptor_t *prefix;
	eigrp_prefix_descriptor_t *collision;
	eigrp_prefix_t key = prefix_key();

	inject(0);
	table = eigrp_topo_table_create();
	assert(table);
	prefix = eigrp_topo_pfx_create();
	collision = eigrp_topo_pfx_create();
	assert(prefix && collision);
	prefix->destination = key;
	collision->destination = key;
	inject(1); /* table_node allocation fails */
	assert(!eigrp_topo_pfx_add(table, prefix));
	assert(!prefix->table_node && !eigrp_table_node_lookup(table, &key));
	inject(0);
	assert(eigrp_topo_pfx_add(table, prefix));
	assert(!eigrp_topo_pfx_add(table, collision));
	assert(table->size == 1);
	assert(eigrp_table_node_lookup(table, &key)->info == prefix);
	eigrp_topo_pfx_free(collision);
	eigrp_table_node_remove(table, prefix->table_node);
	prefix->table_node = NULL;
	eigrp_topo_pfx_free(prefix);
	eigrp_table_free(table);
}

static void test_route_insert_failure(void)
{
	eigrp_instance_t runtime = {0};
	eigrp_prefix_descriptor_t *prefix;
	eigrp_route_descriptor_t *route;

	inject(0);
	prefix = eigrp_topo_pfx_create();
	route = eigrp_topo_route_create(NULL);
	assert(prefix && route);
	inject(1); /* list item allocation fails */
	assert(!eigrp_topo_route_insert(&runtime, prefix, route, false));
	assert(prefix->internal_routes->count == 0);
	assert(prefix->external_routes->count == 0);
	assert(!route->owner_ref);
	inject(0);
	assert(eigrp_topo_route_insert(&runtime, prefix, route, false));
	assert(prefix->internal_routes->count == 1);
	assert(eigrp_list_lookup(prefix->internal_routes, route));
	eigrp_topo_pfx_free(prefix);
}

static int compare_int(void *left, void *right)
{
	int a = *(const int *)left;
	int b = *(const int *)right;

	return (a > b) - (a < b);
}

static void test_sorted_path_reorder(void)
{
	eigrp_list_t *paths = eigrp_list_create();
	int first = 1, second = 2, third = 3;

	assert(paths);
	paths->cmp = compare_int;
	assert(eigrp_list_index_enable(paths));
	assert(eigrp_list_insert(paths, &first));
	assert(eigrp_list_insert(paths, &second));
	assert(eigrp_list_insert(paths, &third));
	first = 5;
	inject(1);
	assert(eigrp_list_reorder(paths, &first));
	assert(paths->head->data == &second);
	assert(paths->tail->data == &first);
	assert(paths->count == 3);
	assert(eigrp_list_lookup(paths, &first));
	second = 7;
	assert(eigrp_list_reorder(paths, &second));
	assert(paths->head->data == &third);
	assert(paths->tail->data == &second);
	inject(0);
	eigrp_list_delete(&paths);
}

static void test_owned_path_moves(void)
{
	eigrp_list_t *internal = eigrp_list_create();
	eigrp_list_t *external = eigrp_list_create();
	int first = 1, second = 2, third = 3;

	assert(internal && external);
	assert(eigrp_list_index_enable(internal));
	assert(eigrp_list_index_enable(external));
	assert(eigrp_list_add(internal, &first));
	assert(eigrp_list_add(internal, &second));
	assert(eigrp_list_add(internal, &third));
	inject(1);
	assert(eigrp_list_move(internal, external, &second));
	assert(internal->count == 2 && external->count == 1);
	assert(!eigrp_list_lookup(internal, &second));
	assert(eigrp_list_lookup(external, &second));
	assert(eigrp_list_move(external, internal, &second));
	assert(internal->count == 3 && external->count == 0);
	assert(eigrp_list_lookup(internal, &second));
	inject(0);
	eigrp_list_delete(&internal);
	eigrp_list_delete(&external);
}

int main(void)
{
	test_constructor_failures();
	test_publication_failure();
	test_route_insert_failure();
	test_owned_path_moves();
	test_sorted_path_reorder();
	puts("topology allocation failure: passed");
	return 0;
}

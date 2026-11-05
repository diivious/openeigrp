// SPDX-License-Identifier: ISC
// Copyright (C) 2026 Donnie V. Savage
/* Host route-map adapter for the stand-alone neighbor regression executable. */
#include "eigrp.h"
#include "eigrp_structs.h"
#include "eigrp_sys.h"

eigrp_result_t eigrp_sys_redistribute_route_map_evaluate(
	eigrp_instance_t *eigrp, const char *name,
	const eigrp_rib_route_t *route, eigrp_filter_decision_t *decision)
{
	(void)eigrp;
	(void)name;
	(void)route;
	if (!decision)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	*decision = EIGRP_FILTER_DECISION_PERMIT;
	return EIGRP_RESULT_SUCCESS;
}

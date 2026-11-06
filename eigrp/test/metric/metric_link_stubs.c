// SPDX-License-Identifier: ISC
// Copyright (C) 2026 Donnie V. Savage
/* Link-only callback for isolated classic-metric/DUAL C test fixtures.
 * These fixtures do not instantiate EIGRP's interface configuration layer.
 * Multigigabit interface-default behavior is covered by wide_metric.c and UUT.
 */
#include <assert.h>

#include "eigrp_interface.h"
#include "eigrp_metric.h"

bool eigrp_intf_delay_is_default(eigrp_intf_t *ei)
{
	/* Do not silently model wide-interface defaults as explicit delays. */
	assert(ei && ei->params.bandwidth < EIGRP_GIGABIT_KBPS);
	return false;
}

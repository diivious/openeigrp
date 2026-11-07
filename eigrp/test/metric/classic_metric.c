// SPDX-License-Identifier: ISC
/* Classic metric oracle regression, Cisco documented and TLV1 wire units.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <assert.h>
#include <stdint.h>
#include <string.h>

#include "eigrp.h"
#include "eigrp_structs.h"
#include "eigrp_metric.h"

static void defaults(eigrp_instance_t *eigrp)
{
	memset(eigrp, 0, sizeof(*eigrp));
	eigrp->metric_version = EIGRP_TLV_32B_VERSION;
	eigrp->k_values[0] = 1;
	eigrp->k_values[2] = 1;
	eigrp->max_hops = EIGRP_MAX_HOPS;
}

int main(void)
{
	eigrp_instance_t eigrp;
	eigrp_metrics_t m = {0};
	eigrp_metric_values_t seed = {0};
	eigrp_intf_t link = {0};
	eigrp_route_descriptor_t route = {0};

	defaults(&eigrp);
	m.load = 1;
	m.reliability = 255;

	/* A 100-Mbit link: TLV1 fields are already classic 256-scaled. */
	eigrp_metric_link_set(&m, 100000, 10, false);
	assert(m.bandwidth == 25600);
	assert(m.delay == 2560);
	assert(eigrp_metric_calculate(&eigrp, m) == 28160);

	/* Cisco's classic 56-kbit/s, 2,200-delay example truncates before
	 * multiplying by 256: (10000000 / 56 + 2200) * 256. */
	assert(eigrp_bandwidth_to_scaled(56) == 45714176);
	assert(eigrp_bandwidth_to_scaled(1544) == (10000000 / 1544) * 256);
	assert(eigrp_bandwidth_to_scaled(100000) == 25600);
	assert(eigrp_bandwidth_to_scaled(10000000) == 256);

	/* Redistribution and summary seeds must use the same internal units
	 * as connected and received metrics, not raw CLI delay units. */
	seed.bandwidth = 56;
	seed.delay = 2200;
	seed.reliability = 255;
	seed.load = 1;
	seed.mtu = 1500;
	eigrp_metric_values_convert(&seed, &m);
	assert(m.bandwidth == 45714176);
	assert(m.delay == 563200);
	assert(eigrp_metric_calculate(&eigrp, m) == 46277376);

	/* Unreachable classic delays must remain unreachable after scaling. */
	assert(eigrp_delay_to_scaled(EIGRP_MAX_METRIC) == EIGRP_MAX_METRIC);
	m.delay = EIGRP_MAX_METRIC;
	assert(eigrp_metric_calculate(&eigrp, m) == EIGRP_MAX_METRIC);

	/* Two hops: minimum bandwidth and additive scaled delay. */
	memset(&m, 0, sizeof(m));
	eigrp_metric_link_set(&m, 100000, 10, false);
	m.load = 1;
	m.reliability = 255;
	link.params.bandwidth = 100000;
	link.params.delay = 10;
	route.ei = &link;
	route.reported_metric = m;
	assert(eigrp_metric_total_calculate(&eigrp, &route) == 30720);
	assert(route.total_metric.bandwidth == 25600);
	assert(route.total_metric.delay == 5120);

	/* Classic K2 load and K5 reliability paths, without K6. */
	m = route.total_metric;
	eigrp.k_values[1] = 1;
	assert(eigrp_metric_calculate(&eigrp, m)
	       == 30720 + 25600 / (EIGRP_MAX_LOAD - 1));
	eigrp.k_values[1] = 0;
	eigrp.k_values[3] = 1;
	eigrp.k_values[4] = 1;
	assert(eigrp_metric_calculate(&eigrp, m) == 30720 / 256);
	return 0;
}

bool eigrp_intf_delay_is_default(eigrp_intf_t *ei)
{
	(void)ei;
	return false;
}

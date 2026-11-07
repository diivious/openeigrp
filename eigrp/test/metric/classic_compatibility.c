// SPDX-License-Identifier: ISC
/* Cisco classic-metric integer reference vectors and route accumulation.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <assert.h>
#include <stdint.h>
#include <string.h>

#include "eigrp.h"
#include "eigrp_structs.h"
#include "eigrp_metric.h"

int main(void)
{
	eigrp_instance_t eigrp = {0};
	eigrp_intf_t link = {0};
	eigrp_route_descriptor_t route = {0};
	eigrp_metrics_t metric = {0};
	eigrp_metric_values_t seed = {0};

	eigrp.metric_version = EIGRP_TLV_32B_VERSION;
	eigrp.k_values[0] = 1;
	eigrp.k_values[2] = 1;
	eigrp.max_hops = EIGRP_MAX_HOPS;

	/* IOS classic calculation truncates before scaling by 256. */
	assert(eigrp_bandwidth_to_scaled(56) == 45714176);
	assert(eigrp_bandwidth_to_scaled(1544) == 1657856);
	assert(eigrp_scaled_to_bandwidth(45714176) == 56);
	assert(eigrp_scaled_to_bandwidth(1657856) == 1544);
	assert(eigrp_scaled_to_bandwidth(0) == EIGRP_BANDWIDTH_MAX);
	assert(eigrp_bandwidth_to_scaled(0) == EIGRP_CLASSIC_MAX);
	assert(eigrp_bandwidth_to_scaled(100000) == 25600);
	assert(eigrp_bandwidth_to_scaled(10000000) == 256);

	/* Classic infinity is a marker, not a finite delay to scale. */
	assert(eigrp_delay_to_scaled(EIGRP_MAX_METRIC) == EIGRP_MAX_METRIC);
	assert(eigrp_delay_to_scaled(EIGRP_METRIC_MAX) == EIGRP_MAX_METRIC);
	assert(eigrp_scaled_to_delay(EIGRP_MAX_METRIC) == EIGRP_MAX_METRIC);
	assert(eigrp_delay_to_scaled(2000) == 512000);
	assert(eigrp_scaled_to_delay(512000) == 2000);

	eigrp_metric_link_set(&metric, 56, 2000, false);
	assert(metric.bandwidth == 45714176);
	assert(metric.delay == 512000);
	assert(eigrp_metric_calculate(&eigrp, metric) == 46226176);

	/* Configured redistribution seeds and local links use the same units.
	 */
	seed.bandwidth = 56;
	seed.delay = 2000;
	seed.reliability = 255;
	seed.load = 1;
	seed.mtu = 1500;
	eigrp_metric_values_convert(&seed, &metric);
	assert(metric.bandwidth == 45714176);
	assert(metric.delay == 512000);
	assert(eigrp_metric_calculate(&eigrp, metric) == 46226176);

	/* Cisco four-router example: 56 Kbit/s, total delay 2200. */
	link.params.bandwidth = 10000;
	link.params.delay = 100;
	route.ei = &link;
	route.reported_metric = metric;
	assert(eigrp_metric_total_calculate(&eigrp, &route) == 46251776);
	assert(route.total_metric.delay == (2000 + 100) * 256);
	route.reported_metric = route.total_metric;
	assert(eigrp_metric_total_calculate(&eigrp, &route) == 46277376);
	assert(route.total_metric.bandwidth == 45714176);
	assert(route.total_metric.delay == (2000 + 100 + 100) * 256);

	/* Unreachable advertisements must not turn into finite distances. */
	metric.delay = EIGRP_MAX_METRIC;
	assert(eigrp_metric_calculate(&eigrp, metric) == EIGRP_MAX_METRIC);
	route.reported_metric = metric;
	assert(eigrp_metric_total_calculate(&eigrp, &route)
	       == EIGRP_MAX_METRIC);
	metric.delay = EIGRP_METRIC_MAX;
	assert(eigrp_metric_calculate(&eigrp, metric) == EIGRP_METRIC_MAX);

	/* K2, K4, K5 components are exercised, not just default K1/K3. */
	memset(&metric, 0, sizeof(metric));
	metric.bandwidth = 25600;
	metric.delay = 2560;
	metric.load = 254;
	metric.reliability = 255;
	eigrp.k_values[1] = 1;
	assert(eigrp_metric_calculate(&eigrp, metric) == 40960);
	/* K2 divides the already-scaled classic bandwidth. */
	eigrp.k_values[0] = 0;
	eigrp.k_values[2] = 0;
	metric.bandwidth = 45714176;
	metric.load = 128;
	assert(eigrp_metric_calculate(&eigrp, metric) == 357142);

	eigrp.k_values[0] = 1;
	eigrp.k_values[2] = 1;
	metric.bandwidth = 25600;
	metric.delay = 5120;
	metric.load = 1;
	assert(eigrp_metric_calculate(&eigrp, metric)
	       == 30720 + 25600 / (EIGRP_MAX_LOAD - 1));
	metric.load = 255;
	assert(eigrp_metric_calculate(&eigrp, metric) == 56320);
	metric.delay = 2560;
	metric.load = 254;
	eigrp.k_values[3] = 1;
	eigrp.k_values[4] = 2;
	assert(eigrp_metric_calculate(&eigrp, metric) == 320);
	return 0;
}

bool eigrp_intf_delay_is_default(eigrp_intf_t *ei)
{
	(void)ei;
	return false;
}

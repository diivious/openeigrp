// SPDX-License-Identifier: ISC
/* Cisco wide-metric integer reference vectors from IOS XE 17.03.06 captures.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <assert.h>
#include <stdint.h>
#include <string.h>

#include "eigrp.h"
#include "eigrp_structs.h"
#include "eigrp_metric.h"
#include "../uut/eigrp_uut_defs.h"

int main(void)
{
	eigrp_instance_t eigrp = {0};
	eigrp_intf_t link = {0};
	eigrp_route_descriptor_t route = {0};
	eigrp_metrics_t metric = {0};
	eigrp_metric_values_t seed = {0};

	eigrp.metric_version = EIGRP_TLV_64B_VERSION;
	eigrp.k_values[0] = 1;
	eigrp.k_values[2] = 1;
	eigrp.max_hops = EIGRP_MAX_HOPS;

	/* RFC 7868 section 5.6.1.2, wide defaults above 1 Gbit/s. */
	static const struct {
		eigrp_bandwidth_t bandwidth;
		eigrp_delay_t wide_ns;
	} delays[] = {
		{EIGRP_INTF_1G_BW, EIGRP_INTF_1G_WIDE_NS},
		{EIGRP_INTF_2G_BW, EIGRP_INTF_2G_WIDE_NS},
		{EIGRP_INTF_5G_BW, EIGRP_INTF_5G_WIDE_NS},
		{EIGRP_INTF_10G_BW, EIGRP_INTF_10G_WIDE_NS},
		{EIGRP_INTF_20G_BW, EIGRP_INTF_20G_WIDE_NS},
		{EIGRP_INTF_50G_BW, EIGRP_INTF_50G_WIDE_NS},
		{EIGRP_INTF_100G_BW, EIGRP_INTF_100G_WIDE_NS},
		{EIGRP_INTF_200G_BW, EIGRP_INTF_200G_WIDE_NS},
		{EIGRP_INTF_500G_BW, EIGRP_INTF_500G_WIDE_NS},
	};
	for (size_t i = 0; i < sizeof(delays) / sizeof(delays[0]); i++) {
		eigrp_delay_t wide = eigrp_metric_delay_classic2wide(
			EIGRP_GIGABIT_CLASSIC_DELAY, delays[i].bandwidth, true);
		assert(wide == delays[i].wide_ns * 1000);
		assert(eigrp_metric_delay_wide2classic(wide,
						       delays[i].bandwidth)
		       == EIGRP_GIGABIT_CLASSIC_DELAY);
		eigrp_metric_link_set(&metric, delays[i].bandwidth,
				      EIGRP_INTF_FASTETH_DELAY, true);
		assert(metric.wide_delay == wide);
		assert(metric.delay == EIGRP_CLASSIC_SCALE);
	}
	/* Configured delay 1 at 10 Gbit/s remains 10 us, not 1 us. */
	assert(eigrp_metric_delay_classic2wide(1, EIGRP_INTF_10G_BW, false)
	       == EIGRP_CLASSIC_DELAY_PICO);
	eigrp_metric_link_set(&metric, EIGRP_INTF_10G_BW, EIGRP_INTF_1G_DELAY,
			      false);
	assert(metric.wide_delay == EIGRP_CLASSIC_DELAY_PICO);
	assert(metric.delay == EIGRP_CLASSIC_SCALE);

	/* Non-default delays must not inherit the physical-link shortcut. */
	assert(eigrp_metric_delay_classic2wide(10, EIGRP_INTF_10G_BW, false)
	       == 100000000);
	assert(eigrp_metric_delay_wide2classic(100000000, EIGRP_INTF_10G_BW)
	       == 10);
	assert(eigrp_metric_delay_classic2wide(10, EIGRP_INTF_FASTETH_BW, false)
	       == 100000000);
	assert(eigrp_metric_delay_classic2wide(1, EIGRP_INTF_FASTETH_BW, false)
	       == 10000000);
	/* Largest representable finite 48-bit delay and the next value. */
	assert(eigrp_metric_delay_classic2wide(
		       (EIGRP_TLV2_INACCESSIBLE - 1) / EIGRP_CLASSIC_DELAY_PICO,
		       EIGRP_INTF_FASTETH_BW, false)
	       < EIGRP_TLV2_INACCESSIBLE);
	assert(eigrp_metric_delay_classic2wide(
		       (EIGRP_TLV2_INACCESSIBLE - 1) / EIGRP_CLASSIC_DELAY_PICO
			       + 1,
		       EIGRP_INTF_FASTETH_BW, false)
	       == EIGRP_TLV2_INACCESSIBLE);
	assert(eigrp_metric_delay_classic2wide(UINT64_MAX,
					       EIGRP_INTF_FASTETH_BW, false)
	       == EIGRP_TLV2_INACCESSIBLE);

	/* Cisco captures: deliberately non-multiple-of-256 RD. */
	metric.wide_bandwidth = 25600;
	metric.wide_delay = 2560;
	assert(eigrp_metric_calculate(&eigrp, metric) == 25600167);
	metric.wide_bandwidth = 8000000; /* Captured Cisco TLV2 bandwidth. */
	metric.wide_delay = 1250000;
	assert(eigrp_metric_calculate(&eigrp, metric) == 163840);
	/* The receiving 100-Mbit link becomes the path bottleneck. */
	link.params.bandwidth = EIGRP_INTF_FASTETH_BW;
	link.params.delay = EIGRP_INTF_FASTETH_DELAY;
	route.ei = &link;
	route.reported_metric = metric;
	assert(eigrp_metric_total_calculate(&eigrp, &route) == 13189120);
	assert(route.total_metric.wide_bandwidth == 100000);
	assert(route.total_metric.wide_delay == 101250000);

	/* Local connected and redistributed seeds preserve native TLV2 units.
	 */
	memset(&metric, 0, sizeof(metric));
	eigrp_metric_link_set(&metric, EIGRP_INTF_FASTETH_BW,
			      EIGRP_INTF_FASTETH_DELAY, false);
	assert(metric.wide_bandwidth == 100000);
	assert(metric.wide_delay == 100000000);
	assert(eigrp_metric_calculate(&eigrp, metric) == 13107200);
	seed.bandwidth = EIGRP_INTF_FASTETH_BW;
	seed.delay = EIGRP_INTF_FASTETH_DELAY;
	seed.load = EIGRP_LOAD_DEFAULT;
	seed.reliability = EIGRP_INTF_REL_100;
	seed.mtu = EIGRP_INTF_MTU_1500;
	eigrp_metric_values_convert(&seed, &metric);
	assert(metric.wide_bandwidth == 100000);
	assert(metric.wide_delay == 100000000);
	assert(eigrp_metric_calculate(&eigrp, metric) == 13107200);
	seed.bandwidth = EIGRP_INTF_10G_BW;
	seed.delay = EIGRP_INTF_1G_DELAY;
	eigrp_metric_values_convert(&seed, &metric);
	assert(metric.wide_delay == EIGRP_CLASSIC_DELAY_PICO);
	assert(eigrp_metric_calculate(&eigrp, metric) == 720896);
	seed.bandwidth = EIGRP_INTF_FASTETH_BW;
	seed.delay = EIGRP_INTF_ETH_DELAY;
	eigrp_metric_values_convert(&seed, &metric);
	assert(eigrp_metric_calculate(&eigrp, metric) == 72089600);

	/* Unit test fixture percentages map to the actual 1..255 fields. */
	seed.load = EIGRP_INTF_LOAD_50;
	seed.reliability = EIGRP_INTF_REL_20;
	eigrp_metric_values_convert(&seed, &metric);
	assert(metric.load == EIGRP_INTF_LOAD_50);
	assert(metric.reliability == EIGRP_INTF_REL_20);

	/* A link adds delay in picoseconds and preserves minimum bandwidth. */
	memset(&metric, 0, sizeof(metric));
	eigrp_metric_link_set(&metric, EIGRP_INTF_FASTETH_BW,
			      EIGRP_INTF_FASTETH_DELAY, false);
	link.params.bandwidth = EIGRP_INTF_FASTETH_BW;
	link.params.delay = EIGRP_INTF_FASTETH_DELAY;
	route.ei = &link;
	route.reported_metric = metric;
	assert(eigrp_metric_total_calculate(&eigrp, &route) == 19660800);
	assert(route.total_metric.wide_delay == 200000000);

	/* 64-bit distances must not wrap into smaller 32-bit values. */
	metric.wide_bandwidth = EIGRP_INTF_FASTETH_BW;
	metric.wide_delay = 100000000000;
	assert(eigrp_metric_calculate(&eigrp, metric) == 6560153600ULL);
	metric.wide_delay = 200000000000;
	assert(eigrp_metric_calculate(&eigrp, metric) == 13113753600ULL);
	return 0;
}

/* No runtime configuration object in the standalone C test. */
bool eigrp_intf_delay_is_default(eigrp_intf_t *ei)
{
	(void)ei;
	return false;
}

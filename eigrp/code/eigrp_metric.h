// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * EIGRP Metric Math Functions.
 * Copyright (C) 2013-2016, 2026
 * Authors:
 *   Donnie Savage
 */
#ifndef _ZEBRA_EIGRP_METRIC_H_
#define _ZEBRA_EIGRP_METRIC_H_

#include "eigrp_instance.h"
#include "eigrp.h"

/* Constants */
/* Release 2 / Wide Metrics is the default local EIGRP capability. */
#define EIGRP_MAJOR_VERSION 2
#define EIGRP_MINOR_VERSION 2

#define EIGRP_TLV_32B_VERSION 1 /* Original 32-bit scaled metrics */
#define EIGRP_TLV_64B_VERSION 2 /* Current 64-bit wide metrics */
#define EIGRP_TLV_MTR_VERSION 3 /* MTR 32-bit metric TLVs; unsupported */
#define EIGRP_TLV_SAF_VERSION 4 /* SAF 64-bit metric TLVs; unsupported */

/* RFC 7868, section 5.6.2.2: conversion and metric constants. */
#define EIGRP_BANDWIDTH 10000000ULL
#define EIGRP_DELAY_PICO 1000000ULL
#define EIGRP_INACCESSIBLE UINT64_MAX
#define EIGRP_MAX_HOPS 100
#define EIGRP_CLASSIC_SCALE 256
#define EIGRP_WIDE_SCALE 65536

#define EIGRP_BANDWIDTH_MIN 1ULL
#define EIGRP_BANDWIDTH_MAX EIGRP_INACCESSIBLE
#define EIGRP_DELAY_MIN 1ULL
#define EIGRP_DELAY_MAX EIGRP_INACCESSIBLE
#define EIGRP_METRIC_MAX EIGRP_INACCESSIBLE
#define EIGRP_CLASSIC_MAX UINT32_MAX

/* RFC 7868, section 5.6.1.2: classic delay in 10-microsecond units. */
#define EIGRP_CLASSIC_DELAY_US 10ULL
#define EIGRP_CLASSIC_DELAY_PICO (EIGRP_CLASSIC_DELAY_US * EIGRP_DELAY_PICO)
#define EIGRP_GIGABIT_KBPS 1000000ULL
#define EIGRP_GIGABIT_CLASSIC_DELAY 1ULL

/* RFC 7868, section 6.9.2: 48-bit TLV2 metric field inaccessibility. */
#define EIGRP_TLV2_METRIC_BITS 48
#define EIGRP_TLV2_INACCESSIBLE ((UINT64_C(1) << EIGRP_TLV2_METRIC_BITS) - 1)

/* RFC 7868, sections 5.6.1.1 and 5.6.2.3: K2 load divisor. */
#define EIGRP_MAX_LOAD 256

/* 64-bit DUAL infinity and the narrower host RIB sentinel. */
#define EIGRP_64BIT_METRIC_INFINITY EIGRP_INACCESSIBLE
#define EIGRP_RIB_METRIC_INFINITY UINT32_MAX

/* Prototypes */
/* Set the local-link vector in both classic and Cisco wide units. */
void eigrp_metric_link_set(eigrp_metrics_t *, eigrp_bandwidth_t, eigrp_delay_t,
			   bool);
eigrp_delay_t eigrp_metric_delay_classic2wide(eigrp_delay_t, eigrp_bandwidth_t,
					      bool);
eigrp_delay_t eigrp_metric_delay_wide2classic(eigrp_delay_t, eigrp_bandwidth_t);
extern eigrp_scaled_t eigrp_bandwidth_to_scaled(eigrp_bandwidth_t);
extern eigrp_bandwidth_t eigrp_scaled_to_bandwidth(eigrp_scaled_t);
extern eigrp_scaled_t eigrp_delay_to_scaled(eigrp_delay_t);
extern eigrp_delay_t eigrp_scaled_to_delay(eigrp_scaled_t);

extern eigrp_metric_t eigrp_metric_calculate(eigrp_instance_t *,
					     eigrp_metrics_t);
extern eigrp_metric_t eigrp_metric_total_calculate(eigrp_instance_t *,
						   eigrp_route_descriptor_t *);
extern bool eigrp_metrics_match(eigrp_metrics_t, eigrp_metrics_t);
void eigrp_metric_values_convert(const eigrp_metric_values_t *values,
				 eigrp_metrics_t *metric);

eigrp_result_t eigrp_metric_default_update(eigrp_operation_t operation,
					   eigrp_instance_context_t *context,
					   const eigrp_metric_values_t *metric);
bool eigrp_metric_default(const eigrp_af_config_t *af,
			  eigrp_metric_values_t *metric);
eigrp_result_t
eigrp_metric_weights_update(eigrp_operation_t operation,
			    eigrp_instance_context_t *context,
			    const eigrp_metric_weights_t *weights);
eigrp_result_t eigrp_metric_variance_update(eigrp_operation_t operation,
					    eigrp_instance_context_t *context,
					    uint8_t variance);
eigrp_result_t
eigrp_traffic_share_balanced_update(eigrp_operation_t operation,
				    eigrp_instance_context_t *context);
eigrp_result_t
eigrp_metric_maximum_hops_update(eigrp_operation_t operation,
				 eigrp_instance_context_t *context,
				 uint8_t maximum_hops);
eigrp_result_t eigrp_metric_version_update(eigrp_operation_t operation,
					   eigrp_instance_context_t *context);
uint8_t eigrp_metric_version_select(const eigrp_instance_t *eigrp,
				    uint8_t peer_version);
void eigrp_metric_config_delete_all(eigrp_af_config_t *af);

#endif /* _ZEBRA_EIGRP_METRIC_H_ */

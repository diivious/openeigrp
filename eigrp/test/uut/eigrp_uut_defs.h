// SPDX-License-Identifier: ISC
/* Test interface vectors. RFC 7868, sections 5.6.1.2 and 5.6.1.1.
 * Never include this header from the portable EIGRP implementation. */
#ifndef _EIGRP_UUT_DEFS_H_
#define _EIGRP_UUT_DEFS_H_

#include "../../code/eigrp_const.h"
#include "../../code/eigrp_metric.h"

/* Bandwidth is kbps; classic delay is expressed in 10-microsecond units. */
#define EIGRP_INTF_TUNNEL_BW 9U
#define EIGRP_INTF_56K_BW 56U
#define EIGRP_INTF_T1_BW 1544U
#define EIGRP_INTF_ETH_BW 10000U
#define EIGRP_INTF_ETH_DELAY 100U
#define EIGRP_INTF_FASTETH_BW EIGRP_BANDWIDTH_DEFAULT
#define EIGRP_INTF_FASTETH_DELAY EIGRP_DELAY_DEFAULT
#define EIGRP_INTF_1G_BW EIGRP_GIGABIT_KBPS
#define EIGRP_INTF_1G_DELAY EIGRP_GIGABIT_CLASSIC_DELAY
#define EIGRP_INTF_2G_BW 2000000U
#define EIGRP_INTF_5G_BW 5000000U
#define EIGRP_INTF_10G_BW 10000000U
#define EIGRP_INTF_20G_BW 20000000U
#define EIGRP_INTF_50G_BW 50000000U
#define EIGRP_INTF_100G_BW 100000000U
#define EIGRP_INTF_200G_BW 200000000U
#define EIGRP_INTF_500G_BW 500000000U

/* Table 5.6.1.2: wide delay in nanoseconds, not classic delay units. */
#define EIGRP_INTF_1G_WIDE_NS 10000U
#define EIGRP_INTF_2G_WIDE_NS 5000U
#define EIGRP_INTF_5G_WIDE_NS 2000U
#define EIGRP_INTF_10G_WIDE_NS 1000U
#define EIGRP_INTF_20G_WIDE_NS 500U
#define EIGRP_INTF_50G_WIDE_NS 200U
#define EIGRP_INTF_100G_WIDE_NS 100U
#define EIGRP_INTF_200G_WIDE_NS 50U
#define EIGRP_INTF_500G_WIDE_NS 20U

/* Test fixture values, not wire-format constants. */
#define EIGRP_INTF_MTU_1500 1500U
#define EIGRP_INTF_MTU_1400 1400U
#define EIGRP_INTF_LOAD_50 127U /* RFC 7868: 127/255 saturation */
#define EIGRP_INTF_LOAD_100 255U
#define EIGRP_INTF_REL_20 51U
#define EIGRP_INTF_REL_90 229U
#define EIGRP_INTF_REL_100 EIGRP_RELIABILITY_DEFAULT

#endif /* _EIGRP_UUT_DEFS_H_ */

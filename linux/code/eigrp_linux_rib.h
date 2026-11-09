// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Linux kernel RIB reconciliation service.
 * Copyright (C) 2026 Donnie V. Savage
 */
#ifndef EIGRP_LINUX_RIB_H_
#define EIGRP_LINUX_RIB_H_

#include "eigrp.h"

/* Host calls this after a Netlink fault, periodically, and after an interface
 * recovery. It retries pending work and verifies previously installed routes.
 * No portable topology or redistribution state is accessed by this service. */
eigrp_result_t eigrp_linux_rib_reconcile(void);

#endif /* EIGRP_LINUX_RIB_H_ */

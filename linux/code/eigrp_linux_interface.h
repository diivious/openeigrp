// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Native Linux interface inventory and Netlink notification service.
 * Copyright (C) 2026 Donnie V. Savage
 */
#ifndef EIGRP_LINUX_INTERFACE_H_
#define EIGRP_LINUX_INTERFACE_H_

#include "eigrp.h"

/* Start with an RTM_GETLINK/RTM_GETADDR snapshot on a subscribed socket.
 * The caller owns the single-threaded polling lifecycle.  No background
 * callbacks or references to portable runtime objects are retained. */
eigrp_result_t eigrp_linux_intf_start(void);

/* Process all queued notifications.  On loss/error, rebuild a fresh snapshot
 * and retire the old interface identities before announcing replacements. */
eigrp_result_t eigrp_linux_intf_process(void);

/* Current socket for integration with a host event loop; may change after a
 * resynchronization.  The caller must discard registrations for the old fd. */
int eigrp_linux_intf_fd(void);

/* Free all host-owned inventory without calling into a terminating core. */
void eigrp_linux_intf_stop(void);

#endif /* EIGRP_LINUX_INTERFACE_H_ */

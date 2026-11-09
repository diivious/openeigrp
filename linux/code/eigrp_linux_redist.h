// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Native Linux redistribution route observation service.
 * Copyright (C) 2026 Donnie V. Savage
 */
#ifndef EIGRP_LINUX_REDIST_H_
#define EIGRP_LINUX_REDIST_H_

#include "eigrp.h"

/* The host pumps this descriptor in its event loop. It changes when Netlink
 * must be reopened; replace an old registration after process/resync. */
int eigrp_linux_redist_fd(void);
eigrp_result_t eigrp_linux_redist_process(void);

/* Refresh the kernel snapshot after Netlink loss and interface metric changes.
 * Unlike process(), an explicit resync replays routes whose host interface
 * properties may have changed without a kernel route notification. */
eigrp_result_t eigrp_linux_redist_resync(void);

/* Instance lifetime is controlled by the host: stop pumping callbacks before
 * tearing down its AF instances. No AF or topology pointers are retained after
 * instance_delete/stop. */
void eigrp_linux_redist_instance_delete(eigrp_instance_t *instance);
void eigrp_linux_redist_stop(void);

#endif /* EIGRP_LINUX_REDIST_H_ */

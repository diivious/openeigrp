// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Native Linux IPv4/IPv6 EIGRP packet transport (IP protocol 88).
 * Copyright (C) 2026 Donnie V. Savage
 */
#ifndef EIGRP_LINUX_PACKET_H_
#define EIGRP_LINUX_PACKET_H_

#include "eigrp.h"

/* Borrowed nonblocking descriptor for host event-loop registration.  A
 * successful socket close invalidates it; unregister before closing. */
int eigrp_linux_packet_fd(const eigrp_instance_t *eigrp);

#endif /* EIGRP_LINUX_PACKET_H_ */

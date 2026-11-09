// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Native Linux local EIGRP management transport.
 * Copyright (C) 2026 Donnie V. Savage
 */
#ifndef EIGRP_LINUX_MGNT_H_
#define EIGRP_LINUX_MGNT_H_

#include <sys/types.h>

#include "eigrp.h"

#define EIGRP_LINUX_MGNT_MAGIC 0x45474d54U /* EGMT */
#define EIGRP_LINUX_MGNT_VERSION 1U
#define EIGRP_LINUX_MGNT_HEADER_SIZE 16U
#define EIGRP_LINUX_MGNT_SELECTOR_SIZE 16U
#define EIGRP_LINUX_MGNT_RESPONSE_MAX 16384U
#define EIGRP_LINUX_MGNT_CLIENT_MAX 32U
#define EIGRP_LINUX_MGNT_PAGE_SIZE 32U

/* Each request and response starts with a network-byte-order header:
 * magic:u32, version:u16, operation:u16, status:u16, flags:u16, length:u32.
 * Requests set status/flags to zero. Response flags bit 0 indicates that
 * another page can be requested (offset += number of JSON-lines returned).
 * The length excludes the header. Responses contain newline-delimited JSON.
 */
typedef enum eigrp_linux_mgnt_operation {
	EIGRP_LINUX_MGNT_CAPABILITIES = 1,
	EIGRP_LINUX_MGNT_PROTOCOLS,
	EIGRP_LINUX_MGNT_NEIGHBORS,
	EIGRP_LINUX_MGNT_INTERFACES,
	EIGRP_LINUX_MGNT_TOPOLOGY,
	EIGRP_LINUX_MGNT_ROUTES,
	EIGRP_LINUX_MGNT_TRAFFIC,
	EIGRP_LINUX_MGNT_EVENTS,
	EIGRP_LINUX_MGNT_DIAGNOSTICS,
	EIGRP_LINUX_MGNT_CLEAR_EVENTS,
} eigrp_linux_mgnt_operation_t;

typedef enum eigrp_linux_mgnt_status {
	EIGRP_LINUX_MGNT_OK = 0,
	EIGRP_LINUX_MGNT_INVALID,
	EIGRP_LINUX_MGNT_VERSION_UNSUPPORTED,
	EIGRP_LINUX_MGNT_DENIED,
	EIGRP_LINUX_MGNT_NOT_FOUND,
	EIGRP_LINUX_MGNT_UNSUPPORTED,
	EIGRP_LINUX_MGNT_BUSY,
	EIGRP_LINUX_MGNT_INTERNAL,
} eigrp_linux_mgnt_status_t;

/* A scoped request is exactly 16 bytes, network byte order:
 * afi:u16 (4 or 6), asn:u16 (nonzero), vrf_id:u32, topology_id:u16,
 * flags:u16 (zero), offset:u32. The offset is the JSON-line record index.
 * Only the base topology ID (0) is supported.
 * Capabilities and protocols take no request body.
 * Clear-events requires a scoped request, never a wildcard.
 */

/* The executor must execute fn synchronously in a protocol-safe context.
 * It must not call fn after returning or while the core is being destroyed.
 * The service thread never accesses private EIGRP state or locks core data.
 * The daemon must stop the service before tearing down protocol instances.
 * Passing NULL is forbidden: unsafe direct core walks are not a fallback.
 */
typedef eigrp_result_t (*eigrp_linux_mgnt_executor_t)(
	eigrp_result_t (*fn)(void *), void *fn_arg, void *host_arg);

/* Absolute socket path. Creation fails if a pathname already exists; only
 * the socket inode created by this service is unlinked on shutdown.
 * socket_mode is restricted to 0600 or 0660; group access is read-only at
 * the protocol level. Privileged operations require root or daemon euid.
 * socket_gid == (gid_t)-1 leaves the effective group unchanged.
 */
int eigrp_linux_mgnt_start(const char *path, gid_t socket_gid,
			   mode_t socket_mode,
			   eigrp_linux_mgnt_executor_t execute, void *host_arg);
void eigrp_linux_mgnt_stop(void);

#endif /* EIGRP_LINUX_MGNT_H_ */

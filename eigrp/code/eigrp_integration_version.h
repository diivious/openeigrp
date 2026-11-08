// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Host-neutral integration version-reporting contract.
 *
 * Copyright (C) 2026 Donnie V. Savage
 */
#ifndef EIGRPD_EIGRP_INTEGRATION_VERSION_H_
#define EIGRPD_EIGRP_INTEGRATION_VERSION_H_

/*
 * The host adapter owns discovery and formatting.  The portable EIGRP core
 * neither populates this snapshot nor interprets operating-system versions.
 * A zero-length field means that the corresponding value is unavailable.
 *
 * integration_version describes the OpenEIGRP adapter build; it is not the
 * framework_version (e.g. FRRouting) or the operating-system version.
 * The core's EIGRP_RELEASE is reported independently by the capability API.
 */
typedef struct eigrp_integration_version {
	const char *integration;
	const char *integration_version;
	const char *framework;
	const char *framework_version;
	char operating_system[64];
	char operating_system_version[64];
	char kernel_version[64];
	char distribution[128];
	char distribution_version[64];
	char distribution_codename[64];
	char architecture[64];
} eigrp_integration_version_t;

#endif /* EIGRPD_EIGRP_INTEGRATION_VERSION_H_ */

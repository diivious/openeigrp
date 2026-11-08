// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * FRRouting integration version discovery.
 *
 * Copyright (C) 2026 Donnie V. Savage
 */

#include <stdio.h>
#include <string.h>
#include <sys/utsname.h>

#ifdef __APPLE__
#include <sys/sysctl.h>
#endif

#include <lib/version.h>

#include "eigrp_features.h"
#include "eigrp_frr_version.h"

#ifndef EIGRP_FRR_INTEGRATION_RELEASE
#define EIGRP_FRR_INTEGRATION_RELEASE EIGRP_RELEASE
#endif

#ifndef EIGRP_OS_RELEASE_PATH
#define EIGRP_OS_RELEASE_PATH "/etc/os-release"
#endif

/* Bounded copy avoids requiring a particular host's strlcpy implementation. */
static void eigrp_frr_version_copy(char *dst, size_t length, const char *src)
{
	size_t count;

	if (!length)
		return;
	if (!src)
		src = "";
	count = strlen(src);

	if (count >= length)
		count = length - 1;
	memcpy(dst, src, count);
	dst[count] = '\0';
}

#if defined(__linux__) && !defined(__APPLE__)
static void eigrp_frr_version_os_release(eigrp_integration_version_t *version)
{
	FILE *file;
	char line[512];

	file = fopen(EIGRP_OS_RELEASE_PATH, "r");
	if (!file)
		return;

	while (fgets(line, sizeof(line), file)) {
		const struct {
			const char *key;
			char *value;
			size_t size;
		} fields[] = {
			{"NAME", version->distribution,
			 sizeof(version->distribution)},
			{"VERSION_ID", version->distribution_version,
			 sizeof(version->distribution_version)},
			{"VERSION_CODENAME", version->distribution_codename,
			 sizeof(version->distribution_codename)},
		};
		char *value;
		size_t length;
		size_t i;

		/* Ignore truncated release-file lines. */
		length = strlen(line);
		if (length && line[length - 1] != '\n' && !feof(file)) {
			int c;

			while ((c = fgetc(file)) != '\n' && c != EOF)
				;
			continue;
		}
		for (i = 0; i < sizeof(fields) / sizeof(fields[0]); i++) {
			length = strlen(fields[i].key);
			if (strncmp(line, fields[i].key, length) != 0
			    || line[length] != '=')
				continue;
			value = line + length + 1;
			length = strcspn(value, "\r\n");
			value[length] = '\0';
			if (length >= 2 && (value[0] == '"' || value[0] == '\'')
			    && value[length - 1] == value[0]) {
				value[length - 1] = '\0';
				value++;
			}
			eigrp_frr_version_copy(fields[i].value, fields[i].size,
					       value);
			break;
		}
	}
	fclose(file);
}
#endif

void eigrp_frr_version_read(eigrp_integration_version_t *version)
{
	struct utsname host;

	if (!version)
		return;
	memset(version, 0, sizeof(*version));
	version->integration = "FRRouting EIGRP";
	version->integration_version = EIGRP_FRR_INTEGRATION_RELEASE;
	version->framework = "FRRouting";
	version->framework_version = FRR_VERSION;

	if (uname(&host) == 0) {
		eigrp_frr_version_copy(version->operating_system,
				       sizeof(version->operating_system),
				       host.sysname);
		eigrp_frr_version_copy(version->kernel_version,
				       sizeof(version->kernel_version),
				       host.release);
		eigrp_frr_version_copy(version->architecture,
				       sizeof(version->architecture),
				       host.machine);
	}

#ifdef __APPLE__
	{
		char *os_version = version->operating_system_version;
		size_t size = sizeof(version->operating_system_version);
		size_t length = size;
		int result;

		result = sysctlbyname("kern.osproductversion", os_version,
				      &length, NULL, 0);
		if (result == 0)
			os_version[size - 1] = '\0';
		else
			os_version[0] = '\0';
	}
#elif defined(__linux__)
	eigrp_frr_version_os_release(version);
#endif
}

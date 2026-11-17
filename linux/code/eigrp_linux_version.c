// SPDX-License-Identifier: GPL-2.0-or-later
/* Native Linux runtime environment discovery.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include "eigrp_linux_version.h"

#include <stdio.h>
#include <string.h>
#include <sys/utsname.h>

#include "eigrp_features.h"

#ifndef EIGRP_OS_RELEASE_PATH
#define EIGRP_OS_RELEASE_PATH "/etc/os-release"
#endif

static void version_copy(char *dst, size_t size, const char *src)
{
	size_t count;

	if (!size)
		return;
	count = strnlen(src, size - 1);
	memcpy(dst, src, count);
	dst[count] = '\0';
}

static void version_os_release(eigrp_integration_version_t *version)
{
	FILE *file = fopen(EIGRP_OS_RELEASE_PATH, "r");
	char line[512];

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
		size_t length, i;

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
			version_copy(fields[i].value, fields[i].size, value);
			break;
		}
	}
	fclose(file);
}

void eigrp_linux_version_read(eigrp_integration_version_t *version)
{
	struct utsname host;

	if (!version)
		return;
	memset(version, 0, sizeof(*version));
	version->integration = "Native Linux EIGRP";
	version->integration_version = EIGRP_RELEASE;
	if (uname(&host) == 0) {
		version_copy(version->operating_system,
			     sizeof(version->operating_system), host.sysname);
		version_copy(version->kernel_version,
			     sizeof(version->kernel_version), host.release);
		version_copy(version->architecture,
			     sizeof(version->architecture), host.machine);
	}
	version_os_release(version);
}

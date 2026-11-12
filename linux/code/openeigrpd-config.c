// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Offline native OpenEIGRP configuration validation.
 * Copyright (C) 2026 Donnie V. Savage
 */
#define _POSIX_C_SOURCE 200809L

#include <errno.h>
#include <stdbool.h>
#include <stdio.h>
#include <string.h>

#include "eigrp_unix_config.h"

#define OPENEIGRPD_CONFIG_DEFAULT "/etc/openeigrpd.conf"

static void usage(FILE *stream)
{
	fprintf(stream, "usage: openeigrpd-config -t [-f PATH]\n");
}

int main(int argc, char **argv)
{
	const char *path = OPENEIGRPD_CONFIG_DEFAULT;
	eigrp_unix_config_t *config;
	eigrp_result_t result;
	FILE *file;
	unsigned line = 0;
	int i;
	bool test = false;

	for (i = 1; i < argc; ++i) {
		if (strcmp(argv[i], "-t") == 0 && !test) {
			test = true;
		} else if (strcmp(argv[i], "-f") == 0 && i + 1 < argc) {
			path = argv[++i];
		} else {
			usage(stderr);
			return 2;
		}
	}
	if (!test) {
		usage(stderr);
		return 2;
	}
	file = fopen(path, "r");
	if (!file) {
		fprintf(stderr, "%s: %s\n", path, strerror(errno));
		return 1;
	}
	config = eigrp_unix_config_create();
	if (!config) {
		fprintf(stderr, "configuration: out of memory\n");
		fclose(file);
		return 1;
	}
	result = eigrp_unix_config_parse_file(config, file, &line);
	fclose(file);
	eigrp_unix_config_delete(config);
	if (result != EIGRP_RESULT_SUCCESS) {
		fprintf(stderr,
			"%s:%u: invalid or unsupported configuration "
			"(result %d)\n",
			path, line, result);
		return 1;
	}
	printf("%s: syntax valid\n", path);
	return 0;
}

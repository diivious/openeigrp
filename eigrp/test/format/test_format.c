// SPDX-License-Identifier: GPL-2.0-or-later
/* Executable formatting and bounded string copying contract tests. */
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>

#include "eigrp_string.h"
#include "eigrp_sys.h"

static int format_va(char *buf, size_t size, const char *fmt, ...)
{
	va_list ap;
	int result;

	va_start(ap, fmt);
	result = eigrp_vsnprintf(buf, size, fmt, ap);
	va_end(ap);
	return result;
}

int main(void)
{
	char buf[8];
	char saved[8];
	int length;

	length = eigrp_snprintf(buf, sizeof(buf), "%s:%u", "peer", 12U);
	assert(length == 7);
	assert(strcmp(buf, "peer:12") == 0);

	memset(buf, 'X', sizeof(buf));
	length = format_va(buf, 5, "%s", "abcdefgh");
	assert(length == 8);
	assert(memcmp(buf, "abcd\0", 5) == 0);
	assert(buf[5] == 'X');

	length = eigrp_snprintf(buf, 1, "empty?");
	assert(length == 6 && buf[0] == '\0');
	length = eigrp_snprintf(buf, 0, "zero");
	assert(length == 4);
	length = eigrp_snprintf(buf, sizeof(buf), "%s", "");
	assert(length == 0 && buf[0] == '\0');

	memset(buf, '#', sizeof(buf));
	assert(eigrp_strcpy(buf, sizeof(buf), "1234567") == buf);
	assert(memcmp(buf, "1234567\0", sizeof(buf)) == 0);

	memcpy(saved, buf, sizeof(saved));
	assert(eigrp_strcpy(buf, sizeof(buf), "12345678") == NULL);
	assert(memcmp(buf, saved, sizeof(buf)) == 0);
	assert(eigrp_strcpy(buf, 0, "x") == NULL);
	assert(memcmp(buf, saved, sizeof(buf)) == 0);
	assert(eigrp_strcpy(NULL, sizeof(buf), "x") == NULL);
	assert(eigrp_strcpy(buf, sizeof(buf), NULL) == NULL);
	assert(eigrp_strcpy(buf, sizeof(buf), "") == buf);
	assert(buf[0] == '\0');

	puts("format and copy contract: PASS");
	return 0;
}

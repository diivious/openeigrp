// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Portable bounded string copying.
 * Copyright (C) 2026 Donnie V. Savage
 */
#ifndef EIGRP_STRING_H_
#define EIGRP_STRING_H_

#include <stddef.h>
#include <string.h>

/* Like strcpy(), return dst on success, but require its capacity.  On an
 * invalid argument or insufficient capacity return NULL without changing dst.
 * As with strcpy(), src must be a valid NUL-terminated, non-overlapping string.
 */
static inline char *eigrp_strcpy(char *dst, size_t size, const char *src)
{
	size_t len;

	if (!dst || !src || !size)
		return NULL;

	for (len = 0; len < size; len++) {
		if (!src[len])
			break;
	}
	if (len == size)
		return NULL;

	return memcpy(dst, src, len + 1);
}

#endif /* EIGRP_STRING_H_ */

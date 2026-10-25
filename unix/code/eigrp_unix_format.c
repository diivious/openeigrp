// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Unix host implementation of portable EIGRP formatting.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <stdarg.h>
#include <stdio.h>

#include "eigrp_sys.h"

int eigrp_vsnprintf(char *dst, size_t size, const char *fmt, va_list args)
{
	return vsnprintf(dst, size, fmt, args);
}

int eigrp_snprintf(char *dst, size_t size, const char *fmt, ...)
{
	va_list args;
	int result;

	va_start(args, fmt);
	result = eigrp_vsnprintf(dst, size, fmt, args);
	va_end(args);

	return result;
}

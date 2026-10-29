// SPDX-License-Identifier: ISC
// Copyright (C) 2026 Donnie V. Savage
/*
 * Diagnostic-only Linux interposer. The normal SIGABRT disposition discards
 * the call stack on GitHub hosted runners. Emit the native stack before
 * re-raising, preserving the failed process exit status and core semantics.
 *
 * This constructor is loaded in C UUT children ONLY. Do not preload it into
 * the Python runner, whose signals and subprocess management must be intact.
 */
#define _GNU_SOURCE
#include <execinfo.h>
#include <signal.h>
#include <unistd.h>

static void uut_abort_trace(int sig)
{
	void *frames[80];
	int count;
	static const char heading[] =
		"\n=== UUT SIGABRT native backtrace ===\n";

	(void)write(STDERR_FILENO, heading, sizeof(heading) - 1);
	count = backtrace(frames, sizeof(frames) / sizeof(frames[0]));
	backtrace_symbols_fd(frames, count, STDERR_FILENO);
	/* SA_RESETHAND makes the re-raised signal terminate the child normally. */
	(void)raise(sig);
	_exit(128 + sig);
}

__attribute__((constructor)) static void uut_trace_install(void)
{
	struct sigaction action = {0};

	action.sa_handler = uut_abort_trace;
	action.sa_flags = SA_RESETHAND | SA_NODEFER;
	sigemptyset(&action.sa_mask);
	(void)sigaction(SIGABRT, &action, NULL);
}

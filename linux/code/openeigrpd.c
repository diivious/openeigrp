// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Native Linux OpenEIGRP daemon and host lifecycle.
 *
 * Portable protocol ownership stays in eigrp/. The daemon drives the Linux
 * interface/redistribution Netlink descriptors and a bounded RIB maintenance
 * cycle; protocol timers, socket readiness, DUAL and packet processing remain
 * with the existing Unix scheduler and EIGRP AF workers.
 *
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <signal.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/signalfd.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

#include "eigrp.h"
#include "eigrp_features.h"
#include "eigrp_linux_config.h"
#include "eigrp_linux_interface.h"
#include "eigrp_linux_mgnt.h"
#include "eigrp_linux_redist.h"
#include "eigrp_linux_rib.h"
#include "eigrp_rib.h"
#include "eigrp_sys.h"
#include "eigrp_unix_config.h"
#include "eigrp_unix_keychain.h"

#define OPENEIGRPD_CONFIG "/etc/openeigrpd.conf"
#define OPENEIGRPD_SOCKET "/run/openeigrpd/control.sock"
#define OPENEIGRPD_RIB_INTERVAL 30000U
#define OPENEIGRPD_NETLINK_RETRY 5000U
#define OPENEIGRPD_POLL_INTERVAL 1000

struct daemon_context {
	pthread_mutex_t lock;
	bool live;
};

static void daemon_usage(FILE *out)
{
	fprintf(out,
		"usage: openeigrpd [-f CONFIG] [-s SOCKET] [-t] [-V] [-h]\n"
		"  -f PATH    configuration file (default: %s)\n"
		"  -s PATH    Unix management socket (default: %s)\n"
		"  -t         validate configuration, then exit\n"
		"  -V         print version, then exit\n"
		"  -h         show help\n",
		OPENEIGRPD_CONFIG, OPENEIGRPD_SOCKET);
}

static eigrp_result_t daemon_execute(eigrp_result_t (*fn)(void *), void *fn_arg,
				     void *host_arg)
{
	struct daemon_context *context = host_arg;
	eigrp_result_t result;

	/* The daemon owns configuration and AF lifetime. Scoped management
	 * walks are separately scheduled on the AF owner by the IPC provider.
	 */
	pthread_mutex_lock(&context->lock);
	result = context->live ? fn(fn_arg) : EIGRP_RESULT_CONFLICT;
	pthread_mutex_unlock(&context->lock);
	return result;
}

static eigrp_result_t daemon_config_parse(eigrp_unix_config_t *config,
					  const char *path, unsigned *line)
{
	FILE *input;
	eigrp_result_t result;

	input = fopen(path, "r");
	if (!input) {
		fprintf(stderr, "openeigrpd: cannot open %s: %s\n", path,
			strerror(errno));
		return EIGRP_RESULT_INTERNAL_FAILURE;
	}
	result = eigrp_unix_config_parse_file(config, input, line);
	if (fclose(input) != 0 && result == EIGRP_RESULT_SUCCESS)
		result = EIGRP_RESULT_INTERNAL_FAILURE;
	if (result != EIGRP_RESULT_SUCCESS)
		fprintf(stderr,
			"openeigrpd: invalid configuration %s at line %u (result %u)\n",
			path, *line, (unsigned)result);
	return result;
}

/* The immediate parent of the default socket is owned by the service.
 * For an alternate socket, demand that the operator created its directory.
 * The IPC service performs the remaining ownership/mode/symlink checks. */
static int daemon_socket_directory(const char *path, bool default_path)
{
	struct stat st;

	if (!default_path)
		return 0;
	if (mkdir("/run/openeigrpd", 0750) < 0 && errno != EEXIST)
		return -1;
	if (lstat("/run/openeigrpd", &st) < 0 || !S_ISDIR(st.st_mode)
	    || st.st_uid != geteuid() || (st.st_mode & 0022) != 0) {
		errno = EACCES;
		return -1;
	}
	(void)path;
	return 0;
}

static int daemon_signal_fd(void)
{
	sigset_t blocked;

	sigemptyset(&blocked);
	sigaddset(&blocked, SIGINT);
	sigaddset(&blocked, SIGTERM);
	sigaddset(&blocked, SIGHUP);
	/* Block before any workers are created, so signalfd is the sole owner
	 * of termination and reload requests. No async handler calls EIGRP. */
	int result = pthread_sigmask(SIG_BLOCK, &blocked, NULL);

	if (result != 0) {
		errno = result;
		return -1;
	}
	return signalfd(-1, &blocked, SFD_NONBLOCK | SFD_CLOEXEC);
}

static uint64_t daemon_now(void)
{
	struct timespec now;

	if (clock_gettime(CLOCK_MONOTONIC, &now) < 0)
		return 0;
	return (uint64_t)now.tv_sec * 1000U + (uint64_t)now.tv_nsec / 1000000U;
}

static int daemon_loop(int signal_fd)
{
	uint64_t rib_due = daemon_now() + OPENEIGRPD_RIB_INTERVAL;
	uint64_t intf_retry = 0;
	uint64_t redist_retry = 0;
	bool running = true;

	while (running) {
		struct pollfd fds[3] = {
			{.fd = signal_fd, .events = POLLIN},
			{.fd = eigrp_linux_intf_fd(), .events = POLLIN},
			{.fd = eigrp_linux_redist_fd(), .events = POLLIN},
		};
		uint64_t now;
		int count;

		count = poll(fds, 3, OPENEIGRPD_POLL_INTERVAL);
		if (count < 0 && errno != EINTR) {
			fprintf(stderr, "openeigrpd: host poll: %s\n",
				strerror(errno));
			return 1;
		}
		if (fds[0].revents & (POLLIN | POLLERR | POLLHUP)) {
			struct signalfd_siginfo signal;
			ssize_t bytes;

			while ((bytes = read(signal_fd, &signal,
					     sizeof(signal)))
			       == (ssize_t)sizeof(signal)) {
				if (signal.ssi_signo == SIGINT
				    || signal.ssi_signo == SIGTERM)
					running = false;
				else if (signal.ssi_signo == SIGHUP)
					/* Replaying initial-load semantic APIs
					 * is not failure-atomic. Preserve the
					 * active generation. */
					fprintf(stderr,
						"openeigrpd: SIGHUP reload rejected: atomic live configuration commit is unavailable\n");
			}
			if (bytes < 0 && errno != EAGAIN && errno != EINTR)
				return 1;
		}
		if (!running)
			break;

		if (fds[1].fd >= 0
		    && (fds[1].revents
			& (POLLIN | POLLERR | POLLHUP | POLLNVAL))) {
			if (eigrp_linux_intf_process() != EIGRP_RESULT_SUCCESS)
				fprintf(stderr,
					"openeigrpd: interface Netlink processing failed\n");
			/* An interface metric can change without a route event.
			 */
			if (fds[2].fd >= 0
			    && eigrp_linux_redist_resync()
				       != EIGRP_RESULT_SUCCESS)
				fprintf(stderr,
					"openeigrpd: redistribution resync failed\n");
		}
		if (fds[2].fd >= 0
		    && (fds[2].revents
			& (POLLIN | POLLERR | POLLHUP | POLLNVAL))) {
			if (eigrp_linux_redist_process()
			    != EIGRP_RESULT_SUCCESS)
				fprintf(stderr,
					"openeigrpd: route Netlink processing failed\n");
		}

		now = daemon_now();
		if (fds[1].fd < 0 && now >= intf_retry) {
			if (eigrp_linux_intf_process() != EIGRP_RESULT_SUCCESS)
				fprintf(stderr,
					"openeigrpd: interface Netlink recovery failed\n");
			intf_retry = now + OPENEIGRPD_NETLINK_RETRY;
		}
		if (fds[2].fd < 0 && now >= redist_retry) {
			/* A disconnected redistribution observer retains its
			 * subscriptions and needs an explicit resync. */
			(void)eigrp_linux_redist_process();
			redist_retry = now + OPENEIGRPD_NETLINK_RETRY;
		}
		if (now >= rib_due) {
			if (eigrp_linux_rib_reconcile() != EIGRP_RESULT_SUCCESS)
				fprintf(stderr,
					"openeigrpd: kernel RIB reconciliation failed\n");
			rib_due = now + OPENEIGRPD_RIB_INTERVAL;
		}
	}
	return 0;
}

int main(int argc, char **argv)
{
	struct daemon_context context = {
		.lock = PTHREAD_MUTEX_INITIALIZER,
	};
	const char *config_path = OPENEIGRPD_CONFIG;
	const char *socket_path = OPENEIGRPD_SOCKET;
	eigrp_unix_config_t *config = NULL;
	unsigned error_line = 0;
	eigrp_result_t result;
	bool check = false;
	bool core_initialized = false;
	bool intf_started = false;
	bool management_started = false;
	int signal_fd = -1;
	int status = 1;
	int opt;

	while ((opt = getopt(argc, argv, "f:s:tVh")) != -1) {
		switch (opt) {
		case 'f':
			config_path = optarg;
			break;
		case 's':
			socket_path = optarg;
			break;
		case 't':
			check = true;
			break;
		case 'V':
			printf("openeigrpd %s\n", EIGRP_RELEASE);
			return 0;
		case 'h':
			daemon_usage(stdout);
			return 0;
		default:
			daemon_usage(stderr);
			return 2;
		}
	}
	if (optind != argc || config_path[0] != '/' || socket_path[0] != '/') {
		daemon_usage(stderr);
		return 2;
	}

	config = eigrp_unix_config_create();
	if (!config) {
		fprintf(stderr, "openeigrpd: cannot allocate config plan\n");
		return 1;
	}
	result = daemon_config_parse(config, config_path, &error_line);
	if (result != EIGRP_RESULT_SUCCESS)
		goto done;
	if (check) {
		status = 0;
		goto done;
	}

	signal_fd = daemon_signal_fd();
	if (signal_fd < 0) {
		fprintf(stderr, "openeigrpd: signal setup failed: %s\n",
			strerror(errno));
		goto done;
	}
	(void)signal(SIGPIPE, SIG_IGN);
	eigrp_sys_runtime_init();
	eigrp_init();
	core_initialized = true;
	eigrp_rib_init();
	eigrp_sys_policy_init();
	eigrp_linux_config_policy_register();

	if (eigrp_linux_intf_start() != EIGRP_RESULT_SUCCESS) {
		fprintf(stderr, "openeigrpd: interface discovery failed\n");
		goto done;
	}
	intf_started = true;
	if (!eigrp_process_receive_start()) {
		fprintf(stderr, "openeigrpd: packet receive worker failed\n");
		goto done;
	}

	result = eigrp_unix_config_commit(config, &error_line);
	if (result != EIGRP_RESULT_SUCCESS) {
		fprintf(stderr,
			"openeigrpd: startup config rejected at line %u (result %u)\n",
			error_line, (unsigned)result);
		goto done;
	}
	if (daemon_socket_directory(socket_path,
				    strcmp(socket_path, OPENEIGRPD_SOCKET) == 0)
	    < 0) {
		fprintf(stderr, "openeigrpd: socket directory: %s\n",
			strerror(errno));
		goto done;
	}
	pthread_mutex_lock(&context.lock);
	context.live = true;
	pthread_mutex_unlock(&context.lock);
	if (eigrp_linux_mgnt_start(socket_path, getegid(), 0660, daemon_execute,
				   &context)
	    < 0) {
		fprintf(stderr, "openeigrpd: management socket %s: %s\n",
			socket_path, strerror(errno));
		goto done;
	}
	management_started = true;
	fprintf(stderr, "openeigrpd: started, control socket %s\n",
		socket_path);
	status = daemon_loop(signal_fd);

done:
	/* Stop external consumers before any AF/config object is retired. */
	if (management_started)
		eigrp_linux_mgnt_stop();
	pthread_mutex_lock(&context.lock);
	context.live = false;
	pthread_mutex_unlock(&context.lock);
	if (core_initialized) {
		/* eigrp_terminate waits for AF/packet/scheduler workers,
		 * removes desired kernel routes, and closes per-AF raw sockets.
		 */
		eigrp_terminate();
		eigrp_sys_policy_finish();
	}
	if (intf_started)
		eigrp_linux_intf_stop();
	eigrp_unix_keychain_reset();
	if (signal_fd >= 0)
		close(signal_fd);
	eigrp_unix_config_delete(config);
	(void)pthread_mutex_destroy(&context.lock);
	return status;
}

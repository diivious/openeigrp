// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * OpenEIGRP local management client. No portable protocol state or host
 * scheduler is linked into this executable.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <arpa/inet.h>
#include <errno.h>
#include <limits.h>
#include <stddef.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <strings.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/utsname.h>
#include <time.h>
#include <unistd.h>

#include "eigrp_features.h"
#include "eigrp_const.h"
#include "eigrp_metric.h"
#include "eigrp_integration_version.h"
#include "eigrp_linux_mgnt.h"
#include "eigrp_linux_version.h"

#define EIGRP_LINUX_CTL_SOCKET "/run/openeigrpd/control.sock"
#define EIGRP_LINUX_CTL_TIMEOUT_MS 3000
#define EIGRP_LINUX_CTL_FIELD_MAX 64
#define EIGRP_LINUX_CTL_VALUE_MAX 1024

struct field {
	char key[48];
	char value[EIGRP_LINUX_CTL_VALUE_MAX];
};

struct record {
	struct field fields[EIGRP_LINUX_CTL_FIELD_MAX];
	size_t count;
};

struct scope {
	bool runtime;
	uint16_t afi;
	uint16_t asn;
	uint32_t vrf;
	char vrf_name[64];
	char instance_name[128];
	uint32_t router_id;
	uint8_t target[EIGRP_LINUX_MGNT_TARGET_SIZE];
};

struct render {
	bool runtime;
	const char *view;
	const char *interface_name;
	bool detail;
	bool all_links;
	bool static_only;
	const char *prefix;
	bool json;
	uint16_t afi;
	bool header_printed;
	bool tech_support;
	int timer_group;
	bool sia_printed;
	uint32_t accounting[2][6];
	bool have_accounting_summary;
	unsigned printed;
	bool interface_empty;
};

struct options {
	const char *socket;
	uint16_t afi;
	uint16_t asn;
	uint32_t vrf;
	bool afi_set;
	bool asn_set;
	bool vrf_set;
	const char *vrf_name;
	bool json;
};

static uint16_t get16(const uint8_t *p)
{
	return ((uint16_t)p[0] << 8) | p[1];
}

static uint32_t get32(const uint8_t *p)
{
	return ((uint32_t)get16(p) << 16) | get16(p + 2);
}

static void put16(uint8_t *p, uint16_t value)
{
	p[0] = (uint8_t)(value >> 8);
	p[1] = (uint8_t)value;
}

static void put32(uint8_t *p, uint32_t value)
{
	put16(p, (uint16_t)(value >> 16));
	put16(p + 2, (uint16_t)value);
}

static const char *status_name(uint16_t status)
{
	switch (status) {
	case EIGRP_LINUX_MGNT_INVALID:
		return "invalid request";
	case EIGRP_LINUX_MGNT_VERSION_UNSUPPORTED:
		return "incompatible client/daemon version or management protocol";
	case EIGRP_LINUX_MGNT_DENIED:
		return "permission denied (daemon administrator required)";
	case EIGRP_LINUX_MGNT_NOT_FOUND:
		return "address family or instance not running";
	case EIGRP_LINUX_MGNT_UNSUPPORTED:
		return "operation unsupported by daemon";
	case EIGRP_LINUX_MGNT_BUSY:
		return "daemon busy";
	case EIGRP_LINUX_MGNT_INTERNAL:
		return "daemon internal error";
	default:
		return "unknown daemon status";
	}
}

static void usage(FILE *file)
{
	fprintf(file,
		"Usage: eigrp [options] show [address-family ipv4|ipv6 [vrf NAME]] [ASN] VIEW [detail] [IFNAME]\n"
		"       eigrp [options] clear [address-family ipv4|ipv6 [vrf NAME]] [ASN] TARGET\n"
		"       eigrp [options] version\n"
		"       eigrp help\n\n"
		"Views: version, capabilities, protocols, neighbors, interfaces, topology,\n"
		"       statistics, traffic, events, timers, diagnostics, tech-support\n"
		"Clear targets: events, neighbors [IFNAME|ADDRESS] [soft], topology [PREFIX]\n\n"
		"Abbreviations must uniquely identify a keyword. Omitted AFI/ASN selects all.\n"
		"Options:\n"
		"  -s, --socket PATH   Control socket (default: %s)\n"
		"  -a, --asn N        Filter to an autonomous system (1..65535)\n"
		"  -4, -6            IPv4 or IPv6\n"
		"      --afi 4|6     Address family\n"
		"      --vrf-id N    Numeric daemon VRF ID (0 is default)\n"
		"      --json        Preserve daemon JSON-lines output\n"
		"  -h, --help        Show this help\n",
		EIGRP_LINUX_CTL_SOCKET);
}

/* Match the shortest unique prefix; never guess at an ambiguous token. */
static int keyword(const char *word, const char *const *names, size_t count)
{
	size_t i, length = strlen(word);
	int found = -1;

	if (!length)
		return -1;
	for (i = 0; i < count; ++i) {
		if (strncasecmp(word, names[i], length) != 0)
			continue;
		if (found != -1)
			return -2;
		found = (int)i;
	}
	return found;
}

static bool number(const char *arg, uint32_t limit, uint32_t *value)
{
	unsigned long parsed;
	char *end;

	const unsigned char *digit = (const unsigned char *)arg;

	if (!arg || !*arg)
		return false;
	while (*digit) {
		if (*digit < '0' || *digit > '9')
			return false;
		digit++;
	}
	errno = 0;
	parsed = strtoul(arg, &end, 10);
	if (errno || *end || parsed > limit)
		return false;
	*value = (uint32_t)parsed;
	return true;
}

static int64_t now_ms(void)
{
	struct timespec ts;

	if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
		return -1;
	return (int64_t)ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

/* Bound each full I/O operation, even if a peer trickles bytes. */
static int ready(int fd, short events, int64_t deadline)
{
	struct pollfd pfd = {.fd = fd, .events = events};
	int64_t remaining;
	int result;

	for (;;) {
		remaining = deadline - now_ms();
		if (remaining <= 0) {
			errno = ETIMEDOUT;
			return -1;
		}
		result = poll(&pfd, 1, (int)remaining);
		if (result >= 0 || errno != EINTR)
			break;
	}
	if (result == 0) {
		errno = ETIMEDOUT;
		return -1;
	}
	if (result < 0)
		return -1;
	if (pfd.revents & (POLLNVAL | POLLERR)) {
		errno = ECONNRESET;
		return -1;
	}
	if (!(pfd.revents & events)) {
		errno = ECONNRESET;
		return -1;
	}
	return 0;
}

static int connect_daemon(const char *path)
{
	struct sockaddr_un addr = {.sun_family = AF_UNIX};
	socklen_t length;
	int fd, error;

	if (!path || path[0] != '/' || strlen(path) >= sizeof(addr.sun_path)) {
		fprintf(stderr,
			"eigrp: socket path must be absolute and shorter than %zu bytes\n",
			sizeof(addr.sun_path));
		return -1;
	}
	fd = socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
	if (fd < 0)
		goto failed;
	memcpy(addr.sun_path, path, strlen(path) + 1);
	length = (socklen_t)(offsetof(struct sockaddr_un, sun_path)
			     + strlen(path) + 1);
	if (connect(fd, (struct sockaddr *)&addr, length) == 0)
		return fd;
	if (errno == EINPROGRESS
	    && ready(fd, POLLOUT, now_ms() + EIGRP_LINUX_CTL_TIMEOUT_MS) == 0) {
		socklen_t size = sizeof(error);
		if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &size) == 0) {
			if (!error)
				return fd;
			errno = error;
		}
	}
failed:
	error = errno;
	if (fd >= 0)
		close(fd);
	fprintf(stderr, "eigrp: cannot connect to daemon at %s: %s\n", path,
		strerror(error));
	return -1;
}

static int transfer(int fd, void *buffer, size_t count, bool send_data)
{
	uint8_t *bytes = buffer;
	size_t done = 0;
	int64_t deadline = now_ms() + EIGRP_LINUX_CTL_TIMEOUT_MS;

	while (done < count) {
		ssize_t size;

		if (ready(fd, send_data ? POLLOUT : POLLIN, deadline) != 0)
			return -1;
		if (send_data)
			size = send(fd, bytes + done, count - done,
				    MSG_NOSIGNAL);
		else
			size = recv(fd, bytes + done, count - done, 0);
		if (size > 0) {
			done += (size_t)size;
			continue;
		}
		if (size == 0) {
			errno = ECONNRESET;
			return -1;
		}
		if (errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK)
			return -1;
	}
	return 0;
}

static int exchange(int fd, uint16_t op, const struct scope *scope,
		    uint32_t offset, char *response, size_t *length, bool *more)
{
	uint8_t request[EIGRP_LINUX_MGNT_HEADER_SIZE
			+ EIGRP_LINUX_MGNT_SELECTOR_SIZE
			+ EIGRP_LINUX_MGNT_TARGET_SIZE] = {0};
	uint8_t header[EIGRP_LINUX_MGNT_HEADER_SIZE];
	uint32_t size =
		op == EIGRP_LINUX_MGNT_IDENTITY ? EIGRP_LINUX_MGNT_IDENTITY_SIZE
		: op == EIGRP_LINUX_MGNT_PROTOCOLS
			? sizeof(uint32_t)
			: (scope ? EIGRP_LINUX_MGNT_SELECTOR_SIZE
					   + ((op == EIGRP_LINUX_MGNT_CLEAR_NEIGHBORS
					       || op == EIGRP_LINUX_MGNT_CLEAR_TOPOLOGY)
						      ? EIGRP_LINUX_MGNT_TARGET_SIZE
						      : 0)
				 : 0);
	uint16_t status, flags;

	put32(request, EIGRP_LINUX_MGNT_MAGIC);
	put16(request + 4, EIGRP_LINUX_MGNT_VERSION);
	put16(request + 6, op);
	put32(request + 12, size);
	if (op == EIGRP_LINUX_MGNT_IDENTITY) {
		if (strlen(EIGRP_RELEASE) >= EIGRP_LINUX_MGNT_IDENTITY_SIZE)
			return -1;
		memcpy(request + EIGRP_LINUX_MGNT_HEADER_SIZE, EIGRP_RELEASE,
		       strlen(EIGRP_RELEASE));
	} else if (op == EIGRP_LINUX_MGNT_PROTOCOLS) {
		put32(request + EIGRP_LINUX_MGNT_HEADER_SIZE, offset);
	} else if (scope) {
		put16(request + 16, scope->afi);
		put16(request + 18, scope->asn);
		put32(request + 20, scope->vrf);
		put32(request + 28, offset);
		memcpy(request + 32, scope->vrf_name, strlen(scope->vrf_name));
		memcpy(request + 32 + EIGRP_LINUX_MGNT_VRF_NAME_SIZE,
		       scope->instance_name, strlen(scope->instance_name));
		if (op == EIGRP_LINUX_MGNT_CLEAR_NEIGHBORS
		    || op == EIGRP_LINUX_MGNT_CLEAR_TOPOLOGY)
			memcpy(request + EIGRP_LINUX_MGNT_HEADER_SIZE
				       + EIGRP_LINUX_MGNT_SELECTOR_SIZE,
			       scope->target, EIGRP_LINUX_MGNT_TARGET_SIZE);
	}
	if (transfer(fd, request, sizeof(header) + size, true) != 0
	    || transfer(fd, header, sizeof(header), false) != 0)
		goto io_error;
	if (get32(header) != EIGRP_LINUX_MGNT_MAGIC
	    || get16(header + 4) != EIGRP_LINUX_MGNT_VERSION
	    || get16(header + 6) != op) {
		fprintf(stderr,
			"eigrp: invalid or incompatible daemon reply\n");
		return -1;
	}
	status = get16(header + 8);
	flags = get16(header + 10);
	size = get32(header + 12);
	if (flags & ~1U || size > EIGRP_LINUX_MGNT_RESPONSE_MAX
	    || (status && (size || flags))) {
		fprintf(stderr, "eigrp: malformed daemon response\n");
		return -1;
	}
	if (status) {
		fprintf(stderr, "eigrp: %s (status %u)\n", status_name(status),
			status);
		return -1;
	}
	if (transfer(fd, response, size, false) != 0)
		goto io_error;
	*length = size;
	*more = (flags & 1U) != 0;
	return 0;
io_error:
	fprintf(stderr, "eigrp: control connection lost: %s\n",
		strerror(errno));
	return -1;
}

static int hex_digit(char c)
{
	if (c >= '0' && c <= '9')
		return c - '0';
	if (c >= 'a' && c <= 'f')
		return c - 'a' + 10;
	if (c >= 'A' && c <= 'F')
		return c - 'A' + 10;
	return -1;
}

/* Decode bounded flat objects produced by the daemon. Control characters
 * remain printable escapes rather than terminal control sequences. */
static int json_string(const char **cursor, const char *end, char *dst,
		       size_t capacity)
{
	const char *p = *cursor;
	size_t used = 0;

	if (p == end || *p++ != '"')
		return -1;
	while (p < end) {
		char c = *p++;
		const char *escaped = NULL;
		int code, i;

		if (c == '"') {
			dst[used] = '\0';
			*cursor = p;
			return 0;
		}
		if (c == '\\') {
			if (p == end)
				return -1;
			c = *p++;
			switch (c) {
			case '"':
			case '\\':
			case '/':
				break;
			case 'n':
				escaped = "\\n";
				break;
			case 'r':
				escaped = "\\r";
				break;
			case 't':
				escaped = "\\t";
				break;
			case 'b':
				escaped = "\\b";
				break;
			case 'f':
				escaped = "\\f";
				break;
			case 'u':
				if (end - p < 4)
					return -1;
				code = 0;
				for (i = 0; i < 4; i++) {
					int digit = hex_digit(p[i]);
					if (digit < 0)
						return -1;
					code = code * 16 + digit;
				}
				p += 4;
				if (code >= 32 && code <= 126) {
					c = (char)code;
					break;
				}
				if (used + 6 >= capacity)
					return -1;
				used += (size_t)snprintf(dst + used,
							 capacity - used,
							 "\\u%04x", code);
				continue;
			default:
				return -1;
			}
		} else if ((unsigned char)c < 32) {
			return -1;
		}
		if (escaped) {
			if (used + 2 >= capacity)
				return -1;
			dst[used++] = escaped[0];
			dst[used++] = escaped[1];
		} else {
			if (used + 1 >= capacity)
				return -1;
			dst[used++] = c;
		}
	}
	return -1;
}

static int parse_record(const char *line, size_t length, struct record *rec)
{
	const char *p = line;
	const char *end = line + length;
	memset(rec, 0, sizeof(*rec));
	if (p == end || *p++ != '{')
		return -1;
	while (p < end && *p != '}') {
		struct field *f;
		const char *start;
		size_t n;

		if (rec->count == EIGRP_LINUX_CTL_FIELD_MAX)
			return -1;
		f = &rec->fields[rec->count++];
		if (json_string(&p, end, f->key, sizeof(f->key)) != 0
		    || p == end || *p++ != ':')
			return -1;
		if (p < end && *p == '"') {
			if (json_string(&p, end, f->value, sizeof(f->value))
			    != 0)
				return -1;
		} else {
			start = p;
			while (p < end && *p != ',' && *p != '}')
				p++;
			n = (size_t)(p - start);
			if (!n || n >= sizeof(f->value))
				return -1;
			memcpy(f->value, start, n);
			f->value[n] = '\0';
		}
		if (p < end && *p == ',') {
			p++;
			if (p == end || *p == '}')
				return -1;
		}
	}
	return p < end && *p++ == '}' && p == end ? 0 : -1;
}

static const char *field(const struct record *rec, const char *key)
{
	size_t i;

	for (i = 0; i < rec->count; ++i)
		if (strcmp(rec->fields[i].key, key) == 0)
			return rec->fields[i].value;
	return "";
}

static int identify(int fd, struct record *record, char *response, size_t *size)
{
	bool more;

	if (exchange(fd, EIGRP_LINUX_MGNT_IDENTITY, NULL, 0, response, size,
		     &more)
	    != 0) {
		fprintf(stderr,
			"eigrp: client build %s cannot manage this daemon\n",
			EIGRP_RELEASE);
		return -1;
	}
	if (more || !*size || response[*size - 1] != '\n'
	    || parse_record(response, *size - 1, record) != 0
	    || strcmp(field(record, "kind"), "identity") != 0
	    || strcmp(field(record, "release"), EIGRP_RELEASE) != 0) {
		fprintf(stderr,
			"eigrp: invalid or mismatched daemon identity\n");
		return -1;
	}
	return 0;
}

static bool interface_match(const char *filter, const char *value)
{
	char safe[EIGRP_LINUX_CTL_VALUE_MAX];
	size_t n = 0;
	const unsigned char *p = (const unsigned char *)filter;

	if (strcmp(filter, value) == 0)
		return true;
	while (*p && n + 7 < sizeof(safe)) {
		unsigned char c = *p++;
		if (c < 32 || c >= 127) {
			n += (size_t)snprintf(safe + n, sizeof(safe) - n,
					      "\\u%04x", c);
		} else {
			safe[n++] = (char)c;
		}
	}
	safe[n] = 0;
	return strcmp(safe, value) == 0;
}

/* The named FRR show handlers are the layout reference. Only fields
 * present in Linux management snapshots can be displayed; never invent
 * the missing state or reinterpret an unknown record as Cisco state. */
static const char *distance_string(const char *value)
{
	if (strcmp(value, "4294967295") == 0
	    || strcmp(value, "18446744073709551615") == 0)
		return "Inaccessible";
	return value;
}

static bool is_true(const struct record *rec, const char *key)
{
	return strcmp(field(rec, key), "true") == 0;
}

static uint64_t numeric(const struct record *rec, const char *key)
{
	return strtoull(field(rec, key), NULL, 10);
}

static uint64_t vector_value(const struct record *rec, const char *base,
			     const char *suffix)
{
	char key[64];

	(void)snprintf(key, sizeof(key), "%s_%s", base, suffix);
	return numeric(rec, key);
}

/* These conversions consume the portable raw DUAL vector snapshot, not
 * Linux kernel metrics. They use the same classic arithmetic as the core. */
static void metric_vector(const struct record *rec, const char *base,
			  const char *label)
{
	char bandwidth[32] = "n/a", delay[40] = "n/a";
	char mtu[24] = "n/a", reliability[32] = "n/a", load[32] = "n/a";
	uint64_t wide_bw, wide_delay, classic_bw, classic_delay, value;
	unsigned hops;

	wide_bw = vector_value(rec, base, "wide_bw");
	wide_delay = vector_value(rec, base, "wide_delay");
	classic_bw = vector_value(rec, base, "bw");
	classic_delay = vector_value(rec, base, "delay");
	if (wide_bw && wide_bw < EIGRP_TLV2_INACCESSIBLE)
		(void)snprintf(bandwidth, sizeof(bandwidth), "%llu",
			       (unsigned long long)wide_bw);
	else if (classic_bw && classic_bw != EIGRP_MAX_METRIC
		 && classic_bw != EIGRP_METRIC_MAX
		 && classic_bw <= UINT32_MAX) {
		value = (EIGRP_CLASSIC_SCALE * EIGRP_BANDWIDTH) / classic_bw;
		(void)snprintf(
			bandwidth, sizeof(bandwidth), "%llu",
			(unsigned long long)(value ? value
						   : EIGRP_BANDWIDTH_MIN));
	}
	if (wide_delay && wide_delay < EIGRP_TLV2_INACCESSIBLE)
		(void)snprintf(
			delay, sizeof(delay), "%llu",
			(unsigned long long)(wide_delay / EIGRP_DELAY_PICO));
	else if (classic_delay && classic_delay != EIGRP_MAX_METRIC
		 && classic_delay != EIGRP_METRIC_MAX
		 && classic_delay <= UINT32_MAX) {
		value = classic_delay / EIGRP_CLASSIC_SCALE;
		(void)snprintf(
			delay, sizeof(delay), "%llu",
			(unsigned long long)((value ? value : EIGRP_DELAY_MIN)
					     * 10U));
	}
	if (strcmp(bandwidth, "n/a") == 0 && strcmp(delay, "n/a") == 0) {
		printf("          %s: n/a\n", label);
		return;
	}
	value = vector_value(rec, base, "mtu");
	if (value)
		(void)snprintf(mtu, sizeof(mtu), "%llu",
			       (unsigned long long)value);
	value = vector_value(rec, base, "reliability");
	if (value)
		(void)snprintf(reliability, sizeof(reliability), "%llu/255",
			       (unsigned long long)value);
	value = vector_value(rec, base, "load");
	if (value)
		(void)snprintf(load, sizeof(load), "%llu/255",
			       (unsigned long long)value);
	hops = (unsigned)vector_value(rec, base, "hops");
	printf("          %s: bandwidth %s Kbit/s, delay %s us, reliability %s, load %s, MTU %s, hop count %u\n",
	       label, bandwidth, delay, reliability, load, mtu, hops);
	if (wide_delay && wide_delay < EIGRP_DELAY_PICO)
		printf("          %s wide delay: %llu ps\n", label,
		       (unsigned long long)wide_delay);
}

static void timer_record(const struct record *rec, struct render *render)
{
	uint32_t type = (uint32_t)strtoul(field(rec, "type"), NULL, 10);
	int group = type == 0		     ? 1
		    : type == 1		     ? 2
		    : type == 3 || type == 4 ? 3
					     : 4;
	const char *heading = group == 1   ? "Hello Process"
			      : group == 2 ? "Update Process"
			      : group == 3 ? "Prefix-limit Process"
					   : "SIA Process";
	uint64_t remaining_ms = numeric(rec, "remaining_ms");
	const char *interface_name = field(rec, "interface");
	const char *neighbor = field(rec, "neighbor");

	if (type > 4)
		return;
	if (render->timer_group != group) {
		printf("%s\nExpiration Type%s\n", heading,
		       group == 4 ? " for Topo(base)" : "");
		render->timer_group = group;
	}
	if (type == 0)
		printf("| %llu.%03llu Hello (%s)\n",
		       (unsigned long long)(remaining_ms / 1000),
		       (unsigned long long)(remaining_ms % 1000),
		       interface_name);
	else if (type == 1)
		printf("| %llu.%03llu Peer holding (%s via %s)\n",
		       (unsigned long long)(remaining_ms / 1000),
		       (unsigned long long)(remaining_ms % 1000),
		       *neighbor ? neighbor : "-",
		       *interface_name ? interface_name : "-");
	else if (type == 3 || type == 4)
		printf("| %llu.%03llu %s (%s via %s)\n",
		       (unsigned long long)(remaining_ms / 1000),
		       (unsigned long long)(remaining_ms % 1000),
		       type == 3 ? "Restart" : "Reset",
		       *neighbor ? neighbor : "-",
		       *interface_name ? interface_name : "-");
	else {
		printf("| %llu.%03llu ACTIVE/SIA (%s)\n",
		       (unsigned long long)(remaining_ms / 1000),
		       (unsigned long long)(remaining_ms % 1000),
		       field(rec, "destination"));
		render->sia_printed = true;
	}
}

static void print_record(const struct record *rec, struct render *render)
{
	const char *kind = field(rec, "kind");
	const char *interface_name = field(rec, "interface");
	const char *name = field(rec, "name");
	uint64_t elapsed;
	size_t i;

	if (render && strcmp(render->view, "timers") == 0) {
		if (strcmp(kind, "timer") != 0)
			return;
		timer_record(rec, render);
		render->printed++;
		return;
	}
	if (render && render->prefix
	    && (strcmp(kind, "prefix") == 0 || strcmp(kind, "path") == 0)
	    && strcmp(render->prefix, field(rec, "prefix")) != 0)
		return;
	if (render && strcmp(kind, "path") == 0 && !render->all_links
	    && !render->prefix
	    && ((strcmp(field(rec, "successor"), "true") != 0
		 && strcmp(field(rec, "feasible"), "true") != 0)
		|| strcmp(field(rec, "rd"), "4294967295") == 0
		|| strcmp(field(rec, "rd"), "18446744073709551615") == 0))
		return;
	if (render && render->interface_name
	    && ((strcmp(kind, "neighbor") == 0
		 && !interface_match(render->interface_name, interface_name))
		|| (strcmp(kind, "interface") == 0
		    && !interface_match(render->interface_name, name))))
		return;
	if (strcmp(kind, "neighbor") == 0) {
		char uptime[24];
		char seq[32], hold[32], rto[32], srtt[32];
		bool penalized = numeric(rec, "limit_status") == 'P'
				 || numeric(rec, "limit_status") == 'D';
		unsigned width = render && render->afi == 6 ? 40 : 23;

		if (render && render->static_only) {
			if (!render->header_printed) {
				printf("%-40s %-22s %-12s\n", "Address",
				       "Interface", "State");
				render->header_printed = true;
			}
			printf("%-40s %-22s %-12s\n", field(rec, "address"),
			       interface_name, field(rec, "state"));
			render->printed++;
			return;
		}
		if (render && !render->header_printed) {
			printf("%-*s %-15s %-5s %-8s %-6s %-5s %-3s %-10s\n",
			       (int)width, "Address", "Interface", "Hold",
			       "Uptime", "SRTT", "RTO", "Q", "Seq");
			printf("%*s %-15s %-5s %-8s %-6s %-5s %-3s %-10s\n",
			       (int)width, "", "", "(sec)", "", "(ms)", "(ms)",
			       "Cnt", "Num");
			render->header_printed = true;
		}
		elapsed = strtoull(field(rec, "uptime"), NULL, 10);
		if (elapsed >= 86400)
			(void)snprintf(
				uptime, sizeof(uptime), "%llud%02lluh",
				(unsigned long long)(elapsed / 86400),
				(unsigned long long)((elapsed / 3600) % 24));
		else
			(void)snprintf(
				uptime, sizeof(uptime), "%02llu:%02llu:%02llu",
				(unsigned long long)(elapsed / 3600),
				(unsigned long long)((elapsed / 60) % 60),
				(unsigned long long)(elapsed % 60));
		(void)snprintf(seq, sizeof(seq), "%s",
			       penalized ? "-" : field(rec, "sequence"));
		(void)snprintf(hold, sizeof(hold), "%s",
			       penalized ? "-" : field(rec, "hold"));
		(void)snprintf(rto, sizeof(rto), "%s",
			       penalized ? "-" : field(rec, "rto"));
		(void)snprintf(srtt, sizeof(srtt), "%s",
			       penalized		    ? "-"
			       : is_true(rec, "srtt_valid") ? field(rec, "srtt")
							    : "n/a");
		if (penalized)
			(void)snprintf(uptime, sizeof(uptime), "-");
		printf("%-*s %-15s %-5s %-8s %-6s %-5s %-3s %-10s\n",
		       (int)width, field(rec, "address"), interface_name, hold,
		       uptime, srtt, rto, field(rec, "queue"), seq);
		if (render && render->detail) {
			printf("   State: %s\n", field(rec, "state"));
			printf("   Version %s.%s/%s.%s (TLV %s), Retransmissions: %s, Current retries: %s, Prefixes: %s\n",
			       field(rec, "os_major"), field(rec, "os_minor"),
			       field(rec, "tlv_major"), field(rec, "tlv_minor"),
			       field(rec, "tlv_version"),
			       field(rec, "retransmissions"),
			       field(rec, "retry"), field(rec, "prefixes"));
			printf("   Reliable queue: %s, last received sequence: %s\n",
			       field(rec, "queue"), seq);
			if (penalized || numeric(rec, "limit_restarts"))
				printf("   Prefix-limit: %c, restarts: %s, restart/reset: %s/%s seconds\n",
				       penalized ? (char)numeric(rec,
								 "limit_status")
						 : 'A',
				       field(rec, "limit_restarts"),
				       field(rec, "limit_restart_secs"),
				       field(rec, "limit_reset_secs"));
		}
	} else if (strcmp(kind, "interface") == 0) {
		bool active = strcmp(field(rec, "runtime"), "true") == 0;

		if (!active && (!render || !render->detail))
			return;
		if (!active)
			printf("  Configured interface %s (runtime not active)\n",
			       name);
		else {
			if (render && !render->header_printed) {
				printf("%-22s %-5s %-12s %-12s %-7s %-7s %s\n",
				       "Interface", "Peers", "Bandwidth",
				       "Delay", "Hello", "Hold", "Mean SRTT");
				printf("%-22s %-5s %-12s %-12s %-7s %-7s %s\n",
				       "", "", "(Kbit/s)", "(10us)", "(sec)",
				       "(sec)", "(ms)");
				render->header_printed = true;
			}
			printf("%-22s %-5s %-12s %-12s %-7s %-7s %s\n", name,
			       field(rec, "up_peers"), field(rec, "bandwidth"),
			       field(rec, "delay"), field(rec, "hello"),
			       field(rec, "hold"),
			       is_true(rec, "mean_srtt_valid")
				       ? field(rec, "mean_srtt")
				       : "n/a");
		}
		if (render && render->detail) {
			if (active || is_true(rec, "hello_configured"))
				printf("  Hello interval: %s seconds\n",
				       field(rec, "hello"));
			else
				puts("  Hello interval: default (not explicitly configured)");
			if (active || is_true(rec, "hold_configured"))
				printf("  Hold time: %s seconds\n",
				       field(rec, "hold"));
			else
				puts("  Hold time: default (not explicitly configured)");
			if (is_true(rec, "bandwidth_percent_configured"))
				printf("  Bandwidth percent: %s%%\n",
				       field(rec, "bandwidth_percent"));
			if (active) {
				printf("  Bandwidth: %s Kbit/s, delay: %s (10us)\n",
				       field(rec, "bandwidth"),
				       field(rec, "delay"));
				printf("  MTU: %s, reliability: %s/255, load: %s/255\n",
				       field(rec, "mtu"),
				       field(rec, "reliability"),
				       field(rec, "load"));
				printf("  TLV1 peers: %s, TLV2 peers: %s\n",
				       field(rec, "tlv1_peers"),
				       field(rec, "tlv2_peers"));
			}
			printf("  Passive: %s, configured shutdown: %s\n",
			       is_true(rec, "passive") ? "yes" : "no",
			       !is_true(rec, "configured") ? "not retained"
			       : is_true(rec, "shutdown")  ? "yes"
							   : "no");
			printf("  Multicast membership: %s\n",
			       active ? (is_true(rec, "multicast") ? "enabled"
								   : "disabled")
				      : "runtime unavailable");
			if (is_true(rec, "auth_configured"))
				printf("  Authentication: configured (mode %s)\n",
				       field(rec, "auth_mode"));
			else
				puts("  Authentication: not configured");
			printf("  Split-horizon: %s, next-hop-self: %s\n",
			       is_true(rec, "split_horizon") ? "enabled"
							     : "disabled",
			       is_true(rec, "next_hop_self") ? "enabled"
							     : "disabled");
			if (active) {
				printf("  Mean SRTT: %s ms (measured UP neighbors only)\n",
				       is_true(rec, "mean_srtt_valid")
					       ? field(rec, "mean_srtt")
					       : "n/a");
				printf("  Buffered output packets: %s, reliable retransmit entries: %s\n",
				       field(rec, "output_queue"),
				       field(rec, "reliable_queue"));
				if (is_true(rec, "hello_timer_running"))
					printf("  Next hello: %s seconds\n",
					       field(rec,
						     "hello_timer_remaining"));
				else
					puts("  Next hello: not scheduled");
				printf("  Un/reliable mcasts: %s/%s  Un/reliable ucasts: %s/%s\n",
				       field(rec, "unreliable_mcast"),
				       field(rec, "reliable_mcast"),
				       field(rec, "unreliable_ucast"),
				       field(rec, "reliable_ucast"));
				printf("  Mcast exceptions: %s  CR packets: %s  Retransmissions sent: %s\n",
				       field(rec, "mcast_exceptions"),
				       field(rec, "cr_packets"),
				       field(rec, "retransmissions"));
				printf("  Pacing: %s bytes sent, %s deferrals, %s ms deferred\n",
				       field(rec, "pacing_bytes"),
				       field(rec, "pacing_defers"),
				       field(rec, "pacing_defer_ms"));
			}
		}

	} else if (strcmp(kind, "prefix") == 0) {
		if (render && render->prefix) {
			printf("  State is %s (DUAL state %s), %s successors, FD is %s",
			       is_true(rec, "active") ? "Active" : "Passive",
			       field(rec, "dual_state"),
			       field(rec, "successors"),
			       distance_string(field(rec, "fd")));
			if (render->all_links)
				printf(", serno %s", field(rec, "serial"));
			putchar('\n');
			printf("  Replies pending: %s, serial number: %s\n",
			       field(rec, "pending_replies"),
			       field(rec, "serial"));
		} else {
			printf("%c %s, %s successors, FD is %s",
			       strcmp(field(rec, "active"), "true") == 0 ? 'A'
									 : 'P',
			       field(rec, "prefix"), field(rec, "successors"),
			       distance_string(field(rec, "fd")));
			if (render && render->all_links)
				printf(", serno %s", field(rec, "serial"));
			putchar('\n');
		}
	} else if (strcmp(kind, "path") == 0) {
		const char *ifname =
			*interface_name ? interface_name : "<unknown>";

		if (is_true(rec, "connected"))
			printf("        via Connected, %s\n", ifname);
		else if (is_true(rec, "redistributed"))
			printf("        via Redistributed (%s/%s)\n",
			       distance_string(field(rec, "distance")),
			       distance_string(field(rec, "rd")));
		else
			printf("        via %s (%s/%s), %s\n",
			       field(rec, "next_hop"),
			       distance_string(field(rec, "distance")),
			       distance_string(field(rec, "rd")), ifname);
		if (render && render->prefix) {
			printf("          Route is %s%s%s\n",
			       is_true(rec, "external") ? "External"
							: "Internal",
			       is_true(rec, "successor") ? ", successor" : "",
			       is_true(rec, "feasible") ? ", feasible successor"
							: "");
			printf("          Composite metric (%s/%s)\n",
			       distance_string(field(rec, "distance")),
			       distance_string(field(rec, "rd")));
			metric_vector(rec, "total", "Total vector");
			metric_vector(rec, "reported", "Reported vector");
		}
	} else if (strcmp(kind, "accounting_summary") == 0) {
		printf("Total Prefix Count: %s\n", field(rec, "total"));
		puts("States: A-Adjacency, P-Suspended, D-Locked");
		printf("%-5s %-40s %-22s %-12s %-13s %-11s %s\n", "State",
		       "Address/Source", "Interface", "Prefix Count",
		       "Restart Count", "Restart(s)", "Reset(s)");
		if (render) {
			static const char *const names[] = {
				"state",	"prefixes",   "restarts",
				"restart_secs", "reset_secs", "penalty"};
			const char *const bases[] = {"process", "redist"};
			for (size_t b = 0; b < 2; b++)
				for (size_t j = 0; j < 6; j++) {
					char key[48];
					(void)snprintf(key, sizeof(key),
						       "%s_%s", bases[b],
						       names[j]);
					render->accounting[b][j] =
						(uint32_t)numeric(rec, key);
				}
			render->have_accounting_summary = true;
		}
	} else if (strcmp(kind, "accounting_entry") == 0) {
		printf("%-5c %-40s %-22s %-12s %-13s %-11s %s\n",
		       (char)numeric(rec, "status"), field(rec, "address"),
		       field(rec, "interface"), field(rec, "prefixes"),
		       field(rec, "restarts"), field(rec, "restart_secs"),
		       field(rec, "reset_secs"));
	} else if (strcmp(kind, "traffic") == 0) {
		static const struct {
			const char *label;
			const char *sent;
			const char *received;
		} counters[] = {
			{"Hellos", "sent_hello", "received_hello"},
			{"Updates", "sent_update", "received_update"},
			{"Queries", "sent_query", "received_query"},
			{"Replies", "sent_reply", "received_reply"},
			{"Acks", "sent_ack", "received_ack"},
			{"SIA-Queries", "sent_sia_query", "received_sia_query"},
			{"SIA-Replies", "sent_sia_reply", "received_sia_reply"},
		};
		for (i = 0; i < sizeof(counters) / sizeof(counters[0]); ++i)
			printf("  %s sent/received: %s/%s\n", counters[i].label,
			       field(rec, counters[i].sent),
			       field(rec, counters[i].received));
	} else if (strcmp(kind, "protocol") == 0) {
		uint32_t id = (uint32_t)numeric(
			rec, is_true(rec, "router_id_configured")
				     ? "router_id"
				     : "effective_router_id");
		struct in_addr address = {.s_addr = htonl(id)};
		char router_id[INET_ADDRSTRLEN] = "automatic";
		const char *version =
			numeric(rec, "metric_version") == EIGRP_TLV_32B_VERSION
				? "classic (32-bit)"
			: numeric(rec, "metric_version")
					== EIGRP_TLV_64B_VERSION
				? "wide (64-bit)"
				: "unknown";

		if (id)
			(void)inet_ntop(AF_INET, &address, router_id,
					sizeof(router_id));
		printf("\nEIGRP-%s Protocol for AS(%s)\n",
		       strcmp(field(rec, "afi"), "6") == 0 ? "IPv6" : "IPv4",
		       field(rec, "asn"));
		printf("  Named instance: %s, VRF: %s\n",
		       field(rec, "instance"), field(rec, "vrf"));
		printf("  Address-family state: %s\n",
		       is_true(rec, "parent_shutdown")
			       ? "shutdown (named instance)"
		       : is_true(rec, "shutdown") ? "shutdown"
		       : is_true(rec, "runtime")  ? "active"
						  : "configured (not running)");
		printf("  Router-ID: %s\n", router_id);
		puts("  Topology: base (0)");
		if (!is_true(rec, "runtime")) {
			puts("  Protocol parameters: runtime not available");
		} else {
			printf("  Metric weights: K1=%s, K2=%s, K3=%s, K4=%s, K5=%s, K6=%s\n",
			       field(rec, "k1"), field(rec, "k2"),
			       field(rec, "k3"), field(rec, "k4"),
			       field(rec, "k5"), field(rec, "k6"));
			printf("  Metric version: %s\n", version);
			printf("  Administrative distance: internal %s, external %s\n",
			       field(rec, "distance_internal"),
			       field(rec, "distance_external"));
			printf("  Maximum paths: %s, maximum hops: %s\n",
			       field(rec, "max_paths"), field(rec, "max_hops"));
			printf("  Variance: %s\n", field(rec, "variance"));
			if (numeric(rec, "active_time"))
				printf("  Active-time configured: %s seconds\n",
				       field(rec, "active_time"));
			else
				puts("  Active-time configured: disabled");
			if (is_true(rec, "topology_count_valid"))
				printf("  Reachable topology prefixes: %s\n",
				       field(rec, "topology_prefixes"));
			else
				puts("  Reachable topology prefixes: n/a (not counted)");
			printf("  Redistributed prefixes: %s\n",
			       field(rec, "redistributed_prefixes"));
		}
	} else if (strcmp(kind, "capability") == 0) {
		static const char *const keys[] = {
			"ipv4", "ipv6", "tlv1",	 "tlv2", "wide_metrics",
			"bfd",	"snmp", "manet", "mtr",	 "evn"};

		printf("OpenEIGRP capabilities (release %s):\n",
		       field(rec, "release"));
		for (i = 0; i < sizeof(keys) / sizeof(keys[0]); ++i)
			printf("  %-17s : %s\n", keys[i],
			       strcmp(field(rec, keys[i]), "true") == 0
				       ? "Supported"
				       : "Not Supported");
	} else if (strcmp(kind, "event_summary") == 0) {
		if (render && strcmp(render->view, "events") == 0) {
			if (render->tech_support)
				printf("  Event log capacity %s stored %s written %s overwritten %s\n",
				       field(rec, "capacity"),
				       field(rec, "count"),
				       field(rec, "written"),
				       field(rec, "overwritten"));
			if (strcmp(field(rec, "count"), "0") == 0)
				puts("  No events recorded");
			return;
		}
		printf("Event log: %s entries (capacity %s, written %s, overwritten %s)\n",
		       field(rec, "count"), field(rec, "capacity"),
		       field(rec, "written"), field(rec, "overwritten"));
	} else if (strcmp(kind, "event") == 0) {
		printf("%s  %s\n", field(rec, "number"), field(rec, "message"));
	} else if (strcmp(kind, "timer") == 0) {
		printf("  Timer %s, Interface %s, Neighbor %s, expires in %s sec\n",
		       field(rec, "type"), interface_name,
		       *field(rec, "neighbor") ? field(rec, "neighbor") : "-",
		       field(rec, "remaining"));
	} else if (strcmp(kind, "debug") == 0) {
		printf("  Debug AFI %s AS %s VRF %s category %s\n",
		       field(rec, "afi"), field(rec, "asn"), field(rec, "vrf"),
		       field(rec, "category"));
	} else {
		printf("%s:", *kind ? kind : "record");
		for (i = 0; i < rec->count; i++) {
			if (strcmp(rec->fields[i].key, "kind") != 0)
				printf(" %s=%s", rec->fields[i].key,
				       rec->fields[i].value);
		}
		putchar('\n');
	}
	if (render)
		render->printed++;
}

static void accounting_footer(const struct render *render)
{
	static const char *const labels[] = {"Process topology",
					     "Redistribution"};
	for (unsigned i = 0; i < 2; i++) {
		const uint32_t *v = render->accounting[i];
		const char *state = v[0] == 2	? "locked"
				    : v[0] == 1 ? "suspended"
						: "normal";
		printf("  %s: %s, prefixes: %u, violations: %u, restart/reset: %u/%u seconds, penalty: %u (Q16)\n",
		       labels[i], state, v[1], v[2], v[3], v[4], v[5]);
	}
}

static void print_heading(const struct scope *scope,
			  const struct render *render)
{
	const char *instance =
		*scope->instance_name ? scope->instance_name : "n/a";
	const char *afi = scope->afi == 6 ? "IPv6" : "IPv4";
	const char *subject =
		strcmp(render->view, "neighbors") == 0
			? "Address-family Neighbors"
		: strcmp(render->view, "static-neighbors") == 0
			? "Static Neighbors"
		: strcmp(render->view, "accounting") == 0 ? "Accounting"
		: strcmp(render->view, "interfaces") == 0
			? "Address-family Interfaces"
		: strcmp(render->view, "statistics") == 0
				|| strcmp(render->view, "traffic") == 0
			? "Traffic Statistics"
		: strcmp(render->view, "timers") == 0 ? "Address-family Timers"
		: strcmp(render->view, "events") == 0 ? "Events"
						      : "Diagnostics";

	if (strcmp(render->view, "topology") == 0
	    || strcmp(render->view, "accounting") == 0) {
		struct in_addr address = {.s_addr = htonl(scope->router_id)};
		char id[INET_ADDRSTRLEN] = "n/a";

		if (scope->router_id)
			(void)inet_ntop(AF_INET, &address, id, sizeof(id));
		if (strcmp(render->view, "accounting") == 0)
			printf("\nEIGRP-%s VR(%s) Accounting for AS(%u)/ID(%s)",
			       afi, instance, scope->asn, id);
		else
			printf("\nEIGRP-%s VR(%s) Topology %s for AS(%u)/ID(%s)",
			       afi, instance,
			       render->prefix ? "Entry" : "Table", scope->asn,
			       id);
	} else
		printf("\nEIGRP-%s VR(%s) %s for AS(%u)", afi, instance,
		       subject, scope->asn);
	if (strcmp(scope->vrf_name, "default") != 0)
		printf(" VRF(%s)", scope->vrf_name);
	if (strcmp(render->view, "topology") == 0 && render->prefix)
		printf(" for %s", render->prefix);
	putchar('\n');
	if (!render->runtime && strcmp(render->view, "accounting") != 0)
		puts("  Runtime state: address-family not active in the runtime");
	if (strcmp(render->view, "topology") == 0 && !render->prefix)
		puts("Codes: P - Passive, A - Active, U - Update, Q - Query, R - Reply,\n"
		     "       r - reply Status, s - sia Status\n");
	else if (strcmp(render->view, "events") == 0 && render->runtime)
		printf("Event information for AS %u:\n", scope->asn);
}

static void print_empty(const struct render *render, const struct scope *scope)
{
	if (render->printed)
		return;
	if (!render->runtime) {
		if (strcmp(render->view, "events") == 0)
			puts("  Event history is unavailable while the address-family is not running");
		else if (strcmp(render->view, "timers") == 0)
			puts("  Runtime timer data is not available");
		else if (strcmp(render->view, "accounting") == 0)
			puts("  Runtime accounting data is not available");
		else if (strcmp(render->view, "traffic") == 0
			 || strcmp(render->view, "statistics") == 0)
			puts("  Runtime traffic counters are not available");
		else if (strcmp(render->view, "topology") == 0)
			puts(render->prefix ? "% Network not in table"
					    : "  Topology table is empty");
		else if (strcmp(render->view, "interfaces") != 0
			 && strcmp(render->view, "neighbors") != 0
			 && strcmp(render->view, "static-neighbors") != 0)
			puts("No records.");
		if (strcmp(render->view, "events") == 0
		    || strcmp(render->view, "timers") == 0
		    || strcmp(render->view, "accounting") == 0
		    || strcmp(render->view, "traffic") == 0
		    || strcmp(render->view, "statistics") == 0
		    || strcmp(render->view, "topology") == 0)
			return;
	}
	if (strcmp(render->view, "neighbors") == 0
	    || strcmp(render->view, "static-neighbors") == 0)
		printf("  No EIGRP %sneighbors matched%s%s\n",
		       render->static_only ? "static " : "",
		       render->interface_name ? " " : "",
		       render->interface_name ? render->interface_name : "");
	else if (strcmp(render->view, "interfaces") == 0) {
		if (render->interface_name || render->interface_empty)
			printf("  No EIGRP interfaces matched%s%s\n",
			       render->interface_name ? " " : "",
			       render->interface_name ? render->interface_name
						      : "");
		else if (!render->detail)
			puts("  No active EIGRP interfaces");
	} else if (strcmp(render->view, "topology") == 0)
		puts(render->prefix ? "% Network not in table"
				    : "  Topology table is empty");
	else if (strcmp(render->view, "timers") == 0)
		printf("  No active %s EIGRP timers are currently scheduled\n",
		       scope->afi == 6 ? "IPv6" : "IPv4");
	else if (strcmp(render->view, "traffic") == 0
		 || strcmp(render->view, "statistics") == 0)
		puts("  Runtime traffic counters are not available");
	else if (strcmp(render->view, "events") != 0)
		puts("No records.");
}

typedef int (*record_cb)(const struct record *, void *);

static int show_page(const char *page, size_t length, bool json,
		     record_cb callback, void *arg, size_t *count,
		     struct render *render)
{
	const char *p = page;
	const char *end = page + length;
	struct record *rec;
	int rc = 0;

	*count = 0;
	rec = calloc(1, sizeof(*rec));
	if (!rec) {
		fprintf(stderr, "eigrp: out of memory\n");
		return -1;
	}
	while (p < end) {
		const char *newline = memchr(p, '\n', (size_t)(end - p));
		if (!newline || newline == p
		    || *count == EIGRP_LINUX_MGNT_PAGE_SIZE
		    || parse_record(p, (size_t)(newline - p), rec) != 0) {
			fprintf(stderr,
				"eigrp: malformed JSON-lines response\n");
			rc = -1;
			break;
		}
		if (strcmp(field(rec, "kind"), "interface_empty") == 0) {
			if (render)
				render->interface_empty = true;
		} else {
			if (callback && callback(rec, arg) != 0) {
				rc = -1;
				break;
			}
			if (json)
				(void)fwrite(p, 1, (size_t)(newline + 1 - p),
					     stdout);
			else if (!callback)
				print_record(rec, render);
		}
		(*count)++;
		p = newline + 1;
	}
	free(rec);
	return rc;
}

static int query_internal(const struct options *opts, uint16_t op,
			  const struct scope *scope, record_cb callback,
			  void *arg, struct render *render)
{
	char page[EIGRP_LINUX_MGNT_RESPONSE_MAX];
	uint32_t offset = 0;
	bool more;
	size_t size, count;
	int fd, rc = -1;

	fd = connect_daemon(opts->socket);
	if (fd < 0)
		return -1;
	{
		struct record identity;
		size_t identity_size;

		if (identify(fd, &identity, page, &identity_size) != 0)
			goto done;
		if (op == EIGRP_LINUX_MGNT_IDENTITY) {
			if (opts->json)
				(void)fwrite(page, 1, identity_size, stdout);
			else
				print_record(&identity, render);
			rc = 0;
			goto done;
		}
	}
	do {
		if (exchange(fd, op, scope, offset, page, &size, &more) != 0)
			goto done;
		if (show_page(page, size, opts->json && !callback, callback,
			      arg, &count, render)
		    != 0)
			goto done;
		if (more && (!count || count > UINT32_MAX - offset)) {
			fprintf(stderr,
				"eigrp: response exceeds management pagination limit\n");
			goto done;
		}
		offset += (uint32_t)count;
	} while (more);
	if (!offset && !opts->json && !callback && !render
	    && op != EIGRP_LINUX_MGNT_CLEAR_EVENTS)
		puts(op == EIGRP_LINUX_MGNT_PROTOCOLS
			     ? "No named EIGRP address families are configured"
			     : "No records.");
	rc = 0;
done:
	close(fd);
	return rc;
}

static int query(const struct options *opts, uint16_t op,
		 const struct scope *scope, record_cb callback, void *arg)
{
	return query_internal(opts, op, scope, callback, arg, NULL);
}

struct scopes {
	struct scope *items;
	size_t count;
	size_t capacity;
	const struct options *opts;
};

static int collect_scope(const struct record *rec, void *arg)
{
	struct scopes *list = arg;
	const struct options *opts = list->opts;
	const char *vrf = field(rec, "vrf");
	uint32_t afi, asn;

	if (strcmp(field(rec, "kind"), "protocol") != 0)
		return 0;
	if (!number(field(rec, "afi"), 6, &afi)
	    || !number(field(rec, "asn"), UINT16_MAX, &asn))
		return -1;
	if (opts->afi_set && afi != opts->afi)
		return 0;
	if (opts->asn_set && asn != opts->asn)
		return 0;
	if (opts->vrf_name && strcmp(vrf, opts->vrf_name) != 0)
		return 0;
	if (opts->vrf_set) {
		uint32_t id;

		if (is_true(rec, "vrf_id_valid")) {
			if (!number(field(rec, "vrf_id"), UINT32_MAX, &id)
			    || id != opts->vrf)
				return 0;
		} else if (opts->vrf != 0 || strcmp(vrf, "default") != 0)
			return 0;
	}
	if (strlen(field(rec, "instance"))
		    >= sizeof(list->items[0].instance_name)
	    || strlen(vrf) >= sizeof(list->items[0].vrf_name)) {
		fprintf(stderr,
			"eigrp: configured instance or VRF name too long\n");
		return -1;
	}
	if (list->count == list->capacity) {
		size_t capacity = list->capacity ? list->capacity * 2 : 16;
		struct scope *items;

		if (capacity < list->capacity
		    || capacity > SIZE_MAX / sizeof(*items)) {
			fprintf(stderr,
				"eigrp: too many protocol instances to select\n");
			return -1;
		}
		items = realloc(list->items, capacity * sizeof(*items));
		if (!items) {
			fprintf(stderr,
				"eigrp: out of memory selecting instances\n");
			return -1;
		}
		list->items = items;
		list->capacity = capacity;
	}
	list->items[list->count] =
		(struct scope){.afi = (uint16_t)afi,
			       .asn = (uint16_t)asn,
			       .vrf = opts->vrf_set ? opts->vrf : 0,
			       .runtime = is_true(rec, "runtime")};
	(void)snprintf(list->items[list->count].instance_name,
		       sizeof(list->items[list->count].instance_name), "%s",
		       field(rec, "instance"));
	(void)snprintf(list->items[list->count].vrf_name,
		       sizeof(list->items[list->count].vrf_name), "%s", vrf);
	(void)number(is_true(rec, "runtime") ? field(rec, "effective_router_id")
					     : field(rec, "router_id"),
		     UINT32_MAX, &list->items[list->count].router_id);
	list->count++;
	return 0;
}

static int select_scopes(const struct options *opts, struct scopes *list,
			 bool allow_empty)
{
	list->opts = opts;
	if (opts->vrf_set && opts->vrf != 0
	    && (!opts->asn_set || !opts->afi_set)) {
		fprintf(stderr,
			"eigrp: nondefault VRF ID requires explicit AFI and ASN\n");
		return -1;
	}
	if (query(opts, EIGRP_LINUX_MGNT_PROTOCOLS, NULL, collect_scope, list)
	    != 0)
		return -1;
	if (!list->count && !allow_empty) {
		fprintf(stderr,
			"eigrp: no matching configured EIGRP address-family\n");
		return -1;
	}
	return 0;
}

/* FRR parses a topology WORD as an exact network prefix, defaulting to
 * a host prefix and masking any host bits before lookup. The daemon sends
 * normalized prefix strings, so the client must apply the same rules. */
static bool topology_prefix(const char *text, uint16_t afi, char *out,
			    size_t capacity)
{
	char input[INET6_ADDRSTRLEN + 4];
	uint8_t address[16] = {0};
	char formatted[INET6_ADDRSTRLEN];
	char *slash;
	uint32_t length = afi == 4 ? 32U : 128U;
	size_t i, n;
	int family = afi == 4 ? AF_INET : AF_INET6;

	if (!text || (n = strlen(text)) == 0 || n >= sizeof(input))
		return false;
	memcpy(input, text, n + 1);
	slash = strchr(input, '/');
	if (slash) {
		*slash++ = 0;
		if (strchr(slash, '/') || !number(slash, length, &length))
			return false;
	}
	if (inet_pton(family, input, address) != 1)
		return false;
	for (i = 0; i < (afi == 4 ? 4U : 16U); i++) {
		uint32_t bits = length > i * 8 ? length - (uint32_t)i * 8 : 0;
		if (bits < 8)
			address[i] &= bits ? (uint8_t)(0xffU << (8 - bits)) : 0;
	}
	if (!inet_ntop(family, address, formatted, sizeof(formatted)))
		return false;
	return snprintf(out, capacity, "%s/%u", formatted, length)
	       < (int)capacity;
}

/* Clear payloads are validated on both sides of the socket. A target of a
 * different address family is skipped when the caller omitted the AFI. */
static int clear_target(struct scope *scope, uint16_t op, const char *target,
			bool soft)
{
	uint8_t bytes[16] = {0};
	char address[INET6_ADDRSTRLEN + 8];
	const char *slash;
	uint32_t prefix_length;
	size_t length;
	int af = scope->afi == 4 ? AF_INET : AF_INET6;

	memset(scope->target, 0, sizeof(scope->target));
	if (op == EIGRP_LINUX_MGNT_CLEAR_NEIGHBORS)
		scope->target[1] = soft ? 1 : 0;
	if (!target)
		return 0;
	if (op == EIGRP_LINUX_MGNT_CLEAR_NEIGHBORS) {
		if (inet_pton(af, target, bytes) == 1) {
			scope->target[0] = 2;
			memcpy(scope->target + 4, bytes,
			       scope->afi == 4 ? 4 : 16);
			return 0;
		}
		/* A valid address of the other family does not name an
		 * interface. */
		if (inet_pton(scope->afi == 4 ? AF_INET6 : AF_INET, target,
			      bytes)
		    == 1)
			return 1;
		length = strlen(target);
		if (length == 0 || length >= 64 || strchr(target, '/'))
			return -1;
		scope->target[0] = 1;
		memcpy(scope->target + 4, target, length);
		return 0;
	}
	slash = strchr(target, '/');
	if (!slash || strchr(slash + 1, '/'))
		return -1;
	length = (size_t)(slash - target);
	if (!length || length >= sizeof(address)
	    || !number(slash + 1, 128, &prefix_length))
		return -1;
	memcpy(address, target, length);
	address[length] = 0;
	if (inet_pton(af, address, bytes) != 1) {
		if (inet_pton(scope->afi == 4 ? AF_INET6 : AF_INET, address,
			      bytes)
		    == 1)
			return 1;
		return -1;
	}
	if (prefix_length > (scope->afi == 4 ? 32U : 128U))
		return -1;
	scope->target[0] = 2;
	scope->target[2] = (uint8_t)prefix_length;
	memcpy(scope->target + 4, bytes, scope->afi == 4 ? 4 : 16);
	return 0;
}

static void print_json_string(const char *value)
{
	const unsigned char *p = (const unsigned char *)value;

	putchar('"');
	for (; *p; p++) {
		if (*p == '"' || *p == '\\')
			putchar('\\');
		if (*p < 32 || *p >= 127)
			printf("\\u%04x", *p);
		else
			putchar(*p);
	}
	putchar('"');
}

static void print_version(bool json)
{
	eigrp_integration_version_t version;

	eigrp_linux_version_read(&version);
	if (json) {
		/* The local record remains independent of daemon availability.
		 */
		fputs("{\"kind\":\"integration\",\"client\":\"eigrp\",\"name\":",
		      stdout);
		print_json_string(version.integration);
		fputs(",\"build\":", stdout);
		print_json_string(version.integration_version);
		fputs(",\"os\":", stdout);
		print_json_string(version.operating_system);
		fputs(",\"kernel\":", stdout);
		print_json_string(version.kernel_version);
		fputs(",\"architecture\":", stdout);
		print_json_string(version.architecture);
		fputs(",\"distribution\":", stdout);
		print_json_string(version.distribution);
		fputs(",\"distribution_version\":", stdout);
		print_json_string(version.distribution_version);
		fputs(",\"distribution_codename\":", stdout);
		print_json_string(version.distribution_codename);
		printf(",\"ipc\":%u}\n", EIGRP_LINUX_MGNT_VERSION);
		return;
	}
	printf("OpenEIGRP Client:\n"
	       "  Version              : %s\n"
	       "  Management IPC       : %u\n\n",
	       EIGRP_RELEASE, EIGRP_LINUX_MGNT_VERSION);
	printf("Host Version:\n"
	       "  Integration          : %s\n"
	       "  Integration build    : %s\n"
	       "  OS                   : %s\n"
	       "  Kernel               : %s\n"
	       "  Architecture         : %s\n",
	       version.integration, version.integration_version,
	       version.operating_system, version.kernel_version,
	       version.architecture);
	if (version.distribution[0])
		printf("  Distribution         : %s %s %s\n",
		       version.distribution, version.distribution_version,
		       version.distribution_codename);
}

struct view {
	const char *name;
	uint16_t op;
};

static const struct view views[] = {
	{"capabilities", EIGRP_LINUX_MGNT_CAPABILITIES},
	{"protocols", EIGRP_LINUX_MGNT_PROTOCOLS},
	{"neighbors", EIGRP_LINUX_MGNT_NEIGHBORS},
	{"interfaces", EIGRP_LINUX_MGNT_INTERFACES},
	{"topology", EIGRP_LINUX_MGNT_TOPOLOGY},
	{"routes", EIGRP_LINUX_MGNT_ROUTES},
	{"statistics", EIGRP_LINUX_MGNT_TRAFFIC},
	{"traffic", EIGRP_LINUX_MGNT_TRAFFIC},
	{"events", EIGRP_LINUX_MGNT_EVENTS},
	{"timers", EIGRP_LINUX_MGNT_TIMERS},
	{"accounting", EIGRP_LINUX_MGNT_ACCOUNTING},
	{"diagnostics", EIGRP_LINUX_MGNT_DIAGNOSTICS},
	{"version", 0},
	{"tech-support", 0},
};

/* Human-readable component inventory, sourced only from daemon status. */
static const char *capability_state(const struct record *rec, const char *key)
{
	const char *value = field(rec, key);

	if (strcmp(value, "true") == 0)
		return "Supported";
	if (strcmp(value, "false") == 0)
		return "Not Supported";
	return "Unknown";
}

static int version_capability(const struct record *rec, void *arg)
{
	static const struct {
		const char *name;
		const char *key;
		const char *description;
	} components[] = {
		{"ipv4-af", "ipv4", "IPv4 Routing Protocol Support"},
		{"ipv6-af", "ipv6", "IPv6 Routing Protocol Support"},
		{"tlv1", "tlv1", "Classic Metrics and TLV Support"},
		{"tlv2", "tlv2", "Wide Metrics TLV Support"},
		{"wide-metrics", "wide_metrics", "Wide Metrics Support"},
		{"bfd", "bfd", "BFD Integration"},
		{"snmp", "snmp", "SNMP Agent Support"},
		{"manet", "manet", "MANET Support"},
		{"mtr", "mtr", "Multi-Topology Routing Support"},
		{"evn", "evn", "Easy Virtual Network Support"},
	};
	size_t i;

	(void)arg;
	if (strcmp(field(rec, "kind"), "capability") != 0)
		return -1;
	printf("  %-21s: %-17s : %s\n", "openeigrp", field(rec, "release"),
	       "Portable EIGRP Release");
	for (i = 0; i < sizeof(components) / sizeof(components[0]); ++i)
		printf("  %-21s: %-17s : %s\n", components[i].name,
		       capability_state(rec, components[i].key),
		       components[i].description);
	return 0;
}

/* Share the exact same version layout between version and tech-support. */
static int show_version(const struct options *opts)
{
	print_version(opts->json);
	if (opts->json) {
		if (query(opts, EIGRP_LINUX_MGNT_IDENTITY, NULL, NULL, NULL)
		    != 0)
			return -1;
		return query(opts, EIGRP_LINUX_MGNT_CAPABILITIES, NULL, NULL,
			     NULL);
	}
	puts("\nOpenEIGRP Version:");
	return query(opts, EIGRP_LINUX_MGNT_CAPABILITIES, NULL,
		     version_capability, NULL);
}

/* FRR's named-mode technical-support presentation, with Linux-owned host
 * version values and the same portable capability snapshot. */
static int tech_features(const struct record *rec, void *arg)
{
	eigrp_integration_version_t version;
	static const struct {
		const char *key;
		const char *name;
		const char *supported;
	} capabilities[] = {
		{"bfd", "bfd", "BFD Support"},
		{"ipv4", "ipv4-af", "IPv4 Routing Protocol Support"},
		{"ipv6", "ipv6-af", "IPv6 Routing Protocol Support"},
		{"manet", "manet", "MANET Support"},
		{"mtr", "mtr", "MTR Support"},
		{"evn", "evn", "EVN Support"},
		{"snmp", "snmp-agent", "SNMP/SNMPv2 Agent Support"},
	};
	size_t i;

	(void)arg;
	if (strcmp(field(rec, "kind"), "capability") != 0)
		return -1;
	eigrp_linux_version_read(&version);
	puts("EIGRP features:\n");
	printf("  release              : %s\n", field(rec, "release"));
	if (version.integration)
		printf("  integration          : %s\n", version.integration);
	if (version.integration_version)
		printf("  integration-version  : %s\n",
		       version.integration_version);
	if (version.framework && version.framework_version)
		printf("  framework            : %s %s\n", version.framework,
		       version.framework_version);
	if (version.operating_system[0])
		printf("  operating-system     : %s\n",
		       version.operating_system);
	if (version.operating_system_version[0])
		printf("  os-version           : %s\n",
		       version.operating_system_version);
	if (version.kernel_version[0])
		printf("  kernel-version       : %s\n", version.kernel_version);
	if (version.distribution[0]) {
		printf("  distribution         : %s", version.distribution);
		if (version.distribution_version[0])
			printf(" %s", version.distribution_version);
		if (version.distribution_codename[0])
			printf(" (%s)", version.distribution_codename);
		putchar('\n');
	}
	if (version.architecture[0])
		printf("  architecture         : %s\n", version.architecture);
	puts("  transport            : TLV 1.0, TLV 2.0, Wide Metrics");
	for (i = 0; i < sizeof(capabilities) / sizeof(capabilities[0]); ++i)
		printf("  %-21s: %s\n", capabilities[i].name,
		       is_true(rec, capabilities[i].key)
			       ? capabilities[i].supported
			       : "Not Supported");
	return 0;
}

struct tech_protocol {
	const struct scope *scope;
	bool printed;
};

static int tech_protocol_show(const struct record *rec, void *arg)
{
	struct tech_protocol *selected = arg;
	const struct scope *scope = selected->scope;

	if (strcmp(field(rec, "kind"), "protocol") != 0
	    || numeric(rec, "asn") != scope->asn
	    || numeric(rec, "afi") != scope->afi
	    || strcmp(field(rec, "vrf"), scope->vrf_name) != 0
	    || strcmp(field(rec, "instance"), scope->instance_name) != 0)
		return 0;
	print_record(rec, NULL);
	selected->printed = true;
	return 0;
}

static int tech_support(const struct options *opts)
{
	static const struct {
		const char *view;
		uint16_t op;
		bool detail;
		bool all_links;
	} sections[] = {
		{"interfaces", EIGRP_LINUX_MGNT_INTERFACES, true, false},
		{"neighbors", EIGRP_LINUX_MGNT_NEIGHBORS, true, false},
		{"topology", EIGRP_LINUX_MGNT_TOPOLOGY, false, true},
		{"traffic", EIGRP_LINUX_MGNT_TRAFFIC, false, false},
		{"timers", EIGRP_LINUX_MGNT_TIMERS, false, false},
		{"accounting", EIGRP_LINUX_MGNT_ACCOUNTING, false, false},
		{"events", EIGRP_LINUX_MGNT_EVENTS, false, false},
	};
	struct scopes list = {.opts = opts};
	size_t i, j;
	int errors = 0;

	/* JSON lines are Linux's existing IPC diagnostic format, not an FRR
	 * named-mode representation. Keep the existing JSON contract. */
	if (opts->json) {
		if (show_version(opts) != 0)
			return 1;
		if (query(opts, EIGRP_LINUX_MGNT_PROTOCOLS, NULL, NULL, NULL)
		    != 0)
			return 1;
		if (select_scopes(opts, &list, false) != 0) {
			free(list.items);
			return 1;
		}
		for (i = 0; i < list.count; ++i) {
			const struct scope *scope = &list.items[i];
			printf("{\"kind\":\"scope\",\"asn\":%u,\"afi\":%u,"
			       "\"vrf_id\":%u}\n",
			       scope->asn, scope->afi, scope->vrf);
			for (j = 2; j < sizeof(views) / sizeof(views[0]); ++j) {
				if (strcmp(views[j].name, "timers") == 0
				    || strcmp(views[j].name, "traffic") == 0
				    || views[j].op == 0)
					continue;
				if (query_internal(opts, views[j].op, scope,
						   NULL, NULL, NULL)
				    != 0)
					errors++;
			}
		}
		free(list.items);
		return errors ? 1 : 0;
	}
	if (query(opts, EIGRP_LINUX_MGNT_CAPABILITIES, NULL, tech_features,
		  NULL)
	    != 0)
		return 1;
	if (select_scopes(opts, &list, true) != 0) {
		free(list.items);
		return 1;
	}
	if (!list.count) {
		puts("\nNo named EIGRP address families are configured");
		free(list.items);
		return 0;
	}
	for (i = 0; i < list.count; ++i) {
		const struct scope *scope = &list.items[i];
		struct tech_protocol selected = {.scope = scope};

		puts("\n============================================================");
		printf("EIGRP technical support: %s IPv%u AS %u VRF %s\n",
		       scope->instance_name, scope->afi, scope->asn,
		       scope->vrf_name);
		puts("Diagnostic sections report OpenEIGRP operational state.");
		if (query(opts, EIGRP_LINUX_MGNT_PROTOCOLS, NULL,
			  tech_protocol_show, &selected)
			    != 0
		    || !selected.printed) {
			errors++;
			continue;
		}
		for (j = 0; j < sizeof(sections) / sizeof(sections[0]); ++j) {
			struct render render = {
				.view = sections[j].view,
				.afi = scope->afi,
				.runtime = scope->runtime,
				.detail = sections[j].detail,
				.all_links = sections[j].all_links,
				.tech_support = true,
			};

			print_heading(scope, &render);
			if (query_internal(opts, sections[j].op, scope, NULL,
					   NULL, &render)
			    != 0) {
				errors++;
				continue;
			}
			print_empty(&render, scope);
			if (strcmp(render.view, "timers") == 0 && render.runtime
			    && !render.sia_printed)
				puts("SIA Process\n  No ACTIVE/SIA deadlines scheduled");
			if (strcmp(render.view, "accounting") == 0
			    && render.have_accounting_summary)
				accounting_footer(&render);
		}
	}
	free(list.items);
	return errors ? 1 : 0;
}

int main(int argc, char **argv)
{
	static const char *const actions[] = {"show", "clear"};
	static const char *const families[] = {"ipv4", "ipv6"};
	static const char *const scopes[] = {"address-family", "vrf"};
	static const char *const extras[] = {"detail", "soft", "all-links"};
	struct options opts = {.socket = EIGRP_LINUX_CTL_SOCKET};
	const char *words[12] = {0};
	struct scopes list = {0};
	uint32_t value;
	size_t used = 0, cursor = 0, i;
	int action, view = -1, extra;
	uint16_t op = 0;
	bool version_requested = false;
	const char *interface_name = NULL;
	const char *target = NULL;
	bool detail = false;
	bool all_links = false;
	bool static_only = false;
	bool soft = false;
	int errors = 0;
	size_t attempted = 0;
	bool matched_prefix = false;

	for (i = 1; i < (size_t)argc; i++) {
		const char *arg = argv[i];
		if (strcmp(arg, "-h") == 0 || strcmp(arg, "--help") == 0) {
			usage(stdout);
			return 0;
		}
		if (strcmp(arg, "--version") == 0) {
			version_requested = true;
			continue;
		}
		if (strcmp(arg, "--json") == 0) {
			opts.json = true;
			continue;
		}
		if (strcmp(arg, "-4") == 0 || strcmp(arg, "-6") == 0) {
			value = (uint32_t)(arg[1] - '0');
			if (opts.afi_set && opts.afi != value)
				goto conflicting_scope;
			opts.afi = (uint16_t)value;
			opts.afi_set = true;
			continue;
		}
		if (strcmp(arg, "-s") == 0 || strcmp(arg, "--socket") == 0
		    || strcmp(arg, "-a") == 0 || strcmp(arg, "--asn") == 0
		    || strcmp(arg, "--afi") == 0
		    || strcmp(arg, "--vrf-id") == 0) {
			if (i + 1 >= (size_t)argc) {
				fprintf(stderr, "eigrp: missing value for %s\n",
					arg);
				return 2;
			}
			if (strcmp(arg, "-s") == 0
			    || strcmp(arg, "--socket") == 0) {
				opts.socket = argv[++i];
				continue;
			}
			if (!number(argv[++i],
				    strcmp(arg, "--vrf-id") == 0 ? UINT32_MAX
								 : UINT16_MAX,
				    &value)
			    || (strcmp(arg, "--vrf-id") != 0 && value == 0)) {
				fprintf(stderr, "eigrp: invalid value for %s\n",
					arg);
				return 2;
			}
			if (strcmp(arg, "--vrf-id") == 0) {
				if (opts.vrf_set && opts.vrf != value)
					goto conflicting_scope;
				opts.vrf = value;
				opts.vrf_set = true;
			} else if (strcmp(arg, "--afi") == 0) {
				if (value != 4 && value != 6) {
					fprintf(stderr,
						"eigrp: AFI must be 4 or 6\n");
					return 2;
				}
				if (opts.afi_set && opts.afi != value)
					goto conflicting_scope;
				opts.afi = (uint16_t)value;
				opts.afi_set = true;
			} else {
				if (opts.asn_set && opts.asn != value)
					goto conflicting_scope;
				opts.asn = (uint16_t)value;
				opts.asn_set = true;
			}
			continue;
		}
		if (arg[0] == '-') {
			fprintf(stderr, "eigrp: unknown option %s\n", arg);
			return 2;
		}
		if (used == sizeof(words) / sizeof(words[0])) {
			fprintf(stderr, "eigrp: too many command words\n");
			return 2;
		}
		words[used++] = arg;
	}
	if (version_requested) {
		if (used) {
			fprintf(stderr,
				"eigrp: --version does not take a command\n");
			return 2;
		}
		print_version(opts.json);
		return 0;
	}
	if (used == 1 && strcmp(words[0], "help") == 0) {
		usage(stdout);
		return 0;
	}
	if (used == 1 && strcmp(words[0], "version") == 0) {
		print_version(opts.json);
		return 0;
	}
	if (used == 1 && strcmp(words[0], "tech-support") == 0)
		return tech_support(&opts);
	if (!used || (action = keyword(words[0], actions, 2)) < 0) {
		fprintf(stderr, "eigrp: unsupported or ambiguous command\n");
		return 2;
	}
	cursor = 1;
	if (cursor < used && keyword(words[cursor], scopes, 2) == 0) {
		int family;
		cursor++;
		if (cursor == used
		    || (family = keyword(words[cursor++], families, 2)) < 0) {
			fprintf(stderr,
				"eigrp: expected address-family ipv4|ipv6\n");
			return 2;
		}
		value = family == 0 ? 4 : 6;
		if (opts.afi_set && opts.afi != value)
			goto conflicting_scope;
		opts.afi_set = true;
		opts.afi = (uint16_t)value;
		if (cursor < used && keyword(words[cursor], scopes, 2) == 1) {
			cursor++;
			if (cursor == used || opts.vrf_set) {
				fprintf(stderr,
					"eigrp: missing or conflicting VRF name\n");
				return 2;
			}
			opts.vrf_name = words[cursor++];
		}
	}
	if (cursor < used && number(words[cursor], UINT16_MAX, &value)) {
		if (!value) {
			fprintf(stderr, "eigrp: ASN must be nonzero\n");
			return 2;
		}
		if (opts.asn_set && opts.asn != value)
			goto conflicting_scope;
		opts.asn_set = true;
		opts.asn = (uint16_t)value;
		cursor++;
	}
	if (cursor == used) {
		fprintf(stderr, "eigrp: missing show view or clear target\n");
		return 2;
	}
	if (action == 0) {
		if (strcasecmp(words[cursor], "routes") == 0) {
			fprintf(stderr,
				"eigrp: routes belong to the host RIB, not the EIGRP topology; use 'ip route'\n");
			return 2;
		}
		for (i = 0; i < sizeof(views) / sizeof(views[0]); ++i) {
			if (strcmp(views[i].name, "routes") == 0)
				continue;
			if (strncasecmp(words[cursor], views[i].name,
					strlen(words[cursor]))
			    == 0) {
				if (view >= 0) {
					fprintf(stderr,
						"eigrp: ambiguous show view %s\n",
						words[cursor]);
					return 2;
				}
				view = (int)i;
			}
		}
		if (view < 0) {
			fprintf(stderr, "eigrp: unsupported show view %s\n",
				words[cursor]);
			return 2;
		}
		op = views[view].op;
		if (op == 0) {
			if (cursor + 1 != used) {
				fprintf(stderr,
					"eigrp: unexpected arguments for %s\n",
					views[view].name);
				return 2;
			}
			if (strcmp(views[view].name, "version") == 0)
				return show_version(&opts) == 0 ? 0 : 1;
			return tech_support(&opts);
		}
	} else {
		static const char *const targets[] = {"events", "neighbors",
						      "topology"};
		int target = keyword(words[cursor], targets, 3);
		if (target < 0) {
			fprintf(stderr,
				"eigrp: unsupported or ambiguous clear target %s\n",
				words[cursor]);
			return 2;
		}
		op = target == 0   ? EIGRP_LINUX_MGNT_CLEAR_EVENTS
		     : target == 1 ? EIGRP_LINUX_MGNT_CLEAR_NEIGHBORS
				   : EIGRP_LINUX_MGNT_CLEAR_TOPOLOGY;
	}
	cursor++;
	if (action == 0 && op == EIGRP_LINUX_MGNT_TOPOLOGY && cursor < used
	    && number(words[cursor], UINT16_MAX, &value)) {
		/* FRR permits the ASN after the 'topology' keyword. */
		if (!value)
			return 2;
		if (opts.asn_set && opts.asn != value)
			goto conflicting_scope;
		opts.asn = (uint16_t)value;
		opts.asn_set = true;
		cursor++;
	}
	while (cursor < used) {
		extra = keyword(words[cursor], extras, 3);
		if (action == 0) {
			if (extra == 0
			    && (op == EIGRP_LINUX_MGNT_NEIGHBORS
				|| op == EIGRP_LINUX_MGNT_STATIC_NEIGHBORS
				|| op == EIGRP_LINUX_MGNT_INTERFACES)
			    && !detail) {
				detail = true;
			} else if (extra == 2 && op == EIGRP_LINUX_MGNT_TOPOLOGY
				   && !all_links) {
				all_links = true;
			} else if (op == EIGRP_LINUX_MGNT_NEIGHBORS
				   && strcasecmp(words[cursor], "static") == 0
				   && !static_only) {
				static_only = true;
				op = EIGRP_LINUX_MGNT_STATIC_NEIGHBORS;
			} else if (
				extra == -1 && !interface_name
				&& (op == EIGRP_LINUX_MGNT_NEIGHBORS
				    || op == EIGRP_LINUX_MGNT_STATIC_NEIGHBORS
				    || op == EIGRP_LINUX_MGNT_INTERFACES
				    || op == EIGRP_LINUX_MGNT_TOPOLOGY)) {
				interface_name = words[cursor];
			} else {
				fprintf(stderr,
					"eigrp: unsupported show arguments: %s\n",
					words[cursor]);
				return 2;
			}
		} else if (op == EIGRP_LINUX_MGNT_CLEAR_NEIGHBORS && extra == 1
			   && !soft) {
			soft = true;
		} else if (op != EIGRP_LINUX_MGNT_CLEAR_EVENTS && !target
			   && extra == -1) {
			target = words[cursor];
		} else {
			fprintf(stderr,
				"eigrp: unsupported clear arguments: %s\n",
				words[cursor]);
			return 2;
		}
		cursor++;
	}
	if (detail && op != EIGRP_LINUX_MGNT_NEIGHBORS
	    && op != EIGRP_LINUX_MGNT_STATIC_NEIGHBORS
	    && op != EIGRP_LINUX_MGNT_INTERFACES) {
		fprintf(stderr, "eigrp: detail is unsupported for this view\n");
		return 2;
	}
	if (opts.json && (detail || interface_name)) {
		fprintf(stderr,
			"eigrp: detail and interface filters do not support --json yet\n");
		return 2;
	}
	if (op <= EIGRP_LINUX_MGNT_PROTOCOLS) {
		if (opts.asn_set || opts.afi_set || opts.vrf_set
		    || opts.vrf_name) {
			fprintf(stderr,
				"eigrp: scope options are not valid for this view\n");
			return 2;
		}
		return query(&opts, op, NULL, NULL, NULL) == 0 ? 0 : 1;
	}
	if (select_scopes(&opts, &list, false) != 0) {
		free(list.items);
		return 1;
	}
	for (i = 0; i < list.count; ++i) {
		int target_result;
		char normalized[INET6_ADDRSTRLEN + 12];
		struct render render = {
			.view = action == 0 ? (static_only ? "static-neighbors"
							   : views[view].name)
					    : "events",
			.afi = list.items[i].afi,
			.runtime = list.items[i].runtime,
			.static_only = static_only,
			.interface_name = op == EIGRP_LINUX_MGNT_TOPOLOGY
						  ? NULL
						  : interface_name,
			.prefix = op == EIGRP_LINUX_MGNT_TOPOLOGY
					  ? interface_name
					  : NULL,
			.detail = detail,
			.all_links = all_links};
		if (action == 0 && op == EIGRP_LINUX_MGNT_TOPOLOGY
		    && interface_name) {
			if (!topology_prefix(interface_name, list.items[i].afi,
					     normalized, sizeof(normalized)))
				continue;
			render.prefix = normalized;
			matched_prefix = true;
		}
		if (action == 1 && op >= EIGRP_LINUX_MGNT_CLEAR_NEIGHBORS) {
			target_result =
				clear_target(&list.items[i], op, target, soft);
			if (target_result == 1)
				continue;
			if (target_result < 0) {
				fprintf(stderr,
					"eigrp: invalid clear target %s\n",
					target);
				free(list.items);
				return 2;
			}
		}
		/* Listing includes retained inactive AFs, but a clear may only
		 * target a running instance. An explicitly selected inactive AF
		 * reports the failed operation without touching other
		 * instances. */
		if (action == 1 && !list.items[i].runtime) {
			if (opts.vrf_name || opts.vrf_set
			    || (opts.asn_set && list.count == 1)) {
				fprintf(stderr,
					"eigrp: address family not running\n");
				errors++;
			}
			continue;
		}
		if (action == 1)
			attempted++;
		if (action == 0 && !opts.json) {
			print_heading(&list.items[i], &render);
		}
		if (query_internal(&opts, op, &list.items[i], NULL, NULL,
				   opts.json ? NULL : &render)
		    != 0) {
			errors++;
			continue;
		}
		if (action == 0 && !opts.json) {
			print_empty(&render, &list.items[i]);
			if (strcmp(render.view, "timers") == 0 && render.runtime
			    && !render.sia_printed)
				puts("SIA Process\n  No ACTIVE/SIA deadlines scheduled");
			if (strcmp(render.view, "accounting") == 0
			    && render.have_accounting_summary)
				accounting_footer(&render);
		}
		if (action == 1 && !opts.json)
			printf("%s cleared for %s AS %u VRF %s.\n",
			       op == EIGRP_LINUX_MGNT_CLEAR_EVENTS ? "Events"
			       : op == EIGRP_LINUX_MGNT_CLEAR_NEIGHBORS
				       ? "Neighbors"
				       : "Topology",
			       list.items[i].afi == 4 ? "IPv4" : "IPv6",
			       list.items[i].asn, list.items[i].vrf_name);
	}
	if (action == 0 && op == EIGRP_LINUX_MGNT_TOPOLOGY && interface_name
	    && !matched_prefix) {
		fprintf(stderr, "eigrp: malformed topology destination: %s\n",
			interface_name);
		free(list.items);
		return 2;
	}
	if (action == 1 && !attempted && !errors) {
		fprintf(stderr,
			"eigrp: no selected address family matches clear target\n");
		free(list.items);
		return 1;
	}
	free(list.items);
	return errors ? 1 : 0;
conflicting_scope:
	fprintf(stderr, "eigrp: conflicting address-family/AS/VRF selectors\n");
	return 2;
}

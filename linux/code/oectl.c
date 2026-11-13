// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * OpenEIGRP local management client. No portable protocol state or host
 * scheduler is linked into this executable.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <errno.h>
#include <limits.h>
#include <stddef.h>
#include <poll.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <sys/utsname.h>
#include <time.h>
#include <unistd.h>

#include "eigrp_features.h"
#include "eigrp_integration_version.h"
#include "eigrp_linux_mgnt.h"
#include "eigrp_linux_version.h"

#define OECTL_SOCKET "/run/openeigrpd/control.sock"
#define OECTL_TIMEOUT_MS 3000
#define OECTL_OFFSET_MAX 8192U
#define OECTL_FIELD_MAX 40
#define OECTL_VALUE_MAX 1024

struct field {
	char key[48];
	char value[OECTL_VALUE_MAX];
};

struct record {
	struct field fields[OECTL_FIELD_MAX];
	size_t count;
};

struct scope {
	uint16_t afi;
	uint16_t asn;
	uint32_t vrf;
};

struct options {
	const char *socket;
	uint16_t afi;
	uint16_t asn;
	uint32_t vrf;
	bool afi_set;
	bool asn_set;
	bool vrf_set;
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
		"Usage: oectl [options] show <view>\n"
		"       oectl [options] clear events\n"
		"       oectl [options] version\n"
		"       oectl help\n\n"
		"Views: capabilities, protocols, neighbors, interfaces, topology,\n"
		"       routes, statistics, events, diagnostics, tech-support\n\n"
		"Options:\n"
		"  -s, --socket PATH   Control socket (default: %s)\n"
		"  -a, --asn N        EIGRP autonomous system (1..65535)\n"
		"  -4, -6            IPv4 or IPv6 (default: autodetect)\n"
		"      --afi 4|6     Address family\n"
		"      --vrf-id N    Numeric daemon VRF ID (default: 0)\n"
		"      --json        Emit daemon JSON lines\n"
		"  -h, --help        Show this help\n\n"
		"Scoped views autodetect one running default-VRF instance. Multiple\n"
		"instances require --asn and, if ambiguous, --afi. Nondefault VRFs\n"
		"require --asn, --afi and --vrf-id. Only base topology is supported.\n"
		"The only modifying command supported by IPC v1 is clear events.\n",
		OECTL_SOCKET);
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
			"oectl: socket path must be absolute and shorter than %zu bytes\n",
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
	    && ready(fd, POLLOUT, now_ms() + OECTL_TIMEOUT_MS) == 0) {
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
	fprintf(stderr, "oectl: cannot connect to daemon at %s: %s\n", path,
		strerror(error));
	return -1;
}

static int transfer(int fd, void *buffer, size_t count, bool send_data)
{
	uint8_t *bytes = buffer;
	size_t done = 0;
	int64_t deadline = now_ms() + OECTL_TIMEOUT_MS;

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
			+ EIGRP_LINUX_MGNT_IDENTITY_SIZE] = {0};
	uint8_t header[EIGRP_LINUX_MGNT_HEADER_SIZE];
	uint32_t size = op == EIGRP_LINUX_MGNT_IDENTITY
				? EIGRP_LINUX_MGNT_IDENTITY_SIZE
				: (scope ? EIGRP_LINUX_MGNT_SELECTOR_SIZE : 0);
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
	} else if (scope) {
		put16(request + 16, scope->afi);
		put16(request + 18, scope->asn);
		put32(request + 20, scope->vrf);
		put32(request + 28, offset);
	}
	if (transfer(fd, request, sizeof(header) + size, true) != 0
	    || transfer(fd, header, sizeof(header), false) != 0)
		goto io_error;
	if (get32(header) != EIGRP_LINUX_MGNT_MAGIC
	    || get16(header + 4) != EIGRP_LINUX_MGNT_VERSION
	    || get16(header + 6) != op) {
		fprintf(stderr,
			"oectl: invalid or incompatible daemon reply\n");
		return -1;
	}
	status = get16(header + 8);
	flags = get16(header + 10);
	size = get32(header + 12);
	if (flags & ~1U || size > EIGRP_LINUX_MGNT_RESPONSE_MAX
	    || (status && (size || flags))) {
		fprintf(stderr, "oectl: malformed daemon response\n");
		return -1;
	}
	if (status) {
		fprintf(stderr, "oectl: %s (status %u)\n", status_name(status),
			status);
		return -1;
	}
	if (transfer(fd, response, size, false) != 0)
		goto io_error;
	*length = size;
	*more = (flags & 1U) != 0;
	return 0;
io_error:
	fprintf(stderr, "oectl: control connection lost: %s\n",
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

		if (rec->count == OECTL_FIELD_MAX)
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
			"oectl: client build %s cannot manage this daemon\n",
			EIGRP_RELEASE);
		return -1;
	}
	if (more || !*size || response[*size - 1] != '\n'
	    || parse_record(response, *size - 1, record) != 0
	    || strcmp(field(record, "kind"), "identity") != 0
	    || strcmp(field(record, "release"), EIGRP_RELEASE) != 0) {
		fprintf(stderr,
			"oectl: invalid or mismatched daemon identity\n");
		return -1;
	}
	return 0;
}

static void print_record(const struct record *rec)
{
	const char *kind = field(rec, "kind");
	size_t i;

	if (strcmp(kind, "neighbor") == 0) {
		printf("%-39s  %-16s  %-8s  hold=%ss  uptime=%ss  prefixes=%s\n",
		       field(rec, "address"), field(rec, "interface"),
		       field(rec, "state"), field(rec, "hold"),
		       field(rec, "uptime"), field(rec, "prefixes"));
		return;
	}
	if (strcmp(kind, "prefix") == 0) {
		printf("%-43s  %-7s  FD=%s  successors=%s\n",
		       field(rec, "prefix"),
		       strcmp(field(rec, "active"), "true") == 0 ? "ACTIVE"
								 : "PASSIVE",
		       field(rec, "fd"), field(rec, "successors"));
		return;
	}
	if (strcmp(kind, "path") == 0) {
		printf("  %-41s via %-39s  %s  metric=%s  rd=%s%s%s\n",
		       field(rec, "prefix"), field(rec, "next_hop"),
		       field(rec, "interface"), field(rec, "distance"),
		       field(rec, "rd"),
		       strcmp(field(rec, "successor"), "true") == 0
			       ? " successor"
			       : "",
		       strcmp(field(rec, "feasible"), "true") == 0 ? " feasible"
								   : "");
		return;
	}
	printf("%s:", *kind ? kind : "record");
	for (i = 0; i < rec->count; i++) {
		if (strcmp(rec->fields[i].key, "kind") == 0)
			continue;
		printf(" %s=%s", rec->fields[i].key, rec->fields[i].value);
	}
	putchar('\n');
}

typedef int (*record_cb)(const struct record *, void *);

static int show_page(const char *page, size_t length, bool json,
		     record_cb callback, void *arg, size_t *count)
{
	const char *p = page;
	const char *end = page + length;
	struct record *rec;
	int rc = 0;

	*count = 0;
	rec = calloc(1, sizeof(*rec));
	if (!rec) {
		fprintf(stderr, "oectl: out of memory\n");
		return -1;
	}
	while (p < end) {
		const char *newline = memchr(p, '\n', (size_t)(end - p));
		if (!newline || newline == p
		    || *count == EIGRP_LINUX_MGNT_PAGE_SIZE
		    || parse_record(p, (size_t)(newline - p), rec) != 0) {
			fprintf(stderr,
				"oectl: malformed JSON-lines response\n");
			rc = -1;
			break;
		}
		if (callback && callback(rec, arg) != 0) {
			rc = -1;
			break;
		}
		if (json)
			(void)fwrite(p, 1, (size_t)(newline + 1 - p), stdout);
		else if (!callback)
			print_record(rec);
		(*count)++;
		p = newline + 1;
	}
	free(rec);
	return rc;
}

static int query(const struct options *opts, uint16_t op,
		 const struct scope *scope, record_cb callback, void *arg)
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
				print_record(&identity);
			rc = 0;
			goto done;
		}
	}
	do {
		if (exchange(fd, op, scope, offset, page, &size, &more) != 0)
			goto done;
		if (show_page(page, size, opts->json && !callback, callback,
			      arg, &count)
		    != 0)
			goto done;
		if (more && (!count || offset + count > OECTL_OFFSET_MAX)) {
			fprintf(stderr,
				"oectl: response exceeds management pagination limit\n");
			goto done;
		}
		offset += (uint32_t)count;
	} while (more);
	if (!offset && !opts->json && !callback
	    && op != EIGRP_LINUX_MGNT_CLEAR_EVENTS)
		puts("No records.");
	rc = 0;
done:
	close(fd);
	return rc;
}

struct scopes {
	struct scope items[128];
	size_t count;
	bool skipped;
	const struct options *opts;
};

static int collect_scope(const struct record *rec, void *arg)
{
	struct scopes *list = arg;
	const struct options *opts = list->opts;
	const char *vrf = field(rec, "vrf");
	uint32_t afi, asn;

	if (strcmp(field(rec, "kind"), "protocol") != 0
	    || strcmp(field(rec, "runtime"), "true") != 0)
		return 0;
	if (!number(field(rec, "afi"), 6, &afi)
	    || !number(field(rec, "asn"), UINT16_MAX, &asn))
		return -1;
	if (opts->afi_set && afi != opts->afi)
		return 0;
	if (opts->asn_set && asn != opts->asn)
		return 0;
	if (strcmp(vrf, "default") != 0) {
		list->skipped = true;
		return 0;
	}
	if (list->count == sizeof(list->items) / sizeof(list->items[0])) {
		fprintf(stderr,
			"oectl: too many protocol instances to select\n");
		return -1;
	}
	list->items[list->count++] =
		(struct scope){.afi = (uint16_t)afi, .asn = (uint16_t)asn};
	return 0;
}

static int select_scope(const struct options *opts, struct scope *selected)
{
	struct scopes list = {.opts = opts};

	if (opts->asn_set && opts->afi_set) {
		*selected = (struct scope){
			.afi = opts->afi, .asn = opts->asn, .vrf = opts->vrf};
		return 0;
	}
	if (opts->vrf_set && opts->vrf != 0) {
		fprintf(stderr,
			"oectl: nondefault VRF requires --asn and --afi\n");
		return -1;
	}
	if (query(opts, EIGRP_LINUX_MGNT_PROTOCOLS, NULL, collect_scope, &list)
	    != 0)
		return -1;
	if (list.count != 1) {
		fprintf(stderr, "oectl: %s; specify --asn and --afi\n",
			list.count
				? "multiple matching instances"
				: "no matching running default-VRF instance");
		return -1;
	}
	*selected = list.items[0];
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
		fputs("{\"kind\":\"integration\",\"client\":\"oectl\",\"name\":",
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
	{"diagnostics", EIGRP_LINUX_MGNT_DIAGNOSTICS},
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

static int tech_support(const struct options *opts)
{
	struct scopes list = {.opts = opts};
	size_t i, j;
	int errors = 0;

	if (!opts->json)
		puts("=== OpenEIGRP tech-support ===");
	if (show_version(opts) != 0)
		return 1;
	if (!opts->json)
		puts("\n=== Protocols ===");
	if (query(opts, EIGRP_LINUX_MGNT_PROTOCOLS, NULL, NULL, NULL) != 0)
		return 1;
	if (opts->asn_set || opts->afi_set || opts->vrf_set) {
		struct scope scope;
		if (select_scope(opts, &scope) != 0)
			return 1;
		list.items[list.count++] = scope;
	} else if (query(opts, EIGRP_LINUX_MGNT_PROTOCOLS, NULL, collect_scope,
			 &list)
		   != 0) {
		return 1;
	}
	if (list.skipped)
		fprintf(stderr,
			"oectl: nondefault VRFs omitted; specify --asn, --afi, --vrf-id\n");
	for (i = 0; i < list.count; ++i) {
		struct scope *scope = &list.items[i];
		if (opts->json)
			printf("{\"kind\":\"scope\",\"asn\":%u,\"afi\":%u,"
			       "\"vrf_id\":%u}\n",
			       scope->asn, scope->afi, scope->vrf);
		for (j = 2; j < sizeof(views) / sizeof(views[0]); ++j) {
			if (strcmp(views[j].name, "traffic") == 0)
				continue;
			if (!opts->json)
				printf("\n=== %s (AS %u, IPv%u, VRF ID %u) ===\n",
				       views[j].name, scope->asn, scope->afi,
				       scope->vrf);
			if (query(opts, views[j].op, scope, NULL, NULL) != 0)
				errors++;
		}
	}
	return errors ? 1 : 0;
}

int main(int argc, char **argv)
{
	struct options opts = {.socket = OECTL_SOCKET};
	const char *command[3] = {0};
	struct scope scope;
	uint32_t value;
	size_t words = 0, i;
	uint16_t op = 0;
	bool scoped;
	bool version_requested = false;
	int result;

	for (i = 1; i < (size_t)argc; i++) {
		const char *arg = argv[i];
		if (strcmp(arg, "-h") == 0 || strcmp(arg, "--help") == 0) {
			usage(stdout);
			return 0;
		}
		if (strcmp(arg, "--version") == 0) {
			/* Parse all options before choosing output format. */
			version_requested = true;
			continue;
		}
		if (strcmp(arg, "--json") == 0) {
			opts.json = true;
			continue;
		}
		if (strcmp(arg, "-4") == 0 || strcmp(arg, "-6") == 0) {
			value = arg[1] - '0';
			opts.afi = (uint16_t)value;
			opts.afi_set = true;
			continue;
		}
		if (strcmp(arg, "-s") == 0 || strcmp(arg, "--socket") == 0
		    || strcmp(arg, "-a") == 0 || strcmp(arg, "--asn") == 0
		    || strcmp(arg, "--afi") == 0
		    || strcmp(arg, "--vrf-id") == 0) {
			if (i + 1 >= (size_t)argc) {
				fprintf(stderr, "oectl: missing value for %s\n",
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
				fprintf(stderr, "oectl: invalid value for %s\n",
					arg);
				return 2;
			}
			if (strcmp(arg, "--vrf-id") == 0) {
				opts.vrf = value;
				opts.vrf_set = true;
			} else if (strcmp(arg, "--afi") == 0) {
				if (value != 4 && value != 6) {
					fprintf(stderr,
						"oectl: AFI must be 4 or 6\n");
					return 2;
				}
				opts.afi = (uint16_t)value;
				opts.afi_set = true;
			} else {
				opts.asn = (uint16_t)value;
				opts.asn_set = true;
			}
			continue;
		}
		if (arg[0] == '-') {
			fprintf(stderr, "oectl: unknown option %s\n", arg);
			return 2;
		}
		if (words == sizeof(command) / sizeof(command[0])) {
			fprintf(stderr, "oectl: too many command words\n");
			return 2;
		}
		command[words++] = arg;
	}
	if (version_requested) {
		if (words != 0) {
			fprintf(stderr,
				"oectl: --version does not take a command\n");
			return 2;
		}
		print_version(opts.json);
		return 0;
	}
	if (words == 1 && strcmp(command[0], "help") == 0) {
		usage(stdout);
		return 0;
	}
	if (words == 1 && strcmp(command[0], "version") == 0) {
		print_version(opts.json);
		return 0;
	}
	if ((words == 2 && strcmp(command[0], "show") == 0
	     && strcmp(command[1], "tech-support") == 0)
	    || (words == 1 && strcmp(command[0], "tech-support") == 0))
		return tech_support(&opts);
	if (words == 2 && strcmp(command[0], "show") == 0
	    && strcmp(command[1], "version") == 0) {
		return show_version(&opts) == 0 ? 0 : 1;
	}
	if (words == 2 && strcmp(command[0], "clear") == 0
	    && strcmp(command[1], "events") == 0) {
		op = EIGRP_LINUX_MGNT_CLEAR_EVENTS;
	} else if (words == 2 && strcmp(command[0], "show") == 0) {
		for (i = 0; i < sizeof(views) / sizeof(views[0]); ++i)
			if (strcmp(command[1], views[i].name) == 0) {
				op = views[i].op;
				break;
			}
	}
	if (!op) {
		fprintf(stderr,
			"oectl: unsupported command; use 'oectl help'\n");
		return 2;
	}
	scoped = op >= EIGRP_LINUX_MGNT_NEIGHBORS;
	if (!scoped && (opts.asn_set || opts.afi_set || opts.vrf_set)) {
		fprintf(stderr,
			"oectl: scope options are not valid for this view\n");
		return 2;
	}
	if (scoped && select_scope(&opts, &scope) != 0)
		return 1;
	result = query(&opts, op, scoped ? &scope : NULL, NULL, NULL);
	if (result != 0)
		return 1;
	if (op == EIGRP_LINUX_MGNT_CLEAR_EVENTS && !opts.json)
		puts("Events cleared.");
	return 0;
}

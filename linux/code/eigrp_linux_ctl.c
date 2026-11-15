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
#include "eigrp_integration_version.h"
#include "eigrp_linux_mgnt.h"
#include "eigrp_linux_version.h"

#define EIGRP_LINUX_CTL_SOCKET "/run/openeigrpd/control.sock"
#define EIGRP_LINUX_CTL_TIMEOUT_MS 3000
#define EIGRP_LINUX_CTL_OFFSET_MAX 8192U
#define EIGRP_LINUX_CTL_FIELD_MAX 40
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
	uint16_t afi;
	uint16_t asn;
	uint32_t vrf;
	char vrf_name[64];
	uint32_t router_id;
	uint8_t target[EIGRP_LINUX_MGNT_TARGET_SIZE];
};

struct render {
	const char *view;
	const char *interface_name;
	bool detail;
	bool all_links;
	const char *prefix;
	bool json;
	unsigned printed;
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
		"       statistics, traffic, events, diagnostics, tech-support\n"
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
		op == EIGRP_LINUX_MGNT_IDENTITY
			? EIGRP_LINUX_MGNT_IDENTITY_SIZE
			: (scope ? EIGRP_LINUX_MGNT_SELECTOR_SIZE
					   + (op >= EIGRP_LINUX_MGNT_CLEAR_NEIGHBORS
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
	} else if (scope) {
		put16(request + 16, scope->afi);
		put16(request + 18, scope->asn);
		put32(request + 20, scope->vrf);
		put32(request + 28, offset);
		if (op >= EIGRP_LINUX_MGNT_CLEAR_NEIGHBORS)
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

/* Human-readable presentation uses the same daemon snapshots as --json.
 * Never reinterpret unknown fields as Cisco state that is not available. */
static void print_record(const struct record *rec, struct render *render)
{
	const char *kind = field(rec, "kind");
	const char *interface_name = field(rec, "interface");
	const char *name = field(rec, "name");
	uint64_t elapsed;
	const char *prefix;
	size_t i;

	if (render && render->prefix
	    && (strcmp(kind, "prefix") == 0 || strcmp(kind, "path") == 0)
	    && strcmp(render->prefix, field(rec, "prefix")) != 0)
		return;
	if (render && strcmp(kind, "path") == 0 && !render->all_links
	    && strcmp(field(rec, "successor"), "true") != 0
	    && strcmp(field(rec, "feasible"), "true") != 0)
		return;
	if (render && render->interface_name
	    && ((strcmp(kind, "neighbor") == 0
		 && !interface_match(render->interface_name, interface_name))
		|| (strcmp(kind, "interface") == 0
		    && !interface_match(render->interface_name, name))))
		return;
	if (render)
		render->printed++;
	if (strcmp(kind, "neighbor") == 0) {
		char uptime[24];
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
		printf("%-39s %-18s %4s %-9s %5s %5s %3s %7s\n",
		       field(rec, "address"), interface_name,
		       field(rec, "hold"), uptime, field(rec, "srtt"),
		       field(rec, "rto"), field(rec, "queue"),
		       field(rec, "sequence"));
		if (render && render->detail)
			printf("  State: %s, Retrans: %s, Prefixes: %s\n",
			       field(rec, "state"),
			       field(rec, "retransmissions"),
			       field(rec, "prefixes"));
		return;
	}
	if (strcmp(kind, "interface") == 0) {
		printf("%-19s %5s %9s %9s %11s\n", name, field(rec, "peers"),
		       field(rec, "output_queue"), field(rec, "reliable_queue"),
		       field(rec, "retransmissions"));
		if (render && render->detail)
			printf("  Hello interval %s sec, Hold time %s sec, MTU %s, Bandwidth %s Kbit, Delay %s\n"
			       "  Passive %s, Shutdown %s, Multicast %s\n",
			       field(rec, "hello"), field(rec, "hold"),
			       field(rec, "mtu"), field(rec, "bandwidth"),
			       field(rec, "delay"), field(rec, "passive"),
			       field(rec, "shutdown"), field(rec, "multicast"));
		return;
	}
	if (strcmp(kind, "prefix") == 0) {
		printf("%c  %s, %s successors, FD is %s\n",
		       strcmp(field(rec, "active"), "true") == 0 ? 'A' : 'P',
		       field(rec, "prefix"), field(rec, "successors"),
		       field(rec, "fd"));
		return;
	}
	if (strcmp(kind, "path") == 0) {
		prefix = field(rec, "next_hop");
		if (strcmp(field(rec, "connected"), "true") == 0)
			printf("   via Connected, %s\n", interface_name);
		else
			printf("   via %s (%s/%s), %s\n", prefix,
			       field(rec, "distance"), field(rec, "rd"),
			       interface_name);
		return;
	}
	if (strcmp(kind, "traffic") == 0) {
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
		return;
	}
	if (strcmp(kind, "protocol") == 0) {
		uint32_t id =
			(uint32_t)strtoul(field(rec, "router_id"), NULL, 10);
		struct in_addr address = {.s_addr = htonl(id)};
		char router_id[INET_ADDRSTRLEN];

		(void)inet_ntop(AF_INET, &address, router_id,
				sizeof(router_id));
		printf("EIGRP-%s for process %s, AS(%s), VRF %s\n",
		       strcmp(field(rec, "afi"), "6") == 0 ? "IPv6" : "IPv4",
		       field(rec, "instance"), field(rec, "asn"),
		       field(rec, "vrf"));
		printf("  Runtime: %s, Shutdown: %s, Router ID: %s\n",
		       field(rec, "runtime"), field(rec, "shutdown"),
		       router_id);
		return;
	}
	if (strcmp(kind, "capability") == 0) {
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
		return;
	}
	if (strcmp(kind, "event_summary") == 0) {
		printf("Event log: %s entries (capacity %s, written %s, overwritten %s)\n",
		       field(rec, "count"), field(rec, "capacity"),
		       field(rec, "written"), field(rec, "overwritten"));
		return;
	}
	if (strcmp(kind, "event") == 0) {
		uint64_t timestamp =
			strtoull(field(rec, "timestamp"), NULL, 10);
		time_t seconds = (time_t)(timestamp / 1000);
		struct tm utc;
		char when[40];

		if (gmtime_r(&seconds, &utc) != NULL
		    && strftime(when, sizeof(when), "%Y-%m-%d %H:%M:%S",
				&utc)) {
			size_t n = strlen(when);
			(void)snprintf(when + n, sizeof(when) - n, ".%03uZ",
				       (unsigned)(timestamp % 1000));
		} else
			(void)snprintf(when, sizeof(when), "%s",
				       field(rec, "timestamp"));
		printf("%5s %s  %s\n", field(rec, "number"), when,
		       field(rec, "message"));
		return;
	}
	if (strcmp(kind, "timer") == 0) {
		static const char *const timers[] = {"Hello", "Neighbor hold",
						     "Limit restart",
						     "Limit reset"};
		uint32_t type = (uint32_t)strtoul(field(rec, "type"), NULL, 10);

		printf("  Timer %s, Interface %s, Neighbor %s, expires in %s sec\n",
		       type < sizeof(timers) / sizeof(timers[0])
			       ? timers[type]
			       : field(rec, "type"),
		       field(rec, "interface"),
		       *field(rec, "neighbor") ? field(rec, "neighbor") : "-",
		       field(rec, "remaining"));
		return;
	}
	if (strcmp(kind, "debug") == 0) {
		printf("  Debug AFI %s AS %s VRF %s category %s\n",
		       field(rec, "afi"), field(rec, "asn"), field(rec, "vrf"),
		       field(rec, "category"));
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

static void print_heading(const struct scope *scope, const char *view)
{
	const char *vrf = *scope->vrf_name ? scope->vrf_name : "default";
	const char *afi = scope->afi == 6 ? "IPv6" : "IPv4";

	if (strcmp(view, "neighbors") == 0) {
		printf("EIGRP-%s VR(%s) Address-family Neighbors for AS(%u)\n",
		       afi, vrf, scope->asn);
		puts("Address                                 Interface          Hold Uptime    SRTT   RTO   Q     Seq");
		puts("                                                                (sec)          (ms)  (ms) Cnt    Num");
		puts("  (Cisco neighbor handle index H is not exposed by IPC)");
	} else if (strcmp(view, "interfaces") == 0) {
		printf("EIGRP-%s VR(%s) Address-family Interfaces for AS(%u)\n",
		       afi, vrf, scope->asn);
		puts("Interface           Peers  Output Q  Reliable Q  Retransmits");
		puts("  (Cisco Mean SRTT / Pacing / Flow Timer: not exposed by IPC)");
	} else if (strcmp(view, "topology") == 0) {
		struct in_addr address = {.s_addr = htonl(scope->router_id)};
		char id[INET_ADDRSTRLEN];
		(void)inet_ntop(AF_INET, &address, id, sizeof(id));
		printf("EIGRP-%s VR(%s) Topology Table for AS(%u)/ID(%s)\n",
		       afi, vrf, scope->asn, id);
		puts("Codes: P - Passive, A - Active, U - Update, Q - Query, R - Reply,");
		puts("       r - Reply status, s - sia Status");
		puts("  (U/Q/R/r/s per-prefix flags are not exposed by the current snapshot)");
	} else if (strcmp(view, "statistics") == 0
		   || strcmp(view, "traffic") == 0) {
		printf("EIGRP-%s VR(%s) Address-family Traffic Statistics for AS(%u)\n",
		       afi, vrf, scope->asn);
		puts("  (Cisco process IDs and queue depths: not exposed by IPC)");
	} else if (strcmp(view, "events") == 0) {
		printf("Event information for AS %u (%s, VRF %s):\n",
		       scope->asn, afi, vrf);
	} else if (strcmp(view, "diagnostics") == 0) {
		printf("EIGRP-%s VR(%s) Diagnostics for AS(%u)\n", afi, vrf,
		       scope->asn);
	} else {
		printf("EIGRP-%s VR(%s) for AS(%u)\n", afi, vrf, scope->asn);
	}
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
		if (callback && callback(rec, arg) != 0) {
			rc = -1;
			break;
		}
		if (json)
			(void)fwrite(p, 1, (size_t)(newline + 1 - p), stdout);
		else if (!callback)
			print_record(rec, render);
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
		if (more
		    && (!count
			|| offset + count > EIGRP_LINUX_CTL_OFFSET_MAX)) {
			fprintf(stderr,
				"eigrp: response exceeds management pagination limit\n");
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

static int query(const struct options *opts, uint16_t op,
		 const struct scope *scope, record_cb callback, void *arg)
{
	return query_internal(opts, op, scope, callback, arg, NULL);
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
	if (opts->vrf_name && strcmp(vrf, opts->vrf_name) != 0)
		return 0;
	if (strcmp(vrf, "default") != 0) {
		list->skipped = true;
		return 0;
	}
	if (list->count == sizeof(list->items) / sizeof(list->items[0])) {
		fprintf(stderr,
			"eigrp: too many protocol instances to select\n");
		return -1;
	}
	list->items[list->count] =
		(struct scope){.afi = (uint16_t)afi, .asn = (uint16_t)asn};
	(void)snprintf(list->items[list->count].vrf_name,
		       sizeof(list->items[list->count].vrf_name), "%s", vrf);
	(void)number(field(rec, "router_id"), UINT32_MAX,
		     &list->items[list->count].router_id);
	list->count++;
	return 0;
}

static int select_scopes(const struct options *opts, struct scopes *list)
{
	list->opts = opts;
	if (opts->vrf_set && opts->vrf != 0) {
		if (!opts->asn_set || !opts->afi_set) {
			fprintf(stderr,
				"eigrp: nondefault VRF ID requires explicit AFI and ASN\n");
			return -1;
		}
		list->items[0] = (struct scope){
			.afi = opts->afi, .asn = opts->asn, .vrf = opts->vrf};
		(void)snprintf(list->items[0].vrf_name,
			       sizeof(list->items[0].vrf_name), "ID %u",
			       opts->vrf);
		list->count = 1;
		return 0;
	}
	if (query(opts, EIGRP_LINUX_MGNT_PROTOCOLS, NULL, collect_scope, list)
	    != 0)
		return -1;
	if (!list->count) {
		fprintf(stderr, "eigrp: no matching running EIGRP instance\n");
		return -1;
	}
	if (list->skipped)
		fprintf(stderr,
			"eigrp: nondefault VRFs omitted (native Linux VRF resolution is unavailable)\n");
	return 0;
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
	if (select_scopes(opts, &list) != 0)
		return 1;
	for (i = 0; i < list.count; ++i) {
		struct scope *scope = &list.items[i];
		if (opts->json)
			printf("{\"kind\":\"scope\",\"asn\":%u,\"afi\":%u,"
			       "\"vrf_id\":%u}\n",
			       scope->asn, scope->afi, scope->vrf);
		for (j = 2; j < sizeof(views) / sizeof(views[0]); ++j) {
			if (strcmp(views[j].name, "traffic") == 0
			    || (!opts->json
				&& strcmp(views[j].name, "routes") == 0)
			    || views[j].op == 0)
				continue;
			if (!opts->json)
				printf("\n=== %s (AS %u, IPv%u, VRF ID %u) ===\n",
				       views[j].name, scope->asn, scope->afi,
				       scope->vrf);
			struct render render = {.view = views[j].name};
			if (!opts->json)
				print_heading(scope, views[j].name);
			if (query_internal(opts, views[j].op, scope, NULL, NULL,
					   opts->json ? NULL : &render)
			    != 0)
				errors++;
		}
	}
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
	bool soft = false;
	int errors = 0;
	size_t attempted = 0;

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
	if (cursor < used) {
		extra = keyword(words[cursor], extras, 3);
		if (action == 0 && extra == 0) {
			detail = true;
			cursor++;
		} else if (action == 0 && op == EIGRP_LINUX_MGNT_TOPOLOGY
			   && extra == 2) {
			all_links = true;
			cursor++;
		} else if (action == 1 && op == EIGRP_LINUX_MGNT_CLEAR_NEIGHBORS
			   && extra == 1) {
			soft = true;
			cursor++;
		} else if (extra == -2) {
			fprintf(stderr, "eigrp: ambiguous view modifier %s\n",
				words[cursor]);
			return 2;
		}
	}
	if (cursor < used) {
		if (action == 0) {
			if ((op != EIGRP_LINUX_MGNT_NEIGHBORS
			     && op != EIGRP_LINUX_MGNT_INTERFACES
			     && op != EIGRP_LINUX_MGNT_TOPOLOGY)
			    || cursor + 1 != used) {
				fprintf(stderr,
					"eigrp: unsupported command arguments\n");
				return 2;
			}
			interface_name = words[cursor++];
		} else {
			if (op == EIGRP_LINUX_MGNT_CLEAR_EVENTS) {
				fprintf(stderr,
					"eigrp: clear events takes no target\n");
				return 2;
			}
			target = words[cursor++];
			if (cursor < used
			    && op == EIGRP_LINUX_MGNT_CLEAR_NEIGHBORS
			    && keyword(words[cursor], extras, 3) == 1) {
				soft = true;
				cursor++;
			}
			if (cursor != used) {
				fprintf(stderr,
					"eigrp: unsupported clear arguments\n");
				return 2;
			}
		}
	}
	if (detail && op != EIGRP_LINUX_MGNT_NEIGHBORS
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
	if (select_scopes(&opts, &list) != 0)
		return 1;
	for (i = 0; i < list.count; ++i) {
		int target_result;
		struct render render = {
			.view = action == 0 ? views[view].name : "events",
			.interface_name = op == EIGRP_LINUX_MGNT_TOPOLOGY
						  ? NULL
						  : interface_name,
			.prefix = op == EIGRP_LINUX_MGNT_TOPOLOGY
					  ? interface_name
					  : NULL,
			.detail = detail,
			.all_links = all_links};
		if (action == 1 && op >= EIGRP_LINUX_MGNT_CLEAR_NEIGHBORS) {
			target_result =
				clear_target(&list.items[i], op, target, soft);
			if (target_result == 1)
				continue;
			if (target_result < 0) {
				fprintf(stderr,
					"eigrp: invalid clear target %s\n",
					target);
				return 2;
			}
		}
		if (action == 1)
			attempted++;
		if (action == 0 && !opts.json) {
			if (i)
				putchar('\n');
			print_heading(&list.items[i], render.view);
		}
		if (query_internal(&opts, op, &list.items[i], NULL, NULL,
				   opts.json ? NULL : &render)
		    != 0) {
			errors++;
			continue;
		}
		if (action == 0 && interface_name && !opts.json
		    && !render.printed)
			printf("%% %s not found: %s\n",
			       op == EIGRP_LINUX_MGNT_TOPOLOGY ? "Network"
							       : "Interface",
			       interface_name);
		if (action == 1 && !opts.json)
			printf("%s cleared for %s AS %u VRF %s.\n",
			       op == EIGRP_LINUX_MGNT_CLEAR_EVENTS ? "Events"
			       : op == EIGRP_LINUX_MGNT_CLEAR_NEIGHBORS
				       ? "Neighbors"
				       : "Topology",
			       list.items[i].afi == 4 ? "IPv4" : "IPv6",
			       list.items[i].asn, list.items[i].vrf_name);
	}
	if (action == 1 && !attempted) {
		fprintf(stderr,
			"eigrp: no selected address family matches clear target\n");
		return 1;
	}
	return errors ? 1 : 0;
conflicting_scope:
	fprintf(stderr, "eigrp: conflicting address-family/AS/VRF selectors\n");
	return 2;
}

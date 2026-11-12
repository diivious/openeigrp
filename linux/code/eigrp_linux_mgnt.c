// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Bounded local Unix-domain IPC and portable management-state presentation.
 * No EIGRP parsing or private protocol-state traversal belongs here.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include "eigrp_linux_mgnt.h"

#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <poll.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/stat.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

#include "eigrp_cli.h"
#include "eigrp_mgnt.h"

#define MGNT_FRAME_MAX                                                         \
	(EIGRP_LINUX_MGNT_HEADER_SIZE + EIGRP_LINUX_MGNT_RESPONSE_MAX)
#define MGNT_IDLE_MSEC 5000U
#define MGNT_STOP_MARKER 0x01U

struct mgnt_selector {
	uint16_t afi;
	uint16_t asn;
	uint32_t vrf;
	uint16_t topology;
	uint16_t flags;
	uint32_t offset;
};

struct mgnt_response {
	char data[EIGRP_LINUX_MGNT_RESPONSE_MAX];
	size_t used;
	uint32_t index;
	uint32_t offset;
	uint16_t count;
	bool more;
	bool stopped;
	bool too_large;
	eigrp_prefix_t prefix;
};

struct mgnt_request {
	uint16_t op;
	struct mgnt_selector selector;
	struct mgnt_response response;
	eigrp_instance_context_t context;
	eigrp_result_t result;
};

struct mgnt_client {
	int fd;
	uid_t uid;
	uint8_t input[EIGRP_LINUX_MGNT_HEADER_SIZE
		      + EIGRP_LINUX_MGNT_SELECTOR_SIZE];
	size_t have;
	size_t need;
	uint8_t output[MGNT_FRAME_MAX];
	size_t out_size;
	size_t sent;
	uint64_t activity;
	bool closing;
};

struct mgnt_service {
	int listener;
	int wake[2];
	pthread_t thread;
	char path[sizeof(((struct sockaddr_un *)0)->sun_path)];
	dev_t device;
	ino_t inode;
	uid_t admin_uid;
	eigrp_linux_mgnt_executor_t execute;
	void *host_arg;
	struct mgnt_client clients[EIGRP_LINUX_MGNT_CLIENT_MAX];
	bool running;
};

static pthread_mutex_t mgnt_lock = PTHREAD_MUTEX_INITIALIZER;
static struct mgnt_service mgnt = {.listener = -1, .wake = {-1, -1}};

static uint16_t mgnt_get16(const uint8_t *p)
{
	return ((uint16_t)p[0] << 8) | p[1];
}

static uint32_t mgnt_get32(const uint8_t *p)
{
	return ((uint32_t)mgnt_get16(p) << 16) | mgnt_get16(p + 2);
}

static void mgnt_put16(uint8_t *p, uint16_t n)
{
	p[0] = (uint8_t)(n >> 8);
	p[1] = (uint8_t)n;
}

static void mgnt_put32(uint8_t *p, uint32_t n)
{
	mgnt_put16(p, (uint16_t)(n >> 16));
	mgnt_put16(p + 2, (uint16_t)n);
}

static uint64_t mgnt_now(void)
{
	struct timespec ts;

	if (clock_gettime(CLOCK_MONOTONIC, &ts) != 0)
		return 0;
	return (uint64_t)ts.tv_sec * 1000U + (uint64_t)ts.tv_nsec / 1000000U;
}

/* Every record is constructed privately, then appended atomically. Strings
 * are JSON escaped and clipped; a client cannot inject extra JSON records. */
static void mgnt_json_string(char *dst, size_t length, const char *src)
{
	static const char hex[] = "0123456789abcdef";
	size_t i = 0;

	if (!length)
		return;
	if (!src)
		src = "";
	while (*src && i + 7 < length) {
		unsigned char c = (unsigned char)*src++;
		if (c == '"' || c == '\\') {
			dst[i++] = '\\';
			dst[i++] = (char)c;
		} else if (c < 0x20 || c >= 0x80) {
			dst[i++] = '\\';
			dst[i++] = 'u';
			dst[i++] = '0';
			dst[i++] = '0';
			dst[i++] = hex[c >> 4];
			dst[i++] = hex[c & 0xf];
		} else {
			dst[i++] = (char)c;
		}
	}
	dst[i] = '\0';
}

static void mgnt_address(const eigrp_address_t *address, char *buf, size_t size)
{
	int family = address->afi == EIGRP_AFI_IPV4   ? AF_INET
		     : address->afi == EIGRP_AFI_IPV6 ? AF_INET6
						      : 0;

	if (!family || !inet_ntop(family, address->bytes, buf, size))
		(void)snprintf(buf, size, "unknown");
}

static void mgnt_prefix(const eigrp_prefix_t *prefix, char *buf, size_t size)
{
	char address[INET6_ADDRSTRLEN];

	mgnt_address(&prefix->address, address, sizeof(address));
	(void)snprintf(buf, size, "%s/%u", address,
		       (unsigned)prefix->prefix_length);
}

/* Stop the public walker on the first record beyond the page. A large offset
 * is rejected at the IPC boundary to bound scanning while holding host-side
 * protocol serialization. The portable iterator exposes no resume cursor. */
#define MGNT_OFFSET_MAX 8192U
static eigrp_result_t mgnt_record(struct mgnt_response *r, const char *line)
{
	size_t size;

	if (r->index++ < r->offset)
		return EIGRP_RESULT_SUCCESS;
	if (r->count >= EIGRP_LINUX_MGNT_PAGE_SIZE) {
		r->more = true;
		r->stopped = true;
		return EIGRP_RESULT_CONFLICT;
	}
	size = strlen(line);
	if (size >= sizeof(r->data) - r->used) {
		r->too_large = true;
		r->stopped = true;
		return EIGRP_RESULT_INTERNAL_FAILURE;
	}
	memcpy(r->data + r->used, line, size);
	r->used += size;
	r->count++;
	return EIGRP_RESULT_SUCCESS;
}

static eigrp_result_t mgnt_protocol(const eigrp_status_protocol_state_t *s,
				    void *arg)
{
	struct mgnt_response *r = arg;
	char line[640], name[144], vrf[144];

	mgnt_json_string(name, sizeof(name), s->instance_name);
	mgnt_json_string(vrf, sizeof(vrf), s->vrf_name);
	(void)snprintf(line, sizeof(line),
		       "{\"kind\":\"protocol\",\"instance\":\"%s\",\"afi\":%u,"
		       "\"vrf\":\"%s\",\"asn\":%u,\"shutdown\":%s,"
		       "\"runtime\":%s,\"router_id\":%u}\n",
		       name, (unsigned)s->afi, vrf, s->asn,
		       s->shutdown ? "true" : "false",
		       s->runtime_present ? "true" : "false", s->router_id);
	return mgnt_record(r, line);
}

static eigrp_result_t mgnt_interface(const eigrp_intf_state_t *s, void *arg)
{
	struct mgnt_response *r = arg;
	char line[1100], name[160];

	mgnt_json_string(name, sizeof(name), s->interface_name);
	(void)snprintf(
		line, sizeof(line),
		"{\"kind\":\"interface\",\"name\":\"%s\",\"runtime\":%s,"
		"\"configured\":%s,\"passive\":%s,\"shutdown\":%s,"
		"\"multicast\":%s,\"bandwidth\":%u,\"delay\":%u,"
		"\"mtu\":%u,\"hello\":%u,\"hold\":%u,\"peers\":%u,"
		"\"output_queue\":%lu,\"reliable_queue\":%lu,"
		"\"retransmissions\":%llu}\n",
		name, s->runtime_present ? "true" : "false",
		s->config_present ? "true" : "false",
		s->passive ? "true" : "false", s->shutdown ? "true" : "false",
		s->multicast_enabled ? "true" : "false", s->bandwidth, s->delay,
		s->mtu, s->hello_interval, s->hold_time, s->peer_count,
		s->output_queue_count, s->reliable_queue_count,
		(unsigned long long)s->retransmissions_sent);
	return mgnt_record(r, line);
}

static eigrp_result_t mgnt_neighbor(const eigrp_nbr_state_t *s, void *arg)
{
	struct mgnt_response *r = arg;
	char line[900], addr[INET6_ADDRSTRLEN], name[160], state[120];

	mgnt_address(&s->address, addr, sizeof(addr));
	mgnt_json_string(name, sizeof(name), s->interface_name);
	mgnt_json_string(state, sizeof(state), s->state_name);
	(void)snprintf(
		line, sizeof(line),
		"{\"kind\":\"neighbor\",\"address\":\"%s\",\"interface\":\"%s\","
		"\"state\":\"%s\",\"uptime\":%llu,\"hold\":%u,"
		"\"queue\":%lu,\"sequence\":%u,\"prefixes\":%u,"
		"\"srtt\":%u,\"rto\":%u,\"retransmissions\":%llu}\n",
		addr, name, state, (unsigned long long)s->uptime_seconds,
		s->hold_time, s->reliable_queue_count, s->sequence_number,
		s->prefix_count, s->srtt_msec, s->rto_msec,
		(unsigned long long)s->retransmit_count);
	return mgnt_record(r, line);
}

static eigrp_result_t
mgnt_topology_prefix(const eigrp_topology_prefix_state_t *s, void *arg)
{
	struct mgnt_response *r = arg;
	char line[400], pfx[INET6_ADDRSTRLEN + 12];

	r->prefix = s->destination;
	mgnt_prefix(&s->destination, pfx, sizeof(pfx));
	(void)snprintf(line, sizeof(line),
		       "{\"kind\":\"prefix\",\"prefix\":\"%s\",\"active\":%s,"
		       "\"fd\":%llu,\"successors\":%u,\"serial\":%llu}\n",
		       pfx, s->active ? "true" : "false",
		       (unsigned long long)s->feasible_distance,
		       s->successor_count,
		       (unsigned long long)s->serial_number);
	return mgnt_record(r, line);
}

static eigrp_result_t mgnt_topology_route(const eigrp_topology_route_state_t *s,
					  void *arg)
{
	struct mgnt_response *r = arg;
	char line[640], pfx[INET6_ADDRSTRLEN + 12];
	char addr[INET6_ADDRSTRLEN], name[160];

	mgnt_prefix(&r->prefix, pfx, sizeof(pfx));
	mgnt_address(&s->next_hop, addr, sizeof(addr));
	mgnt_json_string(name, sizeof(name), s->interface_name);
	(void)snprintf(
		line, sizeof(line),
		"{\"kind\":\"path\",\"prefix\":\"%s\",\"next_hop\":\"%s\","
		"\"interface\":\"%s\",\"connected\":%s,\"successor\":%s,"
		"\"feasible\":%s,\"distance\":%llu,\"rd\":%llu}\n",
		pfx, addr, name, s->connected ? "true" : "false",
		s->successor ? "true" : "false",
		s->feasible_successor ? "true" : "false",
		(unsigned long long)s->distance,
		(unsigned long long)s->reported_distance);
	return mgnt_record(r, line);
}

static eigrp_result_t mgnt_event(uint32_t number, const eigrp_eventlog_msg_t *s,
				 const char *format, void *arg)
{
	struct mgnt_response *r = arg;
	char line[1100], msg[320], escaped[680];

	(void)format;
	if (eigrp_eventlog_msg_format(s, msg, sizeof(msg))
	    != EIGRP_RESULT_SUCCESS)
		return EIGRP_RESULT_INTERNAL_FAILURE;
	mgnt_json_string(escaped, sizeof(escaped), msg);
	(void)snprintf(line, sizeof(line),
		       "{\"kind\":\"event\",\"number\":%u,\"timestamp\":%llu,"
		       "\"opcode\":%u,\"message\":\"%s\"}\n",
		       number, (unsigned long long)s->timestamp, s->opcode,
		       escaped);
	return mgnt_record(r, line);
}

static eigrp_result_t mgnt_timer(const eigrp_timer_state_t *s, void *arg)
{
	struct mgnt_response *r = arg;
	char line[420], name[160], addr[INET6_ADDRSTRLEN];

	mgnt_json_string(name, sizeof(name), s->interface_name);
	mgnt_address(&s->neighbor_address, addr, sizeof(addr));
	(void)snprintf(line, sizeof(line),
		       "{\"kind\":\"timer\",\"type\":%u,\"interface\":\"%s\","
		       "\"neighbor\":\"%s\",\"remaining\":%u}\n",
		       (unsigned)s->type, name, s->neighbor_present ? addr : "",
		       s->expiration_seconds);
	return mgnt_record(r, line);
}

static eigrp_result_t mgnt_capabilities(struct mgnt_response *r)
{
	eigrp_status_capability_state_t s;
	char line[440], release[160];
	eigrp_result_t result = eigrp_status_capability_state_read(&s);

	if (result != EIGRP_RESULT_SUCCESS)
		return result;
	mgnt_json_string(release, sizeof(release), s.release);
	(void)snprintf(line, sizeof(line),
		       "{\"kind\":\"capability\",\"release\":\"%s\","
		       "\"ipv4\":%s,\"ipv6\":%s,\"tlv1\":%s,\"tlv2\":%s,"
		       "\"wide_metrics\":%s,\"bfd\":%s,\"snmp\":%s}\n",
		       release, s.ipv4 ? "true" : "false",
		       s.ipv6 ? "true" : "false", s.tlv1 ? "true" : "false",
		       s.tlv2 ? "true" : "false",
		       s.wide_metrics ? "true" : "false",
		       s.bfd ? "true" : "false", s.snmp ? "true" : "false");
	return mgnt_record(r, line);
}

static eigrp_result_t mgnt_traffic(const eigrp_instance_context_t *context,
				   struct mgnt_response *r)
{
	eigrp_statistics_traffic_state_t s;
	char line[1100];
	eigrp_result_t result = eigrp_stat_traffic_read(context, &s);

	if (result != EIGRP_RESULT_SUCCESS)
		return result;
	(void)snprintf(line, sizeof(line),
		       "{\"kind\":\"traffic\",\"sent_valid\":%u,"
		       "\"received_valid\":%u,\"sent_ack\":%llu,"
		       "\"sent_hello\":%llu,\"sent_query\":%llu,"
		       "\"sent_reply\":%llu,\"sent_update\":%llu,"
		       "\"sent_sia_query\":%llu,\"sent_sia_reply\":%llu,"
		       "\"received_ack\":%llu,\"received_hello\":%llu,"
		       "\"received_query\":%llu,\"received_reply\":%llu,"
		       "\"received_update\":%llu,\"received_sia_query\":%llu,"
		       "\"received_sia_reply\":%llu}\n",
		       s.sent_valid, s.received_valid,
		       (unsigned long long)s.sent_ack,
		       (unsigned long long)s.sent_hello,
		       (unsigned long long)s.sent_query,
		       (unsigned long long)s.sent_reply,
		       (unsigned long long)s.sent_update,
		       (unsigned long long)s.sent_sia_query,
		       (unsigned long long)s.sent_sia_reply,
		       (unsigned long long)s.received_ack,
		       (unsigned long long)s.received_hello,
		       (unsigned long long)s.received_query,
		       (unsigned long long)s.received_reply,
		       (unsigned long long)s.received_update,
		       (unsigned long long)s.received_sia_query,
		       (unsigned long long)s.received_sia_reply);
	return mgnt_record(r, line);
}

/* The portable status callback exposes a config identity, not a VRF ID.
 * Resolve its public context, then match the selected runtime VRF ID. */
struct mgnt_lookup {
	struct mgnt_selector selector;
	eigrp_instance_context_t context;
	bool found;
};

static eigrp_result_t
mgnt_lookup_protocol(const eigrp_status_protocol_state_t *s, void *arg)
{
	struct mgnt_lookup *lookup = arg;
	eigrp_instance_context_t context = {0};

	if (s->afi != lookup->selector.afi || s->asn != lookup->selector.asn)
		return EIGRP_RESULT_SUCCESS;
	if (eigrp_af_config_context_read(s->instance_name, s->afi, s->vrf_name,
					 s->asn, &context)
	    != EIGRP_RESULT_SUCCESS)
		return EIGRP_RESULT_SUCCESS;
	if (!context.runtime
	    || eigrp_instance_vrf_id(context.runtime) != lookup->selector.vrf)
		return EIGRP_RESULT_SUCCESS;
	lookup->found = true;
	lookup->context = context;
	lookup->context.topology_id = lookup->selector.topology;
	return EIGRP_RESULT_CONFLICT;
}

static eigrp_result_t mgnt_query_scoped(void *arg)
{
	struct mgnt_request *req = arg;
	struct mgnt_response *r = &req->response;
	eigrp_instance_context_t *context = &req->context;
	eigrp_eventlog_state_t event_state;
	eigrp_result_t result;
	char line[360];
	size_t i;

	switch (req->op) {
	case EIGRP_LINUX_MGNT_NEIGHBORS:
		result = eigrp_nbr_state_iterate(context->config,
						 context->runtime, NULL, false,
						 mgnt_neighbor, r);
		break;
	case EIGRP_LINUX_MGNT_INTERFACES:
		result = eigrp_intf_state_iterate(context->config,
						  context->runtime, NULL,
						  mgnt_interface, r);
		break;
	case EIGRP_LINUX_MGNT_TOPOLOGY:
	case EIGRP_LINUX_MGNT_ROUTES:
		result = eigrp_topology_state_iterate(
			context->config, context->runtime, NULL,
			req->op == EIGRP_LINUX_MGNT_TOPOLOGY,
			mgnt_topology_prefix, mgnt_topology_route, r);
		break;
	case EIGRP_LINUX_MGNT_TRAFFIC:
		result = mgnt_traffic(context, r);
		break;
	case EIGRP_LINUX_MGNT_EVENTS:
		result = eigrp_eventlog_state_read(context, &event_state);
		if (result != EIGRP_RESULT_SUCCESS)
			break;
		(void)snprintf(
			line, sizeof(line),
			"{\"kind\":\"event_summary\",\"capacity\":%u,"
			"\"count\":%u,\"written\":%llu,\"overwritten\":%llu}\n",
			event_state.capacity, event_state.count,
			(unsigned long long)event_state.written,
			(unsigned long long)event_state.overwritten);
		result = mgnt_record(r, line);
		if (result == EIGRP_RESULT_SUCCESS)
			result = eigrp_eventlog_msg_iterate(context, mgnt_event,
							    r);
		break;
	case EIGRP_LINUX_MGNT_DIAGNOSTICS:
		result = mgnt_capabilities(r);
		if (result != EIGRP_RESULT_SUCCESS)
			break;
		for (i = 0; i < eigrp_debug_af_state_count(); ++i) {
			eigrp_debug_af_state_t state;
			char vrf[144];

			if (!eigrp_debug_af_state_read(EIGRP_DEBUG_SCOPE_CONFIG,
						       i, &state))
				continue;
			mgnt_json_string(vrf, sizeof(vrf), state.vrf_name);
			(void)snprintf(
				line, sizeof(line),
				"{\"kind\":\"debug\",\"afi\":%u,\"asn\":%u,"
				"\"vrf\":\"%s\",\"category\":%u,\"all_vrfs\":%s}\n",
				(unsigned)state.afi, state.asn, vrf,
				(unsigned)state.category,
				state.all_vrfs ? "true" : "false");
			result = mgnt_record(r, line);
			if (result != EIGRP_RESULT_SUCCESS)
				break;
		}
		if (result == EIGRP_RESULT_SUCCESS)
			result = eigrp_timer_state_iterate(context, mgnt_timer,
							   r);
		break;
	case EIGRP_LINUX_MGNT_CLEAR_EVENTS:
		result = eigrp_eventlog_clear(context);
		break;
	default:
		return EIGRP_RESULT_UNSUPPORTED;
	}
	if (r->stopped)
		return r->too_large ? EIGRP_RESULT_INTERNAL_FAILURE
				    : EIGRP_RESULT_SUCCESS;
	return result == EIGRP_RESULT_NOT_FOUND ? EIGRP_RESULT_SUCCESS : result;
}

static eigrp_result_t mgnt_query(void *arg)
{
	struct mgnt_request *req = arg;
	struct mgnt_response *r = &req->response;
	struct mgnt_lookup lookup = {.selector = req->selector};
	eigrp_result_t result;

	if (req->op == EIGRP_LINUX_MGNT_CAPABILITIES)
		return mgnt_capabilities(r);
	if (req->op == EIGRP_LINUX_MGNT_PROTOCOLS) {
		result = eigrp_status_protocol_iterate(mgnt_protocol, r);
		return r->stopped
			       ? (r->too_large ? EIGRP_RESULT_INTERNAL_FAILURE
					       : EIGRP_RESULT_SUCCESS)
			       : result;
	}

	result = eigrp_status_protocol_iterate(mgnt_lookup_protocol, &lookup);
	if (!lookup.found)
		return result == EIGRP_RESULT_SUCCESS
				       || result == EIGRP_RESULT_NOT_FOUND
			       ? EIGRP_RESULT_NOT_FOUND
			       : result;

	req->context = lookup.context;
	/* Configuration identity is protected by the daemon executor. Runtime
	 * topology and neighbor descriptors belong to the AF packet thread. */
	return eigrp_mgnt_runtime_execute(req->context.runtime,
					  mgnt_query_scoped, req);
}

static uint16_t mgnt_result(eigrp_result_t result)
{
	switch (result) {
	case EIGRP_RESULT_SUCCESS:
		return EIGRP_LINUX_MGNT_OK;
	case EIGRP_RESULT_NOT_FOUND:
		return EIGRP_LINUX_MGNT_NOT_FOUND;
	case EIGRP_RESULT_NOT_IMPLEMENTED:
	case EIGRP_RESULT_UNSUPPORTED:
		return EIGRP_LINUX_MGNT_UNSUPPORTED;
	case EIGRP_RESULT_INVALID_ARGUMENT:
		return EIGRP_LINUX_MGNT_INVALID;
	case EIGRP_RESULT_CONFLICT:
		return EIGRP_LINUX_MGNT_BUSY;
	default:
		return EIGRP_LINUX_MGNT_INTERNAL;
	}
}

static void mgnt_reply(struct mgnt_client *client, uint16_t op, uint16_t status,
		       bool more, const void *body, size_t size,
		       bool close_after)
{
	uint8_t *p = client->output;

	mgnt_put32(p, EIGRP_LINUX_MGNT_MAGIC);
	mgnt_put16(p + 4, EIGRP_LINUX_MGNT_VERSION);
	mgnt_put16(p + 6, op);
	mgnt_put16(p + 8, status);
	mgnt_put16(p + 10, more ? 1 : 0);
	mgnt_put32(p + 12, (uint32_t)size);
	if (size)
		memcpy(p + EIGRP_LINUX_MGNT_HEADER_SIZE, body, size);
	client->out_size = EIGRP_LINUX_MGNT_HEADER_SIZE + size;
	client->sent = 0;
	client->closing = close_after;
}

static void mgnt_request(struct mgnt_client *client)
{
	const uint8_t *p = client->input;
	uint16_t op = mgnt_get16(p + 6);
	uint16_t version = mgnt_get16(p + 4);
	uint32_t length = mgnt_get32(p + 12);
	bool scoped = op >= EIGRP_LINUX_MGNT_NEIGHBORS
		      && op != EIGRP_LINUX_MGNT_PROTOCOLS
		      && op != EIGRP_LINUX_MGNT_CAPABILITIES;
	struct mgnt_request *req;
	uint16_t status;
	eigrp_result_t result;

	if (mgnt_get32(p) != EIGRP_LINUX_MGNT_MAGIC || mgnt_get16(p + 8) != 0
	    || mgnt_get16(p + 10) != 0
	    || length != (scoped ? EIGRP_LINUX_MGNT_SELECTOR_SIZE : 0)
	    || op < EIGRP_LINUX_MGNT_CAPABILITIES
	    || op > EIGRP_LINUX_MGNT_CLEAR_EVENTS) {
		mgnt_reply(client, op, EIGRP_LINUX_MGNT_INVALID, false, NULL, 0,
			   true);
		return;
	}
	if (version != EIGRP_LINUX_MGNT_VERSION) {
		mgnt_reply(client, op, EIGRP_LINUX_MGNT_VERSION_UNSUPPORTED,
			   false, NULL, 0, true);
		return;
	}
	/* Socket DAC allows group observers; only the owner and root may
	 * invoke semantic mutations. Never trust a client-provided UID. */
	if (op == EIGRP_LINUX_MGNT_CLEAR_EVENTS && client->uid != 0
	    && client->uid != mgnt.admin_uid) {
		mgnt_reply(client, op, EIGRP_LINUX_MGNT_DENIED, false, NULL, 0,
			   false);
		return;
	}
	req = calloc(1, sizeof(*req));
	if (!req) {
		mgnt_reply(client, op, EIGRP_LINUX_MGNT_INTERNAL, false, NULL,
			   0, false);
		return;
	}
	req->op = op;
	if (scoped) {
		p += EIGRP_LINUX_MGNT_HEADER_SIZE;
		req->selector.afi = mgnt_get16(p);
		req->selector.asn = mgnt_get16(p + 2);
		req->selector.vrf = mgnt_get32(p + 4);
		req->selector.topology = mgnt_get16(p + 8);
		req->selector.flags = mgnt_get16(p + 10);
		req->selector.offset = mgnt_get32(p + 12);
		if ((req->selector.afi != EIGRP_AFI_IPV4
		     && req->selector.afi != EIGRP_AFI_IPV6)
		    || req->selector.asn == 0 || req->selector.flags != 0
		    || req->selector.topology != EIGRP_TOPOLOGY_ID_BASE
		    || req->selector.offset > MGNT_OFFSET_MAX
		    || (op == EIGRP_LINUX_MGNT_CLEAR_EVENTS
			&& req->selector.offset != 0)) {
			mgnt_reply(client, op, EIGRP_LINUX_MGNT_INVALID, false,
				   NULL, 0, false);
			free(req);
			return;
		}
	}
	req->response.offset = req->selector.offset;
	result = mgnt.execute(mgnt_query, req, mgnt.host_arg);
	status = mgnt_result(result);
	mgnt_reply(client, op, status,
		   status == EIGRP_LINUX_MGNT_OK && req->response.more,
		   status == EIGRP_LINUX_MGNT_OK ? req->response.data : NULL,
		   status == EIGRP_LINUX_MGNT_OK ? req->response.used : 0,
		   false);
	free(req);
}

static void mgnt_client_close(struct mgnt_client *client)
{
	if (client->fd >= 0)
		close(client->fd);
	client->fd = -1;
	client->have = 0;
	client->need = EIGRP_LINUX_MGNT_HEADER_SIZE;
	client->out_size = 0;
	client->sent = 0;
	client->closing = false;
}

static void mgnt_client_read(struct mgnt_client *client)
{
	ssize_t n;
	uint16_t op;
	uint32_t length;
	bool scoped;

	n = recv(client->fd, client->input + client->have,
		 client->need - client->have, 0);
	if (n == 0) {
		mgnt_client_close(client);
		return;
	}
	if (n < 0) {
		if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)
			mgnt_client_close(client);
		return;
	}
	client->activity = mgnt_now();
	client->have += (size_t)n;
	if (client->have < client->need)
		return;
	if (client->need == EIGRP_LINUX_MGNT_HEADER_SIZE) {
		op = mgnt_get16(client->input + 6);
		length = mgnt_get32(client->input + 12);
		scoped = op >= EIGRP_LINUX_MGNT_NEIGHBORS
			 && op != EIGRP_LINUX_MGNT_PROTOCOLS
			 && op != EIGRP_LINUX_MGNT_CAPABILITIES;
		if (length != (scoped ? EIGRP_LINUX_MGNT_SELECTOR_SIZE : 0)
		    || mgnt_get32(client->input) != EIGRP_LINUX_MGNT_MAGIC
		    || mgnt_get16(client->input + 8) != 0
		    || mgnt_get16(client->input + 10) != 0
		    || op < EIGRP_LINUX_MGNT_CAPABILITIES
		    || op > EIGRP_LINUX_MGNT_CLEAR_EVENTS) {
			mgnt_reply(client, op, EIGRP_LINUX_MGNT_INVALID, false,
				   NULL, 0, true);
			return;
		}
		if (!length) {
			mgnt_request(client);
			return;
		}
		client->need += length;
		return;
	}
	mgnt_request(client);
}

static void mgnt_client_write(struct mgnt_client *client)
{
	ssize_t n = send(client->fd, client->output + client->sent,
			 client->out_size - client->sent, MSG_NOSIGNAL);

	if (n < 0) {
		if (errno != EAGAIN && errno != EWOULDBLOCK && errno != EINTR)
			mgnt_client_close(client);
		return;
	}
	if (n == 0)
		return;
	client->activity = mgnt_now();
	client->sent += (size_t)n;
	if (client->sent != client->out_size)
		return;
	if (client->closing) {
		mgnt_client_close(client);
		return;
	}
	client->have = 0;
	client->need = EIGRP_LINUX_MGNT_HEADER_SIZE;
	client->out_size = 0;
	client->sent = 0;
}

static void mgnt_accept(void)
{
	struct ucred cred;
	socklen_t length;
	size_t i;
	unsigned int accepted;
	int fd;

	/* A connect flood cannot monopolize the management poll loop. */
	for (accepted = 0; accepted < EIGRP_LINUX_MGNT_CLIENT_MAX; ++accepted) {
		fd = accept4(mgnt.listener, NULL, NULL,
			     SOCK_NONBLOCK | SOCK_CLOEXEC);
		if (fd < 0) {
			if (errno == EINTR)
				continue;
			return;
		}
		length = sizeof(cred);
		if (getsockopt(fd, SOL_SOCKET, SO_PEERCRED, &cred, &length) != 0
		    || length != sizeof(cred)) {
			close(fd);
			continue;
		}
		for (i = 0; i < EIGRP_LINUX_MGNT_CLIENT_MAX; i++)
			if (mgnt.clients[i].fd < 0)
				break;
		if (i == EIGRP_LINUX_MGNT_CLIENT_MAX) {
			close(fd);
			continue;
		}
		mgnt.clients[i].fd = fd;
		mgnt.clients[i].uid = cred.uid;
		mgnt.clients[i].have = 0;
		mgnt.clients[i].need = EIGRP_LINUX_MGNT_HEADER_SIZE;
		mgnt.clients[i].activity = mgnt_now();
	}
}

static void *mgnt_serve(void *unused)
{
	struct pollfd pfds[2 + EIGRP_LINUX_MGNT_CLIENT_MAX];
	uint64_t now;
	size_t i;
	int result;

	(void)unused;
	for (;;) {
		memset(pfds, 0, sizeof(pfds));
		pfds[0].fd = mgnt.listener;
		pfds[0].events = POLLIN;
		pfds[1].fd = mgnt.wake[0];
		pfds[1].events = POLLIN;
		for (i = 0; i < EIGRP_LINUX_MGNT_CLIENT_MAX; ++i) {
			struct mgnt_client *client = &mgnt.clients[i];
			pfds[i + 2].fd = client->fd;
			pfds[i + 2].events =
				client->out_size ? POLLOUT : POLLIN;
		}
		result = poll(pfds, 2 + EIGRP_LINUX_MGNT_CLIENT_MAX, 100);
		if (result < 0 && errno == EINTR)
			continue;
		if (result < 0 || (pfds[1].revents & POLLIN))
			break;
		if (pfds[0].revents & POLLIN)
			mgnt_accept();
		now = mgnt_now();
		for (i = 0; i < EIGRP_LINUX_MGNT_CLIENT_MAX; ++i) {
			struct mgnt_client *client = &mgnt.clients[i];
			short events = pfds[i + 2].revents;

			if (client->fd < 0)
				continue;
			if (events & (POLLERR | POLLNVAL | POLLHUP)) {
				mgnt_client_close(client);
				continue;
			}
			if (events & POLLIN)
				mgnt_client_read(client);
			if (client->fd >= 0 && events & POLLOUT)
				mgnt_client_write(client);
			if (client->fd >= 0 && now >= client->activity
			    && now - client->activity > MGNT_IDLE_MSEC)
				mgnt_client_close(client);
		}
	}
	for (i = 0; i < EIGRP_LINUX_MGNT_CLIENT_MAX; ++i)
		mgnt_client_close(&mgnt.clients[i]);
	return NULL;
}

static bool mgnt_directory_safe(const char *path)
{
	char directory[sizeof(((struct sockaddr_un *)0)->sun_path)];
	const char *slash = strrchr(path, '/');
	struct stat stat;
	size_t length;

	if (!slash)
		return false;
	length = (size_t)(slash - path);
	if (length == 0)
		length = 1;
	if (length >= sizeof(directory))
		return false;
	memcpy(directory, path, length);
	directory[length] = '\0';
	/* The socket directory must not be replaceable by group/other users.
	 * Requiring it to be owned by root or the daemon also rejects foreign
	 * temporary directories that could intercept management traffic. */
	return lstat(directory, &stat) == 0 && S_ISDIR(stat.st_mode)
	       && (stat.st_uid == 0 || stat.st_uid == geteuid())
	       && !(stat.st_mode & (S_IWGRP | S_IWOTH));
}

static void mgnt_path_remove(void)
{
	struct stat stat;

	if (mgnt.path[0] && lstat(mgnt.path, &stat) == 0
	    && S_ISSOCK(stat.st_mode) && stat.st_dev == mgnt.device
	    && stat.st_ino == mgnt.inode)
		(void)unlink(mgnt.path);
}

int eigrp_linux_mgnt_start(const char *path, gid_t socket_gid,
			   mode_t socket_mode,
			   eigrp_linux_mgnt_executor_t execute, void *host_arg)
{
	struct sockaddr_un address = {.sun_family = AF_UNIX};
	struct stat stat;
	mode_t old_umask;
	char socket_handle[64];
	size_t i;
	int socket_fd = -1;
	int error;

	if (!path || path[0] != '/' || strlen(path) >= sizeof(address.sun_path)
	    || !execute || !mgnt_directory_safe(path)
	    || (socket_mode != 0600 && socket_mode != 0660)) {
		errno = EINVAL;
		return -1;
	}
	pthread_mutex_lock(&mgnt_lock);
	if (mgnt.running) {
		pthread_mutex_unlock(&mgnt_lock);
		errno = EALREADY;
		return -1;
	}
	if (lstat(path, &stat) == 0) {
		pthread_mutex_unlock(&mgnt_lock);
		errno = EADDRINUSE;
		return -1;
	}
	if (errno != ENOENT) {
		pthread_mutex_unlock(&mgnt_lock);
		return -1;
	}
	mgnt.listener =
		socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
	if (mgnt.listener < 0) {
		pthread_mutex_unlock(&mgnt_lock);
		return -1;
	}
	memcpy(address.sun_path, path, strlen(path) + 1);
	/* Create the socket private. umask is process-wide, so never relax it
	 * while other daemon threads may create files. */
	old_umask = umask(0077);
	error = bind(mgnt.listener, (struct sockaddr *)&address,
		     sizeof(address));
	umask(old_umask);
	if (error < 0)
		goto failed;
	(void)snprintf(mgnt.path, sizeof(mgnt.path), "%s", path);
	mgnt.device = 0;
	mgnt.inode = 0;

	/* O_PATH opens the socket inode without following replacement symlinks.
	 * Subsequent permission changes refer to this inode, not the pathname.
	 */
	socket_fd = open(mgnt.path, O_PATH | O_NOFOLLOW | O_CLOEXEC);
	if (socket_fd < 0 || fstat(socket_fd, &stat) != 0)
		goto failed;
	if (!S_ISSOCK(stat.st_mode) || stat.st_uid != geteuid()
	    || (stat.st_mode & 0777) != 0700) {
		errno = EACCES;
		goto failed;
	}
	mgnt.device = stat.st_dev;
	mgnt.inode = stat.st_ino;
	if (socket_gid != (gid_t)-1 && socket_gid != stat.st_gid
	    && fchownat(socket_fd, "", (uid_t)-1, socket_gid, AT_EMPTY_PATH)
		       < 0)
		goto failed;
	/* On Linux, /proc/self/fd/N is a magic link to the opened inode.
	 * Unlike chmod(path), it does not re-resolve the socket filename.
	 * /proc must be available; otherwise startup fails closed. */
	if (snprintf(socket_handle, sizeof(socket_handle), "/proc/self/fd/%d",
		     socket_fd)
		    >= (int)sizeof(socket_handle)
	    || chmod(socket_handle, socket_mode) != 0)
		goto failed;
	if (fstat(socket_fd, &stat) != 0 || stat.st_dev != mgnt.device
	    || stat.st_ino != mgnt.inode || !S_ISSOCK(stat.st_mode)
	    || (stat.st_mode & 0777) != socket_mode
	    || (socket_gid != (gid_t)-1 && stat.st_gid != socket_gid)) {
		errno = EACCES;
		goto failed;
	}
	close(socket_fd);
	socket_fd = -1;
	if (listen(mgnt.listener, 32) < 0)
		goto failed;
	if (pipe2(mgnt.wake, O_NONBLOCK | O_CLOEXEC) < 0)
		goto failed;
	mgnt.execute = execute;
	mgnt.host_arg = host_arg;
	mgnt.admin_uid = geteuid();
	for (i = 0; i < EIGRP_LINUX_MGNT_CLIENT_MAX; ++i)
		mgnt.clients[i].fd = -1;
	error = pthread_create(&mgnt.thread, NULL, mgnt_serve, NULL);
	if (error != 0) {
		errno = error;
		goto failed;
	}
	mgnt.running = true;
	pthread_mutex_unlock(&mgnt_lock);
	return 0;

failed:
	error = errno;
	if (socket_fd >= 0)
		close(socket_fd);
	if (mgnt.wake[0] >= 0)
		close(mgnt.wake[0]);
	if (mgnt.wake[1] >= 0)
		close(mgnt.wake[1]);
	mgnt.wake[0] = mgnt.wake[1] = -1;
	mgnt_path_remove();
	if (mgnt.listener >= 0)
		close(mgnt.listener);
	mgnt.listener = -1;
	mgnt.path[0] = '\0';
	pthread_mutex_unlock(&mgnt_lock);
	errno = error;
	return -1;
}

void eigrp_linux_mgnt_stop(void)
{
	uint8_t marker = MGNT_STOP_MARKER;
	ssize_t written;

	pthread_mutex_lock(&mgnt_lock);
	if (!mgnt.running) {
		pthread_mutex_unlock(&mgnt_lock);
		return;
	}
	/* A full nonblocking wake pipe is already readable by the poll loop. */
	do {
		written = write(mgnt.wake[1], &marker, sizeof(marker));
	} while (written < 0 && errno == EINTR);
	pthread_join(mgnt.thread, NULL);
	close(mgnt.wake[0]);
	close(mgnt.wake[1]);
	close(mgnt.listener);
	mgnt.wake[0] = mgnt.wake[1] = mgnt.listener = -1;
	mgnt_path_remove();
	mgnt.path[0] = '\0';
	mgnt.execute = NULL;
	mgnt.host_arg = NULL;
	mgnt.running = false;
	pthread_mutex_unlock(&mgnt_lock);
}

// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * RTNETLINK source-route observation for native Linux redistribution.
 * Copyright (C) 2026 Donnie V. Savage
 *
 * No kernel route is installed here. Linux FIB sources, not protocol-88
 * packets or DUAL topology descriptors, are the only inputs to this module.
 */
#include "eigrp_linux_redist.h"

#include <errno.h>
#include <limits.h>
#include <linux/netlink.h>
#include <linux/rtnetlink.h>
#include <poll.h>
#include <pthread.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "eigrp_metric.h"
#include "eigrp_rib.h"
#include "eigrp_sys.h"

#ifndef RTPROT_EIGRP
#define RTPROT_EIGRP 192
#endif
#define EIGRP_LINUX_REDIST_BUCKETS 4096U
#define EIGRP_LINUX_REDIST_RX_SIZE 65536U
#define EIGRP_LINUX_REDIST_QUEUE_MAX (4U * 1024U * 1024U)
#define EIGRP_LINUX_REDIST_MAX_HOPS 32U
#define EIGRP_LINUX_REDIST_TIMEOUT 1500

struct eigrp_linux_redist_route {
	eigrp_rib_route_t route;
	eigrp_rib_nexthop_t hops[EIGRP_LINUX_REDIST_MAX_HOPS];
	uint32_t priority;
	uint8_t kernel_protocol;
	struct eigrp_linux_redist_route *next;
};

struct eigrp_linux_redist_routes {
	struct eigrp_linux_redist_route *buckets[EIGRP_LINUX_REDIST_BUCKETS];
};

struct eigrp_linux_redist_sub {
	eigrp_instance_t *instance;
	eigrp_redist_source_t source;
	eigrp_afi_t afi;
	struct eigrp_linux_redist_sub *next;
};

struct eigrp_linux_redist_delivery {
	eigrp_instance_t *instance;
	eigrp_rib_event_type_t type;
	eigrp_rib_route_t route;
	eigrp_rib_nexthop_t hops[EIGRP_LINUX_REDIST_MAX_HOPS];
	struct eigrp_linux_redist_delivery *next;
};

struct eigrp_linux_redist_message {
	struct eigrp_linux_redist_message *next;
	size_t length;
	unsigned char bytes[];
};

struct eigrp_linux_redist_state {
	int fd;
	uint32_t port;
	uint32_t seq;
	bool dirty;
	struct eigrp_linux_redist_routes *routes;
	struct eigrp_linux_redist_sub *subscribers;
};

static struct eigrp_linux_redist_state observer = {.fd = -1};
static pthread_mutex_t observer_lock = PTHREAD_MUTEX_INITIALIZER;

static size_t eigrp_linux_redist_addr_bytes(eigrp_afi_t afi)
{
	return afi == EIGRP_AFI_IPV4 ? 4 : afi == EIGRP_AFI_IPV6 ? 16 : 0;
}

static bool eigrp_linux_redist_prefix_equal(const eigrp_prefix_t *a,
					    const eigrp_prefix_t *b)
{
	return a->address.afi == b->address.afi
	       && a->prefix_length == b->prefix_length
	       && !memcmp(a->address.bytes, b->address.bytes, 16);
}

static void eigrp_linux_redist_prefix_mask(eigrp_prefix_t *prefix)
{
	size_t bytes = eigrp_linux_redist_addr_bytes(prefix->address.afi);
	size_t whole = prefix->prefix_length / 8;
	unsigned int bits = prefix->prefix_length % 8;

	if (bits) {
		prefix->address.bytes[whole] &= (uint8_t)(0xffU << (8 - bits));
		whole++;
	}
	memset(prefix->address.bytes + whole, 0, bytes - whole);
	memset(prefix->address.bytes + bytes, 0, 16 - bytes);
}

static unsigned int eigrp_linux_redist_hash(const eigrp_prefix_t *prefix,
					    eigrp_redist_protocol_t protocol)
{
	uint32_t hash = 2166136261U;
	size_t i;

	hash = (hash ^ (uint32_t)protocol) * 16777619U;
	hash = (hash ^ (uint32_t)prefix->address.afi) * 16777619U;
	hash = (hash ^ prefix->prefix_length) * 16777619U;
	for (i = 0; i < eigrp_linux_redist_addr_bytes(prefix->address.afi); i++)
		hash = (hash ^ prefix->address.bytes[i]) * 16777619U;
	return hash % EIGRP_LINUX_REDIST_BUCKETS;
}

static eigrp_redist_protocol_t eigrp_linux_redist_protocol(uint8_t protocol)
{
	switch (protocol) {
	case RTPROT_KERNEL:
		return EIGRP_REDIST_PROTOCOL_CONNECTED;
	case RTPROT_STATIC:
	case RTPROT_BOOT: /* iproute2 defaults to proto boot. */
		return EIGRP_REDIST_PROTOCOL_STATIC;
	default:
		return EIGRP_REDIST_PROTOCOL_UNSPECIFIED;
	}
}

static void
eigrp_linux_redist_routes_free(struct eigrp_linux_redist_routes *set)
{
	unsigned int i;

	if (!set)
		return;
	for (i = 0; i < EIGRP_LINUX_REDIST_BUCKETS; i++) {
		struct eigrp_linux_redist_route *route = set->buckets[i];
		while (route) {
			struct eigrp_linux_redist_route *next = route->next;
			free(route);
			route = next;
		}
	}
	free(set);
}

static struct eigrp_linux_redist_routes *
eigrp_linux_redist_routes_copy(const struct eigrp_linux_redist_routes *old)
{
	struct eigrp_linux_redist_routes *copy = calloc(1, sizeof(*copy));
	unsigned int i;

	if (!copy)
		return NULL;
	if (!old)
		return copy;
	for (i = 0; i < EIGRP_LINUX_REDIST_BUCKETS; i++) {
		const struct eigrp_linux_redist_route *src;
		struct eigrp_linux_redist_route **tail = &copy->buckets[i];
		for (src = old->buckets[i]; src; src = src->next) {
			*tail = malloc(sizeof(**tail));
			if (!*tail) {
				eigrp_linux_redist_routes_free(copy);
				return NULL;
			}
			**tail = *src;
			(*tail)->route.nexthops = (*tail)->hops;
			(*tail)->next = NULL;
			tail = &(*tail)->next;
		}
	}
	return copy;
}

/* The Linux FIB can contain different priorities for one prefix/source.
 * Never invent a preference between them: with ambiguous candidates there is
 * no single host route eligible for redistribution. */
static const struct eigrp_linux_redist_route *
eigrp_linux_redist_route_unique(const struct eigrp_linux_redist_routes *set,
				const eigrp_prefix_t *prefix,
				eigrp_redist_protocol_t protocol)
{
	const struct eigrp_linux_redist_route *route, *found = NULL;
	unsigned int bucket;

	if (!set)
		return NULL;
	bucket = eigrp_linux_redist_hash(prefix, protocol);
	for (route = set->buckets[bucket]; route; route = route->next) {
		if (route->route.redist.source.protocol != protocol
		    || !eigrp_linux_redist_prefix_equal(&route->route.prefix,
							prefix))
			continue;
		if (found)
			return NULL;
		found = route;
	}
	return found;
}

static bool
eigrp_linux_redist_route_equal(const struct eigrp_linux_redist_route *a,
			       const struct eigrp_linux_redist_route *b)
{
	size_t i;

	if (!a || !b || a->kernel_protocol != b->kernel_protocol
	    || a->priority != b->priority || a->route.tag != b->route.tag
	    || a->route.nexthop_count != b->route.nexthop_count)
		return false;
	for (i = 0; i < a->route.nexthop_count; i++) {
		const eigrp_rib_nexthop_t *one = &a->hops[i];
		const eigrp_rib_nexthop_t *two = &b->hops[i];
		if (one->ifindex != two->ifindex
		    || one->gateway_present != two->gateway_present
		    || one->weight != two->weight
		    || (one->gateway_present
			&& (one->gateway.afi != two->gateway.afi
			    || memcmp(one->gateway.bytes, two->gateway.bytes,
				      16))))
			return false;
	}
	return true;
}

static bool
eigrp_linux_redist_route_apply(struct eigrp_linux_redist_routes *set,
			       const struct eigrp_linux_redist_route *route,
			       bool add, bool priority_present)
{
	struct eigrp_linux_redist_route **cursor, *entry;
	unsigned int bucket;
	unsigned int matches = 0;

	bucket = eigrp_linux_redist_hash(&route->route.prefix,
					 route->route.redist.source.protocol);
	/* A kernel replace can change proto static <-> boot without an old
	 * DEL notification. Resnapshot instead of retaining a phantom entry. */
	if (add) {
		struct eigrp_linux_redist_route *item;
		for (item = set->buckets[bucket]; item; item = item->next)
			if (item->kernel_protocol != route->kernel_protocol
			    && item->route.redist.source.protocol
				       == route->route.redist.source.protocol
			    && item->priority == route->priority
			    && eigrp_linux_redist_prefix_equal(
				    &item->route.prefix, &route->route.prefix))
				return false;
	}
	if (!add && !priority_present) {
		struct eigrp_linux_redist_route *item;
		for (item = set->buckets[bucket]; item; item = item->next)
			if (item->kernel_protocol == route->kernel_protocol
			    && eigrp_linux_redist_prefix_equal(
				    &item->route.prefix, &route->route.prefix))
				matches++;
		if (matches > 1)
			return false;
	}
	matches = 0;
	cursor = &set->buckets[bucket];
	while (*cursor) {
		entry = *cursor;
		if (entry->kernel_protocol != route->kernel_protocol
		    || !eigrp_linux_redist_prefix_equal(&entry->route.prefix,
							&route->route.prefix)
		    || ((priority_present || add)
			&& entry->priority != route->priority)) {
			cursor = &entry->next;
			continue;
		}
		matches++;
		if (!add && !priority_present && matches > 1)
			return false;
		if (!add) {
			*cursor = entry->next;
			free(entry);
			continue;
		}
		/* A NEW at an existing prefix/protocol/priority replaces the
		 * old kernel record (including a changed gateway). */
		*cursor = entry->next;
		free(entry);
		continue;
	}
	if (add) {
		entry = malloc(sizeof(*entry));
		if (!entry)
			return false;
		*entry = *route;
		entry->route.nexthops = entry->hops;
		entry->next = set->buckets[bucket];
		set->buckets[bucket] = entry;
	}
	return true;
}

/* Parse a complete kernel route. Unsupported route types and sources are
 * ignored; malformed fields fail the batch and force resynchronization. */
static bool eigrp_linux_redist_decode(const struct nlmsghdr *header,
				      struct eigrp_linux_redist_route *route,
				      bool *relevant, bool *priority_present)
{
	const struct rtmsg *msg;
	struct rtattr *attr;
	const unsigned char *dst = NULL, *gateway = NULL;
	const struct rtattr *multipath = NULL;
	uint32_t table, priority = 0, ifindex = 0;
	bool via = false;
	bool invalid_hops = false;
	size_t address_bytes;
	int remaining;

	*relevant = false;
	*priority_present = false;
	if (header->nlmsg_len < NLMSG_LENGTH(sizeof(*msg)))
		return false;
	msg = NLMSG_DATA(header);
	if ((msg->rtm_family != AF_INET && msg->rtm_family != AF_INET6)
	    || msg->rtm_type != RTN_UNICAST
	    || !eigrp_linux_redist_protocol(msg->rtm_protocol))
		return true;
	address_bytes = msg->rtm_family == AF_INET ? 4 : 16;
	if (msg->rtm_dst_len > address_bytes * 8)
		return false;
	memset(route, 0, sizeof(*route));
	route->kernel_protocol = msg->rtm_protocol;
	route->route.redist.source.protocol =
		eigrp_linux_redist_protocol(msg->rtm_protocol);
	route->route.prefix.address.afi =
		msg->rtm_family == AF_INET ? EIGRP_AFI_IPV4 : EIGRP_AFI_IPV6;
	route->route.prefix.prefix_length = msg->rtm_dst_len;
	route->route.nexthops = route->hops;
	table = msg->rtm_table;
	remaining = RTM_PAYLOAD(header);
	for (attr = RTM_RTA(msg); RTA_OK(attr, remaining);
	     attr = RTA_NEXT(attr, remaining)) {
		switch (attr->rta_type) {
		case RTA_TABLE:
			if (RTA_PAYLOAD(attr) != sizeof(table))
				return false;
			memcpy(&table, RTA_DATA(attr), sizeof(table));
			break;
		case RTA_DST:
			if (RTA_PAYLOAD(attr) != address_bytes)
				return false;
			dst = RTA_DATA(attr);
			break;
		case RTA_PRIORITY:
			if (RTA_PAYLOAD(attr) != sizeof(priority))
				return false;
			memcpy(&priority, RTA_DATA(attr), sizeof(priority));
			*priority_present = true;
			break;
		case RTA_OIF:
			if (RTA_PAYLOAD(attr) != sizeof(ifindex))
				return false;
			memcpy(&ifindex, RTA_DATA(attr), sizeof(ifindex));
			break;
		case RTA_GATEWAY:
			if (RTA_PAYLOAD(attr) != address_bytes)
				return false;
			gateway = RTA_DATA(attr);
			break;
		case RTA_MULTIPATH:
			multipath = attr;
			break;
		case RTA_VIA:
		case RTA_NH_ID:
			via = true; /* No cross-family/indirect next-hop. */
			break;
		default:
			break;
		}
	}
	if (remaining)
		return false;
	if (table != RT_TABLE_MAIN)
		return true;
	if (dst)
		memcpy(route->route.prefix.address.bytes, dst, address_bytes);
	else if (msg->rtm_dst_len)
		return false;
	eigrp_linux_redist_prefix_mask(&route->route.prefix);
	route->priority = priority;
	route->route.metric = priority; /* Host scalar, not EIGRP seed. */
	if (multipath) {
		int left = RTA_PAYLOAD(multipath);
		struct rtnexthop *nh;

		for (nh = RTA_DATA(multipath); RTNH_OK(nh, left);
		     nh = RTNH_NEXT(nh)) {
			eigrp_rib_nexthop_t *hop;
			struct rtattr *sub;
			int attrs = nh->rtnh_len - sizeof(*nh);
			if (nh->rtnh_ifindex <= 0 || attrs < 0
			    || route->route.nexthop_count
				       == EIGRP_LINUX_REDIST_MAX_HOPS)
				return false;
			hop = &route->hops[route->route.nexthop_count++];
			hop->ifindex = (uint32_t)nh->rtnh_ifindex;
			hop->weight = (uint64_t)nh->rtnh_hops + 1;
			for (sub = RTNH_DATA(nh); RTA_OK(sub, attrs);
			     sub = RTA_NEXT(sub, attrs)) {
				if (sub->rta_type == RTA_GATEWAY) {
					if (RTA_PAYLOAD(sub) != address_bytes)
						return false;
					hop->gateway_present = true;
					hop->gateway.afi =
						route->route.prefix.address.afi;
					memcpy(hop->gateway.bytes,
					       RTA_DATA(sub), address_bytes);
				} else if (sub->rta_type == RTA_VIA
					   || sub->rta_type == RTA_NH_ID) {
					invalid_hops = true;
				}
			}
			if (attrs)
				return false;
			left -= RTNH_ALIGN(nh->rtnh_len);
		}
		if (left)
			return false;
	} else if (ifindex) {
		eigrp_rib_nexthop_t *hop = &route->hops[0];
		route->route.nexthop_count = 1;
		hop->ifindex = ifindex;
		hop->weight = 1;
		if (gateway) {
			hop->gateway_present = true;
			hop->gateway.afi = route->route.prefix.address.afi;
			memcpy(hop->gateway.bytes, gateway, address_bytes);
		}
	}
	/* A kernel route without an explicit ifindex, or an indirect next-hop
	 * object, cannot be normalized into the portable next-hop contract. */
	if (header->nlmsg_type == RTM_DELROUTE) {
		*relevant = true;
		return true;
	}
	if (via || invalid_hops || !route->route.nexthop_count)
		return true;
	*relevant = true;
	return true;
}

static bool
eigrp_linux_redist_message_apply(struct eigrp_linux_redist_routes *set,
				 const struct nlmsghdr *header)
{
	struct eigrp_linux_redist_route decoded;
	bool relevant, priority_present;

	if (header->nlmsg_type != RTM_NEWROUTE
	    && header->nlmsg_type != RTM_DELROUTE)
		return true;
	if (!eigrp_linux_redist_decode(header, &decoded, &relevant,
				       &priority_present))
		return false;
	if (!relevant)
		return true;
	return eigrp_linux_redist_route_apply(
		set, &decoded, header->nlmsg_type == RTM_NEWROUTE,
		priority_present);
}

/* An unsupported protocol, non-unicast route or indirect next hop can
 * replace an existing source without an old DEL. Resnapshot only on a
 * prefix/priority overlap; unrelated routes never enter the source cache. */
static bool
eigrp_linux_redist_foreign_overlap(const struct eigrp_linux_redist_routes *set,
				   const struct nlmsghdr *header)
{
	const struct rtmsg *msg;
	struct rtattr *attr;
	eigrp_prefix_t prefix = {0};
	uint32_t table, priority = 0;
	bool priority_present = false;
	bool destination_present = false;
	size_t address_bytes;
	int remaining;
	unsigned int protocol;

	if (!set || header->nlmsg_len < NLMSG_LENGTH(sizeof(*msg)))
		return false;
	msg = NLMSG_DATA(header);
	if (msg->rtm_family != AF_INET && msg->rtm_family != AF_INET6)
		return false;
	prefix.address.afi =
		msg->rtm_family == AF_INET ? EIGRP_AFI_IPV4 : EIGRP_AFI_IPV6;
	address_bytes = eigrp_linux_redist_addr_bytes(prefix.address.afi);
	if (msg->rtm_dst_len > address_bytes * 8)
		return false;
	prefix.prefix_length = msg->rtm_dst_len;
	table = msg->rtm_table;
	remaining = RTM_PAYLOAD(header);
	for (attr = RTM_RTA(msg); RTA_OK(attr, remaining);
	     attr = RTA_NEXT(attr, remaining)) {
		switch (attr->rta_type) {
		case RTA_TABLE:
			if (RTA_PAYLOAD(attr) != sizeof(table))
				return false;
			memcpy(&table, RTA_DATA(attr), sizeof(table));
			break;
		case RTA_PRIORITY:
			if (RTA_PAYLOAD(attr) != sizeof(priority))
				return false;
			memcpy(&priority, RTA_DATA(attr), sizeof(priority));
			priority_present = true;
			break;
		case RTA_DST:
			if (RTA_PAYLOAD(attr) != address_bytes)
				return false;
			memcpy(prefix.address.bytes, RTA_DATA(attr),
			       address_bytes);
			destination_present = true;
			break;
		default:
			break;
		}
	}
	if (remaining || table != RT_TABLE_MAIN
	    || (prefix.prefix_length && !destination_present))
		return false;
	eigrp_linux_redist_prefix_mask(&prefix);
	for (protocol = EIGRP_REDIST_PROTOCOL_CONNECTED;
	     protocol <= EIGRP_REDIST_PROTOCOL_STATIC; protocol++) {
		unsigned int bucket = eigrp_linux_redist_hash(
			&prefix, (eigrp_redist_protocol_t)protocol);
		const struct eigrp_linux_redist_route *entry;

		for (entry = set->buckets[bucket]; entry; entry = entry->next)
			if (entry->route.redist.source.protocol
				    == (eigrp_redist_protocol_t)protocol
			    && eigrp_linux_redist_prefix_equal(
				    &entry->route.prefix, &prefix)
			    && (!priority_present
				|| entry->priority == priority))
				return true;
	}
	return false;
}

static void eigrp_linux_redist_fd_close(void)
{
	if (observer.fd >= 0)
		close(observer.fd);
	observer.fd = -1;
	observer.port = 0;
}

static bool eigrp_linux_redist_fd_open(void)
{
	struct sockaddr_nl local = {
		.nl_family = AF_NETLINK,
		.nl_groups = RTMGRP_IPV4_ROUTE | RTMGRP_IPV6_ROUTE,
	};
	socklen_t length = sizeof(local);
	int fd, buffer = 1 << 20;

	if (observer.fd >= 0)
		return true;
	fd = socket(AF_NETLINK, SOCK_RAW | SOCK_CLOEXEC | SOCK_NONBLOCK,
		    NETLINK_ROUTE);
	if (fd < 0)
		return false;
	(void)setsockopt(fd, SOL_SOCKET, SO_RCVBUF, &buffer, sizeof(buffer));
	if (bind(fd, (struct sockaddr *)&local, sizeof(local)) < 0
	    || getsockname(fd, (struct sockaddr *)&local, &length) < 0
	    || length != sizeof(local)) {
		close(fd);
		return false;
	}
	observer.fd = fd;
	observer.port = local.nl_pid;
	return true;
}

static void
eigrp_linux_redist_messages_free(struct eigrp_linux_redist_message *messages)
{
	while (messages) {
		struct eigrp_linux_redist_message *next = messages->next;
		free(messages);
		messages = next;
	}
}

/* Dump requests use the subscribed socket. Keep multicast messages arriving
 * during either dump, then apply them *after* both completed snapshots. */
static bool
eigrp_linux_redist_dump(struct eigrp_linux_redist_routes *set, int family,
			struct eigrp_linux_redist_message ***pending_tail,
			size_t *pending_size)
{
	struct {
		struct nlmsghdr header;
		struct rtmsg route;
	} request = {0};
	struct sockaddr_nl kernel = {.nl_family = AF_NETLINK};
	struct iovec outbound = {.iov_base = &request,
				 .iov_len = sizeof(request)};
	struct msghdr send_header = {.msg_name = &kernel,
				     .msg_namelen = sizeof(kernel),
				     .msg_iov = &outbound,
				     .msg_iovlen = 1};
	unsigned char buffer[EIGRP_LINUX_REDIST_RX_SIZE];
	uint32_t seq = ++observer.seq;

	request.header.nlmsg_len = NLMSG_LENGTH(sizeof(request.route));
	request.header.nlmsg_type = RTM_GETROUTE;
	request.header.nlmsg_flags = NLM_F_REQUEST | NLM_F_DUMP;
	request.header.nlmsg_seq = seq;
	request.route.rtm_family = family;
	if (sendmsg(observer.fd, &send_header, 0) < 0)
		return false;
	for (;;) {
		struct pollfd wait = {.fd = observer.fd, .events = POLLIN};
		struct sockaddr_nl sender = {0};
		struct iovec iov = {.iov_base = buffer,
				    .iov_len = sizeof(buffer)};
		struct msghdr message = {.msg_name = &sender,
					 .msg_namelen = sizeof(sender),
					 .msg_iov = &iov,
					 .msg_iovlen = 1};
		struct nlmsghdr *h;
		int left, ready;
		ssize_t size;

		ready = poll(&wait, 1, EIGRP_LINUX_REDIST_TIMEOUT);
		if (ready <= 0 || !(wait.revents & POLLIN))
			return false;
		size = recvmsg(observer.fd, &message, MSG_DONTWAIT);
		if (size < 0 && errno == EINTR)
			continue;
		if (size <= 0 || (message.msg_flags & MSG_TRUNC)
		    || message.msg_namelen != sizeof(sender)
		    || sender.nl_pid != 0)
			return false;
		left = (int)size;
		for (h = (struct nlmsghdr *)buffer; NLMSG_OK(h, left);
		     h = NLMSG_NEXT(h, left)) {
			if (h->nlmsg_type == NLMSG_OVERRUN)
				return false;
			if (h->nlmsg_seq != seq) {
				struct eigrp_linux_redist_message *copy;
				if (h->nlmsg_type == NLMSG_NOOP)
					continue;
				if (h->nlmsg_type != RTM_NEWROUTE
				    && h->nlmsg_type != RTM_DELROUTE)
					return false;
				if (*pending_size > EIGRP_LINUX_REDIST_QUEUE_MAX
							    - h->nlmsg_len)
					return false;
				copy = malloc(sizeof(*copy) + h->nlmsg_len);
				if (!copy)
					return false;
				copy->next = NULL;
				copy->length = h->nlmsg_len;
				memcpy(copy->bytes, h, h->nlmsg_len);
				**pending_tail = copy;
				*pending_tail = &copy->next;
				*pending_size += h->nlmsg_len;
				continue;
			}
			if (h->nlmsg_type == NLMSG_DONE)
				return !(h->nlmsg_flags & NLM_F_DUMP_INTR)
				       && (unsigned int)left
						  == NLMSG_ALIGN(h->nlmsg_len);
			if (h->nlmsg_type == NLMSG_ERROR)
				return false;
			if (!eigrp_linux_redist_message_apply(set, h))
				return false;
		}
		if (left)
			return false;
	}
}

static bool
eigrp_linux_redist_foreign_overlap(const struct eigrp_linux_redist_routes *set,
				   const struct nlmsghdr *header);

static struct eigrp_linux_redist_routes *eigrp_linux_redist_snapshot(void)
{
	struct eigrp_linux_redist_routes *routes;
	struct eigrp_linux_redist_message *pending = NULL, *event;
	struct eigrp_linux_redist_message **tail = &pending;
	size_t bytes = 0;

	if (!eigrp_linux_redist_fd_open())
		return NULL;
	routes = calloc(1, sizeof(*routes));
	if (!routes)
		return NULL;
	if (!eigrp_linux_redist_dump(routes, AF_INET, &tail, &bytes)
	    || !eigrp_linux_redist_dump(routes, AF_INET6, &tail, &bytes))
		goto failed;
	for (event = pending; event; event = event->next) {
		const struct nlmsghdr *h =
			(const struct nlmsghdr *)event->bytes;
		struct eigrp_linux_redist_route decoded;
		bool relevant, priority_present;

		if (!eigrp_linux_redist_decode(h, &decoded, &relevant,
					       &priority_present)
		    || (h->nlmsg_type == RTM_NEWROUTE && !relevant
			&& eigrp_linux_redist_foreign_overlap(routes, h))
		    || !eigrp_linux_redist_message_apply(routes, h))
			goto failed;
	}
	eigrp_linux_redist_messages_free(pending);
	return routes;

failed:
	eigrp_linux_redist_messages_free(pending);
	eigrp_linux_redist_routes_free(routes);
	eigrp_linux_redist_fd_close();
	return NULL;
}

/* Deliver a copied source route after releasing the monitor lock. Core AF
 * ownership is respected by eigrp_rib_redist_{add,del}'s enqueue behavior. */
static bool
eigrp_linux_redist_delivery_add(struct eigrp_linux_redist_delivery ***tail,
				eigrp_instance_t *instance,
				eigrp_rib_event_type_t type,
				const struct eigrp_linux_redist_route *route)
{
	struct eigrp_linux_redist_delivery *entry = calloc(1, sizeof(*entry));

	if (!entry)
		return false;
	entry->instance = instance;
	entry->type = type;
	entry->route = route->route;
	memcpy(entry->hops, route->hops,
	       route->route.nexthop_count * sizeof(*entry->hops));
	entry->route.nexthops = entry->hops;
	entry->next = NULL;
	**tail = entry;
	*tail = &entry->next;
	return true;
}

static void
eigrp_linux_redist_deliveries_free(struct eigrp_linux_redist_delivery *head)
{
	while (head) {
		struct eigrp_linux_redist_delivery *next = head->next;
		free(head);
		head = next;
	}
}

static bool eigrp_linux_redist_delivery_changes(
	const struct eigrp_linux_redist_routes *old,
	const struct eigrp_linux_redist_routes *current,
	const struct eigrp_linux_redist_sub *subscription, bool refresh,
	struct eigrp_linux_redist_delivery ***tail)
{
	const struct eigrp_linux_redist_route *entry, *previous, *next;
	unsigned int i;

	for (i = 0; old && i < EIGRP_LINUX_REDIST_BUCKETS; i++)
		for (entry = old->buckets[i]; entry; entry = entry->next) {
			if (entry->route.redist.source.protocol
				    != subscription->source.protocol
			    || entry->route.prefix.address.afi
				       != subscription->afi)
				continue;
			previous = eigrp_linux_redist_route_unique(
				old, &entry->route.prefix,
				subscription->source.protocol);
			if (previous != entry)
				continue;
			next = eigrp_linux_redist_route_unique(
				current, &entry->route.prefix,
				subscription->source.protocol);
			if (!next
			    && !eigrp_linux_redist_delivery_add(
				    tail, subscription->instance,
				    EIGRP_RIB_EVENT_DELETE, entry))
				return false;
			if (next
			    && (refresh
				|| !eigrp_linux_redist_route_equal(entry, next))
			    && !eigrp_linux_redist_delivery_add(
				    tail, subscription->instance,
				    EIGRP_RIB_EVENT_UPDATE, next))
				return false;
		}
	for (i = 0; current && i < EIGRP_LINUX_REDIST_BUCKETS; i++)
		for (entry = current->buckets[i]; entry; entry = entry->next) {
			if (entry->route.redist.source.protocol
				    != subscription->source.protocol
			    || entry->route.prefix.address.afi
				       != subscription->afi)
				continue;
			next = eigrp_linux_redist_route_unique(
				current, &entry->route.prefix,
				subscription->source.protocol);
			if (next != entry)
				continue;
			previous = eigrp_linux_redist_route_unique(
				old, &entry->route.prefix,
				subscription->source.protocol);
			if (!previous
			    && !eigrp_linux_redist_delivery_add(
				    tail, subscription->instance,
				    EIGRP_RIB_EVENT_UPDATE, entry))
				return false;
		}
	return true;
}

static struct eigrp_linux_redist_delivery *
eigrp_linux_redist_diff(const struct eigrp_linux_redist_routes *old,
			const struct eigrp_linux_redist_routes *current,
			const struct eigrp_linux_redist_sub *subscribers,
			bool refresh, bool *success)
{
	struct eigrp_linux_redist_delivery *head = NULL;
	struct eigrp_linux_redist_delivery **tail = &head;
	const struct eigrp_linux_redist_sub *sub;

	*success = false;
	for (sub = subscribers; sub; sub = sub->next)
		if (!eigrp_linux_redist_delivery_changes(old, current, sub,
							 refresh, &tail)) {
			eigrp_linux_redist_deliveries_free(head);
			return NULL;
		}
	*success = true;
	return head;
}

struct eigrp_linux_redist_interface_metric {
	eigrp_ifindex_t ifindex;
	eigrp_metric_values_t values;
	bool found;
};

static void
eigrp_linux_redist_interface_metric(const eigrp_intf_runtime_state_t *state,
				    void *arg)
{
	struct eigrp_linux_redist_interface_metric *match = arg;

	if (state->ifindex != match->ifindex || match->found)
		return;
	match->values.bandwidth =
		state->bandwidth ? state->bandwidth : EIGRP_BANDWIDTH_DEFAULT;
	match->values.delay = EIGRP_DELAY_DEFAULT;
	match->values.reliability = EIGRP_RELIABILITY_DEFAULT;
	match->values.load = EIGRP_LOAD_DEFAULT;
	match->values.mtu =
		state->mtu > UINT16_MAX ? UINT16_MAX : (uint16_t)state->mtu;
	match->found = true;
}

static eigrp_result_t
eigrp_linux_redist_dispatch(struct eigrp_linux_redist_delivery *delivery)
{
	eigrp_result_t result = EIGRP_RESULT_SUCCESS;

	for (; delivery; delivery = delivery->next) {
		eigrp_result_t next;
		eigrp_rib_route_t *route = &delivery->route;

		if (delivery->type == EIGRP_RIB_EVENT_UPDATE
		    && route->nexthop_count
		    && (route->redist.source.protocol
				== EIGRP_REDIST_PROTOCOL_CONNECTED
			|| (route->redist.source.protocol
				    == EIGRP_REDIST_PROTOCOL_STATIC
			    && !route->nexthops[0].gateway_present))) {
			struct eigrp_linux_redist_interface_metric metric = {
				.ifindex = route->nexthops[0].ifindex,
			};
			(void)eigrp_sys_interface_walk(
				delivery->instance,
				eigrp_linux_redist_interface_metric, &metric);
			if (metric.found)
				eigrp_metric_values_convert(
					&metric.values,
					&route->redist.vecmetric);
		}
		next = delivery->type == EIGRP_RIB_EVENT_UPDATE
			       ? eigrp_rib_redist_add(delivery->instance, route)
			       : eigrp_rib_redist_del(delivery->instance,
						      route);
		if (next != EIGRP_RESULT_SUCCESS
		    && result == EIGRP_RESULT_SUCCESS)
			result = next;
	}
	return result;
}

static eigrp_result_t
eigrp_linux_redist_commit(struct eigrp_linux_redist_routes *current,
			  bool refresh)
{
	struct eigrp_linux_redist_routes *old;
	struct eigrp_linux_redist_delivery *deliveries;
	eigrp_result_t result;
	bool success;

	/* Caller holds observer_lock. No callback runs while it is held. */
	old = observer.routes;
	deliveries = eigrp_linux_redist_diff(old, current, observer.subscribers,
					     refresh, &success);
	if (!success) {
		observer.dirty = true;
		return EIGRP_RESULT_INTERNAL_FAILURE;
	}
	observer.routes = current;
	pthread_mutex_unlock(&observer_lock);
	result = eigrp_linux_redist_dispatch(deliveries);
	eigrp_linux_redist_deliveries_free(deliveries);
	eigrp_linux_redist_routes_free(old);
	pthread_mutex_lock(&observer_lock);
	observer.dirty = result == EIGRP_RESULT_INTERNAL_FAILURE;
	return result;
}

/* The host serializes calls to the pumping API with instance teardown. The
 * monitor lock protects the route cache and subscription list, not AF lifetime.
 */
static eigrp_result_t eigrp_linux_redist_resync_locked(bool refresh)
{
	struct eigrp_linux_redist_routes *current =
		eigrp_linux_redist_snapshot();
	eigrp_result_t result;

	if (!current)
		return EIGRP_RESULT_INTERNAL_FAILURE;
	result = eigrp_linux_redist_commit(current, refresh);
	if (observer.routes != current)
		eigrp_linux_redist_routes_free(current);
	return result;
}

eigrp_result_t eigrp_linux_redist_resync(void)
{
	eigrp_result_t result;

	pthread_mutex_lock(&observer_lock);
	result = eigrp_linux_redist_resync_locked(true);
	pthread_mutex_unlock(&observer_lock);
	return result;
}

int eigrp_linux_redist_fd(void)
{
	int fd;

	pthread_mutex_lock(&observer_lock);
	fd = observer.fd;
	pthread_mutex_unlock(&observer_lock);
	return fd;
}

static eigrp_result_t eigrp_linux_redist_process_locked(void)
{
	struct eigrp_linux_redist_routes *next = NULL;
	unsigned char buffer[EIGRP_LINUX_REDIST_RX_SIZE];
	unsigned int batches;
	bool changed = false;
	eigrp_result_t result;

	if (observer.fd < 0 || observer.dirty)
		return eigrp_linux_redist_resync_locked(observer.dirty);
	for (batches = 0; batches < 1024; batches++) {
		struct sockaddr_nl sender = {0};
		struct iovec iov = {.iov_base = buffer,
				    .iov_len = sizeof(buffer)};
		struct msghdr message = {.msg_name = &sender,
					 .msg_namelen = sizeof(sender),
					 .msg_iov = &iov,
					 .msg_iovlen = 1};
		struct nlmsghdr *h;
		int left;
		ssize_t size;

		size = recvmsg(observer.fd, &message, MSG_DONTWAIT);
		if (size < 0 && errno == EINTR)
			continue;
		if (size < 0 && (errno == EAGAIN || errno == EWOULDBLOCK))
			break;
		if (size <= 0 || (message.msg_flags & MSG_TRUNC)
		    || message.msg_namelen != sizeof(sender)
		    || sender.nl_pid != 0)
			goto lost;
		left = (int)size;
		for (h = (struct nlmsghdr *)buffer; NLMSG_OK(h, left);
		     h = NLMSG_NEXT(h, left)) {
			if (h->nlmsg_type == NLMSG_OVERRUN
			    || h->nlmsg_type == NLMSG_ERROR)
				goto lost;
			if (h->nlmsg_type != RTM_NEWROUTE
			    && h->nlmsg_type != RTM_DELROUTE)
				continue;
			{
				struct eigrp_linux_redist_route decoded;
				bool relevant, priority_present;

				if (!eigrp_linux_redist_decode(
					    h, &decoded, &relevant,
					    &priority_present))
					goto lost;
				if (!relevant) {
					if (h->nlmsg_type == RTM_NEWROUTE
					    && eigrp_linux_redist_foreign_overlap(
						    observer.routes, h))
						goto lost;
					continue;
				}
				if (!next) {
					next = eigrp_linux_redist_routes_copy(
						observer.routes);
					if (!next)
						goto lost;
				}
				if (!eigrp_linux_redist_route_apply(
					    next, &decoded,
					    h->nlmsg_type == RTM_NEWROUTE,
					    priority_present))
					goto lost;
				changed = true;
			}
		}
		if (left)
			goto lost;
	}
	if (!changed)
		return EIGRP_RESULT_SUCCESS;
	result = eigrp_linux_redist_commit(next, false);
	if (observer.routes != next)
		eigrp_linux_redist_routes_free(next);
	return result;

lost:
	eigrp_linux_redist_routes_free(next);
	eigrp_linux_redist_fd_close();
	return eigrp_linux_redist_resync_locked(false);
}

eigrp_result_t eigrp_linux_redist_process(void)
{
	eigrp_result_t result;

	pthread_mutex_lock(&observer_lock);
	result = eigrp_linux_redist_process_locked();
	pthread_mutex_unlock(&observer_lock);
	return result;
}

eigrp_result_t eigrp_rib_redistribute_add(eigrp_instance_t *instance,
					  const eigrp_redist_source_t *source)
{
	struct eigrp_linux_redist_sub *sub, *entry;
	struct eigrp_linux_redist_delivery *deliveries;
	eigrp_result_t result;
	bool success;

	if (!instance || !source)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	if (eigrp_instance_vrf_id(instance) != EIGRP_VRF_DEFAULT
	    || (eigrp_instance_afi(instance) != EIGRP_AFI_IPV4
		&& eigrp_instance_afi(instance) != EIGRP_AFI_IPV6))
		return EIGRP_RESULT_UNSUPPORTED;
	if (source->route_instance
	    || (source->protocol != EIGRP_REDIST_PROTOCOL_CONNECTED
		&& source->protocol != EIGRP_REDIST_PROTOCOL_STATIC))
		return EIGRP_RESULT_UNSUPPORTED;
	pthread_mutex_lock(&observer_lock);
	for (sub = observer.subscribers; sub; sub = sub->next)
		if (sub->instance == instance
		    && sub->source.protocol == source->protocol
		    && sub->source.route_instance == source->route_instance) {
			pthread_mutex_unlock(&observer_lock);
			return EIGRP_RESULT_SUCCESS;
		}
	if (observer.fd < 0) {
		struct eigrp_linux_redist_routes *snapshot =
			eigrp_linux_redist_snapshot();
		if (!snapshot) {
			pthread_mutex_unlock(&observer_lock);
			return EIGRP_RESULT_INTERNAL_FAILURE;
		}
		/* No existing subscribers must lose notifications during a
		 * socket recovery initiated by a fresh subscription. */
		result = eigrp_linux_redist_commit(snapshot, false);
		if (result != EIGRP_RESULT_SUCCESS) {
			pthread_mutex_unlock(&observer_lock);
			return result;
		}
	}
	entry = calloc(1, sizeof(*entry));
	if (!entry) {
		pthread_mutex_unlock(&observer_lock);
		return EIGRP_RESULT_INTERNAL_FAILURE;
	}
	entry->instance = instance;
	entry->source = *source;
	entry->afi = eigrp_instance_afi(instance);
	entry->next = NULL;
	/* Synchronous initial replay includes only this subscription. */
	deliveries = NULL;
	/* Build an initial replay without changing the other subscriptions. */
	deliveries = eigrp_linux_redist_diff(NULL, observer.routes, entry,
					     false, &success);
	if (!success) {
		free(entry);
		pthread_mutex_unlock(&observer_lock);
		return EIGRP_RESULT_INTERNAL_FAILURE;
	}
	entry->next = observer.subscribers;
	observer.subscribers = entry;
	pthread_mutex_unlock(&observer_lock);
	result = eigrp_linux_redist_dispatch(deliveries);
	eigrp_linux_redist_deliveries_free(deliveries);
	if (result == EIGRP_RESULT_INTERNAL_FAILURE) {
		/* Subscription rollback: the portable configuration target must
		 * not retain a host binding that reported failure to install.
		 */
		(void)eigrp_rib_redistribute_remove(instance, source);
		return result;
	}
	/* Route policy (e.g. maximum-prefix) may reject individual routes;
	 * that does not invalidate a successfully registered source. */
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t
eigrp_rib_redistribute_remove(eigrp_instance_t *instance,
			      const eigrp_redist_source_t *source)
{
	struct eigrp_linux_redist_sub **cursor, *removed;

	if (!instance || !source)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	pthread_mutex_lock(&observer_lock);
	for (cursor = &observer.subscribers; *cursor; cursor = &(*cursor)->next)
		if ((*cursor)->instance == instance
		    && (*cursor)->source.protocol == source->protocol
		    && (*cursor)->source.route_instance
			       == source->route_instance) {
			removed = *cursor;
			*cursor = removed->next;
			free(removed);
			pthread_mutex_unlock(&observer_lock);
			return EIGRP_RESULT_SUCCESS;
		}
	pthread_mutex_unlock(&observer_lock);
	return EIGRP_RESULT_NOT_FOUND;
}

void eigrp_linux_redist_instance_delete(eigrp_instance_t *instance)
{
	struct eigrp_linux_redist_sub **cursor;

	pthread_mutex_lock(&observer_lock);
	for (cursor = &observer.subscribers; *cursor;) {
		struct eigrp_linux_redist_sub *sub = *cursor;
		if (sub->instance != instance) {
			cursor = &sub->next;
			continue;
		}
		*cursor = sub->next;
		free(sub);
	}
	pthread_mutex_unlock(&observer_lock);
}

void eigrp_linux_redist_stop(void)
{
	struct eigrp_linux_redist_sub *sub;

	pthread_mutex_lock(&observer_lock);
	eigrp_linux_redist_fd_close();
	eigrp_linux_redist_routes_free(observer.routes);
	observer.routes = NULL;
	observer.dirty = false;
	while ((sub = observer.subscribers) != NULL) {
		observer.subscribers = sub->next;
		free(sub);
	}
	pthread_mutex_unlock(&observer_lock);
}

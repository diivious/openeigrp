// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Native Linux RTNETLINK RIB backend for portable EIGRP-selected routes.
 * Copyright (C) 2026 Donnie V. Savage
 *
 * Only protocol 192 routes in the default VRF/main table are managed. Route
 * ownership requires a successful installation in this process. Even an exact
 * readback match cannot prove ownership of a route left by another process.
 */
#include <errno.h>
#include <limits.h>
#include <linux/netlink.h>
#include <linux/rtnetlink.h>
#include <poll.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

#include "eigrp_linux_rib.h"
#include "eigrp_linux_redist.h"
#include "eigrp_rib.h"

#ifndef RTPROT_EIGRP
#define RTPROT_EIGRP 192
#endif

#define EIGRP_LINUX_RIB_MESSAGE_MAX 8192U
#define EIGRP_LINUX_RIB_RECEIVE_MAX 65536U
#define EIGRP_LINUX_RIB_NEXTHOPS_MAX 32U
#define EIGRP_LINUX_RIB_TIMEOUT 1500

struct eigrp_linux_rib_route {
	eigrp_prefix_t prefix;
	eigrp_rib_nexthop_t *nexthops;
	size_t count;
	uint32_t priority;
	uint64_t metric; /* EIGRP composite metric, not Linux route priority. */
	uint32_t tag;
};

struct eigrp_linux_rib_entry {
	const eigrp_instance_t
		*instance; /* Identity only; never dereferenced. */
	struct eigrp_linux_rib_route desired;
	struct eigrp_linux_rib_route installed;
	bool want;
	bool owned;
	struct eigrp_linux_rib_entry *next;
};

struct eigrp_linux_rib_state {
	int fd;
	uint32_t port;
	uint32_t sequence;
	struct eigrp_linux_rib_entry *entries;
};

static pthread_mutex_t rib_lock = PTHREAD_MUTEX_INITIALIZER;
static struct eigrp_linux_rib_state rib = {.fd = -1};

struct eigrp_linux_rib_message {
	struct nlmsghdr header;
	struct rtmsg route;
	unsigned char attrs[EIGRP_LINUX_RIB_MESSAGE_MAX];
};

static size_t eigrp_linux_rib_bytes(eigrp_afi_t afi)
{
	return afi == EIGRP_AFI_IPV4 ? 4 : afi == EIGRP_AFI_IPV6 ? 16 : 0;
}

static int eigrp_linux_rib_family(eigrp_afi_t afi)
{
	return afi == EIGRP_AFI_IPV4 ? AF_INET : AF_INET6;
}

static bool eigrp_linux_rib_prefix_valid(const eigrp_prefix_t *prefix)
{
	if (!prefix)
		return false;
	if (prefix->address.afi == EIGRP_AFI_IPV4)
		return prefix->prefix_length <= 32;
	if (prefix->address.afi == EIGRP_AFI_IPV6)
		return prefix->prefix_length <= 128;
	return false;
}

static void eigrp_linux_rib_prefix_mask(eigrp_prefix_t *prefix)
{
	size_t bytes = eigrp_linux_rib_bytes(prefix->address.afi);
	unsigned int whole = prefix->prefix_length / 8;
	unsigned int part = prefix->prefix_length % 8;

	if (part) {
		prefix->address.bytes[whole] &= (uint8_t)(0xffU << (8 - part));
		whole++;
	}
	memset(prefix->address.bytes + whole, 0, bytes - whole);
	memset(prefix->address.bytes + bytes, 0, 16 - bytes);
}

static bool eigrp_linux_rib_prefix_equal(const eigrp_prefix_t *a,
					 const eigrp_prefix_t *b)
{
	return a->address.afi == b->address.afi
	       && a->prefix_length == b->prefix_length
	       && !memcmp(a->address.bytes, b->address.bytes, 16);
}

static bool eigrp_linux_rib_nexthop_equal(const eigrp_rib_nexthop_t *a,
					  const eigrp_rib_nexthop_t *b)
{
	return a->ifindex == b->ifindex
	       && a->gateway_present == b->gateway_present
	       && (!a->gateway_present
		   || (a->gateway.afi == b->gateway.afi
		       && !memcmp(a->gateway.bytes, b->gateway.bytes, 16)))
	       && a->weight == b->weight;
}

static bool eigrp_linux_rib_route_equal(const struct eigrp_linux_rib_route *a,
					const struct eigrp_linux_rib_route *b)
{
	bool used[EIGRP_LINUX_RIB_NEXTHOPS_MAX] = {false};
	size_t i, j;

	if (!eigrp_linux_rib_prefix_equal(&a->prefix, &b->prefix)
	    || a->priority != b->priority || a->count != b->count)
		return false;
	for (i = 0; i < a->count; i++) {
		for (j = 0; j < b->count; j++)
			if (!used[j]
			    && eigrp_linux_rib_nexthop_equal(&a->nexthops[i],
							     &b->nexthops[j])) {
				used[j] = true;
				break;
			}
		if (j == b->count)
			return false;
	}
	return true;
}

static eigrp_result_t
eigrp_linux_rib_route_copy(struct eigrp_linux_rib_route *dst,
			   const eigrp_rib_route_t *src)
{
	size_t i;

	if (!src || !eigrp_linux_rib_prefix_valid(&src->prefix)
	    || !src->nexthops || !src->nexthop_count
	    || src->nexthop_count > EIGRP_LINUX_RIB_NEXTHOPS_MAX
	    || src->install.admin_dist > 255
	    || (src->install.type != EIGRP_RIB_ROUTE_INTERNAL
		&& src->install.type != EIGRP_RIB_ROUTE_EXTERNAL))
		return EIGRP_RESULT_INVALID_ARGUMENT;
	memset(dst, 0, sizeof(*dst));
	dst->nexthops = calloc(src->nexthop_count, sizeof(*dst->nexthops));
	if (!dst->nexthops)
		return EIGRP_RESULT_INTERNAL_FAILURE;
	dst->prefix = src->prefix;
	eigrp_linux_rib_prefix_mask(&dst->prefix);
	dst->priority = src->install.admin_dist;
	dst->count = src->nexthop_count;
	dst->metric = src->metric;
	dst->tag = src->tag;
	for (i = 0; i < dst->count; i++) {
		const eigrp_rib_nexthop_t *nh = &src->nexthops[i];
		if (!nh->ifindex || nh->ifindex > INT_MAX
		    || (nh->gateway_present
			&& nh->gateway.afi != dst->prefix.address.afi)) {
			free(dst->nexthops);
			dst->nexthops = NULL;
			return EIGRP_RESULT_INVALID_ARGUMENT;
		}
		dst->nexthops[i] = *nh;
		if (!nh->gateway_present)
			memset(&dst->nexthops[i].gateway, 0,
			       sizeof(dst->nexthops[i].gateway));
		/* Linux rtnh_hops is weight minus one, in eight bits. */
		dst->nexthops[i].weight = dst->count == 1 || !nh->weight ? 1
					  : nh->weight > 256 ? 256
							     : nh->weight;
	}
	return EIGRP_RESULT_SUCCESS;
}

static void eigrp_linux_rib_socket_close(void)
{
	if (rib.fd >= 0)
		close(rib.fd);
	rib.fd = -1;
	rib.port = 0;
}

static bool eigrp_linux_rib_socket_open(void)
{
	struct sockaddr_nl addr = {.nl_family = AF_NETLINK};
	socklen_t length = sizeof(addr);
	int fd;

	if (rib.fd >= 0)
		return true;
	fd = socket(AF_NETLINK, SOCK_RAW | SOCK_CLOEXEC, NETLINK_ROUTE);
	if (fd < 0)
		return false;
	if (bind(fd, (struct sockaddr *)&addr, sizeof(addr)) < 0
	    || getsockname(fd, (struct sockaddr *)&addr, &length) < 0
	    || length != sizeof(addr)) {
		close(fd);
		return false;
	}
	rib.fd = fd;
	rib.port = addr.nl_pid;
	return true;
}

static bool eigrp_linux_rib_attr(struct nlmsghdr *message, size_t capacity,
				 uint16_t type, const void *data, size_t size)
{
	size_t offset = NLMSG_ALIGN(message->nlmsg_len);
	struct rtattr *attr;

	if (size > UINT16_MAX - RTA_LENGTH(0) || offset > capacity
	    || RTA_SPACE(size) > capacity - offset)
		return false;
	attr = (struct rtattr *)((unsigned char *)message + offset);
	attr->rta_type = type;
	attr->rta_len = RTA_LENGTH(size);
	if (size)
		memcpy(RTA_DATA(attr), data, size);
	message->nlmsg_len = offset + RTA_SPACE(size);
	return true;
}

static bool
eigrp_linux_rib_message_make(struct eigrp_linux_rib_message *msg,
			     const struct eigrp_linux_rib_route *route,
			     bool remove)
{
	struct nlmsghdr *h = &msg->header;
	const size_t size = sizeof(*msg);
	const size_t address_bytes =
		eigrp_linux_rib_bytes(route->prefix.address.afi);
	uint32_t priority = route->priority;
	size_t i;

	memset(msg, 0, sizeof(*msg));
	h->nlmsg_len = NLMSG_LENGTH(sizeof(struct rtmsg));
	h->nlmsg_type = remove ? RTM_DELROUTE : RTM_NEWROUTE;
	h->nlmsg_flags = NLM_F_REQUEST | NLM_F_ACK;
	msg->route.rtm_family =
		eigrp_linux_rib_family(route->prefix.address.afi);
	msg->route.rtm_dst_len = route->prefix.prefix_length;
	msg->route.rtm_table = RT_TABLE_MAIN;
	msg->route.rtm_protocol = RTPROT_EIGRP;
	msg->route.rtm_type = RTN_UNICAST;
	msg->route.rtm_scope = RT_SCOPE_UNIVERSE;
	if (route->prefix.prefix_length
	    && !eigrp_linux_rib_attr(h, size, RTA_DST,
				     route->prefix.address.bytes,
				     address_bytes))
		return false;
	if (!eigrp_linux_rib_attr(h, size, RTA_PRIORITY, &priority,
				  sizeof(priority)))
		return false;
	if (remove)
		return true;
	if (route->count == 1) {
		const eigrp_rib_nexthop_t *nh = &route->nexthops[0];
		uint32_t ifindex = nh->ifindex;

		if (!eigrp_linux_rib_attr(h, size, RTA_OIF, &ifindex,
					  sizeof(ifindex)))
			return false;
		if (nh->gateway_present
		    && !eigrp_linux_rib_attr(h, size, RTA_GATEWAY,
					     nh->gateway.bytes, address_bytes))
			return false;
		return true;
	}
	/* Build a single RTA_MULTIPATH payload with rtnexthop-aligned entries.
	 */
	{
		unsigned char payload[EIGRP_LINUX_RIB_MESSAGE_MAX] = {0};
		size_t used = 0;

		for (i = 0; i < route->count; i++) {
			const eigrp_rib_nexthop_t *nh = &route->nexthops[i];
			struct rtnexthop *hop;
			size_t space = RTNH_ALIGN(sizeof(*hop));
			if (space > sizeof(payload) - used)
				return false;
			hop = (struct rtnexthop *)(payload + used);
			hop->rtnh_len = sizeof(*hop);
			hop->rtnh_ifindex = (int)nh->ifindex;
			hop->rtnh_hops = (uint8_t)(nh->weight - 1);
			if (nh->gateway_present) {
				struct rtattr *gw;
				if (RTA_SPACE(address_bytes)
				    > sizeof(payload) - used - space)
					return false;
				gw = (struct rtattr *)(payload + used + space);
				gw->rta_type = RTA_GATEWAY;
				gw->rta_len = RTA_LENGTH(address_bytes);
				memcpy(RTA_DATA(gw), nh->gateway.bytes,
				       address_bytes);
				hop->rtnh_len =
					space + RTA_SPACE(address_bytes);
			}
			used += RTNH_ALIGN(hop->rtnh_len);
		}
		return eigrp_linux_rib_attr(h, size, RTA_MULTIPATH, payload,
					    used);
	}
}

/* The socket is synchronous. Accept replies only from the kernel and the
 * matching request sequence; handle multipart dumps and reject truncation. */
static int eigrp_linux_rib_exchange(struct nlmsghdr *request,
				    bool (*visit)(const struct nlmsghdr *,
						  void *),
				    void *context)
{
	struct sockaddr_nl kernel = {.nl_family = AF_NETLINK};
	unsigned char buffer[EIGRP_LINUX_RIB_RECEIVE_MAX];
	struct iovec outbound = {.iov_base = request,
				 .iov_len = request->nlmsg_len};
	struct msghdr send_header = {.msg_name = &kernel,
				     .msg_namelen = sizeof(kernel),
				     .msg_iov = &outbound,
				     .msg_iovlen = 1};
	uint32_t seq;

	if (!eigrp_linux_rib_socket_open())
		return -errno;
	seq = ++rib.sequence;
	request->nlmsg_seq = seq;
	if (sendmsg(rib.fd, &send_header, 0) < 0) {
		int saved = errno;
		eigrp_linux_rib_socket_close();
		return -saved;
	}
	for (;;) {
		struct pollfd wait = {.fd = rib.fd, .events = POLLIN};
		struct sockaddr_nl sender;
		struct iovec iov = {.iov_base = buffer,
				    .iov_len = sizeof(buffer)};
		struct msghdr recv_header = {.msg_name = &sender,
					     .msg_namelen = sizeof(sender),
					     .msg_iov = &iov,
					     .msg_iovlen = 1};
		struct nlmsghdr *h;
		ssize_t received;
		int remaining, ready;

		ready = poll(&wait, 1, EIGRP_LINUX_RIB_TIMEOUT);
		if (ready <= 0 || !(wait.revents & POLLIN)) {
			int saved = ready < 0 ? errno : ETIMEDOUT;
			eigrp_linux_rib_socket_close();
			return -saved;
		}
		received = recvmsg(rib.fd, &recv_header, 0);
		if (received <= 0 || (recv_header.msg_flags & MSG_TRUNC)
		    || recv_header.msg_namelen != sizeof(sender)
		    || sender.nl_pid != 0) {
			eigrp_linux_rib_socket_close();
			return -EIO;
		}
		remaining = (int)received;
		for (h = (struct nlmsghdr *)buffer; NLMSG_OK(h, remaining);
		     h = NLMSG_NEXT(h, remaining)) {
			if (h->nlmsg_seq != seq || h->nlmsg_pid != rib.port)
				continue;
			if (h->nlmsg_type == NLMSG_ERROR) {
				struct nlmsgerr error;
				if (h->nlmsg_len < NLMSG_LENGTH(sizeof(error)))
					return -EIO;
				memcpy(&error, NLMSG_DATA(h), sizeof(error));
				return error.error;
			}
			if (h->nlmsg_type == NLMSG_DONE)
				return (h->nlmsg_flags & NLM_F_DUMP_INTR)
					       ? -EAGAIN
					       : 0;
			if (visit && !visit(h, context))
				return -EBADMSG;
		}
		if (remaining)
			return -EBADMSG;
	}
}

/* A full family dump is the safe alternative to RTM_GETROUTE, which performs
 * a longest-prefix lookup and does not identify a particular RIB entry. */
struct eigrp_linux_rib_lookup {
	struct eigrp_linux_rib_route key;
	struct eigrp_linux_rib_route found;
	bool present;
	bool conflict;
	bool other_eigrp;
};

static bool eigrp_linux_rib_decode(const struct nlmsghdr *h,
				   struct eigrp_linux_rib_lookup *lookup)
{
	const struct rtmsg *msg;
	struct eigrp_linux_rib_route decoded = {0};
	eigrp_rib_nexthop_t nexthops[EIGRP_LINUX_RIB_NEXTHOPS_MAX] = {0};
	uint8_t destination[16] = {0};
	uint32_t table, priority = 0, ifindex = 0;
	const unsigned char *gateway = NULL;
	const struct rtattr *multipath = NULL;
	int remaining;
	struct rtattr *attr;
	size_t address_bytes;

	if (h->nlmsg_type != RTM_NEWROUTE)
		return true;
	if (h->nlmsg_len < NLMSG_LENGTH(sizeof(*msg)))
		return false;
	msg = NLMSG_DATA(h);
	if (msg->rtm_family
		    != eigrp_linux_rib_family(lookup->key.prefix.address.afi)
	    || msg->rtm_dst_len != lookup->key.prefix.prefix_length
	    || msg->rtm_type != RTN_UNICAST)
		return true;
	table = msg->rtm_table;
	address_bytes = eigrp_linux_rib_bytes(lookup->key.prefix.address.afi);
	remaining = RTM_PAYLOAD(h);
	for (attr = RTM_RTA(msg); RTA_OK(attr, remaining);
	     attr = RTA_NEXT(attr, remaining)) {
		switch (attr->rta_type) {
		case RTA_DST:
			if (RTA_PAYLOAD(attr) != address_bytes)
				return false;
			memcpy(destination, RTA_DATA(attr), address_bytes);
			break;
		case RTA_TABLE:
			if (RTA_PAYLOAD(attr) != sizeof(table))
				return false;
			memcpy(&table, RTA_DATA(attr), sizeof(table));
			break;
		case RTA_PRIORITY:
			if (RTA_PAYLOAD(attr) != sizeof(priority))
				return false;
			memcpy(&priority, RTA_DATA(attr), sizeof(priority));
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
		default:
			break;
		}
	}
	if (remaining)
		return false;
	if (table != RT_TABLE_MAIN
	    || memcmp(destination, lookup->key.prefix.address.bytes,
		      address_bytes))
		return true;
	if (priority != lookup->key.priority) {
		/* A stale route at a different priority can still win Linux FIB
		 * selection. Do not silently install alongside an unknown
		 * writer. */
		if (msg->rtm_protocol == RTPROT_EIGRP)
			lookup->other_eigrp = true;
		return true;
	}
	/* Same prefix+priority is a conflict even if another process used the
	 * EIGRP protocol identifier. Never adopt an unexpected route. */
	if (lookup->present) {
		lookup->conflict = true;
		return true;
	}
	lookup->present = true;
	if (msg->rtm_protocol != RTPROT_EIGRP
	    || msg->rtm_scope != RT_SCOPE_UNIVERSE)
		lookup->conflict = true;
	decoded.prefix = lookup->key.prefix;
	decoded.priority = priority;
	decoded.nexthops = nexthops;
	if (multipath) {
		int left = RTA_PAYLOAD(multipath);
		struct rtnexthop *hop;

		for (hop = RTA_DATA(multipath); RTNH_OK(hop, left);
		     hop = RTNH_NEXT(hop)) {
			eigrp_rib_nexthop_t *nh;
			int attrs = hop->rtnh_len - sizeof(*hop);
			struct rtattr *sub;
			if (decoded.count == EIGRP_LINUX_RIB_NEXTHOPS_MAX
			    || hop->rtnh_ifindex <= 0 || attrs < 0)
				return false;
			nh = &decoded.nexthops[decoded.count++];
			nh->ifindex = (uint32_t)hop->rtnh_ifindex;
			nh->weight = (uint64_t)hop->rtnh_hops + 1;
			for (sub = RTNH_DATA(hop); RTA_OK(sub, attrs);
			     sub = RTA_NEXT(sub, attrs)) {
				if (sub->rta_type != RTA_GATEWAY)
					continue;
				if (RTA_PAYLOAD(sub) != address_bytes)
					return false;
				nh->gateway_present = true;
				nh->gateway.afi = decoded.prefix.address.afi;
				memcpy(nh->gateway.bytes, RTA_DATA(sub),
				       address_bytes);
			}
			if (attrs)
				return false;
			left -= RTNH_ALIGN(hop->rtnh_len);
		}
		if (left)
			return false;
	} else if (ifindex) {
		eigrp_rib_nexthop_t *nh = &decoded.nexthops[0];
		decoded.count = 1;
		nh->ifindex = ifindex;
		nh->weight = 1;
		if (gateway) {
			nh->gateway_present = true;
			nh->gateway.afi = decoded.prefix.address.afi;
			memcpy(nh->gateway.bytes, gateway, address_bytes);
		}
	}
	lookup->found = decoded;
	lookup->found.nexthops = calloc(decoded.count ? decoded.count : 1,
					sizeof(*lookup->found.nexthops));
	if (!lookup->found.nexthops)
		return false;
	memcpy(lookup->found.nexthops, nexthops,
	       decoded.count * sizeof(*nexthops));
	return true;
}

static bool eigrp_linux_rib_visit(const struct nlmsghdr *h, void *context)
{
	return eigrp_linux_rib_decode(h, context);
}

static int eigrp_linux_rib_lookup(const struct eigrp_linux_rib_route *key,
				  struct eigrp_linux_rib_lookup *result)
{
	struct eigrp_linux_rib_message message = {0};

	result->key = *key;
	memset(&result->found, 0, sizeof(result->found));
	result->present = false;
	result->conflict = false;
	result->other_eigrp = false;
	message.header.nlmsg_len = NLMSG_LENGTH(sizeof(struct rtmsg));
	message.header.nlmsg_type = RTM_GETROUTE;
	message.header.nlmsg_flags = NLM_F_REQUEST | NLM_F_DUMP;
	message.route.rtm_family =
		eigrp_linux_rib_family(key->prefix.address.afi);
	return eigrp_linux_rib_exchange(&message.header, eigrp_linux_rib_visit,
					result);
}

static int
eigrp_linux_rib_kernel_change(const struct eigrp_linux_rib_route *route,
			      bool remove)
{
	struct eigrp_linux_rib_message message;

	if (!eigrp_linux_rib_message_make(&message, route, remove))
		return -EMSGSIZE;
	if (!remove)
		message.header.nlmsg_flags |= NLM_F_CREATE | NLM_F_EXCL;
	return eigrp_linux_rib_exchange(&message.header, NULL, NULL);
}

static struct eigrp_linux_rib_entry *
eigrp_linux_rib_find(const eigrp_instance_t *instance,
		     const eigrp_prefix_t *prefix)
{
	struct eigrp_linux_rib_entry *entry;

	for (entry = rib.entries; entry; entry = entry->next)
		if (entry->instance == instance
		    && eigrp_linux_rib_prefix_equal(&entry->desired.prefix,
						    prefix))
			return entry;
	return NULL;
}

static void eigrp_linux_rib_entry_remove(struct eigrp_linux_rib_entry *entry)
{
	struct eigrp_linux_rib_entry **cursor;

	for (cursor = &rib.entries; *cursor; cursor = &(*cursor)->next)
		if (*cursor == entry) {
			*cursor = entry->next;
			if (entry->desired.nexthops
			    != entry->installed.nexthops)
				free(entry->desired.nexthops);
			free(entry->installed.nexthops);
			free(entry);
			return;
		}
}

static eigrp_result_t eigrp_linux_rib_result(int error)
{
	if (!error || error == -ESRCH || error == -ENOENT)
		return EIGRP_RESULT_SUCCESS;
	if (error == -EEXIST || error == -EPERM || error == -EACCES)
		return error == -EEXIST ? EIGRP_RESULT_CONFLICT
					: EIGRP_RESULT_INTERNAL_FAILURE;
	return EIGRP_RESULT_INTERNAL_FAILURE;
}

/* Lock held. Revalidate an installed route before deletion. An externally
 * modified or replaced route is never removed, even if it uses protocol 192. */
static eigrp_result_t eigrp_linux_rib_apply(struct eigrp_linux_rib_entry *entry)
{
	struct eigrp_linux_rib_lookup existing;
	int status;

	if (entry->owned) {
		status = eigrp_linux_rib_lookup(&entry->installed, &existing);
		if (status)
			return eigrp_linux_rib_result(status);
		if (!existing.present)
			entry->owned = false;
		else if (existing.conflict || existing.other_eigrp
			 || !eigrp_linux_rib_route_equal(&entry->installed,
							 &existing.found)) {
			free(existing.found.nexthops);
			entry->owned = false;
			return EIGRP_RESULT_CONFLICT;
		}
		free(existing.found.nexthops);
	}
	if (!entry->want) {
		if (!entry->owned)
			return EIGRP_RESULT_SUCCESS;
		status = eigrp_linux_rib_kernel_change(&entry->installed, true);
		if (!status || status == -ESRCH || status == -ENOENT)
			entry->owned = false;
		return eigrp_linux_rib_result(status);
	}
	if (entry->owned
	    && eigrp_linux_rib_route_equal(&entry->installed,
					   &entry->desired)) {
		if (entry->desired.nexthops != entry->installed.nexthops)
			free(entry->desired.nexthops);
		entry->desired = entry->installed;
		return EIGRP_RESULT_SUCCESS;
	}
	if (entry->owned) {
		/* Do not use NLM_F_REPLACE: it may overwrite someone else's
		 * route. If the replacement fails, restore the old route. */
		status = eigrp_linux_rib_kernel_change(&entry->installed, true);
		if (status && status != -ENOENT && status != -ESRCH)
			return eigrp_linux_rib_result(status);
		entry->owned = false;
		status = eigrp_linux_rib_kernel_change(&entry->desired, false);
		if (status) {
			int restored = eigrp_linux_rib_kernel_change(
				&entry->installed, false);
			entry->owned = restored == 0;
			return eigrp_linux_rib_result(status);
		}
	} else {
		/* The kernel permits two routes with equal prefix but different
		 * priority. Refuse an unknown protocol-192 route at *any*
		 * priority, even after a crash and metric change. */
		status = eigrp_linux_rib_lookup(&entry->desired, &existing);
		if (status)
			return eigrp_linux_rib_result(status);
		if (existing.present || existing.other_eigrp) {
			free(existing.found.nexthops);
			return EIGRP_RESULT_CONFLICT;
		}
		status = eigrp_linux_rib_kernel_change(&entry->desired, false);
	}
	/* NLM_F_EXCL rejects even byte-identical routes belonging to another
	 * daemon. After a crash an operator must clear verified stale entries;
	 * adopting them silently could violate cross-process ownership. */
	if (status)
		return eigrp_linux_rib_result(status);
	if (entry->installed.nexthops != entry->desired.nexthops)
		free(entry->installed.nexthops);
	entry->installed = entry->desired;
	entry->owned = true;
	return EIGRP_RESULT_SUCCESS;
}

void eigrp_rib_init(void)
{
	pthread_mutex_lock(&rib_lock);
	(void)eigrp_linux_rib_socket_open();
	pthread_mutex_unlock(&rib_lock);
}

void eigrp_rib_finish(void)
{
	struct eigrp_linux_rib_entry *entry;

	eigrp_linux_redist_stop();

	pthread_mutex_lock(&rib_lock);
	for (entry = rib.entries; entry; entry = entry->next) {
		entry->want = false;
		(void)eigrp_linux_rib_apply(entry);
	}
	while ((entry = rib.entries) != NULL)
		eigrp_linux_rib_entry_remove(entry);
	eigrp_linux_rib_socket_close();
	pthread_mutex_unlock(&rib_lock);
}

void eigrp_rib_instance_delete(eigrp_instance_t *eigrp)
{
	struct eigrp_linux_rib_entry *entry, *next;

	eigrp_linux_redist_instance_delete(eigrp);

	pthread_mutex_lock(&rib_lock);
	for (entry = rib.entries; entry; entry = next) {
		next = entry->next;
		if (entry->instance != eigrp)
			continue;
		entry->want = false;
		(void)eigrp_linux_rib_apply(entry);
		/* Failure retains the pending withdrawal, without dereferencing
		 * the destroyed instance during a later reconciliation. */
		if (!entry->owned)
			eigrp_linux_rib_entry_remove(entry);
		else
			entry->instance = NULL;
	}
	pthread_mutex_unlock(&rib_lock);
}

eigrp_result_t eigrp_rib_route_add(eigrp_instance_t *eigrp,
				   const eigrp_rib_route_t *route)
{
	struct eigrp_linux_rib_route copy;
	struct eigrp_linux_rib_entry *entry;
	eigrp_result_t result;

	if (!eigrp || !route)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	if (eigrp_instance_vrf_id(eigrp) != EIGRP_VRF_DEFAULT
	    || eigrp_instance_afi(eigrp) != route->prefix.address.afi)
		return EIGRP_RESULT_UNSUPPORTED;
	result = eigrp_linux_rib_route_copy(&copy, route);
	if (result != EIGRP_RESULT_SUCCESS)
		return result;
	pthread_mutex_lock(&rib_lock);
	entry = eigrp_linux_rib_find(eigrp, &copy.prefix);
	if (!entry) {
		entry = calloc(1, sizeof(*entry));
		if (!entry) {
			free(copy.nexthops);
			pthread_mutex_unlock(&rib_lock);
			return EIGRP_RESULT_INTERNAL_FAILURE;
		}
		entry->instance = eigrp;
		entry->next = rib.entries;
		rib.entries = entry;
	}
	if (entry->desired.nexthops != entry->installed.nexthops)
		free(entry->desired.nexthops);
	entry->desired = copy;
	entry->want = true;
	result = eigrp_linux_rib_apply(entry);
	pthread_mutex_unlock(&rib_lock);
	return result;
}

eigrp_result_t eigrp_rib_route_del(eigrp_instance_t *eigrp,
				   const eigrp_prefix_t *prefix)
{
	struct eigrp_linux_rib_entry *entry;
	eigrp_prefix_t key;
	eigrp_result_t result;

	if (!eigrp || !eigrp_linux_rib_prefix_valid(prefix))
		return EIGRP_RESULT_INVALID_ARGUMENT;
	if (eigrp_instance_vrf_id(eigrp) != EIGRP_VRF_DEFAULT
	    || eigrp_instance_afi(eigrp) != prefix->address.afi)
		return EIGRP_RESULT_UNSUPPORTED;
	key = *prefix;
	eigrp_linux_rib_prefix_mask(&key);
	pthread_mutex_lock(&rib_lock);
	entry = eigrp_linux_rib_find(eigrp, &key);
	if (!entry) {
		pthread_mutex_unlock(&rib_lock);
		return EIGRP_RESULT_NOT_FOUND;
	}
	entry->want = false;
	result = eigrp_linux_rib_apply(entry);
	if (!entry->owned)
		eigrp_linux_rib_entry_remove(entry);
	pthread_mutex_unlock(&rib_lock);
	return result;
}

eigrp_result_t eigrp_linux_rib_reconcile(void)
{
	struct eigrp_linux_rib_entry *entry, *next;
	eigrp_result_t result = EIGRP_RESULT_SUCCESS;

	pthread_mutex_lock(&rib_lock);
	for (entry = rib.entries; entry; entry = next) {
		eigrp_result_t current;
		next = entry->next;
		current = eigrp_linux_rib_apply(entry);
		if (current != EIGRP_RESULT_SUCCESS)
			result = current;
		if (!entry->want && !entry->owned)
			eigrp_linux_rib_entry_remove(entry);
	}
	pthread_mutex_unlock(&rib_lock);
	return result;
}

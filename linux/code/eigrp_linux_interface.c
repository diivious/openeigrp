// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Linux RTNETLINK link/address discovery for the public EIGRP host contract.
 * Copyright (C) 2026 Donnie V. Savage
 *
 * Lifecycle and receive processing are host-driven; AF threads may walk the
 * inventory concurrently. Only copied public values enter the portable core.
 */
#include "eigrp_linux_interface.h"

#include <errno.h>
#include <limits.h>
#include <linux/if_addr.h>
#include <linux/if_link.h>
#include <linux/netlink.h>
#include <linux/rtnetlink.h>
#include <net/if.h>
#include <poll.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>
#include <sys/socket.h>
#include <unistd.h>

#include "eigrp_sys.h"

#define EIGRP_LINUX_RECV_SIZE (64U * 1024U)
#define EIGRP_LINUX_DUMP_TIMEOUT 5000
#define EIGRP_LINUX_PENDING_LIMIT (4U * 1024U * 1024U)
#define EIGRP_LINUX_DEFAULT_BANDWIDTH 1000000U /* Kbit/s */

struct eigrp_linux_addr {
	eigrp_prefix_t prefix;
	bool secondary;
	struct eigrp_linux_addr *next;
};

struct eigrp_linux_link {
	eigrp_ifindex_t ifindex;
	char name[IFNAMSIZ];
	char kind[32];
	uint32_t master;
	uint32_t mtu;
	uint32_t bandwidth;
	uint64_t generation;
	unsigned int flags;
	struct eigrp_linux_addr *addresses;
	struct eigrp_linux_link *next;
};

struct eigrp_linux_message {
	struct eigrp_linux_message *next;
	size_t size;
	unsigned char bytes[];
};

struct eigrp_linux_inventory {
	int fd;
	uint32_t port_id;
	uint32_t sequence;
	uint64_t generation;
	struct eigrp_linux_link *links;
};

static struct eigrp_linux_inventory inventory = {.fd = -1};
/* The core can walk from AF threads while the host pumps Netlink events.
 * A separate pump lock prevents two host reads/resyncs from interleaving. */
static pthread_mutex_t inventory_lock = PTHREAD_MUTEX_INITIALIZER;
static pthread_mutex_t pump_lock = PTHREAD_MUTEX_INITIALIZER;

static void eigrp_linux_addr_free(struct eigrp_linux_addr *addr)
{
	while (addr) {
		struct eigrp_linux_addr *next = addr->next;
		free(addr);
		addr = next;
	}
}

static void eigrp_linux_link_free(struct eigrp_linux_link *link)
{
	while (link) {
		struct eigrp_linux_link *next = link->next;
		eigrp_linux_addr_free(link->addresses);
		free(link);
		link = next;
	}
}

static struct eigrp_linux_link *
eigrp_linux_link_find(struct eigrp_linux_inventory *state, uint32_t ifindex)
{
	struct eigrp_linux_link *link;

	for (link = state->links; link; link = link->next)
		if (link->ifindex == ifindex)
			return link;
	return NULL;
}

static const struct eigrp_linux_link *
eigrp_linux_link_lookup(const struct eigrp_linux_inventory *state,
			uint32_t ifindex)
{
	const struct eigrp_linux_link *link;

	for (link = state->links; link; link = link->next)
		if (link->ifindex == ifindex)
			return link;
	return NULL;
}

static struct eigrp_linux_link *
eigrp_linux_link_create(struct eigrp_linux_inventory *state, uint32_t ifindex)
{
	struct eigrp_linux_link *link = calloc(1, sizeof(*link));

	if (!link)
		return NULL;
	link->ifindex = ifindex;
	link->generation = ++state->generation;
	link->bandwidth = EIGRP_LINUX_DEFAULT_BANDWIDTH;
	link->next = state->links;
	state->links = link;
	return link;
}

static struct eigrp_linux_inventory *
eigrp_linux_inventory_copy(const struct eigrp_linux_inventory *old)
{
	struct eigrp_linux_inventory *copy = calloc(1, sizeof(*copy));
	const struct eigrp_linux_link *link;
	struct eigrp_linux_link **tail;

	if (!copy)
		return NULL;
	copy->fd = -1;
	copy->generation = old->generation;
	tail = &copy->links;
	for (link = old->links; link; link = link->next) {
		const struct eigrp_linux_addr *addr;
		struct eigrp_linux_addr **addr_tail;

		*tail = calloc(1, sizeof(**tail));
		if (!*tail)
			goto failure;
		(*tail)->ifindex = link->ifindex;
		memcpy((*tail)->name, link->name, sizeof(link->name));
		memcpy((*tail)->kind, link->kind, sizeof(link->kind));
		(*tail)->master = link->master;
		(*tail)->mtu = link->mtu;
		(*tail)->bandwidth = link->bandwidth;
		(*tail)->generation = link->generation;
		(*tail)->flags = link->flags;
		addr_tail = &(*tail)->addresses;
		for (addr = link->addresses; addr; addr = addr->next) {
			*addr_tail = calloc(1, sizeof(**addr_tail));
			if (!*addr_tail)
				goto failure;
			(*addr_tail)->prefix = addr->prefix;
			(*addr_tail)->secondary = addr->secondary;
			addr_tail = &(*addr_tail)->next;
		}
		tail = &(*tail)->next;
	}
	return copy;

failure:
	eigrp_linux_link_free(copy->links);
	free(copy);
	return NULL;
}

static bool eigrp_linux_prefix_match(const eigrp_prefix_t *left,
				     const eigrp_prefix_t *right)
{
	return left->address.afi == right->address.afi
	       && left->prefix_length == right->prefix_length
	       && memcmp(left->address.bytes, right->address.bytes,
			 sizeof(left->address.bytes))
			  == 0;
}

static const struct eigrp_linux_addr *
eigrp_linux_addr_find(const struct eigrp_linux_link *link,
		      const eigrp_prefix_t *prefix)
{
	const struct eigrp_linux_addr *addr;

	for (addr = link->addresses; addr; addr = addr->next)
		if (eigrp_linux_prefix_match(&addr->prefix, prefix))
			return addr;
	return NULL;
}

/* A VRF device or an indirect slave of a VRF is not in the default VRF.
 * Ordinary bridge and bond hierarchies remain eligible. */
static bool eigrp_linux_default_vrf(const struct eigrp_linux_inventory *state,
				    const struct eigrp_linux_link *link)
{
	const struct eigrp_linux_link *master;

	if (!link->name[0] || !link->mtu || strcmp(link->kind, "vrf") == 0)
		return false;
	/* A bridge or bond may itself be a slave of a VRF.  Follow the
	 * master chain, bounding traversal against malformed cycles. */
	master = link;
	for (unsigned int hops = 0; master && hops < 256; hops++) {
		if (strcmp(master->kind, "vrf") == 0)
			return false;
		if (!master->master)
			return true;
		master = eigrp_linux_link_lookup(state, master->master);
	}
	return master == NULL;
}

static uint32_t eigrp_linux_bandwidth_read(const char *name)
{
	char path[sizeof("/sys/class/net//speed") + IFNAMSIZ];
	unsigned long long mbps;
	FILE *input;
	int n;

	/* Kernel interface names have no '/', but reject it for safe sysfs
	 * paths. */
	if (strchr(name, '/'))
		return EIGRP_LINUX_DEFAULT_BANDWIDTH;
	n = snprintf(path, sizeof(path), "/sys/class/net/%s/speed", name);
	if (n < 0 || (size_t)n >= sizeof(path))
		return EIGRP_LINUX_DEFAULT_BANDWIDTH;
	input = fopen(path, "r");
	if (!input)
		return EIGRP_LINUX_DEFAULT_BANDWIDTH;
	if (fscanf(input, "%llu", &mbps) != 1 || mbps == 0
	    || mbps > UINT32_MAX / 1000U)
		mbps = EIGRP_LINUX_DEFAULT_BANDWIDTH / 1000U;
	fclose(input);
	return (uint32_t)(mbps * 1000U);
}

/* Parse bounded attributes, not raw pointer casts into an untrusted frame. */
static bool eigrp_linux_attrs_valid(const struct rtattr *attr, int length)
{
	while (RTA_OK(attr, length)) {
		attr = RTA_NEXT(attr, length);
	}
	return length == 0;
}

static bool eigrp_linux_link_apply(struct eigrp_linux_inventory *state,
				   const struct nlmsghdr *header)
{
	const struct ifinfomsg *info;
	const struct rtattr *attr;
	struct eigrp_linux_link *link;
	int remaining;
	uint32_t mtu = 0, master = 0;
	char name[IFNAMSIZ] = {0}, kind[32] = {0};
	bool got_name = false, got_mtu = false, got_master = false;

	if (header->nlmsg_len < NLMSG_LENGTH(sizeof(*info)))
		return false;
	info = NLMSG_DATA(header);
	if (info->ifi_index <= 0)
		return false;
	if (header->nlmsg_type == RTM_DELLINK) {
		struct eigrp_linux_link **cursor = &state->links;

		while (*cursor
		       && (*cursor)->ifindex != (uint32_t)info->ifi_index)
			cursor = &(*cursor)->next;
		if (*cursor) {
			link = *cursor;
			*cursor = link->next;
			link->next = NULL;
			eigrp_linux_link_free(link);
		}
		return true;
	}
	remaining = (int)IFLA_PAYLOAD(header);
	attr = IFLA_RTA(info);
	if (!eigrp_linux_attrs_valid(attr, remaining))
		return false;
	for (; RTA_OK(attr, remaining); attr = RTA_NEXT(attr, remaining)) {
		switch (attr->rta_type & NLA_TYPE_MASK) {
		case IFLA_IFNAME: {
			const char *raw = RTA_DATA(attr);
			size_t len = RTA_PAYLOAD(attr);

			if (!len || len > sizeof(name)
			    || !memchr(raw, '\0', len))
				return false;
			memcpy(name, raw, len);
			got_name = true;
			break;
		}
		case IFLA_MTU:
			if (RTA_PAYLOAD(attr) != sizeof(mtu))
				return false;
			memcpy(&mtu, RTA_DATA(attr), sizeof(mtu));
			got_mtu = true;
			break;
		case IFLA_MASTER:
			if (RTA_PAYLOAD(attr) != sizeof(master))
				return false;
			memcpy(&master, RTA_DATA(attr), sizeof(master));
			got_master = true;
			break;
		case IFLA_LINKINFO: {
			const struct rtattr *sub = RTA_DATA(attr);
			int rest = RTA_PAYLOAD(attr);

			if (!eigrp_linux_attrs_valid(sub, rest))
				return false;
			for (; RTA_OK(sub, rest); sub = RTA_NEXT(sub, rest)) {
				if ((sub->rta_type & NLA_TYPE_MASK)
				    != IFLA_INFO_KIND)
					continue;
				if (!RTA_PAYLOAD(sub)
				    || RTA_PAYLOAD(sub) > sizeof(kind)
				    || !memchr(RTA_DATA(sub), '\0',
					       RTA_PAYLOAD(sub)))
					return false;
				memcpy(kind, RTA_DATA(sub), RTA_PAYLOAD(sub));
			}
			break;
		}
		}
	}
	link = eigrp_linux_link_find(state, (uint32_t)info->ifi_index);
	if (!link) {
		link = eigrp_linux_link_create(state,
					       (uint32_t)info->ifi_index);
		if (!link)
			return false;
	}
	if (got_name) {
		memcpy(link->name, name, sizeof(link->name));
		link->bandwidth = eigrp_linux_bandwidth_read(name);
	}
	if (got_mtu)
		link->mtu = mtu;
	if (got_master)
		link->master = master;
	if (kind[0])
		memcpy(link->kind, kind, sizeof(kind));
	link->flags = info->ifi_flags;
	return true;
}

static bool eigrp_linux_addr_apply(struct eigrp_linux_inventory *state,
				   const struct nlmsghdr *header)
{
	const struct ifaddrmsg *info;
	const struct rtattr *attr;
	const void *local = NULL, *peer = NULL;
	size_t address_size;
	int remaining;
	uint32_t flags;
	struct eigrp_linux_link *link;
	struct eigrp_linux_addr **cursor, *entry;
	eigrp_prefix_t prefix = {0};
	bool secondary;

	if (header->nlmsg_len < NLMSG_LENGTH(sizeof(*info)))
		return false;
	info = NLMSG_DATA(header);
	if (!info->ifa_index)
		return false;
	if (info->ifa_family == AF_INET) {
		prefix.address.afi = EIGRP_AFI_IPV4;
		address_size = 4;
	} else if (info->ifa_family == AF_INET6) {
		prefix.address.afi = EIGRP_AFI_IPV6;
		address_size = 16;
	} else {
		return true;
	}
	if (info->ifa_prefixlen > address_size * 8)
		return false;
	prefix.prefix_length = info->ifa_prefixlen;
	flags = info->ifa_flags;
	remaining = IFA_PAYLOAD(header);
	attr = IFA_RTA(info);
	if (!eigrp_linux_attrs_valid(attr, remaining))
		return false;
	for (; RTA_OK(attr, remaining); attr = RTA_NEXT(attr, remaining)) {
		switch (attr->rta_type & NLA_TYPE_MASK) {
		case IFA_LOCAL:
			if (RTA_PAYLOAD(attr) != address_size)
				return false;
			local = RTA_DATA(attr);
			break;
		case IFA_ADDRESS:
			if (RTA_PAYLOAD(attr) != address_size)
				return false;
			peer = RTA_DATA(attr);
			break;
		case IFA_FLAGS:
			if (RTA_PAYLOAD(attr) != sizeof(flags))
				return false;
			memcpy(&flags, RTA_DATA(attr), sizeof(flags));
			break;
		}
	}
	if (!local)
		local = peer;
	if (!local)
		return false;
	memcpy(prefix.address.bytes, local, address_size);
	link = eigrp_linux_link_find(state, info->ifa_index);
	if (!link) {
		if (header->nlmsg_type == RTM_DELADDR)
			return true;
		link = eigrp_linux_link_create(state, info->ifa_index);
		if (!link)
			return false;
	}
	cursor = &link->addresses;
	while (*cursor
	       && !eigrp_linux_prefix_match(&(*cursor)->prefix, &prefix))
		cursor = &(*cursor)->next;
	if (header->nlmsg_type == RTM_DELADDR
	    || (flags & (IFA_F_TENTATIVE | IFA_F_DADFAILED))) {
		if (*cursor) {
			entry = *cursor;
			*cursor = entry->next;
			free(entry);
		}
		return true;
	}
	secondary = info->ifa_family == AF_INET && (flags & IFA_F_SECONDARY);
	if (*cursor) {
		(*cursor)->secondary = secondary;
		return true;
	}
	entry = calloc(1, sizeof(*entry));
	if (!entry)
		return false;
	entry->prefix = prefix;
	entry->secondary = secondary;
	entry->next = link->addresses;
	link->addresses = entry;
	return true;
}

static bool eigrp_linux_message_apply(struct eigrp_linux_inventory *state,
				      const struct nlmsghdr *header)
{
	switch (header->nlmsg_type) {
	case RTM_NEWLINK:
	case RTM_DELLINK:
		return eigrp_linux_link_apply(state, header);
	case RTM_NEWADDR:
	case RTM_DELADDR:
		return eigrp_linux_addr_apply(state, header);
	default:
		return true;
	}
}

static void eigrp_linux_state_fill(const struct eigrp_linux_link *link,
				   const struct eigrp_linux_addr *addr,
				   eigrp_intf_runtime_state_t *state)
{
	memset(state, 0, sizeof(*state));
	state->interface_name = link->name;
	state->ifindex = link->ifindex;
	state->address = addr->prefix;
	state->secondary = addr->secondary;
	state->operative = (link->flags & (IFF_UP | IFF_RUNNING))
			   == (IFF_UP | IFF_RUNNING);
	state->bandwidth = link->bandwidth;
	state->mtu = link->mtu;
	/* The public 'type' is a portable EIGRP property, not ARPHRD_*. */
	state->type = 0;
}

static void eigrp_linux_link_publish(const struct eigrp_linux_link *link)
{
	const struct eigrp_linux_addr *addr;
	eigrp_intf_runtime_state_t state;

	for (addr = link->addresses; addr; addr = addr->next) {
		eigrp_linux_state_fill(link, addr, &state);
		eigrp_sys_intf_update(EIGRP_VRF_DEFAULT, &state);
	}
}

/* Compare complete inventories after each receive batch, so address events
 * that precede their link notification do not expose an invalid identity. */
static void
eigrp_linux_inventory_reconcile(const struct eigrp_linux_inventory *before,
				const struct eigrp_linux_inventory *after)
{
	const struct eigrp_linux_link *old, *new_link;

	for (old = before->links; old; old = old->next) {
		const struct eigrp_linux_addr *addr;
		bool changed = false, removed_address = false;
		bool old_default = eigrp_linux_default_vrf(before, old);
		bool new_default;

		new_link = eigrp_linux_link_lookup(after, old->ifindex);
		new_default =
			new_link && eigrp_linux_default_vrf(after, new_link);
		if (!old_default)
			continue;
		if (!new_default || old->generation != new_link->generation
		    || strcmp(old->name, new_link->name) != 0) {
			eigrp_sys_intf_remove(EIGRP_VRF_DEFAULT, old->ifindex,
					      EIGRP_INTERFACE_REMOVE_HOST);
			continue;
		}
		for (addr = old->addresses; addr; addr = addr->next) {
			if (!eigrp_linux_addr_find(new_link, &addr->prefix)) {
				eigrp_sys_intf_addr_update(
					EIGRP_VRF_DEFAULT, old->ifindex,
					&addr->prefix,
					EIGRP_INTERFACE_REMOVE_HOST);
				removed_address = true;
			}
		}
		if ((old->flags & (IFF_UP | IFF_RUNNING))
			    == (IFF_UP | IFF_RUNNING)
		    && (new_link->flags & (IFF_UP | IFF_RUNNING))
			       != (IFF_UP | IFF_RUNNING)) {
			eigrp_sys_intf_down(EIGRP_VRF_DEFAULT, old->ifindex,
					    old->name, 0, new_link->bandwidth,
					    new_link->mtu);
		}
		changed = old->flags != new_link->flags
			  || old->mtu != new_link->mtu
			  || old->bandwidth != new_link->bandwidth
			  || removed_address;
		for (addr = new_link->addresses; addr; addr = addr->next) {
			const struct eigrp_linux_addr *previous =
				eigrp_linux_addr_find(old, &addr->prefix);
			if (!previous || previous->secondary != addr->secondary)
				changed = true;
		}
		if (changed)
			eigrp_linux_link_publish(new_link);
	}
	for (new_link = after->links; new_link; new_link = new_link->next) {
		const struct eigrp_linux_link *previous =
			eigrp_linux_link_lookup(before, new_link->ifindex);

		if (!eigrp_linux_default_vrf(after, new_link))
			continue;
		if (!previous || !eigrp_linux_default_vrf(before, previous)
		    || previous->generation != new_link->generation
		    || strcmp(previous->name, new_link->name) != 0)
			eigrp_linux_link_publish(new_link);
	}
}

static bool eigrp_linux_netlink_open(struct eigrp_linux_inventory *state)
{
	struct sockaddr_nl address = {.nl_family = AF_NETLINK};
	socklen_t address_size = sizeof(address);
	int buffer_size = 1024 * 1024;

	state->fd = socket(AF_NETLINK, SOCK_RAW | SOCK_NONBLOCK | SOCK_CLOEXEC,
			   NETLINK_ROUTE);
	if (state->fd < 0)
		return false;
	(void)setsockopt(state->fd, SOL_SOCKET, SO_RCVBUF, &buffer_size,
			 sizeof(buffer_size));
	address.nl_groups =
		RTMGRP_LINK | RTMGRP_IPV4_IFADDR | RTMGRP_IPV6_IFADDR;
	if (bind(state->fd, (const struct sockaddr *)&address, sizeof(address))
		    < 0
	    || getsockname(state->fd, (struct sockaddr *)&address,
			   &address_size)
		       < 0) {
		close(state->fd);
		state->fd = -1;
		return false;
	}
	state->port_id = address.nl_pid;
	return true;
}

static bool eigrp_linux_dump_request(struct eigrp_linux_inventory *state,
				     uint16_t request, uint32_t *sequence)
{
	union {
		struct {
			struct nlmsghdr header;
			struct ifinfomsg info;
		} link;
		struct {
			struct nlmsghdr header;
			struct ifaddrmsg info;
		} addr;
	} message = {0};
	struct sockaddr_nl kernel = {.nl_family = AF_NETLINK};
	struct nlmsghdr *header = &message.link.header;

	*sequence = ++state->sequence;
	header->nlmsg_type = request;
	header->nlmsg_flags = NLM_F_REQUEST | NLM_F_DUMP;
	header->nlmsg_seq = *sequence;
	header->nlmsg_pid = state->port_id;
	if (request == RTM_GETLINK) {
		header->nlmsg_len = NLMSG_LENGTH(sizeof(message.link.info));
		message.link.info.ifi_family = AF_UNSPEC;
	} else {
		header->nlmsg_len = NLMSG_LENGTH(sizeof(message.addr.info));
		message.addr.info.ifa_family = AF_UNSPEC;
	}
	return sendto(state->fd, &message, header->nlmsg_len, 0,
		      (const struct sockaddr *)&kernel, sizeof(kernel))
	       == (ssize_t)header->nlmsg_len;
}

static void eigrp_linux_message_free(struct eigrp_linux_message *message)
{
	while (message) {
		struct eigrp_linux_message *next = message->next;
		free(message);
		message = next;
	}
}

static bool eigrp_linux_message_enqueue(struct eigrp_linux_message ***tail,
					const struct nlmsghdr *header,
					size_t *pending_bytes)
{
	struct eigrp_linux_message *message;

	if (header->nlmsg_len > EIGRP_LINUX_PENDING_LIMIT - *pending_bytes)
		return false;
	message = malloc(sizeof(*message) + header->nlmsg_len);
	if (!message)
		return false;
	message->size = header->nlmsg_len;
	message->next = NULL;
	memcpy(message->bytes, header, message->size);
	**tail = message;
	*tail = &message->next;
	*pending_bytes += message->size;
	return true;
}

/* Multicast notifications may carry a nonzero sequence on Linux.  Buffer
 * messages that do not belong to the active dump and replay them in arrival
 * order after the snapshots, preventing old dump entries from resurrecting
 * deleted links or addresses. */
static int64_t eigrp_linux_monotime_msec(void)
{
	struct timespec now;

	if (clock_gettime(CLOCK_MONOTONIC, &now) < 0)
		return -1;
	return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static bool eigrp_linux_dump_receive(struct eigrp_linux_inventory *state,
				     uint32_t sequence,
				     struct eigrp_linux_message ***tail,
				     size_t *pending_bytes)
{
	unsigned char buffer[EIGRP_LINUX_RECV_SIZE];
	struct pollfd ready = {.fd = state->fd, .events = POLLIN};
	int64_t start = eigrp_linux_monotime_msec();

	if (start < 0)
		return false;
	for (;;) {
		struct sockaddr_nl sender = {0};
		struct iovec iov = {.iov_base = buffer,
				    .iov_len = sizeof(buffer)};
		struct msghdr msg = {.msg_name = &sender,
				     .msg_namelen = sizeof(sender),
				     .msg_iov = &iov,
				     .msg_iovlen = 1};
		struct nlmsghdr *header;
		ssize_t read_size;
		int remaining, timeout;
		int64_t now = eigrp_linux_monotime_msec();
		bool done = false;

		if (now < 0 || now - start >= EIGRP_LINUX_DUMP_TIMEOUT)
			return false;
		timeout = EIGRP_LINUX_DUMP_TIMEOUT - (int)(now - start);
		if (poll(&ready, 1, timeout) <= 0) {
			if (errno == EINTR)
				continue;
			return false;
		}
		if (ready.revents & (POLLERR | POLLHUP | POLLNVAL))
			return false;
		read_size = recvmsg(state->fd, &msg, MSG_DONTWAIT);
		if (read_size < 0) {
			if (errno == EINTR || errno == EAGAIN)
				continue;
			return false;
		}
		if ((msg.msg_flags & MSG_TRUNC) || !read_size
		    || sender.nl_pid != 0)
			return false;
		remaining = (int)read_size;
		for (header = (struct nlmsghdr *)buffer;
		     NLMSG_OK(header, remaining);
		     header = NLMSG_NEXT(header, remaining)) {
			/* Group delivery identifies multicast even when its
			 * nonzero sequence collides with our dump request. */
			if (sender.nl_groups || header->nlmsg_seq != sequence) {
				if ((header->nlmsg_type == RTM_NEWLINK
				     || header->nlmsg_type == RTM_DELLINK
				     || header->nlmsg_type == RTM_NEWADDR
				     || header->nlmsg_type == RTM_DELADDR)
				    && !eigrp_linux_message_enqueue(
					    tail, header, pending_bytes))
					return false;
				continue;
			}
			if (header->nlmsg_type == NLMSG_DONE) {
				if (header->nlmsg_flags & NLM_F_DUMP_INTR)
					return false;
				done = true;
				continue;
			}
			if (header->nlmsg_type == NLMSG_ERROR
			    || header->nlmsg_type == NLMSG_OVERRUN)
				return false;
			if (!eigrp_linux_message_apply(state, header))
				return false;
		}
		if (remaining)
			return false;
		if (done)
			return true;
	}
}

static bool eigrp_linux_snapshot(struct eigrp_linux_inventory *state)
{
	struct eigrp_linux_message *head = NULL, **tail = &head, *message;
	size_t pending_bytes = 0;
	uint32_t sequence;
	bool valid = false;

	state->fd = -1;
	if (!eigrp_linux_netlink_open(state))
		return false;
	if (!eigrp_linux_dump_request(state, RTM_GETLINK, &sequence)
	    || !eigrp_linux_dump_receive(state, sequence, &tail, &pending_bytes)
	    || !eigrp_linux_dump_request(state, RTM_GETADDR, &sequence)
	    || !eigrp_linux_dump_receive(state, sequence, &tail,
					 &pending_bytes))
		goto out;
	for (message = head; message; message = message->next) {
		if (!eigrp_linux_message_apply(
			    state, (const struct nlmsghdr *)message->bytes))
			goto out;
	}
	valid = true;

out:
	eigrp_linux_message_free(head);
	if (!valid) {
		close(state->fd);
		state->fd = -1;
	}
	return valid;
}

/* Caller holds pump_lock.  The new fd is subscribed before either dump,
 * and no old cache memory is released until every walker exits. */
static eigrp_result_t eigrp_linux_intf_resync(void)
{
	struct eigrp_linux_inventory next = {.fd = -1};
	struct eigrp_linux_inventory previous;
	struct eigrp_linux_link *link;

	if (!eigrp_linux_snapshot(&next)) {
		eigrp_linux_link_free(next.links);
		pthread_mutex_lock(&inventory_lock);
		if (inventory.fd >= 0)
			close(inventory.fd);
		inventory.fd = -1;
		pthread_mutex_unlock(&inventory_lock);
		return EIGRP_RESULT_INTERNAL_FAILURE;
	}
	pthread_mutex_lock(&inventory_lock);
	previous = inventory;
	inventory = next;
	pthread_mutex_unlock(&inventory_lock);

	/* Lost multicast events can hide remove+recreate of an identical index.
	 * Retire old identities before publishing replacement state. */
	for (link = previous.links; link; link = link->next)
		if (eigrp_linux_default_vrf(&previous, link))
			eigrp_sys_intf_remove(EIGRP_VRF_DEFAULT, link->ifindex,
					      EIGRP_INTERFACE_REMOVE_HOST);
	for (link = next.links; link; link = link->next)
		if (eigrp_linux_default_vrf(&next, link))
			eigrp_linux_link_publish(link);
	if (previous.fd >= 0)
		close(previous.fd);
	eigrp_linux_link_free(previous.links);
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_linux_intf_start(void)
{
	eigrp_result_t result;

	pthread_mutex_lock(&pump_lock);
	result = inventory.fd >= 0 ? EIGRP_RESULT_CONFLICT
				   : eigrp_linux_intf_resync();
	pthread_mutex_unlock(&pump_lock);
	return result;
}

int eigrp_linux_intf_fd(void)
{
	int fd;

	pthread_mutex_lock(&inventory_lock);
	fd = inventory.fd;
	pthread_mutex_unlock(&inventory_lock);
	return fd;
}

/* Drain a bounded batch before reconciling.  The temporary state is private
 * until all frames have been parsed, so malformed/lost frames never publish a
 * half-applied inventory.  All notifications are made outside state_lock. */
static eigrp_result_t eigrp_linux_intf_process_locked(void)
{
	unsigned char buffer[EIGRP_LINUX_RECV_SIZE];
	struct eigrp_linux_inventory *next = NULL, previous;
	unsigned int batches = 0;
	bool changed = false;

	if (inventory.fd < 0)
		return eigrp_linux_intf_resync();
	while (batches++ < 1024) {
		struct sockaddr_nl sender = {0};
		struct iovec iov = {.iov_base = buffer,
				    .iov_len = sizeof(buffer)};
		struct msghdr message = {.msg_name = &sender,
					 .msg_namelen = sizeof(sender),
					 .msg_iov = &iov,
					 .msg_iovlen = 1};
		struct nlmsghdr *header;
		int remaining;
		ssize_t size;

		size = recvmsg(inventory.fd, &message, MSG_DONTWAIT);
		if (size < 0) {
			if (errno == EINTR)
				continue;
			if (errno == EAGAIN || errno == EWOULDBLOCK)
				break;
			goto lost;
		}
		if (!size || (message.msg_flags & MSG_TRUNC))
			goto lost;
		if (sender.nl_pid != 0)
			continue;
		if (!next) {
			next = eigrp_linux_inventory_copy(&inventory);
			if (!next)
				goto lost;
		}
		remaining = (int)size;
		for (header = (struct nlmsghdr *)buffer;
		     NLMSG_OK(header, remaining);
		     header = NLMSG_NEXT(header, remaining)) {
			if (header->nlmsg_type == NLMSG_OVERRUN
			    || (header->nlmsg_type == NLMSG_ERROR
				&& (header->nlmsg_len < NLMSG_LENGTH(
					    sizeof(struct nlmsgerr))
				    || ((const struct nlmsgerr *)NLMSG_DATA(
						header))
					       ->error)))
				goto lost;
			if (!eigrp_linux_message_apply(next, header))
				goto lost;
			changed = true;
		}
		if (remaining)
			goto lost;
	}
	if (!changed) {
		if (next) {
			eigrp_linux_link_free(next->links);
			free(next);
		}
		return EIGRP_RESULT_SUCCESS;
	}
	pthread_mutex_lock(&inventory_lock);
	previous = inventory;
	inventory.links = next->links;
	inventory.generation = next->generation;
	pthread_mutex_unlock(&inventory_lock);
	eigrp_linux_inventory_reconcile(&previous, &inventory);
	eigrp_linux_link_free(previous.links);
	free(next);
	return EIGRP_RESULT_SUCCESS;

lost:
	if (next) {
		eigrp_linux_link_free(next->links);
		free(next);
	}
	return eigrp_linux_intf_resync();
}

eigrp_result_t eigrp_linux_intf_process(void)
{
	eigrp_result_t result;

	pthread_mutex_lock(&pump_lock);
	result = eigrp_linux_intf_process_locked();
	pthread_mutex_unlock(&pump_lock);
	return result;
}

void eigrp_linux_intf_stop(void)
{
	struct eigrp_linux_inventory previous;

	pthread_mutex_lock(&pump_lock);
	pthread_mutex_lock(&inventory_lock);
	previous = inventory;
	memset(&inventory, 0, sizeof(inventory));
	inventory.fd = -1;
	pthread_mutex_unlock(&inventory_lock);
	if (previous.fd >= 0)
		close(previous.fd);
	eigrp_linux_link_free(previous.links);
	pthread_mutex_unlock(&pump_lock);
}

/* Production host callback; never link the UUT interface provider here. */
eigrp_result_t eigrp_sys_interface_walk(eigrp_instance_t *eigrp,
					eigrp_sys_interface_walk_cb callback,
					void *arg)
{
	struct eigrp_linux_link *link;
	eigrp_intf_runtime_state_t state;
	eigrp_afi_t afi;
	eigrp_result_t result = EIGRP_RESULT_SUCCESS;

	if (!eigrp || !callback)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	if (eigrp_instance_vrf_id(eigrp) != EIGRP_VRF_DEFAULT)
		return EIGRP_RESULT_UNSUPPORTED;
	afi = eigrp_instance_afi(eigrp);
	/* Hold the inventory while invoking each callback.  A deletion cannot
	 * enqueue its retirement before a stale walk publishes the old state.
	 */
	pthread_mutex_lock(&inventory_lock);
	if (inventory.fd < 0) {
		result = EIGRP_RESULT_INTERNAL_FAILURE;
		goto out;
	}
	for (link = inventory.links; link; link = link->next) {
		struct eigrp_linux_addr *addr;

		if (!eigrp_linux_default_vrf(&inventory, link))
			continue;
		for (addr = link->addresses; addr; addr = addr->next) {
			if (addr->prefix.address.afi != afi)
				continue;
			eigrp_linux_state_fill(link, addr, &state);
			callback(&state, arg);
		}
	}
out:
	pthread_mutex_unlock(&inventory_lock);
	return result;
}

// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Native IPv4/IPv6 EIGRP packet transport. Only IP framing, socket operations,
 * and host envelope metadata live here; the portable core owns EIGRP/RTP.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <arpa/inet.h>
#include <errno.h>
#include <limits.h>
#include <netinet/in.h>
#include <pthread.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>

#include "eigrp_linux_packet.h"
#include "eigrp_sys.h"

#define EIGRP_LINUX_PROTO 88
#define EIGRP_LINUX_GROUP 0xe000000aU
#define EIGRP_LINUX_IP_MIN 20U
#define EIGRP_LINUX_HEADER_MIN 20U
#define EIGRP_LINUX_PACKET_MAX UINT16_MAX
#define EIGRP_LINUX_TTL 1
#define EIGRP_LINUX_TOS 0xc0

static const struct in6_addr eigrp_linux_group6 = {
	.s6_addr = {0xff, 0x02, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0x0a},
};

struct eigrp_linux_packet_socket {
	const eigrp_instance_t *eigrp;
	int fd;
	struct eigrp_linux_packet_socket *next;
};

/* A per-runtime socket is needed because each AF runtime owns its receive
 * readiness.  Holding this mutex during nonblocking I/O prevents close/fd
 * reuse while a callback is using a descriptor. */
static pthread_mutex_t packet_lock = PTHREAD_MUTEX_INITIALIZER;
static struct eigrp_linux_packet_socket *packet_sockets;

static struct eigrp_linux_packet_socket *
eigrp_linux_packet_find(const eigrp_instance_t *eigrp)
{
	struct eigrp_linux_packet_socket *entry;

	for (entry = packet_sockets; entry; entry = entry->next)
		if (entry->eigrp == eigrp)
			return entry;
	return NULL;
}

static bool eigrp_linux_packet_afi(const eigrp_instance_t *eigrp,
				   eigrp_afi_t afi)
{
	return eigrp && eigrp_instance_afi(eigrp) == afi
	       && eigrp_instance_vrf_id(eigrp) == EIGRP_VRF_DEFAULT;
}

static bool eigrp_linux_packet_ipv4(const eigrp_instance_t *eigrp)
{
	return eigrp_linux_packet_afi(eigrp, EIGRP_AFI_IPV4);
}

static bool eigrp_linux_packet_ipv6(const eigrp_instance_t *eigrp)
{
	return eigrp_linux_packet_afi(eigrp, EIGRP_AFI_IPV6);
}

int eigrp_linux_packet_fd(const eigrp_instance_t *eigrp)
{
	struct eigrp_linux_packet_socket *entry;
	int fd = -1;

	pthread_mutex_lock(&packet_lock);
	entry = eigrp_linux_packet_find(eigrp);
	if (entry)
		fd = entry->fd;
	pthread_mutex_unlock(&packet_lock);
	return fd;
}

/* Control messages are mandatory for ingress identity. IPv4 explicitly
 * disables other sockets' multicast group memberships from feeding us. */
static int eigrp_linux_packet_options4(int fd)
{
	int enabled = 1;
	int disabled = 0;
	int ttl = EIGRP_LINUX_TTL;
	int tos = EIGRP_LINUX_TOS;

	return setsockopt(fd, IPPROTO_IP, IP_PKTINFO, &enabled, sizeof(enabled))
			       || setsockopt(fd, IPPROTO_IP, IP_MULTICAST_ALL,
					     &disabled, sizeof(disabled))
			       || setsockopt(fd, IPPROTO_IP, IP_MULTICAST_TTL,
					     &ttl, sizeof(ttl))
			       || setsockopt(fd, IPPROTO_IP, IP_MULTICAST_LOOP,
					     &disabled, sizeof(disabled))
			       || setsockopt(fd, IPPROTO_IP, IP_TTL, &ttl,
					     sizeof(ttl))
			       || setsockopt(fd, IPPROTO_IP, IP_TOS, &tos,
					     sizeof(tos))
		       ? -1
		       : 0;
}

static int eigrp_linux_packet_options6(int fd)
{
	int enabled = 1;
	int disabled = 0;
	int ttl = EIGRP_LINUX_TTL;
	int tos = EIGRP_LINUX_TOS;
	int checksum = -1;

	/* The portable EIGRP checksum does not include an IPv6 pseudoheader.
	 * Never let IPV6_CHECKSUM overwrite the portable wire checksum. */
	return setsockopt(fd, IPPROTO_IPV6, IPV6_V6ONLY, &enabled,
			  sizeof(enabled))
			       || setsockopt(fd, IPPROTO_IPV6, IPV6_RECVPKTINFO,
					     &enabled, sizeof(enabled))
			       || setsockopt(fd, IPPROTO_IPV6,
					     IPV6_RECVHOPLIMIT, &enabled,
					     sizeof(enabled))
			       || setsockopt(fd, IPPROTO_IPV6,
					     IPV6_MULTICAST_HOPS, &ttl,
					     sizeof(ttl))
			       || setsockopt(fd, IPPROTO_IPV6,
					     IPV6_UNICAST_HOPS, &ttl,
					     sizeof(ttl))
			       || setsockopt(fd, IPPROTO_IPV6,
					     IPV6_MULTICAST_LOOP, &disabled,
					     sizeof(disabled))
			       || setsockopt(fd, IPPROTO_IPV6, IPV6_TCLASS,
					     &tos, sizeof(tos))
			       || setsockopt(fd, IPPROTO_IPV6, IPV6_CHECKSUM,
					     &checksum, sizeof(checksum))
		       ? -1
		       : 0;
}

eigrp_result_t eigrp_sys_socket_open(eigrp_instance_t *eigrp)
{
	struct eigrp_linux_packet_socket *entry;
	int fd;
	bool ipv6;

	if (!eigrp)
		return EIGRP_RESULT_INVALID_ARGUMENT;
	/* Task 4 publishes only default-VRF interfaces.  Do not report a
	 * misleading ingress VRF for another Linux routing table. */
	if (!eigrp_linux_packet_ipv4(eigrp) && !eigrp_linux_packet_ipv6(eigrp))
		return EIGRP_RESULT_UNSUPPORTED;
	ipv6 = eigrp_linux_packet_ipv6(eigrp);

	pthread_mutex_lock(&packet_lock);
	if (eigrp_linux_packet_find(eigrp)) {
		pthread_mutex_unlock(&packet_lock);
		return EIGRP_RESULT_SUCCESS;
	}
	fd = socket(ipv6 ? AF_INET6 : AF_INET,
		    SOCK_RAW | SOCK_NONBLOCK | SOCK_CLOEXEC, EIGRP_LINUX_PROTO);
	if (fd < 0) {
		pthread_mutex_unlock(&packet_lock);
		return EIGRP_RESULT_INTERNAL_FAILURE;
	}
	if ((ipv6 ? eigrp_linux_packet_options6(fd)
		  : eigrp_linux_packet_options4(fd))
	    < 0) {
		int saved = errno;
		close(fd);
		errno = saved;
		pthread_mutex_unlock(&packet_lock);
		return EIGRP_RESULT_INTERNAL_FAILURE;
	}
	entry = calloc(1, sizeof(*entry));
	if (!entry) {
		int saved = errno;
		close(fd);
		errno = saved;
		pthread_mutex_unlock(&packet_lock);
		return EIGRP_RESULT_INTERNAL_FAILURE;
	}
	entry->eigrp = eigrp;
	entry->fd = fd;
	entry->next = packet_sockets;
	packet_sockets = entry;
	pthread_mutex_unlock(&packet_lock);
	return EIGRP_RESULT_SUCCESS;
}

void eigrp_sys_socket_close(eigrp_instance_t *eigrp)
{
	struct eigrp_linux_packet_socket **cursor;

	pthread_mutex_lock(&packet_lock);
	for (cursor = &packet_sockets; *cursor; cursor = &(*cursor)->next) {
		struct eigrp_linux_packet_socket *entry = *cursor;

		if (entry->eigrp != eigrp)
			continue;
		*cursor = entry->next;
		close(entry->fd); /* Kernel also retires multicast memberships.
				   */
		free(entry);
		break;
	}
	pthread_mutex_unlock(&packet_lock);
}

void eigrp_sys_socket_send_buffer_ensure(eigrp_instance_t *eigrp,
					 uint32_t minimum)
{
	struct eigrp_linux_packet_socket *entry;
	int current;
	socklen_t length = sizeof(current);
	int required;

	if (minimum > INT_MAX / 2U || !minimum)
		return;
	pthread_mutex_lock(&packet_lock);
	entry = eigrp_linux_packet_find(eigrp);
	if (entry
	    && getsockopt(entry->fd, SOL_SOCKET, SO_SNDBUF, &current, &length)
		       == 0) {
		/* Linux reports twice the requested buffer for accounting. */
		required = (int)minimum;
		if (current < required * 2)
			(void)setsockopt(entry->fd, SOL_SOCKET, SO_SNDBUF,
					 &required, sizeof(required));
	}
	pthread_mutex_unlock(&packet_lock);
}

static bool eigrp_linux_packet_interface(eigrp_intf_t *ei,
					 struct ip_mreqn *request)
{
	eigrp_prefix_t address;

	if (!ei || !request || !eigrp_intf_ifindex(ei)
	    || eigrp_intf_ifindex(ei) > INT_MAX
	    || eigrp_intf_address_read(ei, &address) != EIGRP_RESULT_SUCCESS
	    || address.address.afi != EIGRP_AFI_IPV4)
		return false;
	memset(request, 0, sizeof(*request));
	request->imr_ifindex = (int)eigrp_intf_ifindex(ei);
	memcpy(&request->imr_address, address.address.bytes,
	       sizeof(request->imr_address));
	request->imr_multiaddr.s_addr = htonl(EIGRP_LINUX_GROUP);
	return true;
}

static bool eigrp_linux_packet_interface6(eigrp_intf_t *ei,
					  unsigned int *ifindex,
					  struct in6_addr *source)
{
	eigrp_prefix_t address;
	struct in6_addr local;

	if (!ei || !ifindex || !source || !eigrp_intf_ifindex(ei)
	    || eigrp_intf_ifindex(ei) > INT_MAX
	    || eigrp_intf_address_read(ei, &address) != EIGRP_RESULT_SUCCESS
	    || address.address.afi != EIGRP_AFI_IPV6)
		return false;
	memcpy(&local, address.address.bytes, sizeof(local));
	if (!IN6_IS_ADDR_LINKLOCAL(&local))
		return false;
	*ifindex = (unsigned int)eigrp_intf_ifindex(ei);
	*source = local;
	return true;
}

int eigrp_sys_multicast_interface_update(eigrp_operation_t operation,
					 eigrp_instance_t *eigrp,
					 eigrp_intf_t *ei)
{
	struct eigrp_linux_packet_socket *entry;
	struct ip_mreqn request;
	struct in6_addr source;
	unsigned int index;
	int result = -1;

	if (operation != EIGRP_SET) {
		errno = EINVAL;
		return -1;
	}
	if (eigrp_linux_packet_ipv6(eigrp)) {
		if (!eigrp_linux_packet_interface6(ei, &index, &source)) {
			errno = EINVAL;
			return -1;
		}
		pthread_mutex_lock(&packet_lock);
		entry = eigrp_linux_packet_find(eigrp);
		if (entry)
			result = setsockopt(entry->fd, IPPROTO_IPV6,
					    IPV6_MULTICAST_IF, &index,
					    sizeof(index));
		else
			errno = EBADF;
		pthread_mutex_unlock(&packet_lock);
		return result;
	}
	if (!eigrp_linux_packet_ipv4(eigrp)
	    || !eigrp_linux_packet_interface(ei, &request)) {
		errno = EINVAL;
		return -1;
	}
	pthread_mutex_lock(&packet_lock);
	entry = eigrp_linux_packet_find(eigrp);
	if (entry)
		result = setsockopt(entry->fd, IPPROTO_IP, IP_MULTICAST_IF,
				    &request, sizeof(request));
	else
		errno = EBADF;
	pthread_mutex_unlock(&packet_lock);
	return result;
}

static int eigrp_linux_packet_membership(eigrp_instance_t *eigrp,
					 eigrp_intf_t *ei, int option)
{
	struct eigrp_linux_packet_socket *entry;
	struct ip_mreqn request;
	struct ipv6_mreq request6 = {0};
	struct in6_addr source;
	unsigned int index;
	int result = -1;

	if (eigrp_linux_packet_ipv6(eigrp)) {
		/* Leaving a multicast group needs only its interface index. The
		 * address may already have disappeared on an interface-down. */
		if (option == IP_DROP_MEMBERSHIP) {
			if (!ei || !eigrp_intf_ifindex(ei)
			    || eigrp_intf_ifindex(ei) > INT_MAX) {
				errno = EINVAL;
				return -1;
			}
			index = (unsigned int)eigrp_intf_ifindex(ei);
		} else if (!eigrp_linux_packet_interface6(ei, &index,
							  &source)) {
			errno = EINVAL;
			return -1;
		}
		request6.ipv6mr_multiaddr = eigrp_linux_group6;
		request6.ipv6mr_interface = index;
		pthread_mutex_lock(&packet_lock);
		entry = eigrp_linux_packet_find(eigrp);
		if (entry)
			result = setsockopt(entry->fd, IPPROTO_IPV6,
					    option == IP_ADD_MEMBERSHIP
						    ? IPV6_JOIN_GROUP
						    : IPV6_LEAVE_GROUP,
					    &request6, sizeof(request6));
		else
			errno = EBADF;
		pthread_mutex_unlock(&packet_lock);
		return result;
	}
	if (!eigrp_linux_packet_ipv4(eigrp)
	    || !eigrp_linux_packet_interface(ei, &request)) {
		errno = EINVAL;
		return -1;
	}
	pthread_mutex_lock(&packet_lock);
	entry = eigrp_linux_packet_find(eigrp);
	if (entry)
		result = setsockopt(entry->fd, IPPROTO_IP, option, &request,
				    sizeof(request));
	else
		errno = EBADF;
	pthread_mutex_unlock(&packet_lock);
	return result;
}

int eigrp_sys_multicast_join(eigrp_instance_t *eigrp, eigrp_intf_t *ei)
{
	return eigrp_linux_packet_membership(eigrp, ei, IP_ADD_MEMBERSHIP);
}

int eigrp_sys_multicast_leave(eigrp_instance_t *eigrp, eigrp_intf_t *ei)
{
	return eigrp_linux_packet_membership(eigrp, ei, IP_DROP_MEMBERSHIP);
}

int eigrp_sys_ipv4_packet_send(eigrp_instance_t *eigrp, eigrp_intf_t *ei,
			       const eigrp_address_t *destination,
			       const uint8_t *payload, size_t length)
{
	struct eigrp_linux_packet_socket *entry;
	struct ip_mreqn request;
	struct sockaddr_in peer = {.sin_family = AF_INET};
	struct iovec iov;
	struct msghdr msg = {0};
	struct cmsghdr *cmsg;
	struct in_pktinfo *pktinfo;
	union {
		struct cmsghdr align;
		char bytes[CMSG_SPACE(sizeof(struct in_pktinfo))];
	} ancillary = {0};
	int result = -1;

	if (!eigrp_linux_packet_ipv4(eigrp) || !destination || !payload
	    || destination->afi != EIGRP_AFI_IPV4
	    || !eigrp_linux_packet_interface(ei, &request)) {
		errno = EINVAL;
		return -1;
	}
	if (length < EIGRP_LINUX_HEADER_MIN
	    || length > EIGRP_LINUX_PACKET_MAX - EIGRP_LINUX_IP_MIN) {
		errno = EMSGSIZE;
		return -1;
	}
	memcpy(&peer.sin_addr, destination->bytes, sizeof(peer.sin_addr));
	iov.iov_base = (void *)payload;
	iov.iov_len = length;
	msg.msg_name = &peer;
	msg.msg_namelen = sizeof(peer);
	msg.msg_iov = &iov;
	msg.msg_iovlen = 1;
	msg.msg_control = ancillary.bytes;
	msg.msg_controllen = sizeof(ancillary.bytes);
	cmsg = CMSG_FIRSTHDR(&msg);
	cmsg->cmsg_level = IPPROTO_IP;
	cmsg->cmsg_type = IP_PKTINFO;
	cmsg->cmsg_len = CMSG_LEN(sizeof(*pktinfo));
	pktinfo = (struct in_pktinfo *)CMSG_DATA(cmsg);
	pktinfo->ipi_ifindex = request.imr_ifindex;
	pktinfo->ipi_spec_dst = request.imr_address;

	pthread_mutex_lock(&packet_lock);
	entry = eigrp_linux_packet_find(eigrp);
	if (entry) {
		/* Kernel builds the IPv4 header; unlike FRR's IP_HDRINCL path,
		 * only the portable EIGRP bytes are submitted to the socket. */
		result =
			sendmsg(entry->fd, &msg,
				peer.sin_addr.s_addr == htonl(EIGRP_LINUX_GROUP)
					? MSG_DONTWAIT
					: MSG_DONTWAIT | MSG_DONTROUTE);
	} else {
		errno = EBADF;
	}
	pthread_mutex_unlock(&packet_lock);
	return result;
}

int eigrp_sys_ipv6_packet_send(eigrp_instance_t *eigrp, eigrp_intf_t *ei,
			       const eigrp_address_t *destination,
			       const uint8_t *payload, size_t length)
{
	struct eigrp_linux_packet_socket *entry;
	struct sockaddr_in6 peer = {.sin6_family = AF_INET6};
	struct in6_pktinfo *pktinfo;
	struct cmsghdr *cmsg;
	struct iovec iov;
	struct msghdr msg = {0};
	struct in6_addr source;
	unsigned int index;
	union {
		struct cmsghdr align;
		char bytes[CMSG_SPACE(sizeof(struct in6_pktinfo))];
	} ancillary = {0};
	int result = -1;

	if (!eigrp_linux_packet_ipv6(eigrp) || !destination || !payload
	    || destination->afi != EIGRP_AFI_IPV6
	    || !eigrp_linux_packet_interface6(ei, &index, &source)) {
		errno = EINVAL;
		return -1;
	}
	if (length < EIGRP_LINUX_HEADER_MIN
	    || length > EIGRP_LINUX_PACKET_MAX) {
		errno = EMSGSIZE;
		return -1;
	}
	memcpy(&peer.sin6_addr, destination->bytes, sizeof(peer.sin6_addr));
	if (IN6_IS_ADDR_UNSPECIFIED(&peer.sin6_addr)
	    || (IN6_IS_ADDR_MULTICAST(&peer.sin6_addr)
		&& memcmp(destination->bytes, &eigrp_linux_group6, 16) != 0)
	    || (!IN6_IS_ADDR_MULTICAST(&peer.sin6_addr)
		&& !IN6_IS_ADDR_LINKLOCAL(&peer.sin6_addr))) {
		errno = EINVAL;
		return -1;
	}
	peer.sin6_scope_id = index;
	iov.iov_base = (void *)payload;
	iov.iov_len = length;
	msg.msg_name = &peer;
	msg.msg_namelen = sizeof(peer);
	msg.msg_iov = &iov;
	msg.msg_iovlen = 1;
	msg.msg_control = ancillary.bytes;
	msg.msg_controllen = sizeof(ancillary.bytes);
	cmsg = CMSG_FIRSTHDR(&msg);
	cmsg->cmsg_level = IPPROTO_IPV6;
	cmsg->cmsg_type = IPV6_PKTINFO;
	cmsg->cmsg_len = CMSG_LEN(sizeof(*pktinfo));
	pktinfo = (struct in6_pktinfo *)CMSG_DATA(cmsg);
	pktinfo->ipi6_ifindex = index;
	pktinfo->ipi6_addr = source;

	pthread_mutex_lock(&packet_lock);
	entry = eigrp_linux_packet_find(eigrp);
	if (entry)
		result = sendmsg(entry->fd, &msg, MSG_DONTWAIT);
	else
		errno = EBADF;
	pthread_mutex_unlock(&packet_lock);
	return result;
}

/* Verify only the IPv4 envelope.  Packet opcodes, checksum, authentication,
 * and TLVs are exclusively validated by the portable core. */
static bool eigrp_linux_packet_ipv4_decode(const uint8_t *data, size_t length,
					   eigrp_address_t *source,
					   eigrp_address_t *destination,
					   eigrp_packet_rx_meta_t *meta)
{
	size_t header_length;
	uint16_t total_length;
	uint16_t fragment;
	uint32_t sum = 0;

	if (!data || !source || !destination || !meta
	    || length < EIGRP_LINUX_IP_MIN + EIGRP_LINUX_HEADER_MIN
	    || length > EIGRP_LINUX_PACKET_MAX || (data[0] >> 4) != 4
	    || (data[0] & 0x0f) < 5 || data[9] != EIGRP_LINUX_PROTO)
		return false;
	header_length = (data[0] & 0x0f) * 4U;
	total_length = (uint16_t)((uint16_t)data[2] << 8 | data[3]);
	fragment = (uint16_t)((uint16_t)data[6] << 8 | data[7]);
	if (header_length > length || total_length != length
	    || total_length < header_length + EIGRP_LINUX_HEADER_MIN
	    || (fragment & 0xbfffU))
		return false;
	/* An Internet checksum across the complete IPv4 header (including
	 * options) must fold to 0xffff.  No EIGRP payload is inspected here. */
	for (size_t i = 0; i < header_length; i += 2)
		sum += (uint32_t)((uint16_t)data[i] << 8 | data[i + 1]);
	while (sum >> 16)
		sum = (sum & 0xffffU) + (sum >> 16);
	if (sum != 0xffffU)
		return false;

	memset(source, 0, sizeof(*source));
	memset(destination, 0, sizeof(*destination));
	source->afi = EIGRP_AFI_IPV4;
	destination->afi = EIGRP_AFI_IPV4;
	memcpy(source->bytes, data + 12, 4);
	memcpy(destination->bytes, data + 16, 4);
	meta->network_header_length = (uint16_t)header_length;
	meta->eigrp_length = (uint16_t)(total_length - header_length);
	meta->destination_multicast =
		memcmp(destination->bytes, "\xe0\x00\x00\x0a", 4) == 0;
	return true;
}

struct eigrp_linux_packet_probe {
	eigrp_ifindex_t wanted;
	eigrp_afi_t afi;
	bool found;
};

static void
eigrp_linux_packet_probe_intf(const eigrp_intf_runtime_state_t *state,
			      void *arg)
{
	struct eigrp_linux_packet_probe *probe = arg;
	struct in6_addr address;

	if (state->ifindex != probe->wanted
	    || state->address.address.afi != probe->afi)
		return;
	if (probe->afi == EIGRP_AFI_IPV6) {
		memcpy(&address, state->address.address.bytes, sizeof(address));
		if (!IN6_IS_ADDR_LINKLOCAL(&address))
			return;
	}
	probe->found = true;
}

bool eigrp_sys_ipv4_packet_receive(eigrp_instance_t *eigrp, uint8_t *buffer,
				   size_t capacity, size_t *received_length,
				   eigrp_ifindex_t *ifindex,
				   eigrp_address_t *source,
				   eigrp_address_t *destination,
				   eigrp_packet_rx_meta_t *meta)
{
	struct eigrp_linux_packet_socket *entry;
	struct sockaddr_in peer;
	struct iovec iov;
	struct msghdr msg = {0};
	struct cmsghdr *cmsg;
	struct in_pktinfo info = {0};
	struct eigrp_linux_packet_probe probe;
	eigrp_address_t src;
	eigrp_address_t dst;
	eigrp_packet_rx_meta_t rxmeta = {0};
	union {
		struct cmsghdr align;
		char bytes[CMSG_SPACE(sizeof(struct in_pktinfo))];
	} ancillary;
	ssize_t length = -1;
	bool have_pktinfo = false;

	if (!eigrp_linux_packet_ipv4(eigrp) || !buffer || !received_length
	    || !ifindex || !source || !destination || !meta || !capacity)
		return false;
	/* recvmsg may report an oversize frame via MSG_TRUNC.  Its buffer is
	 * never permitted to exceed the maximum IPv4 wire datagram size. */
	if (capacity > EIGRP_LINUX_PACKET_MAX)
		capacity = EIGRP_LINUX_PACKET_MAX;
	memset(&peer, 0, sizeof(peer));
	iov.iov_base = buffer;
	iov.iov_len = capacity;
	msg.msg_name = &peer;
	msg.msg_namelen = sizeof(peer);
	msg.msg_iov = &iov;
	msg.msg_iovlen = 1;
	msg.msg_control = ancillary.bytes;
	msg.msg_controllen = sizeof(ancillary.bytes);

	pthread_mutex_lock(&packet_lock);
	entry = eigrp_linux_packet_find(eigrp);
	if (entry)
		length = recvmsg(entry->fd, &msg, MSG_DONTWAIT);
	pthread_mutex_unlock(&packet_lock);
	if (length < 0 || (size_t)length > capacity
	    || (msg.msg_flags & (MSG_TRUNC | MSG_CTRUNC))
	    || !eigrp_linux_packet_ipv4_decode(buffer, (size_t)length, &src,
					       &dst, &rxmeta))
		return false;
	for (cmsg = CMSG_FIRSTHDR(&msg); cmsg; cmsg = CMSG_NXTHDR(&msg, cmsg)) {
		if (cmsg->cmsg_level != IPPROTO_IP
		    || cmsg->cmsg_type != IP_PKTINFO
		    || cmsg->cmsg_len < CMSG_LEN(sizeof(info)))
			continue;
		if (have_pktinfo)
			return false;
		memcpy(&info, CMSG_DATA(cmsg), sizeof(info));
		have_pktinfo = true;
	}
	if (!have_pktinfo || info.ipi_ifindex <= 0
	    || memcmp(&info.ipi_addr, dst.bytes, sizeof(info.ipi_addr)) != 0)
		return false;

	/* Task 4's interface provider advertises default-VRF interfaces only.
	 * Confirm the ingress index against that inventory before labeling the
	 * frame with the default VRF.  Never infer VRF from an address alone.
	 */
	probe.wanted = (eigrp_ifindex_t)info.ipi_ifindex;
	probe.afi = EIGRP_AFI_IPV4;
	probe.found = false;
	if (eigrp_sys_interface_walk(eigrp, eigrp_linux_packet_probe_intf,
				     &probe)
		    != EIGRP_RESULT_SUCCESS
	    || !probe.found)
		return false;

	*received_length = (size_t)length;
	*ifindex = probe.wanted;
	*source = src;
	*destination = dst;
	rxmeta.ingress_vrf_id = EIGRP_VRF_DEFAULT;
	*meta = rxmeta;
	return true;
}

/* Linux IPv6 raw sockets deliver only the protocol-88 payload. The kernel
 * handles the IPv6 header and extension headers; IPV6_PKTINFO and
 * IPV6_HOPLIMIT supply the envelope necessary for portable EIGRP. */
bool eigrp_sys_ipv6_packet_receive(eigrp_instance_t *eigrp, uint8_t *buffer,
				   size_t capacity, size_t *received_length,
				   eigrp_ifindex_t *ifindex,
				   eigrp_address_t *source,
				   eigrp_address_t *destination,
				   eigrp_packet_rx_meta_t *meta)
{
	struct eigrp_linux_packet_socket *entry;
	struct sockaddr_in6 peer = {0};
	struct iovec iov;
	struct msghdr msg = {0};
	struct cmsghdr *cmsg;
	struct in6_pktinfo info = {0};
	struct eigrp_linux_packet_probe probe;
	union {
		struct cmsghdr align;
		char bytes[CMSG_SPACE(sizeof(struct in6_pktinfo))
			   + CMSG_SPACE(sizeof(int))];
	} ancillary = {0};
	ssize_t length = -1;
	int hoplimit = -1;
	bool have_pktinfo = false;
	bool have_hoplimit = false;

	if (!eigrp_linux_packet_ipv6(eigrp) || !buffer || !received_length
	    || !ifindex || !source || !destination || !meta || !capacity)
		return false;
	if (capacity > EIGRP_LINUX_PACKET_MAX)
		capacity = EIGRP_LINUX_PACKET_MAX;
	iov.iov_base = buffer;
	iov.iov_len = capacity;
	msg.msg_name = &peer;
	msg.msg_namelen = sizeof(peer);
	msg.msg_iov = &iov;
	msg.msg_iovlen = 1;
	msg.msg_control = ancillary.bytes;
	msg.msg_controllen = sizeof(ancillary.bytes);

	pthread_mutex_lock(&packet_lock);
	entry = eigrp_linux_packet_find(eigrp);
	if (entry)
		length = recvmsg(entry->fd, &msg, MSG_DONTWAIT);
	pthread_mutex_unlock(&packet_lock);
	if (length < EIGRP_LINUX_HEADER_MIN || (size_t)length > capacity
	    || (msg.msg_flags & (MSG_TRUNC | MSG_CTRUNC))
	    || msg.msg_namelen < sizeof(peer) || peer.sin6_family != AF_INET6
	    || !IN6_IS_ADDR_LINKLOCAL(&peer.sin6_addr))
		return false;

	for (cmsg = CMSG_FIRSTHDR(&msg); cmsg; cmsg = CMSG_NXTHDR(&msg, cmsg)) {
		if (cmsg->cmsg_level != IPPROTO_IPV6)
			continue;
		if (cmsg->cmsg_type == IPV6_PKTINFO) {
			if (have_pktinfo
			    || cmsg->cmsg_len < CMSG_LEN(sizeof(info)))
				return false;
			memcpy(&info, CMSG_DATA(cmsg), sizeof(info));
			have_pktinfo = true;
		} else if (cmsg->cmsg_type == IPV6_HOPLIMIT) {
			if (have_hoplimit
			    || cmsg->cmsg_len < CMSG_LEN(sizeof(hoplimit)))
				return false;
			memcpy(&hoplimit, CMSG_DATA(cmsg), sizeof(hoplimit));
			have_hoplimit = true;
		}
	}
	if (!have_pktinfo || !have_hoplimit || info.ipi6_ifindex == 0
	    || info.ipi6_ifindex > INT_MAX || hoplimit != EIGRP_LINUX_TTL
	    || (peer.sin6_scope_id && peer.sin6_scope_id != info.ipi6_ifindex))
		return false;
	if (IN6_IS_ADDR_MULTICAST(&info.ipi6_addr)) {
		if (memcmp(&info.ipi6_addr, &eigrp_linux_group6,
			   sizeof(eigrp_linux_group6))
		    != 0)
			return false;
	} else if (!IN6_IS_ADDR_LINKLOCAL(&info.ipi6_addr)) {
		return false;
	}

	probe.wanted = (eigrp_ifindex_t)info.ipi6_ifindex;
	probe.afi = EIGRP_AFI_IPV6;
	probe.found = false;
	if (eigrp_sys_interface_walk(eigrp, eigrp_linux_packet_probe_intf,
				     &probe)
		    != EIGRP_RESULT_SUCCESS
	    || !probe.found)
		return false;

	memset(source, 0, sizeof(*source));
	memset(destination, 0, sizeof(*destination));
	source->afi = EIGRP_AFI_IPV6;
	destination->afi = EIGRP_AFI_IPV6;
	memcpy(source->bytes, &peer.sin6_addr, sizeof(peer.sin6_addr));
	memcpy(destination->bytes, &info.ipi6_addr, sizeof(info.ipi6_addr));
	*received_length = (size_t)length;
	*ifindex = probe.wanted;
	*meta = (eigrp_packet_rx_meta_t){
		.ingress_vrf_id = EIGRP_VRF_DEFAULT,
		.network_header_length = 0,
		.eigrp_length = (uint16_t)length,
		.destination_multicast = IN6_IS_ADDR_MULTICAST(&info.ipi6_addr),
	};
	return true;
}

int eigrp_sys_packet_send(eigrp_instance_t *eigrp, eigrp_intf_t *ei,
			  const eigrp_address_t *destination,
			  const uint8_t *payload, size_t length)
{
	if (eigrp_linux_packet_ipv6(eigrp))
		return eigrp_sys_ipv6_packet_send(eigrp, ei, destination,
						  payload, length);
	return eigrp_sys_ipv4_packet_send(eigrp, ei, destination, payload,
					  length);
}

bool eigrp_sys_packet_receive(eigrp_instance_t *eigrp, uint8_t *buffer,
			      size_t capacity, size_t *received_length,
			      eigrp_ifindex_t *ifindex, eigrp_address_t *source,
			      eigrp_address_t *destination,
			      eigrp_packet_rx_meta_t *meta)
{
	if (eigrp_linux_packet_ipv6(eigrp))
		return eigrp_sys_ipv6_packet_receive(eigrp, buffer, capacity,
						     received_length, ifindex,
						     source, destination, meta);
	return eigrp_sys_ipv4_packet_receive(eigrp, buffer, capacity,
					     received_length, ifindex, source,
					     destination, meta);
}

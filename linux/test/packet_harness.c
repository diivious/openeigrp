// SPDX-License-Identifier: GPL-2.0-or-later
/* Crafted IPv4 protocol-88, socket and lifecycle tests against production C.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <assert.h>
#include <arpa/inet.h>
#include <errno.h>
#include <netinet/in.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/types.h>
#include <unistd.h>

struct eigrp_instance;
struct eigrp_interface;
static int fake_socket(int family, int type, int protocol);
static int fake_setsockopt(int fd, int level, int option, const void *data,
			   socklen_t size);
static int fake_getsockopt(int fd, int level, int option, void *data,
			   socklen_t *size);
static int fake_close(int fd);
static ssize_t fake_sendmsg(int fd, const struct msghdr *msg, int flags);
static ssize_t fake_recvmsg(int fd, struct msghdr *msg, int flags);

#define socket(...) fake_socket(__VA_ARGS__)
#define setsockopt fake_setsockopt
#define getsockopt fake_getsockopt
#define close fake_close
#define sendmsg fake_sendmsg
#define recvmsg fake_recvmsg
#include "../code/eigrp_linux_packet.c"
static int runtime_bindings;
void eigrp_unix_runtime_fd_set(eigrp_instance_t *instance, int fd)
{
	(void)instance;
	assert(fd >= 0);
	runtime_bindings++;
}
void eigrp_unix_runtime_fd_clear(eigrp_instance_t *instance)
{
	(void)instance;
	assert(runtime_bindings > 0);
	runtime_bindings--;
}

#undef socket
#undef setsockopt
#undef getsockopt
#undef close
#undef sendmsg
#undef recvmsg

struct eigrp_instance {
	eigrp_afi_t afi;
	eigrp_vrf_id_t vrf;
};
struct eigrp_interface {
	eigrp_ifindex_t index;
	eigrp_prefix_t prefix;
};

static int next_fd = 100;
static int opened;
static int closed;
static int fail_socket;
static int fail_option;
static int option_calls;
static int last_option;
static int joined;
static int left;
static int multicast_if;
static int last_send_flags;
static int sent;
static int send_fail;
static int recv_fail;
static int receive_flags;
static int no_control;
static int wrong_destination;
static int received;
static bool interface_present = true;
static uint8_t incoming[256];
static size_t incoming_size;
static uint8_t sent_bytes[256];
static size_t sent_size;
static struct in_pktinfo sent_info;
static struct eigrp_instance runtime = {.afi = EIGRP_AFI_IPV4, .vrf = 0};
static struct eigrp_instance other = {.afi = EIGRP_AFI_IPV6, .vrf = 0};
static struct eigrp_instance runtime2 = {.afi = EIGRP_AFI_IPV4, .vrf = 0};
static struct eigrp_interface local = {.index = 11};

static int fake_socket(int family, int type, int protocol)
{
	assert(family == AF_INET && protocol == EIGRP_LINUX_PROTO);
	assert((type & SOCK_RAW) == SOCK_RAW);
	assert((type & SOCK_NONBLOCK) && (type & SOCK_CLOEXEC));
	if (fail_socket) {
		errno = EPERM;
		return -1;
	}
	opened++;
	return next_fd++;
}

static int fake_setsockopt(int fd, int level, int option, const void *data,
			   socklen_t size)
{
	assert(fd >= 100 && data && size);
	option_calls++;
	last_option = option;
	if (fail_option == option) {
		errno = EIO;
		return -1;
	}
	if (level == IPPROTO_IP) {
		if (option == IP_PKTINFO)
			assert(*(const int *)data == 1);
		if (option == IP_MULTICAST_ALL || option == IP_MULTICAST_LOOP)
			assert(*(const int *)data == 0);
		if (option == IP_MULTICAST_TTL || option == IP_TTL)
			assert(*(const int *)data == 1);
		if (option == IP_TOS)
			assert(*(const int *)data == EIGRP_LINUX_TOS);
		if (option == IP_ADD_MEMBERSHIP || option == IP_DROP_MEMBERSHIP
		    || option == IP_MULTICAST_IF) {
			const struct ip_mreqn *req = data;
			assert(size == sizeof(*req));
			assert(req->imr_ifindex == 11);
			assert(req->imr_multiaddr.s_addr
			       == htonl(EIGRP_LINUX_GROUP));
			if (option == IP_ADD_MEMBERSHIP)
				joined++;
			else if (option == IP_DROP_MEMBERSHIP)
				left++;
			else
				multicast_if++;
		}
	}
	return 0;
}

static int fake_getsockopt(int fd, int level, int option, void *data,
			   socklen_t *size)
{
	assert(fd >= 100 && level == SOL_SOCKET && option == SO_SNDBUF);
	assert(*size >= sizeof(int));
	*(int *)data = 8192;
	return 0;
}

static int fake_close(int fd)
{
	assert(fd >= 100);
	closed++;
	return 0;
}

static ssize_t fake_sendmsg(int fd, const struct msghdr *msg, int flags)
{
	struct cmsghdr *control;
	struct sockaddr_in *peer = msg->msg_name;

	assert(fd >= 100 && msg->msg_iovlen == 1);
	assert(peer->sin_family == AF_INET);
	assert(msg->msg_iov[0].iov_len <= sizeof(sent_bytes));
	control = CMSG_FIRSTHDR((struct msghdr *)msg);
	assert(control && control->cmsg_level == IPPROTO_IP
	       && control->cmsg_type == IP_PKTINFO);
	memcpy(&sent_info, CMSG_DATA(control), sizeof(sent_info));
	memcpy(sent_bytes, msg->msg_iov[0].iov_base, msg->msg_iov[0].iov_len);
	sent_size = msg->msg_iov[0].iov_len;
	last_send_flags = flags;
	sent++;
	if (send_fail) {
		errno = ENOBUFS;
		return -1;
	}
	return (ssize_t)sent_size;
}

static ssize_t fake_recvmsg(int fd, struct msghdr *msg, int flags)
{
	struct cmsghdr *control;
	struct in_pktinfo *info;
	struct sockaddr_in *peer = msg->msg_name;

	assert(fd >= 100 && flags == MSG_DONTWAIT);
	if (recv_fail) {
		errno = EAGAIN;
		return -1;
	}
	assert(msg->msg_iovlen == 1);
	msg->msg_flags = receive_flags;
	memcpy(msg->msg_iov[0].iov_base, incoming,
	       incoming_size < msg->msg_iov[0].iov_len
		       ? incoming_size
		       : msg->msg_iov[0].iov_len);
	memset(peer, 0, sizeof(*peer));
	peer->sin_family = AF_INET;
	memcpy(&peer->sin_addr, incoming + 12, 4);
	if (!no_control) {
		control = CMSG_FIRSTHDR(msg);
		control->cmsg_level = IPPROTO_IP;
		control->cmsg_type = IP_PKTINFO;
		control->cmsg_len = CMSG_LEN(sizeof(*info));
		info = (struct in_pktinfo *)CMSG_DATA(control);
		memset(info, 0, sizeof(*info));
		info->ipi_ifindex = 11;
		memcpy(&info->ipi_addr, incoming + 16, 4);
		if (wrong_destination)
			info->ipi_addr.s_addr = htonl(0xc6336401U);
		msg->msg_controllen = CMSG_SPACE(sizeof(*info));
	} else {
		msg->msg_controllen = 0;
	}
	received++;
	return (ssize_t)incoming_size;
}

eigrp_afi_t eigrp_instance_afi(const eigrp_instance_t *eigrp)
{
	return eigrp->afi;
}

eigrp_vrf_id_t eigrp_instance_vrf_id(const eigrp_instance_t *eigrp)
{
	return eigrp->vrf;
}

eigrp_ifindex_t eigrp_intf_ifindex(const eigrp_intf_t *ei)
{
	return ei->index;
}

eigrp_result_t eigrp_intf_address_read(const eigrp_intf_t *ei,
				       eigrp_prefix_t *address)
{
	*address = ei->prefix;
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_sys_interface_walk(eigrp_instance_t *eigrp,
					eigrp_sys_interface_walk_cb callback,
					void *arg)
{
	eigrp_intf_runtime_state_t state = {0};

	assert(eigrp == &runtime);
	if (interface_present) {
		state.ifindex = local.index;
		state.address = local.prefix;
		callback(&state, arg);
	}
	return EIGRP_RESULT_SUCCESS;
}

static void checksum(void)
{
	uint32_t sum = 0;
	unsigned int length = (incoming[0] & 15U) * 4U;

	incoming[10] = 0;
	incoming[11] = 0;
	for (unsigned int i = 0; i < length; i += 2)
		sum += (uint16_t)((uint16_t)incoming[i] << 8 | incoming[i + 1]);
	while (sum >> 16)
		sum = (sum & 0xffffU) + (sum >> 16);
	uint16_t value = (uint16_t)(~sum);
	incoming[10] = (uint8_t)(value >> 8);
	incoming[11] = (uint8_t)value;
}

static void incoming_init(void)
{
	memset(incoming, 0, sizeof(incoming));
	incoming_size = 40;
	incoming[0] = 0x45;
	incoming[2] = 0;
	incoming[3] = 40;
	incoming[8] = 1;
	incoming[9] = 88;
	memcpy(incoming + 12, "\xc0\x00\x02\x02", 4);
	memcpy(incoming + 16, "\xe0\x00\x00\x0a", 4);
	checksum();
}

static bool receive_frame(size_t capacity, eigrp_packet_rx_meta_t *metadata)
{
	uint8_t frame[256] = {0};
	size_t size = SIZE_MAX;
	eigrp_ifindex_t index = 0;
	eigrp_address_t source = {0};
	eigrp_address_t destination = {0};
	bool result = eigrp_sys_packet_receive(&runtime, frame, capacity, &size,
					       &index, &source, &destination,
					       metadata);

	if (result) {
		assert(size == incoming_size && index == 11);
		assert(source.afi == EIGRP_AFI_IPV4);
		assert(destination.afi == EIGRP_AFI_IPV4);
		assert(memcmp(source.bytes, incoming + 12, 4) == 0);
		assert(memcmp(destination.bytes, incoming + 16, 4) == 0);
	} else {
		assert(size == SIZE_MAX);
	}
	return result;
}

static void test_socket_lifecycle(void)
{
	assert(eigrp_sys_socket_open(NULL) == EIGRP_RESULT_INVALID_ARGUMENT);
	other.vrf = 23;
	assert(eigrp_sys_socket_open(&other) == EIGRP_RESULT_UNSUPPORTED);
	other.vrf = 0;
	runtime.vrf = 23;
	assert(eigrp_sys_socket_open(&runtime) == EIGRP_RESULT_UNSUPPORTED);
	runtime.vrf = 0;
	fail_socket = 1;
	assert(eigrp_sys_socket_open(&runtime)
	       == EIGRP_RESULT_INTERNAL_FAILURE);
	assert(eigrp_linux_packet_fd(&runtime) < 0);
	fail_socket = 0;
	fail_option = IP_PKTINFO;
	assert(eigrp_sys_socket_open(&runtime)
	       == EIGRP_RESULT_INTERNAL_FAILURE);
	assert(opened == closed);
	fail_option = 0;
	assert(eigrp_sys_socket_open(&runtime) == EIGRP_RESULT_SUCCESS);
	assert(eigrp_linux_packet_fd(&runtime) >= 100);
	int before = opened;
	assert(eigrp_sys_socket_open(&runtime) == EIGRP_RESULT_SUCCESS);
	assert(opened == before);
	assert(option_calls >= 7);
	eigrp_sys_socket_send_buffer_ensure(&runtime, 65536);
	assert(last_option == SO_SNDBUF);
	assert(eigrp_sys_multicast_join(&runtime, &local) == 0);
	assert(eigrp_sys_multicast_interface_update(EIGRP_SET, &runtime, &local)
	       == 0);
	assert(eigrp_sys_multicast_leave(&runtime, &local) == 0);
	assert(joined == 1 && left == 1 && multicast_if == 1);
	fail_option = IP_ADD_MEMBERSHIP;
	assert(eigrp_sys_multicast_join(&runtime, &local) < 0);
	assert(errno == EIO);
	fail_option = 0;
	assert(eigrp_sys_multicast_interface_update(EIGRP_RESET, &runtime,
						    &local)
	       < 0);
	/* Invalid kernel interface identities must not be truncated to int. */
	eigrp_ifindex_t saved_index = local.index;
	local.index = UINT32_MAX;
	assert(eigrp_sys_multicast_join(&runtime, &local) < 0);
	assert(errno == EINVAL);
	local.index = saved_index;
	puts("IPv4 raw socket, privileges, multicast, cleanup: PASS");
}

static void test_send(void)
{
	eigrp_address_t dst = {.afi = EIGRP_AFI_IPV4};
	uint8_t payload[20] = {0};

	memcpy(dst.bytes, "\xe0\x00\x00\x0a", 4);
	assert(eigrp_sys_packet_send(&runtime, &local, &dst, payload,
				     sizeof(payload))
	       == 20);
	assert(sent == 1 && sent_size == 20);
	assert(sent_info.ipi_ifindex == 11);
	assert(memcmp(&sent_info.ipi_spec_dst, local.prefix.address.bytes, 4)
	       == 0);
	assert(last_send_flags == MSG_DONTWAIT);
	memcpy(dst.bytes, "\xc0\x00\x02\x02", 4);
	assert(eigrp_sys_packet_send(&runtime, &local, &dst, payload,
				     sizeof(payload))
	       == 20);
	assert(last_send_flags & MSG_DONTROUTE);
	send_fail = 1;
	assert(eigrp_sys_packet_send(&runtime, &local, &dst, payload, 20) < 0);
	send_fail = 0;
	assert(eigrp_sys_packet_send(&runtime, &local, &dst, payload, 19) < 0);
	assert(errno == EMSGSIZE);
	assert(eigrp_sys_packet_send(&runtime, &local, &dst, payload, 65516)
	       < 0);
	assert(errno == EMSGSIZE);
	dst.afi = EIGRP_AFI_IPV6;
	assert(eigrp_sys_packet_send(&runtime, &local, &dst, payload, 20) < 0);
	puts("IPv4 multicast/unicast TX and send failures: PASS");
}

static void test_receive(void)
{
	eigrp_packet_rx_meta_t meta = {0};
	uint8_t copy[256];

	incoming_init();
	recv_fail =
		1; /* Loss/no pending packet does not corrupt socket state. */
	assert(!receive_frame(256, &meta));
	recv_fail = 0;
	assert(receive_frame(256, &meta));
	assert(meta.ingress_vrf_id == EIGRP_VRF_DEFAULT);
	assert(meta.network_header_length == 20 && meta.eigrp_length == 20);
	assert(meta.destination_multicast);
	assert(!receive_frame(39, &meta)); /* MSG_TRUNC-sized body */
	assert(!receive_frame(0, &meta));
	assert(!receive_frame(1, &meta));

	/* IP options and checksum cover the full IPv4 header. */
	incoming_size = 44;
	incoming[0] = 0x46;
	incoming[3] = 44;
	memmove(incoming + 24, incoming + 20, 20);
	incoming[20] = 1;
	incoming[21] = 1;
	incoming[22] = 1;
	incoming[23] = 1;
	checksum();
	assert(receive_frame(256, &meta));
	assert(meta.network_header_length == 24 && meta.eigrp_length == 20);

	incoming_init();
	memcpy(copy, incoming, sizeof(copy));
	incoming[0] = 0x65;
	checksum();
	assert(!receive_frame(256, &meta));
	memcpy(incoming, copy, sizeof(copy));
	incoming[0] = 0x44;
	assert(!receive_frame(256, &meta));
	memcpy(incoming, copy, sizeof(copy));
	incoming[3] = 39;
	checksum();
	assert(!receive_frame(256, &meta));
	memcpy(incoming, copy, sizeof(copy));
	incoming[9] = 17;
	checksum();
	assert(!receive_frame(256, &meta));
	memcpy(incoming, copy, sizeof(copy));
	incoming[6] = 0x20; /* MF flag; fragmented IP is not admissible. */
	checksum();
	assert(!receive_frame(256, &meta));
	memcpy(incoming, copy, sizeof(copy));
	incoming[6] = 0x80; /* Reserved IPv4 fragmentation flag. */
	checksum();
	assert(!receive_frame(256, &meta));
	memcpy(incoming, copy, sizeof(copy));
	incoming[8] ^= 1; /* Invalid IPv4 checksum. */
	assert(!receive_frame(256, &meta));
	memcpy(incoming, copy, sizeof(copy));
	incoming_size = 39;
	incoming[3] = 39;
	checksum();
	assert(!receive_frame(256, &meta));
	incoming_init();
	receive_flags = MSG_CTRUNC;
	assert(!receive_frame(256, &meta));
	receive_flags = MSG_TRUNC;
	assert(!receive_frame(256, &meta));
	receive_flags = 0;
	no_control = 1;
	assert(!receive_frame(256, &meta));
	no_control = 0;
	wrong_destination = 1;
	assert(!receive_frame(256, &meta));
	wrong_destination = 0;
	interface_present = false;
	assert(!receive_frame(256, &meta));
	interface_present = true;
	assert(receive_frame(256, &meta));
	puts("Crafted IPv4 framing, metadata, packet loss/recovery: PASS");
}

int main(void)
{
	local.prefix.address.afi = EIGRP_AFI_IPV4;
	local.prefix.prefix_length = 24;
	memcpy(local.prefix.address.bytes, "\xc0\x00\x02\x01", 4);
	test_socket_lifecycle();
	test_send();
	test_receive();
	assert(eigrp_sys_socket_open(&runtime2) == EIGRP_RESULT_SUCCESS);
	assert(eigrp_linux_packet_fd(&runtime2)
	       != eigrp_linux_packet_fd(&runtime));
	eigrp_sys_socket_close(&runtime);
	assert(eigrp_linux_packet_fd(&runtime) < 0);
	assert(eigrp_linux_packet_fd(&runtime2) >= 0);
	eigrp_sys_socket_close(&runtime);
	eigrp_sys_socket_close(&runtime2);
	assert(opened == closed);
	assert(eigrp_sys_socket_open(&runtime) == EIGRP_RESULT_SUCCESS);
	eigrp_sys_socket_close(&runtime);
	assert(opened == closed);
	assert(runtime_bindings == 0);
	puts("Repeated shutdown and recovery: PASS");
	return 0;
}

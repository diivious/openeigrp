// SPDX-License-Identifier: GPL-2.0-or-later
/* Native IPv6 protocol-88 socket tests using production transport and mocked
 * syscalls. Copyright (C) 2026 Donnie V. Savage
 */
#include <arpa/inet.h>
#include <assert.h>
#include <errno.h>
#include <netinet/in.h>
#include <pthread.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <unistd.h>

struct eigrp_instance;
struct eigrp_interface;
static int fake_socket(int family, int type, int protocol);
static int fake_setsockopt(int fd, int level, int option, const void *value,
			   socklen_t size);
static int fake_getsockopt(int fd, int level, int option, void *value,
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

static struct eigrp_instance ipv6 = {.afi = EIGRP_AFI_IPV6};
static struct eigrp_instance ipv4 = {.afi = EIGRP_AFI_IPV4};
static struct eigrp_interface link6 = {.index = 11};
static struct eigrp_interface link4 = {.index = 12};
static int next_fd = 100;
static int family_by_fd[256];
static int members_by_fd[256];
static int opened;
static int closed;
static int fail_socket;
static int fail_option;
static int joins;
static int leaves;
static int selected_if;
static int option_count;
static int checksum_disabled;
static int sent;
static int send_fail;
static int recv_fail;
static int no_pktinfo;
static int no_hoplimit;
static int wrong_destination;
static int wrong_index;
static int wrong_scope;
static int wrong_source;
static int bad_cmsg;
static int recv_flags;
static int rx_hoplimit = 1;
static int interface_present = 1;
static size_t packet_len = 20;
static uint8_t payload[256];
static struct in6_addr local_addr;
static struct in6_addr peer_addr;
static struct in6_addr multicast_addr;
static struct in6_pktinfo sent_info;
static struct sockaddr_in6 sent_peer;
static uint8_t sent_payload[256];

static int fake_socket(int family, int type, int protocol)
{
	assert((family == AF_INET6 || family == AF_INET)
	       && protocol == EIGRP_LINUX_PROTO);
	assert((type & SOCK_RAW) == SOCK_RAW);
	assert(type & SOCK_NONBLOCK);
	assert(type & SOCK_CLOEXEC);
	if (fail_socket) {
		errno = EPERM;
		return -1;
	}
	family_by_fd[next_fd] = family;
	opened++;
	return next_fd++;
}

static int fake_setsockopt(int fd, int level, int option, const void *value,
			   socklen_t size)
{
	assert(fd >= 100 && fd < 256 && value && size);
	option_count++;
	if (option == fail_option && level == IPPROTO_IPV6) {
		errno = EIO;
		return -1;
	}
	if (family_by_fd[fd] == AF_INET6) {
		assert(level == IPPROTO_IPV6 || level == SOL_SOCKET);
		if (level == IPPROTO_IPV6 && option == IPV6_CHECKSUM) {
			assert(size == sizeof(int)
			       && *(const int *)value == -1);
			checksum_disabled++;
		} else if (option == IPV6_JOIN_GROUP
			   || option == IPV6_LEAVE_GROUP) {
			const struct ipv6_mreq *request = value;
			assert(size == sizeof(*request));
			assert(request->ipv6mr_interface == link6.index);
			assert(memcmp(&request->ipv6mr_multiaddr,
				      &multicast_addr, sizeof(multicast_addr))
			       == 0);
			if (option == IPV6_JOIN_GROUP) {
				assert(!members_by_fd[fd]);
				members_by_fd[fd]++;
				joins++;
			} else {
				assert(members_by_fd[fd] == 1);
				members_by_fd[fd]--;
				leaves++;
			}
		} else if (option == IPV6_MULTICAST_IF) {
			assert(size == sizeof(unsigned int));
			assert(*(const unsigned int *)value == link6.index);
			selected_if++;
		}
	} else {
		assert(level == IPPROTO_IP || level == SOL_SOCKET);
	}
	return 0;
}

static int fake_getsockopt(int fd, int level, int option, void *value,
			   socklen_t *size)
{
	assert(fd >= 100 && level == SOL_SOCKET && option == SO_SNDBUF);
	assert(*size >= sizeof(int));
	*(int *)value = 8192;
	return 0;
}

static int fake_close(int fd)
{
	assert(fd >= 100 && fd < 256);
	members_by_fd[fd] = 0; /* kernel drops outstanding group joins */
	family_by_fd[fd] = 0;
	closed++;
	return 0;
}

static ssize_t fake_sendmsg(int fd, const struct msghdr *msg, int flags)
{
	struct cmsghdr *control = CMSG_FIRSTHDR((struct msghdr *)msg);
	assert(msg->msg_iovlen == 1);
	if (family_by_fd[fd] == AF_INET) {
		struct sockaddr_in peer;
		struct in_pktinfo info;
		assert(flags == MSG_DONTWAIT);
		assert(msg->msg_namelen == sizeof(peer));
		memcpy(&peer, msg->msg_name, sizeof(peer));
		assert(peer.sin_family == AF_INET
		       && peer.sin_addr.s_addr == htonl(EIGRP_LINUX_GROUP));
		assert(control && control->cmsg_level == IPPROTO_IP
		       && control->cmsg_type == IP_PKTINFO
		       && control->cmsg_len == CMSG_LEN(sizeof(info)));
		memcpy(&info, CMSG_DATA(control), sizeof(info));
		assert(info.ipi_ifindex == (int)link4.index);
		assert(memcmp(&info.ipi_spec_dst, link4.prefix.address.bytes, 4)
		       == 0);
		assert(msg->msg_iov[0].iov_len == 20);
		sent++;
		return (ssize_t)msg->msg_iov[0].iov_len;
	}
	assert(family_by_fd[fd] == AF_INET6 && flags == MSG_DONTWAIT);
	assert(msg->msg_namelen == sizeof(sent_peer) && msg->msg_iovlen == 1);
	memcpy(&sent_peer, msg->msg_name, sizeof(sent_peer));
	assert(sent_peer.sin6_family == AF_INET6);
	assert(sent_peer.sin6_scope_id == link6.index);
	assert(control && control->cmsg_level == IPPROTO_IPV6
	       && control->cmsg_type == IPV6_PKTINFO
	       && control->cmsg_len == CMSG_LEN(sizeof(sent_info)));
	memcpy(&sent_info, CMSG_DATA(control), sizeof(sent_info));
	assert(sent_info.ipi6_ifindex == link6.index);
	assert(memcmp(&sent_info.ipi6_addr, &local_addr, 16) == 0);
	assert(msg->msg_iov[0].iov_len <= sizeof(sent_payload));
	memcpy(sent_payload, msg->msg_iov[0].iov_base, msg->msg_iov[0].iov_len);
	sent++;
	if (send_fail) {
		errno = ENOBUFS;
		return -1;
	}
	return (ssize_t)msg->msg_iov[0].iov_len;
}

static ssize_t fake_recvmsg(int fd, struct msghdr *msg, int flags)
{
	struct cmsghdr *control;
	struct in6_pktinfo *info;
	struct sockaddr_in6 *from = msg->msg_name;
	assert(family_by_fd[fd] == AF_INET6 && flags == MSG_DONTWAIT);
	if (recv_fail) {
		errno = EAGAIN;
		return -1;
	}
	assert(msg->msg_iovlen == 1);
	memcpy(msg->msg_iov[0].iov_base, payload,
	       packet_len < msg->msg_iov[0].iov_len ? packet_len
						    : msg->msg_iov[0].iov_len);
	msg->msg_flags = recv_flags;
	memset(from, 0, sizeof(*from));
	from->sin6_family = AF_INET6;
	from->sin6_addr = wrong_source ? multicast_addr : peer_addr;
	from->sin6_scope_id = wrong_scope ? 19 : link6.index;
	msg->msg_namelen = sizeof(*from);
	if (no_pktinfo) {
		msg->msg_controllen = 0;
		return (ssize_t)packet_len;
	}
	control = CMSG_FIRSTHDR(msg);
	assert(control != NULL);
	control->cmsg_level = IPPROTO_IPV6;
	control->cmsg_type = IPV6_PKTINFO;
	control->cmsg_len =
		bad_cmsg ? CMSG_LEN(0) : CMSG_LEN(sizeof(struct in6_pktinfo));
	info = (struct in6_pktinfo *)CMSG_DATA(control);
	memset(info, 0, sizeof(*info));
	info->ipi6_ifindex = wrong_index ? 19 : link6.index;
	info->ipi6_addr = wrong_destination ? peer_addr : multicast_addr;
	msg->msg_controllen = CMSG_SPACE(sizeof(*info));
	if (no_hoplimit)
		return (ssize_t)packet_len;
	control = (struct cmsghdr *)((char *)msg->msg_control
				     + CMSG_SPACE(sizeof(*info)));
	control->cmsg_level = IPPROTO_IPV6;
	control->cmsg_type = IPV6_HOPLIMIT;
	control->cmsg_len = CMSG_LEN(sizeof(int));
	memcpy(CMSG_DATA(control), &rx_hoplimit, sizeof(int));
	msg->msg_controllen =
		CMSG_SPACE(sizeof(*info)) + CMSG_SPACE(sizeof(int));
	return (ssize_t)packet_len;
}

eigrp_afi_t eigrp_instance_afi(const eigrp_instance_t *instance)
{
	return instance->afi;
}
eigrp_vrf_id_t eigrp_instance_vrf_id(const eigrp_instance_t *instance)
{
	return instance->vrf;
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
eigrp_result_t eigrp_sys_interface_walk(eigrp_instance_t *instance,
					eigrp_sys_interface_walk_cb callback,
					void *arg)
{
	eigrp_intf_runtime_state_t state = {0};
	if (interface_present) {
		state.ifindex = instance == &ipv6 ? link6.index : link4.index;
		state.address = instance == &ipv6 ? link6.prefix : link4.prefix;
		callback(&state, arg);
	}
	return EIGRP_RESULT_SUCCESS;
}

static bool receive_packet(void)
{
	eigrp_address_t src = {0};
	eigrp_address_t dst = {0};
	eigrp_packet_rx_meta_t meta = {0};
	eigrp_ifindex_t ifindex = 0;
	size_t size = SIZE_MAX;
	uint8_t buffer[256] = {0};
	bool ok = eigrp_sys_packet_receive(&ipv6, buffer, sizeof(buffer), &size,
					   &ifindex, &src, &dst, &meta);
	if (ok) {
		assert(size == packet_len && ifindex == link6.index);
		assert(src.afi == EIGRP_AFI_IPV6 && dst.afi == EIGRP_AFI_IPV6);
		assert(memcmp(src.bytes, &peer_addr, 16) == 0);
		assert(memcmp(dst.bytes, &multicast_addr, 16) == 0
		       || memcmp(dst.bytes, &peer_addr, 16) == 0);
		assert(meta.ingress_vrf_id == EIGRP_VRF_DEFAULT);
		assert(meta.network_header_length == 0);
		assert(meta.eigrp_length == packet_len);
		assert(meta.destination_multicast == !wrong_destination);
		assert(memcmp(buffer, payload, packet_len) == 0);
	} else {
		assert(size == SIZE_MAX);
	}
	return ok;
}

static void test_open(void)
{
	assert(eigrp_sys_socket_open(NULL) == EIGRP_RESULT_INVALID_ARGUMENT);
	ipv6.vrf = 9;
	assert(eigrp_sys_socket_open(&ipv6) == EIGRP_RESULT_UNSUPPORTED);
	ipv6.vrf = 0;
	fail_socket = 1;
	assert(eigrp_sys_socket_open(&ipv6) == EIGRP_RESULT_INTERNAL_FAILURE);
	assert(eigrp_linux_packet_fd(&ipv6) == -1);
	fail_socket = 0;
	fail_option = IPV6_CHECKSUM;
	assert(eigrp_sys_socket_open(&ipv6) == EIGRP_RESULT_INTERNAL_FAILURE);
	assert(opened == closed);
	fail_option = 0;
	assert(eigrp_sys_socket_open(&ipv6) == EIGRP_RESULT_SUCCESS);
	assert(checksum_disabled == 1);
	int fd = eigrp_linux_packet_fd(&ipv6);
	assert(family_by_fd[fd] == AF_INET6);
	assert(eigrp_sys_socket_open(&ipv6) == EIGRP_RESULT_SUCCESS);
	assert(eigrp_linux_packet_fd(&ipv6) == fd);
	eigrp_sys_socket_send_buffer_ensure(&ipv6, 65536);
	assert(option_count > 8);
	puts("IPv6-only socket options and privilege failures: PASS");
}

static void test_membership(void)
{
	assert(eigrp_sys_multicast_join(&ipv6, &link6) == 0);
	assert(eigrp_sys_multicast_interface_update(EIGRP_SET, &ipv6, &link6)
	       == 0);
	assert(eigrp_sys_multicast_leave(&ipv6, &link6) == 0);
	assert(joins == 1 && leaves == 1 && selected_if == 1);
	assert(eigrp_sys_multicast_interface_update(EIGRP_RESET, &ipv6, &link6)
	       < 0);
	assert(eigrp_sys_multicast_join(&ipv6, &link6) == 0);
	assert(eigrp_sys_multicast_leave(&ipv6, &link6) == 0);
	link6.index = UINT32_MAX;
	assert(eigrp_sys_multicast_join(&ipv6, &link6) == -1);
	link6.index = 11;
	link6.prefix.address.bytes[0] = 0x20;
	assert(eigrp_sys_multicast_join(&ipv6, &link6) == -1);
	link6.prefix.address.bytes[0] = 0xfe;
	assert(eigrp_sys_multicast_join(&ipv6, &link6) == 0);
	link6.prefix.address.bytes[0] = 0x20;
	assert(eigrp_sys_multicast_leave(&ipv6, &link6) == 0);
	link6.prefix.address.bytes[0] = 0xfe;
	assert(eigrp_sys_multicast_join(&ipv6, &link6) == 0);
	/* A disappeared interface must be retired even if no explicit leave
	 * ran. */
	eigrp_sys_socket_close(&ipv6);
	for (int fd = 100; fd < next_fd; fd++)
		assert(members_by_fd[fd] == 0);
	assert(eigrp_sys_socket_open(&ipv6) == EIGRP_RESULT_SUCCESS);
	assert(eigrp_sys_multicast_join(&ipv6, &link6) == 0);
	assert(eigrp_sys_multicast_leave(&ipv6, &link6) == 0);
	puts("IPv6 multicast FF02::A join/leave, address change, shutdown: PASS");
}

static void test_send(void)
{
	eigrp_address_t destination = {.afi = EIGRP_AFI_IPV6};
	uint8_t bytes[20] = {0x02, 0x05, 0x12, 0x34};
	memcpy(destination.bytes, &multicast_addr, 16);
	assert(eigrp_sys_packet_send(&ipv6, &link6, &destination, bytes,
				     sizeof(bytes))
	       == sizeof(bytes));
	assert(sent == 1 && memcmp(sent_payload, bytes, sizeof(bytes)) == 0);
	assert(memcmp(&sent_peer.sin6_addr, &multicast_addr, 16) == 0);
	memcpy(destination.bytes, &peer_addr, 16);
	assert(eigrp_sys_ipv6_packet_send(&ipv6, &link6, &destination, bytes,
					  sizeof(bytes))
	       == sizeof(bytes));
	assert(sent == 2);
	send_fail = 1;
	assert(eigrp_sys_packet_send(&ipv6, &link6, &destination, bytes, 20)
	       == -1);
	assert(errno == ENOBUFS);
	send_fail = 0;
	assert(eigrp_sys_packet_send(&ipv6, &link6, &destination, bytes, 19)
		       == -1
	       && errno == EMSGSIZE);
	assert(eigrp_sys_packet_send(&ipv6, &link6, &destination, bytes, 65536)
		       == -1
	       && errno == EMSGSIZE);
	destination.afi = EIGRP_AFI_IPV4;
	assert(eigrp_sys_packet_send(&ipv6, &link6, &destination, bytes, 20)
		       == -1
	       && errno == EINVAL);
	destination.afi = EIGRP_AFI_IPV6;
	memset(destination.bytes, 0, 16);
	assert(eigrp_sys_packet_send(&ipv6, &link6, &destination, bytes, 20)
	       == -1);
	puts("IPv6 scoped multicast/unicast TX, checksum ownership, failures: PASS");
}

static void test_receive(void)
{
	memcpy(payload, "\x02\x05\x00\x00", 4);
	recv_fail = 1;
	assert(!receive_packet());
	recv_fail = 0;
	assert(receive_packet());
	packet_len = 19;
	assert(!receive_packet());
	packet_len = 20;
	recv_flags = MSG_TRUNC;
	assert(!receive_packet());
	recv_flags = MSG_CTRUNC;
	assert(!receive_packet());
	recv_flags = 0;
	no_pktinfo = 1;
	assert(!receive_packet());
	no_pktinfo = 0;
	no_hoplimit = 1;
	assert(!receive_packet());
	no_hoplimit = 0;
	bad_cmsg = 1;
	assert(!receive_packet());
	bad_cmsg = 0;
	wrong_source = 1;
	assert(!receive_packet());
	wrong_source = 0;
	wrong_scope = 1;
	assert(!receive_packet());
	wrong_scope = 0;
	wrong_index = 1;
	assert(!receive_packet());
	wrong_index = 0;
	rx_hoplimit = 2;
	assert(!receive_packet());
	rx_hoplimit = 1;
	interface_present = 0;
	assert(!receive_packet());
	interface_present = 1;
	wrong_destination = 1;
	assert(receive_packet()); /* Valid link-local unicast */
	wrong_destination = 0;
	assert(receive_packet());
	puts("IPv6 payload framing, cmsgs, scope, loss/recovery: PASS");
}

static void test_dual_stack(void)
{
	eigrp_address_t dst = {.afi = EIGRP_AFI_IPV4};
	uint8_t packet[20] = {0x02, 0x05};
	int ipv6fd = eigrp_linux_packet_fd(&ipv6);
	int ipv4fd;

	assert(ipv6fd >= 0);
	assert(eigrp_sys_socket_open(&ipv4) == EIGRP_RESULT_SUCCESS);
	ipv4fd = eigrp_linux_packet_fd(&ipv4);
	assert(ipv4fd >= 0 && ipv4fd != ipv6fd);
	assert(family_by_fd[ipv4fd] == AF_INET);
	memcpy(dst.bytes, "\xe0\x00\x00\x0a", 4);
	assert(eigrp_sys_packet_send(&ipv4, &link4, &dst, packet,
				     sizeof(packet))
	       == sizeof(packet));
	assert(receive_packet()); /* IPv6 still receives alongside IPv4. */
	eigrp_sys_socket_close(&ipv4);
	assert(eigrp_linux_packet_fd(&ipv4) == -1);
	assert(eigrp_linux_packet_fd(&ipv6) == ipv6fd);
	assert(receive_packet());
	assert(eigrp_sys_socket_open(&ipv4) == EIGRP_RESULT_SUCCESS);
	assert(eigrp_linux_packet_fd(&ipv4) >= 0);
	eigrp_sys_socket_close(&ipv6);
	assert(eigrp_linux_packet_fd(&ipv6) == -1);
	assert(eigrp_sys_socket_open(&ipv6) == EIGRP_RESULT_SUCCESS);
	assert(receive_packet());
	assert(eigrp_linux_packet_fd(&ipv4) >= 0);
	puts("IPv4/IPv6 concurrent send/receive, shutdown and restart: PASS");
}

int main(void)
{
	assert(inet_pton(AF_INET6, "fe80::1", &local_addr) == 1);
	assert(inet_pton(AF_INET6, "fe80::2", &peer_addr) == 1);
	assert(inet_pton(AF_INET6, "ff02::a", &multicast_addr) == 1);
	link6.prefix.address.afi = EIGRP_AFI_IPV6;
	link6.prefix.prefix_length = 64;
	memcpy(link6.prefix.address.bytes, &local_addr, 16);
	link4.prefix.address.afi = EIGRP_AFI_IPV4;
	link4.prefix.prefix_length = 24;
	memcpy(link4.prefix.address.bytes, "\xc0\x00\x02\x01", 4);
	test_open();
	test_membership();
	test_send();
	test_receive();
	test_dual_stack();
	eigrp_sys_socket_close(&ipv6);
	eigrp_sys_socket_close(&ipv4);
	assert(opened == closed);
	assert(eigrp_linux_packet_fd(&ipv6) == -1);
	assert(eigrp_linux_packet_fd(&ipv4) == -1);
	puts("IPv4/IPv6 independent final shutdown: PASS");
	return 0;
}

// SPDX-License-Identifier: GPL-2.0-or-later
/* Native Linux Netlink and public interface contract regression harness.
 * Copyright (C) 2026 Donnie V. Savage
 *
 * Compile the actual provider into the harness to exercise its internal
 * RTNETLINK message parser, without production test hooks or UUT state.
 */
#include <assert.h>
#include <arpa/inet.h>
#include <fcntl.h>
#include <linux/if_addr.h>
#include <stdio.h>
#include <sys/wait.h>

#include "../code/eigrp_linux_interface.c"

struct test_event {
	char action;
	eigrp_ifindex_t ifindex;
	eigrp_prefix_t prefix;
	bool operative;
	uint32_t mtu;
	char name[IFNAMSIZ];
};

#define TEST_EVENT_MAX 16384
static struct test_event events[TEST_EVENT_MAX];
static size_t event_count;
static eigrp_afi_t test_afi = EIGRP_AFI_IPV4;
static eigrp_vrf_id_t test_vrf = EIGRP_VRF_DEFAULT;

static struct test_event *test_event_add(char action, eigrp_ifindex_t ifindex)
{
	struct test_event *event;

	assert(event_count < TEST_EVENT_MAX);
	event = &events[event_count++];
	memset(event, 0, sizeof(*event));
	event->action = action;
	event->ifindex = ifindex;
	return event;
}

eigrp_afi_t eigrp_instance_afi(const eigrp_instance_t *eigrp)
{
	assert(eigrp);
	return test_afi;
}

eigrp_vrf_id_t eigrp_instance_vrf_id(const eigrp_instance_t *eigrp)
{
	assert(eigrp);
	return test_vrf;
}

void eigrp_sys_intf_update(eigrp_vrf_id_t vrf_id,
			   const eigrp_intf_runtime_state_t *state)
{
	struct test_event *event;

	assert(vrf_id == EIGRP_VRF_DEFAULT);
	assert(state->bandwidth > 0);
	assert(state->mtu > 0);
	event = test_event_add('U', state->ifindex);
	event->prefix = state->address;
	event->operative = state->operative;
	event->mtu = state->mtu;
	strncpy(event->name, state->interface_name, sizeof(event->name) - 1);
}

void eigrp_sys_intf_down(eigrp_vrf_id_t vrf_id, eigrp_ifindex_t ifindex,
			 const char *name, uint8_t type, uint32_t bandwidth,
			 uint32_t mtu)
{
	(void)type;
	(void)bandwidth;
	assert(vrf_id == EIGRP_VRF_DEFAULT);
	assert(name);
	test_event_add('D', ifindex)->mtu = mtu;
}

void eigrp_sys_intf_remove(eigrp_vrf_id_t vrf_id, eigrp_ifindex_t ifindex,
			   eigrp_intf_remove_reason_t reason)
{
	assert(vrf_id == EIGRP_VRF_DEFAULT);
	assert(reason == EIGRP_INTERFACE_REMOVE_HOST);
	test_event_add('R', ifindex);
}

void eigrp_sys_intf_addr_update(eigrp_vrf_id_t vrf_id, eigrp_ifindex_t ifindex,
				const eigrp_prefix_t *prefix,
				eigrp_intf_remove_reason_t reason)
{
	assert(vrf_id == EIGRP_VRF_DEFAULT);
	assert(reason == EIGRP_INTERFACE_REMOVE_HOST);
	test_event_add('A', ifindex)->prefix = *prefix;
}

struct test_packet {
	union {
		struct nlmsghdr header;
		uint64_t align;
	} base;
	unsigned char payload[512];
};

static struct nlmsghdr *test_packet_init(struct test_packet *packet,
					 uint16_t type, size_t payload_size)
{
	struct nlmsghdr *header = &packet->base.header;

	memset(packet, 0, sizeof(*packet));
	header->nlmsg_type = type;
	header->nlmsg_len = NLMSG_LENGTH(payload_size);
	assert(header->nlmsg_len <= sizeof(*packet));
	return header;
}

static void test_packet_attr(struct nlmsghdr *header, uint16_t type,
			     const void *value, size_t length)
{
	size_t offset = NLMSG_ALIGN(header->nlmsg_len);
	struct rtattr *attr =
		(struct rtattr *)((unsigned char *)header + offset);

	assert(offset + RTA_SPACE(length) <= sizeof(struct test_packet));
	attr->rta_type = type;
	attr->rta_len = RTA_LENGTH(length);
	memcpy(RTA_DATA(attr), value, length);
	header->nlmsg_len = offset + RTA_SPACE(length);
}

static struct nlmsghdr *test_link_packet(struct test_packet *packet, int index,
					 const char *name, unsigned int flags,
					 unsigned int mtu, uint16_t type)
{
	struct nlmsghdr *header =
		test_packet_init(packet, type, sizeof(struct ifinfomsg));
	struct ifinfomsg *info = NLMSG_DATA(header);

	info->ifi_index = index;
	info->ifi_flags = flags;
	if (name)
		test_packet_attr(header, IFLA_IFNAME, name, strlen(name) + 1);
	if (mtu)
		test_packet_attr(header, IFLA_MTU, &mtu, sizeof(mtu));
	return header;
}

static void test_link_master(struct nlmsghdr *header, uint32_t master)
{
	test_packet_attr(header, IFLA_MASTER, &master, sizeof(master));
}

static void test_link_kind(struct nlmsghdr *header, const char *kind)
{
	unsigned char nested[64] = {0};
	struct rtattr *attr = (struct rtattr *)nested;

	assert(RTA_SPACE(strlen(kind) + 1) <= sizeof(nested));
	attr->rta_type = IFLA_INFO_KIND;
	attr->rta_len = RTA_LENGTH(strlen(kind) + 1);
	memcpy(RTA_DATA(attr), kind, strlen(kind) + 1);
	test_packet_attr(header, IFLA_LINKINFO, nested,
			 RTA_ALIGN(attr->rta_len));
}

static struct nlmsghdr *test_addr_packet(struct test_packet *packet, int index,
					 int family, const char *address,
					 int prefixlen, uint32_t flags,
					 uint16_t type)
{
	struct nlmsghdr *header =
		test_packet_init(packet, type, sizeof(struct ifaddrmsg));
	struct ifaddrmsg *info = NLMSG_DATA(header);
	unsigned char bytes[16] = {0};
	size_t length = family == AF_INET ? 4 : 16;

	assert(inet_pton(family, address, bytes) == 1);
	info->ifa_family = family;
	info->ifa_index = index;
	info->ifa_prefixlen = prefixlen;
	info->ifa_flags = flags;
	test_packet_attr(header, family == AF_INET ? IFA_LOCAL : IFA_ADDRESS,
			 bytes, length);
	return header;
}

static void test_commit(const struct nlmsghdr *header)
{
	struct eigrp_linux_inventory *copy =
		eigrp_linux_inventory_copy(&inventory);

	assert(copy);
	assert(eigrp_linux_message_apply(copy, header));
	eigrp_linux_inventory_reconcile(&inventory, copy);
	eigrp_linux_link_free(inventory.links);
	inventory.links = copy->links;
	inventory.generation = copy->generation;
	free(copy);
}

static size_t test_event_total(char action)
{
	size_t count = 0;

	for (size_t i = 0; i < event_count; i++)
		if (events[i].action == action)
			count++;
	return count;
}

struct test_walk {
	size_t count;
	bool operative;
	bool secondary;
	bool has_index;
	uint32_t mtu;
};

static void test_walk_callback(const eigrp_intf_runtime_state_t *state,
			       void *arg)
{
	struct test_walk *walk = arg;

	assert(state->ifindex);
	assert(state->interface_name[0]);
	assert(state->bandwidth);
	walk->count++;
	walk->operative |= state->operative;
	walk->secondary |= state->secondary;
	walk->has_index = true;
	walk->mtu = state->mtu;
}

static struct test_walk test_walk_family(eigrp_afi_t afi)
{
	struct test_walk result = {0};

	test_afi = afi;
	assert(eigrp_sys_interface_walk((eigrp_instance_t *)&test_afi,
					test_walk_callback, &result)
	       == EIGRP_RESULT_SUCCESS);
	return result;
}

static void test_fixture_lifecycle(void)
{
	struct test_packet packet;
	struct nlmsghdr *header;
	struct test_walk walk;
	size_t before;
	uint32_t master = 900;
	int fd = open("/dev/null", O_RDONLY | O_CLOEXEC);

	assert(fd >= 0);
	inventory.fd = fd;
	/* Address is allowed to arrive before a link in the same or a later
	 * Netlink batch.  It is not advertised without a valid link name. */
	header = test_addr_packet(&packet, 100, AF_INET, "192.0.2.1", 24, 0,
				  RTM_NEWADDR);
	test_commit(header);
	assert(event_count == 0);
	header = test_link_packet(&packet, 100, "t4test0", IFF_UP | IFF_RUNNING,
				  1500, RTM_NEWLINK);
	test_commit(header);
	assert(test_event_total('U') == 1);
	walk = test_walk_family(EIGRP_AFI_IPV4);
	assert(walk.count == 1 && walk.operative && walk.mtu == 1500);
	/* Duplicated notifications are idempotent. */
	before = event_count;
	test_commit(header);
	assert(event_count == before);

	header = test_addr_packet(&packet, 100, AF_INET6, "2001:db8::1", 64, 0,
				  RTM_NEWADDR);
	test_commit(header);
	assert(test_walk_family(EIGRP_AFI_IPV6).count == 1);
	assert(test_walk_family(EIGRP_AFI_IPV4).count == 1);
	header = test_addr_packet(&packet, 100, AF_INET, "192.0.2.2", 24,
				  IFA_F_SECONDARY, RTM_NEWADDR);
	test_commit(header);
	walk = test_walk_family(EIGRP_AFI_IPV4);
	assert(walk.count == 2 && walk.secondary);

	before = test_event_total('D');
	header = test_link_packet(&packet, 100, "t4test0", IFF_UP, 1500,
				  RTM_NEWLINK);
	test_commit(header);
	assert(test_event_total('D') == before + 1);
	walk = test_walk_family(EIGRP_AFI_IPV4);
	assert(walk.count == 2 && !walk.operative);
	header = test_link_packet(&packet, 100, "t4test0", IFF_UP | IFF_RUNNING,
				  1400, RTM_NEWLINK);
	test_commit(header);
	walk = test_walk_family(EIGRP_AFI_IPV4);
	assert(walk.operative && walk.mtu == 1400);

	before = test_event_total('A');
	header = test_addr_packet(&packet, 100, AF_INET, "192.0.2.1", 24, 0,
				  RTM_DELADDR);
	test_commit(header);
	assert(test_event_total('A') == before + 1);
	assert(test_walk_family(EIGRP_AFI_IPV4).count == 1);

	/* VRF master transitions must remove and later restore default-VRF
	 * interfaces, without dropping bridge/bond slaves. */
	header = test_link_packet(&packet, 900, "t4vrf", IFF_UP, 1500,
				  RTM_NEWLINK);
	test_link_kind(header, "vrf");
	test_commit(header);
	before = test_event_total('R');
	header = test_link_packet(&packet, 100, "t4test0", IFF_UP | IFF_RUNNING,
				  1400, RTM_NEWLINK);
	test_link_master(header, master);
	test_commit(header);
	assert(test_event_total('R') == before + 1);
	assert(test_walk_family(EIGRP_AFI_IPV4).count == 0);
	header = test_link_packet(&packet, 100, "t4test0", IFF_UP | IFF_RUNNING,
				  1400, RTM_NEWLINK);
	test_link_master(header, 0);
	test_commit(header);
	assert(test_walk_family(EIGRP_AFI_IPV4).count == 1);

	/* DELETE + NEWLINK with the same ifindex must retire the old identity.
	 */
	before = test_event_total('R');
	header = test_link_packet(&packet, 100, NULL, 0, 0, RTM_DELLINK);
	test_commit(header);
	assert(test_event_total('R') == before + 1);
	header = test_link_packet(&packet, 100, "t4test0", IFF_UP | IFF_RUNNING,
				  1500, RTM_NEWLINK);
	test_commit(header);
	assert(test_walk_family(EIGRP_AFI_IPV4).count == 0);
	header = test_addr_packet(&packet, 100, AF_INET, "192.0.2.5", 24, 0,
				  RTM_NEWADDR);
	test_commit(header);
	assert(test_walk_family(EIGRP_AFI_IPV4).count == 1);

	/* Invalid TLV framing and unusable prefixes cannot pollute inventory.
	 */
	header = test_addr_packet(&packet, 100, AF_INET6, "2001:db8::5", 129, 0,
				  RTM_NEWADDR);
	assert(!eigrp_linux_message_apply(&inventory, header));
	header = test_link_packet(&packet, 101, "t4test1", IFF_UP, 1500,
				  RTM_NEWLINK);
	((struct rtattr *)((unsigned char *)header
			   + NLMSG_ALIGN(
				   NLMSG_LENGTH(sizeof(struct ifinfomsg)))))
		->rta_len = 1;
	assert(!eigrp_linux_message_apply(&inventory, header));

	/* Repeated create/delete cycles must reclaim every address. */
	for (int i = 0; i < 120; i++) {
		header = test_link_packet(&packet, 101, "t4temp", IFF_UP, 1500,
					  RTM_NEWLINK);
		test_commit(header);
		header = test_addr_packet(&packet, 101, AF_INET, "198.51.100.1",
					  24, 0, RTM_NEWADDR);
		test_commit(header);
		header =
			test_link_packet(&packet, 101, NULL, 0, 0, RTM_DELLINK);
		test_commit(header);
		assert(!eigrp_linux_link_find(&inventory, 101));
	}
	eigrp_linux_intf_stop();
	assert(inventory.fd < 0 && inventory.links == NULL);
	puts("fixture Netlink lifecycle: PASS");
}

static void test_live_snapshot(void)
{
	struct test_walk v4, v6;
	int fd;

	assert(eigrp_linux_intf_start() == EIGRP_RESULT_SUCCESS);
	assert(eigrp_linux_intf_fd() >= 0);
	v4 = test_walk_family(EIGRP_AFI_IPV4);
	v6 = test_walk_family(EIGRP_AFI_IPV6);
	assert(v4.count >= 1); /* loopback IPv4 */
	printf("kernel Netlink snapshot: IPv4=%zu IPv6=%zu\n", v4.count,
	       v6.count);
	assert(eigrp_linux_intf_process() == EIGRP_RESULT_SUCCESS);
	fd = inventory.fd;
	assert(close(fd) == 0);
	assert(eigrp_linux_intf_process() == EIGRP_RESULT_SUCCESS);
	assert(inventory.fd >= 0);
	assert(test_walk_family(EIGRP_AFI_IPV4).count >= 1);
	eigrp_linux_intf_stop();
	assert(eigrp_linux_intf_fd() < 0);
	assert(eigrp_linux_intf_start() == EIGRP_RESULT_SUCCESS);
	eigrp_linux_intf_stop();
	puts("kernel Netlink recovery/restart: PASS");
}

static void test_shell(const char *command)
{
	int code = system(command);

	if (code != 0) {
		fprintf(stderr, "command failed (%d): %s\n", code, command);
		exit(1);
	}
}

static void test_kernel_drain(void)
{
	for (int i = 0; i < 5; i++) {
		struct pollfd ready = {.fd = inventory.fd, .events = POLLIN};
		int result = poll(&ready, 1, 80);

		if (result < 0 && errno == EINTR)
			continue;
		if (result < 0) {
			perror("poll");
			exit(1);
		}
		assert(eigrp_linux_intf_process() == EIGRP_RESULT_SUCCESS);
		if (!result)
			break;
	}
}

static void test_namespace_lifecycle(void)
{
	unsigned int ifindex;
	size_t before;
	struct test_walk walk;

	assert(eigrp_linux_intf_start() == EIGRP_RESULT_SUCCESS);
	test_shell("ip link set lo up");
	test_shell("ip link add t4a type veth peer name t4b");
	test_kernel_drain();
	ifindex = if_nametoindex("t4a");
	assert(ifindex > 0);
	assert(eigrp_linux_link_find(&inventory, ifindex));
	test_shell("ip link set t4a up; ip link set t4b up");
	test_shell("ip addr add 192.0.2.1/24 dev t4a");
	test_shell("ip -6 addr add 2001:db8::1/64 dev t4a nodad");
	test_kernel_drain();
	walk = test_walk_family(EIGRP_AFI_IPV4);
	assert(walk.count >= 2 && walk.operative);
	walk = test_walk_family(EIGRP_AFI_IPV6);
	assert(walk.count >= 1);

	test_shell("ip link set t4a mtu 1400");
	test_kernel_drain();
	assert(eigrp_linux_link_find(&inventory, ifindex)->mtu == 1400);
	before = test_event_total('D');
	test_shell("ip link set t4a down");
	test_kernel_drain();
	assert(test_event_total('D') > before);
	test_shell("ip link set t4a up");
	test_kernel_drain();
	assert(eigrp_linux_link_find(&inventory, ifindex)->flags & IFF_UP);

	before = test_event_total('A');
	test_shell("ip addr del 192.0.2.1/24 dev t4a");
	test_kernel_drain();
	assert(test_event_total('A') > before);
	before = test_event_total('R');
	test_shell("ip link del t4a");
	test_kernel_drain();
	assert(test_event_total('R') > before);
	assert(!eigrp_linux_link_find(&inventory, ifindex));
	test_shell("ip link add t4a type veth peer name t4b");
	test_shell("ip link set t4a up; ip link set t4b up");
	test_shell("ip addr add 198.51.100.1/24 dev t4a");
	test_kernel_drain();
	assert(eigrp_linux_link_find(&inventory, if_nametoindex("t4a")));
	test_shell("ip link del t4a");
	test_kernel_drain();
	eigrp_linux_intf_stop();
	puts("network namespace: link/address change and recreation: PASS");
}

int main(int argc, char **argv)
{
	if (argc == 2 && strcmp(argv[1], "--namespace") == 0)
		test_namespace_lifecycle();
	else {
		test_fixture_lifecycle();
		test_live_snapshot();
	}
	return 0;
}

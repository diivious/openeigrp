// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Run the production Linux Netlink provider in a private Linux network ns.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <assert.h>
#include <arpa/inet.h>
#include <net/if.h>
#include <stdio.h>
#include <sys/wait.h>

#include "../code/eigrp_linux_rib.c"

static eigrp_afi_t test_afi = EIGRP_AFI_IPV4;
static eigrp_vrf_id_t test_vrf = EIGRP_VRF_DEFAULT;
static eigrp_instance_t *const test_instance = (void *)(uintptr_t)0x1000;

eigrp_afi_t eigrp_instance_afi(const eigrp_instance_t *eigrp)
{
	assert(eigrp == test_instance);
	return test_afi;
}

eigrp_vrf_id_t eigrp_instance_vrf_id(const eigrp_instance_t *eigrp)
{
	assert(eigrp == test_instance);
	return test_vrf;
}

static void command(const char *cmd)
{
	int status = system(cmd);
	if (status != 0) {
		fprintf(stderr, "command failed: %s (%d)\n", cmd, status);
		abort();
	}
}

static bool output_has(const char *command_line, const char *needle)
{
	char buffer[8192] = {0};
	FILE *pipe = popen(command_line, "r");
	size_t size;

	assert(pipe);
	size = fread(buffer, 1, sizeof(buffer) - 1, pipe);
	buffer[size] = 0;
	assert(pclose(pipe) == 0);
	return strstr(buffer, needle) != NULL;
}

static eigrp_prefix_t prefix(const char *address, eigrp_afi_t afi, uint8_t bits)
{
	eigrp_prefix_t value = {0};
	value.address.afi = afi;
	value.prefix_length = bits;
	assert(inet_pton(afi == EIGRP_AFI_IPV4 ? AF_INET : AF_INET6, address,
			 value.address.bytes)
	       == 1);
	return value;
}

static eigrp_rib_route_t route(eigrp_prefix_t key, eigrp_rib_nexthop_t *hop,
			       size_t count, uint32_t distance)
{
	eigrp_rib_route_t value = {0};
	value.prefix = key;
	value.nexthops = hop;
	value.nexthop_count = count;
	value.install.admin_dist = distance;
	value.install.type = EIGRP_RIB_ROUTE_INTERNAL;
	value.metric = 51200;
	return value;
}

static void fixture(void)
{
	struct eigrp_linux_rib_message msg;
	struct eigrp_linux_rib_lookup found = {0};
	eigrp_rib_nexthop_t hop = {.ifindex = 2};
	eigrp_rib_route_t input =
		route(prefix("192.0.2.19", EIGRP_AFI_IPV4, 24), &hop, 1, 90);
	struct eigrp_linux_rib_route copy;

	assert(eigrp_linux_rib_route_copy(&copy, &input)
	       == EIGRP_RESULT_SUCCESS);
	assert(copy.prefix.address.bytes[3] == 0);
	assert(eigrp_linux_rib_message_make(&msg, &copy, false));
	assert(msg.header.nlmsg_type == RTM_NEWROUTE);
	assert(msg.route.rtm_protocol == RTPROT_EIGRP);
	assert(msg.route.rtm_table == RT_TABLE_MAIN);
	assert(eigrp_linux_rib_message_make(&msg, &copy, true));
	assert(msg.header.nlmsg_type == RTM_DELROUTE);
	free(copy.nexthops);
	copy.nexthops = NULL;
	{
		eigrp_rib_nexthop_t multipath[2] = {
			{.ifindex = 2, .weight = 3},
			{.ifindex = 3, .weight = 1, .gateway_present = true},
		};
		assert(inet_pton(AF_INET, "192.0.2.1",
				 multipath[1].gateway.bytes)
		       == 1);
		multipath[1].gateway.afi = EIGRP_AFI_IPV4;
		input.nexthops = multipath;
		input.nexthop_count = 2;
		assert(eigrp_linux_rib_route_copy(&copy, &input)
		       == EIGRP_RESULT_SUCCESS);
		assert(eigrp_linux_rib_message_make(&msg, &copy, false));
		found.key.prefix = copy.prefix;
		found.key.priority = copy.priority;
		assert(eigrp_linux_rib_decode(&msg.header, &found));
		assert(found.present && !found.conflict);
		assert(found.found.count == 2);
		assert(found.found.nexthops[0].weight == 3);
		assert(found.found.nexthops[1].gateway_present);
		assert(!memcmp(found.found.nexthops[1].gateway.bytes,
			       multipath[1].gateway.bytes, 4));
		free(found.found.nexthops);
		free(copy.nexthops);
		copy.nexthops = NULL;
		found.present = false;
		found.conflict = false;
		msg.header.nlmsg_len = NLMSG_LENGTH(sizeof(struct rtmsg)) - 1;
		assert(!eigrp_linux_rib_decode(&msg.header, &found));
		input.nexthops = &hop;
	}
	input.nexthop_count = 65;
	assert(eigrp_linux_rib_route_copy(&copy, &input)
	       == EIGRP_RESULT_INVALID_ARGUMENT);
	input.nexthop_count = 1;
	input.prefix.prefix_length = 33;
	assert(eigrp_linux_rib_route_copy(&copy, &input)
	       == EIGRP_RESULT_INVALID_ARGUMENT);
	found.key.prefix = prefix("198.51.100.0", EIGRP_AFI_IPV4, 24);
	found.key.priority = 90;
	assert(!eigrp_linux_rib_decode(
		&(struct nlmsghdr){.nlmsg_type = RTM_NEWROUTE, .nlmsg_len = 4},
		&found));
	puts("Netlink route encoding, validation, malformed dump: PASS");
}

static void kernel(void)
{
	eigrp_rib_nexthop_t hops[2] = {{0}};
	eigrp_rib_route_t input;
	eigrp_prefix_t key;
	int veth0, veth1;
	pid_t child;
	int status;

	command("ip link set lo up");
	command("ip link add veth0 type veth peer name veth1");
	command("ip link set veth0 up");
	command("ip link set veth1 up");
	command("ip -4 addr add 10.11.0.1/24 dev veth0");
	veth0 = if_nametoindex("veth0");
	veth1 = if_nametoindex("veth1");
	assert(veth0 > 0 && veth1 > 0);
	hops[0].ifindex = veth0;
	hops[1].ifindex = veth1;
	key = prefix("198.51.100.0", EIGRP_AFI_IPV4, 24);
	input = route(key, hops, 1, 90);
	eigrp_rib_init();
	assert(eigrp_rib_route_add(test_instance, &input)
	       == EIGRP_RESULT_SUCCESS);
	assert(output_has("ip -4 route show 198.51.100.0/24", "proto eigrp"));
	assert(output_has("ip -4 route show 198.51.100.0/24", "veth0"));
	assert(eigrp_rib_route_add(test_instance, &input)
	       == EIGRP_RESULT_SUCCESS);
	input.nexthops = &hops[1];
	assert(eigrp_rib_route_add(test_instance, &input)
	       == EIGRP_RESULT_SUCCESS);
	assert(output_has("ip -4 route show 198.51.100.0/24", "veth1"));
	input.nexthops = hops;
	hops[0].gateway_present = true;
	hops[0].gateway.afi = EIGRP_AFI_IPV4;
	assert(inet_pton(AF_INET, "10.11.0.2", hops[0].gateway.bytes) == 1);
	assert(eigrp_rib_route_add(test_instance, &input)
	       == EIGRP_RESULT_SUCCESS);
	assert(output_has("ip -4 route show 198.51.100.0/24", "via 10.11.0.2"));
	input.install.admin_dist = 170;
	assert(eigrp_rib_route_add(test_instance, &input)
	       == EIGRP_RESULT_SUCCESS);
	assert(output_has("ip -4 route show 198.51.100.0/24", "metric 170"));
	input.install.admin_dist = 90;
	hops[0].gateway_present = false;
	assert(eigrp_rib_route_add(test_instance, &input)
	       == EIGRP_RESULT_SUCCESS);
	assert(output_has("ip -4 route show 198.51.100.0/24", "metric 90"));
	input.nexthop_count = 2;
	hops[0].weight = 3;
	hops[1].weight = 1;
	{
		eigrp_result_t result =
			eigrp_rib_route_add(test_instance, &input);
		if (result == EIGRP_RESULT_SUCCESS) {
			assert(output_has("ip -4 route show 198.51.100.0/24",
					  "nexthop"));
			assert(output_has("ip -4 route show 198.51.100.0/24",
					  "weight 3"));
			puts("IPv4 weighted ECMP kernel install: PASS");
		} else {
			/* Containers without CONFIG_IP_ROUTE_MULTIPATH reject
			 * ECMP. Verify rollback of the previously selected next
			 * hop. */
			assert(result == EIGRP_RESULT_INTERNAL_FAILURE);
			assert(output_has("ip -4 route show 198.51.100.0/24",
					  "veth0"));
			puts("IPv4 weighted ECMP unsupported: rollback PASS");
			input.nexthop_count = 1;
			input.nexthops = hops;
			assert(eigrp_rib_route_add(test_instance, &input)
			       == EIGRP_RESULT_SUCCESS);
		}
	}
	assert(eigrp_linux_rib_reconcile() == EIGRP_RESULT_SUCCESS);
	command("ip -4 route del 198.51.100.0/24 proto eigrp metric 90");
	assert(eigrp_linux_rib_reconcile() == EIGRP_RESULT_SUCCESS);
	assert(output_has("ip -4 route show 198.51.100.0/24", "proto eigrp"));
	assert(eigrp_rib_route_del(test_instance, &key)
	       == EIGRP_RESULT_SUCCESS);
	assert(!output_has("ip -4 route show 198.51.100.0/24", "eigrp"));
	assert(eigrp_rib_route_del(test_instance, &key)
	       == EIGRP_RESULT_NOT_FOUND);
	puts("IPv4 install, idempotence, next-hop replacement, reconcile, withdraw: PASS");

	command("ip -4 route add 198.51.100.0/24 dev veth0 proto 99 metric 90");
	input.nexthop_count = 1;
	assert(eigrp_rib_route_add(test_instance, &input)
	       == EIGRP_RESULT_CONFLICT);
	assert(eigrp_rib_route_del(test_instance, &key)
	       == EIGRP_RESULT_SUCCESS);
	assert(output_has("ip -4 route show 198.51.100.0/24", "proto openr"));
	command("ip -4 route del 198.51.100.0/24 proto 99 metric 90");
	input.nexthops = hops;
	assert(eigrp_rib_route_add(test_instance, &input)
	       == EIGRP_RESULT_SUCCESS);
	command("ip -4 route replace 198.51.100.0/24 dev veth1 proto 99 metric 90");
	assert(eigrp_rib_route_del(test_instance, &key)
	       == EIGRP_RESULT_CONFLICT);
	assert(output_has("ip -4 route show 198.51.100.0/24", "proto openr"));
	command("ip -4 route del 198.51.100.0/24 proto 99 metric 90");
	puts("Foreign route conflict and external replacement ownership: PASS");

	input.nexthops = hops;
	hops[0].ifindex = 2000000;
	assert(eigrp_rib_route_add(test_instance, &input)
	       == EIGRP_RESULT_INTERNAL_FAILURE);
	hops[0].ifindex = veth0;
	assert(eigrp_rib_route_add(test_instance, &input)
	       == EIGRP_RESULT_SUCCESS);
	close(rib.fd);
	rib.fd = -1;
	assert(eigrp_linux_rib_reconcile() == EIGRP_RESULT_SUCCESS);
	assert(eigrp_rib_route_del(test_instance, &key)
	       == EIGRP_RESULT_SUCCESS);
	puts("Kernel rejection, retry and Netlink reconnect: PASS");

	key = prefix("2001:db8:ab::", EIGRP_AFI_IPV6, 64);
	test_afi = EIGRP_AFI_IPV6;
	input = route(key, hops, 1, 170);
	assert(eigrp_rib_route_add(test_instance, &input)
	       == EIGRP_RESULT_SUCCESS);
	assert(output_has("ip -6 route show 2001:db8:ab::/64", "proto eigrp"));
	assert(eigrp_rib_route_del(test_instance, &key)
	       == EIGRP_RESULT_SUCCESS);
	assert(!output_has("ip -6 route show 2001:db8:ab::/64", "eigrp"));
	test_vrf = 99;
	assert(eigrp_rib_route_add(test_instance, &input)
	       == EIGRP_RESULT_UNSUPPORTED);
	test_vrf = 0;
	puts("IPv6 route lifecycle and non-default VRF boundary: PASS");

	/* Simulate a hard daemon crash; no finish/withdraw in the child. */
	test_afi = EIGRP_AFI_IPV4;
	key = prefix("203.0.113.0", EIGRP_AFI_IPV4, 24);
	input = route(key, hops, 1, 90);
	child = fork();
	assert(child >= 0);
	if (child == 0) {
		/* Drop inherited RIB state in the child; its newly installed
		 * route is then owned only by that process. */
		key = prefix("203.0.113.0", EIGRP_AFI_IPV4, 24);
		input.prefix = key;
		assert(eigrp_rib_route_add(test_instance, &input)
		       == EIGRP_RESULT_SUCCESS);
		_exit(0);
	}
	assert(waitpid(child, &status, 0) == child);
	assert(WIFEXITED(status) && WEXITSTATUS(status) == 0);
	assert(output_has("ip -4 route show 203.0.113.0/24", "proto eigrp"));
	/* A restarted daemon cannot prove that a matching protocol-192
	 * route is its own. Do not silently adopt or remove it. */
	/* A stale route with a changed Linux priority must also block
	 * adoption, rather than leaving two conflicting EIGRP entries. */
	input.install.admin_dist = 170;
	assert(eigrp_rib_route_add(test_instance, &input)
	       == EIGRP_RESULT_CONFLICT);
	assert(!output_has("ip -4 route show 203.0.113.0/24", "metric 170"));
	input.install.admin_dist = 90;
	assert(eigrp_rib_route_add(test_instance, &input)
	       == EIGRP_RESULT_CONFLICT);
	assert(eigrp_rib_route_del(test_instance, &key)
	       == EIGRP_RESULT_SUCCESS);
	assert(output_has("ip -4 route show 203.0.113.0/24", "proto eigrp"));
	/* Operator verifies and removes the stale route; selected desired state
	 * can then reconcile it through a fresh exclusive installation. */
	assert(eigrp_rib_route_add(test_instance, &input)
	       == EIGRP_RESULT_CONFLICT);
	command("ip -4 route del 203.0.113.0/24 proto eigrp metric 90");
	assert(eigrp_linux_rib_reconcile() == EIGRP_RESULT_SUCCESS);
	assert(output_has("ip -4 route show 203.0.113.0/24", "proto eigrp"));
	assert(eigrp_rib_route_del(test_instance, &key)
	       == EIGRP_RESULT_SUCCESS);
	assert(!output_has("ip -4 route show 203.0.113.0/24", "eigrp"));
	input.prefix = prefix("203.0.114.0", EIGRP_AFI_IPV4, 24);
	assert(eigrp_rib_route_add(test_instance, &input)
	       == EIGRP_RESULT_SUCCESS);
	eigrp_rib_instance_delete(test_instance);
	assert(!output_has("ip -4 route show 203.0.114.0/24", "eigrp"));
	assert(eigrp_rib_route_add(test_instance, &input)
	       == EIGRP_RESULT_SUCCESS);
	eigrp_rib_finish();
	assert(!output_has("ip -4 route show 203.0.114.0/24", "eigrp"));
	puts("Crash restart fail-closed recovery, instance cleanup and shutdown: PASS");
}

int main(int argc, char **argv)
{
	fixture();
	if (argc > 1 && !strcmp(argv[1], "--kernel"))
		kernel();
	return 0;
}

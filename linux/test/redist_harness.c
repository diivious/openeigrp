// SPDX-License-Identifier: GPL-2.0-or-later
/*
 * Exercise the actual Linux RTNETLINK redistribution observer.
 * Copyright (C) 2026 Donnie V. Savage
 */
#include <assert.h>
#include <arpa/inet.h>
#include <net/if.h>
#include <stdio.h>

#include "../code/eigrp_linux_redist.c"

static eigrp_instance_t *const instance4 = (void *)(uintptr_t)0x1000;
static eigrp_instance_t *const instance6 = (void *)(uintptr_t)0x2000;
static uint32_t test_ifindex = 2;
static unsigned int added, deleted;
static bool fail_next_add;
static eigrp_prefix_t last_prefix;
static eigrp_redist_protocol_t last_protocol;
static uint32_t last_bandwidth;
static uint32_t last_ifindex;
static bool last_gateway;

eigrp_afi_t eigrp_instance_afi(const eigrp_instance_t *instance)
{
	return instance == instance4 ? EIGRP_AFI_IPV4 : EIGRP_AFI_IPV6;
}

eigrp_vrf_id_t eigrp_instance_vrf_id(const eigrp_instance_t *instance)
{
	assert(instance == instance4 || instance == instance6);
	return EIGRP_VRF_DEFAULT;
}

void eigrp_metric_values_convert(const eigrp_metric_values_t *values,
				 eigrp_metrics_t *metric)
{
	memset(metric, 0, sizeof(*metric));
	metric->bandwidth = values->bandwidth;
	metric->delay = values->delay;
	last_bandwidth = values->bandwidth;
}

eigrp_result_t eigrp_sys_interface_walk(eigrp_instance_t *instance,
					eigrp_sys_interface_walk_cb callback,
					void *arg)
{
	eigrp_intf_runtime_state_t state = {
		.ifindex = test_ifindex,
		.bandwidth = 100000,
		.mtu = 1500,
		.operative = true,
	};
	(void)instance;
	callback(&state, arg);
	return EIGRP_RESULT_SUCCESS;
}

static eigrp_result_t record(eigrp_instance_t *instance,
			     const eigrp_rib_route_t *route, bool add)
{
	assert(instance == instance4 || instance == instance6);
	assert(route->redist.source.route_instance == 0);
	assert(route->prefix.address.afi == eigrp_instance_afi(instance));
	last_protocol = route->redist.source.protocol;
	last_prefix = route->prefix;
	last_ifindex = route->nexthop_count ? route->nexthops[0].ifindex : 0;
	last_gateway =
		route->nexthop_count && route->nexthops[0].gateway_present;
	if (add && fail_next_add) {
		fail_next_add = false;
		return EIGRP_RESULT_INTERNAL_FAILURE;
	}
	if (add)
		added++;
	else
		deleted++;
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_rib_redist_add(eigrp_instance_t *instance,
				    const eigrp_rib_route_t *route)
{
	return record(instance, route, true);
}

eigrp_result_t eigrp_rib_redist_del(eigrp_instance_t *instance,
				    const eigrp_rib_route_t *route)
{
	return record(instance, route, false);
}

static bool make_route(struct eigrp_linux_redist_route *r, const char *addr,
		       unsigned int plen, unsigned int priority, uint8_t proto,
		       unsigned int ifindex)
{
	memset(r, 0, sizeof(*r));
	r->route.prefix.address.afi = EIGRP_AFI_IPV4;
	r->route.prefix.prefix_length = plen;
	assert(inet_pton(AF_INET, addr, r->route.prefix.address.bytes) == 1);
	eigrp_linux_redist_prefix_mask(&r->route.prefix);
	r->route.redist.source.protocol = eigrp_linux_redist_protocol(proto);
	r->kernel_protocol = proto;
	r->priority = priority;
	r->route.metric = priority;
	r->route.nexthops = r->hops;
	r->route.nexthop_count = 1;
	r->hops[0].ifindex = ifindex;
	r->hops[0].weight = 1;
	return true;
}

static void crafted(void)
{
	struct eigrp_linux_redist_routes *set, *next;
	struct eigrp_linux_redist_route r;
	struct eigrp_linux_redist_delivery *events;
	struct eigrp_linux_redist_sub sub = {
		.instance = instance4,
		.source = {.protocol = EIGRP_REDIST_PROTOCOL_STATIC},
		.afi = EIGRP_AFI_IPV4,
	};
	struct {
		struct nlmsghdr header;
		struct rtmsg route;
		unsigned char attrs[128];
	} msg = {0};
	bool valid;

	assert(eigrp_linux_redist_protocol(RTPROT_EIGRP)
	       == EIGRP_REDIST_PROTOCOL_UNSPECIFIED);
	assert(eigrp_linux_redist_protocol(RTPROT_BOOT)
	       == EIGRP_REDIST_PROTOCOL_STATIC);
	assert(eigrp_linux_redist_protocol(RTPROT_KERNEL)
	       == EIGRP_REDIST_PROTOCOL_CONNECTED);
	set = calloc(1, sizeof(*set));
	assert(set);
	make_route(&r, "198.51.100.33", 24, 10, RTPROT_STATIC, 2);
	assert(eigrp_linux_redist_route_apply(set, &r, true, true));
	events = eigrp_linux_redist_diff(NULL, set, &sub, false, &valid);
	assert(valid && events && !events->next);
	assert(eigrp_linux_redist_dispatch(events) == EIGRP_RESULT_SUCCESS);
	assert(added == 1 && last_protocol == EIGRP_REDIST_PROTOCOL_STATIC);
	assert(last_prefix.address.bytes[3] == 0);
	eigrp_linux_redist_deliveries_free(events);

	next = eigrp_linux_redist_routes_copy(set);
	assert(next);
	assert(eigrp_linux_redist_route_apply(next, &r, true, true));
	events = eigrp_linux_redist_diff(set, next, &sub, false, &valid);
	assert(valid && !events); /* Repeated route is idempotent. */
	eigrp_linux_redist_routes_free(next);

	next = eigrp_linux_redist_routes_copy(set);
	make_route(&r, "198.51.100.0", 24, 10, RTPROT_STATIC, 3);
	assert(eigrp_linux_redist_route_apply(next, &r, true, true));
	events = eigrp_linux_redist_diff(set, next, &sub, false, &valid);
	assert(valid && events && events->type == EIGRP_RIB_EVENT_UPDATE);
	assert(eigrp_linux_redist_dispatch(events) == EIGRP_RESULT_SUCCESS);
	assert(added == 2 && last_ifindex == 3);
	eigrp_linux_redist_deliveries_free(events);
	eigrp_linux_redist_routes_free(set);
	set = next;

	next = eigrp_linux_redist_routes_copy(set);
	make_route(&r, "198.51.100.0", 24, 20, RTPROT_BOOT, 3);
	assert(eigrp_linux_redist_route_apply(next, &r, true, true));
	/* Competing metrics on the same prefix: fail closed, no new
	 * host route-selection algorithm or arbitrary winner. */
	events = eigrp_linux_redist_diff(set, next, &sub, false, &valid);
	assert(valid && events && events->type == EIGRP_RIB_EVENT_DELETE);
	assert(eigrp_linux_redist_dispatch(events) == EIGRP_RESULT_SUCCESS);
	assert(deleted == 1);
	eigrp_linux_redist_deliveries_free(events);
	eigrp_linux_redist_routes_free(next);

	next = eigrp_linux_redist_routes_copy(set);
	make_route(&r, "198.51.100.0", 24, 10, RTPROT_STATIC, 2);
	assert(eigrp_linux_redist_route_apply(next, &r, false, true));
	events = eigrp_linux_redist_diff(set, next, &sub, false, &valid);
	assert(valid && events && events->type == EIGRP_RIB_EVENT_DELETE);
	assert(eigrp_linux_redist_dispatch(events) == EIGRP_RESULT_SUCCESS);
	assert(deleted == 2);
	eigrp_linux_redist_deliveries_free(events);
	eigrp_linux_redist_routes_free(next);
	eigrp_linux_redist_routes_free(set);

	/* Real parser, malformed route lengths and prefix bounds. */
	msg.header.nlmsg_type = RTM_NEWROUTE;
	msg.header.nlmsg_len = NLMSG_LENGTH(sizeof(msg.route));
	msg.route.rtm_family = AF_INET;
	msg.route.rtm_dst_len = 24;
	msg.route.rtm_type = RTN_UNICAST;
	msg.route.rtm_protocol = RTPROT_STATIC;
	msg.route.rtm_table = RT_TABLE_MAIN;
	{
		struct eigrp_linux_redist_route decoded;
		bool relevant, priority_present;
		assert(!eigrp_linux_redist_decode(
			&(struct nlmsghdr){.nlmsg_len = 4,
					   .nlmsg_type = RTM_NEWROUTE},
			&decoded, &relevant, &priority_present));
		msg.route.rtm_dst_len = 33;
		assert(!eigrp_linux_redist_decode(
			&msg.header, &decoded, &relevant, &priority_present));
		msg.route.rtm_dst_len = 24;
		assert(!eigrp_linux_redist_decode(
			&msg.header, &decoded, &relevant, &priority_present));
		/* Missing destination is invalid for nondefault prefixes. */
		msg.route.rtm_dst_len = 0;
		assert(eigrp_linux_redist_decode(&msg.header, &decoded,
						 &relevant, &priority_present));
		assert(!relevant); /* No interface index. */
		msg.route.rtm_protocol = RTPROT_EIGRP;
		assert(eigrp_linux_redist_decode(&msg.header, &decoded,
						 &relevant, &priority_present));
		assert(!relevant);
	}
	puts("Crafted source, replacement, ambiguity, deletion, malformed, loop guard: PASS");
}

static void command(const char *cmd)
{
	int rc = system(cmd);
	if (rc) {
		fprintf(stderr, "command failed (%d): %s\n", rc, cmd);
		abort();
	}
}

static void kernel(void)
{
	eigrp_redist_source_t connected = {
		.protocol = EIGRP_REDIST_PROTOCOL_CONNECTED};
	eigrp_redist_source_t static_source = {
		.protocol = EIGRP_REDIST_PROTOCOL_STATIC};
	eigrp_redist_source_t unsupported = {
		.protocol = EIGRP_REDIST_PROTOCOL_OSPF};
	eigrp_redist_source_t instance_source = {
		.protocol = EIGRP_REDIST_PROTOCOL_STATIC,
		.route_instance = 1,
	};
	unsigned int count;

	command("ip link set lo up");
	command("ip link add veth0 type veth peer name veth1");
	command("ip link set veth0 up");
	command("ip link set veth1 up");
	test_ifindex = if_nametoindex("veth0");
	assert(test_ifindex);
	command("ip -4 addr add 10.11.0.1/24 dev veth0");
	assert(eigrp_rib_redistribute_add(instance4, &unsupported)
	       == EIGRP_RESULT_UNSUPPORTED);
	assert(eigrp_rib_redistribute_add(instance4, &instance_source)
	       == EIGRP_RESULT_UNSUPPORTED);
	assert(eigrp_rib_redistribute_add(instance4, &connected)
	       == EIGRP_RESULT_SUCCESS);
	assert(added >= 1 && last_protocol == EIGRP_REDIST_PROTOCOL_CONNECTED);
	assert(last_bandwidth == 100000);
	assert(eigrp_rib_redistribute_add(instance4, &connected)
	       == EIGRP_RESULT_SUCCESS);
	assert(eigrp_rib_redistribute_add(instance4, &static_source)
	       == EIGRP_RESULT_SUCCESS);
	/* The same observer normalizes both address families independently. */
	command("ip -6 addr add 2001:db8:1::1/64 dev veth0");
	assert(eigrp_linux_redist_process() == EIGRP_RESULT_SUCCESS);
	count = added;
	assert(eigrp_rib_redistribute_add(instance6, &connected)
	       == EIGRP_RESULT_SUCCESS);
	assert(added > count && last_prefix.address.afi == EIGRP_AFI_IPV6);
	assert(eigrp_rib_redistribute_add(instance6, &static_source)
	       == EIGRP_RESULT_SUCCESS);
	count = added;
	command("ip -6 route add 2001:db8:100::/64 dev veth0 proto static metric 55");
	assert(eigrp_linux_redist_process() == EIGRP_RESULT_SUCCESS);
	assert(added == count + 1 && last_prefix.address.afi == EIGRP_AFI_IPV6);
	{
		unsigned int withdrawals = deleted;
		command("ip -6 route del 2001:db8:100::/64 proto static metric 55");
		assert(eigrp_linux_redist_process() == EIGRP_RESULT_SUCCESS);
		assert(deleted == withdrawals + 1);
	}
	count = added;
	command("ip -4 route add 198.51.100.0/24 dev veth0 proto static metric 10");
	assert(eigrp_linux_redist_process() == EIGRP_RESULT_SUCCESS);
	assert(added == count + 1
	       && last_protocol == EIGRP_REDIST_PROTOCOL_STATIC);
	count = added;
	assert(eigrp_linux_redist_process() == EIGRP_RESULT_SUCCESS);
	assert(added == count);
	command("ip -4 route replace 198.51.100.0/24 dev veth1 proto static metric 10");
	assert(eigrp_linux_redist_process() == EIGRP_RESULT_SUCCESS);
	assert(added == count + 1 && last_ifindex == if_nametoindex("veth1"));
	count = added;
	/* RTM_NEWROUTE with a changed kernel protocol may have no DEL. */
	command("ip -4 route replace 198.51.100.0/24 dev veth0 proto boot metric 10");
	assert(eigrp_linux_redist_process() == EIGRP_RESULT_SUCCESS);
	assert(added == count + 1 && last_ifindex == test_ifindex);
	count = added;
	{
		unsigned int withdrawals = deleted;
		command("ip -4 route replace 198.51.100.0/24 dev veth1 proto 192 metric 10");
		assert(eigrp_linux_redist_process() == EIGRP_RESULT_SUCCESS);
		assert(deleted == withdrawals + 1 && added == count);
		{
			struct eigrp_linux_redist_route removed;
			make_route(&removed, "198.51.100.0", 24, 10,
				   RTPROT_STATIC, test_ifindex);
			assert(!eigrp_linux_redist_route_unique(
				observer.routes, &removed.route.prefix,
				EIGRP_REDIST_PROTOCOL_STATIC));
		}
	}
	command("ip -4 route replace 198.51.100.0/24 dev veth1 proto static metric 10");
	assert(eigrp_linux_redist_process() == EIGRP_RESULT_SUCCESS);
	assert(added == count + 1);
	count = added;
	command("ip -4 route del 198.51.100.0/24 proto static metric 10");
	assert(eigrp_linux_redist_process() == EIGRP_RESULT_SUCCESS);
	assert(deleted >= 1);
	command("ip -4 route add 203.0.113.0/24 dev veth0 proto 192 metric 90");
	assert(eigrp_linux_redist_process() == EIGRP_RESULT_SUCCESS);
	assert(added == count); /* Owned EIGRP protocol is never reflected. */
	command("ip -4 route del 203.0.113.0/24 proto 192 metric 90");
	assert(eigrp_linux_redist_process() == EIGRP_RESULT_SUCCESS);
	assert(added == count);

	command("ip -4 route add 192.0.2.0/24 dev veth0 proto boot metric 12");
	assert(eigrp_linux_redist_process() == EIGRP_RESULT_SUCCESS);
	assert(added == count + 1);
	assert(eigrp_rib_redistribute_remove(instance4, &static_source)
	       == EIGRP_RESULT_SUCCESS);
	count = added;
	command("ip -4 route replace 192.0.2.0/24 dev veth1 proto boot metric 12");
	assert(eigrp_linux_redist_process() == EIGRP_RESULT_SUCCESS);
	assert(added == count);
	assert(eigrp_rib_redistribute_add(instance4, &static_source)
	       == EIGRP_RESULT_SUCCESS);
	assert(added == count + 1);
	count = added;
	close(observer.fd);
	observer.fd = -1;
	command("ip -4 route add 198.18.0.0/24 dev veth0 proto static metric 30");
	assert(eigrp_linux_redist_process() == EIGRP_RESULT_SUCCESS);
	assert(added == count + 1);
	command("ip -4 route del 198.18.0.0/24 proto static metric 30");
	assert(eigrp_linux_redist_resync() == EIGRP_RESULT_SUCCESS);
	assert(deleted >= 2);
	/* A failed core ingress must mark the cache dirty and replay the
	 * actual kernel snapshot on the next process attempt. */
	fail_next_add = true;
	command("ip -4 route add 198.18.1.0/24 dev veth0 proto static metric 31");
	assert(eigrp_linux_redist_process() == EIGRP_RESULT_INTERNAL_FAILURE);
	assert(observer.dirty);
	count = added;
	assert(eigrp_linux_redist_process() == EIGRP_RESULT_SUCCESS);
	assert(!observer.dirty && added > count);
	command("ip -4 route del 198.18.1.0/24 proto static metric 31");
	assert(eigrp_linux_redist_process() == EIGRP_RESULT_SUCCESS);
	eigrp_linux_redist_instance_delete(instance4);
	count = added;
	command("ip -4 route add 198.18.0.0/24 dev veth0 proto static metric 20");
	assert(eigrp_linux_redist_process() == EIGRP_RESULT_SUCCESS);
	assert(added == count);
	eigrp_linux_redist_instance_delete(instance6);
	eigrp_linux_redist_stop();
	assert(eigrp_linux_redist_fd() == -1);
	assert(!observer.routes && !observer.subscribers);
	puts("Netlink kernel snapshot, add/replace/delete, subscriptions, loss and recovery: PASS");
}

int main(int argc, char **argv)
{
	if (argc > 1 && !strcmp(argv[1], "--kernel"))
		kernel();
	else
		crafted();
	return 0;
}

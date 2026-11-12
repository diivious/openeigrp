// SPDX-License-Identifier: ISC
/* Copyright (C) 2026 Donnie V. Savage */
#include <assert.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

#include "eigrp_cli.h"
#include "eigrp_unix_config.h"
#include "eigrp_unix_keychain.h"
#include "eigrp_linux_policy.h"
#include "eigrp_linux_config.h"

static unsigned calls;
static unsigned parents;
static unsigned afs;
static unsigned interfaces;
static unsigned networks;
static unsigned topologies;
static unsigned redists;
static unsigned metrics;
static unsigned flags;
static uint32_t router_id;
static bool af_shutdown;
static bool fail_shutdown;
static int fake_ipv4;
static int fake_ipv6;
static int fake_runtime;
static int fake_interface;

static eigrp_af_config_t *af_pointer(eigrp_afi_t afi)
{
	return (eigrp_af_config_t *)(afi == EIGRP_AFI_IPV4 ? &fake_ipv4
							   : &fake_ipv6);
}

static void record(void)
{
	calls++;
}

eigrp_result_t eigrp_named_config_create(const char *name)
{
	assert(strcmp(name, "eigrprocks") == 0);
	record();
	parents++;
	return EIGRP_RESULT_SUCCESS;
}

eigrp_named_config_t *eigrp_named_config_read(const char *name)
{
	assert(strcmp(name, "eigrprocks") == 0);
	return (eigrp_named_config_t *)&fake_runtime;
}

eigrp_result_t eigrp_named_config_shutdown_update(eigrp_operation_t op,
						  eigrp_named_config_t *parent)
{
	assert(parent == (eigrp_named_config_t *)&fake_runtime);
	assert(op == EIGRP_SET || op == EIGRP_RESET);
	record();
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_af_config_create(const char *name, eigrp_afi_t afi,
				      const char *vrf, uint16_t asn)
{
	assert(strcmp(name, "eigrprocks") == 0);
	assert(afi == EIGRP_AFI_IPV4 || afi == EIGRP_AFI_IPV6);
	assert(strcmp(vrf, "default") == 0);
	assert(asn == (afi == EIGRP_AFI_IPV4 ? 4453 : 6473));
	record();
	afs++;
	return EIGRP_RESULT_SUCCESS;
}

eigrp_af_config_t *eigrp_af_config_read(const char *name, eigrp_afi_t afi,
					const char *vrf, uint16_t asn)
{
	assert(strcmp(name, "eigrprocks") == 0);
	assert(strcmp(vrf, "default") == 0);
	assert(asn == (afi == EIGRP_AFI_IPV4 ? 4453 : 6473));
	return af_pointer(afi);
}

eigrp_result_t eigrp_af_config_context_read(const char *name, eigrp_afi_t afi,
					    const char *vrf, uint16_t asn,
					    eigrp_instance_context_t *context)
{
	context->config = eigrp_af_config_read(name, afi, vrf, asn);
	context->runtime = (eigrp_instance_t *)&fake_runtime;
	context->topology_id = EIGRP_TOPOLOGY_ID_BASE;
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_af_config_shutdown_update(eigrp_operation_t op,
					       eigrp_af_config_t *af)
{
	assert(af == af_pointer(EIGRP_AFI_IPV4)
	       || af == af_pointer(EIGRP_AFI_IPV6));
	record();
	af_shutdown = op == EIGRP_SET;
	return fail_shutdown ? EIGRP_RESULT_NOT_IMPLEMENTED
			     : EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_instance_router_id_update(eigrp_operation_t op,
					       eigrp_instance_context_t *ctx,
					       uint32_t value)
{
	assert(op == EIGRP_SET);
	assert(ctx->runtime == (eigrp_instance_t *)&fake_runtime);
	record();
	router_id = value;
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_network_create(eigrp_instance_context_t *ctx,
				    const eigrp_prefix_t *prefix)
{
	assert(ctx->config == af_pointer(EIGRP_AFI_IPV4));
	assert(prefix->address.afi == EIGRP_AFI_IPV4);
	assert(prefix->prefix_length == 24);
	assert(prefix->address.bytes[0] == 10);
	assert(prefix->address.bytes[1] == 44);
	assert(prefix->address.bytes[2] == 53);
	assert(prefix->address.bytes[3] == 0);
	record();
	networks++;
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_intf_config_create(eigrp_af_config_t *af,
					const char *ifname)
{
	assert(af == af_pointer(EIGRP_AFI_IPV4)
	       || af == af_pointer(EIGRP_AFI_IPV6));
	assert(strcmp(ifname, "eth0") == 0);
	record();
	interfaces++;
	return EIGRP_RESULT_SUCCESS;
}

eigrp_intf_config_t *eigrp_intf_config_read(eigrp_af_config_t *af,
					    const char *ifname)
{
	assert(af != NULL);
	assert(strcmp(ifname, "eth0") == 0);
	return (eigrp_intf_config_t *)&fake_interface;
}

eigrp_intf_t *eigrp_intf_lookup_by_name(eigrp_instance_t *runtime,
					const char *ifname)
{
	assert(runtime == (eigrp_instance_t *)&fake_runtime);
	assert(strcmp(ifname, "eth0") == 0);
	return NULL;
}

#define INTF_NUMBER_STUB(name, type)                                           \
	eigrp_result_t name(eigrp_operation_t op, eigrp_intf_context_t *ctx,   \
			    type value)                                        \
	{                                                                      \
		assert(op == EIGRP_SET);                                       \
		assert(ctx->config == (eigrp_intf_config_t *)&fake_interface); \
		assert(value > 0);                                             \
		record();                                                      \
		return EIGRP_RESULT_SUCCESS;                                   \
	}
INTF_NUMBER_STUB(eigrp_intf_bandwidth_percent_update, uint32_t)
INTF_NUMBER_STUB(eigrp_intf_bandwidth_update, uint32_t)
INTF_NUMBER_STUB(eigrp_intf_delay_update, uint32_t)
INTF_NUMBER_STUB(eigrp_intf_hello_interval_update, uint16_t)
INTF_NUMBER_STUB(eigrp_intf_hold_time_update, uint16_t)

#define INTF_FLAG_STUB(name)                                                   \
	eigrp_result_t name(eigrp_operation_t op, eigrp_intf_context_t *ctx)   \
	{                                                                      \
		assert(op == EIGRP_SET || op == EIGRP_RESET);                  \
		assert(ctx->address_family != NULL);                           \
		record();                                                      \
		flags++;                                                       \
		return EIGRP_RESULT_SUCCESS;                                   \
	}
INTF_FLAG_STUB(eigrp_intf_passive_update)
INTF_FLAG_STUB(eigrp_intf_split_horizon_update)
INTF_FLAG_STUB(eigrp_intf_nexthop_self_update)
INTF_FLAG_STUB(eigrp_intf_shutdown_update)

eigrp_result_t eigrp_nbr_static_create(eigrp_af_config_t *af,
				       const eigrp_address_t *address,
				       const char *ifname)
{
	assert(af == af_pointer(EIGRP_AFI_IPV4));
	assert(address->afi == EIGRP_AFI_IPV4);
	assert(strcmp(ifname, "eth0") == 0);
	record();
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_topology_create(eigrp_instance_context_t *ctx)
{
	assert(ctx->topology_id == EIGRP_TOPOLOGY_ID_BASE);
	record();
	topologies++;
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_af_config_distance_update(eigrp_operation_t op,
					       eigrp_af_config_t *af,
					       uint8_t internal,
					       uint8_t external)
{
	assert(op == EIGRP_SET && af != NULL);
	assert(internal == 90 && external == 170);
	record();
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_metric_default_update(eigrp_operation_t op,
					   eigrp_instance_context_t *ctx,
					   const eigrp_metric_values_t *metric)
{
	assert(op == EIGRP_SET && ctx->config != NULL);
	assert(metric->bandwidth == 100000);
	assert(metric->delay == 10);
	assert(metric->reliability == 255);
	assert(metric->load == 1);
	assert(metric->mtu == 1500);
	record();
	metrics++;
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t
eigrp_metric_weights_update(eigrp_operation_t op, eigrp_instance_context_t *ctx,
			    const eigrp_metric_weights_t *weights)
{
	assert(op == EIGRP_SET && ctx->config != NULL);
	assert(weights->k1 == 1 && weights->k3 == 1);
	record();
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_metric_variance_update(eigrp_operation_t op,
					    eigrp_instance_context_t *ctx,
					    uint8_t variance)
{
	assert(op == EIGRP_SET && ctx->config != NULL && variance == 2);
	record();
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_metric_maximum_hops_update(eigrp_operation_t op,
						eigrp_instance_context_t *ctx,
						uint8_t hops)
{
	assert(op == EIGRP_SET && ctx->config != NULL && hops == 100);
	record();
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_topology_maximum_paths_update(
	eigrp_operation_t op, eigrp_instance_context_t *ctx, uint8_t paths)
{
	assert(op == EIGRP_SET && ctx->config != NULL && paths == 4);
	record();
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_redist_add(eigrp_instance_context_t *ctx,
				const eigrp_redist_source_t *source,
				const eigrp_metric_values_t *metric,
				const char *route_map)
{
	assert(ctx->config != NULL);
	assert(source->route_instance == 0);
	if (source->protocol == EIGRP_REDIST_PROTOCOL_STATIC) {
		assert(route_map && strcmp(route_map, "static-map") == 0);
		assert(metric == NULL);
	} else {
		assert(source->protocol == EIGRP_REDIST_PROTOCOL_CONNECTED);
		assert(metric == NULL && route_map == NULL);
	}
	record();
	redists++;
	return EIGRP_RESULT_SUCCESS;
}

/* Linux production policy requests are real library calls in this harness. */
void eigrp_sys_policy_runtime_update(void)
{
}
void eigrp_redist_policy_update_all(void)
{
}
void eigrp_summary_policy_update_all(void)
{
}

eigrp_result_t eigrp_distribute_add(eigrp_instance_context_t *ctx,
				    eigrp_distribute_list_type_t type,
				    const char *name,
				    eigrp_offset_direction_t direction,
				    const char *ifname)
{
	assert(ctx->config != NULL);
	if (direction == EIGRP_OFFSET_IN) {
		assert(type == EIGRP_DIST_PREFIX_LIST);
		assert(strcmp(name, "lan-in") == 0);
		assert(ifname == NULL);
	} else {
		assert(direction == EIGRP_OFFSET_OUT);
		assert(type == EIGRP_DIST_ACCESS_LIST);
		assert(strcmp(name, "lan-out") == 0);
		assert(ifname && strcmp(ifname, "eth0") == 0);
	}
	return EIGRP_RESULT_SUCCESS;
}

static bool keychain_attached;
eigrp_result_t eigrp_auth_mode_update(eigrp_operation_t op,
				      eigrp_intf_context_t *ctx,
				      eigrp_authentication_mode_t mode,
				      const eigrp_auth_hmac_config_t *hmac)
{
	assert(op == EIGRP_SET && ctx->config != NULL);
	assert(mode == EIGRP_AUTHENTICATION_MD5 && hmac == NULL);
	assert(keychain_attached);
	return EIGRP_RESULT_SUCCESS;
}

eigrp_result_t eigrp_auth_keychain_update(eigrp_operation_t op,
					  eigrp_intf_context_t *ctx,
					  const char *chain)
{
	assert(op == EIGRP_SET && ctx->config != NULL);
	assert(strcmp(chain, "key-lan") == 0);
	keychain_attached = true;
	return EIGRP_RESULT_SUCCESS;
}

static const char config_text[] =
	"router eigrp eigrprocks\n"
	" address-family ipv4 unicast autonomous-system 4453\n"
	"  eigrp router-id 10.44.53.1\n"
	"  network 10.44.53.19 0.0.0.255\n"
	"  distance eigrp 90 170\n"
	"  metric weights 0 1 0 1 0 0\n"
	"  neighbor 10.44.53.2 eth0\n"
	"  af-interface eth0\n"
	"   hello-interval 5\n"
	"   hold-time 15\n"
	"   bandwidth 100000\n"
	"   delay 10\n"
	"   passive-interface\n"
	"   no split-horizon\n"
	"   no shutdown\n"
	"  exit-af-interface\n"
	"  topology base\n"
	"   default-metric 100000 10 255 1 1500\n"
	"   variance 2\n"
	"   metric maximum-hops 100\n"
	"   maximum-paths 4\n"
	"   redistribute connected\n"
	"  exit-af-topology\n"
	"  shutdown\n"
	"  no shutdown\n"
	" exit-address-family\n"
	" address-family ipv6 unicast vrf default autonomous-system 6473\n"
	"  eigrp router-id 10.44.53.1\n"
	"  af-interface eth0\n"
	"   passive-interface\n"
	"  exit-af-interface\n"
	" exit-address-family\n"
	"exit\n";

int main(void)
{
	eigrp_unix_config_t *config = eigrp_unix_config_create();
	unsigned line = 99;
	unsigned before;

	assert(config != NULL);
	assert(eigrp_unix_config_parse_text(config, config_text, &line)
	       == EIGRP_RESULT_SUCCESS);
	assert(line == 0 && calls == 0);
	/* Staging a bad replacement must retain the previous staged plan. */
	assert(eigrp_unix_config_parse_text(
		       config,
		       "router eigrp eigrprocks\n address-family ipv4 "
		       "autonomous-system 4453\n  route-map UNSUPPORTED\n",
		       &line)
	       == EIGRP_RESULT_UNSUPPORTED);
	assert(line == 3 && calls == 0);
	assert(eigrp_unix_config_commit(config, &line) == EIGRP_RESULT_SUCCESS);
	assert(line == 0);
	assert(parents == 1 && afs == 2 && interfaces == 2);
	assert(networks == 1 && topologies == 1 && redists == 1);
	assert(metrics == 1 && flags == 4);
	assert(router_id == 0x0a2c3501U && !af_shutdown);
	before = calls;
	assert(eigrp_unix_config_commit(config, &line)
	       == EIGRP_RESULT_INVALID_ARGUMENT);
	assert(calls == before);
	eigrp_unix_config_delete(config);

	config = eigrp_unix_config_create();
	assert(config);
	/* Full-file syntactic prevalidation must prevent partial application.
	 */
	before = calls;
	assert(eigrp_unix_config_apply_text(
		       config,
		       "router eigrp eigrprocks\n address-family ipv4 "
		       "autonomous-system 4453\n  eigrp router-id bad\n",
		       &line)
	       == EIGRP_RESULT_INVALID_ARGUMENT);
	assert(line == 3 && calls == before);
	assert(eigrp_unix_config_apply_text(
		       config,
		       "router eigrp eigrprocks\n address-family ipv4 "
		       "autonomous-system 4453\n  shutdown\n"
		       " exit-address-family\nexit\n",
		       &line)
	       == EIGRP_RESULT_SUCCESS);
	eigrp_unix_config_delete(config);

	config = eigrp_unix_config_create();
	assert(config);
	fail_shutdown = true;
	assert(eigrp_unix_config_apply_text(
		       config,
		       "router eigrp eigrprocks\n address-family ipv4 "
		       "autonomous-system 4453\n  shutdown\n"
		       " exit-address-family\nexit\n",
		       &line)
	       == EIGRP_RESULT_NOT_IMPLEMENTED);
	assert(line == 3);
	before = calls;
	assert(eigrp_unix_config_commit(config, &line)
	       == EIGRP_RESULT_INVALID_ARGUMENT);
	assert(calls == before);
	fail_shutdown = false;
	eigrp_unix_config_delete(config);
	/* Missing native policy registration must fail closed. */
	config = eigrp_unix_config_create();
	assert(config);
	assert(eigrp_unix_config_apply_text(
		       config,
		       "ip prefix-list closed seq 10 permit 10.0.0.0/8\n",
		       &line)
	       == EIGRP_RESULT_NOT_IMPLEMENTED);
	assert(line == 1);
	assert(!eigrp_linux_policy_rule_exists(EIGRP_DIST_PREFIX_LIST,
					       "closed"));
	eigrp_unix_config_delete(config);
	eigrp_linux_config_policy_register();

	/* Complete native config: policy objects, redistribution references,
	 * and secret ownership dispatch through production providers. */
	config = eigrp_unix_config_create();
	assert(config);
	assert(eigrp_unix_config_apply_text(
		       config,
		       "router eigrp eigrprocks\n"
		       " address-family ipv4 autonomous-system 4453\n"
		       "  distribute-list prefix lan-in in\n"
		       "  distribute-list access lan-out out eth0\n"
		       "  af-interface eth0\n"
		       "   authentication mode md5\n"
		       "   authentication key-chain key-lan\n"
		       "  exit-af-interface\n"
		       "  topology base\n"
		       "   redistribute static route-map static-map\n"
		       "  exit-af-topology\n"
		       " exit-address-family\nexit\n"
		       "route-map static-map permit 10 match prefix-list lan-in\n"
		       "ip prefix-list lan-in seq 10 permit 10.0.0.0/8 ge 16 le 24\n"
		       "ip access-list lan-out seq 10 deny 198.51.100.0/24\n"
		       "key-chain key-lan key 7 secret testing#secret!strong\n",
		       &line)
	       == EIGRP_RESULT_SUCCESS);
	{
		uint32_t id = 0;
		char secret[82];
		eigrp_prefix_t p = {0};
		eigrp_filter_decision_t d = EIGRP_FILTER_DECISION_DENY;
		p.address.afi = EIGRP_AFI_IPV4;
		p.address.bytes[0] = 10;
		p.prefix_length = 16;
		assert(eigrp_sys_filter_evaluate(NULL, EIGRP_DIST_PREFIX_LIST,
						 "lan-in", &p, &d)
		       == EIGRP_RESULT_SUCCESS);
		assert(d == EIGRP_FILTER_DECISION_PERMIT);
		assert(eigrp_sys_auth_key_lookup("key-lan", &id, secret,
						 sizeof(secret)));
		assert(id == 7 && strcmp(secret, "testing#secret!strong") == 0);
		memset(secret, 0, sizeof(secret));
	}
	eigrp_unix_config_delete(config);
	eigrp_unix_keychain_reset();
	eigrp_linux_policy_reset();
	puts("unix config tests: PASS");
	return 0;
}

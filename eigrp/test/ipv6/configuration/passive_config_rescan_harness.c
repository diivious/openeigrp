/* Compiled against the actual portable EIGRP implementation. */
#include <arpa/inet.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "eigrp_network.c"

static eigrp_af_config_t af;
static eigrp_intf_config_t cfg;
static eigrp_intf_runtime_state_t host;
static int activations;
static int notifications;
eigrp_process_t eigrp_process;

eigrp_af_config_t *eigrp_af_config_runtime_read(eigrp_instance_t *runtime)
{
	(void)runtime;
	return &af;
}
eigrp_intf_config_t *eigrp_intf_config_read(eigrp_af_config_t *ctx,
					    const char *name)
{
	return ctx == &af && strcmp(name, "lo") == 0 ? &cfg : NULL;
}
bool eigrp_prefix_address_match(const eigrp_prefix_t *prefix,
				const eigrp_address_t *address)
{
	(void)prefix;
	(void)address;
	return false;
}
bool eigrp_prefix_valid(const eigrp_prefix_t *prefix)
{
	return prefix && prefix->prefix_length <= 128
	       && (prefix->address.afi == EIGRP_AFI_IPV4
		   || prefix->address.afi == EIGRP_AFI_IPV6);
}
eigrp_result_t
eigrp_intf_runtime_update(eigrp_operation_t op, eigrp_instance_t *instance,
			  const eigrp_intf_runtime_state_t *state,
			  eigrp_intf_t *runtime)
{
	(void)instance;
	(void)runtime;
	if (op == EIGRP_SET && state == &host)
		++activations;
	return EIGRP_RESULT_SUCCESS;
}
bool eigrp_instance_intf_update_event_enqueue(
	eigrp_instance_t *instance, const eigrp_intf_runtime_state_t *state)
{
	(void)instance;
	if (state == &host)
		++notifications;
	return true;
}
void eigrp_router_id_update(eigrp_instance_t *instance)
{
	instance->router_id.s_addr = htonl(0x0aff0001u);
}
eigrp_result_t eigrp_sys_interface_walk(eigrp_instance_t *instance,
					eigrp_sys_interface_walk_cb callback,
					void *arg)
{
	(void)instance;
	callback(&host, arg);
	return EIGRP_RESULT_SUCCESS;
}
/* Compile the unmodified source handler after the real network implementation.
 */
#define eigrp_intf_message_dispatch(...) EIGRP_RESULT_INTERNAL_FAILURE
#define eigrp_intf_context_valid(ctx)                                          \
	((ctx) && ((ctx)->config || (ctx)->runtime))
#define eigrp_intf_eventlog_state(...) ((void)0)
#define eigrp_intf_context_runtime_rebind(...) ((void)0)

bool eigrp_intf_is_passive(eigrp_intf_t *ei)
{
	return ei && ei->params.passive_interface == EIGRP_INTF_PASSIVE;
}
void eigrp_intf_multicast_update(eigrp_operation_t operation, eigrp_intf_t *ei)
{
	(void)operation;
	(void)ei;
}
eigrp_afi_t eigrp_instance_afi(const eigrp_instance_t *instance)
{
	return instance->af_vectors.afi;
}

#include "passive_config_function.inc"

int main(void)
{
	eigrp_instance_t instance = {0};
	eigrp_intf_context_t context = {0};
	instance.router_id.s_addr = htonl(0x0aff0001u);
	instance.af_vectors.afi = EIGRP_AFI_IPV6;
	cfg.interface_name = "lo";
	af.runtime = &instance;
	host.interface_name = "lo";
	host.ifindex = 1;
	host.secondary = true;
	host.operative = true;
	host.address.address.afi = EIGRP_AFI_IPV6;
	host.address.prefix_length = 128;
	if (inet_pton(AF_INET6, "2001:db8:4473:ff::1",
		      host.address.address.bytes)
	    != 1)
		return 10;
	context.address_family = &af;
	context.config = &cfg;
	activations = 0;
	/* Host interface already exists; no new host event will arrive. */
	if (eigrp_intf_passive_update(EIGRP_SET, &context)
	    != EIGRP_RESULT_SUCCESS)
		return 11;
	if (activations != 1 || !cfg.passive_configured || !cfg.passive)
		return 12;
	if (eigrp_intf_passive_update(EIGRP_SET, &context)
	    != EIGRP_RESULT_SUCCESS)
		return 13;
	if (activations != 2)
		return 14;
	if (eigrp_intf_passive_update(EIGRP_RESET, &context)
	    != EIGRP_RESULT_SUCCESS)
		return 15;
	if (cfg.passive || activations != 2)
		return 16;
	instance.af_vectors.afi = EIGRP_AFI_IPV4;
	if (eigrp_intf_passive_update(EIGRP_SET, &context)
		    != EIGRP_RESULT_SUCCESS
	    || activations != 2)
		return 17;
	instance.af_vectors.afi = EIGRP_AFI_IPV6;
	cfg.interface_name = NULL;
	if (eigrp_intf_passive_update(EIGRP_SET, &context)
		    != EIGRP_RESULT_SUCCESS
	    || activations != 2)
		return 18;
	puts("PASS IPv6 passive configuration re-evaluates existing host interface through portable network code");
	return 0;
}

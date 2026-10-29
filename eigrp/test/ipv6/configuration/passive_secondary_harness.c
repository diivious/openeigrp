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
static int check(eigrp_instance_t *eigrp, int want)
{
	eigrp_virt_router_t vr = {0};
	eigrp_list_t process_list = {0}, instance_list = {0};
	eigrp_list_item_t process_item = {0}, instance_item = {0};
	vr.af_instance = &instance_list;
	instance_item.data = eigrp;
	instance_list.head = &instance_item;
	process_item.data = &vr;
	process_list.head = &process_item;
	eigrp_process.virt_router = &process_list;
	notifications = 0;
	eigrp_sys_intf_update(eigrp->vrf_id, &host);
	if (notifications != (eigrp->af_vectors.afi == EIGRP_AFI_IPV6 ? 1 : 0))
		return 3;
	eigrp_process.virt_router = NULL;
	activations = 0;
	eigrp_network_intfs_update(eigrp);
	if (activations != want)
		return 1;
	activations = 0;
	eigrp_network_intf_update(eigrp, &host);
	if (activations != want)
		return 2;
	return 0;
}
int main(void)
{
	eigrp_instance_t instance = {0};
	instance.router_id.s_addr = htonl(0x0aff0001u);
	instance.af_vectors.afi = EIGRP_AFI_IPV6;
	cfg.interface_name = "lo";
	cfg.passive_configured = true;
	cfg.passive = true;
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
	if (check(&instance, 1))
		return 11;
	/* Repeated evaluations must retain the same eligibility decision. */
	if (check(&instance, 1))
		return 12;
	cfg.passive = false;
	if (check(&instance, 0))
		return 13;
	cfg.passive = true;
	cfg.passive_configured = false;
	if (check(&instance, 0))
		return 14;
	cfg.passive_configured = true;
	if (inet_pton(AF_INET6, "fe80::1", host.address.address.bytes) != 1)
		return 15;
	if (check(&instance, 0))
		return 16;
	/* Normal primary link-local interface activation must remain working.
	 */
	host.secondary = false;
	if (check(&instance, 1))
		return 21;
	host.secondary = true;
	if (inet_pton(AF_INET6, "::1", host.address.address.bytes) != 1)
		return 17;
	if (check(&instance, 0))
		return 18;
	instance.af_vectors.afi = EIGRP_AFI_IPV4;
	host.address.address.afi = EIGRP_AFI_IPV4;
	host.address.prefix_length = 32;
	if (inet_pton(AF_INET, "10.255.0.1", host.address.address.bytes) != 1)
		return 19;
	if (check(&instance, 0))
		return 20;
	puts("PASS secondary IPv6 passive interface activation and rejection boundaries");
	return 0;
}

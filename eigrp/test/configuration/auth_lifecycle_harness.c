// SPDX-License-Identifier: ISC
/* Executable portable authentication configuration replay unit regression. */
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "eigrp.h"
#include "eigrp_auth.h"
#include "eigrp_eventlog.h"
#include "eigrp_interface.h"
#include "eigrp_instance.h"
#include "eigrp_structs.h"

static eigrp_af_config_t af;

eigrp_af_config_t *eigrp_af_config_runtime_read(eigrp_instance_t *runtime)
{
	return runtime == af.runtime ? &af : NULL;
}

eigrp_result_t eigrp_eventlog_msg_add(eigrp_instance_t *runtime,
				      uint16_t opcode,
				      const eigrp_prefix_t *prefix,
				      eventmsg_arg_t a, eventmsg_arg_t b,
				      eventmsg_arg_t c, eventmsg_arg_t d)
{
	(void)runtime;
	(void)opcode;
	(void)prefix;
	(void)a;
	(void)b;
	(void)c;
	(void)d;
	return EIGRP_RESULT_SUCCESS;
}

static void check(bool condition, const char *name)
{
	if (!condition) {
		fprintf(stderr, "auth lifecycle: %s\n", name);
		exit(1);
	}
}

static void check_runtime(const eigrp_intf_t *ei, int mode,
			  const char *password, const char *chain, bool hop,
			  bool split)
{
	check(ei->params.auth_type == mode, "authentication mode");
	check((!password && !ei->params.auth_password)
		      || (password && ei->params.auth_password
			  && strcmp(password, ei->params.auth_password) == 0),
	      "runtime password");
	check((!chain && !ei->params.auth_keychain)
		      || (chain && ei->params.auth_keychain
			  && strcmp(chain, ei->params.auth_keychain) == 0),
	      "runtime keychain");
	check(ei->next_hop_self == hop, "next-hop-self");
	check(ei->split_horizon == split, "split-horizon");
}

int main(void)
{
	eigrp_instance_t instance = {0};
	eigrp_intf_t ei = {0};
	eigrp_intf_config_t def = {
		.interface_name = "default",
		.authentication_mode_configured = true,
		.authentication_mode = EIGRP_AUTHENTICATION_MD5,
		.keychain = "default-chain",
		.next_hop_self = false,
		.next_hop_self_configured = true,
		.split_horizon = false,
		.split_horizon_configured = true,
	};
	eigrp_intf_config_t specific = {
		.interface_name = "eth0",
		.next = &def,
	};
	int i;

	af.runtime = &instance;
	af.interfaces = &specific;
	instance.eiflist = eigrp_list_create();
	check(instance.eiflist != NULL, "interface list allocation");
	ei.eigrp = &instance;
	ei.name = "eth0";
	ei.next_hop_self = true;
	ei.split_horizon = true;
	ei.params.bandwidth = EIGRP_BANDWIDTH_DEFAULT;
	ei.params.delay = EIGRP_DELAY_DEFAULT;
	check(eigrp_list_add(instance.eiflist, &ei) != NULL,
	      "interface registration");

	/* An empty interface object must inherit MD5, keychain, and booleans.
	 */
	eigrp_intf_config_update(&ei, &specific);
	check_runtime(&ei, EIGRP_AUTH_TYPE_MD5, NULL, "default-chain", false,
		      false);
	check(ei.params.auth_keychain != def.keychain,
	      "keychain retains independent ownership");
	specific.keychain = "specific-chain";
	eigrp_intf_config_update(&ei, &specific);
	check_runtime(&ei, EIGRP_AUTH_TYPE_MD5, NULL, "specific-chain", false,
		      false);
	specific.keychain = NULL;
	eigrp_intf_config_update(&ei, &specific);
	check_runtime(&ei, EIGRP_AUTH_TYPE_MD5, NULL, "default-chain", false,
		      false);

	specific.next_hop_self = true;
	specific.next_hop_self_configured = true;
	specific.split_horizon = true;
	specific.split_horizon_configured = true;
	specific.authentication_mode_configured = true;
	specific.authentication_mode = EIGRP_AUTHENTICATION_HMAC_SHA256;
	specific.authentication_password = "runtime-sha256";
	specific.authentication_encryption_type = 0;
	for (i = 0; i < 100; i++) {
		eigrp_intf_config_update(&ei, &specific);
		check_runtime(&ei, EIGRP_AUTH_TYPE_SHA256, "runtime-sha256",
			      "default-chain", true, true);
	}
	check(specific.authentication_password != ei.params.auth_password,
	      "password retains independent ownership");

	/* Rebind changes from the default while preserving specific overrides.
	 */
	def.keychain = "replacement-chain";
	eigrp_intf_context_t ctx = {
		.address_family = &af,
		.config = &def,
	};
	eigrp_intf_config_rebind(&ctx);
	check_runtime(&ei, EIGRP_AUTH_TYPE_SHA256, "runtime-sha256",
		      "replacement-chain", true, true);

	/* Removing an override restores the inherited mode and discards secret.
	 */
	specific.authentication_mode_configured = false;
	eigrp_intf_config_update(&ei, &specific);
	check_runtime(&ei, EIGRP_AUTH_TYPE_MD5, NULL, "replacement-chain", true,
		      true);

	/* Encrypted type 7 must not use an inherited keychain as a fallback. */
	specific.authentication_mode_configured = true;
	specific.authentication_mode = EIGRP_AUTHENTICATION_HMAC_SHA256;
	specific.authentication_encryption_type = 7;
	specific.authentication_password = "070C285F4D06";
	eigrp_intf_config_update(&ei, &specific);
	check_runtime(&ei, EIGRP_AUTH_TYPE_SHA256, NULL, NULL, true, true);
	check(specific.authentication_password
		      && strcmp(specific.authentication_password,
				"070C285F4D06")
				 == 0,
	      "retained type-7 text unchanged");

	/* Deleting default and specific objects cannot leave stale auth behind.
	 */
	af.interfaces = &specific;
	specific.next = NULL;
	specific.authentication_mode_configured = false;
	eigrp_intf_config_update(&ei, &specific);
	check_runtime(&ei, EIGRP_AUTH_TYPE_NONE, NULL, NULL, true, true);
	af.interfaces = NULL;
	eigrp_intf_config_update(&ei, NULL);
	check_runtime(&ei, EIGRP_AUTH_TYPE_NONE, NULL, NULL, true, true);
	check(ei.bandwidth_percent == 50, "pacing reset");
	check(ei.params.v_hello == EIGRP_HELLO_INTERVAL_DEFAULT,
	      "hello interval reset");

	eigrp_list_delete_data(instance.eiflist, &ei);
	eigrp_list_delete(&instance.eiflist);
	puts("PASS: portable auth configuration replay");
	return 0;
}

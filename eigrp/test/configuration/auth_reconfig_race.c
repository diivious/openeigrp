// SPDX-License-Identifier: ISC
/* Copyright (C) 2026 Donnie V. Savage */
/* Deterministic ingress-vs-AF authentication writer regression. */
#include <arpa/inet.h>
#include <pthread.h>
#include <sched.h>
#include <stdbool.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

#include "eigrp.h"
#include "eigrp_auth.h"
#include "eigrp_interface.h"
#include "eigrp_stream.h"
#include "eigrp_structs.h"

static eigrp_instance_t instance;
static eigrp_intf_t ei;

eigrp_af_config_t *eigrp_af_config_runtime_read(eigrp_instance_t *eigrp)
{
	(void)eigrp;
	return NULL;
}

eigrp_result_t eigrp_eventlog_msg_add(eigrp_instance_t *eigrp, uint16_t opcode,
				      const eigrp_prefix_t *prefix,
				      eventmsg_arg_t a, eventmsg_arg_t b,
				      eventmsg_arg_t c, eventmsg_arg_t d)
{
	(void)eigrp;
	(void)opcode;
	(void)prefix;
	(void)a;
	(void)b;
	(void)c;
	(void)d;
	return EIGRP_RESULT_SUCCESS;
}

static void check(bool ok, const char *what)
{
	if (!ok) {
		fprintf(stderr, "auth reconfiguration: %s\n", what);
		exit(1);
	}
}

bool eigrp_sys_auth_key_lookup(const char *name, uint32_t *id, char *key,
			       size_t capacity)
{
	if (strcmp(name, "testchain") != 0 || capacity < 9)
		return false;
	memcpy(key, "chainkey", 9);
	if (id)
		*id = 8;
	return true;
}

/* Calls the real encoding/key-resolution path while simulating an ingress
 * caller holding peer_lock. No direct pointer reads escape the lock. */
static void read_auth(void)
{
	eigrp_stream_t *s = eigrp_stream_create(128);
	int mode = ei.params.auth_type;
	uint16_t length = 0;

	check(s != NULL, "stream allocation");
	if (mode == EIGRP_AUTH_TYPE_MD5) {
		check(ei.params.auth_password == NULL,
		      "MD5 has no direct secret");
		length = eigrp_auth_tlv_md5_encode(s, &ei);
		check(length == EIGRP_AUTH_MD5_TLV_SIZE, "MD5 key resolution");
	} else if (mode == EIGRP_AUTH_TYPE_SHA256 && ei.params.auth_password) {
		check(ei.params.auth_password[0] == 'S', "SHA key integrity");
		length = eigrp_auth_tlv_sha256_encode(s, &ei);
		check(length == EIGRP_AUTH_SHA256_TLV_SIZE,
		      "HMAC-SHA256 key resolution");
	} else {
		check(mode == EIGRP_AUTH_TYPE_NONE
			      || (mode == EIGRP_AUTH_TYPE_SHA256
				  && !eigrp_auth_material_available(&ei)),
		      "fail-closed unsupported auth");
	}
	if (length)
		check(s->endp == length, "authentication TLV framing");
	eigrp_stream_free(s);
}

typedef enum action {
	APPLY,
	MODE_SET,
	MODE_RESET,
	KEYCHAIN_SET,
	KEYCHAIN_RESET
} action_t;

typedef struct race {
	pthread_mutex_t lock;
	pthread_cond_t changed;
	bool started;
	bool completed;
	action_t action;
	eigrp_intf_context_t ctx;
	eigrp_intf_config_t config;
} race_t;

static eigrp_result_t act(race_t *race)
{
	switch (race->action) {
	case APPLY:
		return eigrp_auth_runtime_apply(&ei, &race->config,
						"testchain");
	case MODE_SET:
		return eigrp_auth_mode_update(EIGRP_SET, &race->ctx,
					      EIGRP_AUTHENTICATION_MD5, NULL);
	case MODE_RESET:
		return eigrp_auth_mode_update(EIGRP_RESET, &race->ctx, 0, NULL);
	case KEYCHAIN_SET:
		return eigrp_auth_keychain_update(EIGRP_SET, &race->ctx,
						  "testchain");
	case KEYCHAIN_RESET:
		return eigrp_auth_keychain_update(EIGRP_RESET, &race->ctx,
						  NULL);
	}
	return EIGRP_RESULT_INVALID_ARGUMENT;
}

static void *run_writer(void *arg)
{
	race_t *race = arg;
	eigrp_result_t result;

	pthread_mutex_lock(&race->lock);
	race->started = true;
	pthread_cond_signal(&race->changed);
	pthread_mutex_unlock(&race->lock);
	result = act(race);
	check(result == EIGRP_RESULT_SUCCESS, "writer operation");
	pthread_mutex_lock(&race->lock);
	race->completed = true;
	pthread_cond_signal(&race->changed);
	pthread_mutex_unlock(&race->lock);
	return NULL;
}

/* The AF writer must not complete while ingress holds peer_lock.
 * This checks each public writer, including the replay path. */
static void check_blocked(action_t action)
{
	race_t race = {0};
	pthread_t thread;
	struct timespec delay = {.tv_sec = 0, .tv_nsec = 50000000};

	race.action = action;
	race.ctx.runtime = &ei;
	race.config.authentication_mode_configured = true;
	race.config.authentication_mode = EIGRP_AUTHENTICATION_HMAC_SHA256;
	race.config.authentication_password = "SHA-key-replaced";
	race.config.authentication_encryption_type = 0;
	check(!pthread_mutex_init(&race.lock, NULL), "race mutex init");
	check(!pthread_cond_init(&race.changed, NULL), "race cond init");
	pthread_mutex_lock(&instance.peer_lock);
	check(!pthread_create(&thread, NULL, run_writer, &race),
	      "writer thread start");
	pthread_mutex_lock(&race.lock);
	while (!race.started)
		pthread_cond_wait(&race.changed, &race.lock);
	pthread_mutex_unlock(&race.lock);
	nanosleep(&delay, NULL);
	pthread_mutex_lock(&race.lock);
	check(!race.completed, "writer bypassed peer_lock");
	pthread_mutex_unlock(&race.lock);
	/* Read real key material while the AF attempts replacement. */
	if (ei.params.auth_type != EIGRP_AUTH_TYPE_NONE
	    && ei.params.auth_keychain)
		read_auth();
	pthread_mutex_unlock(&instance.peer_lock);
	check(!pthread_join(thread, NULL), "writer completion");
	pthread_mutex_lock(&race.lock);
	check(race.completed, "writer must complete after ingress");
	pthread_mutex_unlock(&race.lock);
	pthread_cond_destroy(&race.changed);
	pthread_mutex_destroy(&race.lock);
}

typedef struct stress {
	bool finished;
} stress_t;

static void *churn_writer(void *arg)
{
	stress_t *state = arg;
	eigrp_intf_config_t config = {0};
	int i;

	for (i = 0; i < 2000; i++) {
		config.authentication_mode_configured = true;
		config.authentication_mode =
			i % 3 == 0 ? EIGRP_AUTHENTICATION_MD5
				   : EIGRP_AUTHENTICATION_HMAC_SHA256;
		config.authentication_password = "SHA-key-replaced";
		config.authentication_encryption_type = i % 3 == 2 ? 7 : 0;
		check(eigrp_auth_runtime_apply(&ei, &config, "testchain")
			      == (i % 3 == 2 ? EIGRP_RESULT_UNSUPPORTED
					     : EIGRP_RESULT_SUCCESS),
		      "atomic runtime apply result");
	}
	pthread_mutex_lock(&instance.peer_lock);
	state->finished = true;
	pthread_mutex_unlock(&instance.peer_lock);
	return NULL;
}

int main(void)
{
	pthread_t writer;
	stress_t state = {0};
	eigrp_intf_config_t config = {
		.authentication_mode_configured = true,
		.authentication_mode = EIGRP_AUTHENTICATION_MD5,
	};
	int reads = 0;

	check(!pthread_mutex_init(&instance.peer_lock, NULL),
	      "peer mutex init");
	ei.eigrp = &instance;
	check(eigrp_auth_runtime_apply(&ei, &config, "testchain")
		      == EIGRP_RESULT_SUCCESS,
	      "initial authentication setup");
	check_blocked(APPLY);
	check_blocked(MODE_SET);
	check_blocked(KEYCHAIN_SET);
	check_blocked(KEYCHAIN_RESET);
	check_blocked(MODE_RESET);
	check(eigrp_auth_runtime_apply(&ei, &config, "testchain")
		      == EIGRP_RESULT_SUCCESS,
	      "restore MD5 before concurrent test");
	check(!pthread_create(&writer, NULL, churn_writer, &state),
	      "churn writer start");
	for (;;) {
		bool done;
		pthread_mutex_lock(&instance.peer_lock);
		done = state.finished;
		read_auth();
		pthread_mutex_unlock(&instance.peer_lock);
		reads++;
		if (done)
			break;
		sched_yield();
	}
	check(!pthread_join(writer, NULL), "churn writer finish");
	check(reads > 0, "reader checked live authentication");
	/* No caller may retain the retired interface's runtime credentials. */
	eigrp_del_intf_params(&ei.params);
	check(!pthread_mutex_destroy(&instance.peer_lock), "peer mutex finish");
	puts("PASS: authentication writer serialization, key resolution and churn");
	return 0;
}

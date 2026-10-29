#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Execute the FRR adapter's actual Zebra transmit callbacks with AF workers.

The fake scheduler enqueues work from two AF threads and executes it on the
FRR owner thread. A mock zclient rejects any write on an AF thread.
"""

from pathlib import Path
import re
import subprocess
import textwrap

ROOT = next(p for p in Path(__file__).resolve().parents if (p / "eigrp" / "code").is_dir())
SOURCE = ROOT / "frr" / "code" / "eigrp_frr_rib.c"


def function(source: str, name: str) -> str:
    regex = rf"(?m)^static (?:void|eigrp_result_t) {re.escape(name)}\("
    match = re.search(regex, source)
    assert match, f"production function missing: {name}"
    start, depth = source.index("{", match.start()), 0
    for i in range(start, len(source)):
        if source[i] == "{":
            depth += 1
        elif source[i] == "}":
            depth -= 1
            if depth == 0:
                return source[match.start(): i + 1]
    raise AssertionError("incomplete C function")


def test_af_worker_zebra_writes_serialized_and_snapshotted(tmp_path):
    source = SOURCE.read_text()
    implementations = "\n\n".join(function(source, name) for name in (
        "eigrp_zebra_route_tx_run", "eigrp_zebra_route_tx_add",
        "eigrp_zebra_redist_tx_run", "eigrp_zebra_redist_tx_add",
    ))
    c = tmp_path / "zebra_tx.c"
    binary = tmp_path / "zebra_tx"
    c.write_text(textwrap.dedent(r'''
        #include <assert.h>
        #include <pthread.h>
        #include <stdbool.h>
        #include <stdint.h>
        #include <stdlib.h>
        #include <string.h>
        typedef int afi_t;
        typedef int vrf_id_t;
        typedef enum { EIGRP_RESULT_SUCCESS = 0, EIGRP_RESULT_INTERNAL_FAILURE = 6 } eigrp_result_t;
        enum { ZEBRA_ROUTE_ADD = 20, ZEBRA_ROUTE_DELETE = 21,
               ZEBRA_REDISTRIBUTE_ADD = 60, ZEBRA_REDISTRIBUTE_DELETE = 61 };
        enum { AFI_IP = 1, AFI_IP6 = 2, ZEBRA_ROUTE_STATIC = 3 };
        struct zapi_route { unsigned prefix_id; unsigned afi; unsigned metric; };
        struct zclient { int alive; };
        struct event { void (*callback)(struct event *); void *arg; struct event *next; };
        struct event_loop { int unused; };
        static struct zclient client = { 1 };
        static struct zclient *eigrp_zclient = &client;
        static struct event_loop loop, *master = &loop;
        static pthread_t main_thread;
        static pthread_mutex_t queue_lock = PTHREAD_MUTEX_INITIALIZER;
        static struct event *head, *tail;
        static unsigned routes[2][128], deletes[2][128];
        static unsigned subscriptions[2][2];
        static unsigned writes, pushed;
        #define MTYPE_EIGRP_ZEBRA_INSTANCE 1
        #define XCALLOC(t,n) calloc(1,n)
        #define XFREE(t,p) free(p)
        #define EVENT_ARG(e) ((e)->arg)
        static void event_add_event(struct event_loop *m, void (*fn)(struct event *),
                                    void *arg, int val, struct event **owner) {
            struct event *e = calloc(1, sizeof(*e));
            (void)m; (void)val; (void)owner;
            assert(e);
            e->callback = fn; e->arg = arg;
            pthread_mutex_lock(&queue_lock);
            if (tail) tail->next = e;
            else head = e;
            tail = e; pushed++;
            pthread_mutex_unlock(&queue_lock);
        }
        static int zclient_route_send(uint8_t op, struct zclient *zc, struct zapi_route *r) {
            assert(pthread_equal(pthread_self(), main_thread));
            assert(zc == &client && zc->alive);
            assert(r->afi == AFI_IP || r->afi == AFI_IP6);
            assert(r->prefix_id >= 1 && r->prefix_id <= 128);
            unsigned which = r->afi == AFI_IP ? 0 : 1;
            assert(subscriptions[which][0] == 1 && subscriptions[which][1] == 0);
            assert(r->metric == 10 * r->prefix_id);
            if (op == ZEBRA_ROUTE_ADD) routes[which][r->prefix_id - 1]++;
            else { assert(op == ZEBRA_ROUTE_DELETE); deletes[which][r->prefix_id - 1]++; }
            writes++;
            return 0;
        }
        static void zclient_redistribute(int op, struct zclient *zc, afi_t afi, int type,
                                         unsigned short instance, vrf_id_t vrf) {
            assert(pthread_equal(pthread_self(), main_thread));
            assert(zc == &client && type == ZEBRA_ROUTE_STATIC);
            assert(instance == 0 && vrf == 0);
            assert(afi == AFI_IP || afi == AFI_IP6);
            assert(op == ZEBRA_REDISTRIBUTE_ADD || op == ZEBRA_REDISTRIBUTE_DELETE);
            if (op == ZEBRA_REDISTRIBUTE_DELETE) {
                for (int i = 0; i < 128; i++)
                    assert(routes[afi == AFI_IP ? 0 : 1][i] == 1);
            }
            subscriptions[afi == AFI_IP ? 0 : 1][op == ZEBRA_REDISTRIBUTE_ADD ? 0 : 1]++;
            writes++;
        }
    ''') + "\n" + "struct eigrp_zebra_route_tx { uint8_t command; struct zapi_route route; };\n" + "struct eigrp_zebra_redist_tx { int command; afi_t afi; int type; unsigned short instance; vrf_id_t vrf_id; };\n" + implementations + "\n" + textwrap.dedent(r'''
        static void *worker(void *opaque) {
            unsigned afi = (unsigned)(uintptr_t)opaque;
            eigrp_zebra_redist_tx_add(ZEBRA_REDISTRIBUTE_ADD, afi, ZEBRA_ROUTE_STATIC, 0, 0);
            for (unsigned i = 1; i <= 128; i++) {
                struct zapi_route route = { i, afi, 10 * i };
                assert(eigrp_zebra_route_tx_add(ZEBRA_ROUTE_ADD, &route) == EIGRP_RESULT_SUCCESS);
                if ((i & 1) == 0)
                    assert(eigrp_zebra_route_tx_add(ZEBRA_ROUTE_DELETE, &route) == EIGRP_RESULT_SUCCESS);
                memset(&route, 0xFF, sizeof(route)); /* must be deep snapshotted */
            }
            eigrp_zebra_redist_tx_add(ZEBRA_REDISTRIBUTE_DELETE, afi, ZEBRA_ROUTE_STATIC, 0, 0);
            return NULL;
        }
        int main(void) {
            pthread_t a, b;
            main_thread = pthread_self();
            assert(!pthread_create(&a, NULL, worker, (void *)(uintptr_t)AFI_IP));
            assert(!pthread_create(&b, NULL, worker, (void *)(uintptr_t)AFI_IP6));
            pthread_join(a, NULL); pthread_join(b, NULL);
            assert(writes == 0); /* no direct zclient I/O from AF workers */
            assert(pushed == 388);
            while (head) {
                struct event *e = head;
                head = head->next;
                e->callback(e);
                free(e);
            }
            tail = NULL;
            assert(writes == pushed);
            for (int afi = 0; afi < 2; afi++) {
                assert(subscriptions[afi][0] == 1 && subscriptions[afi][1] == 1);
                for (int i = 0; i < 128; i++) {
                    assert(routes[afi][i] == 1);
                    assert(deletes[afi][i] == ((i & 1) ? 1U : 0U));
                }
            }
            /* A work item must not dereference a stopped zclient. */
            struct zapi_route route = { 1, AFI_IP6, 10 };
            assert(eigrp_zebra_route_tx_add(ZEBRA_ROUTE_ADD, &route) == EIGRP_RESULT_SUCCESS);
            eigrp_zclient = NULL;
            struct event *e = head;
            assert(e);
            e->callback(e);
            free(e);
            return 0;
        }
    '''))
    subprocess.run(["gcc", "-std=c11", "-Wall", "-Wextra", "-Werror", "-pthread", str(c), "-o", str(binary)], check=True)
    subprocess.run([str(binary)], check=True)

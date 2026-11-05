// SPDX-License-Identifier: ISC
// Copyright (C) 2026 Donnie V. Savage
/* Link-only host stubs for the isolated redistribution/DUAL FSM harness.
 * The fixture does not create packet queues or interface output objects. */
#include "eigrp.h"
#include "eigrp_structs.h"
#include "eigrp_packet.h"

/* The process-volume fixture already defines eigrp_hello_send().
 * The regular redistribution fixture needs a link-only replacement. */
#ifndef EIGRP_TEST_PROCESS_VOLUME
void eigrp_hello_send(eigrp_intf_t *ei, uint8_t type, eigrp_addr_t *nbr)
{
	(void)ei;
	(void)type;
	(void)nbr;
}
#endif

void eigrp_packet_queue_clear(eigrp_packet_queue_t *queue)
{
	(void)queue;
}

void eigrp_packet_interface_output_clear(eigrp_intf_t *ei)
{
	(void)ei;
}

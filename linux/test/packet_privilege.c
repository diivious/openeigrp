// SPDX-License-Identifier: GPL-2.0-or-later
/* Check the real kernel's raw-socket privilege boundary. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include "../code/eigrp_linux_packet.c"

struct eigrp_instance {
	eigrp_afi_t afi;
	eigrp_vrf_id_t vrf;
};
struct eigrp_interface {
	int index;
};
eigrp_afi_t eigrp_instance_afi(const eigrp_instance_t *e)
{
	return e->afi;
}
eigrp_vrf_id_t eigrp_instance_vrf_id(const eigrp_instance_t *e)
{
	return e->vrf;
}
eigrp_ifindex_t eigrp_intf_ifindex(const eigrp_intf_t *ei)
{
	return (unsigned)ei->index;
}
eigrp_result_t eigrp_intf_address_read(const eigrp_intf_t *ei,
				       eigrp_prefix_t *p)
{
	(void)ei;
	(void)p;
	return EIGRP_RESULT_NOT_FOUND;
}
eigrp_result_t eigrp_sys_interface_walk(eigrp_instance_t *e,
					eigrp_sys_interface_walk_cb cb,
					void *arg)
{
	(void)e;
	(void)cb;
	(void)arg;
	return EIGRP_RESULT_SUCCESS;
}
int main(void)
{
	struct eigrp_instance e = {.afi = EIGRP_AFI_IPV4, .vrf = 0};
	eigrp_result_t result = eigrp_sys_socket_open(&e);
	if (result == EIGRP_RESULT_SUCCESS) {
		assert(eigrp_linux_packet_fd(&e) >= 0);
		eigrp_sys_socket_close(&e);
		assert(eigrp_linux_packet_fd(&e) == -1);
		puts("real raw socket open/close: PASS");
	} else {
		assert(result == EIGRP_RESULT_INTERNAL_FAILURE);
		assert(errno == EPERM || errno == EACCES
		       || errno == EAFNOSUPPORT);
		assert(eigrp_linux_packet_fd(&e) == -1);
		puts("real raw socket CAP_NET_RAW denial and cleanup: PASS");
	}
	return 0;
}

// SPDX-License-Identifier: GPL-2.0-or-later
/* Check the real kernel's raw-socket privilege boundary. */
#include <assert.h>
#include <errno.h>
#include <stdio.h>
#include <string.h>
#include "../code/eigrp_linux_packet.c"
static int runtime_bindings;
void eigrp_unix_runtime_fd_set(eigrp_instance_t *instance, int fd)
{
	(void)instance;
	assert(fd >= 0);
	runtime_bindings++;
}
void eigrp_unix_runtime_fd_clear(eigrp_instance_t *instance)
{
	(void)instance;
	assert(runtime_bindings > 0);
	runtime_bindings--;
}


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
	struct eigrp_instance v6 = {.afi = EIGRP_AFI_IPV6, .vrf = 0};
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
	result = eigrp_sys_socket_open(&v6);
	if (result == EIGRP_RESULT_SUCCESS) {
		int option = 0;
		socklen_t size = sizeof(option);

		assert(eigrp_linux_packet_fd(&v6) >= 0);
		assert(getsockopt(eigrp_linux_packet_fd(&v6), IPPROTO_IPV6,
				  IPV6_CHECKSUM, &option, &size)
		       == 0);
		assert(size == sizeof(option) && option == -1);
		eigrp_sys_socket_close(&v6);
		assert(eigrp_linux_packet_fd(&v6) == -1);
		puts("real IPv6 raw socket and disabled kernel checksum: PASS");
	} else {
		assert(result == EIGRP_RESULT_INTERNAL_FAILURE);
		assert(errno == EPERM || errno == EACCES
		       || errno == EAFNOSUPPORT || errno == EPROTONOSUPPORT);
		assert(eigrp_linux_packet_fd(&v6) == -1);
		puts("real IPv6 raw socket CAP_NET_RAW denial and cleanup: PASS");
	}
	assert(runtime_bindings == 0);
	return 0;
}

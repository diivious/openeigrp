/*
 * EIGRP AddPath selection, portable across address families and host shims.
 * Copyright (C) 2026 Donnie V. Savage
 */
#ifndef EIGRP_ADDPATH_H
#define EIGRP_ADDPATH_H

#include "eigrp_types.h"

void eigrp_addpath_refresh(eigrp_instance_t *eigrp);
void eigrp_addpath_select(eigrp_instance_t *eigrp, eigrp_intf_t *ei,
			  eigrp_route_descriptor_t *route);

#endif /* EIGRP_ADDPATH_H */

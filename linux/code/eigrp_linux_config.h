// SPDX-License-Identifier: GPL-2.0-or-later
/* Native Linux policy configuration binding.
 * Copyright (C) 2026 Donnie V. Savage
 */
#ifndef EIGRP_LINUX_CONFIG_H_
#define EIGRP_LINUX_CONFIG_H_

/* Register the native policy provider before semantic configuration commit. */
void eigrp_linux_config_policy_register(void);

#endif /* EIGRP_LINUX_CONFIG_H_ */

# OpenEIGRP Core/Host API Inventory (Draft)

Copyright (C) 2026 Donnie V. Savage

**Status:** Inventory only, **not a frozen API or ABI contract**. The public
interface will be reviewed and stabilized at the end of the `linux-support`
implementation. Until then, functions, types, signatures, ownership, and
semantics may change as correctness and integration requirements evolve.

**Baseline:** `diivious/openeigrp`, branch `linux-support`, commit
`babbfd2e88d7dec9a440f1de985538ab838943f3` (reviewed 2026-11-07).
This document records the declarations
on that branch, not any historical ZIP or other branch.

## 1. Purpose and scope

Identify the existing cross-platform C interface, capture the signatures
of **all callable declarations and callback types in the five designated
public headers**, and state **who produces and who consumes each API**.
This is a snapshot for the native Linux daemon work, not an approval of every
existing design choice or a commitment to preserve the listed interfaces.

Designated headers:

```text
eigrp/code/eigrp.h
eigrp/code/eigrp_cli.h
eigrp/code/eigrp_mgnt.h
eigrp/code/eigrp_rib.h
eigrp/code/eigrp_sys.h
```

These five headers are the current *intended* public boundary. Other headers
in `eigrp/code/` may expose callable functions to internal modules or existing
integrations, but are **not automatically public APIs**. Direct platform
usage of those headers must be audited before stabilizing the contract.

The current declarations in the five headers are authoritative for the build.
This README does not redefine C types or override the implementation. The
existing [public API design document](../public-api.md) contains broader
behavioral and ownership guidance; its use of the word *contract* is
premature for the `linux-support` phase. This README is deliberately
provisional until both documents are reconciled and approved.

## 2. Produced versus consumed

**Direction is defined relative to the portable `eigrp/` core, not relative
to an individual header or application.**

| Category | Producer / implementation owner | Consumer / caller | Direction |
|---|---|---|---|
| **EIGRP-produced API** | Portable EIGRP core | Host integration (FRR, BIRD, Unix/Linux, SR Linux, etc.) | Host → Core |
| **EIGRP-consumed API** | Host integration | Portable EIGRP core | Core → Host |
| **Host-supplied callback** | Host supplies function pointer | Core invokes callback | Core → Host callback |
| **Core-supplied callback** | Core supplies function pointer | Host invokes callback | Host → Core callback |

The producer owns the implementation and intended semantics. The consumer
must use the documented arguments, lifetime and execution model. A callable
interface can be declared in `eigrp_sys.h` or `eigrp_rib.h` and still be
**EIGRP-produced**: classification is per symbol, not per header.

### Boundary schematic

```text
   host integration                          portable EIGRP core
   ----------------                          -------------------
   consumes  ----------------------------->  produces
                  eigrp.h / eigrp_cli.h / eigrp_mgnt.h
                  RIB ingress and system notifications

   produces  <-----------------------------  consumes
                  host RIB and system services

   callbacks: the supplier and invoker are recorded separately below
```

## 3. EIGRP-produced APIs (host consumes)

These functions are implemented by `eigrp/`, including the core-facing
notifications in the otherwise mixed-direction system header. Inline helpers
are included as source-level API declarations; they do not export linker symbols.


### `eigrp.h`

```c
static inline bool eigrp_prefix_limit_runtime_supported(const eigrp_prefix_limit_t *limit);
static inline bool eigrp_prefix_limit_allows(const eigrp_prefix_limit_t *limit, uint32_t current, bool already_present);
static inline bool eigrp_prefix_limit_threshold_crossed(const eigrp_prefix_limit_t *limit, uint32_t current);
void eigrp_init(void);
bool eigrp_process_receive_start(void);
void eigrp_terminate(void);
eigrp_instance_t *eigrp_instance_create(eigrp_virt_router_t *virt_router, eigrp_afi_t afi, uint16_t as, eigrp_vrf_id_t vrf_id);
void eigrp_instance_delete(eigrp_instance_t *instance);
eigrp_instance_t *eigrp_lookup(eigrp_vrf_id_t vrf_id);
eigrp_instance_t *eigrp_lookup_by_as_vrf(uint16_t as, eigrp_vrf_id_t vrf_id);
eigrp_instance_t *eigrp_lookup_by_af_as_vrf(eigrp_afi_t afi, uint16_t as, eigrp_vrf_id_t vrf_id);
eigrp_result_t eigrp_instance_iterate(eigrp_instance_iterate_cb callback, void *arg);
eigrp_vrf_id_t eigrp_instance_vrf_id(const eigrp_instance_t *eigrp);
eigrp_afi_t eigrp_instance_afi(const eigrp_instance_t *eigrp);
uint16_t eigrp_instance_asn(const eigrp_instance_t *eigrp);
const char *eigrp_instance_name(const eigrp_instance_t *eigrp);
eigrp_ifindex_t eigrp_intf_ifindex(const eigrp_intf_t *ei);
const char *eigrp_intf_name(const eigrp_intf_t *ei);
eigrp_result_t eigrp_intf_address_read(const eigrp_intf_t *ei, eigrp_prefix_t *address);
```

### `eigrp_cli.h`

```c
eigrp_result_t eigrp_instance_classic_validate(uint16_t asn, eigrp_vrf_id_t vrf_id, const char **owner_name);
eigrp_result_t eigrp_instance_classic_create(uint16_t asn, eigrp_vrf_id_t vrf_id, eigrp_instance_t **runtime);
eigrp_instance_t *eigrp_instance_classic_read(uint16_t asn, eigrp_vrf_id_t vrf_id);
eigrp_result_t eigrp_instance_classic_delete(eigrp_instance_t *runtime);
eigrp_result_t eigrp_named_config_create(const char *name);
eigrp_named_config_t *eigrp_named_config_read(const char *name);
eigrp_result_t eigrp_named_config_delete(const char *name);
eigrp_result_t eigrp_af_config_create(const char *name, eigrp_afi_t afi, const char *vrf_name, uint16_t asn);
eigrp_af_config_t *eigrp_af_config_read(const char *name, eigrp_afi_t afi, const char *vrf_name, uint16_t asn);
eigrp_result_t eigrp_af_config_context_read(const char *name, eigrp_afi_t afi, const char *vrf_name, uint16_t asn, eigrp_instance_context_t *context);
eigrp_result_t eigrp_af_config_delete(const char *name, eigrp_afi_t afi, const char *vrf_name, uint16_t asn);
eigrp_result_t eigrp_instance_router_id_update(eigrp_operation_t operation, eigrp_instance_context_t *context, uint32_t router_id);
eigrp_result_t eigrp_af_config_shutdown_update(eigrp_operation_t operation, eigrp_af_config_t *af);
eigrp_result_t eigrp_named_config_shutdown_update(eigrp_operation_t operation, eigrp_named_config_t *parent);
eigrp_result_t eigrp_af_config_distance_update(eigrp_operation_t operation, eigrp_af_config_t *af, uint8_t internal_distance, uint8_t external_distance);
eigrp_result_t eigrp_network_create(eigrp_instance_context_t *context, const eigrp_prefix_t *prefix);
eigrp_result_t eigrp_network_delete(eigrp_instance_context_t *context, const eigrp_prefix_t *prefix);
eigrp_result_t eigrp_network_runtime_exists(eigrp_instance_t *eigrp, const eigrp_prefix_t *prefix, bool *exists);
eigrp_result_t eigrp_intf_config_create(eigrp_af_config_t *af, const char *interface_name);
eigrp_intf_config_t *eigrp_intf_config_read(eigrp_af_config_t *af, const char *interface_name);
eigrp_result_t eigrp_intf_config_delete(eigrp_af_config_t *af, const char *interface_name);
eigrp_intf_t *eigrp_intf_runtime_lookup(eigrp_instance_t *runtime, const char *interface_name);
eigrp_result_t eigrp_intf_bandwidth_percent_update(eigrp_operation_t operation, eigrp_intf_context_t *context, uint32_t percent);
eigrp_result_t eigrp_intf_bandwidth_update(eigrp_operation_t operation, eigrp_intf_context_t *context, uint32_t bandwidth);
eigrp_result_t eigrp_intf_delay_update(eigrp_operation_t operation, eigrp_intf_context_t *context, uint32_t delay);
eigrp_result_t eigrp_intf_hello_interval_update(eigrp_operation_t operation, eigrp_intf_context_t *context, uint16_t seconds);
eigrp_result_t eigrp_intf_hold_time_update(eigrp_operation_t operation, eigrp_intf_context_t *context, uint16_t seconds);
eigrp_result_t eigrp_intf_passive_update(eigrp_operation_t operation, eigrp_intf_context_t *context);
eigrp_result_t eigrp_intf_nexthop_self_update(eigrp_operation_t operation, eigrp_intf_context_t *context);
eigrp_result_t eigrp_intf_split_horizon_update(eigrp_operation_t operation, eigrp_intf_context_t *context);
eigrp_result_t eigrp_intf_shutdown_update(eigrp_operation_t operation, eigrp_intf_context_t *context);
eigrp_result_t eigrp_nbr_static_create(eigrp_af_config_t *af, const eigrp_address_t *address, const char *interface_name);
eigrp_result_t eigrp_nbr_static_delete(eigrp_af_config_t *af, const eigrp_address_t *address, const char *interface_name);
eigrp_result_t eigrp_nbr_description_update(eigrp_operation_t operation, eigrp_instance_context_t *context, const eigrp_address_t *address, const char *description);
eigrp_result_t eigrp_nbr_max_prefix_update(eigrp_operation_t operation, eigrp_instance_context_t *context, const eigrp_address_t *address, const eigrp_prefix_limit_t *limit);
eigrp_result_t eigrp_nbr_max_prefix_all_update(eigrp_operation_t operation, eigrp_instance_context_t *context, const eigrp_prefix_limit_t *limit);
eigrp_result_t eigrp_nbr_log_update(eigrp_operation_t operation, eigrp_instance_context_t *context, eigrp_nbr_log_type_t type, bool enabled, uint16_t seconds);
eigrp_result_t eigrp_nbr_clear(eigrp_instance_t *runtime, const eigrp_nbr_clear_request_t *request, eigrp_nbr_clear_cb callback, void *arg, size_t *affected_count);
eigrp_result_t eigrp_auth_mode_update(eigrp_operation_t operation, eigrp_intf_context_t *context, eigrp_authentication_mode_t mode, const eigrp_auth_hmac_config_t *hmac);
eigrp_result_t eigrp_auth_keychain_update(eigrp_operation_t operation, eigrp_intf_context_t *context, const char *keychain);
eigrp_result_t eigrp_summary_create(eigrp_intf_context_t *context, const eigrp_prefix_t *prefix, const eigrp_summary_options_t *options);
eigrp_result_t eigrp_summary_delete(eigrp_intf_context_t *context, const eigrp_prefix_t *prefix);
eigrp_result_t eigrp_summary_auto_update(eigrp_operation_t operation, eigrp_instance_context_t *context);
eigrp_result_t eigrp_summary_metric_update(eigrp_operation_t operation, eigrp_instance_context_t *context, const eigrp_prefix_t *prefix, const eigrp_summary_metric_config_t *config);
eigrp_result_t eigrp_metric_default_update(eigrp_operation_t operation, eigrp_instance_context_t *context, const eigrp_metric_values_t *metric);
eigrp_result_t eigrp_metric_weights_update(eigrp_operation_t operation, eigrp_instance_context_t *context, const eigrp_metric_weights_t *weights);
eigrp_result_t eigrp_metric_variance_update(eigrp_operation_t operation, eigrp_instance_context_t *context, uint8_t variance);
eigrp_result_t eigrp_traffic_share_balanced_update(eigrp_operation_t operation, eigrp_instance_context_t *context);
eigrp_result_t eigrp_metric_maximum_hops_update(eigrp_operation_t operation, eigrp_instance_context_t *context, uint8_t maximum_hops);
eigrp_result_t eigrp_metric_version_update(eigrp_operation_t operation, eigrp_instance_context_t *context);
eigrp_result_t eigrp_topology_create(eigrp_instance_context_t *context);
eigrp_result_t eigrp_topology_delete(eigrp_instance_context_t *context);
eigrp_result_t eigrp_topology_default_information_update(eigrp_operation_t operation, eigrp_instance_context_t *context, eigrp_default_information_direction_t direction, const char *access_list);
eigrp_result_t eigrp_topology_max_prefix_update(eigrp_operation_t operation, eigrp_instance_context_t *context, const eigrp_prefix_limit_t *limit);
eigrp_result_t eigrp_topology_maximum_paths_update(eigrp_operation_t operation, eigrp_instance_context_t *context, uint8_t maximum_paths);
eigrp_result_t eigrp_topology_clear(eigrp_instance_context_t *context, const eigrp_topology_clear_request_t *request, size_t *affected_count);
eigrp_result_t eigrp_offset_add(eigrp_instance_context_t *context, const char *access_list, eigrp_offset_direction_t direction, uint32_t offset, const char *interface_name);
eigrp_result_t eigrp_offset_remove(eigrp_instance_context_t *context, const char *access_list, eigrp_offset_direction_t direction, uint32_t offset, const char *interface_name);
eigrp_result_t eigrp_distribute_add(eigrp_instance_context_t *context, eigrp_distribute_list_type_t type, const char *name, eigrp_offset_direction_t direction, const char *interface_name);
eigrp_result_t eigrp_distribute_remove(eigrp_instance_context_t *context, eigrp_distribute_list_type_t type, const char *name, eigrp_offset_direction_t direction, const char *interface_name);
eigrp_result_t eigrp_redist_add(eigrp_instance_context_t *context, const eigrp_redist_source_t *source, const eigrp_metric_values_t *metric, const char *route_map);
eigrp_result_t eigrp_redist_remove(eigrp_instance_context_t *context, const eigrp_redist_source_t *source);
eigrp_result_t eigrp_redist_max_prefix_update(eigrp_operation_t operation, eigrp_instance_context_t *context, const eigrp_prefix_limit_t *limit);
eigrp_result_t eigrp_timer_active_time_update(eigrp_operation_t operation, eigrp_instance_context_t *context, uint16_t seconds);
eigrp_result_t eigrp_eventlog_clear(eigrp_instance_context_t *context);
eigrp_result_t eigrp_eventlog_size_update(eigrp_operation_t operation, eigrp_instance_context_t *context, uint32_t size);
eigrp_result_t eigrp_debug_update(eigrp_operation_t operation, eigrp_debug_target_t target, unsigned long flags, eigrp_debug_scope_t scope);
eigrp_result_t eigrp_debug_af_update(eigrp_operation_t operation, const eigrp_state_request_t *request, eigrp_debug_af_category_t category, const eigrp_address_t *neighbor, eigrp_debug_scope_t scope);
eigrp_result_t eigrp_debug_packet_update(eigrp_operation_t operation, uint32_t packet_mask, unsigned long flags, eigrp_debug_scope_t scope);
const char *eigrp_debug_packet_category_name(eigrp_debug_packet_category_t category);
const char *eigrp_debug_packet_category_cli_name(eigrp_debug_packet_category_t category);
```

### `eigrp_mgnt.h`

```c
eigrp_result_t eigrp_intf_state_iterate(eigrp_af_config_t *config, eigrp_instance_t *runtime, const char *interface_name, eigrp_intf_state_iterate_cb callback, void *arg);
eigrp_result_t eigrp_nbr_state_iterate(eigrp_af_config_t *config, eigrp_instance_t *runtime, const char *interface_name, bool static_only, eigrp_nbr_state_iterate_cb callback, void *arg);
eigrp_result_t eigrp_topology_state_iterate(eigrp_af_config_t *config, eigrp_instance_t *runtime, const eigrp_prefix_t *destination, bool all_links, eigrp_topology_prefix_state_cb prefix_callback, eigrp_topology_route_state_cb route_callback, void *arg);
eigrp_result_t eigrp_topology_instance_iterate(eigrp_afi_t afi, eigrp_vrf_id_t vrf_id, uint16_t asn, eigrp_topology_instance_iterate_cb callback, void *arg);
eigrp_result_t eigrp_timer_state_iterate(const eigrp_instance_context_t *context, eigrp_timer_state_cb callback, void *arg);
eigrp_result_t eigrp_eventlog_state_read(const eigrp_instance_context_t *context, eigrp_eventlog_state_t *state);
eigrp_result_t eigrp_eventlog_msg_iterate(const eigrp_instance_context_t *context, eigrp_eventlog_msg_cb callback, void *arg);
const char *eigrp_eventlog_format_read(unsigned long opcode);
eigrp_result_t eigrp_eventlog_msg_format(const eigrp_eventlog_msg_t *entry, char *buffer, size_t buffer_size);
eigrp_result_t eigrp_stat_pfxlimits_summary_read(const eigrp_instance_context_t *context, eigrp_pfxlimits_summary_t *state);
eigrp_result_t eigrp_stat_accounting_iterate(const eigrp_instance_context_t *context, uint32_t *total_prefix_count, eigrp_stat_accounting_cb callback, void *arg);
eigrp_result_t eigrp_stat_traffic_read(const eigrp_instance_context_t *context, eigrp_statistics_traffic_state_t *state);
eigrp_result_t eigrp_status_capability_state_read(eigrp_status_capability_state_t *state);
eigrp_result_t eigrp_status_protocol_iterate(eigrp_status_protocol_cb callback, void *arg);
eigrp_result_t eigrp_status_tech_support_iterate(eigrp_status_protocol_cb callback, void *arg);
size_t eigrp_debug_af_state_count(void);
bool eigrp_debug_af_state_read(eigrp_debug_scope_t scope, size_t index, eigrp_debug_af_state_t *state);
```

### `eigrp_rib.h`

```c
eigrp_result_t eigrp_rib_routes_replay_instance(eigrp_instance_t *eigrp);
eigrp_result_t eigrp_rib_routes_replay(void);
eigrp_result_t eigrp_rib_redist_add(eigrp_instance_t *eigrp, const eigrp_rib_route_t *route);
eigrp_result_t eigrp_rib_redist_del(eigrp_instance_t *eigrp, const eigrp_rib_route_t *route);
eigrp_result_t eigrp_rib_event_process(eigrp_instance_t *eigrp, const eigrp_rib_event_t *event);
```

### `eigrp_sys.h`

```c
void eigrp_sys_intf_update(eigrp_vrf_id_t vrf_id, const eigrp_intf_runtime_state_t *state);
void eigrp_sys_intf_down(eigrp_vrf_id_t vrf_id, eigrp_ifindex_t ifindex, const char *interface_name, uint8_t type, uint32_t bandwidth, uint32_t mtu);
void eigrp_sys_intf_remove(eigrp_vrf_id_t vrf_id, eigrp_ifindex_t ifindex, eigrp_intf_remove_reason_t reason);
void eigrp_sys_intf_addr_update(eigrp_vrf_id_t vrf_id, eigrp_ifindex_t ifindex, const eigrp_prefix_t *address, eigrp_intf_remove_reason_t reason);
void eigrp_sys_policy_runtime_update(void);
eigrp_result_t eigrp_sys_filter_runtime_replace(eigrp_instance_t *eigrp, const char *interface_name, const eigrp_filter_runtime_snapshot_t *snapshot);
```

## 4. EIGRP-consumed APIs (host produces)

These functions are implemented by each platform integration, not by the
portable core. The core depends on the host to implement the declared
service. Their behavior is not interchangeable with an EIGRP protocol operation.


### `eigrp_rib.h`

```c
void eigrp_rib_init(void);
void eigrp_rib_finish(void);
void eigrp_rib_instance_delete(eigrp_instance_t *eigrp);
eigrp_result_t eigrp_rib_route_add(eigrp_instance_t *eigrp, const eigrp_rib_route_t *route);
eigrp_result_t eigrp_rib_route_del(eigrp_instance_t *eigrp, const eigrp_prefix_t *prefix);
eigrp_result_t eigrp_rib_redistribute_add(eigrp_instance_t *eigrp, const eigrp_redist_source_t *source);
eigrp_result_t eigrp_rib_redistribute_remove(eigrp_instance_t *eigrp, const eigrp_redist_source_t *source);
```

### `eigrp_sys.h`

```c
int eigrp_snprintf(char *dst, size_t size, const char *fmt, ...);
int eigrp_vsnprintf(char *dst, size_t size, const char *fmt, va_list args);
void eigrp_sys_runtime_init(void);
void eigrp_sys_runtime_finish(void);
void eigrp_sys_event_cancel(eigrp_event_t **event);
void eigrp_sys_event_add(eigrp_event_t **event, eigrp_event_callback_t callback, void *arg);
void eigrp_sys_timer_add(eigrp_event_t **event, eigrp_event_callback_t callback, void *arg, uint32_t delay_msec);
void eigrp_sys_read_add(eigrp_event_t **event, eigrp_instance_t *eigrp, eigrp_event_callback_t callback, void *arg);
void eigrp_sys_write_add(eigrp_event_t **event, eigrp_instance_t *eigrp, eigrp_event_callback_t callback, void *arg);
uint32_t eigrp_sys_timer_remaining_seconds(const eigrp_event_t *event);
uint64_t eigrp_sys_monotime_msec(void);
uint64_t eigrp_sys_wallclock_msec(void);
void eigrp_sys_software_version(uint8_t *major, uint8_t *minor);
eigrp_work_queue_t *eigrp_sys_work_queue_create(eigrp_instance_t *eigrp, const char *name, eigrp_work_queue_func_t workfunc, eigrp_work_queue_delete_func_t deletefunc);
void eigrp_sys_work_queue_free(eigrp_work_queue_t *queue);
void eigrp_sys_work_queue_clear(eigrp_work_queue_t *queue);
void eigrp_sys_work_queue_enqueue(eigrp_work_queue_t *queue, void *data);
eigrp_instance_t *eigrp_sys_work_queue_instance(eigrp_work_queue_t *queue);
eigrp_result_t eigrp_sys_socket_open(eigrp_instance_t *eigrp);
void eigrp_sys_socket_close(eigrp_instance_t *eigrp);
void eigrp_sys_socket_send_buffer_ensure(eigrp_instance_t *eigrp, uint32_t minimum);
bool eigrp_sys_router_id_get(eigrp_instance_t *eigrp, uint32_t *router_id);
eigrp_result_t eigrp_sys_vrf_resolve(const char *vrf_name, eigrp_vrf_id_t *vrf_id);
eigrp_result_t eigrp_sys_interface_walk(eigrp_instance_t *eigrp, eigrp_sys_interface_walk_cb callback, void *arg);
int eigrp_sys_multicast_interface_update(eigrp_operation_t operation, eigrp_instance_t *eigrp, eigrp_intf_t *ei);
int eigrp_sys_multicast_join(eigrp_instance_t *eigrp, eigrp_intf_t *ei);
int eigrp_sys_multicast_leave(eigrp_instance_t *eigrp, eigrp_intf_t *ei);
int eigrp_sys_packet_send(eigrp_instance_t *eigrp, eigrp_intf_t *ei, const eigrp_address_t *destination, const uint8_t *payload, size_t length);
bool eigrp_sys_packet_receive(eigrp_instance_t *eigrp, uint8_t *buffer, size_t capacity, size_t *received_length, eigrp_ifindex_t *ifindex, eigrp_address_t *source, eigrp_address_t *destination, eigrp_packet_rx_meta_t *meta);
int eigrp_sys_ipv4_packet_send(eigrp_instance_t *eigrp, eigrp_intf_t *ei, const eigrp_address_t *destination, const uint8_t *payload, size_t length);
bool eigrp_sys_ipv4_packet_receive(eigrp_instance_t *eigrp, uint8_t *buffer, size_t capacity, size_t *received_length, eigrp_ifindex_t *ifindex, eigrp_address_t *source, eigrp_address_t *destination, eigrp_packet_rx_meta_t *meta);
int eigrp_sys_ipv6_packet_send(eigrp_instance_t *eigrp, eigrp_intf_t *ei, const eigrp_address_t *destination, const uint8_t *payload, size_t length);
bool eigrp_sys_ipv6_packet_receive(eigrp_instance_t *eigrp, uint8_t *buffer, size_t capacity, size_t *received_length, eigrp_ifindex_t *ifindex, eigrp_address_t *source, eigrp_address_t *destination, eigrp_packet_rx_meta_t *meta);
void eigrp_sys_policy_init(void);
void eigrp_sys_policy_finish(void);
eigrp_result_t eigrp_sys_policy_instance_create(eigrp_instance_t *eigrp);
void eigrp_sys_policy_instance_delete(eigrp_instance_t *eigrp);
eigrp_result_t eigrp_sys_filter_evaluate(eigrp_instance_t *eigrp, eigrp_distribute_list_type_t type, const char *name, const eigrp_prefix_t *prefix, eigrp_filter_decision_t *decision);
eigrp_result_t eigrp_sys_redistribute_route_map_evaluate(eigrp_instance_t *eigrp, const char *name, const eigrp_rib_route_t *route, eigrp_filter_decision_t *decision);
eigrp_result_t eigrp_sys_summary_leak_map_evaluate(eigrp_instance_t *eigrp, const char *name, const eigrp_prefix_t *prefix, eigrp_filter_decision_t *decision);
bool eigrp_sys_auth_key_lookup(const char *keychain_name, uint32_t *key_id, char *key_string, size_t key_string_size);
```

## 5. Callback signatures and invocation ownership

Callback signatures are part of the effective source-level interface even
though a `typedef` does not itself export a callable symbol. The **supplier**
provides the function pointer; the **invoker** executes it. A callback can
cross the boundary in the opposite direction from the function accepting it.

| Callback | Header | Supplier | Invoker |
|---|---|---|---|
| `eigrp_instance_iterate_cb` | `eigrp.h` | Host | Core |
| `eigrp_nbr_clear_cb` | `eigrp_cli.h` | Host | Core |
| `eigrp_intf_state_iterate_cb` | `eigrp_mgnt.h` | Host | Core |
| `eigrp_nbr_state_iterate_cb` | `eigrp_mgnt.h` | Host | Core |
| `eigrp_topology_prefix_state_cb` | `eigrp_mgnt.h` | Host | Core |
| `eigrp_topology_route_state_cb` | `eigrp_mgnt.h` | Host | Core |
| `eigrp_topology_instance_iterate_cb` | `eigrp_mgnt.h` | Host | Core |
| `eigrp_timer_state_cb` | `eigrp_mgnt.h` | Host | Core |
| `eigrp_eventlog_msg_cb` | `eigrp_mgnt.h` | Host | Core |
| `eigrp_stat_accounting_cb` | `eigrp_mgnt.h` | Host | Core |
| `eigrp_status_protocol_cb` | `eigrp_mgnt.h` | Host | Core |
| `eigrp_work_queue_func_t` | `eigrp_sys.h` | Core | Host |
| `eigrp_work_queue_delete_func_t` | `eigrp_sys.h` | Core | Host |
| `eigrp_event_callback_t` | `eigrp_sys.h` | Core | Host |
| `eigrp_sys_interface_walk_cb` | `eigrp_sys.h` | Core | Host |

Exact callback typedef declarations:


### `eigrp.h` callbacks

```c
typedef eigrp_result_t (*eigrp_instance_iterate_cb)(eigrp_instance_t *instance, void *arg);
```

### `eigrp_cli.h` callbacks

```c
typedef void (*eigrp_nbr_clear_cb)(const eigrp_nbr_clear_state_t *state, void *arg);
```

### `eigrp_mgnt.h` callbacks

```c
typedef eigrp_result_t (*eigrp_intf_state_iterate_cb)(const eigrp_intf_state_t *state, void *arg);
typedef eigrp_result_t (*eigrp_nbr_state_iterate_cb)(const eigrp_nbr_state_t *state, void *arg);
typedef eigrp_result_t (*eigrp_topology_prefix_state_cb)(const eigrp_topology_prefix_state_t *state, void *arg);
typedef eigrp_result_t (*eigrp_topology_route_state_cb)(const eigrp_topology_route_state_t *state, void *arg);
typedef eigrp_result_t (*eigrp_topology_instance_iterate_cb)(eigrp_instance_t *runtime, void *arg);
typedef eigrp_result_t (*eigrp_timer_state_cb)(const eigrp_timer_state_t *state, void *arg);
typedef eigrp_result_t (*eigrp_eventlog_msg_cb)(uint32_t event_number, const eigrp_eventlog_msg_t *entry, const char *format, void *arg);
typedef eigrp_result_t (*eigrp_stat_accounting_cb)(const eigrp_stat_nbr_limit_t *state, void *arg);
typedef eigrp_result_t (*eigrp_status_protocol_cb)(const eigrp_status_protocol_state_t *state, void *arg);
```

### `eigrp_sys.h` callbacks

```c
typedef eigrp_work_queue_result_t (*eigrp_work_queue_func_t)(eigrp_work_queue_t *queue, void *data);
typedef void (*eigrp_work_queue_delete_func_t)(eigrp_work_queue_t *queue, void *data);
typedef void (*eigrp_event_callback_t)(void *arg);
typedef void (*eigrp_sys_interface_walk_cb)(const eigrp_intf_runtime_state_t *state, void *arg);
```

## 6. Public cross-boundary C types

The names below are an inventory, **not** a claim that their layouts or
numeric enum values are stable. The exact `typedef` and structure definitions
remain in their headers. Exported structure layouts, pointer ownership and
callback data lifetime require separate review before any ABI guarantee.


### `eigrp.h`

**Values and enums.** `eigrp_result_t`, `eigrp_operation_t`, `eigrp_afi_t`, `eigrp_address_t`, `eigrp_prefix_t`, `eigrp_metrics_t`, `eigrp_metric_values_t`, `eigrp_metric_weights_t`, `eigrp_prefix_limit_t`, `eigrp_offset_direction_t`, `eigrp_distribute_list_type_t`, `eigrp_filter_decision_t`, `eigrp_redist_protocol_t`, `eigrp_redist_source_t`, `eigrp_state_request_t`, `eigrp_instance_context_t`, `eigrp_debug_scope_t`, `eigrp_debug_af_category_t`.

**Scalar aliases.** `eigrp_topology_id_t`, `eigrp_vrid_t`, `eigrp_vrf_id_t`, `eigrp_ifindex_t`, `eigrp_bandwidth_t`, `eigrp_delay_t`, `eigrp_metric_t`, `eigrp_scaled_t`, `eigrp_system_metric_t`, `eigrp_system_delay_t`, `eigrp_system_bandwidth_t`, `eigrp_route_instance_t`.

**Opaque handles.** `eigrp_process_t`, `eigrp_virt_router_t`, `eigrp_instance_t`, `eigrp_intf_t`, `eigrp_nbr_t`, `eigrp_event_t`, `eigrp_work_queue_t`, `eigrp_named_config_t`, `eigrp_af_config_t`, `eigrp_intf_config_t`.


### `eigrp_cli.h`

**Values and enums.** `eigrp_intf_context_t`, `eigrp_authentication_mode_t`, `eigrp_auth_hmac_config_t`, `eigrp_summary_options_t`, `eigrp_summary_metric_config_t`, `eigrp_nbr_log_type_t`, `eigrp_default_information_direction_t`, `eigrp_nbr_clear_request_t`, `eigrp_nbr_clear_state_t`, `eigrp_topology_clear_request_t`, `eigrp_debug_packet_category_t`, `eigrp_debug_target_t`.


### `eigrp_mgnt.h`

**Snapshots and enums.** `eigrp_intf_state_t`, `eigrp_nbr_state_t`, `eigrp_topology_prefix_state_t`, `eigrp_topology_route_state_t`, `eigrp_timer_state_type_t`, `eigrp_timer_state_t`, `eigrp_eventlog_msg_t`, `eigrp_eventlog_state_t`, `eigrp_statistics_traffic_state_t`, `eigrp_stat_nbr_limit_t`, `eigrp_pfxlimits_af_state_t`, `eigrp_pfxlimits_summary_t`, `eigrp_status_capability_state_t`, `eigrp_status_protocol_state_t`, `eigrp_debug_af_state_t`.

**Event-log scalar alias.** `eventmsg_arg_t`.


### `eigrp_rib.h`

**Values and enums.** `eigrp_rib_route_type_t`, `eigrp_rib_nexthop_t`, `eigrp_rib_route_t`, `eigrp_rib_event_type_t`.

**Public event type.** `eigrp_rib_event_t` (its layout is currently exposed).


### `eigrp_sys.h`

**Values and enums.** `eigrp_work_queue_result_t`, `eigrp_intf_runtime_state_t`, `eigrp_intf_remove_reason_t`, `eigrp_packet_rx_meta_t`, `eigrp_filter_runtime_snapshot_t`.


## 7. Provisional integration rules

1. **Host-neutral values:** platform-specific FRR, BIRD, Linux, Netlink,
   NDK, socket, and scheduler objects stay in their integration layers.
   Normalize inputs to EIGRP-owned types before calling the core.
2. **Opaque state:** integrations must not treat runtime identities as
   permission to inspect DUAL, topology, neighbor, packetizer, or RTP internals.
   A missing capability should lead to consideration of a suitable public API,
   not automatic exposure of a private structure.
3. **Explicit lifecycle:** borrowed pointers, snapshots, ownership transfers,
   callback execution context, thread affinity, and teardown ordering must be
   made unambiguous before the interface is frozen. A declaration alone does
   not establish thread safety or lifetime guarantees.
4. **Protocol correctness first:** this inventory places no restriction on
   bug fixes, Cisco-compatible packet/metric behavior, internal restructuring,
   or Linux implementation work. Changes should still consider the impact on
   integration consumers, even during development.
5. **Do not infer completeness from compilation:** a host may still bypass the
   intended boundary using private headers or direct structure access. Such
   dependencies should be identified and either removed or explicitly reviewed.

## 8. Work required before a controlled contract

- Audit public declarations against actual producers, consumers, and
  application-specific private-header dependencies in FRR, BIRD, Unix/Linux,
  OpenWrt and independent integrations where source is available.
- Validate functional coverage: each capability that a platform legitimately
  needs from EIGRP should have an appropriately scoped API, including setters,
  operations, event notifications and state snapshots.
- Revisit ownership, configuration retention, callback threading, error
  semantics, event lifetime, and RIB/packet boundaries in real integrations.
- Decide whether source compatibility alone is promised, or also ABI
  compatibility for separately compiled binaries. Avoid promising a stable C
  ABI while exposing mutable public structures by value.
- Introduce deliberate API versioning, compatibility review, deprecation and
  migration rules **only when the Linux support implementation is complete**.
- Reconcile this README with `specs/public-api.md`, removing its premature
  contract language and any declarations that have become stale.

## 9. Inventory validation

This document inventories declarations **only**. It does not claim that every
API is currently implemented, exercised, portable, or free of latent defects.
No C source is modified by this documentation change.

Source inventory counts are recorded below and should be updated whenever a
public header changes during `linux-support` development.


| Item | Count |
|---|---:|
| EIGRP-produced functions, including inline helpers | 118 |
| Host-produced / EIGRP-consumed functions | 48 |
| Total function declarations | 166 |
| Of which header-only inline helpers | 3 |
| Callback typedef declarations | 15 |

Baseline GitHub links: [branch](https://github.com/diivious/openeigrp/tree/linux-support), [five public headers](https://github.com/diivious/openeigrp/tree/linux-support/eigrp/code), [prior API design](https://github.com/diivious/openeigrp/blob/linux-support/specs/public-api.md).

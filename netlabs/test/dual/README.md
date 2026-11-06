# DUAL interoperability tests

Copyright (C) 2026 Donnie V. Savage

`2R-routes/` is a discoverable live dual-vendor test of IPv4/IPv6 internal
and redistributed routes, lifecycle, and one-path FD/CD/RD. Its TLV2 checks
use the independent Cisco default-K oracle in `netlabs/common/metric.py`,
including exact seed bandwidth/delay/MTU and changed reported distance.
`test_metric.py` covers classic TLV1 scaled metrics and 1G-500G TLV2
arithmetic locally. These synthetic vectors are **not** live multi-gig
Cisco qualification or evidence of mixed classic-mode adjacency.

`2R-internal.py` is a legacy 2R route driver, not a replacement for the
qualified assertions in `2R-routes/`. The `2R-6R` naming range describes
supported scenario sizes; no complete 3R-6R DUAL live scenario is currently
implemented. Live results must still be collected from the remote appliance;
a successful local regression alone never establishes Cisco interoperability.

Add new scenarios under their own 2R-6R directories with `driver.py`,
`scenario.yaml`, and `topology.yml`. A DUAL qualification must collect wire
and topology/RIB evidence for the specific operation, rather than merely
checking that the destination eventually converged.

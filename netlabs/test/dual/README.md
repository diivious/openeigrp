# DUAL interoperability tests

Copyright (C) 2026 Donnie V. Savage

No complete DUAL live scenario is present on the baseline. The separate
`2R-internal.py` driver remains incomplete until a full fixture and real
assertions are provided. `--dual` must report NOT_IMPLEMENTED, not PASS,
when no runnable DUAL case exists. New tests go into separate purpose-named
2R-6R directories with `driver.py`, `scenario.yaml`, and `topology.yml`.
DUAL qualification requires wire evidence of QUERY/REPLY and state/RIB
observations as appropriate, not only a converged route.

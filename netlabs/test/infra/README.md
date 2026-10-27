# Netlab infrastructure regression tests

Copyright (C) 2026 Donnie V. Savage

`test_network_fault.py`, `test_runner.sh`, `test_dispatch.sh`, and
`test_regressions.py` exercise packet
fault setup/cleanup, remote runner lifecycles, grouped scenario discovery,
client dispatch, regression execution, reporting, and batch safety. They use
isolated mocks and do not deploy real Cisco/OpenEIGRP routers. The unified
`netlabs/tools/remote-uut.sh --all` stages and runs them on StormTrooper,
where Linux `flock` and `/proc` and the provisioned Python packages are
available. Direct developer execution also works on a suitably provisioned
Linux environment.

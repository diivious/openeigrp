# Reliable transport protocol and wire tests

Copyright (C) 2026 Donnie V. Savage

Unit regressions `test_wire.py` and `test_capture.py` validate actual packet
parsing and process behavior with synthetic PCAP data and local capture mocks.
The legacy live `2R-neighbor` scenario is classified as RTP startup evidence,
but it currently qualifies neighbor-table formation only, not packet-level
RTP-01 initialization. Place each additional RTP live case in a named
subdirectory containing `driver.py`, `scenario.yaml`, and `topology.yml`.
Use `netlabs/tools/remote-uut.sh --rtp` to execute the group.

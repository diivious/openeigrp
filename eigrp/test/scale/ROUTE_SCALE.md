# EIGRP route and fault scale testing

Base: GitHub `diivious/eigrp` branch `peer-scale`, commit
`1a7f15e1f78d749b42bc87c8b120e32f4c670e7f`.

The test overlay changes only Make targets and portable test harness files. It
uses actual checksummed EIGRP UPDATE/Hello/ACK packets on the Unix virtual wire
and real Unix-hosted EIGRP UUT processes. Route sources originate through the
actual host-RIB notification and redistribution APIs. No production module is
modified or simulated DUAL state inserted directly.

## Targets

The root Makefile exposes one `scale` target. The default is the quick pacing,
lost-ACK and 32-peer multicast recovery gate. Select heavier tests with
`SUITE` or `CASE`:

| Command | Qualification |
| --- | --- |
| `make scale` | Quick pacing/backpressure gate |
| `make scale SUITE=100k` | 2,048 peers and all five route-volume tests |
| `make scale SUITE=stress` | Withdraw, multicast, linkdown, mixed-TLV and churn |
| `make scale SUITE=auth` | MD5-authenticated route flooding |
| `make scale SUITE=all` | All heavy suites, collecting failures |
| `make scale CASE=peers` | Establish 2,048 neighbors |
| `make scale CASE=internal` / `external` | 100K route injection |
| `make scale CASE=line` / `kite` | 100K mixed-route propagation |
| `make scale CASE=peer-routes` | 50 routes from each of 2,048 peers |
| `make scale CASE=withdraw` | 100K withdrawal and reinjection |
| `make scale CASE=mcast` | Delayed ACK / CR MTU and recovery |
| `make scale CASE=linkdown` | Link loss and relearning |
| `make scale CASE=mixed-tlv` | Classic and MP TLVs on the same interface |
| `make scale CASE=churn` | Repeated updates and withdrawal |
| `make scale CASE=auth-md5` | 100K authenticated redistribution |

The multicast test originates external routes through R1's real static-route
redistribution interface. Injecting routes from a peer on the same shared LAN
would test split horizon, not outbound multicast pacing. When a CR SEQUENCE
Hello fits the MTU, the test requires CR delivery before releasing slow-peer
ACKs. When the slow-peer exclusion list exceeds the MTU (the default
2,048-peer/300-slow-peer case), it instead requires multicast admission to
pause until those ACKs are released, then resume and drain.

The mixed-TLV test also originates redistributed routes on R1 rather than
expecting an UPDATE from a shared-LAN peer to be reflected onto the same
interface. It checks newly transmitted classic and multiprotocol TLVs after
the route injection, not merely the neighbor capability flags.

Every suite continues through its constituent tests after a failure, reports
PASS/FAIL for each, and exits unsuccessfully if any test failed. Tests execute
sequentially to avoid competing for RAM. `make scale` is deliberately the
quick TRANSMIT pacing/backpressure gate and does not run the full `make uut`.

All names are explicit. Old numbered targets and `scale-tests` are intentionally
not retained as aliases. SHA-256 authenticated flooding will be a separate
future `scale-auth-sha2` test after the required configuration path is ready.

## Running

```sh
make scale                    # Quick pacing/backpressure gate
make scale SUITE=100k
make scale SUITE=stress
make scale SUITE=auth
make scale SUITE=all
make scale CASE=peers
make scale CASE=mcast

# Lightweight development smoke (not full qualification)
EIGRP_SCALE_ROUTES=1000 make scale CASE=withdraw
EIGRP_SCALE_ROUTES=1000 EIGRP_SCALE_CHURN_ROUTES=100 make scale CASE=churn
EIGRP_SCALE_ROUTES=1000 make scale CASE=auth-md5
EIGRP_SCALE_PEERS=64 EIGRP_SCALE_MCAST_SLOW_PEERS=16 \
 EIGRP_SCALE_MCAST_ROUTES=48 make scale CASE=mcast
EIGRP_SCALE_ROUTES=1000 make scale CASE=linkdown
EIGRP_SCALE_TLV_ROUTES=48 make scale CASE=mixed-tlv
```

## Parameters and limits

* `EIGRP_SCALE_ROUTES=100000`: route total in 100K and source-RIB tests.
* `EIGRP_SCALE_PEERS=2048`: peer-route and multicast tests.
* `EIGRP_SCALE_ROUTES_PER_PEER=50`: peer-route test.
* `EIGRP_SCALE_BATCH=24`: route TLVs per injected RTP packet.
* `EIGRP_SCALE_WATCHDOG_SEC=1200`: per-test deadlock watchdog.
* `EIGRP_SCALE_CHURN_ROUTES=10000`: prefixes changed per churn cycle (must be below base route count).
* `EIGRP_SCALE_CHURN_CYCLES=3`: number of churn cycles.
* `EIGRP_SCALE_MCAST_ROUTES=256`: multicast stress route count.
* `EIGRP_SCALE_MCAST_SLOW_PEERS=300`: peers whose ACKs are held until release.
* `EIGRP_SCALE_TLV_ROUTES=2048`: mixed-TLV route count.

The default 100K route tests can use significant RAM and may run for many
minutes. Monitor host memory and choose smaller sizes for an initial smoke.
The watchdog is a liveness requirement, **not** a subsecond convergence target.
Elapsed times and RSS are printed as information only. The tests count actual
UUT topology/RIB records with streaming reads, check that the required
adjacencies remain UP, and require every involved UUT process to survive.

The heavy scenarios must be run on a full checkout of `peer-scale`. Generator
unit tests alone cannot qualify protocol behavior or performance.

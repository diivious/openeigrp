# 2R-routes: bidirectional OE/CE DUAL route qualification

Native netlab deploys one OpenEIGRP-FRR and one CSR1000v, named process
`OpenEIGRP`, AS 4473, both IPv4/IPv6. Initial connected loopback origins and
static redistribution policy are configured by `eigrp-2r-interop.j2`. The
runtime driver only stimulates EIGRP by adding/removing tagged static routes,
changing and restoring metrics, and resetting the peer for fresh captures.
Peer resets are captured on the surviving Cisco interface, using cached
IPv6 link-local neighbor identities for loss detection.

```
netlabs/tools/remote-uut.sh --scenario 2R-routes
python3 -m unittest discover -s netlabs/test/dual -p 'test_2r_*.py'
```

DUAL-01 asserts exact bidirectional internal route type, wire UPDATE, metric,
next-hop and RIB convergence. DUAL-02 asserts exact bidirectional external
prefix, external TLV/provenance, tag 7 and metric. DUAL-03 performs actual
announce, metric change, unreachable-distance withdrawal and re-advertisement
for each originating vendor, requiring packet captures and RIB removal/recovery.
DUAL-04 reads actual topology RD/CD/FD on each receiver for its *single*
learned path. It does **not** assert feasible successor selection: 2R has
no alternate path; that requires the planned 4R diamond. On a DUAL-03 metric
increase, IOS XE retains the historical feasible distance (FD) while the
current computed distance (CD) and reported distance (RD) increase. DUAL-03
requires wire UPDATE evidence and a changed CD/RD, not FD == CD or a new FD.

Outputs include pre/post control-plane snapshots, PCAPs, decoded wire events,
image ID/digests and per-AF outcomes. Any absent provenance or topology field
fails its assertion; the test never invents missing metric data. On partial
failure, transient route origin changes are reversed before runner teardown.

The existing `netlabs/uut/2R-internal.py` smoke driver remains independent and
now has a complete native netlab fixture at `netlabs/uut/2R-internal/`. Its
simpler RIB assertions are not a substitute for this fuller DUAL scenario.

The October 28, 2026 Cisco/OpenEIGRP live report
`uut-20261028T191453Z-Qa9iRVis` verified that a metric increase changes the
current RD/CD while Cisco retains its historical FD. The revised DUAL-03
assertions still require live rerun on StormTrooper before qualification.

The netlab deployment ID is `eigrp-2r-dual` (within netlab's 16-character
name limit). OE uses the supported unfiltered Zebra IPv6 RIB command, with
EIGRP-specific rows selected by route source. CE uses native IOS XE route
commands: IPv4 may supply the mask in a preceding subnet heading, while
IPv6 external routes appear as `EX` without a preceding `D`. The RIB checks
normalize those observed formats and still require exact EIGRP prefixes. A missing advertised/learned prefix remains FAIL.

## Host capture prerequisite

Wire assertions need the same noninteractive namespace access as RTP. Use
`netlabs/tools/remote/enable-packet-access.sh` under an administrator account
on StormTrooper before starting the live scenario. Missing access is `BLOCKED`.

Failed external-route assertions now preserve `dual02-failure-*` and
`dual03-failure-*` configuration, RIB and topology snapshots before undoing
the stimulus. `dual03-metric-*` snapshots record the changed default-metric
while it is applied. Cisco IPv6 prefix assertions compare parsed prefix
identity rather than the capitalization of IOS XE output. These diagnostics
do not change route admission or bypass any required objective.

The DUAL-03 lifecycle test accepts Cisco's unreachable QUERY as a withdrawal
signal as well as an unreachable UPDATE. It still checks RIB removal and
re-advertisement; a query alone cannot qualify recovery.

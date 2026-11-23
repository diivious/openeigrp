# OE-03: Cisco-to-Cisco MD5 wire control

This scenario creates two **Cisco CSR1000v IOS XE 17.03.06** routers. There is
**no OpenEIGRP node and no OpenEIGRP production change**. The existing native
StormTrooper runner owns teardown and artifact collection.

Run from the OpenEIGRP repository root:

```sh
netlabs/tools/remote-uut.sh --scenario 2R-md5-cisco
```

Use the published/default runner (no `--local` required). The image selector's
Cisco-only path deliberately skips OpenEIGRP image pinning. The CSR image must
already be installed on StormTrooper. Two virtual IOS instances require more
RAM and CPU than the normal OE/CSR lab.

The driver starts the two CSR1000v containers with `netlab up --no-config`,
then configures the links and loopbacks on **one IOS router at a time** through
the existing Cisco SSH helper. This deliberately avoids Netlab's parallel
Ansible `ios_config` phase, which timed out on the second Cisco VM during the
initial live attempt, even though both CSR instances had booted. Before
starting MD5 it verifies that both GigabitEthernet2 links are up/up.

The driver then applies classic EIGRP AS 4473 with a lab-only MD5 keychain on
both Cisco interfaces.
The configuration style is incidental: the captured EIGRP on-wire protocol is
the subject of this experiment. Capture begins **before** EIGRP is enabled.
It first records bidirectional INIT/ACK/EOT and adjacency establishment, then
adds one advertised loopback per router. PASS requires both remote routes to
be installed, two-way Hello/INIT/ACK and route UPDATE/ACK traffic on the wire,
and valid EIGRP packet checksums.

Required artifacts:

* `cisco-cisco-md5.pcap`: raw, complete capture suitable for independent digest reconstruction.
* `cisco-md5-packets.json`: packet-by-packet fields, MD5 auth sequence/key ID,
  digest, TLV types and *diagnostic-only* OpenEIGRP digest matches.
* `report.json`: independently gated objective results and packet statistics.
* `csr1-*` / `csr2-*`: versions, running configurations, neighbors, RIBs,
  container logs and SSH CLI transcripts.

The captured Cisco-to-Cisco regression fixture at
`netlabs/test/rtp/fixtures/csr1000v-17.03.06-md5-c2c-20261122.pcap`
comes from live report `uut-20261122T162645Z-vBfZoA1h`. Its 20-byte
header-only Hello acknowledgments carry no Authentication TLV: they provide
RTP ACK evidence but must not be counted as authenticated Hellos.

The offline analyzer supplied by the earlier OE-03 diagnostic overlay can
examine the fresh capture afterward:

```sh
/usr/bin/python3 netlabs/test/rtp/md5_digest/diagnose.py \
  logs/netlab/uut-<report-id>/cisco-cisco-md5.pcap \
  --json /tmp/oe03-cisco-cisco-digest.json
```

That companion analyzer is not required for the live scenario. Digest
hypotheses do not determine PASS. **Cisco's actual successful authenticated
exchange is the reference**, including its auth TLV sequence behavior.

This is a diagnostic control, not an allocation/DUAL bug fix. The scenario is
not part of the 130 portable YAML UUT scenarios, and a local unit-test PASS is
not evidence of a live Cisco test.

Security: key `oe03-lab-md5` is a disposable laboratory secret. Report archives
contain the configured key and must not be used as production credentials.

### IOS SSH readiness and failure diagnostics

Both CSR1000v instances may briefly accept a management connection and then
time out while booting. The scenario retries only transient SSH transport
failures for up to 180 seconds per operation; IOS parser/configuration errors
fail immediately. Replayed configuration consists only of idempotent commands.
The report directory retains per-router `*-ssh-retries.log`, `*-docker-state.json`,
and `docker-stats.log` to distinguish transport instability from router crashes
or resource pressure.


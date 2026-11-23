# Cisco MD5 digest research (OE-03 follow-up)

This **offline diagnostic** uses original StormTrooper packet captures from
November 22, 2026. It changes no OpenEIGRP implementation files and sends no
packets to Cisco. It is not a Cisco receive-side acceptance test.

## Evidence

The three fixtures in `netlabs/test/rtp/fixtures/` come from CSR1000v IOS XE
17.03.06, EIGRP AS 4473, sender `10.44.53.2`, authentication key ID 7, and
**lab-only** key `oe03-lab-md5`:

| Fixture | StormTrooper report | Purpose |
| --- | --- | --- |
| `csr1000v-17.03.06-md5-hello.pcap` | `uut-20261122T124135Z-sA9Uclxx` | Initial Cisco transmit verification |
| `csr1000v-17.03.06-md5-named.pcap` | `uut-20261122T141217Z-1g4lNkJi` | Named configuration, INIT exchange |
| `csr1000v-17.03.06-md5-classic.pcap` | `uut-20261122T145531Z-8tJ23Gr3` | Classic configuration, INIT exchange |

The fixtures include repeated transmissions, so packet counts **are not**
independent samples. The known test key must never be deployed in a real
network. Cisco source packets were captured with no production secrets.

## Execute

From the repository root:

```bash
python3 -m pytest -q netlabs/test/rtp/test_md5_digest.py
python3 netlabs/test/rtp/md5_digest/diagnose.py \
  netlabs/test/rtp/fixtures/csr1000v-17.03.06-md5-hello.pcap \
  netlabs/test/rtp/fixtures/csr1000v-17.03.06-md5-named.pcap \
  netlabs/test/rtp/fixtures/csr1000v-17.03.06-md5-classic.pcap \
  --json /tmp/cisco-md5-digest.json
```

To examine a later StormTrooper capture, pass its `.pcap` path. If the test
network uses a different key, source, AS, or key ID, supply `--secret`,
`--source`, `--asn`, and `--key-id`. The analysis never connects to StormTrooper.

## Interpretation

For each captured Cisco-originated authenticated EIGRP packet, the tool:

1. Validates IPv4/EIGRP packet boundaries, authentication TLV framing, and the
   on-wire EIGRP checksum.
2. Extracts both **RTP sequence** and **authentication sequence** without
   conflating them.
3. Computes labelled MD5 candidate inputs. The first two match the current
   OpenEIGRP basic-Hello and UPDATE calculations. Additional hypotheses vary
   key placement (before/after header/payload), raw versus 16-byte-padded key,
   full versus partial post-auth TLV coverage, and checksum values (zero,
   captured, and checksum of a zeroed digest).
4. Reports exact matches with captured Cisco digests; an unmatched packet is
   explicitly `unresolved`, not evidence for a guessed alternative.
5. Compares Cisco 90-byte topology-list Hellos and Goodbyes byte by byte,
   displaying both the K-value and checksum differences. Different digests
   alone **do not establish** whether Cisco authenticates K-values.

The current captures confirm that ordinary 80-byte Cisco MD5 Hellos match the
44-byte-prefix-plus-padded-key calculation, but Cisco 60-byte authentication-
only INIT UPDATEs and 90-byte Hellos with TLV `0x00f5` do not match the tested
candidate forms. These statements apply to Cisco's **transmit** behavior only.

### Limitations and next evidence

This is a finite hypothesis search, not cryptanalysis and not proof that Cisco
uses some unknown algorithm. We do **not** claim Cisco accepts modified
Goodbyes, nor that route-carrying UPDATEs use the same digest construction as
INIT UPDATEs. Classic and named configuration do not constitute separate core
protocols. No production authentication changes are recommended from these
unmatched hashes alone.

If further offline hypotheses fail, obtain a Cisco-to-Cisco authenticated
adjacency capture to characterize Cisco's successful INIT, ACK and route
exchange. Preserve the original PCAP and known lab configuration for fixture
construction rather than editing packets into synthetic Cisco evidence.

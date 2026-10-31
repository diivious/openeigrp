# 4R-addpath-compat: Cisco/OpenEIGRP AddPath compatibility

Mixed topology: OpenEIGRP hub (`hub`) and two OpenEIGRP spoke origins (`r2`,
`r3`), with a Cisco CSR1000v recipient (`csr`). Netlab provisions the AS 4473
named-mode address families; the driver changes only reversible static routes
and the hub's outgoing AddPath interface configuration.

```sh
netlabs/tools/remote-uut.sh --scenario 4R-addpath-compat
```

The test requires equal-metric external paths to the same IPv4 and IPv6
prefix. For each AF it captures actual EIGRP TLV2 UPDATE packets at the
Cisco-facing interface and validates:

1. **AP-INIT**: Cisco has the route, and initial sync includes AddPath
   extended attributes (`0x07`) with at least one extra next hop. IPv6
   restarts adjacency four times to expose intermittent startup failures.
2. **AP-WITHDRAW**: Withdrawal of one spoke route leaves the destination
   reachable. A new UPDATE carries no extra AddPath, while the advertised
   destination delay/bandwidth remain identical and Cisco retains the route.
3. **AP-RECOVER**: Re-adding the spoke route produces a fresh AddPath UPDATE
   and Cisco retains external-route reachability.

The wire decoder is used to parse actual PCAPs; raw extended attributes are
checked using the production TLV2 AddPath format. The Cisco RIB is inspected
independently. Actual per-path Cisco installation is **not** claimed: this
scenario qualifies acceptance and continued reachability with the extension
on the wire. The scenario may expose Cisco IOS XE behavior that requires a
separate protocol/design decision; it must never silently relax its assertions.

Captured evidence and CLI diagnostics are saved under the ordinary netlab
artifacts directory. The scenario runner controls teardown and recovers
capture/fault markers. Source and route state are restored in `finally`.

**Not a substitute for portable UUT ownership and DUAL cleanup assertions.**

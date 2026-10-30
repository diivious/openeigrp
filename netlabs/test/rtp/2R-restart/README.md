# Isolated RTP-12 routing-runtime restart qualification

Run `netlabs/tools/remote-uut.sh --scenario 2R-restart` independently from
`2R-transport`. Each of the four role/address-family cases starts from a fresh
native `netlab up` deployment. `netlab down` precedes the next deployment.
IPv4/IPv6 ICMP and both-family adjacency checks must pass before each case.

**Do not stop or restart Docker containers as a test stimulus.** Removing a
container destroys its containerlab link and invalidates the surviving host
capture. RTP-12 instead restarts the routing execution environment:

- **OE:** Save FRR configuration, suspend `watchfrr`, kill the `eigrpd`
  process, observe neighbor expiration and route withdrawal, then resume
  `watchfrr`. Require a replacement daemon PID and apply the saved integrated
  configuration with `vtysh -b`. The container and its interfaces remain up.
- **Cisco:** Save IOS XE running configuration to startup configuration, issue
  an interactive confirmed `reload` inside the CSR virtual machine, and wait
  for the saved EIGRP configuration to return. The vrnetlab Docker container
  and its network namespace remain up. Reload confirmation is required.

Loss and recovery PCAPs come from the surviving router. A real protocol packet
must be recorded in the loss PCAP before the restart stimulus begins. `tcpdump`
continues across the router-runtime loss; after route withdrawal the loss
capture closes, and recovery capture starts before restoration begins.

Loss requires neighbor removal and withdrawal of the restarted peer's learned
internal route. The surviving router is checked against the peer address
observed before restart. In particular, Cisco IPv6 link-local discovery must
not require SSH to the IOS VM while it is reloading. Recovery uses fresh
address discovery and requires actual IPv4/IPv6 dataplane connectivity,
both-family neighbors, restored learned route and fresh bidirectional NULL
INIT/ACK/EOT evidence. Cisco reload and OE daemon process replacement are
not equivalent to Docker container restart and must not be reported as such.

`capture.py` checks PID start time and command identity before signaling a live
capture. Unexpected exits (including Linux zombies) are reported as capture
failures, with PCAP and tcpdump diagnostics retained. A genuinely mismatched
PID is never signaled. Completed capture markers are cleared even on missing
packet evidence to avoid secondary failures during runner teardown.

The scenario-defined timeout allows Cisco dataplane initialization to complete.
On connectivity timeout, `connectivity-diagnostics.log` contains Linux and
Cisco interface, routing and neighbor evidence before teardown. The first
failed case blocks the rest. No graceful restart, NSF, or protocol-correctness
claim is made from CLI output alone. This scenario still requires live Cisco
qualification on the remote netlab host.

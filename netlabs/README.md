# OpenEIGRP / Cisco EIGRP interoperability qualification

This directory owns the **live interoperability qualification** of OpenEIGRP
(OE) with Cisco EIGRP (CE). Its purpose is to establish that real routers
exchange EIGRP messages correctly and make interoperable routing decisions.
It is not an educational lab curriculum and is not a replacement for the
portable EIGRP unit and YAML UUT suites.

**Source of authority:** [RFC 7868](https://www.rfc-editor.org/rfc/rfc7868.html),
with the OpenEIGRP design contracts in [`../eigrp/specs/rtp.md`](../eigrp/specs/rtp.md),
[`../eigrp/specs/dual.md`](../eigrp/specs/dual.md), and
[`../eigrp/specs/route-selection-spec.md`](../eigrp/specs/route-selection-spec.md).
Where RFC 7868 leaves behavior unspecified or ambiguous, establish the expected
Cisco behavior from the router version under test and captured protocol
exchanges. Distinguish specification requirements, observations, and test
assumptions. Do not treat an FRR/BIRD behavior as EIGRP protocol authority.

## Repository boundary

- **`openeigrp/netlabs/` remains here:** Cisco/OE interoperability scenarios,
  topology fixtures, capture/impairment support, transport, result reporting,
  and qualification documentation.
- **The separate educational labs repository:** CCNA/CCNP/CCIE networking
  exercises, student/instructor material, persistent instructional topologies,
  and OpenEIGRP developer exercises. Its repository name and migration are
  separate decisions; this README does not claim the migration has happened.
- The two repositories must operate independently. Small duplicated netlab,
  provisioning, image, or remote-execution helpers are preferable to a shared
  infrastructure dependency at this stage. Extract shared tooling later only
  when its maintenance cost justifies it.
- The existing `netlabs/labs/` and `remote-lab.sh` are transitional material
  on the current branch. The repository split is **not** accomplished merely
  by replacing this README. Do not remove or redirect those paths as part of
  an unrelated compatibility test.

## Test philosophy

Live interoperability tests answer two different questions:

1. **RTP / wire exchange:** Did OE and CE transmit, receive, sequence,
   acknowledge, retransmit, and decode EIGRP messages correctly? For this
   plan, the RTP group also owns packet formatting, TLV compatibility, and
   neighbor startup/resynchronization, even though these are not all RTP
   reliability functions in the strict protocol sense.

2. **DUAL / route decisions:** Did OE and CE apply the Feasibility Condition,
   select successors, exchange QUERY/REPLY messages, and converge correctly
   as the network changed?

These test does not recreate every portable UUT scenario in netlab. Portable UUT remains
responsible for exhaustive FSM transitions, crafted malformed packets,
internal object ownership and retirement, memory safety, and deterministic
implementation-level edge cases. Live tests concentrate on meaningful
**cross-vendor exchanges** and their observable outcomes.

A successful ping, an installed route, an accepted configuration command, or a
neighbor entry by itself is **not** proof of the underlying RTP or DUAL
exchange. An applicable live test must collect the evidence necessary for its
specific assertion.

### Required implementation mix

`R` is the **total router count**, not the number of routers of either vendor.
These are the default, agreed compositions:

| Topology class | OE | CE | Main use |
| --- | ---: | ---: | --- |
| **2R** | 1 | 1 | Direct peer exchange, codec, basic transport, route lifecycle |
| **3R** | 1 | 2 | Shared-LAN RTP, Conditional Receive, basic diffusion |
| **4R** | 2 | 2 | Mixed-path DUAL, successor/feasible successor, nested queries |
| **5R** | 2 | 3 | Multiple outstanding replies, deferred work, asymmetric failures |
| **6R** | 3 | 3 | Concurrent DUAL calculations and mixed-vendor recovery |

A scenario may choose a chain, shared segment, triangle, diamond, branched
network, or meshed topology. The layout must be chosen to **produce the
protocol event**, not merely to include the listed number of routers.

Keep the default compositions. A documented exception (for example, a
3R **2 OE + 1 CE** variation to test a CE sender with two OE receivers) may
be added when a specific transport invariant cannot be adequately exercised
with the standard mix; it must not silently replace the standard 3R test.

### Role and direction coverage

For every applicable mechanism, exercise **OE as initiator and CE as
initiator**, and verify the other implementation's response. This includes:
UPDATE, QUERY, REPLY, ACK/retransmission, route origination/withdrawal,
multicast reliable delivery, and restart/resynchronization. For CR or
multicast behavior, vary which vendor is the sender, which neighbor lags, and
which neighbor acknowledges normally.

A mixed topology alone is not sufficient. Every qualifying scenario must
identify the **OE-to-CE boundary** traversed by the behavior being asserted.
OE-to-OE and CE-to-CE edges can supply necessary topology context, but must
not be counted as interoperability evidence by themselves.

### Address families, modes, and applicability

- Test IPv4 and IPv6 independently wherever both platforms support the
  exercised feature. Record independent results rather than passing both
  families because one worked.
- Start from named-mode EIGRP and known compatible parameters. Add classic
  mode, alternate metric modes, or other configurations only where the Cisco
  image and OpenEIGRP integration actually support them.
- **TLV1** means the project's classic-route TLV codec; **TLV2** means its
  multiprotocol/wide-metric TLV codec. Verify the negotiated/selected codec
  and actual route TLV types on the wire. Do not assume either is usable in
  every AF or configuration merely because it exists in the source.
- Exercise internal and external routes in both origin directions, including
  the relevant route type, metrics, next hop, and external provenance fields.
- Authentication is a separate capability-dependent matrix. Never declare
  interop for a key format or algorithm that cannot be configured and
  validated at runtime on both images.
- **Cold restart, graceful route resynchronization, and NSF restarting-router
  operation are distinct behaviors.** Current project capability information
  does not claim OE supports the NSF restarting-router role. Only qualify
  supported roles; mark others explicitly as not qualified.

## Coverage plan: RTP and wire exchange

The following IDs identify **test objectives**, not a promise of one separate
Python script or one netlab deployment per row. One scenario may contain
several independently reported objectives.

| ID | Test objective | Minimum topology | Required evidence / acceptance |
| --- | --- | --- | --- |
| **RTP-01** | Peer discovery and initialization | 2R | IPv4/IPv6 HELLOs, matching peer state on both ends, INIT/UPDATE/ACK and EOT where applicable; no stuck pending adjacency |
| **RTP-02** | Incompatible adjacency parameters | 2R | Controlled AS or K-value mismatch prevents adjacency; correcting it forms a healthy peer without stale transport state |
| **RTP-03** | TLV1 / TLV2 interoperability | 2R | Capability/codec choice matches the actual packets; both vendors correctly receive supported classic and multiprotocol internal/external route TLVs |
| **RTP-04** | One and multiple TLVs per packet | 2R | Packet capture proves isolated TLVs and multiple complete route TLVs carried in one EIGRP packet; routes decode correctly |
| **RTP-05** | Packetization across multiple packets | 2R | Controlled route burst or packet-size limit spans EIGRP packets without splitting a TLV, exceeding the interface limit, or losing a route |
| **RTP-06** | Unicast and multicast transmission | 2R/3R | Correct destination addressing for discovery, reliable multicast, neighbor-specific unicast, and unicast ACK behavior |
| **RTP-07** | Sequence ordering and acknowledgments | 2R | Reliable sequence/ACK numbers, ordering, duplicate protection, and outstanding ACK resolution are correct in both directions |
| **RTP-08** | Lost packet and lost ACK recovery | 2R/3R | Deterministic separate loss cases trigger eventual correct retransmission and delivery; no false ACK completion or duplicate route application |
| **RTP-09** | Multicast selective retransmission | 3R | With one ACK delayed, only the delinquent neighbor receives a unicast retransmission; an acknowledged neighbor is not retransmitted to unnecessarily |
| **RTP-10** | Conditional Receive (CR) | 3R | Induced outstanding reliable work produces the applicable Sequence/Multicast Sequence TLVs and CR signaling; eligible receivers progress and excluded receiver catches up |
| **RTP-11** | Pacing and backpressure | 2R/3R | Burst traffic drains without loss, false timeout, stalled unrelated neighbor/interface, or persistent outstanding packet state |
| **RTP-12** | Peer loss, hold expiry, and cold restart | 2R | Both peers remove/recreate the adjacency and outstanding transport state; route withdrawal and fresh startup complete correctly |
| **RTP-13** | Supported graceful resynchronization | 2R | Packet evidence of the relevant restart/resync flags and EOT; refresh and stale-route handling match the documented OE/CE roles, not merely eventual adjacency recovery |
| **RTP-14** | Retry exhaustion and cleanup | 2R/3R | Sustained controlled loss exhausts the documented peer retry behavior, tears down only the appropriate state, and permits recovery |

RTP-specific assertions must account for protocol-allowed variations in
packet timing and aggregation. Do not require Cisco and OE to emit identical
numbers of packets or route TLVs where RFC 7868 allows different batching.

### Critical RTP experiments

**Selective multicast retry (RTP-09):** Put one OE and two CE routers on a
shared segment. Cause an OE reliable multicast and allow one CE to ACK while
withholding the other CE's ACK. Verify an original multicast transmission,
then **unicast** retransmission of the same reliable sequence only to the
unacknowledged CE. Repeat with CE as sender to OE and CE receivers. Capture
headers and track recipient-specific outcomes, not only final routing tables.

**Conditional Receive (RTP-10):** On the same shared segment, deliberately
create one lagging receiver while new multicast reliable work is sent. Verify
the relevant Sequence TLV, multicast-sequence/CR behavior, exclusion, progress
of the unaffected receiver, and later recovery of the lagging receiver.
A successful retry alone does not demonstrate CR.

**Packetization (RTP-04/05):** Originate known prefixes in controlled batches.
Force a single route TLV, several TLVs in one EIGRP packet, and enough routes
to require multiple EIGRP packets. Verify exact advertised prefixes and
successful installation, plus complete TLV boundaries and packet-size limits.
Do not rely on IP fragmentation as a substitute for EIGRP packetization.

**Restart (RTP-12/13):** Test OE restart and CE restart separately. Verify
cold-restart cleanup independently of graceful resynchronization; do not
mistake recovery after a full adjacency flap for graceful restart. The OE
NSF restarting-router role remains outside qualification unless implemented
and separately approved.

## Coverage plan: DUAL and topology

For each DUAL objective, validate relevant packet exchange **and** the
resulting topology/route state. Use implementation diagnostics where available,
but never infer a private descriptor was freed from filtered CLI output.

| ID | Test objective | Minimum topology | Required evidence / acceptance |
| --- | --- | --- | --- |
| **DUAL-01** | Bidirectional internal routes | 2R | Each router originates an internal prefix; the other learns the correct EIGRP-internal type, metric, next hop, and route |
| **DUAL-02** | Bidirectional external routes | 2R | Controlled redistribution on each vendor; correct EIGRP-external type and origin/protocol/tag/metric fields survive cross-vendor exchange |
| **DUAL-03** | Route lifecycle | 2R | Announce, change metric, withdraw, and re-advertise routes; topology and RIB converge and remove stale paths in both directions |
| **DUAL-04** | Metric interpretation | 2R/3R | Correct RD/CD/FD and classic/wide metric behavior for deliberately unequal paths; distinguish differences due to configured interface metrics |
| **DUAL-05** | Successor and feasible-successor choice | 4R diamond | Choose the least-cost valid successor, retain the alternate, and verify strict feasibility `RD < FD` |
| **DUAL-06** | Feasible-successor failover | 4R diamond | Losing the successor promotes a valid feasible successor without unnecessary diffusion, with correct installed route |
| **DUAL-07** | No feasible successor / diffusing computation | 4R diamond | Infeasible alternate is not prematurely installed; ACTIVE, QUERY/REPLY, and eventual PASSIVE completion are evidenced |
| **DUAL-08** | Recursive QUERY and deferred REPLY | 4R chain | An OE or CE queried through another router waits for necessary downstream replies before answering upstream |
| **DUAL-09** | Multiple outstanding replies | 5R branched | Route stays ACTIVE while any required neighbor response remains outstanding; correct last-reply completion |
| **DUAL-10** | ACTIVE-state consistency | 4R/5R | Relevant UPDATEs, cost changes, or duplicate/late replies during ACTIVE do not prematurely commit selected successor/FD |
| **DUAL-11** | Split horizon and poison reverse | 3R/4R | Per-interface QUERY/UPDATE suppression or poisoning matches the actual successor path and RFC rules |
| **DUAL-12** | Internal/external preference and multipath | 4R | Internal preferred to external; equal-cost and feasible unequal-cost paths obey supported maximum-paths/variance behavior |
| **DUAL-13** | Neighbor loss during ACTIVE | 5R | Outstanding reply accounting resolves failed peer, computation completes safely, and stale paths do not survive |
| **DUAL-14** | SIA progress and timeout | 4R/5R | Induced long-running computation exercises SIA-QUERY/SIA-REPLY, continued progress, or required timeout handling |
| **DUAL-15** | Concurrent distributed changes | 6R | Overlapping computations, mixed-vendor path changes, and recoveries converge without persistent ACTIVE routes or routing loops |

### Critical DUAL experiments

**Feasible successor (DUAL-05/06):** Build a four-router diamond containing
two OE and two CE. Choose metric values so an alternate path satisfies
`neighbor RD < destination FD`. Withdraw the current successor path and
verify the permitted immediate successor change. Vary which vendor owns the
DUAL decision and which originates the destination.

**Feasibility failure (DUAL-07):** Use the same diamond with an alternate
that fails the Feasibility Condition. Trigger successor loss. The router
must not promote that alternate before required diffusion; prove the
ACTIVE/QUERY/REPLY/PASSIVE sequence and route selection.

**Recursive diffusion (DUAL-08):** A four-router chain is sufficient to force
a router receiving an upstream QUERY to depend on a downstream response.
Verify that the upstream REPLY is deferred until it is valid, then reverse
the initiating vendor. Use five routers for multiple outstanding replies
(DUAL-09/13), not simply to duplicate the chain.

**Distributed load (DUAL-15):** The six-router mix is reserved for cases
requiring several OE and CE participants simultaneously, such as overlapping
queries and independent failures. It is not a requirement to rerun every 2R
or 4R assertion on six routers.

## Evidence and PASS/FAIL contract

Evidence is collected at three levels:

| Level | Examples | Purpose |
| --- | --- | --- |
| **Wire** | Time-stamped packet capture, protocol-88 header, opcode, flags, sequence/ACK, source/destination, TLV types and contents | Prove the message was actually exchanged |
| **Control plane** | Both neighbor tables; EIGRP topology/diagnostic output; available state-transition and reliable-queue evidence | Prove the resulting RTP/DUAL state and responsibility |
| **Routing** | Both routing tables, exact prefixes/types/next hops/metrics; optional forwarding probes | Prove the decision reached the routing plane |

Each runnable scenario must:

1. Define a controlled initial topology, addresses, router versions, EIGRP
   configuration, route origins, and expected OE/CE roles. Netlab owns initial
   provisioning; scenario actions may introduce a documented test stimulus.
2. Validate both ends are ready before mutation. Establish a known baseline
   and verify prerequisites; an absent prerequisite is not a test PASS.
3. Capture the relevant packets **before** initiating the exchange. Record
   the interfaces/directions captured. Capture tooling and packet-loss
   injection need implementation and must be tested before depending on them.
4. Drive the chosen event deterministically (route change, interface failure,
   lost packet, delayed ACK, restart), with a bounded deadline and explicit
   recovery/cleanup behavior.
5. Assert expected messages and state independently for each applicable
   address family, direction, and test objective. Verify negative conditions
   when material: no unexpected adjacency, wrong route type, premature REPLY,
   incorrect multicast retry, persistent ACTIVE state, or stale route.
6. On failure, save enough evidence to distinguish a Cisco configuration
   problem, unsupported feature, capture gap, transport defect, DUAL defect,
   host-RIB defect, and lab teardown failure. Never turn missing evidence
   into PASS.
7. Exit nonzero on scenario failure. Leave teardown to the shared runner;
   restore transient impairments when necessary to allow teardown, and retain
   the remote session if teardown fails.

**Result vocabulary:** `PASS` (executed, assertions satisfied), `FAIL`
(executed, assertions failed), `NOT_RUN` (not attempted), `BLOCKED`
(prerequisite/capability/tool missing), and `NOT_IMPLEMENTED` (planned objective
without a runnable test). `BLOCKED` and `NOT_IMPLEMENTED` never count as a
successful compatibility qualification. Scenario-level success requires all
**required** objectives to pass; explicit out-of-scope objectives are tracked
separately. Mocked driver tests are not live Cisco interoperability runs.

For every result retain, as applicable: case ID, topology/mix, OE image tag
**and digest**, Cisco image/version, timestamp, AF, initiator/receiver role,
stimulus, expected and observed messages, packet captures or decoded events,
neighbor/topology/RIB snapshots, final state, error diagnostics, and cleanup
result. Existing report names and output locations remain valid; future
scenarios may add evidence files without silently changing the runner contract.

## Scenario implementation conventions

Live tests live under `netlabs/uut/`, separately from the portable
`eigrp/test/uut/` harness:

```text
netlabs/
  README.md                      # This qualification plan and operations guide
  config/eigrp-named.j2           # Existing shared named-mode configuration
  tools/remote-uut.sh             # Client: stage, execute, retrieve report
  tools/remote/{common.sh,runner.sh}
  tools/remote/images.py          # Select installed OpenEIGRP release image
  uut/
    2R-neighbor.py               # Existing scenario driver
    2R-neighbor/
      scenario.yaml
      topology.yml
      README.md
    tests/                       # Mocked/unit driver regression tests
  tests/                          # Transport/remote-runner regression tests
  results/                        # Generated; excluded from Git
```

New runnable scenarios follow the established convention:

- `netlabs/uut/<count>R-<purpose>.py`, with a matching
  `<count>R-<purpose>/{scenario.yaml,topology.yml,README.md}`.
- The driver consumes `--scenario`, `--topology`, and `--artifacts` and returns
  nonzero when the scenario fails.
- Reuse the tested image selection, remote staging, reporting, and teardown
  facilities rather than creating one-off shell lifecycles. Shared helpers
  should be extracted only when at least two scenarios need them.
- Before adding 3R–6R scenarios, extend the current remote runner
  resource/orphan detection beyond its hard-coded `eigrp-neighbor` and
  `eigrp-internal` container names; verify that failed deployments and
  teardown cannot strand an unrecognized topology. Update staging explicitly
  when a new scenario requires shared driver/helper files.
- Include executable regression tests under `netlabs/uut/tests/` or
  `netlabs/tests/` covering the actual validator/driver logic, plus the
  scenario's live topology and assertions. These do **not** replace the
  portable YAML UUT obligation for any separately discovered protocol bug fix.
- Keep each scenario narrowly scoped and independently runnable. A coverage
  objective may be a subcase of a scenario; **objective counts, executable
  script counts, and live scenario counts are different quantities**.
- Do not configure initial EIGRP or addresses in the validator after netlab
  has supposedly configured the topology. Explicit runtime fault stimuli
  and their restoration are allowed when they are the subject of the test.

### Existing implementation status on the `netlabs` baseline

| Item | State | Meaning |
| --- | --- | --- |
| `2R-neighbor` | **Driver and scenario fixture present** | Checks bidirectional IPv4/IPv6 neighbor tables; does not yet qualify full RTP initialization, routes, or packet exchanges |
| `2R-internal.py` | **Incomplete scenario** | Driver exists, but a matching `2R-internal/scenario.yaml` and `topology.yml` are absent; not discoverable/runnable through the current live runner |
| RTP-02 through RTP-14 | **Planned** | No completed live test is asserted by this plan |
| DUAL-02 through DUAL-15 | **Planned** | No completed live test is asserted by this plan |
| Packet capture and deterministic impairment | **Needed infrastructure** | Packet evidence and controlled loss/delay must be implemented and verified before tests that depend on them |

This is a **repository-content inventory**, not a claim that any scenario has
passed against StormTrooper or the selected Cisco and OE images. Track actual
execution results by scenario, tested image/digest, and timestamp.

## Implementation order and qualification gates

Build one test at a time. Each addition should be reviewable, independently
runnable, and leave existing passing scenarios intact.

| Stage | Deliverable | Purpose |
| --- | --- | --- |
| **0: Evidence foundation** | Packet capture/decoder, artifact contract, deterministic impairment helper, and driver regression coverage | Ensure RTP assertions can prove wire behavior rather than infer it |
| **1: 2R startup/route baseline** | Complete `2R-internal`; extend neighbor evidence for RTP-01; add internal/external and withdrawal cases | Establish trusted OE↔CE exchange in both AFs |
| **2: 2R RTP encoding/transport** | TLV1/TLV2, single/multiple TLVs, packet boundaries, sequence/ACK, cold restart | Qualify the common message exchange |
| **3: 3R shared-LAN RTP** | Multicast, selective retransmission, CR, lagging neighbor and recovery | Qualify per-neighbor reliable delivery |
| **4: 3R/4R DUAL** | Split horizon, metric/route selection, feasible successor, QUERY/REPLY, deferred REPLY | Qualify fundamental distributed DUAL behavior |
| **5: 5R/6R DUAL and exceptional RTP** | Multiple outstanding replies, loss during ACTIVE, SIA, concurrent diffusion, retry exhaustion, supported graceful resync | Qualify interactions that require larger topologies |

The **smoke set** should eventually cover 2R adjacency and basic internal
routing. The **core interop set** should cover 2R–4R RTP and DUAL. The
**extended set** should contain 5R/6R and disruptive/long-running tests.
These are proposed qualification levels, not a claim that CI runs them today.

Before a case is marked complete: verify both initiator directions as
applicable, execute its mock/unit driver regressions, execute it live on the
recorded Cisco/OE images, check negative/recovery behavior and cleanup, and
run the previously implemented relevant interoperability scenarios. A new
PASS alone does not establish regression safety.

## Running existing interoperability tests

### Execution environment

The current remote execution target is the Linux netlab/Containerlab host
(StormTrooper), with Docker, Ansible, KVM acceleration for Cisco virtual
routers, `sshpass` for Cisco CLI access, and the dedicated `netlab` account.
The provisioner maintains `/home/netlab/netlab-venv/`,
`/home/netlab-data/runs/`, and `/home/netlab-data/results/`. Verify that the
`netlab` account can execute `netlab test clab` and access required images.
Cisco CSR1000v may take several minutes to finish booting.

The current example router images are
`ghcr.io/diivious/openeigrp-frr:0.3.0` and
`vrnetlab/vr-csr:17.03.06`. The runner selects the highest **locally
installed versioned** OpenEIGRP image for a run and records image selection
evidence; it does not automatically fetch the latest image on each test.
Use `--update` separately when a release-image refresh is intended. Always
record the actual image and digest, not simply the version originally written
in a topology fixture.

The macOS/Linux client requires Bash, SSH, and `tar`. It does not require local
Docker, netlab, or KVM. Prefer SSH keys. Point `REMOTE_LAB_CONFIG` at an
**untracked** local configuration file if credentials are needed; do not commit
real passwords. For example:

```sh
export REMOTE_LAB_CONFIG="$HOME/.config/openeigrp/remote-lab.cfg"
# The file defines REMOTE_HOST, REMOTE_USER, REMOTE_SSH_PORT,
# and optionally REMOTE_PASSWORD for sshpass fallback.
```

The config is sourced as trusted Bash; use only a file under your control.
`localhost` refers to the machine executing the client script, not the remote
Linux host when a client is running on macOS.

### Commands

From the repository root:

```sh
netlabs/tools/remote-uut.sh --list
netlabs/tools/remote-uut.sh --scenario 2R-neighbor
netlabs/tools/remote-uut.sh --update    # Explicitly refresh the OE image

python3 -m unittest discover -s netlabs/uut/tests -p 'test_*.py'
netlabs/tests/test_remote_runtime.sh
```

Only one remote lab/UUT session occupies the current host at a time. The
shared runner stages the selected scenario, runs `netlab up`, executes the
driver, collects `report.json` and CLI/router logs, invokes `netlab down`,
and retrieves an evidence bundle under `netlabs/results/uut-.../`. It records
cleanup separately. A cleanup failure retains the remote session for
recovery and must never be reported as a passing run.

The current `2R-neighbor` driver checks both routers' neighbor tables for
IPv4 and IPv6, using observed IPv6 link-local peer addresses. It does not
currently validate packet captures, route propagation, DUAL transitions,
or descriptor ownership. Future tests must not expand its stated PASS scope
without implementing the corresponding assertions.

## References

- [RFC 7868: Cisco's EIGRP](https://www.rfc-editor.org/rfc/rfc7868.html):
  §3.5 DUAL, §5.2 RTP, §5.3 discovery/initialization, §5.4 topology and
  split horizon, §5.6 metrics, §6 packet formats and TLVs.
- [`eigrp/specs/rtp.md`](../eigrp/specs/rtp.md): packetization,
  acknowledgment, reliable transport, retry/CR, and lifecycle invariants.
- [`eigrp/specs/dual.md`](../eigrp/specs/dual.md): feasibility,
  ACTIVE/PASSIVE, QUERY/REPLY, state freeze, and SIA invariants.
- [`eigrp/specs/route-selection-spec.md`](../eigrp/specs/route-selection-spec.md):
  internal/external preference, successor/feasible successor, variance,
  and maximum-paths behavior.
- [`netlabs/uut/README.md`](uut/README.md): existing live-scenario naming
  and execution contract.

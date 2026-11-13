# OpenEIGRP

[![OpenEIGRP Core CI](https://github.com/diivious/openeigrp/actions/workflows/core.yml/badge.svg?branch=master)](https://github.com/diivious/openeigrp/actions/workflows/core.yml?query=branch%3Amaster)

OpenEIGRP is a portable implementation of Cisco's Enhanced Interior Gateway Routing Protocol (EIGRP). OpenEIGRP is the project name; EIGRP remains the protocol name used by the wire protocol, CLI, data structures, and source APIs.

For interoperability, verified Cisco packets and decodes are authoritative for on-wire formats; observed Cisco behavior informs protocol semantics. [RFC 7868](https://www.rfc-editor.org/rfc/rfc7868.html) is the reference where consistent with that evidence. Host integrations implement operating-system and routing-stack services, not protocol decisions. See the [Cisco/RFC errata and evidence policy](eigrp/specs/cisco-errata.md).

## Try it

To explore the code, no routing stack checkout, root privileges, or network lab is required:

```sh
git clone https://github.com/diivious/openeigrp.git
cd openeigrp
make                         # compile and run style checks
make uut SUITE=ipv4-basic-core  # run a small portable EIGRP scenario suite
```

On Linux, `make linux` also builds the native `openeigrpd` and `oectl` tools; starting a real routing daemon requires additional permissions and configuration. See the [quickstart](specs/quickstart.md) for prerequisites and your first contribution. The default `make` compiles host-compatible code and checks style; `make all` adds unit/UUT coverage and both-architecture Docker and Debian builds.

## Architecture and code overview

```text
+-----------------------------------------------------------+
| Host routing stack / operating environment                |
| Configuration, RIB, interfaces, sockets, timers, logging  |
+----------------------------+------------------------------+
                             |
+----------------------------v------------------------------+
| Host integration / adapter                                |
| Normalization, management, and host-service implementation|
+----------------------------+------------------------------+
                             |
+----------------------------v------------------------------+
| Portable EIGRP protocol core                              |
| DUAL, topology, metrics, neighbors, RTP, packetizer, TLVs  |
+-----------------------------------------------------------+
```

**Architectural rule:** equivalent normalized inputs must yield the same EIGRP protocol decisions regardless of host. The portable core owns protocol behavior; adapters implement operating-system and routing-stack services through EIGRP-owned interfaces.
Portable APIs use EIGRP-owned types and structured results. Host-specific objects remain inside their integrations. The public integration contracts include `eigrp.h`, `eigrp_cli.h`, `eigrp_mgnt.h`, `eigrp_rib.h`, and `eigrp_sys.h` under `eigrp/code/`. See [architecture](specs/architecture.md), [platform integration](specs/platform-integration.md), and [public API](specs/public-api.md).

![Portable core and host-integration architecture](specs/images/project-architecture.svg)

![Public API boundaries and call directions](specs/images/public-api-directions.svg)

## Start here by role

| Role | Read first | Then |
|---|---|---|
| Curious developer | [Quickstart](specs/quickstart.md) | [Developer guide](specs/developer-guide.md) |
| Contributor | [Contributing](CONTRIBUTING.md) | [Style guide](specs/code-conventions.md), [testing guide](specs/testing.md) |
| Operator / CLI user | [Integration compatibility](specs/compatibility.md) | [Linux](linux/README.md), [FRR](frr/README.md), or [Docker](docker/README.md) |
| Platform integrator | [Integration contract](specs/platform-integration.md) | [Public API](specs/public-api.md) and the integration's own README |
| Portable protocol developer | [Core design](eigrp/specs/README.md) | [DUAL](eigrp/specs/dual.md), [route selection](eigrp/specs/route-selection.md), [RTP](eigrp/specs/rtp.md) |
| Tester | [Testing guide](specs/testing.md) | [Portable UUT](eigrp/test/uut/README.md), [Netlab interoperability](netlabs/README.md) |

## Quick Start

<details open>
<summary><strong>Using Docker Images</strong></summary>

Pull a **published** multi-platform release from GHCR (example version `0.2.0`; availability depends on publication). Git tag `rel-0.2.0` is the publishing trigger; the Docker image tag is `0.2.0`.

```sh
docker pull ghcr.io/diivious/openeigrp-frr:0.2.0
docker volume create openeigrp-vol
docker run -d --name openeigrp \
  --cap-add NET_ADMIN --cap-add NET_RAW --cap-add SYS_ADMIN \
  -v openeigrp-vol:/etc/frr \
  ghcr.io/diivious/openeigrp-frr:0.2.0

docker exec -it openeigrp vtysh
```

The Docker package uses Alpine Linux and FRR with OpenEIGRP. For local/GitHub builds, named-mode configuration, end-to-end verification, deployment, and release publishing, see **[Docker Quick Start and Release Guide](docker/README.md)**. Publishing tags are independent of beta, gamma, and production maturity. Direct EIGRP adjacency with a physical LAN requires an appropriate Linux network setup; Docker Desktop's default macOS bridge does not provide it.

</details>

<details>
<summary><strong>Build and Test local</strong></summary>

### Compile and check

From the repository root:

```sh
make
```

The default target compiles the portable EIGRP core and Unix support (and the native integration on Linux) and runs full-tree style validation. It needs no Docker, FRR checkout, VM, or root privileges.

### Build, test and pre-merge qualification

From the repository root:

```sh
make                       # Host compilation and full-tree style checks
make all                   # Fast tests, 107 YAML UUTs, quick scale, both image/package builds
make pre-commit            # Full scale, Docker integration and dpkg lifecycle UUTs

make uut                    # All portable pytest and YAML UUT scenarios
make uut SUITE=rtp-core     # Named scenario group
make uut SCENARIO=prefix-limit-lifecycle/peer-timers-ipv6.yaml

make scale                  # Quick pacing/backpressure qualification
make scale SUITE=100k       # Heavy scale qualification when needed

```

**Run `make pre-commit` before opening a pull request for merge.** `make` compiles
host-compatible sources and checks style without Docker. `make all` adds fast
unit tests, all portable YAML UUT scenarios, quick scale regressions and builds
FRR Docker images and Debian packages for amd64 and arm64. `make pre-commit`
adds full scale/stress suites, Docker image/adjacency integration UUTs and dpkg
package-lifecycle UUTs. Docker is required for the last two tiers; an
unavailable engine fails rather than silently skipping a stage. Direct FRR
host builds and remote Netlab runs remain separate.

See the root `Makefile` for targets and selectors. GitHub Actions runs
build, style and portable UUT gates on Linux and macOS; the local `make all`
also performs scale and platform-specific qualification.

For Docker development:

```sh
make docker                 # Local OpenEIGRP and FRR checkouts
make docker FRR=../frr      # Choose a different FRR checkout
make docker GITHUB=latest   # Latest OpenEIGRP release tag
make docker GITHUB=1.2.3   # Specific rel-1.2.3 plus its FRR pin
```

See [Docker development](docker/README.md) for source selection and advanced
`tools/docker.sh` options.

</details>

## Project State

### Cisco EIGRP / OpenEIGRP capability matrix

This is the project-wide Cisco EIGRP / OpenEIGRP feature assessment, updated for the `linux-support` branch. **Yes** indicates an implemented capability, **Partial** indicates incomplete coverage or a host-dependent subset, and **No** means it is not currently implemented. It is **not** a claim that every integration exposes every capability, or that live Cisco interoperability and production deployment have been certified. In particular, native Linux supports a deliberately smaller configuration and redistribution surface; see the [integration matrix](specs/compatibility.md) and the Linux limitations below.

<details>
<summary><strong>Address Families</strong></summary>

| Feature | Cisco EIGRP | OpenEIGRP | Notes |
|:--|:--:|:--:|:--|
| IPv4 | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| IPv6 | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| IPv4/IPv6 named mode | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Multiple autonomous systems | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Multiple named instances | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| VRF / VRF-Lite | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #1769c2; font-weight: 700;">Partial</span> | Native Linux configuration currently accepts only the default VRF |

</details>

<details>
<summary><strong>Core Protocol</strong></summary>

| Feature | Cisco EIGRP | OpenEIGRP | Notes |
|:--|:--:|:--:|:--|
| DUAL FSM | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Feasibility Condition | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Feasible Successors | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Active/Passive states | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Query / Reply | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| SIA-Query / SIA-Reply | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Reliable Transport Protocol | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Reliable multicast | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Reliable unicast | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Conditional Receive (CR) | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Neighbor discovery / adjacency | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Neighbor resynchronization | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Graceful Restart (GR) | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| NSF awareness | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| NSF restarting router | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #c62828; font-weight: 700;">No</span> |  |
| End-of-Table (EOT) signaling | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |

</details>

<details>
<summary><strong>Metric and Path Selection</strong></summary>

| Feature | Cisco EIGRP | OpenEIGRP | Notes |
|:--|:--:|:--:|:--|
| Classic composite metrics | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Wide metrics | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Configurable K-values | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Bandwidth / Delay metrics | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Reliability / Load metrics | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| MTU advertisement | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Unequal-cost load balancing | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Variance | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Maximum paths | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Maximum hops | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Administrative distance | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Loop-Free Alternate (LFA FRR) | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #c62828; font-weight: 700;">No</span> | Planned |
| Add-Path | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #c62828; font-weight: 700;">No</span> | Not implemented; under consideration |

</details>

<details>
<summary><strong>Route Management</strong></summary>

| Feature | Cisco EIGRP | OpenEIGRP | Notes |
|:--|:--:|:--:|:--|
| Internal routes | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| External routes | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Route redistribution | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Redistribute connected | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Redistribute static | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Redistribute OSPF | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> | Host-dependent; native Linux currently supports only connected and static redistribution |
| Redistribute BGP | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> | Host-dependent; native Linux currently supports only connected and static redistribution |
| Redistribute RIP / IS-IS | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> | Host-dependent; native Linux currently supports only connected and static redistribution |
| Redistribution route maps | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Default metric | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| External route tagging | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Route tag filtering | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #1769c2; font-weight: 700;">Partial</span> | Native Linux has route-map `match tag`, but no route-map `set` actions; other host capabilities vary |
| Default route advertisement | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |

</details>

<details>
<summary><strong>Summarization</strong></summary>

| Feature | Cisco EIGRP | OpenEIGRP | Notes |
|:--|:--:|:--:|:--|
| Manual summarization | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Automatic summarization (IPv4) | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Summary metric configuration | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Summary leak maps | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Summary discard / Null0 route | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #c62828; font-weight: 700;">No</span> | The summary implementation does not install a  local discard route,  |
| Summary administrative distance | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #1769c2; font-weight: 700;">Partial</span> | Needs the Summary discard route support |

</details>

<details>
<summary><strong>Policy and Filtering</strong></summary>

| Feature | Cisco EIGRP | OpenEIGRP | Notes |
|:--|:--:|:--:|:--|
| Distribute lists | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Prefix lists | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Access lists | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Route maps | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> | Native Linux offers restricted first-match policy; route-map `set` actions are unsupported |
| Offset lists | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Per-interface filtering | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Inbound / outbound filtering | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |

</details>

<details>
<summary><strong>Interface and Neighbor Controls</strong></summary>

| Feature | Cisco EIGRP | OpenEIGRP | Notes |
|:--|:--:|:--:|:--|
| Passive interfaces | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Static neighbors | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Hello interval | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Hold interval | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Split horizon | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Next-hop-self | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Bandwidth percentage | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Interface delay configuration | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Neighbor descriptions | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Neighbor prefix limits | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #1769c2; font-weight: 700;">Partial</span> | EIGRP MPLS VPN PE-CE features |
| Topology prefix limits | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #1769c2; font-weight: 700;">Partial</span> | EIGRP MPLS VPN PE-CE features |
| Active timer | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Path Graph Feasibility | <span style="color: #c62828; font-weight: 700;">No</span> | <span style="color: #c62828; font-weight: 700;">No</span> | Planned |
| Peer Groups | <span style="color: #c62828; font-weight: 700;">No</span> | <span style="color: #c62828; font-weight: 700;">No</span> | Planned |

</details>

<details>
<summary><strong>Security</strong></summary>

| Feature | Cisco EIGRP | OpenEIGRP | Notes |
|:--|:--:|:--:|:--|
| MD5 authentication | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| HMAC-SHA-256 authentication | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #1769c2; font-weight: 700;">Partial</span> | Partial core support; native Linux configuration rejects HMAC-SHA-256 and encoded type-7 keys |
| Key chains | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Key rollover | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #1769c2; font-weight: 700;">Partial</span> | Runtime selection not fully established |

</details>

<details>
<summary><strong>Fast Convergence</strong></summary>

| Feature | Cisco EIGRP | OpenEIGRP | Notes |
|:--|:--:|:--:|:--|
| BFD integration (IPv4) | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #c62828; font-weight: 700;">No</span> | [BFD Design](specs/feature-bfd.md) |
| BFD integration (IPv6) | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #c62828; font-weight: 700;">No</span> | [BFD Design](specs/feature-bfd.md) |
| SIA detection | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Graceful neighbor recovery | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Nonstop Forwarding (NSF) | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #1769c2; font-weight: 700;">Partial</span> | Requires host support |

</details>

<details>
<summary><strong>Advanced / Specialized</strong></summary>

| Feature | Cisco EIGRP | OpenEIGRP | Notes |
|:--|:--:|:--:|:--|
| EIGRP Stub | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #c62828; font-weight: 700;">No</span> | No plans |
| EIGRP Over the Top (OTP) | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #c62828; font-weight: 700;">No</span> | [OTP Design](specs/feature-otp.md) |
| EIGRP MANET | Legacy/specialized | <span style="color: #c62828; font-weight: 700;">No</span> | No plans |
| Multi-Topology Routing (MTR) | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #c62828; font-weight: 700;">No</span> | No plans |
| DMVPN integration | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #1769c2; font-weight: 700;">Partial</span> | OpenEIGRP can support DMVPN, but the host must provide the underlying tunnel and NHRP functionality |
| EIGRP MPLS VPN PE-CE | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #c62828; font-weight: 700;">No</span> | No plans |
| EIGRP 6PE / 6VPE | Platform-dependent | <span style="color: #c62828; font-weight: 700;">No</span> | No plans |
| EIGRP Site of Origin (SoO) | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #c62828; font-weight: 700;">No</span> | EIGRP MPLS VPN PE-CE features |

</details>

<details>
<summary><strong>Configuration and Operations</strong></summary>

| Feature | Cisco EIGRP | OpenEIGRP | Notes |
|:--|:--:|:--:|:--|
| Classic IPv4 configuration | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Named-mode configuration | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| IPv6 named-mode configuration | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Configuration writeback | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> | Integration-specific; native Linux loads configuration on startup and has no interactive writeback |
| Show interfaces | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Show neighbors | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Show topology | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Show protocol statistics | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Show traffic counters | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Debug packet / neighbor / DUAL | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> | minimal logs |
| Event logging | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| DUAL state transition logging | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| Clear neighbors | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> | Portable capability; not supported as an `oectl` command on native Linux |
| Clear topology | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> | Portable capability; not supported as an `oectl` command on native Linux |
| Clear event logs | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #16803c; font-weight: 700;">Yes</span> |  |
| SNMP EIGRP MIB | <span style="color: #16803c; font-weight: 700;">Yes</span> | <span style="color: #1769c2; font-weight: 700;">Partial</span> | Conditional on host SNMP |

</details>

### Implementation and integration status

The portable core implements EIGRP protocol state and decisions, including DUAL, topology and metric selection, neighbor management, reliable transport, packet encoding, and IPv4/IPv6 addressing. Classic IPv4 and named-mode configuration use portable feature targets. EIGRP Stub runtime behavior is intentionally outside project scope.

The portable core provides protocol decisions independently of its host. Native Linux and FRR have real integration code. Shared Unix services and the virtual-wire UUT are a development/test environment, **not** a production host. The BIRD integration is deferred; OpenWrt is planned, not available.

| Integration | Current availability | What you can do today |
|---|---|---|
| [Native Linux](linux/README.md) | Daemon, control client, raw IPv4/IPv6 packet transports, Netlink RIB, `.deb` packaging implemented | Run `openeigrpd` with named-mode default-VRF configuration; verify target-system privileges, systemd lifecycle, and live interoperability separately |
| [FRR](frr/README.md) | Integration code and FRR-based Docker workflow present | Build against the selected FRR checkout and use its integration-specific CLI and management surface |
| [Unix UUT](libunix/README.md) | Test-only integration | Develop and run deterministic protocol scenarios without live network privileges |
| [BIRD](bird/README.md) | Deferred | No deployable adapter |
| OpenWrt | Planned | No deployable integration on this branch |

For per-host feature constraints, see [Integration compatibility](specs/compatibility.md).

### Qualification and deployment readiness

The capability matrix describes implementation scope, not a platform-wide production qualification result. The repository provides automated portable and host-specific test paths, but deployment evaluation must distinguish protocol feature coverage, regression test results, interoperability with other EIGRP implementations, and qualification of each host adapter on the target appliance. The build and test section below identifies available test entry points; no blanket production certification is asserted.

## Repository layout

```text
eigrp/
  eigrp/
    code/          Portable EIGRP implementation
    specs/         Portable protocol/module specifications
    test/          Portable protocol and UUT tests
  frr/
    code/          FRR adapters and integration
    specs/         FRR-specific integration specifications
    test/          FRR-native integration/UUT material
  bird/
    code/          BIRD/BSD adapter area
    specs/         BIRD/BSD integration specifications
    test/          BIRD/BSD-native test area
  libunix/
    code/          Shared Unix services and test-only UUT host
    specs/         Unix-host integration specification
    test/          Unix-host tests
  linux/
    code/          Native Linux daemon, CLI, and host services
    man/           Installed daemon, configuration, and CLI manuals
    packaging/     Debian and systemd packaging
    specs/         Linux integration design
    test/          Native Linux tests
  netlabs/         Cisco interoperability and packet evidence
  specs/           Repository-wide developer and integration contracts
  docker/          Alpine FRR/OpenEIGRP image, container startup, and deployment guide
  tools/           Build, staging, UUT, and packaging helpers
  CONTRIBUTING.md Contribution, style, AI, and test rules
```

### Source areas

| Source area | Responsibility |
|:--|:--|
| [`eigrp/code/`](eigrp/code/) | Portable protocol implementation and public EIGRP APIs |
| [`eigrp/specs/`](eigrp/specs/) | DUAL, route selection, RTP, and other protocol-module designs |
| [`frr/`](frr/README.md) | FRR routing-stack adapter and integration |
| [`libunix/`](libunix/README.md) | Shared Unix services and portable UUT qualification |
| [`linux/`](linux/README.md) | Native Linux daemon, control application, and Debian packages |
| [`bird/`](bird/README.md) | Deferred BIRD integration area |
| [`netlabs/`](netlabs/README.md) | Cisco interoperability tests and captured packet evidence |
| [`specs/`](specs/) | Cross-platform architecture, integration, and configuration contracts |
| [`tools/`](tools/README.md) | Build, staging, and test orchestration |
| [`docker/`](docker/README.md) | Multi-architecture container build, testing, deployment, and GHCR release publication |

The repository tree is the source of truth. Platform staging projects portable code and platform adapters into the host build tree; staged copies are not development sources.

## Protocol behavior and feature ownership

Every configuration or operational feature terminates at the module that owns the behavior. CLI and YANG layers parse and normalize values; they do not implement protocol semantics.

Typical ownership is:

```text
neighbor lifecycle/authentication  -> neighbor/authentication modules
DUAL state and ACTIVE processing   -> FSM/DUAL modules
metric/variance/weights            -> metric module
route selection                    -> topology/route-selection modules
packetization/RTP                  -> packetizer/packet modules
summaries                          -> summary module
filtering/offset lists             -> filter module
redistribution                     -> redistribution module
event history                      -> event-log module
host RIB operations                -> eigrp_rib.h boundary
```

Portable code uses structured EIGRP result codes. Where a feature remains incomplete, its real feature target retains configuration and reports `EIGRP_RESULT_NOT_IMPLEMENTED` rather than simulating support in a CLI or adapter.

Human-navigation naming follows:

```text
eigrp_<module>.c
eigrp_<module>_<object>_<action>()
```

See `specs/code-conventions.md` before adding or renaming public symbols.

## Specifications

Repository-wide contracts live under `specs/`; portable protocol internals live under `eigrp/specs/`.

- `specs/quickstart.md` — clone, build, and run a first test.
- `specs/developer-guide.md` — deeper build, debugging, and development workflow.
- `specs/testing.md` — portable UUT, Netlab, and integration test workflow.
- `specs/architecture.md` — ownership and portability boundaries.
- `specs/code-conventions.md` — naming and source conventions, with style-check/style-fix.
- `specs/platform-integration.md` — host integration contract.
- `specs/public-api.md` — host-neutral callable API and data structures.
- `specs/compatibility.md` — differences in host integration coverage.
- `specs/EIGRP-Config-Guide.md` — portable configuration/operation semantics; available syntax varies by host.
- `specs/rfc7868.md` — RFC reference and evidence hierarchy.
- `eigrp/specs/cisco-errata.md` — tracked Cisco/RFC differences and evidence.
- `eigrp/specs/dual.md` — DUAL state-machine design.
- `eigrp/specs/route-selection.md` — topology path ordering and successor selection.
- `eigrp/specs/rtp.md` — packetization and reliable transport design.

## Contribution rules

Preserve existing copyright, SPDX, and author history. New files use the copyright of the person who wrote them.

Do not add alias wrappers, duplicate old/new paths, compatibility layers, or host-specific protocol logic without an approved architectural reason.

See [Contributing](CONTRIBUTING.md) for the contribution workflow and [Developer guide](specs/developer-guide.md) for debugging, style and test details.

## Security

Security reports may be sent to:

```text
diivious [at] hotmail.com
```

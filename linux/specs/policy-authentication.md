# Native Linux EIGRP policy and MD5 authentication

Copyright (C) 2026 Donnie V. Savage

## Scope and authority

This host policy provider is separate from `libunix/code/eigrp_uut_policy.c`,
which is an intentionally permissive **virtual-wire test fixture**. Neither
module owns portable EIGRP DUAL, advertisement, RTP, TLV parsing or cryptographic
packet authentication. The portable core remains responsible for those actions.

**Branch:** `linux-support`. The checked Linux branch has policy, interface,
packet and Netlink libraries plus an offline configuration checker, but no
runnable daemon. Initial-load semantic calls can be exercised with compiled
harnesses; Linux SIGHUP reload and Netlab Cisco route exchange **cannot yet be
claimed or executed** for a native `openeigrpd` process.

## Accepted syntax and semantics

- `ip|ipv6 prefix-list NAME seq N permit|deny NETWORK/LENGTH [ge N] [le N]`:
  ordered ascending sequence, first matching entry, implicit deny. Without
  `ge` and `le`, matches exactly the specified prefix length. Prefix bits
  beyond the configured network length are ignored. `ge`/`le` bound the
  candidate route length within IPv4 0–32 or IPv6 0–128.
- `ip|ipv6 access-list NAME seq N permit|deny NETWORK/LENGTH`:
  destination-network address containment, ordered first match, implicit deny.
  This is **not** Cisco extended access-list grammar. ACLs do not check the
  candidate route's prefix length.
- `route-map NAME permit|deny N [match prefix-list NAME|match access-list NAME|
  match tag N]`: ascending sequence, first matching clause; default `match`
  matches all. An ACL/prefix-list match succeeds only on that list's PERMIT
  result. An absent object, unmatched clause or final implicit deny rejects
  the route. `match tag` is available to redistribution; summary leak-map
  evaluation has no route tag and cannot match tag clauses.
- Address-family `distribute-list prefix|access NAME in|out [INTERFACE]` is
  committed through the portable filter configuration API. The host evaluates
  the referenced object; absent rules evaluate DENY.
- Base topology `redistribute connected|static [metric BW DELAY REL LOAD MTU]
  [route-map NAME]` dispatches through the portable redistribution API.
  Route-map deny removes a candidate; portable redistribution and Netlink
  observer own the source lifecycle. Configuration references must resolve to
  staged definitions before commit.
- `key-chain NAME key ID secret SECRET` defines a single MD5 key per named
  chain; an IPv4 interface must explicitly configure both `authentication mode md5`
  and `authentication key-chain NAME`. Values are looked up by the existing
  portable auth module, not serialized by the Linux policy module. IPv6
  authentication is withheld until a dedicated IPv6 MD5 regression qualifies
  this path.

## Unix/Linux configuration provider boundary

`libunix/code/eigrp_unix_config_apply.c` is compiled by portable Unix/UUT
regression harnesses with no Linux headers or libraries. It must not include
`eigrp_linux_policy.h` or call Linux policy routines directly. A native Linux
host registers the policy configuration provider via
`eigrp_linux_config_policy_register()` **before**
`eigrp_unix_config_commit()`. The binding translates the staged rule/route-map
commands into the Linux policy API. An unregistered host explicitly returns
`EIGRP_RESULT_NOT_IMPLEMENTED` for those commands: the permissive virtual-wire
UUT fixture is never used for native policy decisions. The registration is an
initial-configuration integration mechanism, not a live-reload transaction.

Defining new rules, replacing rules at a sequence, and deleting rules calls
`eigrp_sys_policy_runtime_update()`, `eigrp_redist_policy_update_all()` and
`eigrp_summary_policy_update_all()` **after releasing the policy mutex**. The
portable core owns the actual soft neighbor resync, outgoing advertisement
refresh, redistribution candidate replay, and route retirement. Reset/finish
are shutdown-only; the reset API is not an atomic live policy-reload facility.

## Explicitly unsupported

The parser rejects route-map `set` actions, multi-match AND/OR clauses,
next-hop/interface/community matching, route-map sequence deletion syntax,
Cisco wildcard/extended ACL grammar, multiple key IDs per keychain, key
lifetimes/rotation, HMAC-SHA-256, HMAC encoded type-7 secrets, non-default
VRFs and unsupported redistribution protocols. No unsupported construct may
fall through to implicit policy PERMIT. No silent MD5-to-no-auth downgrade is
allowed; the configuration checker rejects missing mode/chain pairs.

The native host policy API supports rule replacement and removal for future
**serialized** runtime control, but there is no transactional reload parser,
service manager, daemon scheduler or live configuration swap in this branch.
No CLI feature should imply working live policy changes on a daemon yet.

## Security review

- Missing policies and missing route maps evaluate DENY, not the permissive
  UUT fixture result. Rule reads and changes are protected by a mutex; refresh
  callbacks run without that lock to prevent lock-order reentrancy.
- Maximum policy rules are bounded (4096) and names are length-bounded.
  File staging limits total commands and tokens. Invalid length ranges,
  duplicates and unresolved references are rejected before commit.
- Key definitions require a regular config file with group/other permission
  bits clear (`0600` recommended). Access requires protected directories and
  appropriate user ownership as normal Unix deployment hygiene; permission
  bits alone do not enforce privilege separation or prevent process-memory
  inspection by privileged users.
- Secret text is never passed to log/error formatting. Temporary parse lines,
  failed command allocations, committed command plans on delete and retired
  keychain objects are explicitly scrubbed. In-memory keys are not encrypted;
  physical/swap/core-dump exposure is not otherwise mitigated. Operators
  must secure crash dumps and backups.
- `#` and `!` inside secret tokens are literal bytes, not truncated comment
  markers. Only markers at token boundaries open comments. Quotes/spaces in
  secrets are not supported and are explicitly rejected as extra tokens.
- Native MD5 keychain lookup relies on the existing portable authentication
  code; this work adds no new MD5 implementation or raw digest processing.
  MD5 is kept for interoperability, not claimed to provide modern security.

## Verification

- `linux/test/test_policy.py` compiles the real native policy provider with
  C assertions covering ACL/prefix matching, rule order, route-map decisions,
  tag/summary behavior, updates and implicit deny.
- `linux/test/test_config.py` also compiles the shared config dispatcher
  **without any Linux include paths**, protecting the portable UUT build
  contract, and tests missing-provider rejection before native registration.
- `linux/test/test_config.py` compiles the configuration parser and real host
  provider/keychain, validating parse, reference constraints, semantic dispatch,
  secret handling, permissions and fail-closed invalid inputs.
- `eigrp/test/uut/filter-auth/ipv4-policy-md5-lifecycle.yaml` covers the
  portable MD5/filter/redistribution lifecycle using the **UUT fixture**, not
  the Linux provider; it cannot establish native Cisco interoperability.
- Existing `auth-topology`, `prefix-limit`, `config-lifecycle` and core scenarios
  provide portable regression evidence. Netlab scenarios cannot exercise a
  Linux policy/auth daemon until the native process and scheduler exist.

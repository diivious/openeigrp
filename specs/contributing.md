# Contribution and style rules

[Start here](../CONTRIBUTING.md) for the short contribution path. This page
contains the details needed when writing or reviewing code.

## Before editing

- Pick the owning module and read its specification and callers.
- Keep a defect fix narrow; include directly related lifecycle problems and
  appropriate regression tests, not cosmetic refactoring.
- Discuss public API changes, rename-only operations, new compatibility paths,
  or cross-platform ownership changes before submitting them.
- Preserve copyright, SPDX identifiers and author history.

## Style

The repository `.clang-format` governs C formatting. Use the available
`clang-format` executable, not a pinned minor version. Prefer descriptive,
compact `eigrp_<module>_<object>_<action>()` names, with actions last. Use
`nbr`, `intf`, `topo`, `cfg`, `pfx`, and `desc` consistently. Avoid unnecessary
aliases, deprecated wrappers and sentence-length symbols.

```sh
make check             # changed-file validation and checker tests
make check SOURCE=all  # complete tree
python3 tools/style-fix.py --help
python3 tools/style-check.py --help
```

Review the help output before running a fixer. Formatting tools must not
rewrite unrelated code. Additional rules, including platform-specific
exceptions, live in [code conventions](code-conventions.md).

## Tests and PR evidence

A protocol bug fix needs an executable implementation test and a YAML UUT
scenario, either added or extended. Verify failures, withdrawals, recovery,
cleanup and object ownership where relevant. Review the complete diff and
re-run existing suites that share affected code.

A pull request should state: **Issue**, **Change**, **Test results**, and
**Unverified checks**. For live interoperability claims, include verified
Cisco packet or routing-state evidence; FRR behavior alone is not protocol
authority. AI-assisted code requires the same personal understanding and
review as hand-written code.

For sensitive defects, follow [SECURITY.md](../SECURITY.md), not public issue
comments. EIGRP Stub runtime development is out of scope.

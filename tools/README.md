# Repository Development Tools

Copyright (C) 2026 Donnie V. Savage

This directory owns repository-wide developer utilities only:

- `python`: portable Python interpreter selection (`EIGRP_PYTHON` override).
- `style-check.py` and `style-fix.py`: project-wide style validation and formatting.
- `version.sh`: shared release/version resolution for Debian and Docker builds.
- `backup.sh`: repository snapshot utility.
- `test-log.py`: capture pass/fail results and diagnostics under `logs/`.
- `test/`: executable tests of these tools and their integration dispatch.

Integration-specific entry points live with their owners:

- [FRR tools](../frr/tools/README.md): staging, FRR builds, Docker images and FRR UUT.
- [Linux packaging](../linux/packaging/README.md): Debian build and package wrapper.
- [Netlab tools](../netlabs/tools/README.md): optional personal Netlab regression system, **not** part of `make all`.

From the repository root, `make`/`make all` runs the standard developer gate;
`make uut` runs the portable unit and YAML tests; `make pre-commit` adds
both Docker image builds and both Debian package builds/lifecycle tests. `make clean` preserves test
results. `make distclean` removes generated standard test results, but does
not touch Netlab evidence. Compilation outputs remain under their existing
build locations; all generated test evidence belongs under `logs/`.

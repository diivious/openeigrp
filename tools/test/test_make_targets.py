#!/usr/bin/env python3
"""Execute the developer Makefile against isolated, observable test doubles."""

# Copyright (C) 2026 Donnie V. Savage

import os
import platform
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
CHILDREN = ("eigrp/code", "libunix/code", "linux/code", "eigrp/test/uut",
            "eigrp/test/scale", "frr/test/build")


class MakeTargetsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="make-targets-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        shutil.copy2(ROOT / "Makefile", self.root / "Makefile")
        self.log = self.root / "events"
        for child in CHILDREN:
            directory = self.root / child
            directory.mkdir(parents=True, exist_ok=True)
            (directory / "Makefile").write_text(
                ".PHONY: all clean distclean all-scenarios eigrp-uut-node "
                "ipv4-basic-core ipv6-basic-core rtp-core core-topology mesh-topology "
                "basic-core md5-mixed-lan md5-security dual-diamond kite-topology "
                "active-topology summary-topology peers internal external line kite "
                "peer-routes withdraw mcast linkdown mixed-tlv churn auth-md5\n"
                "all clean distclean all-scenarios eigrp-uut-node ipv4-basic-core "
                "ipv6-basic-core rtp-core core-topology mesh-topology basic-core "
                "md5-mixed-lan md5-security dual-diamond kite-topology "
                "active-topology summary-topology peers internal external line kite "
                "peer-routes withdraw mcast linkdown mixed-tlv churn auth-md5:\n"
                "\t@printf 'CHILD %s %s\\n' '$(CURDIR)' '$@' >> '$(LOG_FILE)'\n"
                "\t@if [ \"$(FAIL_LINUX)\" = 1 ] && "
                "[ \"$(CURDIR)\" = \"$(LINUX_CODE)\" ] && "
                "[ '$@' = all ]; then exit 5; fi\n"
                "\t@if [ '$@' = \"$(FAIL_SCALE_CASE)\" ]; then exit 7; fi\n"
            )
        linux = self.root / "linux"
        shutil.copy2(ROOT / "linux/Makefile", linux / "Makefile")
        tools = self.root / "tools"
        tools.mkdir(exist_ok=True)
        self._script("tools/python", "printf 'PY %s\\n' \"$*\" >> \"$LOG_FILE\"\n")
        self._script("frr/tools/docker.sh", "printf 'DOCKER %s\\n' \"$*\" >> \"$LOG_FILE\"\n")
        self._script("frr/tools/frr.sh", "printf 'FRR %s\\n' \"$*\" >> \"$LOG_FILE\"\n")
        self._script("tools/version.sh", "printf 'VERSION %s\n' \"$*\" >> \"$LOG_FILE\"\n"
                     "printf 'testdev-1.2.3-dev\n'\n")
        self._script("bin/docker", "printf 'TAG %s\\n' \"$*\" >> \"$LOG_FILE\"\n"
                     "if [ \"$1 $2\" = 'image inspect' ]; then "
                     "printf 'testdev-1.2.3-dev\\n'; fi\n")
        self._script("linux/packaging/build-packages.sh",
                     "printf 'PACKAGES %s\\n' \"$*\" >> \"$LOG_FILE\"\n")
        for directory in ("eigrp/test/packet", "eigrp/test/rtp",
                          "tools/test", "linux/test"):
            (self.root / directory).mkdir(parents=True, exist_ok=True)

    def _script(self, name, body):
        file = self.root / name
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("#!/bin/sh\n" + body)
        file.chmod(0o755)

    def make(self, *args):
        env = os.environ.copy()
        for key in ("MAKEFLAGS", "MFLAGS", "GNUMAKEFLAGS", "MAKEOVERRIDES",
                    "SOURCE", "STYLE_CHECK_BASE", "SUITE", "SCENARIO", "CASE",
                    "GITHUB", "FRR", "HOST_OS", "ARCH", "TAG", "VERSION"):

            env.pop(key, None)
        env.update(PATH=str(self.root / "bin") + os.pathsep + env.get("PATH", ""),
                   LOG_FILE=str(self.log), USER="testdev",
                   PYTHON=str(self.root / "tools/python"),
                   LINUX_CODE=str(self.root / "linux/code"))
        completed = subprocess.run(["make", "--no-print-directory", *args],
                                   cwd=self.root, env=env, text=True,
                                   capture_output=True)
        events = self.log.read_text().splitlines() if self.log.exists() else []
        return completed, events

    def test_default_is_compilation_and_style_without_docker(self):
        for host in ("Linux", "Darwin"):
            if self.log.exists():
                self.log.unlink()
            result, events = self.make(f"HOST_OS={host}")
            self.assertEqual(result.returncode, 0, (host, result.stderr))
            self.assertEqual(events.count("VERSION --local testdev"), 1)
            self.assertEqual(events.count("PY tools/style-check.py --all"), 1)
            self.assertEqual(any("linux/code all" in e for e in events),
                             host == "Linux")
            self.assertTrue(any("eigrp/code" in e for e in events))
            self.assertTrue(any("libunix/code" in e for e in events))
            self.assertFalse(any("all-scenarios" in e or "pytest" in e
                                 for e in events))
            self.assertFalse(any(e.startswith(("DOCKER ", "PACKAGES "))
                                 for e in events))
            self.assertFalse(any(e.startswith("FRR ") for e in events))

    def test_make_all_builds_and_runs_fast_tests_not_container_uut(self):
        for host in ("Linux", "Darwin"):
            if self.log.exists():
                self.log.unlink()
            result, events = self.make("all", f"HOST_OS={host}")
            self.assertEqual(result.returncode, 0, (host, result.stderr))
            self.assertEqual(events.count("VERSION --local testdev"), 1)
            self.assertEqual(events.count("PY tools/style-check.py --all"), 1)
            self.assertTrue(any("test_make_targets" in e for e in events))
            self.assertTrue(any("all-scenarios" in e for e in events))
            self.assertTrue(any("-m pytest -x eigrp/test" in e for e in events))
            self.assertTrue(any("frr/test/version" in e for e in events))
            self.assertEqual(any("linux/test" in e for e in events),
                             host == "Linux")
            self.assertTrue(any("pacing.py" in e for e in events))
            self.assertEqual(sum(e.startswith("DOCKER --local") for e in events), 2)
            self.assertEqual(sum(e.startswith("DOCKER --verify") for e in events), 0)
            self.assertEqual(sum(e.startswith("PACKAGES ") for e in events), 1)
            self.assertIn("PACKAGES --version testdev-1.2.3-dev --arch all", events)
            self.assertFalse(any("--uut" in e and e.startswith("PACKAGES ")
                                 for e in events))
            self.assertFalse(any("netlabs" in e or e.startswith("FRR ")
                                 for e in events))
            # Full stress/volume/auth scale cases belong to pre-commit only.
            self.assertFalse(any(e.startswith("CHILD ") and
                                 any(e.endswith(" " + name) for name in (
                                     "peers", "internal", "external", "line",
                                     "kite", "peer-routes", "withdraw", "mcast",
                                     "linkdown", "mixed-tlv", "churn", "auth-md5"))
                                 for e in events))
            positions = [next(i for i, e in enumerate(events) if predicate(e))
                         for predicate in (
                             lambda e: "eigrp/code" in e,
                             lambda e: e == "PY tools/style-check.py --all",
                             lambda e: "test_make_targets" in e,
                             lambda e: "all-scenarios" in e,
                             lambda e: "pacing.py" in e,
                             lambda e: e.startswith("DOCKER --local"),
                             lambda e: e.startswith("PACKAGES "))]
            self.assertEqual(positions, sorted(positions))

    def test_darwin_all_builds_both_linux_architectures(self):
        result, events = self.make("all", "HOST_OS=Darwin")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Native Linux executable tests run inside", result.stdout)
        self.assertFalse(any("linux/code all" in e for e in events))
        self.assertIn(f"CHILD {self.root / 'eigrp/code'} all", events)
        self.assertEqual(sum(e.startswith("DOCKER --local") for e in events), 2)
        self.assertEqual(sum(e.startswith("DOCKER --verify") for e in events), 0)
        self.assertEqual(sum(e.startswith("PACKAGES ") for e in events), 1)

    def test_linux_checks_host_before_any_compilation(self):
        result, events = self.make("linux", "HOST_OS=Darwin")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires a Linux host", result.stderr)
        self.assertFalse(any("CHILD" in e for e in events))

    def test_linux_build_dispatch_and_failure_stop(self):
        result, events = self.make("linux", "HOST_OS=Linux")
        self.assertEqual(result.returncode, 0, result.stderr)
        for child in ("eigrp/code", "libunix/code", "linux/code"):
            self.assertIn(f"CHILD {self.root / child} all", events)
        self.log.unlink()
        result, events = self.make("all", "HOST_OS=Linux", "FAIL_LINUX=1")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any(e.startswith("PY") for e in events))

    def test_frr_packages_docker_dispatch(self):
        result, events = self.make("frr")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(events[-1], "FRR --build")
        self.log.unlink()
        result, events = self.make("packages", "VERSION=0.1.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PACKAGES --version 0.1.0 --arch all", events)
        self.log.unlink()
        result, events = self.make("packages")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("VERSION --local testdev", events)
        self.assertIn("PACKAGES --version testdev-1.2.3-dev --arch all", events)
        self.log.unlink()
        result, events = self.make("docker")
        self.assertEqual(result.returncode, 0, result.stderr)
        for arch in ("amd64", "arm64"):
            self.assertTrue(any(f"--platform linux/{arch}" in e and
                                f"--image openeigrp:testdev-{arch}" in e
                                for e in events))
        host_arch = ("arm64" if platform.machine().lower() in ("aarch64", "arm64")
                     else "amd64")
        self.assertIn(f"TAG tag openeigrp:testdev-{host_arch} "
                      "openeigrp:testdev", events)
        self.log.unlink()
        result, events = self.make("docker", "GITHUB=latest")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(e.startswith("DOCKER --build latest ") for e in events), 2)
        self.assertNotEqual(self.make("docker", "FRR=x", "GITHUB=x")[0].returncode, 0)

    def test_arm64_host_keeps_native_image_alias(self):
        self._script("bin/uname", "if [ \"$1\" = -m ]; then echo arm64; "
                     "else /usr/bin/uname \"$@\"; fi\n")
        result, events = self.make("docker")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TAG tag openeigrp:testdev-arm64 openeigrp:testdev", events)

    def test_arch_selection_and_invalid_arch(self):
        for target in ("docker", "packages", "package-uut"):
            if self.log.exists():
                self.log.unlink()
            result, events = self.make(target, "ARCH=arm64", "VERSION=1.0.0")
            self.assertEqual(result.returncode, 0, (target, result.stderr))
            match = [event for event in events if event.startswith(("DOCKER ", "PACKAGES "))]
            self.assertEqual(len(match), 1, (target, match))
            self.assertIn("arm64", match[0])
            self.assertNotEqual(self.make(target, "ARCH=i386")[0].returncode, 0)

    def test_pre_commit_runs_all_long_regressions_not_remote_netlab(self):
        for host in ("Linux", "Darwin"):
            if self.log.exists():
                self.log.unlink()
            result, events = self.make("pre-commit", f"HOST_OS={host}")
            self.assertEqual(result.returncode, 0, (host, result.stderr))
            self.assertEqual(sum(e.startswith("DOCKER --local") for e in events), 2)
            self.assertEqual(sum(e.startswith("DOCKER --verify") for e in events), 2)
            self.assertEqual(sum(e.startswith("PACKAGES ") for e in events), 2)
            self.assertEqual(sum(e.startswith("CHILD ") and
                                 any(e.endswith(" " + name) for name in (
                                     "peers", "internal", "external", "line",
                                     "kite", "peer-routes", "withdraw", "mcast",
                                     "linkdown", "mixed-tlv", "churn", "auth-md5"))
                                 for e in events), 11)
            self.assertTrue(any("neighbor_2048.py" in e for e in events))
            self.assertFalse(any("netlabs" in e or "frr/test/uut" in e
                                 for e in events))
            self.assertEqual(any("linux/test" in e for e in events),
                             host == "Linux")
            indices = [next(i for i, e in enumerate(events) if predicate(e))
                       for predicate in (
                           lambda e: e.startswith("DOCKER --local"),
                           lambda e: e.startswith("PACKAGES "),
                           lambda e: "neighbor_2048.py" in e,
                           lambda e: e.startswith("DOCKER --verify"),
                           lambda e: e.startswith("PACKAGES ") and "--uut" in e)]
            self.assertEqual(indices, sorted(indices))

    def test_pre_commit_builds_once_and_resolves_one_version(self):
        for host in ("Linux", "Darwin"):
            if self.log.exists():
                self.log.unlink()
            result, events = self.make("pre-commit", f"HOST_OS={host}")
            self.assertEqual(result.returncode, 0, (host, result.stderr))
            self.assertEqual(events.count("VERSION --local testdev"), 1)
            self.assertEqual(events.count("PY tools/style-check.py --all"), 1)
            self.assertEqual(sum(e.startswith("DOCKER --local") for e in events), 2)
            self.assertEqual(sum(e.startswith("DOCKER --verify") for e in events), 2)
            self.assertEqual(sum(e.startswith("PACKAGES ") for e in events), 2)
            self.assertFalse(any(e.startswith("FRR ") for e in events))

    def test_pre_commit_stops_on_package_lifecycle_failure(self):
        package = self.root / "linux/packaging/build-packages.sh"
        package.write_text("#!/bin/sh\nprintf 'PACKAGES %s\\n' \"$*\" >> \"$LOG_FILE\"\n"
                           "case \"$*\" in *'--uut'*) exit 23;; esac\n")
        package.chmod(0o755)
        result, events = self.make("pre-commit", "HOST_OS=Darwin")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sum(e.startswith("PACKAGES ") for e in events), 2)
        self.assertEqual(sum(e.startswith("DOCKER --verify") for e in events), 2)

    def test_pre_commit_uses_local_build_even_with_release_overrides(self):
        result, events = self.make("pre-commit", "HOST_OS=Darwin",
                                   "GITHUB=latest", "VERSION=3.2.1", "ARCH=amd64")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(e.startswith("DOCKER --local") for e in events), 2)
        self.assertEqual(sum(e.startswith("DOCKER --build") for e in events), 0)
        self.assertIn("PACKAGES --version testdev-1.2.3-dev --arch all", events)

    def test_all_forces_both_architectures(self):
        result, events = self.make("all", "HOST_OS=Darwin", "ARCH=amd64")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(sum(e.startswith("DOCKER --local") for e in events), 2)
        self.assertEqual(sum(e.startswith("DOCKER --verify") for e in events), 0)
        self.assertTrue(all("--arch all" in e for e in events if e.startswith("PACKAGES ")))

    def test_all_stops_if_an_architecture_build_fails(self):
        docker = self.root / "frr/tools/docker.sh"
        docker.write_text("#!/bin/sh\nprintf 'DOCKER %s\\n' \"$*\" >> \"$LOG_FILE\"\n"
                          "case \"$*\" in *linux/arm64*) exit 43;; esac\n")
        docker.chmod(0o755)
        result, events = self.make("all", "HOST_OS=Darwin")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sum(e.startswith("DOCKER --local") for e in events), 2)
        self.assertFalse(any(e.startswith("PACKAGES ") for e in events))
        self.assertTrue(any("all-scenarios" in e for e in events))

    def test_docker_uut_rejects_wrong_embedded_version(self):
        self._script("bin/docker", "printf 'TAG %s\n' \"$*\" >> \"$LOG_FILE\"\n"
                     "if [ \"$1 $2\" = 'image inspect' ]; then echo stale-release; fi\n")
        result, events = self.make("docker-uut", "ARCH=arm64")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("reports stale-release", result.stderr)
        self.assertFalse(any(e.startswith("DOCKER --verify") for e in events))

    def test_pre_commit_stops_on_docker_runtime_uut_failure(self):
        docker = self.root / "frr/tools/docker.sh"
        docker.write_text("#!/bin/sh\nprintf 'DOCKER %s\\n' \"$*\" >> \"$LOG_FILE\"\n"
                          "case \"$*\" in *--verify*) exit 41;; esac\n")
        docker.chmod(0o755)
        result, events = self.make("pre-commit", "HOST_OS=Darwin")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(sum(e.startswith("PACKAGES ") for e in events), 1)
        self.assertFalse(any("--uut" in e for e in events if e.startswith("PACKAGES ")))

    def test_uut_single_scenario_and_named_suite(self):
        result, events = self.make("uut", "SUITE=rtp-core")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(any("rtp-core" in e for e in events))
        self.log.unlink()
        result, events = self.make("uut", "SCENARIO=examples/scenario.yaml")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PY eigrp/test/uut/run_all.py --scenario examples/scenario.yaml", events)
        self.assertNotEqual(self.make("uut", "SUITE=rtp-core", "SCENARIO=x.yaml")[0].returncode, 0)
        self.assertNotEqual(self.make("uut", "SUITE=missing")[0].returncode, 0)

    def test_removed_public_targets_and_style_in_make(self):
        self.assertNotEqual(self.make("check")[0].returncode, 0)
        self.assertNotEqual(self.make("portable-build")[0].returncode, 0)
        result, events = self.make("HOST_OS=Darwin")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("PY tools/style-check.py --all", events)
        self.assertFalse(any("test_style_check.py" in e for e in events))

    def test_style_failure_stops_before_unit_tests(self):
        self._script("tools/python", "printf 'PY %s\\n' \"$*\" >> \"$LOG_FILE\"\n"
                     "case \"$*\" in *tools/style-check.py*) exit 29;; esac\n")
        result, events = self.make("all", "HOST_OS=Darwin")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("PY tools/style-check.py --all", events)
        self.assertFalse(any("all-scenarios" in e for e in events))
        self.assertFalse(any(e.startswith(("DOCKER --local", "PACKAGES "))
                             for e in events))

    def test_make_requires_no_docker_but_all_does(self):
        self._script("bin/docker", "exit 17\n")
        result, events = self.make("HOST_OS=Darwin")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(any(e.startswith("DOCKER ") for e in events))
        self.log.unlink()
        result, events = self.make("all", "HOST_OS=Darwin")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(any("all-scenarios" in e for e in events))

    def test_scale_suite_stops_at_first_failure(self):
        result, events = self.make("scale", "SUITE=stress", "FAIL_SCALE_CASE=mcast")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FAIL mcast", result.stderr)
        self.assertTrue(any(" withdraw" in event for event in events))
        self.assertTrue(any(" mcast" in event for event in events))
        self.assertFalse(any(" linkdown" in event for event in events))
        self.assertFalse(any(" mixed-tlv" in event for event in events))

    def test_scale_selectors(self):
        for selection, needle in ((('scale',), "pacing.py"),
                                  (('scale', 'CASE=mcast'), "mcast"),
                                  (('scale', 'SUITE=auth'), "auth-md5")):
            if self.log.exists():
                self.log.unlink()
            result, events = self.make(*selection)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(any(needle in e for e in events), events)
        self.assertNotEqual(self.make("scale", "CASE=invalid")[0].returncode, 0)

    def test_clean_preserves_logs_and_distclean_removes_them(self):
        evidence = self.root / "logs/uut/eigrp/run-one/report.json"
        evidence.parent.mkdir(parents=True, exist_ok=True)
        evidence.write_text('{"result":"PASS"}')
        personal_evidence = self.root / "logs/netlab/private-run/report.json"
        personal_evidence.parent.mkdir(parents=True, exist_ok=True)
        personal_evidence.write_text('{"result":"PASS"}')
        source = self.root / "netlabs/test/rtp/scenario.yaml"
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("schema: eigrp-uut-test/v1\n")
        cache = self.root / "netlabs/test/__pycache__"
        cache.mkdir()
        (cache / "junk.pyc").write_bytes(b"cache")
        result, events = self.make("clean")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(evidence.exists())
        self.assertTrue(source.exists())
        self.assertTrue(cache.exists())
        self.assertTrue(personal_evidence.exists())
        result, _ = self.make("distclean")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(evidence.exists())
        self.assertTrue(personal_evidence.exists())
        self.assertTrue(cache.exists())
        self.assertTrue(source.exists())
        self.assertEqual(self.make("distclean")[0].returncode, 0)

    def test_no_public_build_or_frr_smoke_targets(self):
        self.assertNotEqual(self.make("build")[0].returncode, 0)
        self.assertNotEqual(self.make("frr-smoke")[0].returncode, 0)


if __name__ == "__main__":
    unittest.main()

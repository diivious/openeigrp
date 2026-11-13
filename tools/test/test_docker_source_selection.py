#!/usr/bin/env python3
"""Test Docker source staging against real local Git repositories (no Docker)."""

# Copyright (C) 2026 Donnie V. Savage

import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "frr/tools/docker.sh"


class DockerSourceSelectionTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="docker-sources-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.eigrp = self.root / "openeigrp"
        self.frr = self.root / "frr"
        self.eigrp.mkdir()
        self.frr.mkdir()
        self.git(self.eigrp, "init", "-q")
        self.git(self.eigrp, "branch", "-M", "master")
        self.git(self.frr, "init", "-q")
        for repo in (self.eigrp, self.frr):
            self.git(repo, "config", "user.name", "Release Test")
            self.git(repo, "config", "user.email", "release@example.invalid")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "docker.log"
        # Preserve real Git operations but substitute only the two remote URLs.
        git = self.bin / "git"
        git.write_text(
            "#!/bin/bash\n"
            "args=()\n"
            "for arg in \"$@\"; do\n"
            "  case \"$arg\" in\n"
            f"    https://github.com/diivious/openeigrp.git) arg='{self.eigrp}' ;;\n"
            f"    https://github.com/FRRouting/frr.git) arg='{self.frr}' ;;\n"
            "  esac\n"
            "  args+=(\"$arg\")\n"
            "done\n"
            "exec /usr/bin/git \"${args[@]}\"\n"
        )
        git.chmod(0o755)
        docker = self.bin / "docker"
        docker.write_text(
            "#!/bin/bash\n"
            "printf '%s\\n' \"$*\" >> \"$DOCKER_TEST_LOG\"\n"
            "if [[ ${1:-} == run ]]; then\n"
            "  version=; arch=; stage=\n"
            "  for arg in \"$@\"; do\n"
            "    case \"$arg\" in\n"
            "      PACKAGE_VERSION=*) version=${arg#PACKAGE_VERSION=} ;;\n"
            "      PACKAGE_ARCH=*) arch=${arg#PACKAGE_ARCH=} ;;\n"
            "      type=bind,source=*,target=/out) stage=${arg#*source=}; stage=${stage%,target=/out} ;;\n"
            "    esac\n"
            "  done\n"
            "  if [[ -n $version && -n $arch && -n $stage ]]; then\n"
            "    printf 'fixture' > \"$stage/openeigrpd_${version}_${arch}.deb\"\n"
            "  fi\n"
            "  exit 0\n"
            "fi\n"
            "for arg in \"$@\"; do\n"
            "  case \"$arg\" in\n"
            "    eigrp-source=*)\n"
            "      src=${arg#eigrp-source=}\n"
            "      cat \"$src/docker/Dockerfile\" >> \"$DOCKER_TEST_LOG\"\n"
            "      cat \"$src/docker/docker.cfg\" >> \"$DOCKER_TEST_LOG\" 2>/dev/null || :\n"
            "      cat \"$src/local-edit\" >> \"$DOCKER_TEST_LOG\" 2>/dev/null || :\n"
            "      ;;\n"
            "    frr-source=*)\n"
            "      src=${arg#frr-source=}\n"
            "      cat \"$src/revision.txt\" >> \"$DOCKER_TEST_LOG\"\n"
            "      cat \"$src/local-edit\" >> \"$DOCKER_TEST_LOG\" 2>/dev/null || :\n"
            "      ;;\n"
            "  esac\n"
            "done\n"
        )
        docker.chmod(0o755)
        (self.bin / "rsync").symlink_to("/usr/bin/rsync")

        (self.frr / "bootstrap.sh").write_text("#!/bin/sh\n")
        (self.frr / "tests").mkdir()
        (self.frr / "revision.txt").write_text("FRR_OLD\n")
        self.commit(self.frr)
        self.git(self.frr, "tag", "-a", "frr-10.7.1", "-m", "old")
        self.frr_old_sha = self.git(self.frr, "rev-parse", "HEAD").stdout.strip()
        (self.frr / "revision.txt").write_text("FRR_NEW\n")
        self.commit(self.frr)
        self.git(self.frr, "tag", "-a", "frr-10.7.2", "-m", "new")
        self.frr_new_sha = self.git(self.frr, "rev-parse", "HEAD").stdout.strip()

        (self.eigrp / "docker").mkdir()
        (self.eigrp / "tools").mkdir()
        (self.eigrp / "frr/tools").mkdir(parents=True)
        # The Docker source probe must exercise the integration-owned path.
        (self.eigrp / "frr/tools/frr.sh").write_text("#!/bin/sh\n")
        (self.eigrp / "frr/tools/docker.sh").write_bytes(SCRIPT.read_bytes())
        # The packaged-source fixture must include the same build helper as
        # the real tree.  The package entrypoint invokes it at runtime.
        for name in ("version.sh", "build-version.sh"):
            target = self.eigrp / "tools" / name
            target.write_bytes((ROOT / "tools" / name).read_bytes())
            target.chmod(0o755)
        (self.eigrp / "linux/packaging").mkdir(parents=True)
        for relative in ("linux/packaging/package.sh", "linux/packaging/build-packages.sh"):
            target = self.eigrp / relative
            target.write_bytes((ROOT / relative).read_bytes())
            target.chmod(0o755)
        (self.eigrp / "docker/Dockerfile").write_text("ARG ALPINE_VERSION=3.21\nRUN echo TAGGED_OLD\n")
        self.write_config("frr-10.7.1", self.frr_old_sha)
        self.commit(self.eigrp)
        self.git(self.eigrp, "tag", "-a", "rel-1.9.0", "-m", "old")
        (self.eigrp / "docker/Dockerfile").write_text("ARG ALPINE_VERSION=3.22\nRUN echo TAGGED_NEW\n")
        self.write_config("frr-10.7.2", self.frr_new_sha)
        self.commit(self.eigrp)
        self.git(self.eigrp, "tag", "-a", "rel-1.10.0", "-m", "new")
        # Current development images pin a native FRR source commit, while
        # historical release tags retain their original tag-based pins.
        self.write_config(self.frr_new_sha, self.frr_new_sha)
        (self.eigrp / "docker/Dockerfile").write_text("RUN echo WORKING_BRANCH\n")
        self.commit(self.eigrp)
        self.git(self.eigrp, "remote", "add", "origin", str(self.eigrp))

    @staticmethod
    def git(repo, *args):
        return subprocess.run(["/usr/bin/git", "-C", str(repo), *args],
                              check=True, text=True, capture_output=True)

    def commit(self, repo):
        self.git(repo, "add", "-A")
        self.git(repo, "commit", "-qm", "fixture")

    def write_config(self, ref, sha):
        key = "FRR_REF" if len(ref) == 40 else "FRR_TAG"
        (self.eigrp / "docker/docker.cfg").write_text(
            "FRR_REPO=https://github.com/FRRouting/frr.git\n"
            f"{key}={ref}\nFRR_SHA={sha}\n"
        )

    def build(self, mode, *args, script=None):
        # Keep source selection independent of the developer's FRR checkout.
        env = dict(os.environ, HOME=str(self.root),
                   PATH=str(self.bin) + ":" + os.environ["PATH"],
                   DOCKER_TEST_LOG=str(self.log), USER="testdev")
        result = subprocess.run(["bash", str(script or self.eigrp / "frr/tools/docker.sh"), mode, *args],
                                cwd=self.eigrp,
                                env=env, text=True, capture_output=True, timeout=40)
        log = self.log.read_text() if self.log.exists() else ""
        return result, log

    def test_old_release_uses_old_config_and_old_frr(self):
        result, log = self.build("--build", "1.9.0")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("TAGGED_OLD", log)
        self.assertIn("FRR_OLD", log)
        self.assertNotIn("FRR_NEW", log)
        self.assertNotIn("WORKING_BRANCH", log)
        self.assertIn("FRR_TAG=frr-10.7.1", log)
        self.assertIn("ALPINE_VERSION=3.21", log)

    def test_latest_uses_numerically_highest_release(self):
        result, log = self.build("--build", "latest")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Selected latest OpenEIGRP release: rel-1.10.0", result.stdout)
        self.assertIn("TAGGED_NEW", log)
        self.assertIn("FRR_NEW", log)
        self.assertIn("ALPINE_VERSION=3.22", log)
        self.assertNotIn("WORKING_BRANCH", log)

    def test_local_build_includes_uncommitted_sources(self):
        (self.eigrp / "local-edit").write_text("EIGRP_UNCOMMITTED\n")
        (self.frr / "local-edit").write_text("FRR_UNCOMMITTED\n")
        result, log = self.build("--local", "dev-test", "--eigrp-root", str(self.eigrp),
                                 "--frr-root", str(self.frr))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("WORKING_BRANCH", log)
        self.assertIn("EIGRP_UNCOMMITTED", log)
        self.assertIn("FRR_UNCOMMITTED", log)
        self.assertIn("EIGRP_VERSION=dev-test-1.10.0-dev", log)
        self.assertIn("Image:     openeigrp:dev-test", result.stdout)

    def test_local_build_uses_pinned_frr_without_checkout(self):
        # No candidate checkout: the builder must use the FRR commit pinned
        # by the current working OpenEIGRP tree, not a mutable branch.
        (self.frr / "bootstrap.sh").unlink()
        (self.eigrp / "local-edit").write_text("EIGRP_UNCOMMITTED\n")
        result, log = self.build("--local", "dev-test", "--eigrp-root", str(self.eigrp))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Using pinned FRR " + self.frr_new_sha, result.stderr)
        self.assertIn("WORKING_BRANCH", log)
        self.assertIn("EIGRP_UNCOMMITTED", log)
        self.assertIn("FRR_NEW", log)
        self.assertNotIn("FRR_OLD", log)

    def test_local_pin_mismatch_stops_before_docker(self):
        (self.frr / "bootstrap.sh").unlink()
        self.write_config("frr-10.7.2", "0" * 40)
        result, log = self.build("--local", "dev-test", "--eigrp-root", str(self.eigrp))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FRR source pin changed", result.stderr)
        self.assertNotIn("buildx build", log)

    def test_explicit_missing_frr_checkout_is_error(self):
        result, log = self.build("--local", "dev-test", "--eigrp-root", str(self.eigrp),
                                 "--frr-root", str(self.root / "missing"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("local FRR checkout not found", result.stderr)
        self.assertNotIn("buildx build", log)

    def test_default_local_version_uses_user_dev_identity(self):
        result, log = self.build("--local", "--eigrp-root", str(self.eigrp),
                                 "--frr-root", str(self.frr))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("EIGRP_VERSION=testdev-1.10.0-dev", log)

    def test_remote_development_build_uses_shared_version(self):
        result, log = self.build("--build", "dev-test", "--eigrp-ref",
                                 "refs/heads/master")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("EIGRP_VERSION=dev-test-1.10.0-dev", log)
        self.assertIn("Image:     ghcr.io/diivious/openeigrp-frr:dev-test", result.stdout)

    def test_unmerged_higher_tag_not_selected_locally(self):
        self.git(self.eigrp, "checkout", "-qb", "experiment")
        (self.eigrp / "experiment").write_text("new\n")
        self.commit(self.eigrp)
        self.git(self.eigrp, "tag", "rel-9.0.0")
        result, log = self.build("--local", "dev-test", "--eigrp-root", str(self.eigrp),
                                 "--frr-root", str(self.frr))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("EIGRP_VERSION=dev-test-1.10.0-dev", log)

    def test_docker_and_debian_embed_the_same_version(self):
        result, log = self.build("--local", "diivious-test",
                                 "--eigrp-root", str(self.eigrp),
                                 "--frr-root", str(self.frr),
                                 "--image", "openeigrp:diivious-test")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("EIGRP_VERSION=diivious-test-1.10.0-dev", log)
        output = self.root / "dist"
        env = dict(os.environ, HOME=str(self.root),
                   PATH=str(self.bin) + ":" + os.environ["PATH"],
                   DOCKER_TEST_LOG=str(self.log), USER="diivious")
        package = subprocess.run(
            [str(self.eigrp / "linux/packaging/package.sh"), "--local", "diivious-test",
             "--platform", "linux/amd64", "--image", "openeigrp:diivious-test",
             "--output", str(output)],
            cwd=self.eigrp, env=env, text=True, capture_output=True, timeout=40)
        self.assertEqual(package.returncode, 0, package.stderr)
        self.assertTrue((output / "openeigrpd_1.10.0~diivious-test.dev_amd64.deb").exists())
        self.assertIn("PACKAGE_VERSION=1.10.0~diivious-test.dev", self.log.read_text())
        self.assertIn("EIGRP_BUILD_VERSION=diivious-test-1.10.0-dev",
                      self.log.read_text())

    def test_release_ref_cannot_override_its_frr(self):
        result, log = self.build("--build", "1.9.0", "--frr-ref", "refs/heads/main")
        self.assertNotEqual(result.returncode, 0)
        # The read-only daemon probe is permitted; no Docker build may occur.
        self.assertEqual(log, "info\n")

    def test_historical_release_reads_pin_from_its_own_script(self):
        # Pre-docker.cfg releases pinned FRR in the tagged shell script.
        (self.eigrp / "docker/docker.cfg").unlink()
        (self.eigrp / "tools/docker.sh").write_text(
            "frr_url=https://github.com/FRRouting/frr.git\n"
            "frr_release_tag=frr-10.7.1\n"
            f"pinned_frr_sha={self.frr_old_sha}\n"
        )
        self.commit(self.eigrp)
        self.git(self.eigrp, "tag", "rel-1.8.0")
        result, log = self.build("--build", "1.8.0", script=SCRIPT)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("historical FRR pin", result.stderr)
        self.assertIn("FRR_OLD", log)
        self.assertNotIn("FRR_NEW", log)

    def test_invalid_pinned_sha_fails_before_docker(self):
        # Alter the SHA in the release fixture and create a new tag.
        self.write_config("frr-10.7.2", "0" * 40)
        self.commit(self.eigrp)
        self.git(self.eigrp, "tag", "rel-1.11.0")
        result, log = self.build("--build", "1.11.0")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("FRR ref", result.stderr)
        # The read-only daemon probe is permitted; no Docker build may occur.
        self.assertEqual(log, "info\n")



if __name__ == "__main__":
    unittest.main()

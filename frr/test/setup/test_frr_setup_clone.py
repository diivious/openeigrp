# SPDX-License-Identifier: GPL-2.0-or-later
"""Exercise the Debian FRR setup clone path with isolated host commands."""

import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[3]
SETUP = ROOT / "frr" / "tools" / "frr-setup.sh"
SHA = "d63396451886705ad7beaf136ce457ed8f8cde36"


def _script(path, contents):
    path.write_text("#!/bin/sh\n" + contents)
    path.chmod(0o755)


def _run_setup(existing_frr=False, existing_orig=False, skip_clone=False):
    with tempfile.TemporaryDirectory(prefix="eigrp-frr-setup-") as tmp:
        base = Path(tmp)
        base.chmod(0o777)
        tools = base / "frr/tools"
        tools.mkdir(parents=True)
        shutil.copy2(SETUP, tools / "frr-setup.sh")
        _script(tools / "frr-install.sh", "exit 0\n")
        (tools / "etc.frr.frr.conf").write_text("! test configuration\n")

        bindir = base / "bin"
        bindir.mkdir()
        _script(bindir / "groups", "echo 'staff sudo'\n")
        _script(bindir / "getent", "echo 'frr:x:92:'\n")
        _script(bindir / "sudo", "cat >/dev/null\n")
        _script(bindir / "apt", "echo 'libyang2 2.1.128'\n")
        _script(bindir / "git",
                'printf "%s\\n" "$*" >> "$SETUP_GIT_LOG"\n'
                'if [ "$1" = init ]; then\n'
                '  for target do :; done\n'
                '  mkdir -p "$target/.git"\n'
                'elif [ "$1" = -C ] && [ "$3" = rev-parse ]; then\n'
                '  if [ "$4" = HEAD ]; then\n'
                f'    printf "%s\\n" "{SHA}"\n'
                '  else echo true; fi\n'
                'fi\n')

        devel = base / "development"
        devel.mkdir()
        devel.chmod(0o777)
        if existing_frr:
            (devel / "frr").mkdir()
        if existing_orig:
            (devel / "frr-orig").mkdir()
            (devel / "frr-orig" / "keep").write_text("manual checkout\n")
        log = base / "git.log"
        log.touch()
        log.chmod(0o666)
        env = os.environ.copy()
        env["PATH"] = f"{bindir}:{env['PATH']}"
        env["SETUP_GIT_LOG"] = str(log)

        kwargs = {}
        if os.geteuid() == 0:
            # Running setup as root is forbidden, matching production behavior.
            def unprivileged():
                os.setgid(65534)
                os.setuid(65534)
            kwargs["preexec_fn"] = unprivileged

        cmd = ["bash", str(tools / "frr-setup.sh"), "--devel-root", str(devel), "--skip-build"]
        if skip_clone:
            cmd.append("--skip-clone")
        result = subprocess.run(cmd, cwd=base, env=env, capture_output=True, text=True, **kwargs)
        clones = [s for s in log.read_text().splitlines() if s.startswith("init -q ")]
        orig_unchanged = (
            not (devel / "frr-orig").exists()
            if not existing_orig
            else (devel / "frr-orig" / "keep").read_text() == "manual checkout\n"
        )
        return result, clones, (devel / "frr").is_dir(), orig_unchanged


def test_missing_frr_clones_official_repository_once():
    result, clones, found_frr, orig_unchanged = _run_setup()
    assert result.returncode == 0, result.stderr
    assert len(clones) == 1
    assert clones[0].startswith("init -q ")
    assert found_frr
    assert orig_unchanged


def test_existing_frr_does_not_clone_or_modify_manual_original():
    result, clones, found_frr, orig_unchanged = _run_setup(existing_frr=True, existing_orig=True)
    assert result.returncode == 0, result.stderr
    assert clones == []
    assert found_frr
    assert orig_unchanged


def test_new_frr_does_not_modify_manual_original():
    result, clones, found_frr, orig_unchanged = _run_setup(existing_orig=True)
    assert result.returncode == 0, result.stderr
    assert len(clones) == 1
    assert found_frr
    assert orig_unchanged


def test_skip_clone_fails_when_frr_missing_without_touching_original():
    result, clones, found_frr, orig_unchanged = _run_setup(existing_orig=True, skip_clone=True)
    assert result.returncode != 0
    assert "FRR checkout not found" in result.stderr
    assert clones == []
    assert not found_frr
    assert orig_unchanged

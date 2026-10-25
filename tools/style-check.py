#!/usr/bin/env python3
"""Read-only source style check for the working tree or a committed range."""

# Copyright (C) 2026 Donnie V. Savage

import argparse
import difflib
import re
import shutil
import subprocess
import sys
from pathlib import Path


C_SUFFIXES = {".c", ".h", ".cc", ".cpp", ".hpp"}
TEXT_SUFFIXES = C_SUFFIXES | {
    ".py", ".sh", ".bash", ".yaml", ".yml", ".md", ".json",
    ".toml", ".ini", ".mk", ".txt",
}
TEXT_NAMES = {
    "Makefile", "Dockerfile", "README", ".clang-format", ".gitignore",
    ".gitattributes", ".dockerignore", "CONTRIBUTING", "LICENSE",
    "SECURITY.md", "tools/python", "docker/docker-start", "docker/daemons",
}
UNSAFE_APIS = {
    "sprintf": "use snprintf() with an explicit buffer size",
    "vsprintf": "use vsnprintf() with an explicit buffer size",
    "strcpy": "use a bounded copy with verified termination",
    "strcat": "use a bounded append with verified capacity",
    "gets": "use fgets() with an explicit buffer size",
}
API_CALL = re.compile(r"\b(" + "|".join(UNSAFE_APIS) + r"|snprintf)\s*\(")
HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")
MAX_FORMAT_REPORTS = 8
CLANG_FORMAT_VERSION = "23.1.4"


def git(root, *args):
    result = subprocess.run(["git", "-C", str(root), *args],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if result.returncode:
        raise RuntimeError(result.stderr.decode(errors="replace").strip())
    return result.stdout


def checked(path):
    name = Path(path).name
    return (Path(path).suffix.lower() in TEXT_SUFFIXES or name in TEXT_NAMES
            or path in TEXT_NAMES)


def paths_from_git(data):
    return [p.decode("utf-8", errors="surrogateescape")
            for p in data.split(b"\0") if p]


def diff_lines(data):
    """Return new-file line numbers per path for a -U0 Git diff."""
    found = {}
    path = None
    for line in data.decode("utf-8", errors="surrogateescape").splitlines():
        if line.startswith("+++ b/"):
            path = line[6:]
            if checked(path):
                found.setdefault(path, set())
            else:
                path = None
        elif path is not None:
            match = HUNK.match(line)
            if match:
                first = int(match.group(1))
                count = int(match.group(2) or "1")
                found[path].update(range(first, first + count))
    return {path: lines for path, lines in found.items() if lines}


def changed(root, *args):
    return diff_lines(git(root, "-c", "core.quotePath=false", "diff",
                          "--no-ext-diff", "--no-renames", "--no-color",
                          "--unified=0", "--diff-filter=ACMRT", *args))


def c_code_only(source):
    """Mask C comments and quoted literals without losing line locations."""
    out = list(source)
    state = "code"
    i = 0
    while i < len(source):
        c = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ""
        if state == "code":
            if c == "/" and nxt == "/":
                out[i] = out[i + 1] = " "
                state = "line"
                i += 2
                continue
            if c == "/" and nxt == "*":
                out[i] = out[i + 1] = " "
                state = "block"
                i += 2
                continue
            if c in ('"', "'"):
                out[i] = " "
                state = c
        elif state == "line":
            if c == "\n":
                state = "code"
            else:
                out[i] = " "
        elif state == "block":
            if c == "*" and nxt == "/":
                out[i] = out[i + 1] = " "
                state = "code"
                i += 2
                continue
            if c != "\n":
                out[i] = " "
        else:
            if c == "\\" and nxt:
                out[i] = " "
                if nxt != "\n":
                    out[i + 1] = " "
                i += 2
                continue
            if c == state:
                state = "code"
            if c != "\n":
                out[i] = " "
        i += 1
    return "".join(out)


def line_ranges(lines):
    if not lines:
        return []
    numbers = sorted(lines)
    start = end = numbers[0]
    result = []
    for value in numbers[1:]:
        if value != end + 1:
            result.append((start, end))
            start = value
        end = value
    result.append((start, end))
    return result


def formatting_errors(root, path, contents, lines, formatter):
    if not formatter:
        return [f"{path}: clang-format is required for C source; install it and retry"]
    argv = [formatter, "--style=file", "--assume-filename=" + str(root / path)]
    if lines is not None:
        argv.extend(f"--lines={start}:{end}" for start, end in line_ranges(lines))
    result = subprocess.run(argv, input=contents, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, cwd=root)
    if result.returncode:
        detail = result.stderr.decode(errors="replace").strip()
        return [f"{path}: clang-format failed: {detail or result.returncode}"]
    if result.stdout == contents:
        return []
    old = contents.decode("utf-8", errors="replace").splitlines(keepends=True)
    new = result.stdout.decode("utf-8", errors="replace").splitlines(keepends=True)
    violations = []
    for operation, i1, i2, _, _ in difflib.SequenceMatcher(
            a=old, b=new, autojunk=False).get_opcodes():
        if operation == "equal":
            continue
        if lines is not None and not (set(range(i1 + 1, i2 + 1)) & lines or
                                      (i1 == i2 and (i1 in lines or i1 + 1 in lines))):
            continue
        violations.append(f"{path}:{i1 + 1}: clang-format mismatch")
        if len(violations) == MAX_FORMAT_REPORTS:
            violations.append(f"{path}: additional formatting differences omitted")
            break
    return violations


def check_source(root, path, contents, selected, formatter):
    if b"\0" in contents:
        return []
    try:
        source = contents.decode("utf-8")
    except UnicodeDecodeError:
        return [f"{path}: source is not UTF-8"]
    lines = source.splitlines(keepends=True)
    wanted = set(range(1, len(lines) + 1)) if selected is None else selected
    violations = []
    for number in sorted(wanted):
        if number > len(lines):
            continue
        line = lines[number - 1].removesuffix("\n")
        if line.rstrip(" \t\r") != line.rstrip("\r"):
            violations.append(f"{path}:{number}: trailing whitespace")
    if (lines and not source.endswith("\n") and
            (selected is None or len(lines) in wanted)):
        violations.append(f"{path}:{len(lines)}: missing newline at EOF")
    if Path(path).suffix.lower() in C_SUFFIXES:
        masked = c_code_only(source)
        for match in API_CALL.finditer(masked):
            number = masked.count("\n", 0, match.start()) + 1
            if number not in wanted:
                continue
            name = match.group(1)
            if name == "snprintf":
                if path.startswith("frr/code/"):
                    violations.append(f"{path}:{number}: snprintf() in FRR adapter; use snprintfrr()")
            else:
                guidance = UNSAFE_APIS[name]
                if name == "sprintf" and path.startswith("frr/code/"):
                    guidance = "use snprintfrr() with an explicit buffer size"
                violations.append(f"{path}:{number}: {name}() is prohibited: {guidance}")
        violations += formatting_errors(root, path, contents, selected, formatter)
    return violations


def snapshots(root, mode, base):
    """Yield (label, path, contents, changed lines), including index and worktree."""
    if mode == "all":
        paths = paths_from_git(git(root, "ls-files", "--cached", "--others",
                                   "--exclude-standard", "-z"))
        for path in sorted(set(paths)):
            file = root / path
            if checked(path) and file.is_file() and not file.is_symlink():
                yield "tree", path, file.read_bytes(), None
        return
    if base:
        for path, lines in sorted(changed(root, base, "HEAD").items()):
            yield "commit", path, git(root, "show", "HEAD:" + path), lines
        return
    for path, lines in sorted(changed(root, "--cached").items()):
        yield "staged", path, git(root, "show", ":" + path), lines
    for path, lines in sorted(changed(root).items()):
        file = root / path
        if file.is_file() and not file.is_symlink():
            yield "working", path, file.read_bytes(), lines
    for path in sorted(paths_from_git(git(root, "ls-files", "--others",
                                          "--exclude-standard", "-z"))):
        file = root / path
        if checked(path) and file.is_file() and not file.is_symlink():
            yield "untracked", path, file.read_bytes(), None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--all", action="store_true", help="check entire source tree")
    selection.add_argument("--base", metavar="REV", help="check changes from REV to HEAD (CI)")
    args = parser.parse_args()
    root = Path(__file__).resolve().parent.parent
    mode = "all" if args.all else "changes"
    try:
        git(root, "rev-parse", "--is-inside-work-tree")
        formatter = shutil.which("clang-format")
        violations = []
        count = 0
        version_checked = False
        for label, path, contents, lines in snapshots(root, mode, args.base):
            count += 1
            if Path(path).suffix.lower() in C_SUFFIXES:
                if not formatter:
                    raise RuntimeError("clang-format not found; install it before checking C source")
                if not version_checked:
                    result = subprocess.run([formatter, "--version"], capture_output=True,
                                            text=True, check=False)
                    expected = "clang-format version " + CLANG_FORMAT_VERSION
                    found = result.stdout.strip()
                    if result.returncode or not (found == expected or
                                                  found.startswith(expected + " ")):
                        raise RuntimeError(f"expected {expected}; found "
                                           f"{found or result.stderr.strip()}")
                    version_checked = True
            errors = check_source(root, path, contents, lines, formatter)
            violations += [f"[{label}] {error}" for error in errors]
    except (OSError, RuntimeError) as error:
        print(f"style-check: {error}", file=sys.stderr)
        return 2
    for violation in violations:
        print(violation)
    if violations:
        print(f"style-check: FAIL ({len(violations)} reported violations; {count} snapshots checked)")
        return 1
    print(f"style-check: PASS ({count} snapshots checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

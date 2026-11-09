#!/usr/bin/env python3
"""Apply clang-format to changed source lines, or to the entire source tree."""

# Copyright (C) 2026 Donnie V. Savage

import argparse
import re
import runpy
import subprocess
import sys
from pathlib import Path

# Use the same Git diff and formatter selection rules as the read-only checker.
STYLE = runpy.run_path(str(Path(__file__).with_name("style-check.py")))

EXTENSIONS = {
    ".c", ".h", ".cpp", ".hpp", ".cc", ".hh", ".cxx", ".hxx",
    ".m", ".mm", ".java", ".js", ".jsx", ".ts", ".tsx",
    ".proto", ".cu", ".cuh",
}


def get_git_root():
    result = subprocess.run(["git", "rev-parse", "--show-toplevel"],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            text=True)
    if result.returncode:
        raise RuntimeError("not inside a git repository")
    return Path(result.stdout.strip())


def source_files(root, all_files):
    """Map source paths to changed lines; None requests full-file formatting."""
    if all_files:
        paths = STYLE["paths_from_git"](STYLE["git"](
            root, "ls-files", "--cached", "--others", "--exclude-standard", "-z"))
        selected = dict.fromkeys(paths)
    else:
        # Diff HEAD against the working tree: staged and unstaged changes share
        # the same working-tree line numbers, including newly staged files.
        selected = STYLE["changed"](root, "HEAD")
        untracked = STYLE["paths_from_git"](STYLE["git"](
            root, "ls-files", "--others", "--exclude-standard", "-z"))
        selected.update(dict.fromkeys(untracked))

    return {
        path: lines for path, lines in sorted(selected.items())
        if Path(path).suffix.lower() in EXTENSIONS
        and (root / path).is_file() and not (root / path).is_symlink()
    }


def run_clang_format(root, files):
    if not files:
        print("No source files need formatting.")
        return

    formatter = STYLE["find_formatter"]()
    if not formatter:
        raise RuntimeError("clang-format not found; install it or set CLANG_FORMAT")
    version = subprocess.run([formatter, "--version"],
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                             text=True)
    if version.returncode or not re.search(
            r"\bclang-format version \d+", version.stdout):
        raise RuntimeError(f"invalid clang-format executable {formatter}: "
                           f"{version.stdout.strip() or version.stderr.strip() or version.returncode}")

    print(f"style-fix: using {version.stdout.strip()} ({formatter})")
    print(f"Formatting {len(files)} source file(s)...")

    # Whole-file formatting can be batched, avoiding one process per file when
    # --all is used on a large repository.
    if all(lines is None for lines in files.values()):
        paths = list(files)
        for index in range(0, len(paths), 100):
            batch = paths[index:index + 100]
            argv = [formatter, "-i", "--style=file"]
            argv.extend(str(root / path) for path in batch)
            result = subprocess.run(argv, cwd=root)
            if result.returncode:
                raise RuntimeError(f"clang-format failed near {batch[0]} "
                                   f"(exit code {result.returncode})")
        return

    for path, lines in files.items():
        argv = [formatter, "-i", "--style=file"]
        if lines is not None:
            argv.extend(f"--lines={start}:{end}" for start, end in
                        STYLE["line_ranges"](lines))
        argv.append(str(root / path))
        result = subprocess.run(argv, cwd=root)
        if result.returncode:
            raise RuntimeError(f"clang-format failed for {path} "
                               f"(exit code {result.returncode})")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--changed", action="store_true",
                           help="format changed lines and untracked source files (default)")
    selection.add_argument("--all", action="store_true",
                           help="format all tracked and untracked source files")
    args = parser.parse_args()

    try:
        root = get_git_root()
        run_clang_format(root, source_files(root, args.all))
    except (OSError, RuntimeError) as error:
        print(f"style-fix: {error}", file=sys.stderr)
        return 2
    print("Done. Restage any previously staged files before style-check.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

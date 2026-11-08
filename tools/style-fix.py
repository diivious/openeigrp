#!/usr/bin/env python3
"""
Run clang-format -i on all tracked source files in the current git repository.
"""

# Copyright (C) 2026 Donnie V. Savage

import subprocess
import sys
from pathlib import Path

# File extensions clang-format typically understands.
# Edit this set as needed for your project.
EXTENSIONS = {
    ".c", ".h",
    ".cpp", ".hpp", ".cc", ".hh", ".cxx", ".hxx",
    ".m", ".mm",
    ".java",
    ".js", ".jsx", ".ts", ".tsx",
    ".proto",
    ".cu", ".cuh",
}

def get_git_root() -> Path:
    """Return the absolute path to the git repository root."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except subprocess.CalledProcessError:
        print("Error: Not inside a git repository.", file=sys.stderr)
        sys.exit(1)

def get_tracked_source_files(root: Path) -> list[Path]:
    """Return a list of tracked files under root that match EXTENSIONS."""
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=root,
        capture_output=True,
        check=True,
    )
    # Null-terminated output handles spaces and special characters safely
    raw_files = result.stdout.split(b"\0")
    files = []
    for raw in raw_files:
        if not raw:
            continue
        rel_path = raw.decode("utf-8", errors="surrogateescape")
        if Path(rel_path).suffix.lower() in EXTENSIONS:
            files.append(root / rel_path)
    return files

def run_clang_format(files: list[Path]) -> None:
    """Run clang-format -i on the given files, batching to avoid ARG_MAX issues."""
    if not files:
        print("No matching source files found.")
        return

    print(f"Formatting {len(files)} file(s)...")

    batch_size = 100
    for i in range(0, len(files), batch_size):
        batch = [str(f) for f in files[i : i + batch_size]]
        try:
            subprocess.run(["clang-format", "-i", *batch], check=True)
        except FileNotFoundError:
            print("Error: clang-format not found in PATH.", file=sys.stderr)
            sys.exit(1)
        except subprocess.CalledProcessError as e:
            print(f"clang-format failed (exit code {e.returncode})", file=sys.stderr)
            sys.exit(e.returncode)

def main() -> None:
    root = get_git_root()
    files = get_tracked_source_files(root)
    run_clang_format(files)
    print("Done.")

if __name__ == "__main__":
    main()

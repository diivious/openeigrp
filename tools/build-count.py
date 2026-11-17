#!/usr/bin/env python3
# SPDX-License-Identifier: ISC
# Copyright (C) 2026 Donnie V. Savage
"""Allocate a single, persistent local build number with process-safe locking."""

import argparse
import fcntl
from pathlib import Path
import re


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("next", "current", "reset"))
    parser.add_argument("--base", default="")
    parser.add_argument("--repo", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    if args.action != "reset" and not re.fullmatch(r"[0-9]+\.[0-9]+", args.base):
        parser.error("--base MAJOR.MINOR is required")
    state = args.repo / ".build-count"
    lock = args.repo / ".build-count.lock"
    with lock.open("a+") as locked:
        fcntl.flock(locked, fcntl.LOCK_EX)
        if args.action == "reset":
            state.unlink(missing_ok=True)
            return
        saved = state.read_text().strip().split() if state.exists() else []
        if saved and (len(saved) != 2 or not saved[1].isascii() or not saved[1].isdecimal()):
            parser.error("invalid .build-count (run make distclean to reset)")
        if args.action == "current" and (not saved or saved[0] != args.base):
            parser.error("no build recorded for this release; build an image first")
        number = int(saved[1]) if saved and saved[0] == args.base else 0
        if args.action == "next":
            number += 1
            # The persistent lock is separate so os.replace() does not invalidate it.
            temporary = state.with_suffix(".tmp")
            temporary.write_text(f"{args.base} {number}\n")
            temporary.replace(state)
        print(number)


if __name__ == "__main__":
    main()

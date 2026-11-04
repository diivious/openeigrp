# SPDX-License-Identifier: GPL-2.0-or-later
# Copyright (C) 2026 Donnie V. Savage
"""Clock-injectable polling for test-side UUT state observations.

Only scenario polling uses this clock. UUT processes, transport response
watchdogs, the wire broker, and protocol timers retain their own real clocks.
"""
import time


class MonotonicClock:
    def now(self):
        return time.monotonic()

    def sleep(self, seconds):
        time.sleep(seconds)


class ManualClock:
    """Virtual time for tests: no wall-clock waits or global monkeypatches."""

    def __init__(self, start=0.0):
        self.elapsed = float(start)
        self.sleeps = []

    def now(self):
        return self.elapsed

    def advance(self, seconds):
        if seconds < 0:
            raise ValueError("cannot reverse monotonic time")
        self.elapsed += seconds

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.advance(seconds)


class Poller:
    """Poll one coherent predicate result at a time through a hard deadline."""

    def __init__(self, clock=None, interval=0.1):
        if interval <= 0:
            raise ValueError("poll interval must be positive")
        self.clock = clock if clock is not None else MonotonicClock()
        self.interval = interval

    def until(self, check, timeout, stable_for=0):
        """Require a successful poll, optionally sustained over stable_for.

        The clock is read after the predicate (including its STATE snapshots),
        so a slow or blocked sample cannot be credited to an earlier instant.
        Reaching the deadline permits a final sample, but never a late pass.
        """
        start = self.clock.now()
        deadline = start + timeout
        stable_since = None
        while True:
            if self.clock.now() > deadline:
                return False
            ok = check()
            now = self.clock.now()
            if now > deadline:
                return False
            if ok:
                if stable_since is None:
                    stable_since = now
                if now >= stable_since + stable_for:
                    return True
            else:
                stable_since = None
            if now >= deadline:
                return False
            interval = min(self.interval, deadline - now)
            if stable_since is not None and stable_for:
                interval = min(interval, max(0.0, stable_since + stable_for - now))
            if interval <= 0:
                # Floating-point rounding near a boundary must not spin.
                return False
            self.clock.sleep(interval)

    def throughout(self, check, duration):
        """Require the predicate at every sample, including at the boundary."""
        deadline = self.clock.now() + duration
        while True:
            if not check():
                return False
            now = self.clock.now()
            if now >= deadline:
                return True
            self.clock.sleep(min(self.interval, deadline - now))

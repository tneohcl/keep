"""The acceptance harness only counts a mid-write interruption as exercised
when borg create had really written data (review of #6)."""
import importlib.util
from pathlib import Path
import signal
import unittest
from unittest.mock import patch

_SPEC = importlib.util.spec_from_file_location(
    "keep_acceptance", Path(__file__).resolve().parent / "packaging" / "acceptance.py")
acceptance = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(acceptance)


class Clock:
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now

    def time(self):
        return self.now

    def sleep(self, seconds):
        self.now += 10          # a stalled run: time passes quickly


class Process:
    pid = 4242

    def __init__(self, exits_after=None):
        self.polls, self.exits_after, self.waited = 0, exits_after, False

    def poll(self):
        self.polls += 1
        return 0 if self.exits_after is not None and self.polls > self.exits_after else None

    def wait(self, timeout=None):
        self.waited = True
        return -9


class Box:
    def wait_for(self, text, process, timeout=120):
        return True


class StrikeRequiresWrites(unittest.TestCase):
    def strike(self, process, written):
        actions, kills = [], []
        sizes = iter(written)
        last = [0]

        def du(_path):
            last[0] = next(sizes, last[0])
            return last[0]
        clock = Clock()
        with patch.object(acceptance, "du", du), patch.object(acceptance, "time", clock), \
                patch.object(acceptance.os, "killpg", lambda pid, sig: kills.append((pid, sig))):
            struck = acceptance.strike_after_create_starts(Box(), process, lambda: actions.append(1),
                                                           40 << 20, "/watched")
        return struck, actions, kills

    def test_no_writes_before_the_deadline_is_not_a_mid_write_strike(self):
        process = Process()
        struck, actions, kills = self.strike(process, [0])            # nothing is ever written
        self.assertFalse(struck)
        self.assertEqual(actions, [])
        self.assertEqual(kills, [(process.pid, signal.SIGKILL)])      # the stalled run is stopped...
        self.assertTrue(process.waited)                               # ...and reaped

    def test_strikes_once_enough_has_been_written(self):
        struck, actions, kills = self.strike(Process(), [0, 10 << 20, 50 << 20])
        self.assertTrue(struck)
        self.assertEqual((actions, kills), ([1], []))

    def test_a_run_that_ended_on_its_own_is_not_struck(self):
        struck, actions, kills = self.strike(Process(exits_after=1), [0])
        self.assertFalse(struck)
        self.assertEqual((actions, kills), ([], []))


if __name__ == "__main__":
    unittest.main()

"""What this tool costs to use, asserted rather than assumed.

Two budgets, both of which creep one helpful addition at a time:

**Time.** `wb status`, `wb next` and `wb route` are what a session runs first,
often several times. Past roughly a fifth of a second they stop reading as
instant and start reading as a tool that has to be tolerated, which is how a
step gets skipped. They read artifacts off disk and must never call a tracker,
so the budget is generous on purpose -- it catches a *change in kind*, like
somebody adding a network call or an unbounded directory walk, not ordinary
variance on a slow machine.

**Payload.** A triage that grew from 1.4 KB to 15 KB costs tokens in every
session that reads one, and nothing else in the suite would notice.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import support  # noqa: E402
import wb  # noqa: E402

# The budget is here to catch a *change in kind* -- a git call per ticket, a
# network call, an unbounded directory walk -- and never to grade a busy box.
# A timing test that fails on load teaches people to rerun the suite until it
# passes, which costs more than the check is worth.
#
# So the kind is asserted deterministically: an opening command starts at most
# MAX_SPAWNS processes, and as many with sixty tickets as with twenty. The
# wall-clock ceiling only backs that up, and it is priced in process starts.
# A fixed 0.75 s was sized on a machine where `wb status` took 50ms; on Windows
# one git start costs 40-55ms, `wb status` makes 13 of them whatever the ticket
# count, and a cold run failed the fixed number with no code changed (WB-50).
MAX_SPAWNS = 16
# The floor for everything that is not a process start, and the ceiling's floor:
# a fast box never gets a tighter bar than the fixed number it replaces.
NON_SPAWN_SECONDS = 0.25
OPENING_COMMAND = 0.75
# Best of five: a scheduling hiccup during one attempt must not fail a build.
ATTEMPTS = 5


def _spawn_seconds() -> float:
    """What starting one git process costs on this machine, right now."""
    best = float("inf")
    for _ in range(ATTEMPTS):
        started = time.perf_counter()
        subprocess.run(["git", "--version"], capture_output=True, timeout=15, check=False)
        best = min(best, time.perf_counter() - started)
    return best


def ceiling(spawn_seconds: float) -> float:
    return max(OPENING_COMMAND, NON_SPAWN_SECONDS + MAX_SPAWNS * spawn_seconds)


class OpeningCommands(unittest.TestCase):
    """The commands a session runs before doing anything else."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(lambda: os.chdir(self._cwd))

        for name, value in (("WORKBENCH_HOME", str(self.root / "home")), ("WORKBENCH_NO_EVENTS", "1")):
            previous = os.environ.get(name)
            os.environ[name] = value
            self.addCleanup(self._restore, name, previous)

        patcher = mock.patch("workbench.gitctx.repo_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

        # Twenty tickets: more than a checkout normally carries, so the budget
        # is measured against a bad case rather than an empty one.
        self._tickets(range(20))

    def _tickets(self, indexes) -> None:
        for index in indexes:
            directory = self.root / ".workflow" / f"ABC-{index}"
            directory.mkdir(parents=True)
            (directory / "triage.json").write_text(json.dumps({"title": f"ticket {index}"}), encoding="utf-8")
            (directory / "sdd.json").write_text(
                json.dumps({"key": f"ABC-{index}", "files": [{"path": "src/a.py"}], "verify": ["pytest"]}),
                encoding="utf-8",
            )

    def _restore(self, name: str, previous) -> None:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous

    def _fastest(self, *argv: str) -> float:
        best = float("inf")
        for _ in range(ATTEMPTS):
            started = time.perf_counter()
            with redirect_stdout(io.StringIO()):
                wb.main(list(argv))
            best = min(best, time.perf_counter() - started)
        return best

    def _spawns(self, *argv: str) -> int:
        real = subprocess.run
        count = 0

        def counting(*args, **kwargs):
            nonlocal count
            count += 1
            return real(*args, **kwargs)

        with mock.patch("subprocess.run", counting), redirect_stdout(io.StringIO()):
            wb.main(list(argv))
        return count

    def _assert_instant(self, name: str, elapsed: float) -> None:
        # Priced now, in the same run: a cold machine raises the spawn cost and
        # the ceiling together, so only a change in the command can fail this.
        spawn = _spawn_seconds()
        limit = ceiling(spawn)
        self.assertLess(
            elapsed, limit, f"wb {name} took {elapsed:.3f}s; ceiling {limit:.3f}s at {spawn * 1000:.0f}ms per spawn"
        )

    def test_status_is_instant(self) -> None:
        self._assert_instant("status", self._fastest("status"))

    def test_next_is_instant(self) -> None:
        with mock.patch("workbench.gitctx.branch", return_value="feature/ABC-1-thing"):
            elapsed = self._fastest("next")
        self._assert_instant("next", elapsed)

    def test_route_is_instant(self) -> None:
        self._assert_instant("route", self._fastest("route", "ABC-1"))

    def test_the_opening_commands_start_a_bounded_number_of_processes(self) -> None:
        with mock.patch("workbench.gitctx.branch", return_value="feature/ABC-1-thing"):
            spawns = {"status": self._spawns("status"), "next": self._spawns("next")}
        spawns["route"] = self._spawns("route", "ABC-1")
        for name, count in spawns.items():
            self.assertLessEqual(count, MAX_SPAWNS, f"wb {name} started {count} processes")

    def test_status_starts_no_process_per_ticket(self) -> None:
        with_twenty = self._spawns("status")
        self._tickets(range(20, 60))
        self.assertEqual(with_twenty, self._spawns("status"), "wb status started more processes for more tickets")

    def test_the_ceiling_is_priced_in_process_starts_and_never_below_the_old_bar(self) -> None:
        self.assertEqual(OPENING_COMMAND, ceiling(0.001))
        self.assertGreater(ceiling(0.050), ceiling(0.040))
        self.assertAlmostEqual(NON_SPAWN_SECONDS + MAX_SPAWNS * 0.05, ceiling(0.05))

    def test_the_opening_commands_never_reach_the_network(self) -> None:
        """The reason they can be fast, and the reason they work with no
        credential configured: they read artifacts and nothing else."""
        from workbench import http

        with mock.patch.object(http, "request", side_effect=AssertionError("opening commands must not call out")):
            with redirect_stdout(io.StringIO()), mock.patch("workbench.gitctx.branch", return_value=None):
                wb.main(["status"])
                wb.main(["next"])
                wb.main(["route", "ABC-1"])


class PayloadSize(unittest.TestCase):
    """A triage is read by every skill downstream. Its size is a running cost."""

    # The README quotes ~1.4 KB for a typical triage. Doubling that is a
    # regression worth failing over; the ceiling leaves room for a wordy ticket.
    TRIAGE_BYTES = 4096

    def test_a_normalised_task_stays_small(self) -> None:
        provider = support.FakeJira()
        with mock.patch("workbench.gitctx.repo_root", return_value=Path(tempfile.gettempdir())):
            payload = provider.get_task("ABC-123", depth=1, requested=[], use_cache=False)

        size = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        self.assertLess(size, self.TRIAGE_BYTES, f"a triage payload is {size} bytes")

    def test_depth_actually_bounds_the_payload(self) -> None:
        """Depth is the knob that keeps this bounded; if a deeper read is not
        bigger, something is ignoring it, and if it is unbounded, the cap is
        decorative."""
        provider = support.FakeJira()
        with mock.patch("workbench.gitctx.repo_root", return_value=Path(tempfile.gettempdir())):
            shallow = provider.get_task("ABC-123", depth=0, requested=[], use_cache=False)
            deep = provider.get_task("ABC-123", depth=1, requested=[], use_cache=False)

        deep_size = len(json.dumps(deep).encode("utf-8"))
        self.assertGreaterEqual(deep_size, len(json.dumps(shallow).encode("utf-8")))
        self.assertLess(deep_size, self.TRIAGE_BYTES * 4, f"a depth-1 read is {deep_size} bytes")


if __name__ == "__main__":
    unittest.main()

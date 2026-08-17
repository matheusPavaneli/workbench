"""The command history behind `wb status --stats`.

The snapshot cannot distinguish a plan that passed its audit first time from
one that passed on the fifth attempt, because artifacts are overwritten. The
log exists for that difference, and it is held to two rules: it records
outcomes and never arguments, and it never becomes the reason a command fails.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workbench import events


class EventsBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(lambda: os.chdir(self._cwd))

        patcher = mock.patch("workbench.gitctx.repo_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

        os.environ.pop("WORKBENCH_NO_EVENTS", None)


class Recording(EventsBase):
    def test_a_tracked_command_is_recorded(self) -> None:
        events.record("sdd", "audit", "ABC-1", 0, 12)
        entries = events.read()
        self.assertEqual(1, len(entries))
        self.assertEqual("audit", entries[0]["action"])
        self.assertEqual("ABC-1", entries[0]["key"])

    def test_an_inspection_command_is_not_recorded(self) -> None:
        """Logging every status would drown the signal in looking at it."""
        events.record("status", "", None, 0, 3)
        events.record("ctx", "show", None, 0, 3)
        self.assertEqual([], events.read())

    def test_a_failure_is_recorded_with_its_exit_code(self) -> None:
        events.record("sdd", "audit", "ABC-1", 7, 40)
        self.assertEqual(7, events.read()[0]["exit"])

    def test_nothing_but_the_outcome_is_stored(self) -> None:
        """Arguments and output are where a secret or a customer name ends up."""
        events.record("impl", "verify", "ABC-1", 0, 5)
        self.assertEqual({"at", "group", "action", "exit", "ms", "key"}, set(events.read()[0]))

    def test_it_can_be_turned_off(self) -> None:
        os.environ["WORKBENCH_NO_EVENTS"] = "1"
        self.addCleanup(lambda: os.environ.pop("WORKBENCH_NO_EVENTS", None))
        events.record("sdd", "audit", "ABC-1", 0, 1)
        self.assertEqual([], events.read())


class NeverLoadBearing(EventsBase):
    def test_an_unwritable_log_is_not_a_failure(self) -> None:
        with mock.patch("pathlib.Path.open", side_effect=OSError("read-only")):
            events.record("sdd", "audit", "ABC-1", 0, 1)  # must not raise

    def test_a_missing_log_reads_as_empty(self) -> None:
        self.assertEqual([], events.read())

    def test_a_malformed_line_is_skipped_not_fatal(self) -> None:
        events.record("sdd", "audit", "ABC-1", 0, 1)
        with events.path().open("a", encoding="utf-8") as handle:
            handle.write("{not json\n")
        self.assertEqual(1, len(events.read()))

    def test_the_log_is_capped(self) -> None:
        payload = json.dumps({"group": "sdd", "action": "audit", "exit": 0, "ms": 1})
        events.path().parent.mkdir(parents=True, exist_ok=True)
        events.path().write_text("\n".join([payload] * (events.MAX_EVENTS + 50)) + "\n", encoding="utf-8")
        events.record("sdd", "audit", "ABC-1", 0, 1)
        self.assertLessEqual(len(events.read()), events.MAX_EVENTS)


class Summary(unittest.TestCase):
    def _log(self, *pairs):
        return [{"group": g, "action": a, "exit": e, "ms": 10} for g, a, e in pairs]

    def test_it_counts_runs_and_failures_per_command(self) -> None:
        summary = events.summarise(self._log(("sdd", "audit", 7), ("sdd", "audit", 0), ("impl", "verify", 0)))
        self.assertEqual(2, summary["commands"]["sdd audit"]["runs"])
        self.assertEqual(1, summary["commands"]["sdd audit"]["failed"])
        self.assertEqual(0, summary["commands"]["impl verify"]["failed"])

    def test_it_names_the_step_that_fails_then_passes(self) -> None:
        """The expensive stage the snapshot cannot see."""
        summary = events.summarise(self._log(("sdd", "audit", 7), ("sdd", "audit", 7), ("sdd", "audit", 0)))
        self.assertEqual("sdd audit", summary["most_retried"])

    def test_a_command_that_only_ever_failed_is_not_a_retry(self) -> None:
        summary = events.summarise(self._log(("impl", "verify", 1)))
        self.assertEqual("", summary["most_retried"])

    def test_an_empty_history_renders_without_failing(self) -> None:
        self.assertEqual("no history yet", events.render(events.summarise([])))


if __name__ == "__main__":
    unittest.main()

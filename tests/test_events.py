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

        # The global log lives under the home directory, so without this every
        # run of this suite would append to the developer's own history.
        previous = os.environ.get("WORKBENCH_HOME")
        os.environ["WORKBENCH_HOME"] = str(self.root / "home")
        self.addCleanup(self._restore_home, previous)

    def _restore_home(self, previous) -> None:
        if previous is None:
            os.environ.pop("WORKBENCH_HOME", None)
        else:
            os.environ["WORKBENCH_HOME"] = previous


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

    def test_a_local_log_reports_no_checkouts(self) -> None:
        """Nothing in a per-repo log knows which repo it is; the section is skipped."""
        summary = events.summarise(self._log(("sdd", "audit", 0)))
        self.assertEqual({}, summary["repos"])
        self.assertNotIn("by checkout", events.render(summary))

    def test_it_separates_a_bad_stage_from_a_bad_checkout(self) -> None:
        """The one thing a per-repo log structurally cannot answer."""
        entries = [
            {"group": "sdd", "action": "audit", "exit": 7, "ms": 10, "repo": "billing"},
            {"group": "sdd", "action": "audit", "exit": 7, "ms": 10, "repo": "billing"},
            {"group": "sdd", "action": "audit", "exit": 0, "ms": 10, "repo": "docs"},
        ]
        summary = events.summarise(entries)
        self.assertEqual({"runs": 2, "failed": 2}, summary["repos"]["billing"])
        self.assertEqual({"runs": 1, "failed": 0}, summary["repos"]["docs"])
        self.assertIn("by checkout", events.render(summary))


class Everywhere(EventsBase):
    """The history kept once per machine rather than once per checkout."""

    def test_one_command_lands_in_both_logs(self) -> None:
        events.record("sdd", "audit", "ABC-1", 0, 12)
        self.assertEqual(1, len(events.read()))
        self.assertEqual(1, len(events.read(everywhere=True)))

    def test_only_the_global_copy_says_which_checkout(self) -> None:
        events.record("sdd", "audit", "ABC-1", 0, 12)
        self.assertNotIn("repo", events.read()[0])
        self.assertEqual(self.root.name, events.read(everywhere=True)[0]["repo"])

    def test_two_checkouts_accumulate_in_one_place(self) -> None:
        events.record("sdd", "audit", "ABC-1", 7, 12)
        with mock.patch("workbench.gitctx.repo_root", return_value=self.root / "other"):
            events.record("sdd", "audit", "ABC-2", 0, 12)

        summary = events.summarise(events.read(everywhere=True))
        self.assertEqual({self.root.name, "other"}, set(summary["repos"]))
        self.assertEqual(1, len(events.read()), "the local log still only holds this checkout")

    def test_turning_events_off_turns_both_off(self) -> None:
        os.environ["WORKBENCH_NO_EVENTS"] = "1"
        events.record("sdd", "audit", "ABC-1", 0, 12)
        self.assertEqual([], events.read())
        self.assertEqual([], events.read(everywhere=True))

    def test_the_global_log_is_capped_too(self) -> None:
        payload = json.dumps({"group": "sdd", "action": "audit", "exit": 0, "ms": 1})
        target = events.global_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("\n".join([payload] * (events.MAX_GLOBAL_EVENTS + 50)) + "\n", encoding="utf-8")

        events.record("sdd", "audit", "ABC-1", 0, 1)
        self.assertLessEqual(len(events.read(everywhere=True)), events.MAX_GLOBAL_EVENTS)

    def test_an_unwritable_global_log_never_fails_a_command(self) -> None:
        """The rule the whole module is held to, at the new write site."""
        with mock.patch("workbench.events.global_path", side_effect=OSError("no")):
            with self.assertRaises(OSError):
                events.global_path()
            events.record("sdd", "audit", "ABC-1", 0, 12)
        self.assertEqual(1, len(events.read()), "the local log was still written")


if __name__ == "__main__":
    unittest.main()

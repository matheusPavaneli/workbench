"""Resuming work: reading the pipeline back off the artifacts.

The artifacts were always on disk. Until ``wb status`` existed, nothing read
them back, so a session that had been cleared had to open four files and infer.
What is asserted here is that the inference is now the tool's job and that it
lands on the right next command.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workbench import status as status_lib


class StatusBase(unittest.TestCase):
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

        self.changed = mock.patch("workbench.gitctx.changed_files", return_value=[])
        self.changed.start()
        self.addCleanup(self.changed.stop)

    def write(self, key: str, name: str, data) -> None:
        path = self.root / ".workflow" / key / name
        path.parent.mkdir(parents=True, exist_ok=True)
        text = json.dumps(data) if not isinstance(data, str) else data
        path.write_text(text, encoding="utf-8")

    def stage(self, key: str, name: str):
        return next(s for s in status_lib.read(key).stages if s.name == name)


class Pipeline(StatusBase):
    def test_an_untouched_ticket_asks_for_triage(self) -> None:
        self.write("ABC-1", "notes.txt", "x")
        self.assertIn("task get ABC-1", status_lib.read("ABC-1").next_command)

    def test_triage_alone_asks_for_a_plan(self) -> None:
        self.write("ABC-1", "triage.json", {"title": "t", "type": "feature", "provider": "jira"})
        status = status_lib.read("ABC-1")
        self.assertEqual("ok", self.stage("ABC-1", "triage").state)
        self.assertIn("plan-change", status.next_command)

    def test_a_plan_with_no_audit_asks_for_the_audit(self) -> None:
        self.write("ABC-1", "triage.json", {"title": "t", "type": "feature"})
        self.write("ABC-1", "sdd.json", {"files": [{"path": "a.py"}], "steps": [1], "verify": ["pytest"]})
        self.assertIn("sdd audit ABC-1", status_lib.read("ABC-1").next_command)

    def test_a_failed_audit_blocks_and_says_to_fix_the_plan(self) -> None:
        self.write("ABC-1", "triage.json", {"title": "t", "type": "feature"})
        self.write("ABC-1", "sdd.json", {"files": [{"path": "a.py"}]})
        self.write("ABC-1", "audit.json", {"verdict": "fail", "citations_checked": 4, "citations_failed": 2})
        status = status_lib.read("ABC-1")
        self.assertEqual("audit", status.blocked.name)
        self.assertIn("fix the plan", status.next_command)

    def test_a_blocked_stage_wins_over_a_merely_unfinished_one(self) -> None:
        """A failure further up must not be hidden by the next empty stage."""
        self.write("ABC-1", "triage.json", {"title": "t", "type": "feature"})
        self.write("ABC-1", "sdd.json", {"files": [{"path": "a.py"}]})
        self.write("ABC-1", "audit.json", {"verdict": "fail", "citations_checked": 1, "citations_failed": 1})
        self.assertIn("fix the plan", status_lib.read("ABC-1").next_command)

    def test_verify_is_not_offered_before_the_audit_passes(self) -> None:
        self.write("ABC-1", "triage.json", {"title": "t", "type": "feature"})
        self.write("ABC-1", "sdd.json", {"files": [{"path": "a.py"}]})
        self.assertEqual("", self.stage("ABC-1", "verify").command)

    def test_a_passing_audit_opens_the_verify_stage(self) -> None:
        self.write("ABC-1", "triage.json", {"title": "t", "type": "feature"})
        self.write("ABC-1", "sdd.json", {"files": [{"path": "a.py"}], "steps": [1]})
        self.write("ABC-1", "audit.json", {"verdict": "pass", "citations_checked": 3})
        self.assertIn("impl verify ABC-1", self.stage("ABC-1", "verify").command)


class Scope(StatusBase):
    def _planned(self, key: str) -> None:
        self.write(key, "triage.json", {"title": "t", "type": "feature"})
        self.write(key, "sdd.json", {"files": [{"path": "a.py"}, {"path": "b.py"}]})
        self.write(key, "audit.json", {"verdict": "pass", "citations_checked": 2})

    def test_scope_is_read_from_the_working_tree_not_from_a_file(self) -> None:
        self._planned("ABC-1")
        self.changed.stop()
        with mock.patch("workbench.gitctx.changed_files", return_value=["a.py"]):
            stage = self.stage("ABC-1", "scope")
        self.changed.start()
        self.assertEqual(status_lib.PENDING, stage.state)
        self.assertIn("1 of 2", stage.detail)

    def test_a_file_outside_the_plan_blocks(self) -> None:
        self._planned("ABC-1")
        self.changed.stop()
        with mock.patch("workbench.gitctx.changed_files", return_value=["a.py", "elsewhere.py"]):
            stage = self.stage("ABC-1", "scope")
        self.changed.start()
        self.assertEqual(status_lib.FAIL, stage.state)


class Handover(StatusBase):
    def test_a_bug_owes_a_handover(self) -> None:
        self.write("ABC-1", "triage.json", {"title": "t", "type": "bug"})
        self.assertEqual(status_lib.TODO, self.stage("ABC-1", "handover").state)

    def test_a_feature_does_not(self) -> None:
        self.write("ABC-1", "triage.json", {"title": "t", "type": "feature"})
        self.assertEqual(status_lib.SKIP, self.stage("ABC-1", "handover").state)


class Robustness(StatusBase):
    def test_a_corrupt_artifact_reads_as_absent(self) -> None:
        """Status is what a stuck session runs; it must never be the thing that fails."""
        self.write("ABC-1", "triage.json", "{not json")
        self.assertIn("task get ABC-1", status_lib.read("ABC-1").next_command)

    def test_the_tasks_directory_is_not_a_ticket(self) -> None:
        path = self.root / ".workflow" / "tasks" / "WB-1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}", encoding="utf-8")
        self.assertEqual([], status_lib.keys())


class Aggregate(StatusBase):
    def test_it_counts_where_work_is_waiting(self) -> None:
        for key in ("ABC-1", "ABC-2"):
            self.write(key, "triage.json", {"title": "t", "type": "feature"})
        summary = status_lib.summarise([status_lib.read(k) for k in status_lib.keys()])
        self.assertEqual(2, summary["tickets"])
        self.assertEqual(2, summary["waiting_at"]["plan"])

    def test_a_failing_stage_is_counted_separately_from_an_unstarted_one(self) -> None:
        self.write("ABC-1", "triage.json", {"title": "t", "type": "feature"})
        self.write("ABC-1", "sdd.json", {"files": [{"path": "a.py"}]})
        self.write("ABC-1", "audit.json", {"verdict": "fail", "citations_checked": 1, "citations_failed": 1})
        summary = status_lib.summarise([status_lib.read("ABC-1")])
        self.assertEqual(1, summary["blocked_at"]["audit"])


if __name__ == "__main__":
    unittest.main()

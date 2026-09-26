"""Resuming work: reading the pipeline back off the artifacts.

The artifacts were always on disk. Until ``wb status`` existed, nothing read
them back, so a session that had been cleared had to open four files and infer.
What is asserted here is that the inference is now the tool's job and that it
lands on the right next command.
"""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from workbench import audit as audit_lib, status as status_lib


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

    def audited(self, key: str, plan: dict, **report) -> None:
        """A plan and the passing audit of exactly that plan."""
        self.write(key, "sdd.json", plan)
        self.write(key, "audit.json", {"verdict": "pass", "plan_sha256": audit_lib.digest(plan), **report})

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
        self.audited("ABC-1", {"files": [{"path": "a.py"}], "steps": [1]}, citations_checked=3)
        self.assertIn("impl verify ABC-1", self.stage("ABC-1", "verify").command)

    def test_a_plan_changed_after_its_audit_blocks_and_asks_for_the_audit(self) -> None:
        self.write("ABC-1", "triage.json", {"title": "t", "type": "feature"})
        self.audited("ABC-1", {"files": [{"path": "a.py"}], "steps": [1]}, citations_checked=3)
        self.write("ABC-1", "sdd.json", {"files": [{"path": "a.py"}, {"path": "b.py"}], "steps": [1]})
        status = status_lib.read("ABC-1")
        self.assertEqual("audit", status.blocked.name)
        self.assertIn("changed since", self.stage("ABC-1", "audit").detail)
        self.assertIn("sdd audit ABC-1", status.next_command)
        self.assertEqual("", self.stage("ABC-1", "verify").command)

    def test_one_failed_command_of_three_reads_as_one(self) -> None:
        """evidence.json carries exit_code; the count read a key it never writes."""
        self.write("ABC-1", "triage.json", {"title": "t", "type": "feature"})
        self.audited("ABC-1", {"files": [{"path": "a.py"}], "steps": [1]})
        results = [{"command": c, "exit_code": e} for c, e in (("a", 0), ("b", 1), ("c", 0))]
        self.write("ABC-1", "evidence.json", {"verdict": "fail", "results": results, "refused": []})
        self.assertEqual("1 failed", self.stage("ABC-1", "verify").detail)

    def test_tests_the_plan_named_and_nobody_wrote_are_named_under_verify(self) -> None:
        """The regression: every command passed, so a missing test read as a clean verify."""
        self.write("ABC-1", "triage.json", {"title": "t", "type": "bug"})
        self.audited("ABC-1", {"files": [{"path": "a.py"}], "steps": [1]})
        self.write("ABC-1", "evidence.json", {
            "verdict": "fail", "results": [{"command": "pytest", "exit_code": 0}], "refused": [],
            "tests_missing": ["tests/test_a.py", "tests/test_b.py"],
        })
        stage = self.stage("ABC-1", "verify")
        self.assertEqual(status_lib.FAIL, stage.state)
        self.assertIn("tests/test_a.py, tests/test_b.py", stage.detail)
        self.assertIn("impl verify ABC-1", stage.command)

    def test_a_long_missing_list_is_cut_with_a_count(self) -> None:
        self.write("ABC-1", "triage.json", {"title": "t", "type": "bug"})
        self.audited("ABC-1", {"files": [{"path": "a.py"}], "steps": [1]})
        missing = [f"tests/test_{n}.py" for n in "abcde"]
        self.write("ABC-1", "evidence.json", {"verdict": "fail", "results": [], "refused": [], "tests_missing": missing})
        detail = self.stage("ABC-1", "verify").detail
        self.assertIn("tests/test_c.py (+2 more)", detail)
        self.assertNotIn("tests/test_d.py", detail)

    def test_a_regression_that_passed_without_the_fix_is_named(self) -> None:
        self.write("ABC-1", "triage.json", {"title": "t", "type": "bug"})
        self.audited("ABC-1", {"files": [{"path": "a.py"}], "steps": [1]})
        self.write("ABC-1", "evidence.json", {
            "verdict": "fail", "results": [{"command": "pytest", "exit_code": 0}], "refused": [], "tests_missing": [],
            "regression": {"base": "abc", "targets": [
                {"target": "tests/test_a.py", "ok": True},
                {"target": "tests/test_b.py", "ok": False},
            ]},
        })
        detail = self.stage("ABC-1", "verify").detail
        self.assertIn("regression not proven: tests/test_b.py", detail)
        self.assertNotIn("tests/test_a.py", detail)


class StaleEvidence(StatusBase):
    """A pass recorded before the last edit is a claim about code that is gone."""

    PLAN = {"files": [{"path": "a.py"}], "steps": [1]}

    def setUp(self) -> None:
        super().setUp()
        self.write("ABC-1", "triage.json", {"title": "t", "type": "feature"})
        self.audited("ABC-1", self.PLAN)
        self.write("ABC-1", "evidence.json", {
            "verdict": "pass", "results": [{"command": "pytest", "exit_code": 0}], "refused": [],
            "tree": "tree-verified", "plan_sha256": audit_lib.digest(self.PLAN),
        })

    def verify_stage(self, tree: str):
        with mock.patch("workbench.gitctx.tree", return_value=tree):
            return self.stage("ABC-1", "verify")

    def test_the_verified_tree_reads_ok(self) -> None:
        self.assertEqual(status_lib.OK, self.verify_stage("tree-verified").state)

    def test_a_changed_tree_reads_stale_and_offers_verify_again(self) -> None:
        stage = self.verify_stage("tree-edited")
        self.assertNotEqual(status_lib.OK, stage.state)
        self.assertIn("stale", stage.detail)
        self.assertIn("impl verify ABC-1", stage.command)

    def test_a_changed_plan_reads_stale(self) -> None:
        wider = {"files": [{"path": "a.py"}, {"path": "b.py"}], "steps": [1]}
        self.audited("ABC-1", wider)
        stage = self.verify_stage("tree-verified")
        self.assertNotEqual(status_lib.OK, stage.state)
        self.assertIn("plan changed", stage.detail)

    def test_a_listing_passes_the_tree_it_resolved_once(self) -> None:
        with mock.patch("workbench.gitctx.tree", side_effect=AssertionError("resolved per ticket")):
            status = status_lib.read("ABC-1", changed=set(), tree="tree-verified")
        verify = next(s for s in status.stages if s.name == "verify")
        self.assertEqual(status_lib.OK, verify.state)


class Scope(StatusBase):
    def _planned(self, key: str) -> None:
        self.write(key, "triage.json", {"title": "t", "type": "feature"})
        self.audited(key, {"files": [{"path": "a.py"}, {"path": "b.py"}]}, citations_checked=2)

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


class Closed(StatusBase):
    """WB-24: a shipped ticket kept its artifacts, and status read them alone,
    so every merged ticket stayed listed at its last stage -- or as blocked."""

    def close(self, key: str, status: str = "done") -> None:
        path = self.root / ".workflow" / "tasks" / f"{key}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"key": key, "status": status}), encoding="utf-8")

    def shipped(self, key: str) -> None:
        self.write(key, "triage.json", {"title": "t", "type": "feature", "provider": "local"})
        self.write(key, "commit.txt", "fix: a thing")

    def test_a_done_ticket_with_a_commit_reads_as_done_with_nothing_next(self) -> None:
        self.shipped("ABC-1")
        self.close("ABC-1")
        status = status_lib.read("ABC-1")

        self.assertTrue(status.closed)
        self.assertEqual("done", status.headline)
        self.assertEqual("", status.next_command)
        self.assertIn("complete", status_lib.render_next(status, "named"))
        self.assertTrue(status.to_dict()["closed"])

    def test_a_done_ticket_is_never_blocked(self) -> None:
        self.write("ABC-1", "triage.json", {"title": "t", "type": "feature"})
        self.write("ABC-1", "sdd.json", {"files": [{"path": "a.py"}]})
        self.write("ABC-1", "audit.json", {"verdict": "fail", "citations_checked": 1, "citations_failed": 1})
        self.close("ABC-1")

        status = status_lib.read("ABC-1")
        self.assertIsNone(status.blocked)
        self.assertNotIn("BLOCKED", status_lib.render_list([status]))
        self.assertEqual({}, status_lib.summarise([status])["blocked_at"])

    def test_an_open_ticket_with_the_same_artifacts_is_unchanged(self) -> None:
        self.shipped("ABC-1")
        self.close("ABC-1", status="open")
        status = status_lib.read("ABC-1")

        self.assertFalse(status.closed)
        self.assertEqual("commit", status.headline)
        self.assertNotEqual("", status.next_command)

    def test_the_most_recent_fallback_skips_a_closed_ticket(self) -> None:
        self.shipped("ABC-1")
        self.shipped("ABC-2")
        self.close("ABC-2")
        old = time.time() - 3600
        os.utime(self.root / ".workflow" / "ABC-1", (old, old))

        with mock.patch("workbench.gitctx.branch", return_value=None):
            picked, origin = status_lib.pick()
        self.assertEqual(("ABC-1", "most recent"), (picked.key, origin))

    def test_a_closed_ticket_is_still_the_answer_when_it_is_the_only_one(self) -> None:
        self.shipped("ABC-1")
        self.close("ABC-1")
        with mock.patch("workbench.gitctx.branch", return_value=None):
            picked, _ = status_lib.pick()
        self.assertEqual("ABC-1", picked.key)


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

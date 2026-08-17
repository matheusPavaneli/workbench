"""Attributing a changed file to the ticket that accounts for it.

The scope guard is the thing that keeps an implementation inside the plan it
was audited against, so widening it is dangerous by default. Most of what is
asserted here is what still counts as a deviation.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workbench import scope


class ScopeBase(unittest.TestCase):
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

    def plan(self, key: str, paths: list, *, audited: bool = True) -> None:
        directory = self.root / ".workflow" / key
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "sdd.json").write_text(
            json.dumps({"key": key, "files": [{"path": p, "change": "edit", "why": "w"} for p in paths]}),
            encoding="utf-8",
        )
        if audited:
            (directory / "audit.json").write_text(
                json.dumps({"key": key, "verdict": "pass"}), encoding="utf-8"
            )


class Attribution(ScopeBase):
    def test_another_audited_plan_accounts_for_its_own_files(self) -> None:
        self.plan("ABC-1", ["src/a.py"])
        self.plan("ABC-2", ["src/b.py"])
        self.assertEqual({"src/b.py": ["ABC-2"]}, scope.claims("ABC-1"))

    def test_the_ticket_under_check_never_claims_against_itself(self) -> None:
        """Its own files would come back reported as belonging elsewhere."""
        self.plan("ABC-1", ["src/a.py"])
        self.assertEqual({}, scope.claims("ABC-1"))

    def test_a_path_claimed_twice_names_both_tickets(self) -> None:
        """Two plans editing one file is worth knowing before either lands."""
        self.plan("ABC-1", ["src/a.py"])
        self.plan("ABC-2", ["src/shared.py"])
        self.plan("ABC-3", ["src/shared.py"])
        self.assertEqual(["ABC-2", "ABC-3"], scope.claims("ABC-1")["src/shared.py"])

    def test_backslashes_are_normalised_so_windows_paths_match(self) -> None:
        self.plan("ABC-2", ["src\\b.py"])
        self.assertIn("src/b.py", scope.claims("ABC-1"))


class WhatStillCountsAsADeviation(ScopeBase):
    """The guard is widened here, so these are the tests that matter."""

    def test_an_unaudited_plan_accounts_for_nothing(self) -> None:
        """Otherwise anyone could silence the check by writing a document."""
        self.plan("ABC-2", ["src/b.py"], audited=False)
        self.assertEqual({}, scope.claims("ABC-1"))

    def test_a_failed_audit_accounts_for_nothing(self) -> None:
        directory = self.root / ".workflow" / "ABC-2"
        directory.mkdir(parents=True)
        (directory / "sdd.json").write_text(
            json.dumps({"files": [{"path": "src/b.py"}]}), encoding="utf-8"
        )
        (directory / "audit.json").write_text(json.dumps({"verdict": "fail"}), encoding="utf-8")
        self.assertEqual({}, scope.claims("ABC-1"))

    def test_a_file_no_plan_lists_is_not_accounted_for(self) -> None:
        self.plan("ABC-2", ["src/b.py"])
        self.assertNotIn("src/elsewhere.py", scope.claims("ABC-1"))

    def test_a_malformed_plan_accounts_for_nothing_and_does_not_raise(self) -> None:
        directory = self.root / ".workflow" / "ABC-2"
        directory.mkdir(parents=True)
        (directory / "sdd.json").write_text("{not json", encoding="utf-8")
        (directory / "audit.json").write_text(json.dumps({"verdict": "pass"}), encoding="utf-8")
        self.assertEqual({}, scope.claims("ABC-1"))

    def test_a_plan_with_no_files_accounts_for_nothing(self) -> None:
        self.plan("ABC-2", [])
        self.assertEqual({}, scope.claims("ABC-1"))


class Robustness(ScopeBase):
    def test_no_workflow_directory_is_not_an_error(self) -> None:
        self.assertEqual({}, scope.claims("ABC-1"))

    def test_the_task_backlog_is_not_a_plan(self) -> None:
        (self.root / ".workflow" / "tasks").mkdir(parents=True)
        (self.root / ".workflow" / "tasks" / "WB-1.json").write_text("{}", encoding="utf-8")
        self.assertEqual({}, scope.claims("ABC-1"))

    def test_an_empty_exclude_still_returns_every_claim(self) -> None:
        self.plan("ABC-2", ["src/b.py"])
        self.assertIn("src/b.py", scope.claims(""))


if __name__ == "__main__":
    unittest.main()

"""``wb next``: resolving which work, then saying one thing about it.

The resolution is the part worth testing. Printing a stage is already covered
by the status tests; picking the *wrong ticket* is the failure that sends a
session to edit code for something it is not working on.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from test_cli import CliBase, run  # noqa: E402

from workbench import status as status_lib  # noqa: E402
from workbench.errors import EXIT_NOT_FOUND  # noqa: E402


class Resolution(CliBase):
    def _ticket(self, key: str, **files: str) -> None:
        directory = self.root / ".workflow" / key
        directory.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (directory / name.replace("_", ".")).write_text(content, encoding="utf-8")

    def _on_branch(self, name: str):
        return mock.patch("workbench.gitctx.branch", return_value=name)

    def test_no_work_at_all_is_not_found_with_a_way_in(self) -> None:
        code, out, err = run("next")
        self.assertEqual(EXIT_NOT_FOUND, code)
        self.assertIn("wb task list", err + out)

    def test_an_explicit_key_wins_over_everything(self) -> None:
        self._ticket("ABC-1", triage_json=json.dumps({"title": "one"}))
        self._ticket("ABC-2", triage_json=json.dumps({"title": "two"}))
        with self._on_branch("feature/ABC-2-two"):
            code, out, _ = run("next", "ABC-1")
        self.assertEqual(0, code)
        self.assertIn("ABC-1", out)
        self.assertNotIn("ABC-2", out)

    def test_the_branch_decides_when_no_key_is_given(self) -> None:
        """Most-recent would pick the other ticket here, which is the bug."""
        self._ticket("ABC-2", triage_json=json.dumps({"title": "two"}))
        self._ticket("ABC-1", triage_json=json.dumps({"title": "one"}))
        with self._on_branch("feature/ABC-2-two"):
            code, out, _ = run("next")
        self.assertEqual(0, code)
        self.assertIn("ABC-2", out)
        self.assertIn("(branch)", out)

    def test_a_branch_naming_no_ticket_falls_back_to_the_last_touched(self) -> None:
        self._ticket("ABC-1", triage_json=json.dumps({"title": "one"}))
        with self._on_branch("chore/tidy-up"):
            code, out, _ = run("next")
        self.assertEqual(0, code)
        self.assertIn("ABC-1", out)
        self.assertIn("(most recent)", out)

    def test_a_slug_key_is_found_in_a_branch_too(self) -> None:
        """idea- and incident- work has no ticket number to match on."""
        self._ticket("incident-checkout-500s", sdd_json=json.dumps({"objective": "stop the bleeding"}))
        with self._on_branch("hotfix/incident-checkout-500s"):
            code, out, _ = run("next")
        self.assertEqual(0, code)
        self.assertIn("incident-checkout-500s", out)
        self.assertIn("(branch)", out)

    def test_a_key_in_the_branch_with_no_artifacts_still_resolves(self) -> None:
        """A branch exists before triage does; next must say to run triage."""
        with self._on_branch("feature/ABC-9-new"):
            code, out, _ = run("next")
        self.assertEqual(0, code)
        self.assertIn("ABC-9", out)
        self.assertIn("wb task get ABC-9", out)

    def test_a_longer_key_is_not_matched_by_a_shorter_one(self) -> None:
        """Regression: substring matching made ABC-1 match feature/ABC-12-thing,
        so whichever was touched more recently won."""
        self._ticket("ABC-12", triage_json=json.dumps({"title": "twelve"}))
        self._ticket("ABC-1", triage_json=json.dumps({"title": "one"}))  # touched last
        with self._on_branch("feature/ABC-12-thing"):
            _, out, _ = run("next")
        self.assertIn("ABC-12", out)
        self.assertNotIn("ABC-1 ", out)

    def test_a_branch_with_a_number_in_it_does_not_invent_a_ticket(self) -> None:
        """Regression: the key regex matched anywhere, so chore/bump-node-20
        resolved to NODE-20 and hid the work actually in flight."""
        self._ticket("ABC-1", triage_json=json.dumps({"title": "one"}))
        with self._on_branch("chore/bump-node-20"):
            _, out, _ = run("next")
        self.assertIn("ABC-1", out)
        self.assertNotIn("NODE-20", out)

    def test_a_guessed_key_never_displaces_real_work(self) -> None:
        """Even a well-formed key in the branch loses to a ticket with artifacts:
        a guess is the answer only when there is nothing else to answer with."""
        self._ticket("ABC-1", triage_json=json.dumps({"title": "one"}))
        with self._on_branch("feature/ZZZ-9-elsewhere"):
            _, out, _ = run("next")
        self.assertIn("ABC-1", out)
        self.assertNotIn("ZZZ-9", out)

    def test_no_git_branch_at_all_is_not_a_failure(self) -> None:
        self._ticket("ABC-1", triage_json=json.dumps({"title": "one"}))
        with self._on_branch(None):
            code, out, _ = run("next")
        self.assertEqual(0, code)
        self.assertIn("ABC-1", out)


class Output(CliBase):
    def _ticket(self, key: str, **files: str) -> None:
        directory = self.root / ".workflow" / key
        directory.mkdir(parents=True, exist_ok=True)
        for name, content in files.items():
            (directory / name.replace("_", ".")).write_text(content, encoding="utf-8")

    def test_it_is_two_lines_not_a_status_report(self) -> None:
        """The whole reason it exists: status prints eight stages, this one."""
        self._ticket("ABC-1", triage_json=json.dumps({"title": "one"}))
        with mock.patch("workbench.gitctx.branch", return_value=None):
            _, out, _ = run("next")
        self.assertEqual(2, len(out.strip().splitlines()))

    def test_json_has_the_five_fields_a_skill_branches_on(self) -> None:
        self._ticket("ABC-1", triage_json=json.dumps({"title": "one"}))
        with mock.patch("workbench.gitctx.branch", return_value=None):
            _, out, _ = run("next", "--json")
        data = json.loads(out)
        for field in ("key", "origin", "stage", "state", "command"):
            self.assertIn(field, data)

    def test_a_blocked_stage_outranks_the_next_undone_one(self) -> None:
        """A failed audit is what to fix, even though later stages are also undone."""
        self._ticket(
            "ABC-1",
            triage_json=json.dumps({"title": "one"}),
            sdd_json=json.dumps({"objective": "x", "files": [], "steps": [], "verify": []}),
            audit_json=json.dumps({"verdict": "fail", "citations_checked": 4, "citations_failed": 1}),
        )
        with mock.patch("workbench.gitctx.branch", return_value=None):
            _, out, _ = run("next")
        self.assertIn("audit BLOCKED", out)
        self.assertIn("wb sdd audit ABC-1", out)


class Library(unittest.TestCase):
    def test_pick_reports_nothing_rather_than_raising(self) -> None:
        """The CLI turns this into an exit code; the library stays quiet."""
        with mock.patch.object(status_lib, "keys", return_value=[]), mock.patch(
            "workbench.gitctx.branch", return_value=None
        ):
            self.assertIsNone(status_lib.pick())


if __name__ == "__main__":
    unittest.main()

"""Every question this CLI answers in more than one place, asked in all of them.

The tests elsewhere check what each path *does*. These check that the paths
*agree*, which is a different property and the one that kept failing: three of
the eight findings in the last review had the same shape -- two commands
answering one question two ways, each correct alone.

    wb sdd gates      read the detected preset, ignoring a recorded override
    wb git            resolved the flow by skipping to detection, so a repo
                      that had recorded develop/release got back ["main"]
    flow carry        measured its range against the local source branch, while
                      everything else in the flow used origin/<source>

None of those is visible from inside one module. Each is a divergence, so each
needs a test that holds two callers to the same answer -- and the fixture below
is deliberately non-trivial, because every one of those bugs was invisible under
a default config.
"""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import wb  # noqa: E402
from workbench import (  # noqa: E402
    audit as audit_lib,
    flow as flow_lib,
    gitctx,
    profile as profile_lib,
    scope as scope_lib,
    status as status_lib,
)

# Nothing here is a default. A default config is exactly what hid every bug
# these tests exist to catch.
CONFIG = {
    "provider": "local",
    "preset": "scaleup",
    "preset_confirmed": True,
    "preset_paths": {"packages/billing/**": "enterprise"},
    "flow": {
        "source": "develop",
        "validation": ["release/2024", "homolog"],
        "strategy": "cherry-pick",
        "branch_pattern": "feature/{key}-{slug}",
    },
}


class ConsistencyBase(unittest.TestCase):
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

        self.config(CONFIG)

    def _restore(self, name: str, previous) -> None:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous

    def config(self, data: dict) -> None:
        path = self.root / ".workflow" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def cli(self, *argv: str) -> str:
        out = io.StringIO()
        with redirect_stdout(out):
            wb.main(list(argv))
        return out.getvalue()

    def plan(self, key: str, **overrides) -> dict:
        doc = {
            "key": key,
            "preset": "scaleup",
            "objective": "a thing",
            "files": [{"path": "packages/billing/charge.ts", "change": "edit"}],
            "steps": ["do it"],
            "verify": ["pytest -q"],
            "rollback": "revert",
            "evidence": [],
        }
        doc.update(overrides)
        directory = self.root / ".workflow" / key
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "sdd.json").write_text(json.dumps(doc), encoding="utf-8")
        return doc


class WhichPreset(ConsistencyBase):
    """`wb repo profile`, `wb sdd gates`, `wb repo gates` and the audit all
    decide which bar applies. They must decide it the same way."""

    def test_every_command_reports_the_recorded_preset(self) -> None:
        """Regression: sdd gates called detect() and ignored the override."""
        self.assertIn("scaleup", self.cli("repo", "profile"))
        self.assertIn("scaleup", self.cli("sdd", "gates"))
        self.assertIn("scaleup", self.cli("repo", "gates", "src/util.ts"))

    def test_the_gate_lines_themselves_are_identical(self) -> None:
        """Not just the label: the rules a plan is held to have to match, or one
        command is quietly stricter than another."""
        from_repo = _gate_lines(self.cli("repo", "profile"))
        from_sdd = _gate_lines(self.cli("sdd", "gates"))
        self.assertEqual(from_repo, from_sdd)

    def test_a_path_override_reaches_every_caller(self) -> None:
        resolved = self.cli("repo", "gates", "packages/billing/charge.ts")
        self.assertIn("enterprise", resolved)

        library, _ = profile_lib.resolve_for(
            ["packages/billing/charge.ts"], profile_lib.preset_paths(self.root), profile_lib.resolve(self.root).preset
        )
        self.assertEqual("enterprise", library)

    def test_the_audit_holds_a_plan_to_what_the_paths_resolve_to(self) -> None:
        """The audit must not disagree with the command that told the author
        which bar applied."""
        doc = self.plan("ABC-1", preset="scaleup")
        problems = audit_lib._preset_problems(doc, self.root)
        self.assertTrue(problems, "billing resolves to enterprise; a scaleup plan must not pass")
        self.assertIn("enterprise", problems[0])

        doc = self.plan("ABC-2", preset="enterprise")
        self.assertEqual([], audit_lib._preset_problems(doc, self.root))

    def test_review_and_pr_read_the_recorded_preset_too(self) -> None:
        """Found by the structural guard, not by review: `wb review context`
        graded the diff against the *detected* preset, so a repo that had set
        its bar was reviewed against a different one."""
        from workbench.cli import pr as pr_cli, review as review_cli  # noqa: F401

        with mock.patch("workbench.gitctx.changed_files", return_value=["src/a.py"]), mock.patch(
            "workbench.gitctx.subjects_since", return_value=["feat: x"]
        ), mock.patch("workbench.gitctx.branch", return_value="feature/ABC-1"):
            review = self.cli("review", "context")
            payload = self.cli("pr", "context", "ABC-1")

        self.assertIn("scaleup", review)
        self.assertIn("scaleup", payload)

    def test_doctor_reports_the_flow_that_flow_show_reports(self) -> None:
        """doctor read the context and skipped .workflow/config.json, the rung
        above it -- so the command whose job is checking the setup described a
        different setup."""
        from workbench.cli import doctor as doctor_cli

        seen: list = []
        doctor_cli._flow(lambda *a, **k: seen.append(a), self.root)
        reported = " ".join(str(part) for entry in seen for part in entry)
        self.assertIn("develop", reported)

    def test_an_override_beats_detection_in_every_caller(self) -> None:
        with mock.patch.object(profile_lib, "_contributors", return_value=1):
            self.assertEqual("scaleup", profile_lib.resolve(self.root).preset)
            self.assertIn("scaleup", self.cli("sdd", "gates"))


class WhatIsProtected(ConsistencyBase):
    """A branch is protected or it is not. Two answers is a write onto a branch
    the repo declared off limits."""

    def test_the_library_and_the_commit_guard_agree(self) -> None:
        """Regression: cli/git resolved with load(None, root), which skips the
        repo config entirely and guesses from the remote."""
        from workbench.cli import git as git_cli

        expected = flow_lib.resolve(self.root).protected
        self.assertEqual(expected, flow_lib.protected(self.root))
        self.assertEqual(expected, git_cli._protected(self.root))

    def test_every_declared_branch_is_covered(self) -> None:
        protected = flow_lib.protected(self.root)
        for branch in ("develop", "release/2024", "homolog"):
            self.assertIn(branch, protected, f"{branch} is declared in the flow and must be protected")

    def test_what_flow_show_prints_is_what_the_guard_enforces(self) -> None:
        printed = self.cli("flow", "show")
        for branch in flow_lib.protected(self.root):
            self.assertIn(branch, printed)


class WhichRef(ConsistencyBase):
    """Starting a branch and measuring a carry range are the same question about
    freshness: which ref is the truth. A local branch is never the answer."""

    def test_the_start_base_and_the_carry_base_are_both_remote(self) -> None:
        with mock.patch("workbench.gitctx.branch_exists", return_value=True):
            base = flow_lib.carry_base(self.root, "develop")
        switch = flow_lib.start_actions("feature/ABC-1", "develop")[-1]

        self.assertEqual("origin/develop", base)
        self.assertIn("origin/develop", switch.argv)

    def test_the_carry_target_is_remote_too(self) -> None:
        switch = flow_lib.carry_actions("b", "release/2024", ["aaa1111 x"])[0]
        self.assertIn("origin/release/2024", switch.argv)

    def test_a_repo_without_the_remote_ref_degrades_the_same_way_everywhere(self) -> None:
        with mock.patch("workbench.gitctx.branch_exists", return_value=False):
            self.assertEqual("develop", flow_lib.carry_base(self.root, "develop"))

    def test_the_carry_command_measures_against_the_ref_it_resolved(self) -> None:
        """Testing carry_base alone proves nothing about the caller: the bug was
        that cli/flow passed flow.source.branch and never called it."""
        seen: list[str] = []

        def note(root, source_branch, base, onto):
            seen.append(base)
            return []

        with mock.patch.object(flow_lib, "carry_plan", side_effect=note), mock.patch(
            "workbench.gitctx.branch", return_value="feature/ABC-1-thing"
        ), mock.patch("workbench.gitctx.branch_exists", return_value=True):
            self.cli("flow", "carry", "ABC-1", "--to", "homolog")

        self.assertEqual(["origin/develop"], seen, "the carry range must be measured against the remote ref")


class WhichKey(ConsistencyBase):
    """`wb next` and `wb flow carry` both work out which ticket a checkout is
    on. Disagreeing means carrying one ticket's commits under another's name."""

    def _artifacts(self, key: str) -> None:
        directory = self.root / ".workflow" / key
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "triage.json").write_text(json.dumps({"title": key}), encoding="utf-8")

    def test_both_resolve_the_same_ticket_from_the_same_branch(self) -> None:
        self._artifacts("ABC-12")
        self._artifacts("ABC-1")  # touched last: most-recent would pick this one

        with mock.patch("workbench.gitctx.branch", return_value="feature/ABC-12-thing"):
            picked, origin = status_lib.pick()
            carried = _source_branch(self.root, flow_lib.resolve(self.root), "ABC-12")

        self.assertEqual("ABC-12", picked.key)
        self.assertEqual("branch", origin)
        self.assertIn("ABC-12", carried)

    def test_neither_lets_a_shorter_key_answer_for_a_longer_one(self) -> None:
        self._artifacts("ABC-12")
        self._artifacts("ABC-1")
        with mock.patch("workbench.gitctx.branch", return_value="feature/ABC-12-thing"):
            self.assertEqual("ABC-12", status_lib.key_from_branch(["ABC-1", "ABC-12"]))


class IsTheTreeDirty(ConsistencyBase):
    """Two questions that read like one. Scope counts untracked files because a
    plan that adds a file must be checked against it; the switch precondition
    must not, because an untracked file crosses a branch switch unharmed."""

    def test_the_two_reads_are_deliberately_different(self) -> None:
        with mock.patch.object(gitctx, "_git", side_effect=_fake_git(tracked="", untracked="notes.md")):
            self.assertEqual(["notes.md"], gitctx.changed_files(self.root))
            self.assertEqual([], gitctx.tracked_changes(self.root))

    def test_a_tracked_edit_shows_up_in_both(self) -> None:
        with mock.patch.object(gitctx, "_git", side_effect=_fake_git(tracked="src/a.py", untracked="")):
            self.assertEqual(["src/a.py"], gitctx.changed_files(self.root))
            self.assertEqual(["src/a.py"], gitctx.tracked_changes(self.root))

    def test_both_exclude_this_tool_s_own_artifacts(self) -> None:
        """Otherwise the tool's own writes read as scope creep on the ticket."""
        noise = ".workflow/ABC-1/sdd.json"
        with mock.patch.object(gitctx, "_git", side_effect=_fake_git(tracked=noise, untracked=noise)):
            self.assertEqual([], gitctx.changed_files(self.root))
            self.assertEqual([], gitctx.tracked_changes(self.root))

    def test_scope_attribution_reads_the_same_paths_the_plan_declares(self) -> None:
        self.plan("ABC-1")
        audit = self.root / ".workflow" / "ABC-1" / "audit.json"
        audit.write_text(json.dumps({"verdict": "pass"}), encoding="utf-8")

        claimed = scope_lib.claims(exclude="ABC-2", cwd=self.root)
        self.assertIn("packages/billing/charge.ts", claimed)


def _gate_lines(output: str) -> list[str]:
    return sorted(line.strip(" -") for line in output.splitlines() if line.startswith("  - "))


def _source_branch(root: Path, flow, key: str) -> str:
    from workbench.cli import flow as flow_cli

    return flow_cli._source_branch(root, flow, key)


def _fake_git(*, tracked: str, untracked: str):
    def run(args: list[str], cwd) -> str | None:
        if args[:2] == ["diff", "--name-only"]:
            return tracked
        if args[:2] == ["ls-files", "--others"]:
            return untracked
        return None

    return run


if __name__ == "__main__":
    unittest.main()

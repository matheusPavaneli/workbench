import subprocess
import tempfile
import unittest
from pathlib import Path

from workbench import gitctx
from workbench.errors import UsageError


def _git(args: list[str], cwd: Path) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False)


class WorkingTree(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _git(["init", "-q", "."], self.root)
        _git(["config", "user.email", "t@example.com"], self.root)
        _git(["config", "user.name", "T"], self.root)
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_text("one\n", encoding="utf-8")
        _git(["add", "-A"], self.root)
        _git(["commit", "-qm", "init"], self.root)
        # init.defaultBranch differs between machines; the tests need the name.
        self.base = gitctx.branch(self.root) or "master"

    def test_modified_and_untracked_both_count(self) -> None:
        (self.root / "src" / "a.py").write_text("two\n", encoding="utf-8")
        (self.root / "src" / "b.py").write_text("new\n", encoding="utf-8")
        self.assertEqual(["src/a.py", "src/b.py"], gitctx.changed_files(self.root))

    def test_changed_since_counts_what_the_branch_committed(self) -> None:
        """The regression: a committed change read as no change at all."""
        _git(["switch", "-c", "work", "-q"], self.root)
        (self.root / "src" / "b.py").write_text("new\n", encoding="utf-8")
        _git(["add", "-A"], self.root)
        _git(["commit", "-qm", "add b"], self.root)

        self.assertEqual([], gitctx.changed_files(self.root))
        self.assertEqual(["src/b.py"], gitctx.changed_since(self.root, self.base))

    def test_changed_since_adds_the_working_tree_to_the_commits(self) -> None:
        _git(["switch", "-c", "work", "-q"], self.root)
        (self.root / "src" / "b.py").write_text("new\n", encoding="utf-8")
        _git(["add", "-A"], self.root)
        _git(["commit", "-qm", "add b"], self.root)
        (self.root / "src" / "c.py").write_text("later\n", encoding="utf-8")

        self.assertEqual(["src/b.py", "src/c.py"], gitctx.changed_since(self.root, self.base))

    def test_changed_since_ignores_what_the_base_did_after_the_branch_left(self) -> None:
        """Three dots: a busy base is not this branch's doing."""
        _git(["switch", "-c", "work", "-q"], self.root)
        (self.root / "src" / "b.py").write_text("new\n", encoding="utf-8")
        _git(["add", "-A"], self.root)
        _git(["commit", "-qm", "add b"], self.root)

        _git(["switch", self.base, "-q"], self.root)
        (self.root / "src" / "elsewhere.py").write_text("theirs\n", encoding="utf-8")
        _git(["add", "-A"], self.root)
        _git(["commit", "-qm", "elsewhere"], self.root)
        _git(["switch", "work", "-q"], self.root)

        self.assertEqual(["src/b.py"], gitctx.changed_since(self.root, self.base))

    def test_an_unknown_base_still_answers_with_the_working_tree(self) -> None:
        (self.root / "src" / "a.py").write_text("two\n", encoding="utf-8")
        self.assertEqual(["src/a.py"], gitctx.changed_since(self.root, "origin/nope"))

    def test_changed_since_never_reports_this_tools_own_artifacts(self) -> None:
        _git(["switch", "-c", "work", "-q"], self.root)
        artifacts = self.root / ".workflow" / "ABC-1"
        artifacts.mkdir(parents=True)
        (artifacts / "sdd.json").write_text("{}", encoding="utf-8")
        (self.root / "src" / "a.py").write_text("two\n", encoding="utf-8")
        _git(["add", "-A", "-f"], self.root)
        _git(["commit", "-qm", "both"], self.root)

        self.assertEqual(["src/a.py"], gitctx.changed_since(self.root, self.base))

    def test_workflow_artifacts_are_never_reported_as_changes(self) -> None:
        artifacts = self.root / ".workflow" / "ABC-1"
        artifacts.mkdir(parents=True)
        (artifacts / "sdd.json").write_text("{}", encoding="utf-8")
        (self.root / "src" / "a.py").write_text("two\n", encoding="utf-8")
        self.assertEqual(["src/a.py"], gitctx.changed_files(self.root))

    def test_generated_output_is_never_a_change(self) -> None:
        """A checkout without a .gitignore reported bytecode as scope creep,
        and marked it as touching a critical zone."""
        cache = self.root / "src" / "__pycache__"
        cache.mkdir()
        (cache / "a.cpython-312.pyc").write_bytes(b"\x00")
        (self.root / "node_modules").mkdir()
        (self.root / "node_modules" / "left-pad.js").write_text("x", encoding="utf-8")
        (self.root / "src" / "a.py").write_text("two\n", encoding="utf-8")
        self.assertEqual(["src/a.py"], gitctx.changed_files(self.root))

    def test_a_source_file_named_like_a_build_dir_still_counts(self) -> None:
        (self.root / "src" / "distance.py").write_text("x\n", encoding="utf-8")
        self.assertIn("src/distance.py", gitctx.changed_files(self.root))

    def test_paths_use_forward_slashes(self) -> None:
        (self.root / "src" / "a.py").write_text("two\n", encoding="utf-8")
        self.assertTrue(all("\\" not in path for path in gitctx.changed_files(self.root)))

    def test_staged_view_excludes_unstaged_work(self) -> None:
        (self.root / "src" / "a.py").write_text("two\n", encoding="utf-8")
        (self.root / "src" / "c.py").write_text("three\n", encoding="utf-8")
        _git(["add", "src/a.py"], self.root)
        self.assertEqual(["src/a.py"], gitctx.changed_files(self.root, staged=True))

    def test_identity_comes_from_the_checkout(self) -> None:
        self.assertEqual("t@example.com", gitctx.identity(self.root)["email"])

    def test_branch_is_reported(self) -> None:
        self.assertIsNotNone(gitctx.branch(self.root))


class OutsideACheckout(unittest.TestCase):
    """The two answers the package is allowed to give, in one place each.

    Both used to be inlined per caller -- the fallback twenty times, the
    refusal eight -- so changing either meant finding every spelling of it.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.plain = Path(self._tmp.name)

    def test_checkout_falls_back_to_the_directory_it_was_given(self) -> None:
        self.assertEqual(self.plain.resolve(), gitctx.checkout(self.plain).resolve())

    def test_require_checkout_refuses_instead_of_inventing_a_root(self) -> None:
        with self.assertRaises(UsageError) as caught:
            gitctx.require_checkout(self.plain)
        self.assertIn("not a git repository", str(caught.exception))

    def test_a_caller_can_say_what_to_do_about_it(self) -> None:
        """`wb init` is the one command whose fix is `git init`."""
        with self.assertRaises(UsageError) as caught:
            gitctx.require_checkout(self.plain, fix=["run this inside a checkout, or: git init"])
        self.assertIn("git init", caught.exception.render())


class Tree(unittest.TestCase):
    """What the tree fingerprint must hold for evidence to be bound to code."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _git(["init", "-q", "."], self.root)
        _git(["config", "user.email", "t@example.com"], self.root)
        _git(["config", "user.name", "T"], self.root)
        (self.root / ".gitignore").write_text("*.log\n", encoding="utf-8")
        (self.root / "a.py").write_text("one\n", encoding="utf-8")
        _git(["add", "-A"], self.root)
        _git(["commit", "-qm", "init"], self.root)

    def status(self) -> str:
        return subprocess.run(
            ["git", "status", "--porcelain"], cwd=str(self.root), capture_output=True, text=True, check=False
        ).stdout

    def test_it_is_stable_across_calls(self) -> None:
        first = gitctx.tree(self.root)
        self.assertTrue(first)
        self.assertEqual(first, gitctx.tree(self.root))

    def test_a_tracked_edit_changes_it(self) -> None:
        before = gitctx.tree(self.root)
        (self.root / "a.py").write_text("two\n", encoding="utf-8")
        self.assertNotEqual(before, gitctx.tree(self.root))

    def test_an_untracked_file_changes_it(self) -> None:
        before = gitctx.tree(self.root)
        (self.root / "b.py").write_text("new\n", encoding="utf-8")
        self.assertNotEqual(before, gitctx.tree(self.root))

    def test_workflow_and_ignored_files_do_not(self) -> None:
        """Writing the evidence must not invalidate the evidence."""
        before = gitctx.tree(self.root)
        (self.root / ".workflow" / "ABC-1").mkdir(parents=True)
        (self.root / ".workflow" / "ABC-1" / "evidence.json").write_text("{}", encoding="utf-8")
        (self.root / "run.log").write_text("noise", encoding="utf-8")
        self.assertEqual(before, gitctx.tree(self.root))

    def test_committing_the_verified_tree_keeps_it(self) -> None:
        """Verify before the commit, commit exactly that: the evidence still describes HEAD."""
        (self.root / "a.py").write_text("two\n", encoding="utf-8")
        (self.root / "b.py").write_text("new\n", encoding="utf-8")
        verified = gitctx.tree(self.root)
        _git(["add", "-A"], self.root)
        _git(["commit", "-qm", "work"], self.root)
        self.assertEqual(verified, gitctx.tree(self.root))
        head_tree = subprocess.run(
            ["git", "rev-parse", "HEAD^{tree}"], cwd=str(self.root), capture_output=True, text=True, check=False
        ).stdout.strip()
        self.assertEqual(head_tree, verified)

    def test_the_staging_area_is_left_alone(self) -> None:
        (self.root / "a.py").write_text("two\n", encoding="utf-8")
        (self.root / "b.py").write_text("new\n", encoding="utf-8")
        before = self.status()
        gitctx.tree(self.root)
        self.assertEqual(before, self.status())

    def test_outside_a_checkout_it_is_none(self) -> None:
        with tempfile.TemporaryDirectory() as plain:
            self.assertIsNone(gitctx.tree(Path(plain)))


if __name__ == "__main__":
    unittest.main()

"""The execution boundary: what may run, what may not, and when it stops.

This is the only module in the package that writes to a repository, so the
tests are written as refusals first. Every case below is something that must
*not* happen; the handful that do run are checked for stopping in the right
place, because a cherry-pick series that continues past a failure lands commits
out of order -- the exact mistake the carry computation exists to prevent.
"""

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from test_cli import CliBase, run  # noqa: E402

from workbench import flow as flow_lib, gitrun  # noqa: E402
from workbench.errors import EXIT_NOT_FOUND, EXIT_USAGE, UsageError  # noqa: E402


class Allowlist(unittest.TestCase):
    def refusal(self, *argv: str) -> str:
        reason = gitrun.check(gitrun.Action(list(argv)))
        self.assertIsNotNone(reason, f"git {' '.join(argv)} was allowed")
        return reason or ""

    def test_the_commands_this_tool_computes_are_allowed(self) -> None:
        for argv in (
            ["fetch", "origin"],
            ["switch", "-c", "feature/ABC-1", "origin/main"],
            ["cherry-pick", "abc1234", "def5678"],
            ["commit", "-F", ".workflow/ABC-1/commit.txt"],
            ["push", "-u", "origin", "feature/ABC-1"],
        ):
            with self.subTest(argv=argv):
                self.assertIsNone(gitrun.check(gitrun.Action(argv)))

    def test_an_unlisted_subcommand_is_refused(self) -> None:
        for subcommand in ("reset", "rebase", "clean", "filter-branch", "merge", "tag", "worktree"):
            with self.subTest(subcommand=subcommand):
                self.assertIn("not a git subcommand", self.refusal(subcommand, "--hard"))

    def test_force_is_refused_in_every_spelling(self) -> None:
        for flag in ("--force", "-f", "--force-with-lease"):
            with self.subTest(flag=flag):
                self.refusal("push", flag, "origin", "main")

    def test_amend_is_refused_because_it_rewrites(self) -> None:
        self.refusal("commit", "--amend", "-F", "msg.txt")

    def test_no_verify_is_refused_because_hooks_are_the_repo_s_gates(self) -> None:
        self.refusal("commit", "--no-verify", "-F", "msg.txt")

    def test_a_config_override_cannot_be_smuggled_in(self) -> None:
        """``git -c core.hooksPath=/tmp commit`` would run something else entirely."""
        self.refusal("-c", "core.hooksPath=/tmp", "commit", "-F", "msg.txt")

    def test_switch_keeps_its_own_dash_c(self) -> None:
        """-c is git's config override *before* a subcommand and switch's create
        flag after one. Denying the token outright would break the real use."""
        self.assertIsNone(gitrun.check(gitrun.Action(["switch", "-c", "x", "origin/main"])))

    def test_a_flag_the_subcommand_does_not_own_is_refused(self) -> None:
        self.assertIn("not allowed on git commit", self.refusal("commit", "--prune"))

    def test_shell_characters_are_refused_since_there_is_no_shell(self) -> None:
        for token in ("main; rm -rf /", "main && echo", "$(whoami)", "main | tee x", "a`b`"):
            with self.subTest(token=token):
                self.assertIn("shell character", self.refusal("fetch", token))

    def test_an_empty_command_is_refused(self) -> None:
        self.assertIn("empty", self.refusal())

    def test_an_author_address_is_data_not_redirection(self) -> None:
        """Regression: <> in the FORBIDDEN set rejected every --author a context
        supplies, so the feature refused 100% of the time wherever it applied."""
        action = gitrun.Action(["commit", "-F", "m.txt", "--author", "Jane Doe <jane@example.com>"])
        self.assertIsNone(gitrun.check(action))

    def test_an_author_value_still_cannot_chain_a_command(self) -> None:
        """Only the angle brackets are relaxed, and only for a flag's value."""
        for value in ("Jane; rm -rf /", "Jane && echo", "$(whoami)", "a`b`", "Jane | tee x"):
            with self.subTest(value=value):
                self.refusal("commit", "-F", "m.txt", "--author", value)

    def test_redirection_is_still_refused_anywhere_else(self) -> None:
        self.refusal("fetch", "origin > /tmp/x")
        self.refusal("commit", "-F", "out > x.txt")

    def test_the_printed_form_survives_being_pasted(self) -> None:
        """Unquoted, `--author Jane Doe <jane@x>` is redirection to the user's shell."""
        action = gitrun.Action(["commit", "-F", "m.txt", "--author", "Jane Doe <jane@example.com>"])
        self.assertIn('"Jane Doe <jane@example.com>"', action.rendered)

    def test_ordinary_tokens_are_not_quoted(self) -> None:
        self.assertEqual("git fetch origin", gitrun.Action(["fetch", "origin"]).rendered)


class KillSwitches(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._previous = os.environ.pop("WB_NO_EXECUTE", None)
        self.addCleanup(self._restore)

    def _restore(self) -> None:
        if self._previous is None:
            os.environ.pop("WB_NO_EXECUTE", None)
        else:
            os.environ["WB_NO_EXECUTE"] = self._previous

    def _config(self, data: dict) -> None:
        path = self.root / ".workflow" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")

    def test_execution_is_available_by_default(self) -> None:
        self.assertIsNone(gitrun.disabled_reason(self.root))

    def test_the_environment_switch_disables_it(self) -> None:
        os.environ["WB_NO_EXECUTE"] = "1"
        self.assertIn("WB_NO_EXECUTE", gitrun.disabled_reason(self.root) or "")

    def test_a_falsey_value_is_not_a_switch(self) -> None:
        """Otherwise WB_NO_EXECUTE=0 would read as "no execution", which is the
        opposite of what anyone setting it that way means."""
        os.environ["WB_NO_EXECUTE"] = "0"
        self.assertIsNone(gitrun.disabled_reason(self.root))

    def test_the_repo_can_refuse_execution_standing(self) -> None:
        self._config({"execute": False})
        self.assertIn("execute", gitrun.disabled_reason(self.root) or "")

    def test_a_disabled_run_raises_rather_than_silently_doing_nothing(self) -> None:
        os.environ["WB_NO_EXECUTE"] = "1"
        with self.assertRaises(UsageError):
            gitrun.apply([gitrun.Action(["fetch", "origin"])], self.root)


class Preconditions(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_a_dirty_tree_blocks_a_branch_switch(self) -> None:
        action = gitrun.Action(["switch", "-c", "x", "origin/main"], precondition=gitrun.CLEAN_TREE)
        with mock.patch("workbench.gitctx.tracked_changes", return_value=["a.py"]):
            self.assertIn("uncommitted changes", gitrun.precondition(action, self.root, []) or "")

    def test_a_clean_tree_does_not(self) -> None:
        action = gitrun.Action(["switch", "-c", "x", "origin/main"], precondition=gitrun.CLEAN_TREE)
        with mock.patch("workbench.gitctx.tracked_changes", return_value=[]):
            self.assertIsNone(gitrun.precondition(action, self.root, []))

    def test_an_untracked_scratch_file_is_not_a_dirty_tree(self) -> None:
        """git switch carries untracked files across, and git stash leaves them
        where they are -- so refusing on one blocked the ordinary case and gave
        advice that could not clear it."""
        action = gitrun.Action(["switch", "-c", "x", "origin/main"], precondition=gitrun.CLEAN_TREE)
        with mock.patch("workbench.gitctx.tracked_changes", return_value=[]), mock.patch(
            "workbench.gitctx.changed_files", return_value=["notes.md"]
        ):
            self.assertIsNone(gitrun.precondition(action, self.root, []))

    def test_a_protected_branch_blocks_a_commit(self) -> None:
        action = gitrun.Action(["commit", "-F", "m.txt"], precondition=gitrun.NOT_PROTECTED)
        with mock.patch("workbench.gitctx.branch", return_value="main"):
            self.assertIn("protected", gitrun.precondition(action, self.root, ["main", "homolog"]) or "")

    def test_a_working_branch_does_not(self) -> None:
        action = gitrun.Action(["commit", "-F", "m.txt"], precondition=gitrun.NOT_PROTECTED)
        with mock.patch("workbench.gitctx.branch", return_value="feature/ABC-1"):
            self.assertIsNone(gitrun.precondition(action, self.root, ["main"]))

    def test_a_published_branch_is_never_pushed_again(self) -> None:
        """The recovery from a bad push onto a published branch is a force-push,
        so this tool must not be able to reach the situation."""
        action = gitrun.Action(["push", "-u", "origin", "x"], precondition=gitrun.NO_UPSTREAM)
        with mock.patch.object(gitrun, "_upstream", return_value="origin/x"):
            self.assertIn("already has an upstream", gitrun.precondition(action, self.root, []) or "")

    def test_an_unpublished_branch_may_be_pushed(self) -> None:
        action = gitrun.Action(["push", "-u", "origin", "x"], precondition=gitrun.NO_UPSTREAM)
        with mock.patch.object(gitrun, "_upstream", return_value=None):
            self.assertIsNone(gitrun.precondition(action, self.root, []))


class Sequencing(unittest.TestCase):
    """Where a series stops is the whole safety story: a cherry-pick that keeps
    going after a failure lands the rest out of order."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        os.environ.pop("WB_NO_EXECUTE", None)

    def _completed(self, code: int, out: str = "") -> mock.Mock:
        return mock.Mock(returncode=code, stdout=out, stderr="")

    def test_a_clean_series_runs_every_step(self) -> None:
        actions = [gitrun.Action(["fetch", "origin"]), gitrun.Action(["cherry-pick", "abc"])]
        with mock.patch("subprocess.run", return_value=self._completed(0)) as runner:
            run = gitrun.apply(actions, self.root)
        self.assertTrue(run.ok)
        self.assertEqual(2, runner.call_count)

    def test_a_failure_stops_the_rest(self) -> None:
        actions = [gitrun.Action(["fetch", "origin"]), gitrun.Action(["cherry-pick", "abc"]), gitrun.Action(["push", "-u", "origin", "x"])]
        with mock.patch("subprocess.run", side_effect=[self._completed(0), self._completed(1, "conflict")]) as runner:
            run = gitrun.apply(actions, self.root)
        self.assertFalse(run.ok)
        self.assertEqual(2, runner.call_count)
        self.assertIn("exited 1", run.stopped)

    def test_a_refusal_stops_before_running_anything_further(self) -> None:
        actions = [gitrun.Action(["fetch", "origin"]), gitrun.Action(["reset", "--hard"]), gitrun.Action(["fetch", "origin"])]
        with mock.patch("subprocess.run", return_value=self._completed(0)) as runner:
            run = gitrun.apply(actions, self.root)
        self.assertFalse(run.ok)
        self.assertEqual(1, runner.call_count)
        self.assertTrue(run.steps[-1].refused)

    def test_a_precondition_is_checked_before_each_step_not_once(self) -> None:
        """The first step is what makes the second step's precondition false."""
        actions = [
            gitrun.Action(["fetch", "origin"]),
            gitrun.Action(["switch", "-c", "x", "origin/main"], precondition=gitrun.CLEAN_TREE),
        ]
        with mock.patch("subprocess.run", return_value=self._completed(0)), mock.patch(
            "workbench.gitctx.tracked_changes", side_effect=[["a.py"]]
        ):
            run = gitrun.apply(actions, self.root)
        self.assertFalse(run.ok)
        self.assertIn("working tree", run.stopped)

    def test_nothing_reaches_a_shell(self) -> None:
        with mock.patch("subprocess.run", return_value=self._completed(0)) as runner:
            gitrun.apply([gitrun.Action(["fetch", "origin"])], self.root)
        self.assertFalse(runner.call_args.kwargs["shell"])
        self.assertEqual(["git", "fetch", "origin"], runner.call_args.args[0])

    def test_a_timeout_is_a_failure_not_a_hang(self) -> None:
        with mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("git", 120)):
            run = gitrun.apply([gitrun.Action(["fetch", "origin"])], self.root)
        self.assertFalse(run.ok)
        self.assertIn("timed out", run.steps[0].output)

    def test_the_run_is_recorded_against_the_ticket(self) -> None:
        with mock.patch("subprocess.run", return_value=self._completed(0)), mock.patch(
            "workbench.gitctx.repo_root", return_value=self.root
        ):
            run = gitrun.apply([gitrun.Action(["fetch", "origin"])], self.root)
            path = gitrun.record(run, "ABC-1", self.root)
        self.assertIsNotNone(path)
        history = json.loads(Path(path).read_text(encoding="utf-8"))
        self.assertEqual(1, len(history))
        self.assertEqual("git fetch origin", history[0]["steps"][0]["command"])

    def test_a_second_run_appends_rather_than_replaces(self) -> None:
        with mock.patch("subprocess.run", return_value=self._completed(0)), mock.patch(
            "workbench.gitctx.repo_root", return_value=self.root
        ):
            run = gitrun.apply([gitrun.Action(["fetch", "origin"])], self.root)
            gitrun.record(run, "ABC-1", self.root)
            path = gitrun.record(run, "ABC-1", self.root)
        self.assertEqual(2, len(json.loads(Path(path).read_text(encoding="utf-8"))))


class FromTheFlow(unittest.TestCase):
    """What flow computes must survive the allowlist, or --execute is theatre."""

    def test_start_actions_pass_the_allowlist(self) -> None:
        for action in flow_lib.start_actions("feature/ABC-1-thing", "main"):
            with self.subTest(command=action.rendered):
                self.assertIsNone(gitrun.check(action))

    def test_carry_actions_pass_the_allowlist(self) -> None:
        commits = ["abc1234 first", "def5678 second"]
        for action in flow_lib.carry_actions("feature/ABC-1-homolog", "homolog", commits):
            with self.subTest(command=action.rendered):
                self.assertIsNone(gitrun.check(action))

    def test_carry_keeps_the_commits_in_the_order_it_was_given(self) -> None:
        """Oldest first. Reversed, every commit after the first conflicts."""
        commits = ["aaa1111 first", "bbb2222 second", "ccc3333 third"]
        pick = flow_lib.carry_actions("b", "homolog", commits)[-1]
        self.assertEqual(["cherry-pick", "aaa1111", "bbb2222", "ccc3333"], pick.argv)

    def test_starting_a_branch_requires_a_clean_tree(self) -> None:
        switch = flow_lib.start_actions("x", "main")[-1]
        self.assertEqual(gitrun.CLEAN_TREE, switch.precondition)


class Freshness(unittest.TestCase):
    """Every base in this flow is a remote-tracking ref, never a local branch.

    A local ``main`` is only as current as the last time somebody checked it out
    and pulled. Branching from a stale one starts the work behind; *measuring*
    against a stale one is worse, because the carry range then includes commits
    already merged upstream and picks them onto the validation branch twice.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_a_new_branch_starts_from_the_remote_ref_not_the_local_one(self) -> None:
        switch = flow_lib.start_actions("feature/ABC-1", "main")[-1]
        self.assertIn("origin/main", switch.argv)
        self.assertNotIn("main", switch.argv[2:])

    def test_the_fetch_comes_first_so_the_base_is_current(self) -> None:
        """No pull is needed, and none is wanted: pull would merge into a local
        branch this flow never touches."""
        actions = flow_lib.start_actions("feature/ABC-1", "main")
        self.assertEqual(["fetch", "origin"], actions[0].argv)

    def test_the_carry_branch_starts_from_the_remote_target(self) -> None:
        switch = flow_lib.carry_actions("b", "homolog", ["aaa1111 first"])[0]
        self.assertIn("origin/homolog", switch.argv)

    def test_the_range_is_measured_against_the_remote_source(self) -> None:
        with mock.patch("workbench.gitctx.branch_exists", return_value=True):
            self.assertEqual("origin/main", flow_lib.carry_base(self.root, "main"))

    def test_a_repo_without_that_remote_ref_falls_back_to_the_local_branch(self) -> None:
        """Better a narrow answer than no answer: a repo with no origin still works."""
        with mock.patch("workbench.gitctx.branch_exists", return_value=False):
            self.assertEqual("main", flow_lib.carry_base(self.root, "main"))

    def test_the_protected_list_comes_from_what_the_repo_recorded(self) -> None:
        """Regression: wb git resolved the flow with load(None, root), which goes
        straight to detection -- so a repo that had recorded develop/release as
        protected got back ["main"], and a commit onto release passed the check."""
        config = self.root / ".workflow" / "config.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            json.dumps({"flow": {"source": "develop", "validation": ["release/2024"]}}), encoding="utf-8"
        )
        self.assertEqual(["develop", "release/2024"], flow_lib.protected(self.root))

    def test_an_unresolvable_flow_fails_closed(self) -> None:
        """Not being able to name the protected branches is not permission to
        write to any of them."""
        with mock.patch.object(flow_lib, "resolve", side_effect=RuntimeError("boom")):
            fallback = flow_lib.protected(self.root)
        self.assertIn("main", fallback)
        self.assertIn("homolog", fallback)

    def test_carry_actions_no_longer_carry_their_own_fetch(self) -> None:
        """The caller fetches before measuring; a second fetch here would mean
        the range was computed against older refs than the branch it creates."""
        actions = flow_lib.carry_actions("b", "homolog", ["aaa1111 first"])
        self.assertNotIn(["fetch", "origin"], [action.argv for action in actions])


class ThroughTheCli(CliBase):
    """The default has to stay "print it". That is the contract every existing
    skill was written against, and --execute is an addition to it, not a change."""

    def test_flow_start_prints_and_runs_nothing_by_default(self) -> None:
        with mock.patch("subprocess.run") as runner:
            code, out, _ = run("flow", "start", "ABC-1", "--title", "a thing")
        self.assertEqual(0, code)
        self.assertIn("git switch -c", out)
        # Resolving the flow reads from git; what must not happen is a write.
        written = [
            call.args[0]
            for call in runner.call_args_list
            if call.args and len(call.args[0]) > 1 and call.args[0][1] in gitrun.ALLOWED
        ]
        self.assertEqual([], written)

    def test_flow_start_with_execute_runs_the_same_commands_it_prints(self) -> None:
        with mock.patch("workbench.gitrun._run", side_effect=lambda a, r: gitrun.Step(action=a)) as runner, mock.patch(
            "workbench.gitctx.changed_files", return_value=[]
        ):
            code, out, _ = run("flow", "start", "ABC-1", "--title", "a thing", "--execute")
        self.assertEqual(0, code)
        ran = [call.args[0].rendered for call in runner.call_args_list]
        self.assertEqual(["git fetch origin", "git switch -c ABC-1-a-thing origin/main"], ran)
        for command in ran:
            self.assertIn(command, out)

    def test_git_commit_refuses_without_a_message_written_first(self) -> None:
        code, _, err = run("git", "commit", "ABC-1")
        self.assertEqual(EXIT_NOT_FOUND, code)
        self.assertIn("wb commit check ABC-1", err)

    def test_git_commit_refuses_with_nothing_staged(self) -> None:
        """Choosing what goes into a commit is the user's, not the tool's."""
        path = self.root / ".workflow" / "ABC-1"
        path.mkdir(parents=True)
        (path / "commit.txt").write_text("fix: a thing\n", encoding="utf-8")
        with mock.patch("workbench.gitctx.changed_files", return_value=[]):
            code, _, err = run("git", "commit", "ABC-1")
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("nothing is staged", err)

    def test_carry_fetches_before_it_measures_the_range(self) -> None:
        """Regression: the range used to be computed first, so --execute could
        carry commits that were already merged into the source upstream."""
        config = self.root / ".workflow" / "config.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(
            json.dumps({"flow": {"source": "main", "validation": ["homolog"], "strategy": "cherry-pick"}}),
            encoding="utf-8",
        )
        order: list[str] = []

        def note_fetch(actions, root, protected=None):
            order.append("fetch")
            return gitrun.Run(steps=[gitrun.Step(action=actions[0])])

        def note_range(root, source_branch, base, onto):
            order.append("range")
            return []

        with mock.patch.object(gitrun, "apply", side_effect=note_fetch), mock.patch.object(
            flow_lib, "carry_plan", side_effect=note_range
        ), mock.patch("workbench.gitctx.branch", return_value="ABC-1-thing"):
            run("flow", "carry", "ABC-1", "--to", "homolog", "--execute")

        self.assertEqual(["fetch", "range"], order)

    def test_a_failed_step_is_reprinted_not_skipped(self) -> None:
        """Regression: the handover started *after* the failed step, so a switch
        that failed left the user pasting a cherry-pick onto whatever branch
        they happened to be standing on."""
        config = self.root / ".workflow" / "config.json"
        config.parent.mkdir(parents=True, exist_ok=True)
        config.write_text(json.dumps({"flow": {"source": "main", "validation": ["homolog"]}}), encoding="utf-8")

        actions = [
            gitrun.Action(["fetch", "origin"]),
            gitrun.Action(["switch", "-c", "b", "origin/homolog"]),
            gitrun.Action(["cherry-pick", "aaa1111"]),
        ]
        outcome = gitrun.Run(
            steps=[
                gitrun.Step(action=actions[0]),
                gitrun.Step(action=actions[1], exit_code=128, output="branch already exists"),
            ],
            stopped="git switch -c b origin/homolog exited 128",
        )
        from workbench.cli import flow as flow_cli

        printed = io.StringIO()
        with mock.patch.object(gitrun, "apply", return_value=outcome), redirect_stdout(printed):
            code = flow_cli._emit(actions, self.root, flow_lib.resolve(self.root), "ABC-1", execute=True)

        self.assertEqual(1, code)
        handover = printed.getvalue().split("not run")[-1]
        self.assertIn("git switch -c b origin/homolog", handover)
        self.assertIn("git cherry-pick aaa1111", handover)

    def test_git_push_names_the_current_branch_only(self) -> None:
        with mock.patch("workbench.gitctx.branch", return_value="feature/ABC-1"):
            code, out, _ = run("git", "push")
        self.assertEqual(0, code)
        self.assertIn("git push -u origin feature/ABC-1", out)
        self.assertNotIn("--force", out)


if __name__ == "__main__":
    unittest.main()

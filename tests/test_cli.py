"""The command layer, driven the way a skill drives it.

Everything below this is covered by its own tests. What was not covered was the
glue: argument shapes, exit codes and the refusals that stop a skill doing the
wrong thing. Those are the contract a SKILL.md is written against, so they are
worth asserting rather than assuming.
"""

import io
import json
import os
import sys
import tempfile
import time
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import wb  # noqa: E402
from workbench.errors import EXIT_AUDIT, EXIT_CONFIG, EXIT_NOT_FOUND, EXIT_USAGE  # noqa: E402


def run(*argv) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = wb.main(list(argv))
    return code, out.getvalue(), err.getvalue()


class CliBase(unittest.TestCase):
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

    def _restore(self, name: str, previous) -> None:
        if previous is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = previous

    def use_local(self) -> None:
        path = self.root / ".workflow" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"provider": "local", "preset": "prototype"}), encoding="utf-8")


class Surface(CliBase):
    def test_no_group_lists_the_groups_rather_than_guessing(self) -> None:
        code, _, err = run()
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("groups:", err)

    def test_an_unknown_group_is_refused(self) -> None:
        self.assertEqual(EXIT_USAGE, run("nonsense")[0])

    def test_a_group_with_no_action_lists_its_actions(self) -> None:
        code, _, err = run("task")
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("actions:", err)

    def test_there_is_no_free_text_query_flag(self) -> None:
        """The anti-hallucination guarantee, asserted at the parser."""
        for flag in ("--jql", "--wiql", "--fields", "--url"):
            with self.subTest(flag=flag):
                self.assertEqual(EXIT_USAGE, run("task", "list", flag, "x")[0])


class TaskNew(CliBase):
    def test_it_writes_a_task_and_names_the_next_command(self) -> None:
        self.use_local()
        code, out, _ = run("task", "new", "Do the thing", "--type", "bug")
        self.assertEqual(0, code)
        self.assertIn("WB-1", out)
        self.assertIn("task get WB-1", out)

    def test_an_unknown_type_is_refused_with_the_valid_set(self) -> None:
        self.use_local()
        self.assertEqual(EXIT_USAGE, run("task", "new", "x", "--type", "epic")[0])

    def test_it_refuses_on_a_repo_that_has_a_real_tracker(self) -> None:
        """A task invented locally would never reach the tracker's board."""
        home = self.root / "home" / "contexts"
        home.mkdir(parents=True, exist_ok=True)
        (home / "j.json").write_text(
            json.dumps(
                {
                    "provider": "jira",
                    "base_url": "https://acme.atlassian.net",
                    "project": "ABC",
                    "auth": {"pat_env": "T", "email": "a@b.c"},
                }
            ),
            encoding="utf-8",
        )
        (self.root / ".workflow").mkdir(parents=True, exist_ok=True)
        (self.root / ".workflow" / "config.json").write_text(json.dumps({"context": "j"}), encoding="utf-8")
        code, _, err = run("task", "new", "x")
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("jira", err)


class TaskGet(CliBase):
    def test_a_missing_task_exits_not_found(self) -> None:
        self.use_local()
        self.assertEqual(EXIT_NOT_FOUND, run("task", "get", "WB-9")[0])

    def test_a_malformed_key_is_refused_before_any_lookup(self) -> None:
        self.use_local()
        self.assertEqual(EXIT_USAGE, run("task", "get", "not a key")[0])

    def test_an_invented_expand_handle_is_refused(self) -> None:
        self.use_local()
        run("task", "new", "Do the thing")
        code, _, err = run("task", "get", "WB-1", "--expand", "comments:page2")
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("comments:page2", err)


class TaskClean(CliBase):
    """Removing one ticket's scratch, and provably nothing beside it.

    `.workflow/` holds the ticket directories, the committed context binding,
    the committed local backlog and the event log side by side. The command is
    only safe if the last three cannot be reached, so they are asserted present
    after a real removal rather than assumed out of range.
    """

    def seed(self, key: str, *names: str) -> Path:
        directory = self.root / ".workflow" / key
        directory.mkdir(parents=True, exist_ok=True)
        for name in names or ("triage.json",):
            (directory / name).write_text("{}", encoding="utf-8")
        return directory

    def test_it_lists_without_removing_anything(self) -> None:
        directory = self.seed("ABC-123", "triage.json", "sdd.json")
        code, out, _ = run("task", "clean", "ABC-123")
        self.assertEqual(0, code)
        self.assertTrue(directory.is_dir())
        self.assertIn("triage.json", out)
        self.assertIn("sdd.json", out)
        self.assertIn("nothing removed", out)

    def test_the_listing_marks_only_what_cannot_be_produced_again(self) -> None:
        self.seed("ABC-123", "triage.json", "frame.md")
        out = run("task", "clean", "ABC-123")[1]
        frame = next(line for line in out.splitlines() if "frame.md" in line)
        triage = next(line for line in out.splitlines() if "triage.json" in line)
        self.assertIn("not regenerable", frame)
        self.assertNotIn("not regenerable", triage)

    def test_force_removes_the_ticket_and_leaves_its_siblings(self) -> None:
        self.use_local()
        run("task", "new", "Do the thing")
        events = self.root / ".workflow" / ".events.jsonl"
        events.write_text('{"group": "task"}\n', encoding="utf-8")
        directory = self.seed("ABC-123")

        code, out, _ = run("task", "clean", "ABC-123", "--force")

        self.assertEqual(0, code)
        self.assertFalse(directory.exists())
        self.assertIn("removed", out)
        self.assertTrue((self.root / ".workflow" / "config.json").is_file())
        self.assertTrue((self.root / ".workflow" / "tasks" / "WB-1.json").is_file())
        self.assertTrue(events.is_file())

    def test_a_malformed_key_is_refused_before_anything_is_written(self) -> None:
        """Seeded on purpose: an empty directory cannot show that nothing was removed."""
        self.use_local()
        run("task", "new", "Do the thing")
        workflow = self.root / ".workflow"
        (workflow / ".events.jsonl").write_text('{"group": "task"}\n', encoding="utf-8")
        self.seed("ABC-123")
        before = sorted(path.name for path in workflow.rglob("*"))
        self.assertIn("config.json", before)

        for bad in ("../..", "tasks", "config.json", ".events.jsonl"):
            with self.subTest(bad=bad):
                self.assertEqual(EXIT_USAGE, run("task", "clean", bad, "--force")[0])

        self.assertEqual(before, sorted(path.name for path in workflow.rglob("*")))

    def test_a_key_with_no_artifacts_is_not_reported_as_a_success(self) -> None:
        code, _, err = run("task", "clean", "ABC-123", "--force")
        self.assertEqual(EXIT_NOT_FOUND, code)
        self.assertIn("wb status", err)

    def test_it_refuses_to_guess_what_to_clean(self) -> None:
        code, _, err = run("task", "clean")
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("--merged", err)

    def test_a_key_and_a_selector_together_are_refused(self) -> None:
        self.seed("ABC-123")
        code, _, err = run("task", "clean", "ABC-123", "--merged", "--force")
        self.assertEqual(EXIT_USAGE, code)
        self.assertTrue((self.root / ".workflow" / "ABC-123").is_dir())

    def test_older_than_selects_only_what_is_stale(self) -> None:
        old = self.seed("ABC-1")
        fresh = self.seed("ABC-2")
        self._age(old, days=40)

        out = run("task", "clean", "--older-than", "30d")[1]
        self.assertIn("ABC-1", out)
        self.assertNotIn("ABC-2", out)
        self.assertIn("1 ticket(s)", out)

    def test_a_plan_edited_in_place_counts_as_touched(self) -> None:
        """The directory's own timestamp does not move when a file is rewritten."""
        directory = self.seed("ABC-1", "sdd.json")
        self._age(directory, days=40)
        os.utime(directory / "sdd.json", None)

        self.assertIn("no ticket has been untouched", run("task", "clean", "--older-than", "30d")[1])

    def test_a_nonsense_age_is_refused(self) -> None:
        for bad in ("soon", "0d", "-3d"):
            with self.subTest(bad=bad):
                self.assertEqual(EXIT_USAGE, run("task", "clean", "--older-than", bad)[0])

    def test_merged_needs_both_shipped_and_branchless(self) -> None:
        """The half that matters: no branch alone also describes work never started."""
        self.seed("ABC-1", "pr.md")          # shipped, branch gone
        self.seed("ABC-2", "pr.md")          # shipped, branch still there
        self.seed("ABC-3", "triage.json")    # never branched at all

        with mock.patch("workbench.gitctx.remote_branches", return_value=["origin/ABC-2-thing"]):
            out = run("task", "clean", "--merged")[1]

        self.assertIn("ABC-1", out)
        self.assertNotIn("ABC-2", out)
        self.assertNotIn("ABC-3", out)

    def test_a_selector_matching_nothing_is_not_an_error(self) -> None:
        self.seed("ABC-1", "triage.json")
        with mock.patch("workbench.gitctx.remote_branches", return_value=[]):
            code, out, _ = run("task", "clean", "--merged")
        self.assertEqual(0, code)
        self.assertIn("no ticket has shipped", out)

    def test_the_listing_says_which_stage_unfinished_work_stopped_at(self) -> None:
        directory = self.seed("ABC-1")
        (directory / "triage.json").write_text(json.dumps({"provider": "local", "type": "feature"}), "utf-8")
        self._age(directory, days=40)

        out = run("task", "clean", "--older-than", "30d")[1]
        self.assertIn("still at plan", out, "triage is done, so the next thing owed is the plan")

    def test_a_selector_removes_every_match_and_no_sibling(self) -> None:
        self.use_local()
        run("task", "new", "Do the thing")
        self._age(self.seed("ABC-1"), days=40)
        self._age(self.seed("ABC-2"), days=40)
        kept = self.seed("ABC-3")

        code, out, _ = run("task", "clean", "--older-than", "30d", "--force")

        self.assertEqual(0, code)
        self.assertIn("2 ticket(s) removed", out)
        self.assertFalse((self.root / ".workflow" / "ABC-1").exists())
        self.assertFalse((self.root / ".workflow" / "ABC-2").exists())
        self.assertTrue(kept.is_dir())
        self.assertTrue((self.root / ".workflow" / "config.json").is_file())
        self.assertTrue((self.root / ".workflow" / "tasks" / "WB-1.json").is_file())

    def _age(self, directory: Path, *, days: int) -> Path:
        stamp = time.time() - days * 86400
        for path in [*directory.rglob("*"), directory]:
            os.utime(path, (stamp, stamp))
        return directory

    def test_work_that_started_before_a_ticket_cleans_the_same_way(self) -> None:
        directory = self.seed("idea-dashboard", "frame.md")
        self.assertEqual(0, run("task", "clean", "idea-dashboard", "--force")[0])
        self.assertFalse(directory.exists())


class Status(CliBase):
    def test_an_empty_repo_says_so_and_suggests_a_start(self) -> None:
        code, out, _ = run("status")
        self.assertEqual(0, code)
        self.assertIn("no work in progress", out)

    def test_it_reports_the_next_command_for_a_ticket(self) -> None:
        self.use_local()
        run("task", "new", "Do the thing")
        run("task", "get", "WB-1")
        code, out, _ = run("status", "WB-1")
        self.assertEqual(0, code)
        self.assertIn("next:", out)

    def test_json_is_machine_readable(self) -> None:
        self.use_local()
        run("task", "new", "Do the thing")
        run("task", "get", "WB-1")
        code, out, _ = run("status", "WB-1", "--json")
        self.assertEqual(0, code)
        self.assertEqual("WB-1", json.loads(out)["key"])

    def test_stats_and_a_key_together_are_refused(self) -> None:
        self.assertEqual(EXIT_USAGE, run("status", "ABC-1", "--stats")[0])

    def test_status_works_with_no_context_configured(self) -> None:
        """It is what a stuck session runs first, before anything is set up."""
        self.assertEqual(0, run("status")[0])


class Doctor(CliBase):
    def test_it_reports_every_check_and_a_total(self) -> None:
        self.use_local()
        code, out, err = run("doctor", "--offline")
        self.assertIn("checks,", out)
        self.assertIn("python", out + err)

    def test_a_broken_context_fails_without_hiding_later_checks(self) -> None:
        """A missing credential must not mask a misconfigured git author."""
        code, out, err = run("doctor", "--offline")
        self.assertEqual(EXIT_CONFIG, code)
        self.assertIn("context", err)
        self.assertIn("git author", out + err)

    def test_offline_skips_the_tracker_check(self) -> None:
        self.use_local()
        _, out, err = run("doctor", "--offline")
        self.assertNotIn("tracker", out + err)


class ReviewGates(CliBase):
    def test_a_clean_tree_passes(self) -> None:
        with mock.patch("workbench.gitctx.changed_files", return_value=[]), \
             mock.patch("workbench.gitctx.added_lines", return_value=[]):
            code, out, _ = run("review", "gates")
        self.assertEqual(0, code)
        self.assertIn("pass", out)

    def test_a_finding_exits_seven_so_a_caller_can_branch(self) -> None:
        added = [("src/a.py", 1, 'API_KEY = "abcd1234abcd1234abcd"')]
        with mock.patch("workbench.gitctx.changed_files", return_value=["src/a.py"]), \
             mock.patch("workbench.gitctx.added_lines", return_value=added):
            code, _, err = run("review", "gates")
        self.assertEqual(EXIT_AUDIT, code)
        self.assertIn("src/a.py:1", err)

    def test_the_secret_value_never_reaches_the_output(self) -> None:
        added = [("src/a.py", 1, 'API_KEY = "abcd1234abcd1234abcd"')]
        with mock.patch("workbench.gitctx.changed_files", return_value=["src/a.py"]), \
             mock.patch("workbench.gitctx.added_lines", return_value=added):
            _, out, err = run("review", "gates")
        self.assertNotIn("abcd1234abcd1234abcd", out + err)


class Impl(CliBase):
    def test_check_refuses_without_a_passing_audit(self) -> None:
        """Implementing from an unaudited plan is the failure this all exists to stop."""
        path = self.root / ".workflow" / "ABC-1" / "sdd.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"key": "ABC-1", "files": [{"path": "a.py"}]}), encoding="utf-8")
        self.assertNotEqual(0, run("impl", "check", "ABC-1")[0])



class DoctorRunners(unittest.TestCase):
    """unittest is reached through the interpreter, not as a program on PATH.

    Reporting a runner that is present as missing is worse than not checking:
    it trains the reader to skip the line.
    """

    def test_a_module_runner_resolves_to_its_interpreter(self) -> None:
        from workbench.cli import doctor

        self.assertTrue(doctor._available("unittest"))

    def test_an_ordinary_runner_is_looked_up_directly(self) -> None:
        from workbench.cli import doctor

        self.assertFalse(doctor._available("a-runner-that-does-not-exist"))

if __name__ == "__main__":
    unittest.main()

import os
import sys
import tempfile
import unittest
from pathlib import Path

from workbench import verify


class Allowlist(unittest.TestCase):
    def test_known_runners_are_allowed(self) -> None:
        for command in ("pytest -q", "npm run test", "go test ./...", "cargo clippy", "make check"):
            self.assertIsNone(verify.check(command), command)

    def test_windows_executable_suffix_is_stripped(self) -> None:
        self.assertIsNone(verify.check("pytest.exe -q"))

    def test_unknown_runner_is_refused(self) -> None:
        reason = verify.check("curl https://example.com")
        self.assertIn("not a known test", reason)

    def test_git_is_not_a_verification_runner(self) -> None:
        self.assertIsNotNone(verify.check("git push"))

    def test_shell_chaining_is_refused(self) -> None:
        for command in ("pytest && rm -rf /", "pytest; echo done", "pytest | tee log", "pytest > out.txt"):
            with self.subTest(command=command):
                reason = verify.check(command)
                self.assertIn("shell features are not available", reason)

    def test_command_substitution_is_refused(self) -> None:
        self.assertIsNotNone(verify.check("pytest $(whoami)"))
        self.assertIsNotNone(verify.check("pytest `whoami`"))

    def test_empty_command_is_refused(self) -> None:
        self.assertIsNotNone(verify.check("   "))

    def test_a_refused_command_is_never_executed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            evidence = verify.run("ABC-1", ["curl https://example.com"], Path(tmp))
        self.assertEqual([], evidence.results)
        self.assertEqual(1, len(evidence.refused))
        self.assertFalse(evidence.passed)


class Execution(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def _script(self, body: str) -> str:
        """A real command line, quoted the way a verify[] entry has to be."""
        path = self.root / "script.py"
        path.write_text(body, encoding="utf-8")
        return f'"{sys.executable}" script.py'

    def test_success_is_recorded_with_its_output(self) -> None:
        evidence = verify.run("ABC-1", [self._script("print('ok-marker')")], self.root)
        self.assertTrue(evidence.passed, evidence.to_dict())
        self.assertIn("ok-marker", evidence.results[0].output)

    def test_failure_keeps_the_exit_code_and_fails_the_verdict(self) -> None:
        evidence = verify.run("ABC-1", [self._script("raise SystemExit(3)")], self.root)
        self.assertEqual(3, evidence.results[0].exit_code)
        self.assertFalse(evidence.passed)

    def test_missing_binary_is_reported_not_raised(self) -> None:
        evidence = verify.run("ABC-1", ["pytest --version"], self.root)
        self.assertEqual(1, len(evidence.results))  # ran or not, it is recorded

    def test_long_output_keeps_head_and_tail(self) -> None:
        command = self._script("print('START'); print('x' * 20000); print('END')")
        evidence = verify.run("ABC-1", [command], self.root)
        output = evidence.results[0].output
        self.assertTrue(evidence.results[0].truncated)
        self.assertIn("START", output)
        self.assertIn("END", output)
        self.assertIn("omitted", output)

    def test_no_commands_means_no_pass(self) -> None:
        self.assertFalse(verify.run("ABC-1", [], self.root).passed)


class Rendering(unittest.TestCase):
    def test_evidence_shows_the_command_and_the_verdict(self) -> None:
        evidence = verify.Evidence(key="ABC-1")
        evidence.results.append(verify.Result(command="pytest -q", exit_code=1, duration_ms=12, output="1 failed"))
        text = verify.render(evidence)
        self.assertIn("**Verdict:** fail", text)
        self.assertIn("pytest -q", text)
        self.assertIn("1 failed", text)

    def test_refused_commands_are_listed_as_not_run(self) -> None:
        evidence = verify.Evidence(key="ABC-1")
        evidence.refused.append(("curl x", "not a known runner"))
        self.assertIn("Not run", verify.render(evidence))


if __name__ == "__main__":
    unittest.main()


class Environment(unittest.TestCase):
    """Shell is refused, so `VAR=x cmd` cannot be expressed as a command.

    Without a declared env block a repo whose tests need PYTHONPATH could not
    be verified at all -- this repo included. The variables are data in the
    audited plan, reviewed alongside the commands.
    """

    def test_no_env_block_is_not_an_error(self) -> None:
        self.assertEqual(({}, []), verify.resolve_env(None))
        self.assertEqual(({}, []), verify.resolve_env({}))

    def test_a_plain_variable_is_applied(self) -> None:
        applied, rejected = verify.resolve_env({"PYTHONPATH": "lib"})
        self.assertEqual({"PYTHONPATH": "lib"}, applied)
        self.assertEqual([], rejected)

    def test_a_loader_variable_is_refused(self) -> None:
        """These run code the command allowlist never sees."""
        for name in ("LD_PRELOAD", "PATH", "NODE_OPTIONS", "PYTHONSTARTUP", "BASH_ENV"):
            with self.subTest(name=name):
                applied, rejected = verify.resolve_env({name: "anything"})
                self.assertEqual({}, applied)
                self.assertEqual(1, len(rejected))

    def test_the_refusal_is_case_insensitive(self) -> None:
        self.assertEqual({}, verify.resolve_env({"ld_preload": "x"})[0])

    def test_a_malformed_name_is_refused(self) -> None:
        self.assertEqual({}, verify.resolve_env({"NOT A NAME": "x"})[0])

    def test_a_structured_value_is_refused(self) -> None:
        self.assertEqual({}, verify.resolve_env({"A": {"b": 1}})[0])

    def test_an_oversized_value_is_refused(self) -> None:
        self.assertEqual({}, verify.resolve_env({"A": "x" * (verify.MAX_ENV_VALUE + 1)})[0])

    def test_a_non_object_block_is_refused_rather_than_ignored(self) -> None:
        applied, rejected = verify.resolve_env(["PYTHONPATH=lib"])
        self.assertEqual({}, applied)
        self.assertTrue(rejected)

    def test_a_refused_variable_blocks_the_verdict(self) -> None:
        """Refusing quietly would let a plan claim verification it did not get."""
        evidence = verify.Evidence(key="ABC-1")
        evidence.refused.append(("env LD_PRELOAD", "changes how the process loads code; not applied"))
        evidence.results.append(verify.Result(command="pytest", exit_code=0, duration_ms=1, output=""))
        self.assertFalse(evidence.passed)

    def test_only_names_reach_the_evidence_file(self) -> None:
        """A value is as likely to be a connection string as a search path."""
        evidence = verify.Evidence(key="ABC-1", env={"DATABASE_URL": "postgres://user:pw@host/db"})
        self.assertEqual(["DATABASE_URL"], evidence.to_dict()["env"])
        self.assertNotIn("postgres://", verify.render(evidence))


class Standing(unittest.TestCase):
    """A pass is a claim about one tree and one plan, not about whatever is on disk now."""

    def evidence(self, **overrides) -> dict:
        return {"verdict": "pass", "tree": "t1", "plan_sha256": "p1", **overrides}

    def test_a_pass_on_the_same_plan_and_tree_stands(self) -> None:
        self.assertIsNone(verify.standing(self.evidence(), "p1", "t1"))

    def test_a_changed_tree_does_not_stand(self) -> None:
        self.assertEqual(verify.STALE_TREE, verify.standing(self.evidence(), "p1", "t2"))

    def test_a_changed_plan_does_not_stand(self) -> None:
        self.assertEqual(verify.STALE_PLAN, verify.standing(self.evidence(), "p2", "t1"))

    def test_evidence_that_recorded_no_tree_does_not_stand_in_a_checkout(self) -> None:
        legacy = {"verdict": "pass", "plan_sha256": "p1"}
        self.assertEqual(verify.STALE_TREE, verify.standing(legacy, "p1", "t1"))

    def test_a_failed_run_never_stands(self) -> None:
        self.assertIsNotNone(verify.standing(self.evidence(verdict="fail"), "p1", "t1"))
        self.assertIsNotNone(verify.standing(None, "p1", "t1"))

    def test_the_fingerprints_reach_the_evidence_file(self) -> None:
        data = verify.Evidence(key="ABC-1", tree="t1", head="h1", plan="p1").to_dict()
        self.assertEqual(("t1", "h1", "p1"), (data["tree"], data["head"], data["plan_sha256"]))


class Approval(unittest.TestCase):
    """Nothing a plan names runs until a person approved that exact string here."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        self.repo, self.other = base / "repo", base / "other"
        self.repo.mkdir()
        self.other.mkdir()
        previous = os.environ.get("WORKBENCH_HOME")
        os.environ["WORKBENCH_HOME"] = str(base / "home")
        self.addCleanup(lambda: os.environ.pop("WORKBENCH_HOME", None) if previous is None
                        else os.environ.__setitem__("WORKBENCH_HOME", previous))

    def test_nothing_is_approved_to_begin_with(self) -> None:
        self.assertEqual(["pytest -q"], verify.unapproved(self.repo, ["pytest -q"]))

    def test_an_approved_command_is_approved_in_that_repo_only(self) -> None:
        verify.approve(self.repo, ["pytest -q"])
        self.assertEqual([], verify.unapproved(self.repo, ["pytest -q"]))
        self.assertEqual(["pytest -q"], verify.unapproved(self.other, ["pytest -q"]))

    def test_one_changed_character_asks_again(self) -> None:
        verify.approve(self.repo, ["pytest -q"])
        self.assertEqual(["pytest -x"], verify.unapproved(self.repo, ["pytest -x"]))

    def test_an_env_entry_is_approved_by_name_and_value(self) -> None:
        verify.approve(self.repo, verify.entries(["pytest"], {"PYTHONPATH": "lib"}))
        self.assertEqual([], verify.unapproved(self.repo, verify.entries(["pytest"], {"PYTHONPATH": "lib"})))
        changed = verify.entries(["pytest"], {"PYTHONPATH": "evil"})
        self.assertEqual(["env PYTHONPATH=evil"], verify.unapproved(self.repo, changed))

    def test_the_plan_s_own_key_is_abstracted_as_a_whole_token(self) -> None:
        cases = {
            "python lib/wb.py sdd audit ABC-1": "python lib/wb.py sdd audit <KEY>",
            "pytest .workflow/ABC-1/tests": "pytest .workflow/<KEY>/tests",
            "pytest --key=ABC-1": "pytest --key=<KEY>",
            "pytest ABC-12": "pytest ABC-12",
            "pytest x-ABC-1": "pytest x-ABC-1",
            "pytest ABC-1-x": "pytest ABC-1-x",
            "pytest XABC-1": "pytest XABC-1",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(expected, verify.abstract(text, "ABC-1"))

    def test_without_a_key_nothing_is_abstracted(self) -> None:
        self.assertEqual(["wb sdd audit ABC-1"], verify.entries(["wb sdd audit ABC-1"], {}))

    def test_an_approval_covers_the_same_command_on_another_ticket(self) -> None:
        verify.approve(self.repo, verify.entries(["wb sdd audit ABC-1"], {"P": "ABC-1"}, "ABC-1"))
        self.assertEqual([], verify.unapproved(self.repo, verify.entries(["wb sdd audit ABC-2"], {"P": "ABC-2"}, "ABC-2"), "ABC-2"))

    def test_the_abstract_approval_covers_nothing_else(self) -> None:
        verify.approve(self.repo, verify.entries(["wb sdd audit ABC-1"], {"P": "lib"}, "ABC-1"))
        # A different key spelled out, a changed flag, a changed env value.
        self.assertTrue(verify.unapproved(self.repo, verify.entries(["wb sdd audit ABC-1"], {}, "ABC-2"), "ABC-2"))
        self.assertTrue(verify.unapproved(self.repo, verify.entries(["wb sdd audit ABC-2 -x"], {}, "ABC-2"), "ABC-2"))
        self.assertTrue(verify.unapproved(self.repo, verify.entries([], {"P": "evil"}, "ABC-2"), "ABC-2"))

    def test_a_verbatim_approval_from_before_still_counts_for_its_ticket(self) -> None:
        verify.approve(self.repo, ["wb sdd audit ABC-1"])
        self.assertEqual([], verify.unapproved(self.repo, verify.entries(["wb sdd audit ABC-1"], {}, "ABC-1"), "ABC-1"))
        self.assertTrue(verify.unapproved(self.repo, verify.entries(["wb sdd audit ABC-2"], {}, "ABC-2"), "ABC-2"))

    def test_approvals_live_outside_the_checkout(self) -> None:
        verify.approve(self.repo, ["pytest"])
        self.assertFalse(any(self.repo.rglob("*")))
        self.assertTrue(verify.approvals_path().is_file())

    def test_an_unreadable_file_is_refused_rather_than_read_as_empty(self) -> None:
        verify.approvals_path().parent.mkdir(parents=True)
        verify.approvals_path().write_text("{not json", encoding="utf-8")
        with self.assertRaises(verify.UsageError):
            verify.unapproved(self.repo, ["pytest"])

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

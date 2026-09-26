"""WB-56: wb run drives a ticket headless and stops only for a person."""

import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from test_cli import run  # noqa: E402
from test_light import MONEY_FIXED, TEST_REFUND, LightPath  # noqa: E402

from workbench import agent  # noqa: E402
from workbench.cli import run as run_cli  # noqa: E402
from workbench.errors import EXIT_USAGE  # noqa: E402


class Driven(LightPath):
    def setUp(self) -> None:
        super().setUp()
        environment = mock.patch.dict(os.environ, {"WB_RUN": "1"})
        environment.start()
        self.addCleanup(environment.stop)
        available = mock.patch.object(agent.ClaudeCode, "missing", return_value=None)
        available.start()
        self.addCleanup(available.stop)
        self.prompts: list[str] = []

    def agent_does(self, *sessions):
        """Each call plays the next scripted session."""
        script = list(sessions)

        def session(adapter, prompt, root, timeout_s):
            self.prompts.append(prompt)
            return script.pop(0)()

        return mock.patch.object(agent.ClaudeCode, "session", autospec=True, side_effect=session)

    def works(self) -> agent.Session:
        self.write("shop/money.py", MONEY_FIXED)
        self.write("tests/test_money.py", TEST_REFUND)
        code, out, err = run("finish", "ABC-1", "-m", "fix: print refunds with their real amount")
        self.assertEqual(0, code, out + err)
        self.git("add", "--", "shop/money.py", "tests/test_money.py")
        self.git("commit", "-q", "-F", ".workflow/ABC-1/commit.txt")
        return agent.Session(0, report={"num_turns": 9, "total_cost_usd": 0.12, "usage": {"input_tokens": 5}})

    def idles(self) -> agent.Session:
        return agent.Session(0, report={"num_turns": 2})

    def times_out(self) -> agent.Session:
        self.write("shop/money.py", MONEY_FIXED)
        return agent.Session(124, timed_out=True, error="timed out after 5s")

    def record(self) -> dict:
        return json.loads((self.root / ".workflow" / "ABC-1" / "run.json").read_text(encoding="utf-8"))


class Runs(Driven):
    def test_it_is_off_unless_the_repo_or_the_call_turns_it_on(self) -> None:
        with mock.patch.dict(os.environ, {"WB_RUN": ""}):
            code, _, err = run("run", "ABC-1")
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn('"run": {"enabled": true}', err)

    def test_it_drives_the_ticket_to_a_commit_and_records_the_session(self) -> None:
        with self.agent_does(self.works):
            code, out, err = run("run", "ABC-1", "--until", "commit")
        self.assertEqual(0, code, out + err)
        self.assertIn("done: committed", out)
        self.assertIn("session 1: 9 turns, US$0.12", out)
        self.assertIn("Pick up ticket ABC-1", self.prompts[0])
        self.assertIn("Never approve anything", self.prompts[0])
        sessions = self.record()["sessions"]
        self.assertEqual(1, len(sessions))
        self.assertEqual({"input_tokens": 5}, sessions[0]["usage"])

    def test_a_ticket_already_at_its_goal_starts_no_session(self) -> None:
        with self.agent_does(self.works):
            run("run", "ABC-1", "--until", "commit")
        with self.agent_does() as session:
            code, out, _ = run("run", "ABC-1", "--until", "commit")
        self.assertEqual(0, code)
        session.assert_not_called()
        self.assertIn("done", out)

    def test_a_timeout_leaves_the_work_resumable_and_a_rerun_resumes(self) -> None:
        with self.agent_does(self.times_out):
            code, _, err = run("run", "ABC-1", "--until", "commit")
        self.assertNotEqual(0, code)
        self.assertIn("timed out", err)
        self.assertIn("nothing is lost", err)
        self.assertIn("wb run ABC-1", err)
        self.assertEqual(MONEY_FIXED, (self.root / "shop" / "money.py").read_text(encoding="utf-8"))
        with self.agent_does(self.works):
            code, out, err = run("run", "ABC-1", "--until", "commit")
        self.assertEqual(0, code, out + err)
        self.assertEqual(2, len(self.record()["sessions"]))

    def edits_only(self) -> agent.Session:
        self.write("shop/money.py", MONEY_FIXED + f"# pass {len(self.prompts)}" + chr(10))
        return agent.Session(0, report={"num_turns": 4})

    def test_a_session_that_changes_code_is_progress_even_at_the_same_stage(self) -> None:
        with self.agent_does(self.edits_only, self.edits_only):
            code, _, err = run("run", "ABC-1", "--until", "commit", "--max-sessions", "2")
        self.assertNotEqual(0, code)
        self.assertNotIn("no progress", err)
        self.assertIn("not at its goal after 2 session(s)", err)

    def test_each_session_is_told_how_to_call_wb(self) -> None:
        with self.agent_does(self.works):
            run("run", "ABC-1", "--until", "commit")
        self.assertIn("wb.py", self.prompts[0])
        self.assertIn("not on PATH", self.prompts[0])

    def test_a_session_that_moves_nothing_stops_the_run(self) -> None:
        with self.agent_does(self.idles, self.idles):
            code, _, err = run("run", "ABC-1", "--until", "commit")
        self.assertNotEqual(0, code)
        self.assertIn("no progress", err)
        self.assertEqual(1, len(self.record()["sessions"]), "it does not pay for a second idle session")


class Waits(Driven):
    KIND = "feature"

    def test_it_stops_at_a_person_s_decision_before_spending_a_session(self) -> None:
        plan = {
            "schema": 1, "key": "ABC-1", "preset": "prototype", "persona": "x", "objective": "refunds",
            "evidence": [{"claim": "money", "file": "shop/money.py", "line": 1, "quote": "def format_money(cents):"}],
            "files": [{"path": "shop/money.py", "change": "edit", "lines": 4, "why": "the fix"}],
            "zones": {}, "steps": [], "tests": [{"kind": "unit", "asserts": "a refund keeps its value"}], "verify": ["python -m unittest -q"],
            "rollback": "revert", "product": {"metric": "m", "who_asked": "w"}, "questions": [],
        }
        run("start", "ABC-1")
        (self.root / ".workflow" / "ABC-1" / "sdd.json").write_text(json.dumps(plan), encoding="utf-8")
        self.assertEqual(0, run("sdd", "audit", "ABC-1")[0])
        with self.agent_does() as session:
            code, out, _ = run("run", "ABC-1")
        self.assertEqual(run_cli.EXIT_WAITING, code)
        session.assert_not_called()
        self.assertIn("waiting on you: verify", out)
        self.assertIn("approve: wb approve ABC-1 ", out)
        self.assertIn("then resume: wb run ABC-1", out)


class Adapter(unittest.TestCase):
    def test_the_claude_command_carries_the_session_settings(self) -> None:
        with mock.patch("shutil.which", return_value="/bin/claude"):
            argv = agent.ClaudeCode(model="m", permission_mode="bypassPermissions", plugin_dir="/p").argv("go")
        self.assertEqual(["/bin/claude", "-p", "go", "--output-format", "json"], argv[:5])
        self.assertEqual("bypassPermissions", argv[argv.index("--permission-mode") + 1])
        self.assertEqual("m", argv[argv.index("--model") + 1])
        self.assertEqual("/p", argv[argv.index("--plugin-dir") + 1])

    def test_the_defaults_leave_the_model_and_plugin_to_claude(self) -> None:
        with mock.patch("shutil.which", return_value="/bin/claude"):
            argv = agent.ClaudeCode().argv("go")
        self.assertNotIn("--model", argv)
        self.assertNotIn("--plugin-dir", argv)
        self.assertEqual("acceptEdits", argv[argv.index("--permission-mode") + 1])

    def test_a_report_that_is_not_json_is_empty(self) -> None:
        self.assertEqual({}, agent.parse("oops"))
        self.assertEqual({}, agent.parse("[1]"))
        self.assertEqual(3, agent.parse('{"num_turns": 3}')["num_turns"])

    def test_an_error_report_is_not_ok(self) -> None:
        self.assertFalse(agent.Session(0, report={"is_error": True}).ok)
        self.assertFalse(agent.Session(124, timed_out=True).ok)
        self.assertTrue(agent.Session(0).ok)


if __name__ == "__main__":
    unittest.main()

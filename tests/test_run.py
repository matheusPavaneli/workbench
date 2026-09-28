"""WB-56: wb run drives a ticket headless and stops only for a person."""

from __future__ import annotations

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

    def test_setting_sources_pass_through_only_when_set(self) -> None:
        with mock.patch("shutil.which", return_value="/bin/claude"):
            argv = agent.ClaudeCode(setting_sources="project,local").argv("go")
            plain = agent.ClaudeCode().argv("go")
        self.assertEqual("project,local", argv[argv.index("--setting-sources") + 1])
        self.assertNotIn("--setting-sources", plain)

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


class Selection(Driven):
    """WB-61: the agent is a choice, Claude Code by default."""

    def drive(self, *args: str, config: dict | None = None):
        chosen: list[str] = []

        def session(adapter, prompt, root, timeout_s):
            chosen.append(adapter.name)
            return self.works()

        patches = [mock.patch.object(kind, "session", autospec=True, side_effect=session) for kind in agent.AGENTS.values()]
        patches += [mock.patch.object(kind, "missing", return_value=None) for kind in agent.AGENTS.values()]
        if config is not None:
            patches.append(mock.patch.object(run_cli, "_config", return_value=config))
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        code, out, err = run("run", "ABC-1", "--until", "commit", *args)
        return code, out + err, chosen

    def test_claude_code_is_the_default(self) -> None:
        code, output, chosen = self.drive()
        self.assertEqual(0, code, output)
        self.assertEqual(["claude-code"], chosen)
        self.assertEqual("claude-code", self.record()["sessions"][0]["agent"])

    def test_the_repo_config_picks_the_agent(self) -> None:
        code, output, chosen = self.drive(config={"agent": "codex"})
        self.assertEqual(0, code, output)
        self.assertEqual(["codex"], chosen)

    def test_the_flag_overrides_the_repo_config(self) -> None:
        code, output, chosen = self.drive("--agent", "gemini", config={"agent": "codex"})
        self.assertEqual(0, code, output)
        self.assertEqual(["gemini"], chosen)

    def test_an_unknown_agent_is_refused_naming_the_known_ones(self) -> None:
        code, output, chosen = self.drive(config={"agent": "vim"})
        self.assertNotEqual(0, code)
        self.assertEqual([], chosen)
        self.assertIn("unknown agent 'vim'", output)
        for name in agent.AGENTS:
            self.assertIn(name, output)

    def test_a_missing_agent_names_its_own_install(self) -> None:
        with mock.patch.object(agent.Codex, "missing", return_value="codex is not on PATH"):
            code, _, err = run("run", "ABC-1", "--agent", "codex")
        self.assertNotEqual(0, code)
        self.assertIn("npm i -g @openai/codex", err)
        self.assertNotIn("claude-code", err)


class OtherAdapters(unittest.TestCase):
    def test_select_hands_each_agent_only_the_settings_it_takes(self) -> None:
        codex = agent.select("codex", model="m", permission_mode="acceptEdits", plugin_dir="/p")
        self.assertEqual("m", codex.model)
        self.assertFalse(hasattr(codex, "permission_mode"))
        claude = agent.select("claude-code", permission_mode="bypassPermissions")
        self.assertEqual("bypassPermissions", claude.permission_mode)
        with self.assertRaises(ValueError):
            agent.select("vim")

    def test_the_codex_command_runs_exec_with_json_and_workspace_writes(self) -> None:
        with mock.patch("shutil.which", return_value="/bin/codex"):
            argv = agent.Codex(model="gpt-5").argv("go")
            plain = agent.Codex().argv("go")
        self.assertEqual(["/bin/codex", "exec", "--json"], argv[:3])
        self.assertEqual("workspace-write", argv[argv.index("--sandbox") + 1])
        self.assertEqual("gpt-5", argv[argv.index("--model") + 1])
        self.assertEqual("go", argv[-1])
        self.assertNotIn("--model", plain)

    def test_the_codex_event_stream_becomes_the_same_report(self) -> None:
        stream = chr(10).join([
            '{"type":"thread.started","thread_id":"t-1"}',
            "not json",
            '{"type":"turn.completed","usage":{"input_tokens":10,"cached_input_tokens":4,"output_tokens":2}}',
            "[1]",
            '{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":1}}',
        ])
        report = agent.parse_codex(stream)
        self.assertEqual("t-1", report["session_id"])
        self.assertEqual(2, report["num_turns"])
        self.assertEqual({"input_tokens": 15, "cached_input_tokens": 4, "output_tokens": 3}, report["usage"])
        self.assertTrue(agent.Session(0, report=report).ok)

    def test_a_failed_codex_turn_is_not_ok(self) -> None:
        report = agent.parse_codex('{"type":"turn.failed","error":{"message":"stream ended"}}')
        self.assertFalse(agent.Session(0, report=report).ok)
        self.assertEqual({}, agent.parse_codex(""))

    def test_the_gemini_command_is_headless_json_with_edits_approved(self) -> None:
        with mock.patch("shutil.which", return_value="/bin/gemini"):
            argv = agent.Gemini(model="gemini-3-pro").argv("go")
        self.assertEqual(["/bin/gemini", "-p", "go", "--output-format", "json"], argv[:5])
        self.assertEqual("auto_edit", argv[argv.index("--approval-mode") + 1])
        self.assertEqual("gemini-3-pro", argv[argv.index("--model") + 1])

    def test_gemini_token_counts_are_summed_over_its_models(self) -> None:
        payload = {"response": "done", "stats": {"models": {
            "a": {"tokens": {"prompt": 10, "candidates": 3, "cached": 2}},
            "b": {"tokens": {"prompt": 1, "candidates": 1}},
        }}}
        report = agent.parse_gemini(json.dumps(payload))
        self.assertEqual({"prompt": 11, "candidates": 4, "cached": 2}, report["usage"])
        self.assertTrue(agent.Session(0, report=report).ok)
        self.assertFalse(agent.Session(0, report=agent.parse_gemini('{"error": {"message": "quota"}}')).ok)
        self.assertEqual({}, agent.parse_gemini("oops"))

    def test_the_cursor_command_prints_json_and_may_write(self) -> None:
        with mock.patch("shutil.which", return_value="/bin/agent"):
            argv = agent.Cursor().argv("go", Path("/repo"))
        self.assertEqual(["/bin/agent", "-p", "go", "--output-format", "json"], argv[:5])
        self.assertIn("--force", argv)
        self.assertEqual(str(Path("/repo")), argv[argv.index("--workspace") + 1])
        self.assertNotIn("--model", argv)

    def test_the_cursor_result_reads_like_claude_s(self) -> None:
        report = agent.parse('{"type":"result","is_error":true,"session_id":"s-9","result":"x"}')
        self.assertEqual("s-9", agent.Session(0, report=report).to_dict()["session_id"])
        self.assertFalse(agent.Session(0, report=report).ok)


if __name__ == "__main__":
    unittest.main()

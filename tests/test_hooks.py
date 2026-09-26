from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workbench import audit, gitctx, hooks, profile

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "lib"))

import wb_hook  # noqa: E402


def _plan(key: str = "ABC-1", files: tuple = ("src/checkout.py",)) -> dict:
    return {
        "schema": 1,
        "key": key,
        "preset": "startup",
        "persona": "fullstack-specialist",
        "objective": "Validate the coupon before charging.",
        "evidence": [
            {"claim": "c", "file": "src/checkout.py", "line": 2, "quote": "charge = create_charge(total)"}
        ],
        "files": [{"path": path, "change": "edit", "why": "w"} for path in files],
        "zones": profile.critical_zones(list(files)),
        "steps": [{"do": "move validation above the charge"}],
        "tests": [{"kind": "regression", "target": "tests/test_checkout.py", "asserts": "expired coupon returns 422"}],
        "verify": ["python -m unittest -q"],
        "rollback": "revert the commit",
        "product": {"metric": "m", "who_asked": "w"},
        "questions": [],
    }


class HookCase(unittest.TestCase):
    """A checkout on a ticket branch, with an audited plan for that ticket."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.git("init", "-q", ".")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        self.write(".gitignore", ".workflow/\n")
        (self.root / "src").mkdir()
        self.write("src/checkout.py", "def pay(total):\n    charge = create_charge(total)\n")
        self.write("src/other.py", "x = 1\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")
        self.git("switch", "-q", "-c", "ABC-1-coupon")
        environment = mock.patch.dict(os.environ, {"WB_NO_HOOKS": ""})
        environment.start()
        self.addCleanup(environment.stop)

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=str(self.root), capture_output=True, text=True, check=False)

    def write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def record(self, key: str, plan: dict, report: dict | None = None) -> None:
        directory = self.root / ".workflow" / key
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "sdd.json").write_text(json.dumps(plan), encoding="utf-8")
        if report is None:
            report = audit.run(plan, self.root).to_dict()
        (directory / "audit.json").write_text(json.dumps(report), encoding="utf-8")

    def edit(self, path: str, tool: str = "Edit") -> dict | None:
        field = "notebook_path" if tool == "NotebookEdit" else "file_path"
        payload = {"tool_name": tool, "tool_input": {field: str(self.root / path)}, "cwd": str(self.root)}
        return hooks.pre_tool_use(payload, self.root)

    def denied(self, answer: dict | None) -> str:
        self.assertIsNotNone(answer)
        output = answer["hookSpecificOutput"]
        self.assertEqual("deny", output["permissionDecision"])
        return output["permissionDecisionReason"]


class Scope(HookCase):
    def setUp(self) -> None:
        super().setUp()
        self.record("ABC-1", _plan())

    def test_a_planned_file_may_be_edited(self) -> None:
        self.assertIsNone(self.edit("src/checkout.py"))

    def test_an_unplanned_file_is_refused_naming_the_plan(self) -> None:
        reason = self.denied(self.edit("src/other.py"))
        self.assertIn("src/other.py", reason)
        self.assertIn("wb sdd audit ABC-1", reason)

    def test_every_file_writing_tool_is_held_to_the_plan(self) -> None:
        for tool in ("Write", "MultiEdit", "NotebookEdit"):
            with self.subTest(tool=tool):
                self.denied(self.edit("src/other.py", tool))

    def test_a_relative_path_is_read_against_the_checkout(self) -> None:
        payload = {"tool_name": "Write", "tool_input": {"file_path": "src/other.py"}}
        self.denied(hooks.pre_tool_use(payload, self.root))

    def test_the_plan_itself_stays_writable(self) -> None:
        self.assertIsNone(self.edit(".workflow/ABC-1/sdd.json"))

    def test_a_file_outside_the_checkout_is_not_this_hook_s_business(self) -> None:
        with tempfile.TemporaryDirectory() as elsewhere:
            payload = {"tool_name": "Write", "tool_input": {"file_path": str(Path(elsewhere) / "notes.md")}}
            self.assertIsNone(hooks.pre_tool_use(payload, self.root))

    def test_a_file_another_audited_plan_lists_is_accounted_for(self) -> None:
        self.write("src/other.py", "x = 2\n")
        self.record("ABC-2", _plan("ABC-2", files=("src/other.py",)))
        self.assertIsNone(self.edit("src/other.py"))

    def test_a_plan_edited_after_its_audit_covers_no_edit(self) -> None:
        plan = _plan(files=("src/checkout.py", "src/other.py"))
        (self.root / ".workflow" / "ABC-1" / "sdd.json").write_text(json.dumps(plan), encoding="utf-8")
        reason = self.denied(self.edit("src/checkout.py"))
        self.assertIn("wb sdd audit ABC-1", reason)

    def test_a_tool_that_writes_no_file_is_ignored(self) -> None:
        payload = {"tool_name": "Read", "tool_input": {"file_path": str(self.root / "src/other.py")}}
        self.assertIsNone(hooks.pre_tool_use(payload, self.root))


class Silence(HookCase):
    """No opinion, no output: a hook that gets in the way of unrelated work is
    the hook that gets switched off."""

    def test_no_ticket_on_the_branch(self) -> None:
        self.record("ABC-1", _plan())
        self.git("switch", "-q", "-c", "chore/bump-node-20")
        self.assertIsNone(self.edit("src/other.py"))

    def test_a_ticket_with_no_plan(self) -> None:
        (self.root / ".workflow" / "ABC-1").mkdir(parents=True)
        (self.root / ".workflow" / "ABC-1" / "triage.json").write_text("{}", encoding="utf-8")
        self.assertIsNone(self.edit("src/other.py"))

    def test_a_plan_whose_audit_never_passed(self) -> None:
        self.record("ABC-1", _plan(), {"verdict": "fail", "under_way": False})
        self.assertIsNone(self.edit("src/other.py"))

    def test_a_plan_that_failed_a_re_audit_still_holds_edits(self) -> None:
        """Once under way, a failing correction is not a way out of the guard."""
        self.record("ABC-1", _plan(), {"verdict": "fail", "under_way": True})
        self.denied(self.edit("src/checkout.py"))


class Switches(HookCase):
    def setUp(self) -> None:
        super().setUp()
        self.record("ABC-1", _plan())

    def test_the_environment_turns_the_hooks_off(self) -> None:
        with mock.patch.dict(os.environ, {"WB_NO_HOOKS": "1"}):
            self.assertIsNone(self.edit("src/other.py"))
            self.write("src/checkout.py", "changed\n")
            self.assertIsNone(hooks.stop({}, self.root))

    def test_the_repo_config_turns_the_hooks_off(self) -> None:
        self.write(".workflow/config.json", json.dumps({"hooks": False}))
        self.assertIsNone(self.edit("src/other.py"))
        self.write("src/checkout.py", "changed\n")
        self.assertIsNone(hooks.stop({}, self.root))

    def test_modes(self) -> None:
        self.assertEqual(hooks.ON, hooks.mode(self.root))
        self.write(".workflow/config.json", json.dumps({"hooks": "strict"}))
        self.assertEqual(hooks.STRICT, hooks.mode(self.root))


class Stop(HookCase):
    def setUp(self) -> None:
        super().setUp()
        self.plan = _plan()
        self.record("ABC-1", self.plan)

    def test_nothing_changed_says_nothing(self) -> None:
        self.assertIsNone(hooks.stop({}, self.root))

    def test_changed_work_with_no_verification_is_reported_not_blocked(self) -> None:
        self.write("src/checkout.py", "def pay(total):\n    validate()\n")
        answer = hooks.stop({}, self.root)
        self.assertEqual({"systemMessage"}, set(answer))
        self.assertIn("wb impl verify ABC-1", answer["systemMessage"])

    def test_strict_mode_blocks_once(self) -> None:
        self.write(".workflow/config.json", json.dumps({"hooks": "strict"}))
        self.write("src/checkout.py", "def pay(total):\n    validate()\n")
        answer = hooks.stop({"stop_hook_active": False}, self.root)
        self.assertEqual("block", answer["decision"])
        self.assertIn("wb impl verify ABC-1", answer["reason"])
        again = hooks.stop({"stop_hook_active": True}, self.root)
        self.assertNotIn("decision", again)

    def test_standing_evidence_says_nothing(self) -> None:
        self.write("src/checkout.py", "def pay(total):\n    validate()\n")
        evidence = {
            "verdict": "pass",
            "plan_sha256": audit.digest(self.plan),
            "tree": gitctx.tree(self.root),
        }
        self.write(".workflow/ABC-1/evidence.json", json.dumps(evidence))
        self.assertIsNone(hooks.stop({}, self.root))

    def test_evidence_for_older_code_is_reported(self) -> None:
        self.write("src/checkout.py", "def pay(total):\n    validate()\n")
        evidence = {"verdict": "pass", "plan_sha256": audit.digest(self.plan), "tree": gitctx.tree(self.root)}
        self.write(".workflow/ABC-1/evidence.json", json.dumps(evidence))
        self.write("src/checkout.py", "def pay(total):\n    validate(total)\n")
        self.assertIn("changed since", hooks.stop({}, self.root)["systemMessage"])


class EntryPoint(HookCase):
    def setUp(self) -> None:
        super().setUp()
        self.record("ABC-1", _plan())

    def call(self, event: str, payload: object) -> tuple[int, str]:
        out = io.StringIO()
        raw = payload if isinstance(payload, str) else json.dumps(payload)
        code = wb_hook.main([event], io.StringIO(raw), out)
        return code, out.getvalue()

    def payload(self, path: str) -> dict:
        return {"tool_name": "Edit", "tool_input": {"file_path": str(self.root / path)}, "cwd": str(self.root)}

    def test_an_unplanned_edit_prints_a_deny(self) -> None:
        code, output = self.call("pre-tool-use", self.payload("src/other.py"))
        self.assertEqual(0, code)
        self.assertEqual("deny", json.loads(output)["hookSpecificOutput"]["permissionDecision"])

    def test_an_allowed_edit_prints_nothing(self) -> None:
        self.assertEqual((0, ""), self.call("pre-tool-use", self.payload("src/checkout.py")))

    def test_malformed_input_is_reported_and_allowed(self) -> None:
        code, output = self.call("pre-tool-use", "{not json")
        self.assertEqual(0, code)
        self.assertIn("did not run", json.loads(output)["systemMessage"])

    def test_an_unknown_event_is_reported(self) -> None:
        code, output = self.call("post-tool-use", {})
        self.assertEqual(0, code)
        self.assertIn("unknown hook event", json.loads(output)["systemMessage"])

    def test_an_oversized_payload_is_refused(self) -> None:
        code, output = self.call("stop", " " * (wb_hook.MAX_PAYLOAD + 1))
        self.assertIn("larger than", json.loads(output)["systemMessage"])

    def test_it_runs_as_a_script(self) -> None:
        completed = subprocess.run(
            [sys.executable, str(ROOT / "lib" / "wb_hook.py"), "pre-tool-use"],
            input=json.dumps(self.payload("src/other.py")),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        self.assertEqual(0, completed.returncode, completed.stderr)
        self.assertEqual("deny", json.loads(completed.stdout)["hookSpecificOutput"]["permissionDecision"])


class Manifest(unittest.TestCase):
    def setUp(self) -> None:
        self.config = json.loads((ROOT / "hooks" / "hooks.json").read_text(encoding="utf-8"))["hooks"]

    def test_every_hook_calls_the_entry_point_through_the_plugin_root(self) -> None:
        for event, groups in self.config.items():
            for group in groups:
                for hook in group["hooks"]:
                    with self.subTest(event=event):
                        self.assertEqual("command", hook["type"])
                        self.assertIn('"${CLAUDE_PLUGIN_ROOT}/lib/wb_hook.py"', hook["command"])
                        self.assertIn("timeout", hook)

    def test_every_file_writing_tool_is_matched(self) -> None:
        matcher = self.config["PreToolUse"][0]["matcher"]
        self.assertEqual(set(hooks.PATH_FIELDS), set(matcher.split("|")))

    def test_each_event_names_one_the_entry_point_accepts(self) -> None:
        expected = {"PreToolUse": "pre-tool-use", "Stop": "stop"}
        self.assertEqual(set(expected), set(self.config))
        for event, argument in expected.items():
            self.assertTrue(self.config[event][0]["hooks"][0]["command"].endswith(f" {argument}"))
            self.assertIn(argument, wb_hook.EVENTS)


if __name__ == "__main__":
    unittest.main()

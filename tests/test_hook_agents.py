"""WB-61: the scope guard speaks each agent's hook protocol, and decides once."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from test_hooks import HookCase, _plan  # noqa: E402

import wb_hook  # noqa: E402
from workbench import hook_agents, hooks  # noqa: E402

CODEX = hook_agents.AGENTS["codex"]
GEMINI = hook_agents.AGENTS["gemini"]
CURSOR = hook_agents.AGENTS["cursor"]
CLAUDE = hook_agents.AGENTS["claude-code"]


def _patch(*lines: str) -> dict:
    body = "\n".join(["*** Begin Patch", *lines, "*** End Patch"])
    return {"tool_name": "apply_patch", "tool_input": {"command": body}}


class Guarded(HookCase):
    def setUp(self) -> None:
        super().setUp()
        self.record("ABC-1", _plan())

    def hook(self, *argv: str, payload: dict) -> dict | None:
        out = io.StringIO()
        self.assertEqual(0, wb_hook.main(list(argv), io.StringIO(json.dumps({**payload, "cwd": str(self.root)})), out))
        text = out.getvalue().strip()
        return json.loads(text) if text else None


class Claude(Guarded):
    def test_the_default_agent_answers_exactly_as_before(self) -> None:
        payload = {"tool_name": "Edit", "tool_input": {"file_path": str(self.root / "src/other.py")}, "cwd": str(self.root)}
        self.assertEqual(hooks.pre_tool_use(payload, self.root), self.hook("pre-tool-use", payload=payload))
        self.assertEqual(
            hooks.pre_tool_use(payload, self.root), self.hook("--agent", "claude-code", "pre-tool-use", payload=payload)
        )
        answer = self.hook("pre-tool-use", payload=payload)
        self.assertEqual(["hookSpecificOutput"], list(answer))
        self.assertEqual("deny", answer["hookSpecificOutput"]["permissionDecision"])
        self.assertEqual("PreToolUse", answer["hookSpecificOutput"]["hookEventName"])

    def test_the_neutral_guard_takes_paths_not_payloads(self) -> None:
        self.assertIsNone(hooks.guard_edit(["src/checkout.py"], self.root))
        self.assertIn("src/other.py", hooks.guard_edit(["src/checkout.py", "src/other.py"], self.root))

    def test_the_claude_stop_holds_once_then_reports(self) -> None:
        with mock.patch.object(hooks, "check_stop", return_value=("unverified", True)):
            self.assertEqual({"decision": "block", "reason": "unverified"}, hook_agents.stop(CLAUDE, {}, self.root))
            self.assertEqual(
                {"systemMessage": "unverified"}, hook_agents.stop(CLAUDE, {"stop_hook_active": True}, self.root)
            )


class Codex(Guarded):
    def test_a_patch_touching_an_unplanned_file_is_refused_naming_it(self) -> None:
        answer = self.hook("--agent", "codex", "pre-tool-use", payload=_patch(
            "*** Update File: src/checkout.py", "@@", "-a", "+b", "*** Add File: src/other.py", "+x = 3",
        ))
        output = answer["hookSpecificOutput"]
        self.assertEqual("deny", output["permissionDecision"])
        self.assertIn("src/other.py", output["permissionDecisionReason"])
        self.assertNotIn("src/checkout.py is not", output["permissionDecisionReason"])

    def test_a_patch_touching_only_planned_files_is_allowed(self) -> None:
        self.assertIsNone(
            self.hook("--agent", "codex", "pre-tool-use", payload=_patch("*** Update File: src/checkout.py", "+b"))
        )

    def test_a_move_destination_is_a_touched_path(self) -> None:
        self.assertEqual(
            ["src/checkout.py", "src/renamed.py"],
            hook_agents.patch_paths("*** Update File: src/checkout.py\n*** Move to: src/renamed.py\n"),
        )
        answer = self.hook("--agent", "codex", "pre-tool-use", payload=_patch(
            "*** Update File: src/checkout.py", "*** Move to: src/renamed.py",
        ))
        self.assertIn("src/renamed.py", answer["hookSpecificOutput"]["permissionDecisionReason"])

    def test_patch_paths_are_read_from_the_session_s_directory(self) -> None:
        """A Codex session started in src/ writes paths relative to src/."""
        payload = {**_patch("*** Update File: checkout.py", "+b"), "cwd": str(self.root / "src")}
        out = io.StringIO()
        self.assertEqual(0, wb_hook.main(["--agent", "codex", "pre-tool-use"], io.StringIO(json.dumps(payload)), out))
        self.assertEqual("", out.getvalue(), "src/checkout.py is planned")
        payload = {**_patch("*** Update File: other.py", "+b"), "cwd": str(self.root / "src")}
        out = io.StringIO()
        wb_hook.main(["--agent", "codex", "pre-tool-use"], io.StringIO(json.dumps(payload)), out)
        self.assertIn("src/other.py", json.loads(out.getvalue())["hookSpecificOutput"]["permissionDecisionReason"])

    def test_a_tool_that_writes_nothing_is_let_through(self) -> None:
        self.assertIsNone(
            self.hook("--agent", "codex", "pre-tool-use", payload={"tool_name": "Bash", "tool_input": {"command": "ls"}})
        )

    def test_hooks_off_lets_every_patch_through(self) -> None:
        with mock.patch.dict("os.environ", {"WB_NO_HOOKS": "1"}):
            self.assertIsNone(
                self.hook("--agent", "codex", "pre-tool-use", payload=_patch("*** Add File: src/other.py"))
            )


class Gemini(Guarded):
    def test_an_unplanned_write_is_denied_with_its_reason(self) -> None:
        for tool in ("write_file", "replace"):
            with self.subTest(tool=tool):
                answer = self.hook("--agent", "gemini", "pre-tool-use", payload={
                    "tool_name": tool, "tool_input": {"file_path": str(self.root / "src/other.py")},
                })
                self.assertEqual("deny", answer["decision"])
                self.assertIn("src/other.py", answer["reason"])

    def test_a_planned_write_is_allowed(self) -> None:
        self.assertIsNone(self.hook("--agent", "gemini", "pre-tool-use", payload={
            "tool_name": "write_file", "tool_input": {"file_path": "src/checkout.py"},
        }))

    def test_after_agent_denies_once_in_strict_mode_then_reports(self) -> None:
        with mock.patch.object(hooks, "check_stop", return_value=("unverified", True)):
            self.assertEqual({"decision": "deny", "reason": "unverified"}, hook_agents.stop(GEMINI, {}, self.root))
            self.assertEqual({"systemMessage": "unverified"}, hook_agents.stop(GEMINI, {"stop_hook_active": True}, self.root))
        with mock.patch.object(hooks, "check_stop", return_value=("unverified", False)):
            self.assertEqual({"systemMessage": "unverified"}, hook_agents.stop(GEMINI, {}, self.root))


class Cursor(Guarded):
    def test_an_unplanned_write_is_denied_in_cursor_s_own_shape(self) -> None:
        for field in ("file_path", "path"):
            with self.subTest(field=field):
                answer = self.hook("--agent", "cursor", "pre-tool-use", payload={
                    "tool_name": "Write", "tool_input": {field: str(self.root / "src/other.py")},
                })
                self.assertEqual("deny", answer["permission"])
                self.assertIn("src/other.py", answer["agent_message"])

    def test_a_write_with_no_path_it_can_read_is_allowed(self) -> None:
        self.assertIsNone(self.hook("--agent", "cursor", "pre-tool-use", payload={
            "tool_name": "Write", "tool_input": {"contents": "x"},
        }))

    def test_the_workspace_root_stands_in_for_cwd(self) -> None:
        payload = {"tool_name": "Write", "tool_input": {"file_path": "src/other.py"}, "workspace_roots": [str(self.root)]}
        self.assertEqual(str(self.root), hook_agents.root_hint(payload))

    def test_stop_follows_up_once_in_strict_mode_and_is_silent_otherwise(self) -> None:
        with mock.patch.object(hooks, "check_stop", return_value=("unverified", True)):
            self.assertEqual({"followup_message": "unverified"}, hook_agents.stop(CURSOR, {"loop_count": 0}, self.root))
            self.assertIsNone(hook_agents.stop(CURSOR, {"loop_count": 1}, self.root))
        with mock.patch.object(hooks, "check_stop", return_value=("unverified", False)):
            self.assertIsNone(hook_agents.stop(CURSOR, {"loop_count": 0}, self.root))

    def test_an_internal_error_prints_nothing_cursor_would_read_as_a_refusal(self) -> None:
        out, err = io.StringIO(), io.StringIO()
        with mock.patch.object(sys, "stderr", err):
            self.assertEqual(0, wb_hook.main(["--agent", "cursor", "pre-tool-use"], io.StringIO("[1]"), out))
        self.assertEqual("", out.getvalue())
        self.assertIn("edit allowed", err.getvalue())


class Unknown(Guarded):
    def test_an_unknown_agent_is_reported_and_the_edit_allowed(self) -> None:
        answer = self.hook("--agent", "vim", "pre-tool-use", payload={"tool_name": "Edit", "tool_input": {}})
        self.assertIn("unknown agent 'vim'", answer["systemMessage"])
        self.assertIn("edit allowed", answer["systemMessage"])

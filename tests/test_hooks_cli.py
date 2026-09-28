"""WB-61: wb hooks install proposes, merges, and never drops a key it did not write."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from test_cli import run  # noqa: E402

from workbench.cli import hooks as hooks_cli  # noqa: E402


class Install(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        subprocess.run(["git", "init", "-q", "."], cwd=str(self.root), capture_output=True, check=False)
        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)

    def read(self, relative: str) -> dict:
        return json.loads((self.root / relative).read_text(encoding="utf-8"))

    def test_without_write_nothing_changes(self) -> None:
        for agent, target in (("gemini", ".gemini/settings.json"), ("cursor", ".cursor/hooks.json")):
            with self.subTest(agent=agent):
                code, out, err = run("hooks", "install", agent)
                self.assertEqual(0, code, out + err)
                self.assertIn("nothing written", out)
                self.assertFalse((self.root / target).exists())

    def test_write_adds_workbench_and_keeps_every_other_key(self) -> None:
        settings = self.root / ".gemini" / "settings.json"
        settings.parent.mkdir()
        foreign = {"matcher": "run_shell_command", "hooks": [{"type": "command", "command": "lint.sh"}]}
        settings.write_text(json.dumps({"theme": "dark", "hooks": {"BeforeTool": [foreign]}}), encoding="utf-8")

        code, out, err = run("hooks", "install", "gemini", "--write")
        self.assertEqual(0, code, out + err)
        data = self.read(".gemini/settings.json")
        self.assertEqual("dark", data["theme"])
        self.assertEqual(foreign, data["hooks"]["BeforeTool"][0])
        ours = data["hooks"]["BeforeTool"][1]
        self.assertEqual("write_file|replace", ours["matcher"])
        self.assertIn("--agent gemini pre-tool-use", ours["hooks"][0]["command"])
        self.assertIn("--agent gemini stop", data["hooks"]["AfterAgent"][0]["hooks"][0]["command"])

    def test_a_second_run_changes_nothing(self) -> None:
        self.assertEqual(0, run("hooks", "install", "cursor", "--write")[0])
        first = (self.root / ".cursor" / "hooks.json").read_text(encoding="utf-8")
        code, out, _ = run("hooks", "install", "cursor", "--write")
        self.assertEqual(0, code)
        self.assertIn("up to date", out)
        self.assertEqual(first, (self.root / ".cursor" / "hooks.json").read_text(encoding="utf-8"))
        data = json.loads(first)
        self.assertEqual(1, data["version"])
        self.assertEqual(1, len(data["hooks"]["preToolUse"]))
        self.assertEqual("Write", data["hooks"]["preToolUse"][0]["matcher"])

    def test_an_older_workbench_entry_is_replaced_not_duplicated(self) -> None:
        old = {"command": 'python "/moved/lib/wb_hook.py" --agent cursor stop'}
        merged = hooks_cli.merge({"version": 1, "hooks": {"stop": [old, {"command": "notify.sh"}]}}, "cursor")
        commands = [entry["command"] for entry in merged["hooks"]["stop"]]
        self.assertEqual("notify.sh", commands[0])
        self.assertEqual(2, len(commands))
        self.assertNotIn("/moved/", commands[1])

    def test_a_config_it_cannot_read_is_refused_not_overwritten(self) -> None:
        target = self.root / ".cursor" / "hooks.json"
        target.parent.mkdir()
        target.write_text("{not json", encoding="utf-8")
        code, _, err = run("hooks", "install", "cursor", "--write")
        self.assertNotEqual(0, code)
        self.assertIn("not valid JSON", err)
        self.assertEqual("{not json", target.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()

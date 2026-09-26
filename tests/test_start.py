"""WB-54: wb start picks a ticket up in one command, and a second run is a no-op."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from test_cli import CliBase, run  # noqa: E402

from workbench.errors import EXIT_USAGE  # noqa: E402


class Start(CliBase):
    def setUp(self) -> None:
        super().setUp()
        for argv in (["init", "-q", "-b", "main", "."], ["config", "user.email", "t@example.com"],
                     ["config", "user.name", "T"], ["commit", "-q", "--allow-empty", "-m", "base"]):
            self.git(*argv)
        self.use_local()
        environment = mock.patch.dict(os.environ, {"WB_NO_EXECUTE": ""})
        environment.start()
        self.addCleanup(environment.stop)
        code, out, err = run("task", "new", "Refunds print the wrong amount", "--type", "chore",
                             "--desc", "format_money(-150) prints USD -2.50.", "--key", "ABC-1")
        self.assertEqual(0, code, out + err)
        # The backlog is committed, as a real repo commits it: the branch
        # switch needs a clean tree, and a task file is a tracked change.
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "backlog")

    def git(self, *args: str) -> str:
        done = subprocess.run(["git", *args], cwd=str(self.root), capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=30, check=False)
        return done.stdout.strip()

    def test_it_reads_the_ticket_branches_for_it_and_names_the_next_command(self) -> None:
        code, out, err = run("start", "ABC-1")
        self.assertEqual(0, code, out + err)
        self.assertTrue((self.root / ".workflow" / "ABC-1" / "triage.json").is_file())
        self.assertIn("format_money(-150)", out, "the ticket text is shown, so nothing has to open triage.json")
        self.assertEqual("ABC-1-refunds-print-the-wrong-amount", self.git("branch", "--show-current"))
        self.assertIn("no origin remote", out)
        self.assertIn("route   ", out)
        self.assertIn("next:", out)

    def test_a_second_run_changes_nothing_and_says_where_it_stands(self) -> None:
        run("start", "ABC-1")
        head = self.git("rev-parse", "HEAD")
        with mock.patch("workbench.providers.local.LocalProvider.get_task") as fetch:
            code, out, err = run("start", "ABC-1")
        self.assertEqual(0, code, out + err)
        fetch.assert_not_called()
        self.assertIn("already read", out)
        self.assertIn("already on it", out)
        self.assertIn("next:", out)
        self.assertEqual(head, self.git("rev-parse", "HEAD"))

    def test_an_existing_branch_is_switched_to_not_created_again(self) -> None:
        run("start", "ABC-1")
        self.git("switch", "-q", "main")
        code, out, err = run("start", "ABC-1")
        self.assertEqual(0, code, out + err)
        self.assertIn("switched to it", out)
        self.assertEqual("ABC-1-refunds-print-the-wrong-amount", self.git("branch", "--show-current"))

    def test_uncommitted_work_stops_the_switch_and_names_the_commands(self) -> None:
        (self.root / "tracked.txt").write_text("a\n", encoding="utf-8")
        self.git("add", "tracked.txt")
        self.git("commit", "-q", "-m", "tracked")
        (self.root / "tracked.txt").write_text("changed\n", encoding="utf-8")
        code, out, err = run("start", "ABC-1")
        self.assertNotEqual(0, code)
        self.assertIn("branch was not created", err)
        self.assertIn("git switch -c ABC-1-refunds-print-the-wrong-amount", err)
        self.assertIn("wb start ABC-1", err)
        self.assertEqual("main", self.git("branch", "--show-current"))

    def test_with_git_writes_off_it_prints_the_commands_instead(self) -> None:
        with mock.patch.dict(os.environ, {"WB_NO_EXECUTE": "1"}):
            code, out, err = run("start", "ABC-1")
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("git writes are off", err)
        self.assertIn("git switch -c ABC-1-refunds-print-the-wrong-amount main", err)
        self.assertTrue((self.root / ".workflow" / "ABC-1" / "triage.json").is_file(), "the read is kept")

    def test_an_unknown_ticket_names_the_fix(self) -> None:
        code, out, err = run("start", "ABC-99")
        self.assertNotEqual(0, code)
        self.assertIn("ABC-99", err)
        self.assertIn("wb task", err)

    def test_refresh_reads_the_ticket_again(self) -> None:
        run("start", "ABC-1")
        path = self.root / ".workflow" / "ABC-1" / "triage.json"
        stale = json.loads(path.read_text(encoding="utf-8"))
        stale["title"] = "stale"
        path.write_text(json.dumps(stale), encoding="utf-8")
        _, out, _ = run("start", "ABC-1", "--refresh")
        self.assertIn("Refunds print the wrong amount", out)


if __name__ == "__main__":
    unittest.main()

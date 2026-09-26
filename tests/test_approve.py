"""WB-55: wb approve shows the one decision waiting, and approves exactly what it showed."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from test_amend import _plan, run  # noqa: E402

from workbench import approve, verify  # noqa: E402
from workbench.errors import EXIT_USAGE  # noqa: E402


class Approve(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.git("init", "-q", "-b", "main", ".")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        (self.root / ".gitignore").write_text(".workflow/\nh/\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "util.py").write_text("def total(items):\n    return sum(items)\n", encoding="utf-8")
        (self.root / "src" / "format.py").write_text("def money(x):\n    return str(x)\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")
        self.git("switch", "-q", "-c", "ABC-1-totals")

        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)
        environment = mock.patch.dict(
            os.environ,
            {"WORKBENCH_NO_EVENTS": "1", "WORKBENCH_HOME": str(self.root / "h"), "WB_NO_EXECUTE": ""},
        )
        environment.start()
        self.addCleanup(environment.stop)

        self.dir = self.root / ".workflow" / "ABC-1"
        self.dir.mkdir(parents=True)
        (self.dir / "triage.json").write_text(json.dumps({"key": "ABC-1", "type": "chore"}), encoding="utf-8")
        (self.dir / "sdd.json").write_text(json.dumps(_plan()), encoding="utf-8")
        self.assertEqual(0, run("sdd", "audit", "ABC-1")[0])

    def git(self, *args: str) -> str:
        done = subprocess.run(["git", *args], cwd=str(self.root), capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60, check=True)
        return done.stdout.strip()

    def token(self, out: str) -> str:
        line = next(line for line in out.splitlines() if line.startswith("approve: "))
        return line.split()[-1]

    def test_verify_commands_are_shown_and_approved_without_running(self) -> None:
        code, out, err = run("approve")
        self.assertEqual(0, code, out + err)
        self.assertIn("ABC-1  verify:", out)
        self.assertIn("python -m unittest -q", out)
        self.assertIn("nothing runs until wb impl verify ABC-1", out)
        with mock.patch("workbench.verify.run") as ran:
            code, out, err = run("approve", "ABC-1", self.token(out))
        self.assertEqual(0, code, out + err)
        ran.assert_not_called()
        self.assertEqual([], verify.unapproved(self.root, ["python -m unittest -q"], "ABC-1"))
        self.assertIn("wb impl verify ABC-1", out)

    def test_status_and_next_point_to_approve_while_a_gate_waits(self) -> None:
        _, out, _ = run("status", "ABC-1")
        self.assertIn("await your approval", out, "status shows the waiting gate before the work reaches it")
        (self.root / "src" / "util.py").write_text("def total(items):\n    return round(sum(items))\n",
                                                   encoding="utf-8")
        _, out, _ = run("next", "ABC-1")
        self.assertIn("wb approve ABC-1", out)
        self.assertIn("await your approval", out)
        _, out, _ = run("approve", "ABC-1")
        run("approve", "ABC-1", self.token(out))
        _, out, _ = run("next", "ABC-1")
        self.assertNotIn("wb approve", out)
        self.assertIn("wb impl verify ABC-1", out)

    def test_a_token_for_something_else_approves_nothing(self) -> None:
        _, out, _ = run("approve", "ABC-1")
        token = self.token(out)
        plan = json.loads((self.dir / "sdd.json").read_text(encoding="utf-8"))
        plan["verify"] = ["python -m unittest discover -q"]
        (self.dir / "sdd.json").write_text(json.dumps(plan), encoding="utf-8")
        self.assertEqual(0, run("sdd", "audit", "ABC-1")[0])
        code, _, err = run("approve", "ABC-1", token)
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("changed since that token was printed", err)
        self.assertEqual(["python -m unittest discover -q"],
                         verify.unapproved(self.root, ["python -m unittest discover -q"], "ABC-1"))

    def test_nothing_waiting_says_so_and_exits_zero(self) -> None:
        verify.approve(self.root, ["python -m unittest -q"])
        code, out, _ = run("approve", "ABC-1")
        self.assertEqual(0, code)
        self.assertIn("nothing waiting on you", out)
        self.assertIn("next:", out)

    def test_a_token_with_nothing_waiting_is_refused(self) -> None:
        verify.approve(self.root, ["python -m unittest -q"])
        code, _, err = run("approve", "ABC-1", "0123456789")
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("nothing is waiting", err)

    def test_a_file_outside_the_plan_is_the_decision_first_and_approving_amends(self) -> None:
        (self.root / "src" / "format.py").write_text("def money(x):\n    return f'{x}'\n", encoding="utf-8")
        _, out, _ = run("next", "ABC-1")
        self.assertIn("wb approve ABC-1", out)
        code, out, _ = run("approve", "ABC-1")
        self.assertEqual(0, code)
        self.assertIn("scope:", out)
        self.assertIn("src/format.py", out)
        self.assertIn("revert", out)
        code, out, err = run("approve", "ABC-1", self.token(out))
        self.assertEqual(0, code, out + err)
        plan = json.loads((self.dir / "sdd.json").read_text(encoding="utf-8"))
        amended = [item for item in plan["files"] if item["path"] == "src/format.py"]
        self.assertEqual("approved by a person with wb approve", amended[0]["why"])
        _, out, _ = run("approve", "ABC-1")
        self.assertIn("verify:", out, "with scope settled, the next decision is the verify one")

    def test_a_drafted_pr_on_an_unpushed_branch_is_a_publish_decision(self) -> None:
        verify.approve(self.root, ["python -m unittest -q"])
        (self.dir / "pr.md").write_text("## What\nx\n", encoding="utf-8")
        remote = self.root.parent / (self.root.name + "-origin.git")
        subprocess.run(["git", "init", "-q", "--bare", str(remote)], capture_output=True, check=True,
                       stdin=subprocess.DEVNULL, timeout=60)
        self.addCleanup(lambda: __import__("shutil").rmtree(remote, ignore_errors=True))
        self.git("remote", "add", "origin", str(remote))
        _, out, _ = run("approve", "ABC-1")
        self.assertIn("publish:", out)
        self.assertIn("git push -u origin ABC-1-totals", out)
        code, out, err = run("approve", "ABC-1", self.token(out))
        self.assertEqual(0, code, out + err)
        self.assertIn("pushed ABC-1-totals", out)
        _, out, _ = run("approve", "ABC-1")
        self.assertIn("nothing waiting on you", out)


class Token(unittest.TestCase):
    def test_the_token_binds_what_was_shown(self) -> None:
        one = approve.Decision("ABC-1", approve.VERIFY, "t", ["python -m unittest -q"])
        same = approve.Decision("ABC-1", approve.VERIFY, "other title", ["python -m unittest -q"])
        other = approve.Decision("ABC-1", approve.VERIFY, "t", ["python -m unittest -q", "env A=1"])
        self.assertEqual(one.token, same.token)
        self.assertNotEqual(one.token, other.token)


if __name__ == "__main__":
    unittest.main()

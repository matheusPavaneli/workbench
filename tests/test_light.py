"""WB-53: the light path -- no plan up front, the same floor at wb finish."""

import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from test_cli import CliBase, run  # noqa: E402

from workbench import light  # noqa: E402
from workbench.errors import EXIT_AUDIT, EXIT_USAGE  # noqa: E402

MONEY = 'def format_money(cents):\n    return f"USD {cents // 100}.{cents % 100:02d}"\n'
MONEY_FIXED = (
    "def format_money(cents):\n"
    '    sign = "-" if cents < 0 else ""\n'
    "    cents = abs(cents)\n"
    '    return f"USD {sign}{cents // 100}.{cents % 100:02d}"\n'
)
TEST_OLD = (
    "import unittest\n\nfrom shop.money import format_money\n\n\n"
    "class MoneyTest(unittest.TestCase):\n"
    "    def test_positive(self):\n"
    '        self.assertEqual("USD 1.50", format_money(150))\n'
)
TEST_REFUND = TEST_OLD + (
    "\n    def test_refund(self):\n"
    '        self.assertEqual("USD -1.50", format_money(-150))\n'
)
TEST_WEAK = TEST_OLD + (
    "\n    def test_zero(self):\n"
    '        self.assertEqual("USD 0.00", format_money(0))\n'
)


class LightPath(CliBase):
    KIND = "chore"

    def setUp(self) -> None:
        super().setUp()
        for argv in (["init", "-q", "-b", "main", "."], ["config", "user.email", "t@example.com"],
                     ["config", "user.name", "T"]):
            self.git(*argv)
        self.write("shop/__init__.py", "")
        self.write("shop/money.py", MONEY)
        self.write("tests/test_money.py", TEST_OLD)
        self.write(".gitignore", ".workflow/*\n!.workflow/config.json\n!.workflow/tasks/\n__pycache__/\n")
        self.use_local()
        environment = mock.patch.dict(os.environ, {"WB_NO_EXECUTE": ""})
        environment.start()
        self.addCleanup(environment.stop)
        code, out, err = run("task", "new", "Refunds print the wrong amount", "--type", self.KIND,
                             "--desc", "format_money(-150) should print USD -1.50.", "--key", "ABC-1")
        self.assertEqual(0, code, out + err)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: base")

    def git(self, *args: str) -> str:
        done = subprocess.run(["git", *args], cwd=str(self.root), capture_output=True, text=True,
                              stdin=subprocess.DEVNULL, timeout=60, check=False)
        return done.stdout.strip()

    def write(self, relative: str, content: str) -> None:
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def finish(self, message: str = "fix: print refunds with their real amount") -> tuple[int, str, str]:
        return run("finish", "ABC-1", "-m", message)


class Chore(LightPath):
    def test_start_puts_small_work_on_the_light_path(self) -> None:
        code, out, err = run("start", "ABC-1")
        self.assertEqual(0, code, out + err)
        self.assertIsNotNone(light.marker("ABC-1", self.root))
        self.assertIn("light path", out)
        self.assertIn("wb finish ABC-1", out)
        self.assertNotIn("plan-change", out)

    def test_a_change_with_its_test_is_cleared_and_gets_the_commit_command(self) -> None:
        run("start", "ABC-1")
        self.write("shop/money.py", MONEY_FIXED)
        self.write("tests/test_money.py", TEST_REFUND)
        code, out, err = self.finish()
        self.assertEqual(0, code, out + err)
        self.assertIn("clear", out)
        self.assertIn("1 suite run(s) passed", out)
        self.assertIn("git add -- shop/money.py tests/test_money.py && git commit -F", out)
        self.assertLessEqual(len(out.strip().splitlines()), 3, "a pass speaks in three lines at most")
        commit = (self.root / ".workflow" / "ABC-1" / "commit.txt").read_text(encoding="utf-8")
        self.assertEqual("fix: print refunds with their real amount\n", commit)
        self.assertEqual("pass", light.marker("ABC-1", self.root)["verdict"])
        _, nxt, _ = run("next", "ABC-1")
        self.assertIn("wb pr context ABC-1", nxt)

    def test_commit_stages_exactly_the_checked_files_and_commits(self) -> None:
        run("start", "ABC-1")
        self.write("shop/money.py", MONEY_FIXED)
        self.write("tests/test_money.py", TEST_REFUND)
        # Ignored, so never part of the change: --commit must not reach it.
        self.write("shop/__pycache__/money.cpython-312.pyc", "")
        code, out, err = run("finish", "ABC-1", "-m", "fix: print refunds with their real amount", "--commit")
        self.assertEqual(0, code, out + err)
        self.assertIn("committed", out)
        self.assertEqual("fix: print refunds with their real amount", self.git("log", "-1", "--format=%s"))
        self.assertEqual(["shop/money.py", "tests/test_money.py"],
                         sorted(self.git("show", "--name-only", "--format=", "HEAD").split()))
        self.assertEqual("", self.git("status", "--porcelain", "--", "shop", "tests"))

    def test_a_logic_change_with_no_test_change_is_refused(self) -> None:
        run("start", "ABC-1")
        self.write("shop/money.py", MONEY_FIXED)
        code, _, err = self.finish()
        self.assertEqual(EXIT_AUDIT, code)
        self.assertIn("shop/money.py changed and no test did", err)
        self.assertIsNotNone(light.marker("ABC-1", self.root), "a refusal keeps the path")

    def test_a_failing_suite_is_refused(self) -> None:
        run("start", "ABC-1")
        self.write("tests/test_money.py", TEST_REFUND)
        self.write("shop/money.py", MONEY.replace("USD", "EUR"))
        code, _, err = self.finish()
        self.assertEqual(EXIT_AUDIT, code)
        self.assertIn("exited", err)

    def test_a_message_outside_the_convention_is_refused_before_anything_runs(self) -> None:
        run("start", "ABC-1")
        self.write("shop/money.py", MONEY_FIXED)
        self.write("tests/test_money.py", TEST_REFUND)
        with mock.patch("workbench.verify.run") as suite:
            code, _, err = self.finish("updated stuff")
        self.assertEqual(EXIT_AUDIT, code)
        self.assertIn("commit message", err)
        suite.assert_not_called()

    def test_nothing_changed_yet_says_what_to_do(self) -> None:
        run("start", "ABC-1")
        code, _, err = self.finish()
        self.assertEqual(EXIT_AUDIT, code)
        self.assertIn("nothing has changed", err)

    def test_a_change_past_the_bound_leaves_the_path_for_a_plan(self) -> None:
        run("start", "ABC-1")
        self.write("shop/money.py", MONEY_FIXED + "".join(f"X{n} = {n}\n" for n in range(150)))
        self.write("tests/test_money.py", TEST_REFUND)
        code, _, err = self.finish()
        self.assertEqual(EXIT_AUDIT, code)
        self.assertIn("outgrew the light path", err)
        self.assertIn("plan-change", err)
        self.assertIn("do not revert it", err)
        self.assertIsNone(light.marker("ABC-1", self.root))
        _, nxt, _ = run("next", "ABC-1")
        self.assertIn("plan", nxt)
        self.assertTrue((self.root / "shop" / "money.py").read_text(encoding="utf-8").startswith(MONEY_FIXED))

    def test_a_critical_zone_leaves_the_path(self) -> None:
        run("start", "ABC-1")
        self.write("shop/billing/charge.py", "def charge():\n    return 1\n")
        self.write("tests/test_money.py", TEST_REFUND)
        self.write("shop/money.py", MONEY_FIXED)
        code, _, err = self.finish()
        self.assertEqual(EXIT_AUDIT, code)
        self.assertIn("touches billing", err)

    def test_code_that_moves_after_a_pass_reopens_the_change(self) -> None:
        run("start", "ABC-1")
        self.write("shop/money.py", MONEY_FIXED)
        self.write("tests/test_money.py", TEST_REFUND)
        self.assertEqual(0, self.finish()[0])
        self.write("shop/money.py", MONEY_FIXED + "# later\n")
        _, nxt, _ = run("next", "ABC-1")
        self.assertIn("changed since wb finish", nxt)
        self.assertIn("wb finish ABC-1", nxt)

    def test_finish_on_a_ticket_not_on_the_path_names_the_fix(self) -> None:
        code, _, err = self.finish()
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("wb start ABC-1", err)


class SourceThatNeverExisted(LightPath):
    """The first probe: a flow source recorded as main on a repo that is on master."""

    def test_finish_measures_against_the_commit_the_path_started_from(self) -> None:
        self.git("branch", "-m", "main", "master")
        config = self.root / ".workflow" / "config.json"
        data = json.loads(config.read_text(encoding="utf-8"))
        data["flow"] = {"source": "main", "validation": [], "protected": ["main"], "strategy": "cherry-pick"}
        config.write_text(json.dumps(data), encoding="utf-8")
        self.git("commit", "-qam", "chore: flow")
        run("start", "ABC-1")
        self.write("shop/money.py", MONEY_FIXED)
        self.write("tests/test_money.py", TEST_REFUND)
        code, out, err = self.finish()
        self.assertEqual(0, code, out + err)
        self.assertIn("clear", out)


class Bug(LightPath):
    KIND = "bug"

    def test_a_bug_s_test_must_fail_without_the_fix(self) -> None:
        run("start", "ABC-1")
        self.assertIsNotNone(light.marker("ABC-1", self.root), "a small bug takes the light path too")
        self.write("shop/money.py", MONEY_FIXED)
        self.write("tests/test_money.py", TEST_WEAK)
        code, _, err = self.finish()
        self.assertEqual(EXIT_AUDIT, code)
        self.assertIn("passes without the fix", err)

    def test_a_test_that_cannot_run_alone_is_not_called_weak(self) -> None:
        """WB-59: the file failed to import on its own, and finish said it passed without the fix."""
        from workbench import verify

        run("start", "ABC-1")
        self.write("shop/money.py", MONEY_FIXED)
        self.write("tests/test_money.py", TEST_REFUND)
        broken = verify.Result(command="python -m unittest tests/test_money.py", exit_code=1, duration_ms=1,
                               output="ModuleNotFoundError: No module named 'shop'", truncated=False)
        outcome = verify.Regression("tests/test_money.py", broken.command, broken, broken)
        with mock.patch("workbench.verify.run_regression", return_value=[outcome]):
            code, _, err = self.finish()
        self.assertEqual(EXIT_AUDIT, code)
        self.assertIn("fails with the fix in place: ModuleNotFoundError", err)
        self.assertNotIn("passes without the fix", err)

    def test_a_bug_s_regression_test_is_proven(self) -> None:
        run("start", "ABC-1")
        self.write("shop/money.py", MONEY_FIXED)
        self.write("tests/test_money.py", TEST_REFUND)
        code, out, err = self.finish()
        self.assertEqual(0, code, out + err)
        self.assertIn("1 regression test(s) proven", out)


class ZoneNamed(LightPath):
    """WB-58: a ticket naming a function in a critical zone plans first, instead of finishing twice."""

    KIND = "bug"

    def test_a_named_function_in_a_critical_zone_starts_on_the_standard_route(self) -> None:
        self.write("shop/billing/__init__.py", "")
        self.write("shop/billing/charge.py", "def charge_card(total):" + chr(10) + "    return total" + chr(10))
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "chore: billing")
        code, out, err = run("task", "new", "Cards are charged twice", "--type", "bug",
                             "--desc", "charge_card runs twice on a retry.", "--key", "ABC-2")
        self.assertEqual(0, code, out + err)
        code, out, err = run("start", "ABC-2")
        self.assertEqual(0, code, out + err)
        self.assertIsNone(light.marker("ABC-2", self.root))
        self.assertIn("names charge_card, defined in shop/billing/charge.py, which is in the billing zone", out)
        self.assertIn("standard", out)

    def test_a_named_function_outside_every_zone_keeps_the_light_path(self) -> None:
        run("start", "ABC-1")
        self.assertIsNotNone(light.marker("ABC-1", self.root), "format_money lives in shop/money.py, no zone")

    def test_names_are_the_identifiers_a_ticket_spells_not_its_plain_words(self) -> None:
        text = "display_name keeps stray whitespace; `in_stock` and ChargeError, the total and the name."
        self.assertEqual(["display_name", "in_stock", "ChargeError"], light.names(text))


class Feature(LightPath):
    KIND = "feature"

    def test_feature_work_keeps_the_standard_route(self) -> None:
        _, out, _ = run("start", "ABC-1")
        self.assertIsNone(light.marker("ABC-1", self.root))
        self.assertIn("standard", out)


class SwitchedOff(LightPath):
    def test_the_config_key_restores_the_standard_route(self) -> None:
        config = self.root / ".workflow" / "config.json"
        data = json.loads(config.read_text(encoding="utf-8"))
        data["light_path"] = False
        config.write_text(json.dumps(data), encoding="utf-8")
        self.git("commit", "-qam", "chore: light path off")
        _, out, _ = run("start", "ABC-1")
        self.assertIsNone(light.marker("ABC-1", self.root))
        self.assertIn("standard", out)

    def test_switching_it_off_mid_way_moves_the_ticket_to_the_standard_route(self) -> None:
        run("start", "ABC-1")
        config = self.root / ".workflow" / "config.json"
        data = json.loads(config.read_text(encoding="utf-8"))
        data["light_path"] = False
        config.write_text(json.dumps(data), encoding="utf-8")
        code, _, err = self.finish()
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("standard route", err)
        self.assertIsNone(light.marker("ABC-1", self.root))


class Suite(unittest.TestCase):
    def test_the_suite_command_comes_from_the_runner(self) -> None:
        self.assertEqual("python -m unittest discover -s tests -q",
                         light.suite_command({"test_runner": "unittest", "test_dir": "tests"}))
        self.assertEqual("python -m pytest -q", light.suite_command({"test_runner": "pytest"}))
        self.assertIsNone(light.suite_command({"test_runner": "tox"}))
        self.assertIsNone(light.suite_command({}))


if __name__ == "__main__":
    unittest.main()

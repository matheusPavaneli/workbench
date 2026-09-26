"""The benchmark's scoring and fixture: the parts that decide a number.

The harness itself spends money and never runs here (docs/bench.md). What runs
is everything that could make a result lie without anyone noticing: a metric
read in the wrong direction, a missing count scored as zero, an arm given the
wrong flags, or a ticket whose hidden tests would pass on an untouched fixture.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

BENCH = Path(__file__).resolve().parent.parent / "bench"
sys.path.insert(0, str(BENCH))

import run as bench_run  # noqa: E402
import score  # noqa: E402

FIXTURE = BENCH / "fixture"
HIDDEN = BENCH / "hidden"


def _ticket(**overrides) -> dict:
    ticket = {
        "key": "BN-9",
        "kind": "bug",
        "title": "a title",
        "desc": "a description",
        "expected_files": ["shop/a.py", "tests/test_a.py"],
        "hidden": "BN-9",
    }
    ticket.update(overrides)
    return ticket


def _record(ticket: str, arm: str, **metrics) -> dict:
    base = {"hidden_pass": 1, "out_of_scope": 0, "rework": 0, "tokens": 1000, "cost_usd": 1.0, "wall_s": 60.0}
    base.update(metrics)
    return {"ticket": ticket, "arm": arm, "run": 1, "metrics": base, "error": None}


class OutOfScopeTest(unittest.TestCase):
    def test_a_file_the_ticket_did_not_need_counts(self) -> None:
        self.assertEqual(["shop/b.py"], score.out_of_scope(["shop/a.py", "shop/b.py"], ["shop/a.py"]))

    def test_workflow_artifacts_never_count(self) -> None:
        changed = [".workflow/BN-1/sdd.json", ".workflow/tasks/BN-1.json"]
        self.assertEqual([], score.out_of_scope(changed, ["shop/a.py"]))

    def test_a_listed_file_never_counts(self) -> None:
        self.assertEqual([], score.out_of_scope(["shop/a.py", "tests/test_a.py"], ["shop/a.py", "tests/test_a.py"]))


class ReworkTest(unittest.TestCase):
    def test_rework_is_commits_after_the_first(self) -> None:
        self.assertEqual(0, score.rework(0))
        self.assertEqual(0, score.rework(1))
        self.assertEqual(2, score.rework(3))


class TestsMissingTest(unittest.TestCase):
    def test_logic_changed_with_no_test_changed_is_missing(self) -> None:
        self.assertEqual(1, score.tests_missing(["shop/x.py"]))

    def test_a_changed_test_beside_the_logic_is_not_missing(self) -> None:
        self.assertEqual(0, score.tests_missing(["shop/x.py", "tests/test_x.py"]))

    def test_only_tests_or_docs_changed_is_not_missing(self) -> None:
        self.assertEqual(0, score.tests_missing(["tests/test_x.py"]))
        self.assertEqual(0, score.tests_missing(["README.md"]))
        self.assertEqual(0, score.tests_missing([]))

    def test_workflow_files_are_never_logic(self) -> None:
        self.assertEqual(0, score.tests_missing([".workflow/BN-9/helper.py"]))


SHOP = {
    "shop/__init__.py": "",
    "shop/stock.py": "import json\n\ndef available(stock, sku, quantity=1):\n    return True\n",
    "shop/billing/__init__.py": "",
    "shop/billing/charge.py": "from ..stock import available\nfrom . import charge\n",
}


class InvalidRefsTest(unittest.TestCase):
    def _refs(self, changed: dict[str, str]) -> list[str] | None:
        return score.invalid_refs(list(changed), {**SHOP, **changed})

    def test_a_name_the_module_does_not_define_is_invalid(self) -> None:
        refs = self._refs({"tests/test_stock.py": "from shop.stock import in_stock\n"})
        self.assertEqual(["tests/test_stock.py: from shop.stock import in_stock"], refs)

    def test_a_repo_module_that_does_not_exist_is_invalid(self) -> None:
        refs = self._refs({"tests/test_x.py": "import shop.inventory\nfrom shop.money import fmt\n"})
        self.assertEqual(
            ["tests/test_x.py: from shop.money import fmt", "tests/test_x.py: import shop.inventory"], refs
        )

    def test_stdlib_and_outside_imports_are_not_checked(self) -> None:
        self.assertEqual([], self._refs({"tests/test_x.py": "import json\nfrom requests import nope\n"}))

    def test_valid_imports_submodules_and_relative_imports_resolve(self) -> None:
        refs = self._refs({
            "shop/billing/charge.py": "from ..stock import available, json\nfrom . import charge\nfrom shop import stock\n",
            "tests/test_y.py": "import shop.billing.charge\n",
        })
        self.assertEqual([], refs)

    def test_a_relative_import_of_a_missing_name_is_invalid(self) -> None:
        refs = self._refs({"shop/billing/charge.py": "from ..stock import in_stock\n"})
        self.assertEqual(["shop/billing/charge.py: from shop.stock import in_stock"], refs)

    def test_a_changed_file_that_does_not_parse_is_absent_not_zero(self) -> None:
        self.assertIsNone(self._refs({"shop/stock.py": "def available(:\n"}))

    def test_files_the_run_did_not_change_are_not_read(self) -> None:
        sources = {**SHOP, "shop/old.py": "from shop.stock import gone\n"}
        self.assertEqual([], score.invalid_refs(["shop/stock.py"], sources))


DIFF = """diff --git a/shop/stock.py b/shop/stock.py
index 1111111..2222222 100644
--- a/shop/stock.py
+++ b/shop/stock.py
@@ -9 +9 @@ def available(stock: dict, sku: str, quantity: int = 1) -> bool:
-    return stock.get(sku, 0) > quantity
+    return stock.get(sku, 0) >= quantity
@@ -14,2 +13,0 @@ def restock(stock: dict, sku: str, units: int) -> None:
--- a removed line that looks like a header
-    stock[sku] = stock.get(sku, 0) + units
diff --git a/tests/test_stock.py b/tests/test_stock.py
new file mode 100644
index 0000000..3333333
--- /dev/null
+++ b/tests/test_stock.py
@@ -0,0 +1,3 @@
+import unittest
+
+x = 1
diff --git a/shop/gone.py b/shop/gone.py
deleted file mode 100644
--- a/shop/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-a = 1
-b = 2
"""

STOCK = (
    '"""Stock."""\n'
    "\n"
    "from __future__ import annotations\n"
    "\n"
    "\n"
    "def available(stock: dict, sku: str, quantity: int = 1) -> bool:\n"
    "    if quantity < 1:\n"
    '        raise ValueError("quantity must be at least 1")\n'
    "    return stock.get(sku, 0) >= quantity\n"
    "\n"
    "\n"
    "def restock(stock: dict, sku: str, units: int) -> None:\n"
    "    stock[sku] = units\n"
)


class DiffLinesTest(unittest.TestCase):
    def test_hunks_map_to_new_file_lines(self) -> None:
        lines = score.diff_lines(DIFF)
        self.assertEqual([9, 13], lines["shop/stock.py"])

    def test_a_new_file_is_every_one_of_its_lines(self) -> None:
        self.assertEqual([1, 2, 3], score.diff_lines(DIFF)["tests/test_stock.py"])

    def test_a_deleted_file_is_kept_under_its_old_path(self) -> None:
        self.assertEqual([0], score.diff_lines(DIFF)["shop/gone.py"])

    def test_no_diff_is_no_lines(self) -> None:
        self.assertEqual({}, score.diff_lines(""))


class UnexpectedChangesTest(unittest.TestCase):
    def test_spans_cover_methods_and_decorators(self) -> None:
        spans = score.function_spans("class A:\n    @staticmethod\n    def f():\n        return 1\n")
        self.assertEqual((1, 4), spans["A"])
        self.assertEqual((2, 4), spans["A.f"])

    def test_lines_inside_the_needed_function_do_not_count(self) -> None:
        count = score.unexpected_changes({"shop/stock.py": [9]}, {"shop/stock.py": STOCK}, ["shop/stock.py::available"])
        self.assertEqual(0, count)

    def test_lines_outside_the_needed_function_count(self) -> None:
        count = score.unexpected_changes({"shop/stock.py": [9, 13]}, {"shop/stock.py": STOCK}, ["shop/stock.py::available"])
        self.assertEqual(1, count)

    def test_every_line_of_a_logic_file_the_ticket_did_not_need_counts(self) -> None:
        count = score.unexpected_changes(
            {"shop/stock.py": [9], "shop/regions.py": [3, 4, 5]}, {"shop/stock.py": STOCK}, ["shop/stock.py::available"]
        )
        self.assertEqual(3, count)

    def test_tests_workflow_and_non_python_files_never_count(self) -> None:
        lines = {"tests/test_stock.py": [1, 2], ".workflow/BN-9/x.py": [1], "README.md": [4]}
        self.assertEqual(0, score.unexpected_changes(lines, {"shop/stock.py": STOCK}, ["shop/stock.py::available"]))

    def test_a_ticket_without_expected_functions_is_absent_not_zero(self) -> None:
        self.assertIsNone(score.unexpected_changes({"shop/stock.py": [9]}, {"shop/stock.py": STOCK}, None))
        self.assertIsNone(score.unexpected_changes({"shop/stock.py": [9]}, {"shop/stock.py": STOCK}, []))

    def test_a_needed_file_that_does_not_parse_is_absent(self) -> None:
        self.assertIsNone(score.unexpected_changes({"shop/stock.py": [1]}, {"shop/stock.py": "def (:\n"}, ["shop/stock.py::available"]))


class ErrorMetricsInTheRecordTest(unittest.TestCase):
    def _record(self, changed: tuple[str, ...] = ("shop/stock.py",), **kwargs) -> dict:
        ticket = _ticket(expected_files=["shop/stock.py", "tests/test_stock.py"], expected_functions=["shop/stock.py::available"])
        return score.record(
            ticket=ticket, arm="plain", run=1, session={}, changed=list(changed), commits=1,
            hidden_passed=True, wall_s=1.0, **kwargs,
        )

    def test_without_diff_and_sources_the_error_metrics_are_absent_not_zero(self) -> None:
        metrics = self._record()["metrics"]
        self.assertIsNone(metrics["invalid_refs"])
        self.assertIsNone(metrics["unexpected_changes"])
        self.assertEqual(1, metrics["tests_missing"], "tests_missing needs only the changed paths")

    def test_with_diff_and_sources_every_error_metric_is_scored(self) -> None:
        sources = {"shop/__init__.py": "", "shop/stock.py": STOCK, "tests/test_stock.py": "from shop.stock import in_stock\n"}
        rec = self._record(changed=("shop/stock.py", "tests/test_stock.py", "shop/gone.py"), diff=DIFF, sources=sources)
        self.assertEqual(0, rec["metrics"]["tests_missing"])
        self.assertEqual(1, rec["metrics"]["invalid_refs"])
        self.assertEqual(["tests/test_stock.py: from shop.stock import in_stock"], rec["invalid_ref_list"])
        self.assertEqual(2, rec["metrics"]["unexpected_changes"], "line 13 of stock.py and the deleted shop/gone.py")


class TokensTest(unittest.TestCase):
    def test_every_kind_of_token_is_summed(self) -> None:
        usage = {
            "input_tokens": 10,
            "output_tokens": 20,
            "cache_read_input_tokens": 300,
            "cache_creation_input_tokens": 4000,
        }
        self.assertEqual(4330, score.tokens(usage))

    def test_a_missing_usage_block_is_no_count_not_zero(self) -> None:
        self.assertIsNone(score.tokens(None))
        self.assertIsNone(score.tokens({}))
        self.assertIsNone(score.tokens("oops"))

    def test_a_missing_cost_is_absent_in_the_record(self) -> None:
        rec = score.record(
            ticket=_ticket(), arm="plain", run=1, session={}, changed=[], commits=0, hidden_passed=False, wall_s=1.0
        )
        self.assertIsNone(rec["metrics"]["tokens"])
        self.assertIsNone(rec["metrics"]["cost_usd"])

    def test_the_record_keeps_turns_and_workflow_artifacts(self) -> None:
        rec = score.record(
            ticket=_ticket(), arm="workbench", run=1, session={"num_turns": 7}, changed=[], commits=1,
            hidden_passed=True, wall_s=1.0, artifacts=[".workflow/BN-9/sdd.json", ".workflow/BN-9/triage.json"],
        )
        self.assertEqual(7, rec["num_turns"])
        self.assertEqual([".workflow/BN-9/sdd.json", ".workflow/BN-9/triage.json"], rec["workflow_artifacts"])

    def test_an_unknown_arm_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            score.record(
                ticket=_ticket(), arm="other", run=1, session={}, changed=[], commits=0, hidden_passed=True, wall_s=1.0
            )


class SummaryTest(unittest.TestCase):
    def test_median_min_and_max_per_ticket_metric_and_arm(self) -> None:
        records = [_record("BN-1", "plain", tokens=t) for t in (300, 100, 200)]
        stats = score.summarize(records)["BN-1"]["tokens"]["plain"]
        self.assertEqual({"median": 200, "min": 100, "max": 300, "n": 3}, stats)

    def test_an_even_count_takes_the_mean_of_the_middle_two(self) -> None:
        self.assertEqual(2.5, score.median([4, 1, 2, 3]))

    def test_absent_values_are_left_out_not_counted_as_zero(self) -> None:
        records = [_record("BN-1", "plain", tokens=None), _record("BN-1", "plain", tokens=500)]
        self.assertEqual({"median": 500, "min": 500, "max": 500, "n": 1}, score.summarize(records)["BN-1"]["tokens"]["plain"])


class ReportTest(unittest.TestCase):
    def _report(self, records: list[dict], skipped: int = 0) -> str:
        return score.report(records, commit="abc1234", model="claude-sonnet-5", date="2026-09-26", skipped=skipped)

    def test_a_loss_is_read_by_the_metric_s_direction(self) -> None:
        records = [
            _record("BN-1", "plain", tokens=1000, hidden_pass=0),
            _record("BN-1", "workbench", tokens=3000, hidden_pass=1),
        ]
        lost = score.losses(score.summarize(records))
        self.assertEqual([("BN-1", "tokens", 1000, 3000)], lost)

    def test_fewer_passes_is_a_loss(self) -> None:
        records = [_record("BN-2", "plain", hidden_pass=1), _record("BN-2", "workbench", hidden_pass=0)]
        self.assertEqual([("BN-2", "hidden_pass", 1, 0)], score.losses(score.summarize(records)))

    def test_the_report_names_every_loss(self) -> None:
        records = [
            _record("BN-1", "plain", wall_s=60.0),
            _record("BN-1", "workbench", wall_s=240.0),
            _record("BN-3", "plain", cost_usd=0.5),
            _record("BN-3", "workbench", cost_usd=1.5),
        ]
        text = self._report(records)
        section = text.split("## Where workbench loses", 1)[1]
        self.assertIn("BN-1 wall_s", section)
        self.assertIn("BN-3 cost_usd", section)

    def test_the_report_says_so_when_workbench_loses_nowhere(self) -> None:
        records = [_record("BN-1", "plain"), _record("BN-1", "workbench")]
        section = self._report(records).split("## Where workbench loses", 1)[1]
        self.assertIn("Nowhere", section)

    def test_a_subscription_run_says_its_cost_is_an_estimate(self) -> None:
        text = score.report(
            [_record("BN-1", "plain")], commit="c", model="m", date="d", skipped=0, auth="subscription"
        )
        self.assertIn("auth: subscription", text)
        self.assertIn("not billed", text)

    def test_the_report_says_how_many_workbench_runs_used_the_flow(self) -> None:
        used = dict(_record("BN-1", "workbench"), workflow_artifacts=[".workflow/BN-1/sdd.json"])
        unused = dict(_record("BN-1", "workbench"), workflow_artifacts=[])
        self.assertIn("left workflow artifacts: 1 of 2", self._report([used, unused, _record("BN-1", "plain")]))

    def test_the_report_carries_commit_model_date_and_skipped_runs(self) -> None:
        text = self._report([_record("BN-1", "plain")], skipped=5)
        self.assertIn("abc1234", text)
        self.assertIn("claude-sonnet-5", text)
        self.assertIn("2026-09-26", text)
        self.assertIn("skipped by the spend cap: 5", text)

    def test_the_report_lists_caught_errors_before_the_losses(self) -> None:
        records = [
            _record("BN-7", "plain", tests_missing=1),
            _record("BN-7", "workbench", tests_missing=0),
        ]
        text = self._report(records)
        caught = text.split("## Caught errors", 1)[1].split("## Where workbench loses", 1)[0]
        self.assertIn("- BN-7 tests_missing: plain 1 of 1 runs, workbench 0 of 1", caught)

    def test_the_report_says_so_when_no_error_separates_the_arms(self) -> None:
        records = [_record("BN-1", "plain"), _record("BN-1", "workbench")]
        caught = self._report(records).split("## Caught errors", 1)[1].split("## Where workbench loses", 1)[0]
        self.assertIn("None:", caught)


class CaughtTest(unittest.TestCase):
    def test_an_error_one_arm_made_more_often_is_listed(self) -> None:
        records = [_record("BN-7", "plain", tests_missing=v) for v in (1, 1, 0)]
        records += [_record("BN-7", "workbench", tests_missing=0) for _ in range(3)]
        self.assertEqual([("BN-7", "tests_missing", 2, 3, 0, 3)], score.caught(records))

    def test_the_same_share_in_both_arms_is_not_listed(self) -> None:
        records = [_record("BN-5", "plain", out_of_scope=v) for v in (1, 0)]
        records += [_record("BN-5", "workbench", out_of_scope=v) for v in (0, 0, 1, 1)]
        self.assertEqual([], score.caught(records))

    def test_a_hidden_test_failure_is_an_error(self) -> None:
        records = [_record("BN-1", "plain", hidden_pass=0), _record("BN-1", "workbench", hidden_pass=1)]
        self.assertEqual([("BN-1", "hidden_pass", 1, 1, 0, 1)], score.caught(records))

    def test_absent_values_are_left_out_of_n(self) -> None:
        records = [
            _record("BN-6", "plain", invalid_refs=1),
            _record("BN-6", "plain", invalid_refs=None),
            _record("BN-6", "workbench", invalid_refs=0),
        ]
        self.assertEqual([("BN-6", "invalid_refs", 1, 1, 0, 1)], score.caught(records))

    def test_a_ticket_one_arm_never_ran_is_not_listed(self) -> None:
        self.assertEqual([], score.caught([_record("BN-6", "plain", invalid_refs=2)]))

    def test_records_from_before_the_error_metrics_still_read(self) -> None:
        old = {"ticket": "BN-1", "arm": "plain", "run": 1, "metrics": {"hidden_pass": 1, "out_of_scope": 0}}
        self.assertEqual([], score.caught([old, dict(old, arm="workbench")]))


class CommandTest(unittest.TestCase):
    def test_the_plain_arm_loads_no_plugin(self) -> None:
        argv = bench_run.command("claude", "plain", "do it", model="claude-sonnet-5", plugin_dir=None)
        self.assertNotIn("--plugin-dir", argv)
        self.assertEqual("claude-sonnet-5", argv[argv.index("--model") + 1])
        self.assertEqual("json", argv[argv.index("--output-format") + 1])

    def test_the_workbench_arm_loads_the_plugin_copy(self) -> None:
        argv = bench_run.command("claude", "workbench", "do it", model="claude-sonnet-5", plugin_dir=Path("/tmp/p"))
        self.assertEqual(str(Path("/tmp/p")), argv[argv.index("--plugin-dir") + 1])
        self.assertEqual("claude-sonnet-5", argv[argv.index("--model") + 1])
        self.assertEqual("json", argv[argv.index("--output-format") + 1])

    def test_the_workbench_arm_without_a_plugin_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            bench_run.command("claude", "workbench", "do it", model="m", plugin_dir=None)

    def test_both_arms_get_the_same_ticket_text(self) -> None:
        ticket = _ticket()
        plain, bench = bench_run.prompt(ticket, "plain"), bench_run.prompt(ticket, "workbench")
        self.assertTrue(bench.endswith(plain))
        self.assertIn("Pick up ticket BN-9", bench)
        self.assertIn("workbench flow", bench)
        self.assertNotIn("BN-9", plain)


class GateTest(unittest.TestCase):
    def test_nothing_runs_without_wb_bench(self) -> None:
        missing = bench_run.gate({"ANTHROPIC_API_KEY": "k"}, "claude")
        self.assertEqual(1, len(missing))
        self.assertIn("WB_BENCH=1", missing[0])

    def test_nothing_runs_without_a_credential(self) -> None:
        missing = bench_run.gate({"WB_BENCH": "1"}, "claude")
        self.assertEqual(1, len(missing))
        self.assertIn("CLAUDE_CODE_OAUTH_TOKEN", missing[0])
        self.assertIn("ANTHROPIC_API_KEY", missing[0])

    def test_the_gate_opens_with_an_api_key(self) -> None:
        self.assertEqual([], bench_run.gate({"WB_BENCH": "1", "ANTHROPIC_API_KEY": "k"}, "claude"))

    def test_the_gate_opens_with_a_subscription_token(self) -> None:
        self.assertEqual([], bench_run.gate({"WB_BENCH": "1", "CLAUDE_CODE_OAUTH_TOKEN": "t"}, "claude"))

    def test_an_api_key_wins_over_a_subscription_token(self) -> None:
        self.assertEqual("api-key", bench_run.auth({"ANTHROPIC_API_KEY": "k", "CLAUDE_CODE_OAUTH_TOKEN": "t"}))
        self.assertEqual("subscription", bench_run.auth({"CLAUDE_CODE_OAUTH_TOKEN": "t"}))


class BudgetTest(unittest.TestCase):
    def test_runs_after_the_cap_is_reached_are_skipped(self) -> None:
        budget = bench_run.Budget(50.0)
        planned = bench_run.plan_runs([_ticket(key="BN-1"), _ticket(key="BN-2")], runs=2)
        ran = skipped = 0
        for _ in planned:
            if not budget.allows():
                skipped += 1
                continue
            budget.add(20.0)
            ran += 1
        self.assertEqual((3, 5), (ran, skipped))

    def test_runs_interleave_arms_so_a_cap_does_not_starve_one(self) -> None:
        planned = bench_run.plan_runs([_ticket(key="BN-1")], runs=2)
        self.assertEqual(
            [("BN-1", "plain", 1), ("BN-1", "workbench", 1), ("BN-1", "plain", 2), ("BN-1", "workbench", 2)],
            [(t["key"], arm, n) for t, arm, n in planned],
        )

    def test_a_session_without_a_cost_adds_nothing(self) -> None:
        budget = bench_run.Budget(1.0)
        budget.add(None)
        self.assertTrue(budget.allows())


class PluginCopyTest(unittest.TestCase):
    def test_the_copy_loads_like_the_plugin_and_hides_the_benchmark(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            dest = bench_run.copy_plugin(Path(tmp) / "plugin")
            self.assertTrue((dest / "lib" / "wb.py").is_file())
            self.assertTrue((dest / "skills").is_dir())
            self.assertTrue((dest / ".claude-plugin" / "plugin.json").is_file())
            self.assertEqual([], [p for p in dest.rglob("*") if "bench" in p.relative_to(dest).parts])
            self.assertFalse(any((dest / "lib").rglob("__pycache__")))


class PrepareTest(unittest.TestCase):
    """The setup a run does before any paid session, on a real git repo."""

    def _prepare(self, arm: str) -> tuple[Path, str]:
        context = bench_run.workdir("wb-bench-test-")
        work = context.__enter__()
        self.addCleanup(context.__exit__, None, None, None)
        self.env = bench_run.session_env(work)
        plugin = bench_run.copy_plugin(work / "plugin") if arm == "workbench" else None
        ticket = json.loads((BENCH / "tickets.json").read_text(encoding="utf-8"))["tickets"][2]
        base = bench_run.prepare(work / "repo", ticket, arm, self.env, plugin)
        return work / "repo", base

    def _git(self, repo: Path, *args: str) -> str:
        return bench_run._git(repo, *args, env=self.env)

    def test_the_workbench_arm_starts_with_its_ticket_and_config_in_the_base_commit(self) -> None:
        repo, base = self._prepare("workbench")
        committed = self._git(repo, "ls-tree", "-r", "--name-only", base).splitlines()
        self.assertIn(".workflow/config.json", committed)
        self.assertIn(".workflow/tasks/BN-3.json", committed)
        self.assertEqual([], bench_run._changed(repo, base, self.env), "setup left changes that would be scored")

    def test_the_plain_arm_has_no_workbench_setup(self) -> None:
        repo, base = self._prepare("plain")
        self.assertFalse((repo / ".workflow").exists())
        self.assertEqual([], bench_run._changed(repo, base, self.env))

    def test_bytecode_from_running_the_tests_is_not_a_change(self) -> None:
        repo, base = self._prepare("plain")
        (repo / "shop" / "__pycache__").mkdir()
        (repo / "shop" / "__pycache__" / "format.cpython-312.pyc").write_bytes(b"")
        self.assertEqual([], bench_run._changed(repo, base, self.env))

    def test_the_diff_carries_edited_and_new_files_and_no_workflow(self) -> None:
        repo, base = self._prepare("workbench")
        loyalty = repo / "shop" / "loyalty.py"
        loyalty.write_text(loyalty.read_text(encoding="utf-8").replace("// 1000", "// 100"), encoding="utf-8")
        (repo / "tests" / "test_loyalty.py").write_text("import unittest\n\nx = 1\n", encoding="utf-8")
        (repo / ".workflow" / "BN-3").mkdir(parents=True, exist_ok=True)
        (repo / ".workflow" / "BN-3" / "helper.py").write_text("y = 2\n", encoding="utf-8")

        changed = bench_run._changed(repo, base, self.env)
        lines = score.diff_lines(bench_run._diff(repo, base, self.env))
        self.assertEqual([10], lines["shop/loyalty.py"])
        self.assertEqual([1, 2, 3], lines["tests/test_loyalty.py"])
        self.assertFalse(any(path.startswith(".workflow/") for path in lines))
        self.assertEqual(changed, bench_run._changed(repo, base, self.env), "the diff must not change what counts as changed")

        sources = bench_run._sources(repo, self.env)
        self.assertIn("tests/test_loyalty.py", sources)
        self.assertIn("// 100", sources["shop/loyalty.py"])
        self.assertFalse(any(path.startswith(".workflow/") for path in sources))

    def test_the_owner_s_homes_never_reach_a_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp)
            env = bench_run.session_env(work, {"WORKBENCH_HOME": "/home/me/.workbench", "PATH": "p"})
            self.assertEqual(str(work / "home"), env["WORKBENCH_HOME"])
            self.assertEqual(str(work / "config"), env["CLAUDE_CONFIG_DIR"])
            self.assertEqual("", Path(env["GIT_CONFIG_GLOBAL"]).read_text(encoding="utf-8"))
            self.assertEqual("1", env["GIT_CONFIG_NOSYSTEM"])
            self.assertEqual("p", env["PATH"])

    def test_the_owner_s_git_config_does_not_reach_the_repo(self) -> None:
        repo, _ = self._prepare("plain")
        self.assertEqual("", self._git(repo, "config", "--global", "--list").strip())


class StdinTest(unittest.TestCase):
    def test_no_process_a_run_starts_can_wait_on_the_terminal(self) -> None:
        real = bench_run.subprocess.run
        calls = []

        def spy(argv, *args, **kwargs):
            calls.append((argv, kwargs.get("stdin")))
            return real(argv, *args, **kwargs)

        with bench_run.workdir("wb-bench-test-") as work, mock.patch.object(bench_run.subprocess, "run", spy):
            ticket = json.loads((BENCH / "tickets.json").read_text(encoding="utf-8"))["tickets"][2]
            env = bench_run.session_env(work)
            base = bench_run.prepare(work / "repo", ticket, "workbench", env, bench_run.copy_plugin(work / "plugin"))
            bench_run._changed(work / "repo", base, env)
        self.assertTrue(calls)
        self.assertEqual([], [argv for argv, stdin in calls if stdin is not bench_run.subprocess.DEVNULL])


class WorkdirTest(unittest.TestCase):
    def test_a_dir_with_read_only_files_is_removed(self) -> None:
        with bench_run.workdir("wb-bench-test-") as work:
            locked = work / "objects" / "ab"
            locked.parent.mkdir()
            locked.write_text("x", encoding="utf-8")
            locked.chmod(0o444)
        self.assertFalse(work.exists())


def _run_suite(start: Path) -> unittest.TestResult:
    """Run the tests under start against the pristine fixture, in this process."""
    saved_path = list(sys.path)
    before = set(sys.modules)
    sys.path.insert(0, str(FIXTURE))
    try:
        suite = unittest.TestLoader().discover(str(start), top_level_dir=str(start))
        result = unittest.TestResult()
        suite.run(result)
        return result
    finally:
        sys.path[:] = saved_path
        for name in set(sys.modules) - before:
            if name == "shop" or name.startswith(("shop.", "test_")):
                del sys.modules[name]


class FixtureTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tickets = json.loads((BENCH / "tickets.json").read_text(encoding="utf-8"))["tickets"]

    def test_the_visible_tests_pass_on_the_pristine_fixture(self) -> None:
        result = _run_suite(FIXTURE / "tests")
        self.assertGreater(result.testsRun, 0)
        self.assertTrue(result.wasSuccessful(), result.failures + result.errors)

    def test_every_hidden_suite_fails_on_the_pristine_fixture(self) -> None:
        for ticket in self.tickets:
            with self.subTest(ticket=ticket["key"]):
                result = _run_suite(HIDDEN / ticket["hidden"])
                self.assertGreater(result.testsRun, 0)
                self.assertFalse(result.wasSuccessful(), f"{ticket['key']} passes without any change")

    def test_every_expected_file_exists_or_has_a_home(self) -> None:
        for ticket in self.tickets:
            for path in ticket["expected_files"]:
                with self.subTest(ticket=ticket["key"], path=path):
                    self.assertTrue((FIXTURE / path).parent.is_dir(), path)

    def test_the_seven_shapes_are_all_there(self) -> None:
        self.assertEqual([f"BN-{n}" for n in range(1, 8)], [t["key"] for t in self.tickets])
        self.assertEqual(7, len({t["hidden"] for t in self.tickets}))

    def test_every_expected_function_exists_in_the_pristine_fixture(self) -> None:
        for ticket in self.tickets:
            for entry in ticket.get("expected_functions", []):
                with self.subTest(ticket=ticket["key"], entry=entry):
                    path, _, name = entry.partition("::")
                    self.assertIn(path, ticket["expected_files"])
                    spans = score.function_spans((FIXTURE / path).read_text(encoding="utf-8"))
                    self.assertIn(name, spans)


if __name__ == "__main__":
    unittest.main()

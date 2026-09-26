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

    def test_the_report_carries_commit_model_date_and_skipped_runs(self) -> None:
        text = self._report([_record("BN-1", "plain")], skipped=5)
        self.assertIn("abc1234", text)
        self.assertIn("claude-sonnet-5", text)
        self.assertIn("2026-09-26", text)
        self.assertIn("skipped by the spend cap: 5", text)


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
        self.assertIn("BN-9", bench)
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

    def test_the_four_shapes_are_all_there(self) -> None:
        self.assertEqual(["BN-1", "BN-2", "BN-3", "BN-4"], [t["key"] for t in self.tickets])
        self.assertEqual(4, len({t["hidden"] for t in self.tickets}))


if __name__ == "__main__":
    unittest.main()

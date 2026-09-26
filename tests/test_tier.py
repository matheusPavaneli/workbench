"""Rigour tiers: the bar scaling with the change, and refusing to scale too far.

A gate that costs more than the change it guards is a gate people route around,
so a small plan waives two sections. The tests that matter here are the ones
proving what it does *not* waive -- the citations, the file list, the verify
commands and the rollback are what make a plan checkable, and a one-line change
is not a less checkable one.
"""

from __future__ import annotations

import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import wb  # noqa: E402
from workbench import audit, gitctx, sdd  # noqa: E402
from workbench.errors import EXIT_AUDIT  # noqa: E402


def _plan(**overrides) -> dict:
    doc = {
        "schema": sdd.SCHEMA_VERSION,
        "key": "ABC-1",
        "preset": "solo-saas",
        "persona": "maintainer",
        "objective": "Fix the coupon ordering.",
        "evidence": [{"claim": "charge first", "file": "a.py", "line": 3, "quote": "createCharge()"}],
        "files": [{"path": "src/a.py", "change": "edit", "lines": 4, "why": "the ordering lives here"}],
        "zones": {},
        "steps": [],
        "tests": [{"kind": "regression", "asserts": "an expired coupon does not charge"}],
        "verify": ["pytest -q"],
        "rollback": "revert the commit",
        "product": {},
        "questions": [],
    }
    doc.update(overrides)
    return doc


class Tier(unittest.TestCase):
    def test_a_small_plain_change_is_light(self) -> None:
        self.assertEqual(sdd.LIGHT, sdd.tier(_plan())[0])

    def test_a_wide_change_is_standard(self) -> None:
        files = [{"path": f"src/{n}.py", "change": "edit", "why": "w"} for n in range(5)]
        self.assertEqual(sdd.STANDARD, sdd.tier(_plan(files=files))[0])

    def test_three_small_files_are_light(self) -> None:
        """A one-line fix, its test and a fixture used to pay for the full plan."""
        files = [
            {"path": "src/a.py", "change": "edit", "lines": 2, "why": "w"},
            {"path": "tests/test_a.py", "change": "edit", "lines": 30, "why": "w"},
            {"path": "tests/fixtures/a.json", "change": "add", "lines": 8, "why": "w"},
        ]
        tier, reason = sdd.tier(_plan(files=files))
        self.assertEqual(sdd.LIGHT, tier)
        self.assertEqual("~40 lines in 3 file(s), no critical zone", reason)

    def test_one_large_file_is_standard(self) -> None:
        """A 400-line rewrite of one file used to qualify as light."""
        tier, reason = sdd.tier(_plan(files=[{"path": "src/a.py", "change": "edit", "lines": 400, "why": "w"}]))
        self.assertEqual(sdd.STANDARD, tier)
        self.assertIn("~400 lines", reason)
        self.assertIn(f"light is up to {sdd.LIGHT_MAX_LINES}", reason)

    def test_the_bound_itself_is_light(self) -> None:
        files = [{"path": "src/a.py", "change": "edit", "lines": sdd.LIGHT_MAX_LINES, "why": "w"}]
        self.assertEqual(sdd.LIGHT, sdd.tier(_plan(files=files))[0])

    def test_a_missing_estimate_is_standard(self) -> None:
        """The safe default: a size nobody stated is not a small size."""
        files = [
            {"path": "src/a.py", "change": "edit", "lines": 2, "why": "w"},
            {"path": "src/b.py", "change": "edit", "why": "w"},
        ]
        tier, reason = sdd.tier(_plan(files=files))
        self.assertEqual(sdd.STANDARD, tier)
        self.assertIn("src/b.py", reason)

    def test_an_estimate_that_is_not_a_count_is_missing(self) -> None:
        for bad in ("10", -1, 2.5, True, None):
            files = [{"path": "src/a.py", "change": "edit", "lines": bad, "why": "w"}]
            self.assertEqual(sdd.STANDARD, sdd.tier(_plan(files=files))[0], bad)
            self.assertTrue(any("lines" in problem for problem in sdd.validate(_plan(files=files))), bad)

    def test_the_bound_is_a_parameter(self) -> None:
        files = [{"path": "src/a.py", "change": "edit", "lines": 30, "why": "w"}]
        self.assertEqual(sdd.STANDARD, sdd.tier(_plan(files=files), max_lines=20)[0])
        self.assertEqual(sdd.LIGHT, sdd.tier(_plan(files=files), max_lines=30)[0])

    def test_a_zone_is_standard_however_small_the_estimate(self) -> None:
        files = [{"path": "src/billing.py", "change": "edit", "lines": 1, "why": "w"}]
        self.assertEqual(sdd.STANDARD, sdd.tier(_plan(files=files))[0])

    def test_a_critical_zone_is_standard_however_small(self) -> None:
        tier, reason = sdd.tier(_plan(files=[{"path": "src/billing.py", "change": "edit", "why": "w"}]))
        self.assertEqual(sdd.STANDARD, tier)
        self.assertIn("billing", reason)

    def test_auth_counts_as_a_critical_zone(self) -> None:
        self.assertEqual(
            sdd.STANDARD, sdd.tier(_plan(files=[{"path": "src/auth/session.py", "change": "edit", "why": "w"}]))[0]
        )

    def test_a_bug_is_standard_because_someone_outside_engineering_reads_it(self) -> None:
        self.assertEqual(sdd.STANDARD, sdd.tier(_plan(ticket_type="bug"))[0])

    def test_a_plan_with_no_files_never_qualifies(self) -> None:
        self.assertEqual(sdd.STANDARD, sdd.tier(_plan(files=[]))[0])

    def test_the_tier_is_computed_not_declared(self) -> None:
        """A plan cannot ask for a lower bar by writing one into itself."""
        files = [{"path": f"src/{n}.py", "change": "edit", "why": "w"} for n in range(5)]
        self.assertEqual(sdd.STANDARD, sdd.tier(_plan(files=files, tier="light"))[0])


class ConfiguredBound(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / ".workflow").mkdir()

    def _config(self, value) -> None:
        (self.root / ".workflow" / "config.json").write_text(json.dumps({"light_max_lines": value}), encoding="utf-8")

    def test_no_config_is_the_documented_default(self) -> None:
        self.assertEqual(sdd.LIGHT_MAX_LINES, sdd.light_max_lines(self.root))

    def test_config_moves_the_bound(self) -> None:
        self._config(250)
        self.assertEqual(250, sdd.light_max_lines(self.root))

    def test_a_malformed_value_keeps_the_default(self) -> None:
        for bad in (0, -5, "250", 2.5, True, None):
            self._config(bad)
            self.assertEqual(sdd.LIGHT_MAX_LINES, sdd.light_max_lines(self.root), bad)


def _git(root: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(root), capture_output=True, text=True, check=True)


class MeasuredSize(unittest.TestCase):
    """The estimate is checked against the real diff, or it is the way down."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        _git(self.root, "init", "-q", ".")
        _git(self.root, "config", "user.email", "t@example.com")
        _git(self.root, "config", "user.name", "T")
        _git(self.root, "config", "core.autocrlf", "false")
        (self.root / "src").mkdir()
        (self.root / "src" / "a.py").write_bytes(b"one\n")
        _git(self.root, "add", "-A")
        _git(self.root, "commit", "-qm", "init")
        self.baseline = gitctx.head(self.root) or ""

        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)
        previous = os.environ.get("WORKBENCH_NO_EVENTS")
        os.environ["WORKBENCH_NO_EVENTS"] = "1"
        self.addCleanup(self._restore, previous)

    def _restore(self, previous: str | None) -> None:
        if previous is None:
            os.environ.pop("WORKBENCH_NO_EVENTS", None)
        else:
            os.environ["WORKBENCH_NO_EVENTS"] = previous

    def _plan(self, lines: int) -> None:
        doc = _plan(files=[{"path": "src/a.py", "change": "edit", "lines": lines, "why": "w"}])
        directory = self.root / ".workflow" / "ABC-1"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "sdd.json").write_text(json.dumps(doc), encoding="utf-8")
        (directory / "audit.json").write_text(
            json.dumps({"verdict": "pass", "plan_sha256": audit.digest(doc), "baseline": self.baseline}),
            encoding="utf-8",
        )

    def _check(self) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = wb.main(["impl", "check", "ABC-1"])
        return code, out.getvalue(), err.getvalue()

    def test_lines_changed_counts_added_and_removed(self) -> None:
        (self.root / "src" / "a.py").write_bytes(b"two\nthree\n")
        self.assertEqual(3, gitctx.lines_changed(self.root, self.baseline, ["src/a.py"]))

    def test_an_untracked_planned_file_counts_whole(self) -> None:
        (self.root / "src" / "b.py").write_bytes(b"x\ny\n")
        self.assertEqual(2, gitctx.lines_changed(self.root, self.baseline, ["src/b.py"]))

    def test_an_untracked_non_ascii_name_is_measured(self) -> None:
        """git quotes such a name unless asked not to, and the quoted form cannot be opened."""
        (self.root / "src" / "não.py").write_bytes(b"x\ny\nz\n")
        self.assertEqual(3, gitctx.lines_changed(self.root, self.baseline, ["src/não.py"]))

    def test_a_binary_file_cannot_be_measured(self) -> None:
        (self.root / "src" / "a.py").write_bytes(bytes([0, 1, 2]))
        self.assertIsNone(gitctx.lines_changed(self.root, self.baseline, ["src/a.py"]))

    def test_a_light_plan_inside_its_bound_passes(self) -> None:
        self._plan(4)
        (self.root / "src" / "a.py").write_bytes(b"two\n")
        code, out, _ = self._check()
        self.assertEqual(0, code)
        self.assertIn("2 line(s) changed", out)

    def test_a_light_plan_whose_diff_outgrew_the_bound_fails(self) -> None:
        self._plan(4)
        (self.root / "src" / "a.py").write_bytes(b"x\n" * (sdd.LIGHT_MAX_LINES + 1))
        code, _, err = self._check()
        self.assertEqual(EXIT_AUDIT, code)
        self.assertIn("DEVIATION", err)
        self.assertIn(f"measured {sdd.LIGHT_MAX_LINES + 2} lines", err)
        self.assertIn("Re-plan at", err)

    def test_an_unmeasurable_diff_counts_as_over(self) -> None:
        self._plan(4)
        (self.root / "src" / "a.py").write_bytes(bytes([0, 1, 2]))
        code, _, err = self._check()
        self.assertEqual(EXIT_AUDIT, code)
        self.assertIn("cannot be measured", err)

    def test_a_standard_plan_is_not_held_to_the_bound(self) -> None:
        self._plan(500)
        (self.root / "src" / "a.py").write_bytes(b"x\n" * (sdd.LIGHT_MAX_LINES + 1))
        self.assertEqual(0, self._check()[0])


class WhatLightWaives(unittest.TestCase):
    def test_steps_may_be_empty_on_a_light_plan(self) -> None:
        self.assertEqual([], sdd.validate(_plan()))

    def test_steps_are_still_required_on_a_standard_plan(self) -> None:
        files = [{"path": f"src/{n}.py", "change": "edit", "why": "w"} for n in range(5)]
        problems = sdd.validate(_plan(files=files))
        self.assertTrue(any("steps" in problem for problem in problems))

    def test_the_product_section_is_waived_on_a_light_plan(self) -> None:
        self.assertFalse(any("product" in problem for problem in sdd.validate(_plan())))

    def test_the_product_section_is_required_on_a_standard_one(self) -> None:
        files = [{"path": f"src/{n}.py", "change": "edit", "why": "w"} for n in range(5)]
        problems = sdd.validate(_plan(files=files, steps=["do it"]))
        self.assertTrue(any("product" in problem for problem in problems))


class WhatLightNeverWaives(unittest.TestCase):
    """The four sections that make a plan checkable at all."""

    def test_citations_are_still_required(self) -> None:
        problems = sdd.validate(_plan(evidence=[]))
        self.assertTrue(any("evidence" in problem for problem in problems))

    def test_a_citation_still_needs_its_quote(self) -> None:
        problems = sdd.validate(_plan(evidence=[{"claim": "c", "file": "a.py", "line": 1}]))
        self.assertTrue(any("quote" in problem for problem in problems))

    def test_verify_commands_are_still_required(self) -> None:
        problems = sdd.validate(_plan(verify=[]))
        self.assertTrue(any("verify" in problem for problem in problems))

    def test_a_rollback_is_still_required(self) -> None:
        problems = sdd.validate(_plan(rollback=""))
        self.assertTrue(any("rollback" in problem for problem in problems))

    def test_tests_are_still_required(self) -> None:
        problems = sdd.validate(_plan(tests=[]))
        self.assertTrue(any("tests" in problem for problem in problems))

    def test_a_file_still_needs_a_reason(self) -> None:
        problems = sdd.validate(_plan(files=[{"path": "src/a.py", "change": "edit"}]))
        self.assertTrue(any("why" in problem for problem in problems))

    def test_a_bug_still_owes_its_handover(self) -> None:
        problems = sdd.validate(_plan(ticket_type="bug"))
        self.assertTrue(any("handover" in problem for problem in problems))


if __name__ == "__main__":
    unittest.main()

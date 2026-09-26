"""`wb sdd amend`: the narrow door for a file the plan missed.

It must widen a plan without weakening it: the audit re-runs at the same
baseline, a wider plan is held to the bar its new size demands, and the record
that it widened reaches the reviewer.
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
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import wb  # noqa: E402
from workbench import audit, events, scope, sdd  # noqa: E402
from workbench.errors import EXIT_AUDIT, EXIT_USAGE  # noqa: E402


def run(*argv: str) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = wb.main(list(argv))
    return code, out.getvalue(), err.getvalue()


def _plan(**overrides) -> dict:
    doc = {
        "schema": sdd.SCHEMA_VERSION,
        "key": "ABC-1",
        "preset": "solo-saas",
        "persona": "maintainer",
        "objective": "Round the totals.",
        "evidence": [{"claim": "totals", "file": "src/util.py", "line": 1, "quote": "def total(items):"}],
        "files": [{"path": "src/util.py", "change": "edit", "lines": 5, "why": "rounding lives here"}],
        "zones": {},
        "steps": [],
        "tests": [{"kind": "unit", "asserts": "totals round half up"}],
        "verify": ["python -m unittest -q"],
        "rollback": "revert the commit",
        "product": {},
        "questions": [],
    }
    doc.update(overrides)
    return doc


class Amend(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self.git("init", "-q", ".")
        self.git("config", "user.email", "t@example.com")
        self.git("config", "user.name", "T")
        (self.root / ".gitignore").write_text(".workflow/\n", encoding="utf-8")
        (self.root / "src").mkdir()
        (self.root / "src" / "util.py").write_text("def total(items):\n    return sum(items)\n", encoding="utf-8")
        (self.root / "src" / "format.py").write_text("def money(x):\n    return str(x)\n", encoding="utf-8")
        self.git("add", "-A")
        self.git("commit", "-qm", "init")
        self.git("switch", "-q", "-c", "ABC-1-totals")

        cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(os.chdir, cwd)
        environment = mock.patch.dict(os.environ, {"WORKBENCH_NO_EVENTS": "1", "WORKBENCH_HOME": str(self.root / "h")})
        environment.start()
        self.addCleanup(environment.stop)

        self.dir = self.root / ".workflow" / "ABC-1"
        self.dir.mkdir(parents=True)
        self.write_plan(_plan())
        self.assertEqual(0, run("sdd", "audit", "ABC-1")[0])
        self.baseline = self.audit()["baseline"]

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=str(self.root), capture_output=True, text=True, check=True)

    def write_plan(self, doc: dict) -> None:
        (self.dir / "sdd.json").write_text(json.dumps(doc), encoding="utf-8")

    def plan(self) -> dict:
        return json.loads((self.dir / "sdd.json").read_text(encoding="utf-8"))

    def audit(self) -> dict:
        return json.loads((self.dir / "audit.json").read_text(encoding="utf-8"))

    def test_the_happy_path_widens_the_plan_and_it_still_stands(self) -> None:
        code, out, _ = run("sdd", "amend", "ABC-1", "src/format.py", "--why", "totals print through it", "--lines", "4")
        self.assertEqual(0, code, out)
        doc = self.plan()
        self.assertEqual(
            {"path": "src/format.py", "change": "edit", "lines": 4, "why": "totals print through it"}, doc["files"][-1]
        )
        [amendment] = doc["amendments"]
        self.assertEqual(("src/format.py", "totals print through it"), (amendment["path"], amendment["why"]))
        self.assertTrue(amendment["at"])
        self.assertEqual({}, doc["zones"])
        self.assertIn("light tier", out)
        self.assertIsNone(audit.standing(self.audit(), doc))
        self.assertEqual(self.baseline, self.audit()["baseline"])

    def test_zones_are_recomputed(self) -> None:
        (self.root / "src" / "billing.py").write_text("x = 1\n", encoding="utf-8")
        run("sdd", "amend", "ABC-1", "src/billing.py", "--why", "w", "--lines", "1")
        self.assertEqual({"billing": ["src/billing.py"]}, self.plan()["zones"])

    def test_why_is_required(self) -> None:
        before = self.plan()
        code, _, err = run("sdd", "amend", "ABC-1", "src/format.py")
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("--why", err)
        self.assertEqual(before, self.plan())

    def test_a_missing_path_needs_new(self) -> None:
        code, _, err = run("sdd", "amend", "ABC-1", "src/new.py", "--why", "w")
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("--new", err)
        code, _, _ = run("sdd", "amend", "ABC-1", "src/new.py", "--why", "w", "--new", "--lines", "3")
        self.assertEqual(0, code)
        self.assertEqual("add", self.plan()["files"][-1]["change"])

    def test_new_refuses_a_file_that_exists(self) -> None:
        self.assertEqual(EXIT_USAGE, run("sdd", "amend", "ABC-1", "src/format.py", "--why", "w", "--new")[0])

    def test_an_already_planned_path_is_refused(self) -> None:
        self.assertEqual(EXIT_USAGE, run("sdd", "amend", "ABC-1", "src/util.py", "--why", "w")[0])

    def test_a_plan_that_does_not_stand_cannot_be_amended(self) -> None:
        self.write_plan(_plan(objective="changed after the audit"))
        code, _, err = run("sdd", "amend", "ABC-1", "src/format.py", "--why", "w")
        self.assertEqual(EXIT_USAGE, code)
        self.assertIn("wb sdd audit ABC-1", err)

    def test_raising_the_tier_fails_the_audit_and_leaves_it_not_standing(self) -> None:
        """No estimate: the plan goes standard, and steps and product are owed."""
        before = self.audit()
        code, out, err = run("sdd", "amend", "ABC-1", "src/format.py", "--why", "w")
        self.assertEqual(EXIT_AUDIT, code)
        self.assertIn("standard tier", err)
        self.assertIn("steps", err)
        self.assertEqual("src/format.py", self.plan()["files"][-1]["path"])
        self.assertEqual(before, self.audit())
        self.assertIsNotNone(audit.standing(self.audit(), self.plan()))

    def test_the_amended_plan_claims_its_new_file(self) -> None:
        run("sdd", "amend", "ABC-1", "src/format.py", "--why", "w", "--lines", "2")
        self.assertIn("src/format.py", scope.claims("ABC-2"))

    def test_the_amendment_reaches_the_event_log_as_a_count(self) -> None:
        with mock.patch.dict(os.environ):
            os.environ.pop("WORKBENCH_NO_EVENTS")
            run("sdd", "amend", "ABC-1", "src/format.py", "--why", "w", "--lines", "2")
        entry = events.read()[-1]
        self.assertEqual(("sdd", "amend", 0, 1), (entry["group"], entry["action"], entry["exit"], entry["amended"]))
        self.assertNotIn("src/format.py", json.dumps(entry))

    def test_pr_and_review_context_show_the_amendments(self) -> None:
        run("sdd", "amend", "ABC-1", "src/format.py", "--why", "prints totals", "--lines", "2")
        (self.root / "src" / "format.py").write_text("def money(x):\n    return f'{x:.2f}'\n", encoding="utf-8")
        _, out, _ = run("pr", "context", "ABC-1", "--base", "HEAD")
        self.assertEqual(["src/format.py"], [item["path"] for item in json.loads(out)["amended"]])
        _, out, _ = run("review", "context")
        self.assertIn("plan amended after its audit", out)
        self.assertIn("src/format.py  (prints totals)", out)
        _, out, _ = run("review", "context", "--json")
        self.assertEqual("prints totals", json.loads(out)["amended"][0]["why"])

    def test_the_rendered_plan_lists_them(self) -> None:
        run("sdd", "amend", "ABC-1", "src/format.py", "--why", "prints totals", "--lines", "2")
        self.assertIn("## Amendments", sdd.render(self.plan()))


class Validation(unittest.TestCase):
    def test_a_plan_without_amendments_validates_as_before(self) -> None:
        self.assertEqual([], sdd.validate(_plan()))

    def test_a_malformed_amendment_is_reported(self) -> None:
        problems = sdd.validate(_plan(amendments=[{"path": "src/util.py"}, "x", {"path": "gone.py", "why": "w", "at": "t"}]))
        self.assertTrue(any("amendments[0] has no why" in problem for problem in problems))
        self.assertTrue(any("amendments[1] must be an object" in problem for problem in problems))
        self.assertTrue(any("gone.py, which files does not list" in problem for problem in problems))


if __name__ == "__main__":
    unittest.main()

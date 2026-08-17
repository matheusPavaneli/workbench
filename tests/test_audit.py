import subprocess
import tempfile
import unittest
from pathlib import Path

from workbench import audit, gitctx, sdd


def _doc(**overrides) -> dict:
    doc = {
        "schema": 1,
        "key": "ABC-1",
        "preset": "scaleup",
        "persona": "fullstack-specialist",
        "objective": "Validate the coupon before charging.",
        "evidence": [
            {
                "claim": "the charge is created before validation",
                "file": "src/checkout.py",
                "line": 2,
                "quote": "charge = create_charge(total)",
            }
        ],
        "files": [{"path": "src/checkout.py", "change": "edit", "why": "reorder"}],
        "zones": {},
        "steps": [{"do": "move validation above the charge"}],
        "tests": [{"kind": "regression", "target": "tests/test_checkout.py", "asserts": "expired coupon returns 422"}],
        "verify": ["pytest -q"],
        "rollback": "revert the commit",
        "product": {},
        "questions": [],
    }
    doc.update(overrides)
    return doc


class AuditCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        source = self.root / "src"
        source.mkdir()
        (source / "checkout.py").write_text(
            "def checkout(total):\n    charge = create_charge(total)\n    validate(coupon)\n",
            encoding="utf-8",
        )


class Citations(AuditCase):
    def test_true_citation_passes(self) -> None:
        report = audit.run(_doc(), self.root)
        self.assertTrue(report.passed, report.to_dict())

    def test_invented_quote_fails_and_shows_the_real_line(self) -> None:
        doc = _doc(evidence=[{"claim": "c", "file": "src/checkout.py", "line": 2, "quote": "charge = Stripe.pay()"}])
        report = audit.run(doc, self.root)
        finding = report.failures[0]
        self.assertEqual(audit.MISMATCH, finding.verdict)
        self.assertIn("create_charge", finding.detail)

    def test_wrong_line_reports_the_right_one(self) -> None:
        doc = _doc(evidence=[{"claim": "c", "file": "src/checkout.py", "line": 3, "quote": "charge = create_charge(total)"}])
        report = audit.run(doc, self.root)
        finding = report.failures[0]
        self.assertEqual(audit.MOVED, finding.verdict)
        self.assertIn("line 2", finding.detail)

    def test_a_citation_pointing_at_a_blank_line_fails(self) -> None:
        """An empty line is a substring of every quote.

        Found by dogfooding: a plan citing a blank line passed the audit, which
        is exactly the class of error this module exists to catch.
        """
        (self.root / "src" / "spaced.py").write_text("first\n\nthird\n", encoding="utf-8")
        doc = _doc(
            evidence=[{"claim": "c", "file": "src/spaced.py", "line": 2, "quote": "charge = create_charge(total)"}]
        )
        report = audit.run(doc, self.root)
        self.assertFalse(report.passed)
        self.assertEqual(audit.MISMATCH, report.failures[0].verdict)

    def test_a_blank_line_is_never_offered_as_the_moved_location(self) -> None:
        (self.root / "src" / "spaced.py").write_text("first\n\nthird\n", encoding="utf-8")
        doc = _doc(
            evidence=[{"claim": "c", "file": "src/spaced.py", "line": 1, "quote": "a quote matching nothing here"}]
        )
        finding = audit.run(doc, self.root).failures[0]
        self.assertEqual(audit.MISMATCH, finding.verdict)

    def test_a_trivial_line_cannot_support_a_longer_quote(self) -> None:
        (self.root / "src" / "tiny.py").write_text("x = 1\n)\n", encoding="utf-8")
        doc = _doc(
            evidence=[{"claim": "c", "file": "src/tiny.py", "line": 2, "quote": "some_call(argument, other))"}]
        )
        self.assertFalse(audit.run(doc, self.root).passed)

    def test_a_wrapped_statement_still_matches(self) -> None:
        (self.root / "src" / "wrapped.py").write_text(
            "result = compute_the_total(\n    items, discount_code\n)\n", encoding="utf-8"
        )
        doc = _doc(
            evidence=[
                {
                    "claim": "c",
                    "file": "src/wrapped.py",
                    "line": 1,
                    "quote": "result = compute_the_total( items, discount_code )",
                }
            ]
        )
        self.assertTrue(audit.run(doc, self.root).passed)

    def test_nonexistent_file_fails(self) -> None:
        doc = _doc(evidence=[{"claim": "c", "file": "src/nope.py", "line": 1, "quote": "x"}])
        self.assertEqual(audit.MISSING_FILE, audit.run(doc, self.root).failures[0].verdict)

    def test_line_past_end_of_file_fails(self) -> None:
        doc = _doc(evidence=[{"claim": "c", "file": "src/checkout.py", "line": 900, "quote": "x"}])
        finding = audit.run(doc, self.root).failures[0]
        self.assertEqual(audit.OUT_OF_RANGE, finding.verdict)
        self.assertIn("3 lines", finding.detail)

    def test_citation_without_a_quote_cannot_pass(self) -> None:
        doc = _doc(evidence=[{"claim": "c", "file": "src/checkout.py", "line": 2, "quote": "   "}])
        report = audit.run(doc, self.root)
        self.assertFalse(report.passed)

    def test_path_escaping_the_repo_is_refused(self) -> None:
        doc = _doc(evidence=[{"claim": "c", "file": "../../etc/passwd", "line": 1, "quote": "root"}])
        self.assertEqual(audit.MISSING_FILE, audit.run(doc, self.root).failures[0].verdict)

    def test_whitespace_differences_are_tolerated(self) -> None:
        doc = _doc(evidence=[{"claim": "c", "file": "src/checkout.py", "line": 2, "quote": "charge  =   create_charge(total)"}])
        self.assertTrue(audit.run(doc, self.root).passed)

    def test_editing_a_file_that_does_not_exist_fails(self) -> None:
        doc = _doc(files=[{"path": "src/ghost.py", "change": "edit", "why": "x"}])
        report = audit.run(doc, self.root)
        self.assertIn("src/ghost.py", report.missing_paths)

    def test_adding_a_file_that_does_not_exist_is_fine(self) -> None:
        doc = _doc(files=[{"path": "src/new.py", "change": "add", "why": "new module"}])
        self.assertEqual([], audit.run(doc, self.root).missing_paths)


class Structure(AuditCase):
    def test_missing_rollback_is_reported(self) -> None:
        problems = sdd.validate(_doc(rollback=""))
        self.assertTrue(any("rollback" in p for p in problems))

    def test_missing_tests_names_the_floor(self) -> None:
        problems = sdd.validate(_doc(tests=[]))
        self.assertTrue(any("unit test" in p for p in problems))

    def test_assertionless_test_is_rejected(self) -> None:
        problems = sdd.validate(_doc(tests=[{"kind": "unit", "target": "t", "asserts": ""}]))
        self.assertTrue(any("asserts" in p for p in problems))

    def test_unknown_change_kind_is_rejected(self) -> None:
        problems = sdd.validate(_doc(files=[{"path": "a", "change": "tweak", "why": "x"}]))
        self.assertTrue(any("change must be one of" in p for p in problems))

    def test_solo_saas_must_state_the_product_effect(self) -> None:
        problems = sdd.validate(_doc(preset="solo-saas"))
        self.assertTrue(any("product" in p for p in problems))

    def test_scaleup_does_not_require_the_product_section(self) -> None:
        self.assertEqual([], sdd.validate(_doc(preset="scaleup")))

    def test_structure_failure_fails_the_whole_audit(self) -> None:
        report = audit.run(_doc(rollback=""), self.root)
        self.assertFalse(report.passed)
        self.assertTrue(report.structure)


class Sections(unittest.TestCase):
    def test_summary_is_a_slice_not_the_whole_plan(self) -> None:
        summary = sdd.section(_doc(), "summary")
        self.assertEqual(["src/checkout.py"], summary["files"])
        self.assertNotIn("evidence", summary)

    def test_unknown_section_lists_the_valid_ones(self) -> None:
        from workbench.errors import UsageError

        with self.assertRaises(UsageError) as caught:
            sdd.section(_doc(), "conclusion")
        self.assertIn("rollback", caught.exception.render())

    def test_render_produces_markdown_with_the_citation(self) -> None:
        text = sdd.render(_doc())
        self.assertIn("# ABC-1", text)
        self.assertIn("src/checkout.py:2", text)



class Baseline(unittest.TestCase):
    """Correcting a plan while implementing it.

    The audit reads the working tree, so once code has changed, every citation
    written before the change fails -- and plan-change tells the author to fix
    the plan exactly when the tree has already moved. The first audit records
    the commit it ran against; every audit after it is anchored there.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self._git(["init", "-q", "."])
        self._git(["config", "user.email", "t@example.com"])
        self._git(["config", "user.name", "T"])
        (self.root / "src").mkdir()
        self.write("src/checkout.py", "def pay(total):\n    charge = create_charge(total)\n    validate()\n")
        self._git(["add", "-A"])
        self._git(["commit", "-qm", "init"])
        self.baseline = gitctx.head(self.root)

    def _git(self, args: list[str]) -> None:
        subprocess.run(["git", *args], cwd=str(self.root), capture_output=True, text=True, check=False)

    def write(self, relative: str, content: str) -> None:
        (self.root / relative).write_text(content, encoding="utf-8")

    def test_the_first_audit_is_strict_about_line_numbers(self) -> None:
        """A wrong number is a defect while the plan is still cheap to change."""
        self.write("src/checkout.py", "# a new first line\ndef pay(total):\n    charge = create_charge(total)\n")
        report = audit.run(_doc(), self.root)
        self.assertFalse(report.passed)
        self.assertEqual(audit.MOVED, report.findings[0].verdict)

    def test_a_plan_under_way_is_not_failed_for_a_shifted_line(self) -> None:
        self.write("src/checkout.py", "# a new first line\ndef pay(total):\n    charge = create_charge(total)\n")
        report = audit.run(_doc(), self.root, self.baseline)
        self.assertTrue(report.passed)
        self.assertEqual(audit.MOVED, report.findings[0].verdict)

    def test_a_line_rewritten_during_implementation_verifies_at_the_baseline(self) -> None:
        self.write("src/checkout.py", "def pay(total):\n    validate()\n    charge = bill(total)\n")
        report = audit.run(_doc(), self.root, self.baseline)
        self.assertTrue(report.passed)
        self.assertEqual(audit.BASELINE, report.findings[0].verdict)

    def test_a_cited_file_deleted_during_implementation_still_verifies(self) -> None:
        (self.root / "src" / "checkout.py").unlink()
        doc = _doc(files=[{"path": "src/new.py", "change": "add", "why": "replacement"}])
        report = audit.run(doc, self.root, self.baseline)
        self.assertEqual(audit.BASELINE, report.findings[0].verdict)

    def test_a_claim_that_was_never_true_is_still_a_mismatch(self) -> None:
        """The fallback must not become a way to pass an invented citation."""
        doc = _doc(evidence=[{"claim": "c", "file": "src/checkout.py", "line": 2, "quote": "refund_everything()"}])
        report = audit.run(doc, self.root, self.baseline)
        self.assertFalse(report.passed)
        self.assertEqual(audit.MISMATCH, report.findings[0].verdict)

    def test_the_first_audit_records_the_commit_for_the_ones_after_it(self) -> None:
        report = audit.run(_doc(), self.root)
        self.assertEqual(self.baseline, report.baseline)
        self.assertFalse(report.under_way)

    def test_a_re_audit_reports_itself_as_under_way(self) -> None:
        report = audit.run(_doc(), self.root, self.baseline)
        self.assertTrue(report.under_way)
        self.assertEqual(self.baseline, report.baseline)

    def test_a_drifted_citation_is_never_folded_into_a_plain_ok(self) -> None:
        """A reader has to be able to tell which claims describe old code."""
        self.write("src/checkout.py", "def pay(total):\n    validate()\n    charge = bill(total)\n")
        report = audit.run(_doc(), self.root, self.baseline)
        self.assertNotEqual(audit.OK, report.findings[0].verdict)
        self.assertIn(report.findings[0].verdict, report.passing)

    def test_an_unchanged_tree_still_verifies_at_the_cited_line(self) -> None:
        report = audit.run(_doc(), self.root, self.baseline)
        self.assertEqual(audit.OK, report.findings[0].verdict)


class OutsideGit(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        (self.root / "src").mkdir()
        (self.root / "src" / "checkout.py").write_text(
            "def pay(total):\n    charge = create_charge(total)\n", encoding="utf-8"
        )

    def test_an_audit_with_no_commit_to_anchor_to_still_runs(self) -> None:
        """A folder that is not a checkout has no baseline, and must not need one."""
        report = audit.run(_doc(), self.root)
        self.assertTrue(report.passed)
        self.assertEqual("", report.baseline)

if __name__ == "__main__":
    unittest.main()

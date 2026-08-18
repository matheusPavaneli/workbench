"""Scenario checks: does the whole chain hold together on a realistic ticket?

Everything else in this suite tests a unit or a seam. These walk a ticket from
triage to commit the way a session would, and assert on the **artifacts** rather
than on any model's prose -- which is the only part of an agent workflow that
can be asserted at all. A skill's wording is a prompt; the file it leaves behind
is a fact.

Three shapes, chosen because they exercise different decisions:

- a bug with a thin description, which must still produce a handover
- a feature in a monorepo, where the bar comes from the path
- an incident, which has no ticket and starts from a symptom

What they catch is the failure that unit tests structurally cannot: a stage that
works alone and produces something the next stage refuses.
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

from test_cli import CliBase, run  # noqa: E402

from workbench import audit as audit_lib, sdd as sdd_lib  # noqa: E402
from workbench.errors import EXIT_AUDIT  # noqa: E402


class Scenario(CliBase):
    """A repo with real files, so citations can be checked against something."""

    def setUp(self) -> None:
        super().setUp()
        self.source = self.root / "src"
        self.source.mkdir()
        (self.source / "coupon.py").write_text(
            "def apply(coupon, total):\n"
            "    if coupon.expired:\n"
            "        return total\n"
            "    return total - coupon.amount\n",
            encoding="utf-8",
        )
        (self.root / "packages" / "billing").mkdir(parents=True)
        (self.root / "packages" / "billing" / "charge.py").write_text(
            "def charge(customer, amount):\n    return gateway.post(customer, amount)\n", encoding="utf-8"
        )

    def config(self, **extra) -> None:
        path = self.root / ".workflow" / "config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"provider": "local", **extra}), encoding="utf-8")

    def triage(self, key: str, **fields) -> None:
        directory = self.root / ".workflow" / key
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "triage.json").write_text(json.dumps({"key": key, **fields}), encoding="utf-8")

    def plan(self, key: str, doc: dict) -> Path:
        directory = self.root / ".workflow" / key
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "sdd.json"
        path.write_text(json.dumps(doc), encoding="utf-8")
        return path


class ABugWithAThinDescription(Scenario):
    """The common case, and the one where the process earns its keep: the
    ticket says almost nothing, and the plan has to be anchored in the code."""

    KEY = "ABC-42"

    def _doc(self, **overrides) -> dict:
        doc = {
            "schema": sdd_lib.SCHEMA_VERSION,
            "key": self.KEY,
            "preset": "startup",
            "persona": "engineer",
            "ticket_type": "bug",
            "objective": "an expired coupon still reduces the total",
            "evidence": [
                {
                    "file": "src/coupon.py",
                    "line": 2,
                    "quote": "    if coupon.expired:",
                    "claim": "expiry is checked, but the discount is applied regardless of the result",
                },
            ],
            "files": [
                {
                    "path": "src/coupon.py",
                    "change": "edit",
                    "why": "the expiry branch returns the discounted total instead of the original",
                }
            ],
            "steps": ["return the total unchanged when the coupon has expired"],
            "tests": [
                {"kind": "regression", "asserts": "an expired coupon leaves the total unchanged"},
            ],
            "verify": ["python -m unittest discover -s tests -q"],
            "rollback": "revert the commit",
            "product": {"metric": "checkout revenue leakage", "who_asked": "support, via the escalation"},
            "handover": {
                "symptom_plain": "expired coupons still take money off the total",
                "cause_plain": "the expiry check runs after the discount",
                "fix_plain": "check expiry first",
                "scope": "checkout only",
                "workaround": "remove the coupon by hand",
                "qa_steps": ["apply an expired coupon", "confirm the total does not change"],
            },
        }
        doc.update(overrides)
        return doc

    def test_the_route_for_a_bug_always_includes_the_handover(self) -> None:
        self.config()
        self.triage(self.KEY, type="bug")
        _, out, _ = run("route", self.KEY, "--files", "src/coupon.py")
        self.assertIn("write-handover", out)

    def test_a_plan_anchored_in_the_real_file_passes_the_audit(self) -> None:
        self.config()
        self.triage(self.KEY, type="bug")
        self.plan(self.KEY, self._doc())
        code, out, _ = run("sdd", "audit", self.KEY)
        self.assertEqual(0, code, out)

    def test_a_plan_citing_a_line_that_does_not_say_that_fails(self) -> None:
        """The whole point of the audit: a citation is checked, not trusted."""
        self.config()
        self.triage(self.KEY, type="bug")
        self.plan(self.KEY, self._doc(evidence=[
            {
                "file": "src/coupon.py",
                "line": 2,
                "quote": "    if coupon.is_expired():",
                "claim": "the expiry check calls a method",
            },
        ]))
        code, _, err = run("sdd", "audit", self.KEY)
        self.assertEqual(EXIT_AUDIT, code)
        self.assertIn("fix the plan", err)

    def test_a_bug_plan_without_a_handover_is_refused(self) -> None:
        """Someone has to be able to validate the fix without reading the diff."""
        doc = self._doc()
        doc.pop("handover")
        problems = sdd_lib.validate(doc)
        self.assertTrue(any("handover" in problem for problem in problems), problems)

    def test_the_whole_chain_leaves_the_artifacts_the_next_stage_needs(self) -> None:
        """A stage that works alone and produces something the next stage
        refuses is the failure unit tests structurally cannot see."""
        self.config()
        self.triage(self.KEY, type="bug", title="coupon")
        self.plan(self.KEY, self._doc())

        self.assertEqual(0, run("sdd", "audit", self.KEY)[0])
        self.assertEqual(0, run("sdd", "handover", self.KEY)[0])

        directory = self.root / ".workflow" / self.KEY
        for artifact in ("triage.json", "sdd.json", "audit.json", "handover.md"):
            self.assertTrue((directory / artifact).is_file(), f"{artifact} is missing")

        _, out, _ = run("next", self.KEY)
        self.assertIn(self.KEY, out)


class AMalformedPlan(Scenario):
    """Found by the scenarios above, not by review.

    A list of strings where the schema wants a list of objects is the commonest
    malformed plan there is -- it is what a model writes when it has read the
    section name and not the shape. Every one of these used to raise
    AttributeError from inside the checker, and an audit that crashes teaches
    people to skip the audit.
    """

    KEY = "ABC-44"

    MALFORMED = {
        "schema": sdd_lib.SCHEMA_VERSION,
        "key": KEY,
        "preset": "startup",
        "objective": "something",
        "evidence": ["src/coupon.py:2"],
        "files": ["src/coupon.py"],
        "tests": ["an expired coupon changes nothing"],
        "verify": ["python -m unittest"],
        "rollback": "revert",
    }

    def test_validate_reports_the_shape_instead_of_raising(self) -> None:
        problems = sdd_lib.validate(self.MALFORMED)
        for section in ("evidence", "files", "tests"):
            self.assertTrue(
                any(problem.startswith(f"{section}[0] must be an object") for problem in problems),
                f"{section} was not reported: {problems}",
            )

    def test_the_tier_is_computed_without_raising(self) -> None:
        """tier() runs before validate(), so it is reached by exactly the
        documents that have not been checked yet."""
        tier, _ = sdd_lib.tier(self.MALFORMED)
        self.assertIn(tier, (sdd_lib.LIGHT, sdd_lib.STANDARD))

    def test_the_audit_fails_it_cleanly(self) -> None:
        self.config()
        self.plan(self.KEY, self.MALFORMED)
        code, _, err = run("sdd", "audit", self.KEY)
        self.assertEqual(EXIT_AUDIT, code)
        self.assertIn("must be an object", err)
        self.assertNotIn("Traceback", err)


class AFeatureInAMonorepo(Scenario):
    """Where the bar comes from the path rather than from the repo."""

    KEY = "ABC-43"

    def test_the_route_is_full_because_of_where_it_lands(self) -> None:
        self.config(preset="prototype", preset_confirmed=True)
        _, out, _ = run("route", self.KEY, "--files", "packages/billing/charge.py")
        self.assertIn("standard route", out)
        self.assertIn("billing", out)

    def test_the_gates_come_from_the_path_not_the_repo(self) -> None:
        self.config(
            preset="prototype",
            preset_confirmed=True,
            preset_paths={"packages/billing/**": "enterprise"},
        )
        _, out, _ = run("repo", "gates", "packages/billing/charge.py")
        self.assertIn("enterprise", out)
        self.assertIn("backwards compatibility", out)

    def test_a_plan_declared_under_that_bar_fails_the_audit(self) -> None:
        self.config(
            preset="prototype",
            preset_confirmed=True,
            preset_paths={"packages/billing/**": "enterprise"},
        )
        doc = {
            "key": self.KEY,
            "preset": "prototype",
            "files": [{"path": "packages/billing/charge.py", "change": "edit"}],
        }
        problems = audit_lib._preset_problems(doc, self.root)
        self.assertTrue(problems)
        self.assertIn("enterprise", problems[0])


class AnIncident(Scenario):
    """No ticket, a symptom, and a hotfix that still has to be checkable."""

    KEY = "incident-checkout-500s"

    def test_an_incident_key_is_a_valid_place_to_put_artifacts(self) -> None:
        self.config()
        self.triage(self.KEY, type="incident")
        _, out, _ = run("status", self.KEY)
        self.assertIn(self.KEY, out)

    def test_an_incident_owes_a_handover_even_with_a_one_file_fix(self) -> None:
        self.config()
        self.triage(self.KEY, type="incident")
        _, out, _ = run("route", self.KEY, "--files", "src/coupon.py")
        self.assertIn("write-handover", out)

    def test_next_resolves_an_incident_from_the_branch(self) -> None:
        self.config()
        self.triage(self.KEY, type="incident")
        with mock.patch("workbench.gitctx.branch", return_value=f"hotfix/{self.KEY}"):
            _, out, _ = run("next")
        self.assertIn(self.KEY, out)
        self.assertIn("(branch)", out)


if __name__ == "__main__":
    unittest.main()

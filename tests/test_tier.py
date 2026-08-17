"""Rigour tiers: the bar scaling with the change, and refusing to scale too far.

A gate that costs more than the change it guards is a gate people route around,
so a small plan waives two sections. The tests that matter here are the ones
proving what it does *not* waive -- the citations, the file list, the verify
commands and the rollback are what make a plan checkable, and a one-line change
is not a less checkable one.
"""

import unittest

from workbench import sdd


def _plan(**overrides) -> dict:
    doc = {
        "schema": sdd.SCHEMA_VERSION,
        "key": "ABC-1",
        "preset": "solo-saas",
        "persona": "maintainer",
        "objective": "Fix the coupon ordering.",
        "evidence": [{"claim": "charge first", "file": "a.py", "line": 3, "quote": "createCharge()"}],
        "files": [{"path": "src/a.py", "change": "edit", "why": "the ordering lives here"}],
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

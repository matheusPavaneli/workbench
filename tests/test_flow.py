from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workbench import flow, gitctx, gitrun, prose, sdd
from workbench.errors import ConfigError, UsageError


def _git(args, cwd) -> None:
    subprocess.run(["git", *args], cwd=str(cwd), capture_output=True, text=True, check=False)


class Configured(unittest.TestCase):
    def test_source_and_validation_targets(self) -> None:
        loaded = flow.load(
            {"strategy": "cherry-pick", "source": "main", "validation": ["homolog"]}, Path(".")
        )
        self.assertEqual("main", loaded.source.branch)
        self.assertEqual(["homolog"], [t.branch for t in loaded.validation])
        self.assertEqual(["main", "homolog"], [t.branch for t in loaded.targets])

    def test_unknown_strategy_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            flow.load({"strategy": "gitflow-ish", "source": "main"}, Path("."))

    def test_source_is_required(self) -> None:
        with self.assertRaises(ConfigError):
            flow.load({"strategy": "trunk"}, Path("."))

    def test_asking_for_a_branch_outside_the_flow_lists_the_targets(self) -> None:
        loaded = flow.load({"source": "main", "validation": ["homolog"]}, Path("."))
        with self.assertRaises(UsageError) as caught:
            loaded.target("production")
        self.assertIn("homolog", caught.exception.render())


class BranchNames(unittest.TestCase):
    def test_pattern_is_applied(self) -> None:
        loaded = flow.load({"source": "main", "branch_pattern": "feature/{key}-{slug}"}, Path("."))
        name = flow.branch_name(loaded, "ABC-123", "Checkout fails when the coupon is expired")
        self.assertEqual("feature/ABC-123-checkout-fails-when-the-coupon", name)

    def test_slug_is_cut_on_a_word_boundary(self) -> None:
        self.assertNotIn("--", flow.slugify("Checkout fails -- badly -- on expiry"))
        self.assertLessEqual(len(flow.slugify("x" * 100)), flow.SLUG_MAX)

    def test_pattern_must_contain_the_key(self) -> None:
        with self.assertRaises(UsageError):
            flow.validate_pattern("feature/{slug}")

    def test_unknown_placeholder_is_refused(self) -> None:
        with self.assertRaises(UsageError) as caught:
            flow.validate_pattern("{key}-{author}")
        self.assertIn("author", caught.exception.render())

    def test_prefix_convention_is_read_off_existing_branches(self) -> None:
        branches = [
            "origin/main",
            "origin/feature/ABC-1-a",
            "origin/feature/ABC-2-b",
            "origin/feature/ABC-3-c",
        ]
        self.assertEqual("feature/{key}-{slug}", flow.detect_pattern(branches))

    def test_bare_key_convention_is_recognised(self) -> None:
        branches = ["origin/main", "origin/ABC-1-a", "origin/ABC-2-b", "origin/ABC-3-c"]
        self.assertEqual(flow.DEFAULT_PATTERN, flow.detect_pattern(branches))

    def test_too_few_branches_is_not_a_convention(self) -> None:
        self.assertIsNone(flow.detect_pattern(["origin/main", "origin/feature/ABC-1-a"]))


class CarryPlan(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        _git(["init", "-q", "-b", "main", "."], self.root)
        _git(["config", "user.email", "t@example.com"], self.root)
        _git(["config", "user.name", "T"], self.root)
        (self.root / "a.txt").write_text("one\n", encoding="utf-8")
        _git(["add", "-A"], self.root)
        _git(["commit", "-qm", "base"], self.root)

        _git(["switch", "-qc", "ABC-1-fix"], self.root)
        for step in ("two", "three"):
            (self.root / "a.txt").write_text(step + "\n", encoding="utf-8")
            _git(["add", "-A"], self.root)
            _git(["commit", "-qm", f"step {step}"], self.root)

    def test_commits_are_listed_oldest_first(self) -> None:
        commits = gitctx.commits_between(self.root, "main", "ABC-1-fix")
        self.assertEqual(2, len(commits))
        self.assertIn("step two", commits[0])
        self.assertIn("step three", commits[1])

    def test_nothing_to_carry_from_the_base_itself(self) -> None:
        self.assertEqual([], gitctx.commits_between(self.root, "main", "main"))

    def test_branch_existence_is_checked_not_assumed(self) -> None:
        self.assertTrue(gitctx.branch_exists(self.root, "ABC-1-fix"))
        self.assertFalse(gitctx.branch_exists(self.root, "ABC-9-nope"))


class StartThenPush(unittest.TestCase):
    """WB-23: a branch made from origin/<base> tracked origin/<base> under git's
    default autoSetupMerge, so the push guard refused every branch flow start
    had created. Run on a real clone: the bug lived in git's defaults, not ours."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        base = Path(self._tmp.name)
        origin, self.root = base / "origin.git", base / "clone"
        seed = base / "seed"
        seed.mkdir()
        _git(["init", "-q", "--bare", "-b", "main", str(origin)], base)
        _git(["init", "-q", "-b", "main", "."], seed)
        _git(["config", "user.email", "t@example.com"], seed)
        _git(["config", "user.name", "T"], seed)
        (seed / "a.txt").write_text("one\n", encoding="utf-8")
        _git(["add", "-A"], seed)
        _git(["commit", "-qm", "base"], seed)
        _git(["push", "-q", str(origin), "main", "main:homolog"], seed)
        _git(["clone", "-q", str(origin), str(self.root)], base)
        env = mock.patch.dict(os.environ)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop("WB_NO_EXECUTE", None)

    def _upstream(self) -> str:
        completed = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "@{u}"],
            cwd=str(self.root), capture_output=True, text=True, check=False,
        )
        return completed.stdout.strip() if completed.returncode == 0 else ""

    def _push_refusal(self) -> str | None:
        push = gitrun.Action(["push", "-u", "origin", "x"], precondition=gitrun.NO_UPSTREAM)
        return gitrun.precondition(push, self.root, [])

    def test_a_started_branch_can_be_pushed(self) -> None:
        run = gitrun.apply(flow.start_actions("ABC-1-fix", "main"), self.root)

        self.assertTrue(run.ok)
        self.assertEqual("ABC-1-fix", gitctx.branch(self.root))
        self.assertEqual("", self._upstream())
        self.assertIsNone(self._push_refusal())

    def test_a_carry_branch_can_be_pushed(self) -> None:
        switch = flow.carry_actions("ABC-1-homolog", "homolog", ["aaa1111 x"])[0]
        run = gitrun.apply([switch], self.root)

        self.assertTrue(run.ok)
        self.assertEqual("ABC-1-homolog", gitctx.branch(self.root))
        self.assertEqual("", self._upstream())
        self.assertIsNone(self._push_refusal())


class Prose(unittest.TestCase):
    def test_size_class_scales_with_the_change(self) -> None:
        self.assertEqual(prose.TRIVIAL, prose.size_class(["a.py"], insertions=3))
        self.assertEqual(prose.SMALL, prose.size_class(["a.py", "b.py", "c.py"], insertions=200))
        self.assertEqual(prose.LARGE, prose.size_class([f"f{i}.py" for i in range(20)]))

    def test_empty_section_is_reported(self) -> None:
        text = "## What\n\nA real change.\n\n## Why\n\n## How\n\nDetails.\n"
        self.assertEqual(["Why"], prose.empty_sections(text))

    def test_trailing_empty_section_is_reported(self) -> None:
        self.assertEqual(["Notes"], prose.empty_sections("## What\n\nx\n\n## Notes\n"))

    def test_ai_attribution_is_filler(self) -> None:
        problems = prose.check("## What\n\nFix.\n\nGenerated with [Claude Code]\n")
        self.assertTrue(any("filler" in p for p in problems))

    def test_unticked_checkbox_is_rejected(self) -> None:
        problems = prose.check("## What\n\nFix.\n\n- [ ] tested\n")
        self.assertTrue(any("checklist" in p for p in problems))

    def test_placeholder_is_rejected(self) -> None:
        self.assertTrue(any("placeholder" in p for p in prose.check("## What\n\nTBD\n")))

    def test_an_unfilled_slot_is_rejected(self) -> None:
        problems = prose.check("## What\n\nContact <YOUR NAME> about it.\n")
        self.assertTrue(any("placeholder" in p for p in problems))

    def test_html_tags_and_generic_types_are_not_placeholders(self) -> None:
        """`<.*?>` matched every angle bracket, so `List<String>` read as unfilled."""
        self.assertEqual([], prose.check("## What\n\nHandles List<String> and <br> fine.\n"))

    def test_the_word_todo_inside_a_sentence_is_not_a_placeholder(self) -> None:
        self.assertEqual([], prose.check("## What\n\nRemoved the old TODO comment.\n"))

    def test_headings_on_a_trivial_change_are_rejected(self) -> None:
        problems = prose.check("## What\n\nOne-line fix.\n", expected_shape=prose.TRIVIAL)
        self.assertTrue(any("trivial" in p for p in problems))

    def test_a_clean_description_passes(self) -> None:
        text = "## What\n\nValidate the coupon before charging.\n\n## Verification\n\n`pytest -q` passed.\n"
        self.assertEqual([], prose.check(text))


class Handover(unittest.TestCase):
    def _doc(self, **overrides) -> dict:
        doc = {
            "schema": 1,
            "key": "ABC-1",
            "preset": "scaleup",
            "persona": "fullstack-specialist",
            "ticket_type": "Bug",
            "objective": "Validate before charging.",
            "evidence": [{"claim": "c", "file": "a.py", "line": 1, "quote": "x"}],
            "files": [{"path": "a.py", "change": "edit", "why": "y"}],
            "zones": {},
            "steps": [{"do": "x"}],
            "tests": [{"kind": "regression", "target": "t", "asserts": "a"}],
            "verify": ["pytest -q"],
            "rollback": "revert",
            "product": {},
            "handover": {},
            "questions": [],
        }
        doc.update(overrides)
        return doc

    def test_a_bug_ticket_requires_the_plain_language_answers(self) -> None:
        problems = sdd.validate(self._doc())
        self.assertTrue(any("symptom_plain" in p for p in problems))
        self.assertTrue(any("qa_steps" in p for p in problems))

    def test_a_feature_ticket_does_not(self) -> None:
        self.assertEqual([], sdd.validate(self._doc(ticket_type="Story")))

    def test_the_requirement_can_be_forced_on(self) -> None:
        problems = sdd.validate(self._doc(ticket_type="Story", handover_required=True))
        self.assertTrue(problems)

    def test_the_requirement_can_be_forced_off(self) -> None:
        self.assertEqual([], sdd.validate(self._doc(handover_required=False)))

    def test_a_filled_handover_passes(self) -> None:
        doc = self._doc(
            handover={
                "symptom_plain": "The order failed with an error page.",
                "qa_steps": ["Apply an expired coupon", "The order is refused, no charge appears"],
            }
        )
        self.assertEqual([], sdd.validate(doc))

    def test_qa_steps_must_be_a_list(self) -> None:
        doc = self._doc(handover={"symptom_plain": "x", "qa_steps": "do the thing"})
        self.assertTrue(any("list of steps" in p for p in sdd.validate(doc)))

    def test_the_rendered_note_carries_no_code_detail(self) -> None:
        doc = self._doc(
            handover={
                "symptom_plain": "The order failed with an error page.",
                "cause_plain": "The discount was checked after the payment was taken.",
                "fix_plain": "Expired discounts are refused before any payment.",
                "qa_steps": ["Apply an expired coupon", "The order is refused"],
            }
        )
        text = sdd.render_handover(doc)
        self.assertIn("How to confirm it", text)
        self.assertNotIn("a.py", text)
        self.assertNotIn("pytest", text)

    def test_an_incident_handover_is_pending_not_a_problem(self) -> None:
        doc = self._doc(key="incident-checkout-500s", ticket_type="incident")
        self.assertEqual([], sdd.validate(doc))
        owed = sdd.pending(doc)
        self.assertTrue(any("symptom_plain" in p for p in owed), owed)
        self.assertTrue(any("qa_steps" in p for p in owed), owed)

    def test_a_bug_handover_is_still_a_problem_and_never_pending(self) -> None:
        doc = self._doc()
        self.assertTrue(any("symptom_plain" in p for p in sdd.validate(doc)))
        self.assertEqual([], sdd.pending(doc))

    def test_an_incident_key_owes_a_handover_without_a_ticket_type(self) -> None:
        doc = self._doc(key="incident-checkout-500s")
        doc.pop("ticket_type")
        self.assertTrue(sdd.needs_handover(doc))
        self.assertTrue(sdd.pending(doc))

    def test_an_incident_qa_steps_string_is_still_a_problem(self) -> None:
        doc = self._doc(key="incident-x", handover={"symptom_plain": "x", "qa_steps": "do the thing"})
        self.assertTrue(any("list of steps" in p for p in sdd.validate(doc)))

    def test_an_incident_forced_off_owes_nothing(self) -> None:
        doc = self._doc(key="incident-x", handover_required=False)
        self.assertEqual([], sdd.pending(doc))
        self.assertEqual([], sdd.validate(doc))

    def test_a_filled_incident_handover_is_not_pending(self) -> None:
        doc = self._doc(
            key="incident-x",
            handover={"symptom_plain": "Checkout failed.", "qa_steps": ["Pay", "The order goes through"]},
        )
        self.assertEqual([], sdd.pending(doc))


if __name__ == "__main__":
    unittest.main()

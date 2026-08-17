import unittest

from workbench import commitmsg, redact, review


class Detection(unittest.TestCase):
    def test_conventional_history_is_recognised(self) -> None:
        subjects = ["feat: add coupons", "fix(billing): expiry check", "chore: bump deps", "docs: readme"]
        self.assertEqual("conventional", commitmsg.detect(subjects).style)

    def test_ticket_prefixed_history_is_recognised(self) -> None:
        subjects = ["ABC-1: add coupons", "ABC-2: fix expiry", "ABC-3: bump deps", "ABC-4 tidy"]
        convention = commitmsg.detect(subjects)
        self.assertEqual("ticket-prefixed", convention.style)
        self.assertTrue(convention.ticket_prefix)

    def test_mixed_history_is_free_form(self) -> None:
        subjects = ["feat: a", "fixed the thing", "more work", "tweaks", "update"]
        self.assertEqual("free-form", commitmsg.detect(subjects).style)

    def test_empty_history_does_not_invent_a_style(self) -> None:
        convention = commitmsg.detect([])
        self.assertEqual("free-form", convention.style)
        self.assertEqual(0, convention.sample)

    def test_a_single_matching_commit_is_not_a_house_style(self) -> None:
        subjects = ["feat: a", "b", "c", "d", "e"]
        self.assertEqual("free-form", commitmsg.detect(subjects).style)


class Checking(unittest.TestCase):
    def setUp(self) -> None:
        self.conventional = commitmsg.detect(["feat: a", "fix: b", "chore: c", "docs: d"])
        self.free = commitmsg.detect(["did a thing", "another", "more", "again"])

    def test_a_good_conventional_message_passes(self) -> None:
        self.assertEqual([], commitmsg.check("fix(billing): validate coupon before charging", self.conventional))

    def test_wrong_style_is_reported(self) -> None:
        problems = commitmsg.check("validate coupon before charging", self.conventional)
        self.assertTrue(any("conventional commits" in p for p in problems))

    def test_overlong_subject_is_reported(self) -> None:
        problems = commitmsg.check("x" * 90, self.free)
        self.assertTrue(any("under 72" in p for p in problems))

    def test_trailing_period_is_reported(self) -> None:
        self.assertTrue(any("period" in p for p in commitmsg.check("do the thing.", self.free)))

    def test_missing_blank_line_after_subject_is_reported(self) -> None:
        problems = commitmsg.check("do the thing\nbody starts here", self.free)
        self.assertTrue(any("line 2 must be blank" in p for p in problems))

    def test_wip_markers_are_rejected(self) -> None:
        for subject in ("wip: half done", "fixup! earlier commit", "squash! earlier"):
            with self.subTest(subject=subject):
                self.assertTrue(any("unfinished" in p for p in commitmsg.check(subject, self.free)))

    def test_empty_subject_is_rejected(self) -> None:
        self.assertTrue(commitmsg.check("\n\nbody only", self.free))

    def test_a_credential_in_the_message_is_rejected(self) -> None:
        redact.reset()
        problems = commitmsg.check("fix auth\n\napi_key=abcdef123456 was rotated", self.free)
        self.assertTrue(any("credential" in p for p in problems))

    def test_missing_ticket_key_is_reported_when_the_repo_uses_them(self) -> None:
        ticketed = commitmsg.detect(["ABC-1: a", "ABC-2: b", "ABC-3: c", "ABC-4: d"])
        problems = commitmsg.check("ABC-9: do the thing", ticketed, key="ABC-9")
        self.assertEqual([], problems)
        problems = commitmsg.check("XYZ-1: do the thing", ticketed, key="ABC-9")
        self.assertTrue(any("ABC-9" in p for p in problems))


class TestPairing(unittest.TestCase):
    def test_source_with_a_matching_test_is_not_flagged(self) -> None:
        self.assertEqual([], review.untested(["src/checkout.py", "tests/test_checkout.py"]))

    def test_source_without_any_test_change_is_flagged(self) -> None:
        self.assertEqual(["src/checkout.py"], review.untested(["src/checkout.py", "README.md"]))

    def test_a_short_name_is_not_covered_by_an_unrelated_test(self) -> None:
        """Substring matching made "id" look covered by test_validator.py,
        because "id" is inside "validator"."""
        self.assertEqual(["src/id.py"], review.untested(["src/id.py", "tests/test_validator.py"]))
        self.assertEqual(["src/a.py"], review.untested(["src/a.py", "tests/test_charge.py"]))

    def test_a_matching_test_still_counts_across_naming_styles(self) -> None:
        for test_path in ("tests/test_checkout.py", "tests/checkout_test.py", "src/checkout.spec.ts"):
            with self.subTest(test_path=test_path):
                self.assertEqual([], review.untested(["src/checkout.py", test_path]))

    def test_non_code_files_are_not_expected_to_have_tests(self) -> None:
        self.assertEqual([], review.untested(["README.md", "docs/guide.md", "config.yaml"]))

    def test_test_files_are_recognised_across_conventions(self) -> None:
        for path in (
            "tests/test_a.py",
            "src/__tests__/a.test.ts",
            "spec/a_spec.rb",
            "src/a.spec.ts",
        ):
            with self.subTest(path=path):
                self.assertTrue(review.is_test(path))

    def test_a_source_file_is_not_mistaken_for_a_test(self) -> None:
        self.assertFalse(review.is_test("src/billing/latest.py"))
        self.assertTrue(review.is_source("src/billing/latest.py"))


if __name__ == "__main__":
    unittest.main()

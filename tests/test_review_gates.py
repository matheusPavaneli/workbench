"""Gates a script can settle.

The floor says "no secret in a committed file" and "no error swallowed
silently". Those are claims about bytes, and a model grading itself against
them passes whenever it is confident. These tests are mostly about the other
direction: a check that cries wolf gets ignored, which is worse than no check.
"""

import unittest

from workbench import review


def _added(path: str, body: str):
    return [(path, index, line) for index, line in enumerate(body.strip("\n").splitlines(), start=1)]


def _gates(path: str, body: str):
    return review.gate_findings(_added(path, body))


class Secrets(unittest.TestCase):
    def test_a_literal_token_is_a_high_finding(self) -> None:
        findings = _gates("src/a.py", 'API_KEY = "abcd1234abcd1234abcd"')
        self.assertEqual(1, len(findings))
        self.assertEqual(review.HIGH, findings[0].severity)

    def test_an_aws_key_is_recognised_by_shape(self) -> None:
        self.assertTrue(_gates("src/a.py", "AKIAIOSFODNN7EXAMPLE"))

    def test_a_private_key_header_is_recognised(self) -> None:
        self.assertTrue(_gates("src/a.py", "-----BEGIN RSA PRIVATE KEY-----"))

    def test_an_environment_lookup_is_the_correct_shape_not_a_leak(self) -> None:
        self.assertEqual([], _gates("src/a.py", 'API_KEY = os.environ["API_KEY"]'))

    def test_a_placeholder_is_not_a_leak(self) -> None:
        self.assertEqual([], _gates("src/a.py", 'password = "changeme-placeholder"'))

    def test_a_template_variable_is_not_a_leak(self) -> None:
        self.assertEqual([], _gates("config.yml", 'api_key: "${VAULT_API_KEY}"'))

    def test_a_short_value_is_not_a_credential(self) -> None:
        """A pattern that fires on every form field teaches people to skip it."""
        self.assertEqual([], _gates("src/a.py", 'password = "abc"'))

    def test_a_fixture_credential_in_a_test_is_the_point_of_the_test(self) -> None:
        self.assertEqual([], _gates("tests/test_auth.py", 'TOKEN = "abcd1234abcd1234abcd"'))


class SwallowedErrors(unittest.TestCase):
    def test_the_single_line_form(self) -> None:
        findings = _gates("src/a.py", "    except ValueError: pass")
        self.assertEqual(1, len(findings))
        self.assertEqual(review.MEDIUM, findings[0].severity)

    def test_the_multi_line_form_is_the_one_people_write(self) -> None:
        body = """
def f():
    try:
        g()
    except ValueError:
        pass
"""
        findings = _gates("src/a.py", body)
        self.assertEqual(1, len(findings))
        self.assertEqual(4, findings[0].line)  # reported at the except, not at the pass

    def test_a_handled_error_is_not_a_finding(self) -> None:
        body = """
    try:
        g()
    except OSError:
        log.warning("kept", exc_info=True)
"""
        self.assertEqual([], _gates("src/a.py", body))

    def test_an_empty_catch_block(self) -> None:
        self.assertTrue(_gates("src/a.ts", "} catch (e) {\n}"))

    def test_a_catch_block_that_reports_is_not_a_finding(self) -> None:
        self.assertEqual([], _gates("src/a.ts", "} catch (e) {\n  report(e);\n}"))

    def test_an_empty_promise_catch(self) -> None:
        self.assertTrue(_gates("src/a.ts", "load().catch(() => {})"))

    def test_an_opener_whose_body_is_not_in_the_diff_is_not_judged(self) -> None:
        """Only the following *added* line counts; unchanged context is unknown."""
        self.assertEqual([], _gates("src/a.py", "    except ValueError:"))

    def test_a_non_source_file_is_not_scanned_for_control_flow(self) -> None:
        self.assertEqual([], _gates("README.md", "    except ValueError:\n    pass"))


class RegressionTest(unittest.TestCase):
    def test_a_bug_fix_with_no_test_in_the_diff_fails_the_floor(self) -> None:
        self.assertTrue(review.regression_test_missing("bug", ["src/a.py"]))

    def test_a_bug_fix_with_a_test_passes(self) -> None:
        self.assertFalse(review.regression_test_missing("bug", ["src/a.py", "tests/test_a.py"]))

    def test_a_feature_is_not_held_to_the_regression_rule(self) -> None:
        self.assertFalse(review.regression_test_missing("feature", ["src/a.py"]))

    def test_an_unknown_ticket_type_is_not_held_to_it_either(self) -> None:
        self.assertFalse(review.regression_test_missing("", ["src/a.py"]))


class Reporting(unittest.TestCase):
    def test_a_finding_carries_the_line_it_was_found_on(self) -> None:
        finding = _gates("src/a.py", 'x = 1\nAPI_KEY = "abcd1234abcd1234abcd"')[0]
        self.assertEqual(2, finding.line)
        self.assertEqual("src/a.py", finding.file)

    def test_a_finding_names_the_gate_it_violates(self) -> None:
        finding = _gates("src/a.py", 'API_KEY = "abcd1234abcd1234abcd"')[0]
        self.assertIn("secret", finding.gate)


if __name__ == "__main__":
    unittest.main()

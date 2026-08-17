import unittest

from workbench import redact


class RegisteredValues(unittest.TestCase):
    def setUp(self) -> None:
        redact.reset()

    def test_registered_secret_is_masked_anywhere(self) -> None:
        redact.register("s3cr3t-token-value")
        self.assertNotIn("s3cr3t", redact.scrub("failed with s3cr3t-token-value in body"))

    def test_short_values_are_not_registered(self) -> None:
        redact.register("abc")
        self.assertIn("abc", redact.scrub("abc appears in ordinary text"))


class Patterns(unittest.TestCase):
    def setUp(self) -> None:
        redact.reset()

    def test_authorization_header(self) -> None:
        scrubbed = redact.scrub("Authorization: Basic dXNlcjpwYXNzd29yZDEyMzQ1Ng==")
        self.assertIn("Authorization:", scrubbed)
        self.assertNotIn("dXNlcjpwYXNz", scrubbed)

    def test_atlassian_token(self) -> None:
        self.assertNotIn("ATATT", redact.scrub("token ATATT3xFfGF0abcdefghijklmnop"))

    def test_azure_pat_shape(self) -> None:
        pat = "a" * 52
        self.assertNotIn(pat, redact.scrub(f"pat is {pat} here"))

    def test_key_value_pairs_keep_the_key(self) -> None:
        scrubbed = redact.scrub("api_key=abcdef123456")
        self.assertIn("api_key=", scrubbed)
        self.assertNotIn("abcdef123456", scrubbed)

    def test_url_credentials_keep_the_host(self) -> None:
        scrubbed = redact.scrub("https://user:hunter2@acme.atlassian.net/rest")
        self.assertIn("acme.atlassian.net", scrubbed)
        self.assertNotIn("hunter2", scrubbed)

    def test_ordinary_text_survives(self) -> None:
        text = "fetched ABC-123 with 4 linked issues"
        self.assertEqual(text, redact.scrub(text))


if __name__ == "__main__":
    unittest.main()

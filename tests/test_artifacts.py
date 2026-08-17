import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from workbench import artifacts, gitctx, secrets
from workbench.errors import AuthError, ConfigError, NotFoundError, UsageError


class Keys(unittest.TestCase):
    def test_accepts_jira_and_azure_shapes(self) -> None:
        self.assertEqual("ABC-123", artifacts.validate_key("abc-123"))
        self.assertEqual("4821", artifacts.validate_key("4821"))

    def test_rejects_path_traversal(self) -> None:
        for bad in ("../etc", "ABC-123/../..", "", "ABC 123"):
            with self.subTest(bad=bad), self.assertRaises(UsageError):
                artifacts.validate_key(bad)

    def test_accepts_slugs_for_work_that_has_no_ticket_yet(self) -> None:
        self.assertEqual("idea-coupon-limits", artifacts.validate_key("idea-coupon-limits"))
        self.assertEqual("incident-checkout-500", artifacts.validate_key("Incident-Checkout-500"))

    def test_slugs_cannot_escape_the_workflow_directory(self) -> None:
        for bad in ("idea-../etc", "incident-", "idea-" + "x" * 60, "idea-a/b", "notes-thing"):
            with self.subTest(bad=bad), self.assertRaises(UsageError):
                artifacts.validate_key(bad)


class Storage(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(gitctx, "repo_root", return_value=self.repo)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_round_trip(self) -> None:
        artifacts.write_json("ABC-1", "triage.json", {"key": "ABC-1"}, cwd=self.repo)
        self.assertEqual({"key": "ABC-1"}, artifacts.read_json("ABC-1", "triage.json", cwd=self.repo))

    def test_missing_artifact_names_the_producer_step(self) -> None:
        with self.assertRaises(NotFoundError) as caught:
            artifacts.read_json("ABC-1", "sdd.json", cwd=self.repo)
        self.assertIn("sdd.json", caught.exception.render())

    def test_write_leaves_no_temp_files_behind(self) -> None:
        artifacts.write_text("ABC-1", "pr.md", "body", cwd=self.repo)
        entries = os.listdir(self.repo / ".workflow" / "ABC-1")
        self.assertEqual(["pr.md"], entries)

    def test_cache_miss_when_stale(self) -> None:
        artifacts.cache_put("ABC-1", "issue.json", {"v": 1}, cwd=self.repo)
        self.assertEqual({"v": 1}, artifacts.cache_get("ABC-1", "issue.json", cwd=self.repo))

        path = self.repo / ".workflow" / "ABC-1" / ".cache" / "issue.json"
        stale = time.time() - artifacts.CACHE_TTL_SECONDS - 10
        os.utime(path, (stale, stale))
        self.assertIsNone(artifacts.cache_get("ABC-1", "issue.json", cwd=self.repo))

    def test_corrupt_cache_is_a_miss_not_a_crash(self) -> None:
        artifacts.cache_put("ABC-1", "issue.json", {"v": 1}, cwd=self.repo)
        path = self.repo / ".workflow" / "ABC-1" / ".cache" / "issue.json"
        path.write_text("{not json", encoding="utf-8")
        self.assertIsNone(artifacts.cache_get("ABC-1", "issue.json", cwd=self.repo))


class Secrets(unittest.TestCase):
    def test_missing_env_var_says_which_one(self) -> None:
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(AuthError) as caught:
                secrets.resolve({"pat_env": "JIRA_TOKEN_ACME"}, "work")
        self.assertIn("JIRA_TOKEN_ACME", caught.exception.render())

    def test_no_reference_at_all_is_a_config_error(self) -> None:
        with self.assertRaises(ConfigError):
            secrets.resolve({}, "work")

    def test_both_sources_is_rejected(self) -> None:
        with self.assertRaises(ConfigError):
            secrets.resolve({"pat_env": "A", "pat_keychain": "b"}, "work")

    def test_resolved_secret_is_registered_for_redaction(self) -> None:
        from workbench import redact

        redact.reset()
        with mock.patch.dict(os.environ, {"JIRA_TOKEN": "token-value-long-enough"}, clear=False):
            secrets.resolve({"pat_env": "JIRA_TOKEN"}, "work")
        self.assertNotIn("token-value-long-enough", redact.scrub("leaked token-value-long-enough"))


if __name__ == "__main__":
    unittest.main()

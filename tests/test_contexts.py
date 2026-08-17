import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workbench import contexts, gitctx
from workbench.errors import ConfigError, UsageError


def _context(**overrides):
    data = {
        "provider": "jira",
        "base_url": "https://acme.atlassian.net",
        "project": "ABC",
        "preset": "startup",
        "auth": {"pat_env": "JIRA_TOKEN", "email": "dev@acme.com"},
    }
    data.update(overrides)
    return data


class ContextsBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.home = Path(self._tmp.name) / "home"
        (self.home / "contexts").mkdir(parents=True)
        self.repo = Path(self._tmp.name) / "repo"
        self.repo.mkdir()
        self._env = mock.patch.dict(os.environ, {"WORKBENCH_HOME": str(self.home)}, clear=False)
        self._env.start()
        os.environ.pop("WORKBENCH_CONTEXT", None)
        self.addCleanup(self._env.stop)
        self.addCleanup(self._tmp.cleanup)

    def write_context(self, name: str, **overrides) -> None:
        path = self.home / "contexts" / f"{name}.json"
        path.write_text(json.dumps(_context(**overrides)), encoding="utf-8")

    def write_rules(self, rules: list) -> None:
        (self.home / "rules.json").write_text(json.dumps(rules), encoding="utf-8")


class Loading(ContextsBase):
    def test_rejects_unknown_provider(self) -> None:
        self.write_context("work", provider="trello")
        with self.assertRaises(UsageError) as caught:
            contexts.load("work")
        self.assertIn("jira", caught.exception.render())

    def test_rejects_unknown_preset(self) -> None:
        self.write_context("work", preset="webscale")
        with self.assertRaises(UsageError):
            contexts.load("work")

    def test_rejects_inline_credentials(self) -> None:
        path = self.home / "contexts" / "work.json"
        data = _context()
        data["token"] = "should-not-be-here"
        path.write_text(json.dumps(data), encoding="utf-8")
        with self.assertRaises(ConfigError) as caught:
            contexts.load("work")
        self.assertIn("credential key", caught.exception.message)

    def test_missing_context_lists_known_ones(self) -> None:
        self.write_context("personal")
        with self.assertRaises(ConfigError) as caught:
            contexts.load("work")
        self.assertIn("personal", caught.exception.render())


class ResolutionLadder(ContextsBase):
    def test_repo_config_wins_over_everything(self) -> None:
        self.write_context("work")
        self.write_context("personal")
        os.environ["WORKBENCH_CONTEXT"] = "personal"
        self.addCleanup(os.environ.pop, "WORKBENCH_CONTEXT", None)
        config = self.repo / ".workflow" / "config.json"
        config.parent.mkdir(parents=True)
        config.write_text(json.dumps({"context": "work", "preset": "enterprise"}), encoding="utf-8")

        with mock.patch.object(gitctx, "repo_root", return_value=self.repo):
            resolution = contexts.resolve(self.repo)

        self.assertEqual("work", resolution.context.name)
        self.assertEqual("enterprise", resolution.context.preset)  # repo override applied
        self.assertIn("repo config", resolution.source)

    def test_env_wins_over_rules(self) -> None:
        self.write_context("work")
        self.write_context("personal")
        self.write_rules([{"match": {"path_prefix": str(self.repo)}, "context": "work"}])
        os.environ["WORKBENCH_CONTEXT"] = "personal"
        self.addCleanup(os.environ.pop, "WORKBENCH_CONTEXT", None)

        with mock.patch.object(gitctx, "repo_root", return_value=None):
            resolution = contexts.resolve(self.repo)

        self.assertEqual("personal", resolution.context.name)
        self.assertEqual("WORKBENCH_CONTEXT", resolution.source)

    def test_remote_rule_beats_path_rule_regardless_of_order(self) -> None:
        self.write_context("work")
        self.write_context("personal")
        self.write_rules(
            [
                {"match": {"path_prefix": str(self.repo)}, "context": "personal"},
                {"match": {"remote_host": "dev.azure.com", "org": "acme"}, "context": "work"},
            ]
        )
        remote = gitctx.Remote(url="x", host="dev.azure.com", org="acme")

        with mock.patch.object(gitctx, "repo_root", return_value=None), mock.patch.object(
            gitctx, "origin", return_value=remote
        ):
            resolution = contexts.resolve(self.repo)

        self.assertEqual("work", resolution.context.name)

    def test_no_match_stops_and_lists_options(self) -> None:
        self.write_context("personal")
        with mock.patch.object(gitctx, "repo_root", return_value=None), mock.patch.object(
            gitctx, "origin", return_value=None
        ):
            with self.assertRaises(ConfigError) as caught:
                contexts.resolve(self.repo)

        rendered = caught.exception.render()
        self.assertIn("personal", rendered)
        self.assertIn("wb ctx use", rendered)


class RemoteParsing(unittest.TestCase):
    def test_ssh_remote(self) -> None:
        remote = gitctx.parse_remote("git@github.com:acme/app.git")
        self.assertEqual(("github.com", "acme"), (remote.host, remote.org))

    def test_https_remote(self) -> None:
        remote = gitctx.parse_remote("https://github.com/Acme/app")
        self.assertEqual(("github.com", "acme"), (remote.host, remote.org))

    def test_azure_devops_remote(self) -> None:
        remote = gitctx.parse_remote("https://acme@dev.azure.com/acme/Platform/_git/app")
        self.assertEqual(("dev.azure.com", "acme"), (remote.host, remote.org))

    def test_unparseable_remote_is_none(self) -> None:
        self.assertIsNone(gitctx.parse_remote("not-a-remote"))


if __name__ == "__main__":
    unittest.main()

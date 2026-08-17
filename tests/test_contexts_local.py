"""Context resolution for providers that need less than a hosted tracker.

The requirement table is what lets a provider exist without a site URL or a
credential. It is also the thing most likely to be relaxed by accident, so the
tests that matter are the ones proving the hosted providers still demand what
they always demanded.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workbench import contexts
from workbench.errors import ConfigError


class Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

        self._home = os.environ.get("WORKBENCH_HOME")
        os.environ["WORKBENCH_HOME"] = str(self.root / "home")
        self.addCleanup(self._restore_home)

        patcher = mock.patch("workbench.gitctx.repo_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _restore_home(self) -> None:
        if self._home is None:
            os.environ.pop("WORKBENCH_HOME", None)
        else:
            os.environ["WORKBENCH_HOME"] = self._home

    def write_repo_config(self, data: dict) -> None:
        path = self.root / contexts.REPO_CONFIG
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")


class Requirements(unittest.TestCase):
    def test_a_hosted_tracker_still_needs_a_site_and_a_project(self) -> None:
        for provider in ("jira", "azure"):
            with self.subTest(provider=provider):
                self.assertEqual(("base_url", "project"), contexts.REQUIRED_FIELDS[provider])

    def test_github_and_local_need_neither(self) -> None:
        self.assertEqual((), contexts.REQUIRED_FIELDS["github"])
        self.assertEqual((), contexts.REQUIRED_FIELDS["local"])

    def test_every_provider_has_a_requirement_entry(self) -> None:
        """A provider missing from the table would raise a KeyError on load."""
        self.assertEqual(set(contexts.PROVIDERS), set(contexts.REQUIRED_FIELDS))


class InlineLocalConfig(Base):
    def test_a_local_backlog_can_be_defined_entirely_in_the_repo(self) -> None:
        """The zero-setup case: a clone works with nothing in ~/.workbench."""
        self.write_repo_config({"provider": "local", "preset": "solo-saas"})
        resolution = contexts.resolve(self.root)
        self.assertEqual("local", resolution.context.provider)
        self.assertEqual("solo-saas", resolution.context.preset)
        self.assertIn("inline", resolution.source)

    def test_the_default_base_url_is_filled_in(self) -> None:
        self.write_repo_config({"provider": "local"})
        self.assertEqual(contexts.DEFAULT_BASE_URL["local"], contexts.resolve(self.root).context.base_url)

    def test_flow_carries_through_from_the_repo_config(self) -> None:
        self.write_repo_config({"provider": "local", "flow": {"strategy": "trunk", "source": "main"}})
        self.assertEqual("trunk", contexts.resolve(self.root).context.flow["strategy"])

    def test_a_hosted_provider_may_not_be_defined_inline(self) -> None:
        """Only a provider with no credential is safe in a committable file."""
        self.write_repo_config({"provider": "jira", "base_url": "https://x.atlassian.net", "project": "ABC"})
        with self.assertRaises(ConfigError) as caught:
            contexts.resolve(self.root)
        self.assertIn("context", caught.exception.message)

    def test_a_credential_in_the_repo_config_is_still_refused(self) -> None:
        self.write_repo_config({"provider": "local", "token": "abcd1234abcd"})
        with self.assertRaises(ConfigError):
            contexts.resolve(self.root)

    def test_a_named_context_still_wins_over_an_inline_definition(self) -> None:
        home = Path(os.environ["WORKBENCH_HOME"]) / "contexts"
        home.mkdir(parents=True, exist_ok=True)
        (home / "named.json").write_text(json.dumps({"provider": "local", "preset": "prototype"}), encoding="utf-8")
        self.write_repo_config({"context": "named", "provider": "local", "preset": "enterprise"})
        self.assertEqual("named", contexts.resolve(self.root).context.name)


class Transport(Base):
    def test_a_local_context_skips_the_https_requirement(self) -> None:
        """There is no request to protect: the provider reads files."""
        self.write_repo_config({"provider": "local", "base_url": "file://somewhere"})
        self.assertEqual("file://somewhere", contexts.resolve(self.root).context.base_url)


if __name__ == "__main__":
    unittest.main()

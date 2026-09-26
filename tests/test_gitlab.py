"""GitLab provider mapping.

GitLab has what GitHub lacks -- typed issue links -- so a blocker here is
reported as one. Most of what is asserted is that the typed information
survives and that the untyped parts (labels, system notes) are not mistaken for
something stronger.
"""

from __future__ import annotations

import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import FakeGitlab, gitlab_context
from workbench import gitctx, schema
from workbench.errors import AuthError, ConfigError, UsageError
from workbench.providers.gitlab import GitlabProvider


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(gitctx, "repo_root", return_value=Path(self._tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.provider = FakeGitlab()


class Mapping(Case):
    def test_normalises_an_issue(self) -> None:
        task = self.provider.fetch_task("42")
        self.assertEqual("42", task.key)
        self.assertEqual("opened", task.status)
        self.assertEqual("gitlab", task.provider)
        self.assertEqual("Ana Ruiz", task.assignee)
        self.assertIn("expired coupon", task.desc)
        self.assertEqual("https://gitlab.com/acme/platform/widgets/-/issues/42", task.url)

    def test_a_scoped_type_label_is_read(self) -> None:
        self.assertEqual("bug", self.provider.fetch_task("42").type)

    def test_an_incident_comes_from_issue_type(self) -> None:
        issue = {"iid": 7, "title": "down", "state": "opened", "issue_type": "incident", "labels": []}
        self.assertEqual("incident", FakeGitlab(issue=issue, links=[]).fetch_task("7").type)

    def test_unrecognised_labels_are_reported_not_dropped(self) -> None:
        issue = {"iid": 7, "title": "t", "state": "opened", "labels": ["epic-ish"]}
        task = FakeGitlab(issue=issue, links=[]).fetch_task("7")
        self.assertEqual("issue", task.type)
        self.assertTrue(any("epic-ish" in entry for entry in task.unmapped))

    def test_the_project_path_is_url_encoded(self) -> None:
        self.provider.fetch_task("42")
        self.assertEqual("/projects/acme%2Fplatform%2Fwidgets/issues/42", self.provider.calls[0])


class Links(Case):
    def links(self) -> dict[str, str]:
        return {link.key: link.type for link in self.provider.fetch_task("42").linked}

    def test_typed_links_keep_their_meaning(self) -> None:
        links = self.links()
        self.assertEqual(schema.BLOCKED_BY, links["40"])
        self.assertEqual(schema.BLOCKS, links["50"])

    def test_relates_to_is_relates_and_another_project_keeps_its_path(self) -> None:
        self.assertEqual(schema.RELATES, self.links()["acme/billing#8"])

    def test_the_epic_is_the_parent(self) -> None:
        self.assertEqual(schema.PARENT, self.links()["&3"])

    def test_an_unknown_link_type_is_other_and_flagged(self) -> None:
        task = self.provider.fetch_task("42")
        self.assertEqual(schema.OTHER, {link.key: link.type for link in task.linked}["61"])
        self.assertIn("issue link type: mitigates", task.unmapped)

    def test_every_link_type_is_canonical(self) -> None:
        for link in self.provider.fetch_task("42").linked:
            self.assertIn(link.type, schema.LINK_TYPES)


class Notes(Case):
    def test_system_notes_are_not_comments(self) -> None:
        total, comments = self.provider.fetch_comments("42", None)
        self.assertEqual(3, total)
        self.assertNotIn("changed the description", [comment.text for comment in comments])

    def test_newest_first_and_capped(self) -> None:
        total, comments = self.provider.fetch_comments("42", 2)
        self.assertEqual(3, total)
        self.assertEqual(["2026-08-09", "2026-08-08"], [comment.when for comment in comments])

    def test_an_author_gitlab_no_longer_has_is_unknown(self) -> None:
        _, comments = self.provider.fetch_comments("42", None)
        self.assertEqual("unknown", comments[-1].author)

    def test_history_is_the_system_notes(self) -> None:
        lines = self.provider.fetch_history("42", 10)
        self.assertEqual(2, len(lines))
        self.assertEqual("2026-08-10 Ana Ruiz: changed the description", lines[0])

    def test_paging_is_capped(self) -> None:
        page = [{"id": n, "body": "x", "system": False, "created_at": "2026-01-01"} for n in range(100)]
        provider = FakeGitlab(notes=page)
        provider.get = lambda path, **query: (provider.record(path), page)[1]  # type: ignore[method-assign]
        provider.fetch_comments("42", None)
        self.assertEqual(3, len(provider.calls))


class Keys(Case):
    def test_a_non_numeric_key_is_refused_before_any_request(self) -> None:
        with self.assertRaises(UsageError):
            self.provider.fetch_task("ENG-42")
        self.assertEqual([], self.provider.calls)

    def test_a_hash_prefix_is_accepted(self) -> None:
        self.provider.fetch_updated("#42")
        self.assertTrue(self.provider.calls[0].endswith("/issues/42"))

    def test_descriptions_skip_other_projects_and_epics(self) -> None:
        result = self.provider.fetch_descriptions(["40", "acme/billing#8", "&3"])
        self.assertEqual({"40"}, set(result))


class Project(unittest.TestCase):
    def remote(self, url: str):
        return mock.patch.object(gitctx, "origin", return_value=gitctx.parse_remote(url))

    def test_the_context_project_wins(self) -> None:
        self.assertEqual("acme/widgets", GitlabProvider(gitlab_context(project="acme/widgets")).project)

    def test_a_nested_project_comes_from_the_remote(self) -> None:
        with self.remote("git@gitlab.com:acme/platform/widgets.git"):
            self.assertEqual("acme/platform/widgets", GitlabProvider(gitlab_context(project="")).project)

    def test_a_self_managed_remote_needs_a_matching_base_url(self) -> None:
        with self.remote("https://git.acme.io/team/widgets.git"):
            with self.assertRaises(ConfigError) as caught:
                _ = GitlabProvider(gitlab_context(project="")).project
            self.assertIn("https://git.acme.io", " ".join(caught.exception.fix))
            provider = GitlabProvider(gitlab_context(project="", base_url="https://git.acme.io"))
            self.assertEqual("team/widgets", provider.project)

    def test_a_project_without_a_group_is_refused(self) -> None:
        with self.assertRaises(ConfigError):
            _ = GitlabProvider(gitlab_context(project="widgets")).project


class Auth(unittest.TestCase):
    def test_glab_token_is_the_fallback_and_is_bearer(self) -> None:
        done = subprocess.CompletedProcess([], 0, stdout="glpat-abc\n", stderr="")
        with mock.patch("shutil.which", return_value="/bin/glab"), \
             mock.patch("subprocess.run", return_value=done) as ran:
            self.assertEqual("Bearer glpat-abc", GitlabProvider(gitlab_context()).auth)
        self.assertEqual(["glab", "config", "get", "token", "--host", "gitlab.com"], ran.call_args[0][0])

    def test_without_glab_the_error_names_both_ways(self) -> None:
        with mock.patch("shutil.which", return_value=None):
            with self.assertRaises(AuthError) as caught:
                _ = GitlabProvider(gitlab_context()).auth
        fix = " ".join(caught.exception.fix)
        self.assertIn("glab auth login", fix)
        self.assertIn("--pat-env GITLAB_TOKEN", fix)

    def test_a_configured_token_is_not_replaced_by_glab(self) -> None:
        provider = GitlabProvider(gitlab_context(auth={"pat_env": "T"}))
        with mock.patch("workbench.secrets.resolve", return_value="configured"), \
             mock.patch("workbench.providers.gitlab._glab_token") as glab:
            self.assertEqual("Bearer configured", provider.auth)
        glab.assert_not_called()


class Cache(Case):
    def test_a_second_read_revalidates_instead_of_fetching_links(self) -> None:
        self.provider.get_task("42", depth=0, requested=[])
        self.provider.calls.clear()
        self.provider.get_task("42", depth=0, requested=[])
        self.assertFalse(any(call.endswith("/links") for call in self.provider.calls))


if __name__ == "__main__":
    unittest.main()

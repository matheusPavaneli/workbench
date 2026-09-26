"""Linear provider mapping.

Linear has typed relations, so unlike GitHub most of what is asserted here is
that direction survives: a relation is stored once, on the issue that created
it, and which list it comes back in is what says who blocks whom.
"""

from __future__ import annotations

import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from support import FakeLinear, linear_context
from workbench import gitctx, schema
from workbench.errors import AuthError, NotFoundError, ProviderError, UsageError
from workbench.providers.linear import LinearProvider


class Case(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(gitctx, "repo_root", return_value=Path(self._tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)
        self.provider = FakeLinear()


class Mapping(Case):
    def test_normalises_an_issue(self) -> None:
        task = self.provider.fetch_task("ENG-42")
        self.assertEqual("ENG-42", task.key)
        self.assertEqual("In Progress", task.status)
        self.assertEqual("linear", task.provider)
        self.assertEqual("Ana Ruiz", task.assignee)
        self.assertEqual("2026-08-10T09:15:00.000Z", task.updated)
        self.assertIn("expired coupon", task.desc)
        self.assertTrue(task.url.startswith("https://linear.app/acme/issue/ENG-42"))

    def test_type_comes_from_labels_because_linear_has_none(self) -> None:
        self.assertEqual("bug", self.provider.fetch_task("ENG-42").type)

    def test_unrecognised_labels_are_reported_not_dropped(self) -> None:
        issue = {"data": {"issue": {"identifier": "ENG-1", "title": "t", "labels": {"nodes": [{"name": "Epic"}]}}}}
        task = FakeLinear(issue=issue).fetch_task("ENG-1")
        self.assertEqual("issue", task.type)
        self.assertTrue(any("Epic" in entry for entry in task.unmapped))

    def test_the_key_is_sent_as_a_variable_not_spliced_into_the_query(self) -> None:
        self.provider.fetch_task("eng-42")
        body = self.provider.bodies[-1]
        self.assertEqual({"id": "ENG-42"}, body["variables"])
        self.assertNotIn("ENG-42", body["query"])


class Relations(Case):
    def links(self) -> dict[str, str]:
        return {link.key: link.type for link in self.provider.fetch_task("ENG-42").linked}

    def test_this_issue_blocking_another_is_blocks(self) -> None:
        self.assertEqual(schema.BLOCKS, self.links()["ENG-50"])

    def test_an_inverse_blocks_relation_is_blocked_by(self) -> None:
        self.assertEqual(schema.BLOCKED_BY, self.links()["ENG-40"])

    def test_duplicates_keep_their_direction(self) -> None:
        links = self.links()
        self.assertEqual(schema.DUPLICATES, links["ENG-7"])
        self.assertEqual(schema.DUPLICATED_BY, links["ENG-60"])

    def test_related_is_relates(self) -> None:
        self.assertEqual(schema.RELATES, self.links()["OPS-3"])

    def test_parent_and_sub_issues_are_hierarchy(self) -> None:
        links = self.links()
        self.assertEqual(schema.PARENT, links["ENG-12"])
        self.assertEqual(schema.CHILD, links["ENG-43"])

    def test_an_unknown_relation_is_other_and_flagged_not_guessed(self) -> None:
        task = self.provider.fetch_task("ENG-42")
        self.assertEqual(schema.OTHER, {link.key: link.type for link in task.linked}["ENG-88"])
        self.assertIn("relation type: similar", task.unmapped)

    def test_every_link_type_is_canonical_and_carries_its_status(self) -> None:
        for link in self.provider.fetch_task("ENG-42").linked:
            self.assertIn(link.type, schema.LINK_TYPES)
        self.assertEqual("Done", {link.key: link.status for link in self.provider.fetch_task("ENG-42").linked}["ENG-40"])


class Comments(Case):
    def test_newest_first_with_the_total(self) -> None:
        total, comments = self.provider.fetch_comments("ENG-42", 2)
        self.assertEqual(3, total)
        self.assertEqual(["2026-08-09", "2026-08-08"], [comment.when for comment in comments])
        self.assertEqual("Ana Ruiz", comments[0].author)

    def test_an_author_linear_no_longer_has_is_unknown(self) -> None:
        _, comments = self.provider.fetch_comments("ENG-42", None)
        self.assertEqual("unknown", comments[-1].author)

    def test_paging_is_capped(self) -> None:
        page = {"data": {"issue": {"comments": {"nodes": [], "pageInfo": {"hasNextPage": True, "endCursor": "c"}}}}}
        provider = FakeLinear(comments=page)
        provider.fetch_comments("ENG-42", None)
        self.assertLessEqual(provider.calls.count("IssueComments"), 4)


class History(Case):
    def test_one_line_per_change_and_noise_skipped(self) -> None:
        lines = self.provider.fetch_history("ENG-42", 10)
        self.assertEqual(3, len(lines))
        self.assertEqual("2026-08-10 Ana Ruiz: state Todo -> In Progress", lines[0])
        self.assertIn("+Billing", lines[1])
        self.assertIn("assignee none -> Ana Ruiz", lines[2])


class Listing(Case):
    def test_four_columns_only(self) -> None:
        rows = self.provider.list_tasks(10)
        self.assertEqual({"key", "status", "title", "updated"}, set(rows[0]))
        self.assertEqual("ENG-42", rows[0]["key"])

    def test_open_issues_only_and_the_team_when_the_context_names_one(self) -> None:
        provider = FakeLinear(linear_context(project="eng"))
        provider.list_tasks(10)
        issue_filter = provider.bodies[-1]["variables"]["filter"]
        self.assertEqual({"nin": ["completed", "canceled"]}, issue_filter["state"]["type"])
        self.assertEqual({"key": {"eq": "ENG"}}, issue_filter["team"])


class Descriptions(Case):
    def test_one_request_for_all_and_a_missing_one_is_skipped(self) -> None:
        result = self.provider.fetch_descriptions(["ENG-12", "ENG-40", "ENG-404"])
        self.assertEqual(["IssueDescriptions"], self.provider.calls)
        self.assertEqual({"ENG-12", "ENG-40"}, set(result))


class Errors(Case):
    def test_a_rate_limit_in_the_body_is_one_clear_error(self) -> None:
        limited = {"errors": [{"message": "Rate limit exceeded", "extensions": {"code": "RATELIMITED"}}]}
        provider = FakeLinear(issue=limited)
        with self.assertRaises(ProviderError) as caught:
            provider.fetch_task("ENG-42")
        self.assertIn("rate limit", caught.exception.message.lower())
        self.assertEqual(["Issue"], provider.calls)

    def test_a_rate_limit_as_http_400_is_not_retried(self) -> None:
        provider = LinearProvider(linear_context())
        provider._auth = "lin_api_test"
        body = b'{"errors":[{"message":"Rate limit exceeded","extensions":{"code":"RATELIMITED"}}]}'
        error = urllib.error.HTTPError("https://api.linear.app/graphql", 400, "Bad Request", {}, None)
        error.read = lambda *_: body  # type: ignore[method-assign]
        with mock.patch("urllib.request.urlopen", side_effect=error) as opened, \
             mock.patch("time.sleep") as slept:
            with self.assertRaises(ProviderError) as caught:
                provider.fetch_task("ENG-42")
        self.assertIn("RATELIMITED", caught.exception.message)
        self.assertEqual(1, opened.call_count)
        slept.assert_not_called()

    def test_a_missing_issue_is_not_found(self) -> None:
        with self.assertRaises(NotFoundError):
            FakeLinear(issue={"data": {"issue": None}}).fetch_task("ENG-9")

    def test_an_authentication_error_is_an_auth_error(self) -> None:
        rejected = {"errors": [{"message": "Authentication required", "extensions": {"code": "AUTHENTICATION_ERROR"}}]}
        with self.assertRaises(AuthError):
            FakeLinear(issue=rejected).fetch_task("ENG-42")

    def test_a_malformed_key_is_refused_before_any_request(self) -> None:
        with self.assertRaises(UsageError):
            self.provider.fetch_task("42")
        self.assertEqual([], self.provider.calls)


class Transport(Case):
    def test_the_api_key_is_the_header_as_is(self) -> None:
        """A personal API key takes no Bearer prefix; that is for OAuth tokens."""
        self.assertEqual("lin_api_x", LinearProvider(linear_context())._build_auth("lin_api_x"))

    def test_a_second_read_revalidates_with_updated_at(self) -> None:
        self.provider.get_task("ENG-42", depth=0, requested=[])
        self.provider.calls.clear()
        self.provider.get_task("ENG-42", depth=0, requested=[])
        self.assertIn("IssueUpdated", self.provider.calls)
        self.assertNotIn("Issue", self.provider.calls)

    def test_probe_names_the_workspace(self) -> None:
        identity = self.provider.probe()
        self.assertEqual(("Ana Ruiz", "linear.app/acme"), (identity.account, identity.detail))


if __name__ == "__main__":
    unittest.main()

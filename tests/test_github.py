"""GitHub provider mapping.

GitHub is the loosest of the three trackers -- no issue type, no typed links,
no total on a comment list -- so most of what is asserted here is that the
provider does not paper over a gap by inventing something plausible.
"""

import unittest

from support import FakeGithub, github_context
from workbench import schema
from workbench.errors import ConfigError, UsageError


class Mapping(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = FakeGithub()

    def test_normalises_an_issue(self) -> None:
        task = self.provider.fetch_task("42")
        self.assertEqual("42", task.key)
        self.assertEqual("open", task.status)
        self.assertEqual("github", task.provider)
        self.assertEqual("ana", task.assignee)
        self.assertIn("expired coupon", task.desc)

    def test_type_comes_from_labels_because_github_has_none(self) -> None:
        self.assertEqual("bug", self.provider.fetch_task("42").type)

    def test_unrecognised_labels_are_reported_not_dropped(self) -> None:
        provider = FakeGithub(issue={"number": 5, "title": "t", "state": "open", "labels": [{"name": "epic"}]})
        task = provider.fetch_task("5")
        self.assertEqual("issue", task.type)
        self.assertTrue(any("epic" in entry for entry in task.unmapped))

    def test_body_references_become_relates_and_never_more(self) -> None:
        links = {link.key: link.type for link in self.provider.fetch_task("42").linked}
        self.assertEqual(schema.RELATES, links["17"])
        self.assertEqual(schema.RELATES, links["acme/billing#8"])

    def test_a_reference_inside_code_is_not_a_link(self) -> None:
        """`#999` is a colour or a comment far more often than an issue."""
        self.assertNotIn("999", {link.key for link in self.provider.fetch_task("42").linked})

    def test_parent_is_hierarchy_not_a_mention(self) -> None:
        links = {link.key: link.type for link in self.provider.fetch_task("42").linked}
        self.assertEqual(schema.PARENT, links["12"])

    def test_sub_issue_count_is_reported_without_a_second_request(self) -> None:
        before = len(self.provider.calls)
        children = [l for l in self.provider.fetch_task("42").linked if l.type == schema.CHILD]
        self.assertEqual(1, len(children))
        self.assertIn("1/3", children[0].status)
        self.assertEqual(1, len(self.provider.calls) - before)

    def test_closed_as_not_planned_is_not_the_same_as_fixed(self) -> None:
        provider = FakeGithub(
            issue={"number": 5, "title": "t", "state": "closed", "state_reason": "not_planned", "labels": []}
        )
        self.assertEqual("closed (not planned)", provider.fetch_task("5").status)


class Comments(unittest.TestCase):
    def test_newest_first_like_every_other_provider(self) -> None:
        total, comments = FakeGithub().fetch_comments("42", 5)
        self.assertEqual(2, total)
        self.assertEqual("bo", comments[0].author)

    def test_paging_terminates_on_a_short_page(self) -> None:
        provider = FakeGithub()
        provider.fetch_comments("42", None)
        self.assertEqual(1, sum(1 for call in provider.calls if call.endswith("/comments")))


class Listing(unittest.TestCase):
    def test_pull_requests_are_not_work_items(self) -> None:
        rows = FakeGithub().list_tasks(20)
        self.assertEqual(["42"], [row["key"] for row in rows])


class History(unittest.TestCase):
    def test_comment_events_are_dropped_because_comments_have_their_own_channel(self) -> None:
        lines = FakeGithub().fetch_history("42", 10)
        self.assertTrue(lines)
        self.assertFalse(any("commented" in line for line in lines))


class Repo(unittest.TestCase):
    def test_a_malformed_project_is_refused_not_guessed(self) -> None:
        provider = FakeGithub(github_context(project="widgets"))
        with self.assertRaises(ConfigError):
            _ = provider.repo

    def test_a_non_numeric_key_is_refused(self) -> None:
        with self.assertRaises(UsageError):
            FakeGithub().fetch_task("ABC-1")


class Capabilities(unittest.TestCase):
    def test_history_is_offered_because_github_has_a_timeline(self) -> None:
        self.assertTrue(FakeGithub().has_history)


if __name__ == "__main__":
    unittest.main()

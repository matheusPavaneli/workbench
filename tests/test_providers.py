import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from workbench import gitctx, schema
from workbench.errors import UsageError

from support import FakeAzure, FakeJira, jira_context


class ProviderTestCase(unittest.TestCase):
    """Artifacts are written under a throwaway repo root, not the real one."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(gitctx, "repo_root", return_value=Path(self._tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)


class JiraNormalisation(ProviderTestCase):
    def test_core_fields(self) -> None:
        task = FakeJira().fetch_task("ABC-123")
        self.assertEqual("ABC-123", task.key)
        self.assertEqual("In Review", task.status)
        self.assertEqual("Bug", task.type)
        self.assertEqual("Ana Ruiz", task.assignee)
        self.assertEqual("https://acme.atlassian.net/browse/ABC-123", task.url)

    def test_adf_becomes_plain_text(self) -> None:
        desc = FakeJira().fetch_task("ABC-123").desc
        self.assertIn("Applying an expired coupon returns a 500", desc)
        self.assertIn("- Only on the v2 endpoint", desc)
        self.assertIn("@Bo Lin", desc)
        self.assertIn("raise CouponError", desc)

    def test_unknown_adf_node_is_traversed_not_dropped(self) -> None:
        self.assertIn("still readable", FakeJira().fetch_task("ABC-123").desc)

    def test_link_direction_decides_the_type(self) -> None:
        links = {link.key: link.type for link in FakeJira().fetch_task("ABC-123").linked}
        self.assertEqual(schema.BLOCKED_BY, links["ABC-98"])   # arrived as inwardIssue
        self.assertEqual(schema.BLOCKS, links["ABC-150"])      # arrived as outwardIssue
        self.assertEqual(schema.RELATES, links["ABC-77"])

    def test_parent_and_subtasks_become_links(self) -> None:
        links = {link.key: link.type for link in FakeJira().fetch_task("ABC-123").linked}
        self.assertEqual(schema.PARENT, links["ABC-100"])
        self.assertEqual(schema.CHILD, links["ABC-124"])

    def test_unknown_link_type_is_flagged_not_guessed(self) -> None:
        task = FakeJira().fetch_task("ABC-123")
        links = {link.key: link.type for link in task.linked}
        self.assertEqual(schema.OTHER, links["ABC-201"])
        self.assertTrue(any("mitigates" in note for note in task.unmapped))

    def test_comment_total_survives_the_page_size(self) -> None:
        total, comments = FakeJira().fetch_comments("ABC-123", 5)
        self.assertEqual(42, total)
        self.assertEqual(2, len(comments))
        self.assertEqual("Bo Lin", comments[0].author)

    def test_project_is_escaped_into_the_query(self) -> None:
        provider = FakeJira(jira_context(project='ODD"NAME'))
        provider.list_tasks(20)
        self.assertIn('project = "ODD\\"NAME"', provider.last_jql)

    def test_list_returns_only_four_columns(self) -> None:
        rows = FakeJira().list_tasks(20)
        self.assertEqual({"key", "status", "title", "updated"}, set(rows[0]))


class AzureNormalisation(ProviderTestCase):
    def test_core_fields(self) -> None:
        task = FakeAzure().fetch_task("4821")
        self.assertEqual("4821", task.key)
        self.assertEqual("Active", task.status)
        self.assertEqual("Bug", task.type)
        self.assertEqual("Ana Ruiz", task.assignee)

    def test_repro_steps_are_used_when_description_is_absent(self) -> None:
        desc = FakeAzure().fetch_task("4821").desc
        self.assertIn("Applying an expired coupon returns a 500", desc)
        self.assertIn("- Only on the v2 endpoint", desc)

    def test_script_content_is_dropped(self) -> None:
        self.assertNotIn("ignored()", FakeAzure().fetch_task("4821").desc)

    def test_relation_suffix_decides_the_type(self) -> None:
        links = {link.key: link.type for link in FakeAzure().fetch_task("4821").linked}
        self.assertEqual(schema.PARENT, links["4800"])       # Hierarchy-Reverse
        self.assertEqual(schema.CHILD, links["4830"])        # Hierarchy-Forward
        self.assertEqual(schema.BLOCKED_BY, links["4700"])   # Dependency-Reverse
        self.assertEqual(schema.RELATES, links["4655"])

    def test_attachments_and_hyperlinks_are_not_links(self) -> None:
        task = FakeAzure().fetch_task("4821")
        self.assertNotIn("abc", {link.key for link in task.linked})
        self.assertFalse([note for note in task.unmapped if "attachedfile" in note.lower()])

    def test_unknown_relation_is_flagged_not_guessed(self) -> None:
        task = FakeAzure().fetch_task("4821")
        links = {link.key: link.type for link in task.linked}
        self.assertEqual(schema.OTHER, links["4999"])
        self.assertTrue(any("custom-forward" in note for note in task.unmapped))

    def test_markdown_comments_are_not_html_stripped(self) -> None:
        """`text` is markdown unless `format` says html -- stripping tags off
        markdown eats anything with an angle bracket in it."""
        _, comments = FakeAzure().fetch_comments("4821", 5)
        self.assertIn("qty <5", comments[0].text)

    def test_html_comments_use_the_rendered_text(self) -> None:
        _, comments = FakeAzure().fetch_comments("4821", 5)
        self.assertEqual("Root cause is in the validator, not the endpoint.", comments[1].text)

    def test_cross_organization_dependency_is_mapped(self) -> None:
        links = {link.key: link.type for link in FakeAzure().fetch_task("4821").linked}
        self.assertEqual(schema.BLOCKED_BY, links["7001"])

    def test_the_batch_call_asks_the_api_to_omit_bad_ids(self) -> None:
        """Without it, one deleted linked item fails the whole request."""
        from workbench.providers import azure as azure_module

        source = Path(azure_module.__file__).read_text(encoding="utf-8")
        self.assertIn('"errorPolicy": "Omit"', source)
        self.assertNotIn('"$errorPolicy"', source)  # the $ form is silently ignored

    def test_links_are_titled_from_the_batch_call(self) -> None:
        links = {link.key: link.title for link in FakeAzure().fetch_task("4821").linked}
        self.assertEqual("Coupon engine rewrite", links["4800"])

    def test_list_costs_exactly_two_calls(self) -> None:
        provider = FakeAzure()
        rows = provider.list_tasks(20)
        self.assertEqual({"key", "status", "title", "updated"}, set(rows[0]))
        self.assertEqual(2, len(provider.calls))  # WIQL returns ids only


class Parity(ProviderTestCase):
    """Same ticket, two trackers, one shape. A consumer must not be able to tell."""

    def test_payload_keys_match(self) -> None:
        jira = FakeJira().get_task("ABC-123", depth=1, requested=[])
        azure = FakeAzure().get_task("4821", depth=1, requested=[])
        ignore = {"_unmapped", "_truncated", "history"}
        self.assertEqual(set(jira) - ignore, set(azure) - ignore)
        self.assertEqual(set(jira["linked"][0]) - {"desc"}, set(azure["linked"][0]) - {"desc"})

    def test_link_types_come_from_the_canonical_set(self) -> None:
        for payload in (
            FakeJira().get_task("ABC-123", depth=1, requested=[]),
            FakeAzure().get_task("4821", depth=1, requested=[]),
        ):
            for link in payload["linked"]:
                self.assertIn(link["type"], schema.LINK_TYPES)


class DepthPolicy(ProviderTestCase):
    def test_depth_0_omits_links_entirely(self) -> None:
        payload = FakeJira().get_task("ABC-123", depth=0, requested=[])
        self.assertEqual([], payload["linked"])
        self.assertEqual(6, payload["linked_total"])  # parent + subtask + 4 issue links

    def test_depth_1_is_one_line_per_link(self) -> None:
        payload = FakeJira().get_task("ABC-123", depth=1, requested=[])
        self.assertTrue(payload["linked"])
        self.assertFalse(any("desc" in link for link in payload["linked"]))

    def test_depth_2_reads_bodies_of_blocking_and_hierarchy_links_only(self) -> None:
        descriptions = {
            "ABC-98": {"key": "ABC-98", "fields": {"description": _adf("blocker body")}},
            "ABC-77": {"key": "ABC-77", "fields": {"description": _adf("relates body")}},
        }
        provider = FakeJira(descriptions={"issues": list(descriptions.values())})
        payload = provider.get_task("ABC-123", depth=2, requested=[])
        bodies = {link["key"]: link.get("desc") for link in payload["linked"]}
        self.assertEqual("blocker body", bodies["ABC-98"])
        self.assertIsNone(bodies["ABC-77"])  # "relates" is never followed in depth

    def test_depth_out_of_range_is_rejected(self) -> None:
        with self.assertRaises(UsageError):
            FakeJira().get_task("ABC-123", depth=3, requested=[])


class ExpandHandles(ProviderTestCase):
    def test_offered_handles_reflect_what_exists(self) -> None:
        payload = FakeJira().get_task("ABC-123", depth=1, requested=[])
        self.assertIn("comments:all", payload["_expand"])   # 42 total, 2 shown
        self.assertIn("history", payload["_expand"])
        self.assertIn("linked:ABC-98:full", payload["_expand"])
        self.assertNotIn("linked:ABC-77:full", payload["_expand"])  # relates is not deep

    def test_invented_handle_is_rejected_with_the_valid_list(self) -> None:
        with self.assertRaises(UsageError) as caught:
            FakeJira().get_task("ABC-123", depth=1, requested=["comments:page2"])
        rendered = caught.exception.render()
        self.assertIn("comments:page2", rendered)
        self.assertIn("comments:all", rendered)

    def test_desc_full_restores_the_whole_description(self) -> None:
        long_text = "word " * 400
        issue = _issue_with_description(long_text)
        provider = FakeJira(issue=issue)

        capped = provider.get_task("ABC-1", depth=0, requested=[])
        self.assertIn("desc:full", capped["_expand"])
        self.assertLessEqual(len(capped["desc"]), schema.DESC_CHARS + 1)

        full = FakeJira(issue=issue).get_task("ABC-1", depth=0, requested=["desc:full"], use_cache=False)
        self.assertGreater(len(full["desc"]), schema.DESC_CHARS)
        self.assertNotIn("desc:full", full["_expand"])  # nothing left to expand


class Caps(ProviderTestCase):
    def test_long_description_is_capped_and_reported(self) -> None:
        provider = FakeJira(issue=_issue_with_description("word " * 400))
        payload = provider.get_task("ABC-1", depth=0, requested=[])
        self.assertLessEqual(len(payload["desc"]), schema.DESC_CHARS + 1)
        self.assertGreater(payload["desc_chars"], schema.DESC_CHARS)
        self.assertIn("desc", payload["_truncated"])

    def test_missing_comments_are_counted_not_hidden(self) -> None:
        payload = FakeJira().get_task("ABC-123", depth=1, requested=[])
        self.assertEqual(42, payload["comments"]["total"])
        self.assertEqual(2, len(payload["comments"]["recent"]))
        self.assertTrue(any("comments" in note for note in payload["_truncated"]))

    def test_payload_stays_under_the_hard_cap(self) -> None:
        import json

        provider = FakeJira(issue=_issue_with_description("word " * 4000))
        payload = provider.get_task("ABC-1", depth=1, requested=["desc:full"])
        size = len(json.dumps(payload).encode("utf-8"))
        # desc:full is an explicit request, so the description itself is kept;
        # everything optional around it is shed first.
        self.assertTrue(payload["_truncated"])
        self.assertLess(size, 40_000)

    def test_degradation_is_deterministic(self) -> None:
        payload = {
            "comments": {"total": 9, "recent": [{"text": "x" * 500, "author": "a", "when": "d"} for _ in range(5)]},
            "linked": [{"key": f"K-{i}", "type": "blocks", "status": "s", "title": "t", "desc": "y" * 400} for i in range(10)],
        }
        first = schema.fit(json.loads(json.dumps(payload)))
        second = schema.fit(json.loads(json.dumps(payload)))
        self.assertEqual(first, second)


def _full_fetches(provider) -> int:
    return len(
        [c for c in provider.calls if "/issue/ABC-123" in c and "fields=updated" not in c and not c.endswith("/comment")]
    )


class Caching(ProviderTestCase):
    def test_second_read_revalidates_instead_of_refetching(self) -> None:
        provider = FakeJira()
        provider.get_task("ABC-123", depth=0, requested=[])
        self.assertEqual(1, _full_fetches(provider))

        provider.get_task("ABC-123", depth=0, requested=[])
        self.assertEqual(1, _full_fetches(provider))  # heavy fetch avoided
        self.assertTrue(any("fields=updated" in c for c in provider.calls))  # freshness proven, not assumed

    def test_a_ticket_that_moved_is_refetched(self) -> None:
        provider = FakeJira()
        provider.get_task("ABC-123", depth=0, requested=[])

        moved = json.loads(json.dumps(provider._fixture("issue")))
        moved["fields"]["updated"] = "2026-08-20T10:00:00.000+0000"
        provider.fixtures["issue"] = moved

        provider.get_task("ABC-123", depth=0, requested=[])
        self.assertEqual(2, _full_fetches(provider))

    def test_no_cache_forces_a_refetch(self) -> None:
        provider = FakeJira()
        provider.get_task("ABC-123", depth=0, requested=[])
        provider.get_task("ABC-123", depth=0, requested=[], use_cache=False)
        self.assertEqual(2, _full_fetches(provider))


def _adf(text: str) -> dict:
    return {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": text}]}]}


def _issue_with_description(text: str) -> dict:
    return {
        "key": "ABC-1",
        "fields": {
            "summary": "Long one",
            "status": {"name": "To Do"},
            "issuetype": {"name": "Task"},
            "updated": "2026-08-14T09:12:33.000+0000",
            "description": _adf(text),
            "issuelinks": [],
        },
    }


if __name__ == "__main__":
    unittest.main()

"""Custom fields: reporting the ones being dropped, mapping the ones that matter.

The packaged fixtures cannot describe a tenant's custom fields, so this is the
half of the tenant problem that survives even with a recording: a field carrying
the acceptance criteria is absent from the normalised task, and nothing says so.

Two properties, and the second is the sharper one:

- a mapped field reaches the task, whatever container the tracker wrapped it in
- an *unmapped* field with content is reported rather than silently dropped
"""

import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import support  # noqa: E402
from workbench import fields  # noqa: E402


class Mapping(unittest.TestCase):
    def test_a_mapped_field_reaches_the_task(self) -> None:
        payload = {"customfield_10042": "refunds must be idempotent"}
        self.assertEqual(
            {"acceptance_criteria": "refunds must be idempotent"},
            fields.mapped(payload, {"customfield_10042": "acceptance_criteria"}),
        )

    def test_every_container_a_tracker_uses_is_flattened(self) -> None:
        """A reader that handles only bare strings sees an empty field on most
        tenants: a select is an object, a multi-select a list of them, and rich
        text is a document tree."""
        cases = {
            "plain": "text here",
            "select": {"value": "High"},
            "named": {"name": "Billing"},
            "multi": [{"value": "one"}, {"value": "two"}],
            "number": 42,
            "adf": {"type": "doc", "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "from a doc"}]}
            ]},
        }
        expected = {
            "plain": "text here", "select": "High", "named": "Billing",
            "multi": "one, two", "number": "42", "adf": "from a doc",
        }
        for name, raw in cases.items():
            with self.subTest(shape=name):
                got = fields.mapped({name: raw}, {name: "impact"})
                self.assertEqual(expected[name], got.get("impact", ""))

    def test_an_empty_field_is_not_carried(self) -> None:
        for empty in ("", "   ", None, [], {}):
            with self.subTest(value=empty):
                self.assertEqual({}, fields.mapped({"f": empty}, {"f": "impact"}))

    def test_an_unknown_destination_is_dropped_not_invented(self) -> None:
        """A typo in the config must not create a key no skill reads."""
        self.assertEqual({}, fields.mapped({"f": "x"}, {"f": "acceptance_critera"}))

    def test_validate_names_the_destinations_that_exist(self) -> None:
        problems = fields.validate({"customfield_1": "acceptance_critera"})
        self.assertEqual(1, len(problems))
        self.assertIn("acceptance_criteria", problems[0])

    def test_a_usable_mapping_validates_clean(self) -> None:
        self.assertEqual([], fields.validate({"customfield_1": "acceptance_criteria"}))
        self.assertEqual([], fields.validate(None))

    def test_a_mapping_that_is_not_an_object_is_rejected(self) -> None:
        self.assertTrue(fields.validate(["customfield_1"]))


class Reporting(unittest.TestCase):
    READ = {"summary", "status", "description"}

    def test_a_custom_field_with_content_is_reported(self) -> None:
        payload = {"summary": "x", "customfield_10042": "acceptance stuff"}
        self.assertIn("customfield_10042", fields.unread(payload, self.READ))

    def test_an_empty_custom_field_is_not(self) -> None:
        """Every tenant has hundreds. Reporting them is a dump, not a finding."""
        payload = {"customfield_1": None, "customfield_2": "", "customfield_3": []}
        self.assertEqual({}, fields.unread(payload, self.READ))

    def test_a_field_already_read_is_not_reported(self) -> None:
        self.assertEqual({}, fields.unread({"summary": "x"}, self.READ))

    def test_a_field_already_mapped_is_not_reported(self) -> None:
        payload = {"customfield_10042": "mapped already"}
        self.assertEqual({}, fields.unread(payload, self.READ, {"customfield_10042": "impact"}))

    def test_structural_noise_is_never_reported(self) -> None:
        payload = {"watches": {"watchCount": 2}, "workratio": -1, "votes": {"votes": 0}}
        self.assertEqual({}, fields.unread(payload, self.READ))

    def test_the_sample_is_capped(self) -> None:
        payload = {"customfield_1": "x" * 500}
        sample = fields.unread(payload, self.READ)["customfield_1"]
        self.assertLessEqual(len(sample), fields.MAX_SAMPLE + 1)


class ThroughTheProvider(unittest.TestCase):
    """The mapping has to survive the round trip, including the request itself."""

    def setUp(self) -> None:
        # get_task writes a cache under .workflow/<KEY>/. Without this the suite
        # leaves artifacts in whatever repo it was run from -- which it did.
        import tempfile

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch("workbench.gitctx.repo_root", return_value=Path(self._tmp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def _jira(self, field_map: dict, issue: dict | None = None):
        provider = support.FakeJira(**({"issue": issue} if issue else {}))
        provider.field_map = field_map
        return provider

    def test_a_mapped_field_lands_in_the_normalised_task(self) -> None:
        issue = json.loads(json.dumps(support.load("jira", "issue")))
        issue["fields"]["customfield_10042"] = "refunds are idempotent"
        provider = self._jira({"customfield_10042": "acceptance_criteria"}, issue)

        task = provider.fetch_task("ABC-123")
        self.assertEqual("refunds are idempotent", task.extra["acceptance_criteria"])

    def test_the_mapped_field_is_actually_requested(self) -> None:
        """Jira returns only the fields the query names, so a mapping missing
        from the request reads as an empty field rather than a broken mapping."""
        provider = self._jira({"customfield_10042": "impact"})
        captured = {}

        original = provider.get

        def spy(path, **query):
            captured.update(query)
            return original(path, **query)

        provider.get = spy
        provider.fetch_task("ABC-123")
        self.assertIn("customfield_10042", captured.get("fields", ""))

    def test_extras_reach_the_artifact_a_skill_reads(self) -> None:
        issue = json.loads(json.dumps(support.load("jira", "issue")))
        issue["fields"]["customfield_10042"] = "must be idempotent"
        provider = self._jira({"customfield_10042": "acceptance_criteria"}, issue)

        payload = provider.get_task("ABC-123", depth=1, requested=[], use_cache=False)
        self.assertEqual("must be idempotent", payload["extra"]["acceptance_criteria"])

    def test_a_task_with_no_mapping_carries_no_extra_key(self) -> None:
        provider = self._jira({})
        payload = provider.get_task("ABC-123", depth=1, requested=[], use_cache=False)
        self.assertNotIn("extra", payload)

    def test_scanning_reports_what_the_normal_path_never_asked_for(self) -> None:
        issue = json.loads(json.dumps(support.load("jira", "issue")))
        issue["fields"]["customfield_10042"] = "acceptance criteria live here"
        provider = self._jira({}, issue)

        found = provider.scan_fields("ABC-123")
        self.assertIn("customfield_10042", found)
        self.assertIn("acceptance", found["customfield_10042"])


class Configured(unittest.TestCase):
    """The mapping comes off .workflow/config.json, with no caller passing it."""

    def test_a_provider_picks_up_the_repo_s_mapping(self) -> None:
        from workbench import profile

        with mock.patch.object(
            profile, "repo_config", return_value={"field_map": {"customfield_9": "impact"}}
        ):
            provider = support.FakeJira()
            provider.field_map = support.FakeJira._field_map()
        self.assertEqual({"customfield_9": "impact"}, provider.field_map)

    def test_an_invalid_entry_is_dropped_rather_than_breaking_the_read(self) -> None:
        """doctor reports the typo; a ticket must still be readable meanwhile."""
        from workbench import profile

        with mock.patch.object(
            profile,
            "repo_config",
            return_value={"field_map": {"good": "impact", "bad": "nonsense_destination"}},
        ):
            resolved = support.FakeJira._field_map()
        self.assertEqual({"good": "impact"}, resolved)


if __name__ == "__main__":
    unittest.main()

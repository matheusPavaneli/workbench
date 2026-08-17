"""The local backlog: the entry point for a repo with no tracker."""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from support import local_context
from workbench import schema
from workbench.errors import NotFoundError, UsageError, WbError
from workbench.providers import local


class LocalBase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        self.addCleanup(self._tmp.cleanup)

        self._cwd = os.getcwd()
        os.chdir(self.root)
        self.addCleanup(lambda: os.chdir(self._cwd))

        # No git repo here, so artifacts.root() falls back to the working
        # directory -- which is what a fresh, un-initialised folder does too.
        patcher = mock.patch("workbench.gitctx.repo_root", return_value=self.root)
        patcher.start()
        self.addCleanup(patcher.stop)

        self.provider = local.LocalProvider(local_context())

    def make(self, title: str, **kwargs) -> dict:
        return local.create(title, **kwargs)


class Creating(LocalBase):
    def test_keys_start_at_one_and_count_up(self) -> None:
        self.assertEqual("WB-1", self.make("first")["key"])
        self.assertEqual("WB-2", self.make("second")["key"])

    def test_a_freed_number_is_never_reused(self) -> None:
        """Two tasks sharing a key would share a .workflow directory."""
        self.make("first")
        self.make("second")
        local.task_path("WB-1").unlink()
        self.assertEqual("WB-3", self.make("third")["key"])

    def test_refuses_to_overwrite_an_existing_task(self) -> None:
        self.make("first", key="WB-9")
        with self.assertRaises(UsageError):
            self.make("again", key="WB-9")

    def test_refuses_an_unknown_type(self) -> None:
        with self.assertRaises(WbError):
            self.make("first", kind="epic")

    def test_refuses_an_empty_title(self) -> None:
        with self.assertRaises(UsageError):
            self.make("   ")


class Reading(LocalBase):
    def test_normalises_to_the_same_schema_as_a_tracker(self) -> None:
        self.make("Fix the coupon order", kind="bug", desc="Charge happens first.")
        task = self.provider.fetch_task("WB-1")
        self.assertEqual("WB-1", task.key)
        self.assertEqual("bug", task.type)
        self.assertEqual("local", task.provider)
        self.assertEqual("Charge happens first.", task.desc)

    def test_a_missing_task_names_the_ones_that_exist(self) -> None:
        self.make("first")
        with self.assertRaises(NotFoundError) as caught:
            self.provider.fetch_task("WB-7")
        self.assertIn("WB-1", " ".join(caught.exception.fix))

    def test_done_tasks_drop_out_of_the_listing(self) -> None:
        self.make("open one")
        data = self.make("closed one")
        path = local.task_path(data["key"])
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["status"] = local.DONE
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(["WB-1"], [row["key"] for row in self.provider.list_tasks(20)])

    def test_a_key_in_the_body_becomes_a_relates_link(self) -> None:
        self.make("other")
        self.make("this one", desc="Blocked in spirit by WB-1.")
        links = self.provider.fetch_task("WB-2").linked
        self.assertEqual(["WB-1"], [l.key for l in links])
        self.assertEqual(schema.RELATES, links[0].type)
        self.assertEqual("other", links[0].title)

    def test_a_body_reference_never_becomes_a_blocker(self) -> None:
        """An untyped mention is not evidence that something blocks."""
        self.make("other")
        self.make("this one", desc="See WB-1.")
        self.assertNotIn(
            schema.BLOCKED_BY, {l.type for l in self.provider.fetch_task("WB-2").linked}
        )

    def test_a_dangling_reference_is_reported_rather_than_dropped(self) -> None:
        self.make("this one", desc="See WB-99.")
        link = self.provider.fetch_task("WB-1").linked[0]
        self.assertEqual("WB-99", link.key)
        self.assertEqual("unknown", link.status)

    def test_an_explicit_link_keeps_its_type(self) -> None:
        self.make("other")
        data = self.make("this one")
        path = local.task_path(data["key"])
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["linked"] = [{"key": "WB-1", "type": schema.BLOCKED_BY}]
        path.write_text(json.dumps(payload), encoding="utf-8")
        self.assertEqual(schema.BLOCKED_BY, self.provider.fetch_task("WB-2").linked[0].type)

    def test_an_unknown_link_type_is_reported_not_silently_kept(self) -> None:
        self.make("other")
        data = self.make("this one")
        path = local.task_path(data["key"])
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["linked"] = [{"key": "WB-1", "type": "supersedes"}]
        path.write_text(json.dumps(payload), encoding="utf-8")
        task = self.provider.fetch_task("WB-2")
        self.assertEqual(schema.OTHER, task.linked[0].type)
        self.assertTrue(any("supersedes" in entry for entry in task.unmapped))

    def test_comments_come_back_newest_first(self) -> None:
        data = self.make("this one")
        path = local.task_path(data["key"])
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["comments"] = [
            {"author": "ana", "when": "2026-01-01", "text": "older"},
            {"author": "bo", "when": "2026-02-01", "text": "newer"},
        ]
        path.write_text(json.dumps(payload), encoding="utf-8")
        total, comments = self.provider.fetch_comments("WB-1", 5)
        self.assertEqual(2, total)
        self.assertEqual("newer", comments[0].text)


class Capabilities(LocalBase):
    def test_history_is_not_offered_because_a_file_has_no_changelog(self) -> None:
        self.make("first")
        payload = self.provider.get_task("WB-1", 1, [])
        self.assertNotIn("history", payload["_expand"])

    def test_asking_for_history_anyway_is_refused_with_the_valid_handles(self) -> None:
        self.make("first")
        with self.assertRaises(UsageError):
            self.provider.get_task("WB-1", 1, ["history"])

    def test_nothing_here_tries_to_authenticate(self) -> None:
        with self.assertRaises(NotFoundError):
            _ = self.provider.auth

class Closing(LocalBase):
    """A backlog that can only grow is not a backlog.

    Four statuses were defined and only one was ever written, so every finished
    ticket still read open: the listing showed work that had shipped and the
    stats counted it as outstanding.
    """

    def test_closing_a_task_removes_it_from_the_listing(self) -> None:
        self.make("first")
        self.make("second")
        local.set_status("WB-1", local.DONE)
        self.assertEqual(["WB-2"], [row["key"] for row in self.provider.list_tasks(20)])

    def test_it_reports_the_status_it_moved_from(self) -> None:
        """So a no-op is visible rather than looking like a change."""
        self.make("first")
        self.assertEqual(local.OPEN, local.set_status("WB-1", local.DONE)[0])
        self.assertEqual(local.DONE, local.set_status("WB-1", local.DONE)[0])

    def test_an_unknown_status_is_refused_rather_than_stored(self) -> None:
        self.make("first")
        with self.assertRaises(WbError):
            local.set_status("WB-1", "shipped")
        self.assertEqual(local.OPEN, self.provider.fetch_task("WB-1").status)

    def test_a_missing_task_is_refused_naming_the_ones_that_exist(self) -> None:
        self.make("first")
        with self.assertRaises(NotFoundError) as caught:
            local.set_status("WB-9", local.DONE)
        self.assertIn("WB-1", " ".join(caught.exception.fix))

    def test_the_stamp_moves_so_the_listing_order_follows(self) -> None:
        self.make("first")
        before = self.provider.fetch_task("WB-1").updated
        local.set_status("WB-1", "blocked")
        self.assertNotEqual("", self.provider.fetch_task("WB-1").updated)
        self.assertGreaterEqual(self.provider.fetch_task("WB-1").updated, before)

    def test_closing_a_task_keeps_everything_else_in_it(self) -> None:
        """Closing a ticket must not be a way to lose its description."""
        self.make("first", desc="the detail that matters", kind="bug")
        local.set_status("WB-1", local.DONE)
        task = self.provider.fetch_task("WB-1")
        self.assertEqual("the detail that matters", task.desc)
        self.assertEqual("bug", task.type)
        self.assertEqual("first", task.title)

    def test_every_defined_status_can_actually_be_reached(self) -> None:
        self.make("first")
        for status in local.STATUSES:
            with self.subTest(status=status):
                local.set_status("WB-1", status)
                self.assertEqual(status, self.provider.fetch_task("WB-1").status)


if __name__ == "__main__":
    unittest.main()

"""The same provider tests, run against the user's own recorded payloads.

Everything in `test_providers.py` runs against the vendors' published contracts.
That is the right baseline and it is not the thing that breaks: what breaks is a
custom field, a custom link type, or a workflow state named in 2019 -- none of
which any published contract describes.

So these tests skip in a fresh clone and come alive the moment somebody runs
`wb ctx record`. The assertions are deliberately about *invariants* rather than
about values: a recording is anonymised, so the titles are lorem and the names
are fake, and any test asserting on content would be asserting on the
anonymiser. What must hold is that the real payload shape still normalises --
that every required field is found, that nothing raises, and that the caps hold.

The failure this catches is the useful one: "your Jira sends the summary
somewhere ours does not, and the normaliser silently produced an empty title".
"""

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

import support  # noqa: E402


def _recorded(provider: str) -> bool:
    return support.has_local(provider)


class TenantShape(unittest.TestCase):
    """One case per provider. Skipped until a recording exists for it."""

    def _check_task(self, task) -> None:
        """What the rest of the package assumes about a normalised task.

        Every one of these is something a downstream skill reads without
        checking, so an empty value here is a plan written about nothing.
        """
        self.assertTrue(task.key, "the tenant's payload produced no key")
        self.assertTrue(task.title, "the tenant's payload produced no title")
        self.assertTrue(task.status, "the tenant's payload produced no status")
        self.assertTrue(task.type, "the tenant's payload produced no type")
        self.assertIsInstance(task.linked, list)

    @unittest.skipUnless(_recorded("jira"), "no recording: run wb ctx record <KEY> against a Jira context")
    def test_a_recorded_jira_issue_normalises(self) -> None:
        provider = support.FakeJira(local=True)
        self._check_task(provider.fetch_task("ABC-123"))

    @unittest.skipUnless(_recorded("jira"), "no recording")
    def test_recorded_jira_comments_page_and_cap(self) -> None:
        provider = support.FakeJira(local=True)
        total, comments = provider.fetch_comments("ABC-123", 5)
        self.assertGreaterEqual(total, len(comments))
        self.assertLessEqual(len(comments), 5, "the cap must hold on a real payload too")

    @unittest.skipUnless(_recorded("azure"), "no recording: run wb ctx record <ID> against an Azure context")
    def test_a_recorded_azure_work_item_normalises(self) -> None:
        provider = support.FakeAzure(local=True)
        self._check_task(provider.fetch_task("42"))

    @unittest.skipUnless(_recorded("github"), "no recording: run wb ctx record <N> against a GitHub context")
    def test_a_recorded_github_issue_normalises(self) -> None:
        provider = support.FakeGithub(local=True)
        self._check_task(provider.fetch_task("7"))


class RecordingHygiene(unittest.TestCase):
    """A recording is a committed file. These run wherever one exists."""

    def _recordings(self):
        for provider in ("jira", "azure", "github"):
            if support.has_local(provider):
                yield from support.local_fixtures(provider).glob("*.json")

    def test_no_recording_carries_an_obvious_credential(self) -> None:
        """Last line of defence. The anonymiser scrubs first, but a fixture is
        forever and this check costs nothing."""
        import re

        patterns = (
            re.compile(r"\bATATT[A-Za-z0-9_\-=]{16,}"),
            re.compile(r"(?i)\bbearer\s+[A-Za-z0-9+/=_\-.]{16,}"),
            re.compile(r"(?i)\b(token|password|secret|api[_-]?key)\"?\s*[:=]\s*\"?[A-Za-z0-9+/=_\-.]{12,}"),
            re.compile(r"\bghp_[A-Za-z0-9]{20,}"),
        )
        for path in self._recordings():
            text = path.read_text(encoding="utf-8")
            for pattern in patterns:
                with self.subTest(fixture=path.name, pattern=pattern.pattern[:30]):
                    self.assertIsNone(pattern.search(text), f"{path} may contain a credential")

    def test_no_recording_is_empty(self) -> None:
        for path in self._recordings():
            with self.subTest(fixture=path.name):
                self.assertGreater(path.stat().st_size, 2, f"{path} recorded nothing")


if __name__ == "__main__":
    unittest.main()

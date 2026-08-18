"""Fixture-backed providers.

Fixtures stand in for the transport, not for the normalisation: every test
below drives the real provider code, so a wrong field name or a wrong link
direction fails here rather than in front of a user.

Replace the JSON in ``tests/fixtures`` with anonymised payloads from a real
tracker and these tests keep their meaning -- that is the point of keeping the
transport and the mapping separate.
"""

from __future__ import annotations

import json
from pathlib import Path

from workbench.contexts import Context
from workbench.providers.azure import AzureProvider
from workbench.providers.github import GithubProvider
from workbench.providers.jira import JiraProvider

FIXTURES = Path(__file__).parent / "fixtures"

# A recording of the user's own tenant, written by ``wb ctx record``. Absent in
# a fresh clone, which is why every lookup falls back to the packaged contract.
#
# This is the difference between "the shapes the vendor documents" and "the
# shapes your instance actually sends" -- custom fields, custom link types,
# workflow states named years ago. The second is where this tool breaks first,
# and it cannot ship in the package because it does not exist until somebody
# records it.
LOCAL = "local"


def local_fixtures(provider: str) -> Path:
    return FIXTURES / provider / LOCAL


def has_local(provider: str) -> bool:
    return local_fixtures(provider).is_dir() and any(local_fixtures(provider).glob("*.json"))


def load(provider: str, name: str, *, prefer_local: bool = False):
    """A fixture by name, from the recording when asked for and present."""
    if prefer_local:
        recorded = local_fixtures(provider) / f"{name}.json"
        if recorded.is_file():
            return json.loads(recorded.read_text(encoding="utf-8"))
    return json.loads((FIXTURES / provider / f"{name}.json").read_text(encoding="utf-8"))


def jira_context(**overrides) -> Context:
    data = {
        "name": "test-jira",
        "provider": "jira",
        "base_url": "https://acme.atlassian.net",
        "project": "ABC",
        "auth": {"pat_env": "T", "email": "dev@acme.com"},
        "preset": "startup",
    }
    data.update(overrides)
    return Context(**data)


def azure_context(**overrides) -> Context:
    data = {
        "name": "test-azure",
        "provider": "azure",
        "base_url": "https://dev.azure.com/acme",
        "project": "Platform",
        "auth": {"pat_env": "T"},
        "preset": "scaleup",
    }
    data.update(overrides)
    return Context(**data)


def github_context(**overrides) -> Context:
    data = {
        "name": "test-github",
        "provider": "github",
        "base_url": "https://api.github.com",
        "project": "acme/widgets",
        "auth": {},
        "preset": "startup",
    }
    data.update(overrides)
    return Context(**data)


def local_context(**overrides) -> Context:
    data = {
        "name": "test-local",
        "provider": "local",
        "base_url": "file://.workflow/tasks",
        "project": "",
        "auth": {},
        "preset": "solo-saas",
    }
    data.update(overrides)
    return Context(**data)


class _Recorder:
    """Counts calls so tests can assert on request budgets, not just output."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def record(self, path: str) -> None:
        self.calls.append(path)


class FakeJira(JiraProvider, _Recorder):
    def __init__(self, context: Context | None = None, *, local: bool = False, **fixtures) -> None:
        JiraProvider.__init__(self, context or jira_context())
        _Recorder.__init__(self)
        self._auth = "Basic test"
        self.fixtures = fixtures
        self.local = local

    def _fixture(self, name: str) -> dict:
        return self.fixtures.get(name) or load("jira", name, prefer_local=self.local)

    def get(self, path: str, **query) -> object:
        self.record(path)
        if path.endswith("/comment"):
            # The fixture is one page. Paging past it must terminate, the way a
            # real second page of a two-comment issue would come back empty.
            page = dict(self._fixture("comments"))
            if int(query.get("startAt", 0)) > 0:
                page["comments"] = []
            return page
        if query.get("expand") == "changelog":
            return self._fixture("changelog")
        if "/issue/" in path:
            if query.get("fields") == "updated":
                # The cache revalidation call: one field, not the whole issue.
                self.calls[-1] = f"{path}?fields=updated"
                return {"fields": {"updated": self._fixture("issue")["fields"]["updated"]}}
            return self._fixture("issue")
        if path.endswith("/myself"):
            return {"displayName": "Ana Ruiz"}
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path: str, body: object, **query) -> object:
        self.record(path)
        if path.endswith("/search/jql"):
            jql = body.get("jql", "") if isinstance(body, dict) else ""
            self.last_jql = jql
            if "key in" in jql:
                return self._fixture("descriptions")
            return self._fixture("search")
        raise AssertionError(f"unexpected POST {path}")


class FakeAzure(AzureProvider, _Recorder):
    def __init__(self, context: Context | None = None, *, local: bool = False, **fixtures) -> None:
        AzureProvider.__init__(self, context or azure_context())
        _Recorder.__init__(self)
        self._auth = "Basic test"
        self.fixtures = fixtures
        self.local = local

    def _fixture(self, name: str) -> dict:
        return self.fixtures.get(name) or load("azure", name, prefer_local=self.local)

    def get(self, path: str, **query) -> object:
        self.record(path)
        if path.endswith("/comments"):
            return self._fixture("comments")
        if path.endswith("/updates"):
            return self._fixture("updates")
        if path.endswith("/_apis/wit/workitems"):
            fields = query.get("fields", "")
            if "ChangedDate" in fields:
                return self._fixture("batch_list")
            if "Description" in fields:
                return self._fixture("descriptions")
            return self._fixture("batch")
        if "/workitems/" in path:
            return self._fixture("workitem")
        if "/_apis/projects/" in path:
            return {"name": "Platform"}
        raise AssertionError(f"unexpected GET {path}")

    def post(self, path: str, body: object, **query) -> object:
        self.record(path)
        if path.endswith("/wiql"):
            self.last_wiql = body.get("query", "") if isinstance(body, dict) else ""
            return self._fixture("wiql")
        raise AssertionError(f"unexpected POST {path}")


class FakeGithub(GithubProvider, _Recorder):
    """The transport is a fixture; the mapping under test is the real one."""

    def __init__(self, context: Context | None = None, *, local: bool = False, **fixtures) -> None:
        GithubProvider.__init__(self, context or github_context())
        _Recorder.__init__(self)
        self._auth = "Bearer test"
        self.fixtures = fixtures
        self.local = local

    def _fixture(self, name: str):
        loaded = self.fixtures.get(name)
        return loaded if loaded is not None else load("github", name, prefer_local=self.local)

    def get(self, path: str, **query) -> object:
        self.record(path)
        if path == "/user":
            return {"login": "ana"}
        if path.endswith("/timeline"):
            return self._fixture("timeline")
        if path.endswith("/comments"):
            # One page only. A second page must come back empty, the way a real
            # two-comment issue does, or paging never terminates.
            return self._fixture("comments") if int(query.get("page", 1)) == 1 else []
        if path.endswith("/issues"):
            return self._fixture("list")
        if "/issues/" in path:
            return self._fixture("issue")
        raise AssertionError(f"unexpected GET {path}")

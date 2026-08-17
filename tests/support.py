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
from workbench.providers.jira import JiraProvider

FIXTURES = Path(__file__).parent / "fixtures"


def load(provider: str, name: str) -> dict:
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


class _Recorder:
    """Counts calls so tests can assert on request budgets, not just output."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def record(self, path: str) -> None:
        self.calls.append(path)


class FakeJira(JiraProvider, _Recorder):
    def __init__(self, context: Context | None = None, **fixtures) -> None:
        JiraProvider.__init__(self, context or jira_context())
        _Recorder.__init__(self)
        self._auth = "Basic test"
        self.fixtures = fixtures

    def _fixture(self, name: str) -> dict:
        return self.fixtures.get(name) or load("jira", name)

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
    def __init__(self, context: Context | None = None, **fixtures) -> None:
        AzureProvider.__init__(self, context or azure_context())
        _Recorder.__init__(self)
        self._auth = "Basic test"
        self.fixtures = fixtures

    def _fixture(self, name: str) -> dict:
        return self.fixtures.get(name) or load("azure", name)

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

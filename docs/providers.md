# Provider notes

Reference for people maintaining `lib/workbench/providers/`. Agents never read
this: the behaviour it describes is enforced in code and covered by tests, so
loading it into a session would be paying twice for the same guarantee.

## Internal schema

One shape, whatever the tracker. Consumers must never be able to tell which one
answered.

```json
{
  "schema": 1, "provider": "jira", "key": "ABC-123",
  "title": "...", "type": "Bug", "status": "In Review",
  "assignee": "Ana Ruiz", "updated": "...", "url": "...",
  "desc": "first 800 chars", "desc_chars": 1840,
  "comments": { "total": 42, "recent": [{ "author": "", "when": "", "text": "" }] },
  "linked": [{ "key": "ABC-98", "type": "blocked_by", "status": "Done", "title": "..." }],
  "linked_total": 6,
  "_expand": ["comments:all", "linked:ABC-98:full", "history"],
  "_unmapped": ["issuelink type: mitigates"],
  "_truncated": ["comments (40 more)"]
}
```

Link `url` is deliberately absent: it is derivable from the key and cost about a
sixth of the payload for no new fact.

Canonical link types: `blocks`, `blocked_by`, `duplicates`, `duplicated_by`,
`parent`, `child`, `relates`, `other`. A type that does not map lands in
`other` **and** is named in `_unmapped` — never silently reinterpreted.

## Jira Cloud (REST v3)

| Concern | Detail |
|---|---|
| Rich text | ADF documents, not strings. `text.adf_to_text` flattens them; unknown node types are traversed rather than dropped, because ADF gains node types over time. |
| Search | `/rest/api/3/search` is retired (shutdown completed October 2025). Use `POST /rest/api/3/search/jql`. It returns **no `total`** and pages with `nextPageToken`, not `startAt` — an approximate count needs `POST /rest/api/3/search/approximate-count`. Nothing here depends on a total from search. |
| Link direction | An `issuelinks` entry carries either `inwardIssue` or `outwardIssue`, and which one decides the meaning. `Blocks` + inward = **blocked_by**; `Blocks` + outward = **blocks**. |
| Hierarchy | `parent` and `subtasks` are separate fields, not links. Folded into the link list. |
| Comments | `GET /issue/{key}/comment` is a normal paginated bean: `startAt`, `maxResults`, `total`, `orderBy=-created`. `total` is the real count; the page size is what you asked for. Both are reported. Comments are **not** read through the issue endpoint, which limits how many it returns. |
| Status | `status.name` is free text per workflow. Never compared against a hardcoded list; `statusCategory` is used for open/closed. |

## Azure DevOps (REST 7.1)

| Concern | Detail |
|---|---|
| Fields | Namespaced strings (`System.Title`, `System.State`), not nested objects. |
| Rich text | Descriptions are HTML. Bugs put their body in `Microsoft.VSTS.TCM.ReproSteps`, not `System.Description`; both are checked, in that order. `<script>` content is dropped. **Comments are different**: `text` is markdown unless `format` says `html`, in which case `renderedText` holds the HTML. Stripping tags off markdown eats anything with an angle bracket, so `format` decides. |
| Links | `relations` targets are URLs, so the id is parsed out. Attachments, hyperlinks and artifact links are **not** work item links and are skipped — that is correct behaviour, so they never reach `_unmapped`. |
| Link direction | The rel suffix names the target from this item's point of view. Verified against `az boards work-item relation list-type`: `Hierarchy-Forward`=Child, `Hierarchy-Reverse`=Parent, `Dependency-Forward`=Successor, `Dependency-Reverse`=Predecessor, `Duplicate-Forward`=Duplicate, `Duplicate-Reverse`=Duplicate Of. A successor completes *after* this item, so Dependency-Forward → `blocks`. Remote (cross-org) pairs map the same way. |
| Listing | WIQL returns ids only, so `task list` always costs two calls: the query, then one batch fetch. |
| Batch | `/_apis/wit/workitems?ids=` takes **200 ids maximum**. The parameter is `errorPolicy=Omit` — **without the `$`**; the `$` form is silently ignored, and then a single deleted or inaccessible id fails the whole request. Only one of `fields` / `$expand` is ever passed. |

## Adding a provider

1. Subclass `Provider`, implement `list_tasks`, `fetch_task`, `fetch_comments`,
   `fetch_descriptions`, `fetch_history`, `probe`, `_build_auth`.
2. Map link types onto the canonical set. Anything unmapped goes to `other`
   *and* `_unmapped`.
3. Register it in `providers/__init__.py`.
4. Add fixtures under `tests/fixtures/<name>/` and extend the parity test.

Depth, expansion, caps and degradation are orchestrated in `Provider.get_task`
and must not be reimplemented per provider — that is how two trackers drift
apart in behaviour.

## What the fixtures prove, and what they do not

The contracts above were checked against the vendors' published documentation,
and three defects were fixed as a result: `errorPolicy` was being sent with a
`$` prefix and therefore ignored, Azure comment bodies were HTML-stripped when
they are markdown by default, and the cross-organization relation types were
unmapped. The endpoints, parameter names, response shapes and link-direction
semantics are **verified**.

What documentation cannot verify is your instance:

- custom fields (`customfield_10xxx`, custom Azure process fields)
- workflow state names — `status.name` and `System.State` are free text per
  project, and nothing here compares them against a hardcoded list
- custom link types, which land in `other` and are named in `_unmapped`
- required fields your process adds to a work item type

So the fixtures prove the mapping is correct and self-consistent against the
published contract. Replace them with anonymised payloads from your own tenant
and they additionally prove it against your configuration — the transport is
faked either way, the normalisation under test is real.

Sources: [Jira issue search](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/),
[Jira comments](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-comments/),
[Azure link types](https://learn.microsoft.com/en-us/azure/devops/boards/queries/link-type-reference?view=azure-devops),
[Azure comments](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/comments/get-comments?view=azure-devops-rest-7.1),
[Azure work items list](https://learn.microsoft.com/en-us/rest/api/azure/devops/wit/work-items/list?view=azure-devops-rest-7.1).

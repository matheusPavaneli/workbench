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

## GitHub Issues

Chosen for reach rather than depth. A GitHub context costs nothing on a machine
that already has `gh` logged in, which is most personal and open-source work.

Three gaps, none of them papered over:

| GitHub has no | What the provider does |
|---|---|
| typed links | body references (`#123`, `owner/repo#123`) map to `relates`, never to `blocks`. Real hierarchy comes from the `parent` and `sub_issues_summary` fields |
| an issue type | the label set is the only signal; unrecognised labels land in `_unmapped` rather than being guessed at |
| a field-limited read | every read returns the whole issue, so revalidation costs what a refetch costs. Caching is skipped rather than pretended |

Comments arrive oldest-first with no total, so the provider pages and reverses,
capped like every other provider. Code spans are stripped before references are
extracted: `` `#999` `` is a colour far more often than an issue.

Auth prefers a configured `pat_env`/`pat_keychain` and falls back to
`gh auth token`. The fallback is what makes the provider cheap to adopt; it is
a fallback and not the default so a context that names its credential keeps
working on a machine with no `gh`.

## GitLab Issues (REST v4)

This provider works with gitlab.com and with self-managed instances. `base_url`
is the instance root (`https://gitlab.com` by default), and the API is read
from `<base_url>/api/v4`.

| Concern | Detail |
|---|---|
| Keys | The issue's **IID**, a plain number such as `42` (`#42` is also accepted). IIDs are unique within a project, and the project is fixed, so one number maps to exactly one issue. The global issue id would be unique too, but it appears in no URL, reference or UI, and a key the user cannot read off the screen is a key they will get wrong. |
| Project | `group/subgroup/project`, taken from the context's `project` or from the checkout's remote. The remote is only used when its host matches `base_url`'s host; otherwise the error says which `base_url` to set. The path is URL-encoded as the project id, so nested groups work. |
| Auth | A `pat_env`/`pat_keychain` token is used if the context has one. Otherwise the provider falls back to glab's stored token for the host (`glab config get token --host <host>`), the same way GitHub falls back to `gh`. The token is sent as `Bearer`, registered with `redact`, and never written anywhere. |
| Links | `GET …/issues/:iid/links` returns typed links: `blocks` maps to `blocks`, `is_blocked_by` to `blocked_by`, and `relates_to` to `relates`. A linked issue in another project keeps its full reference (`acme/billing#8`) as its key. Any other link type lands in `other` and `_unmapped`. |
| Hierarchy | On Premium, the issue payload carries its `epic`, which becomes the `parent` (key `&<iid>`). |
| Notes | Requested with `sort=desc`, then sorted again locally; 100 per page, 3 pages at most. **System notes** ("changed the description", "added ~bug label") are left out of the comments and used as the issue's history, which costs no extra request. |
| Type | `issue_type: incident` becomes `incident`. Everything else comes from labels, including scoped ones (`type::bug`). Unrecognised labels land in `_unmapped`. |
| Cache | There is no field-limited read, so revalidation reads the whole issue. It still skips the links request that a refetch would cost. |

What GitLab lacks here:

| GitLab REST has no | What the provider does |
|---|---|
| child tasks in the issue payload | tasks are work items and are only exposed through GraphQL; only the epic is mapped to hierarchy |
| a batch issue read | linked descriptions cost one request per same-project IID, capped at the linked limit |
| a comment total in the body | the total is what the capped pages returned |

## Linear (GraphQL)

Linear is the tracker many small product teams use instead of Jira. The whole
API is one endpoint: `POST https://api.linear.app/graphql`.

| Concern | Detail |
|---|---|
| Auth | A personal API key from Linear's security settings, named by `pat_env` (e.g. `LINEAR_API_KEY`) or `pat_keychain`. It is sent as the `Authorization` header as-is. `Bearer` is only for OAuth tokens. Like every credential, it is resolved through `secrets.resolve` and registered with `redact`, and it is never written to a context, cache or log. |
| Keys | `TEAM-123`, which `issue(id:)` accepts directly. A malformed key is refused before any request. `project` is optional and holds a team key: when set, `task list` only shows that team's issues. |
| Operations | Each call sends an `operationName` (`Issue`, `IssueComments`, `IssueHistory`, `IssueDescriptions`, `IssueUpdated`, `AssignedIssues`, `Viewer`). That name is how `wb ctx record` tells the payloads apart, since the path never changes. Keys are passed as variables and never spliced into the query text. |
| Relations | Linear stores a relation once, on the issue that created it. `relations` is this issue's side and `inverseRelations` is the other issue's, so the list a relation arrives in gives its direction. `blocks` maps to `blocks` / `blocked_by`, `duplicate` to `duplicates` / `duplicated_by`, and `related` to `relates`. `similar` is Linear's AI suggestion rather than a decision, so it lands in `other` and `_unmapped`, as does any type Linear adds later. |
| Hierarchy | `parent` and `children` (sub-issues) become `parent` / `child` links. |
| Comments | Paged at 50 with at most 4 pages. The connection's order is not documented as chronological, so comments are sorted by `createdAt`, newest first, and then capped. |
| Batch | Linked descriptions are fetched in one request, with one alias per issue. If a link points to an issue the key cannot see, that alias comes back as an error next to the others' data, and the rest are kept. |
| Cache | `issue { updatedAt }` is a real field-limited read, so a cached ticket is revalidated instead of refetched. |
| Rate limits | Linear reports a limit as a GraphQL error with code `RATELIMITED`, either in a 200 body or with HTTP 400. Both become one `ProviderError` that says so. The shared transport only retries 429/5xx, so no requests are spent against a limit that resets hourly. |

What Linear has no equivalent for:

| Linear has no | What the provider does |
|---|---|
| issue type | the label set is the only signal (`Bug`, `Feature`, `Improvement`, …); unrecognised labels land in `_unmapped` |
| a status category you can compare to a fixed list | `state.name` is shown as written; `task list` filters on `state.type` (`completed`, `canceled`), which is fixed |
| a comment total | the total is what the capped pages returned, as on GitHub |

The fixtures in `tests/fixtures/linear/` follow Linear's published schema.
They are not a recording of a live workspace. `wb ctx record` works for this
provider, so you can add one.

## Recording your own tenant

The fixtures in `tests/fixtures/` follow the vendors' published contracts —
endpoints, parameter names, response shapes, link-direction semantics, all
checked against the documentation. What they cannot cover is *your* instance:
the custom fields, the custom link types, the workflow state names somebody
invented years ago. That gap is where this tool breaks first for a new user.

```sh
wb ctx record ABC-123
```

Runs the calls `triage-task` makes against a real ticket, replaces the content,
keeps the shape, and writes the result to `tests/fixtures/<provider>/local/`.
The suite picks those up automatically and runs every provider test twice: once
against the packaged contracts, once against your tenant.

What "replaces the content" means precisely:

| | |
|---|---|
| Kept | key names, nesting, types, list lengths, and the structural values the code branches on (`id`, `key`, `type`, `statusCategory`, …) |
| Replaced | every free-text value, with lorem of the same length, line count and list markers |
| Replaced consistently | names, emails, URLs and account ids — the same person stays one person across the fixture, without being traceable |
| Replaced by default | anything it cannot classify |

Consistency comes from a salt generated per run and thrown away, so two
recordings of the same ticket do not agree and nothing survives to correlate.
Secrets go through `redact.scrub` first, whatever the field is called.

It is still your data and your judgement: **read a recording before committing
it.** The design fails towards losing information, but no anonymiser is a
substitute for looking.

`wb ctx test --deep` is the other half. It reports the fields present in the
payload that this tool does not read — a `customfield_10042` carrying acceptance
criteria is invisible today, and silence there is worse than a wrong mapping,
because a wrong mapping gets noticed. Map one with `field_map` in
`.workflow/config.json`.

## Local

A backlog with no tracker, no network and no credential: one JSON file per task
under `.workflow/tasks/`. It exists because nine of the ten skills are
tracker-agnostic, and requiring a Jira site to reach them was a cost with
nothing behind it.

- Keys are `WB-<n>` and only ever count up. Reusing a freed number would point
  two `.workflow/<KEY>/` directories at one task.
- Keys mentioned in a body become `relates` links and never more; a reference
  to a task the backlog does not hold is reported with status `unknown` rather
  than dropped.
- `has_history = False`: a JSON file keeps no changelog, so the `history`
  expand handle is not offered. Offering it would cost a round trip to learn
  nothing.

Because a local context holds no secret, it is the one provider that may be
defined inline in the committable `.workflow/config.json`, which is what makes
a fresh clone work with no setup.

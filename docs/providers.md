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

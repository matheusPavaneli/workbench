# Depth and expansion policy

Enforced in `lib/workbench/depth.py` and `lib/workbench/expand.py`, and covered
by tests. This page is for maintainers; agents choose `--depth` and pass
handles back, and the code decides the rest.

## Depth

| Depth | What is read |
|---|---|
| 0 | The task alone. `linked_total` still reports how many links exist. |
| 1 | Linked items as one line each: key, type, status, title. **Default.** |
| 2 | Full body, but only for `blocks`, `blocked_by`, `parent`, `child`. |
| — | `relates` is never followed in depth, at any level. |

`relates` is what people attach when they are not sure the link matters.
Following it turns a triage into a crawl of the board.

Depth 2 fetches **one hop, never recursively**. A board is a cyclic graph, and
a recursive read of it is unbounded work for information nobody asked for. A
visited set and `MAX_NODES = 25` hold regardless, because a ticket can link the
same key twice, link itself, or reappear through its parent.

A batch call can return more items than were selected. Only selected links get
a body — otherwise `relates` picks one up for free just by sharing a response.

## Expansion handles

`_expand` is a map of what exists, without the contents: the agent sees the
shape of what it is missing and chooses where to spend.

Handles are opaque. They are generated from what the payload actually contains
and validated on the way back in; one that was never offered is rejected along
with the list of the ones that were. That is what stops an invented
`comments:page2` from quietly returning nothing.

| Handle | Offered when |
|---|---|
| `desc:full` | the description was capped |
| `comments:all` | more comments exist than were shown |
| `linked:<KEY>:full` | that link is a deep type and has no body yet |
| `history` | always |

The round trip an expansion costs is largely absorbed by the 15-minute cache in
`artifacts.py`: the task itself is not refetched.

## Caps

| What | Cap |
|---|---|
| description | 800 chars (`desc_chars` reports the real length) |
| comment body | 300 chars, newest 5 |
| linked items | 15 |
| title | 160 chars |
| whole payload | 3 KB, degrading in a fixed order |

Degradation order is fixed so the same ticket always degrades the same way:
comment bodies, then whole comments, then linked descriptions, then links.
Everything shed is named in `_truncated`.

An explicit expansion overrides the ceiling — asking for `desc:full` and then
silently truncating would make the handle a lie. The upstream bound in
`text.MAX_CHARS` (20 KB) still applies.

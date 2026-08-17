# Implementation guardrails and verification

Maintainer reference for `lib/workbench/verify.py`, `cli/impl.py` and the
read-only git façade.

## Scope guard

`wb impl check <KEY>` compares the working tree against the plan's `files` list.

- **In plan** — reported as `ok`.
- **Planned but untouched** — reported as `pending`, not an error. A plan is
  allowed to be half done.
- **Changed but unplanned** — a deviation. Exit 7, and any critical zone the
  stray paths touch is named alongside them.

`.workflow/` and generated output — bytecode, `node_modules/`, `dist/`,
coverage — are excluded from the comparison everywhere. A repo with a proper
`.gitignore` never surfaces them, but a fresh checkout without one reported
`__pycache__/*.pyc` as scope creep, and as touching a critical zone.

Both `impl check` and `impl verify` refuse to run unless `audit.json` exists and
reads `pass`. Implementing from an unaudited plan is the failure this whole
design exists to prevent, so the CLI does not offer a way to do it.

## When the audit runs

`wb sdd audit` is a **pre-implementation** gate. It compares the plan's
citations against the code as it stands before the change. After implementing,
those line numbers have moved and a re-run reports `moved` — correctly. The
post-implementation checks are `impl check` (scope) and `impl verify`
(behaviour); re-audit only after editing the plan itself.

## Verification

`wb impl verify <KEY>` runs the plan's `verify` list and writes
`evidence.md` plus `evidence.json`.

Commands come from a file a model wrote, so the boundary is drawn narrowly:

| Rule | Why |
|---|---|
| Only commands already in the audited `sdd.json` | Nothing can be passed in ad hoc at call time. |
| Only known build, test and lint runners | An allowlist in `verify.ALLOWED_RUNNERS`. `git` is not on it. |
| No shell | `;`, `&&`, `\|`, `>`, `` ` ``, `$(` are refused outright. Commands are split with `shlex` and executed directly. |
| 600 s timeout per command | A hung test suite fails the step rather than the session. |
| Output capped, head and tail kept | Failures live at the end, context at the start. |

A refused command is **not** executed and **not** silently dropped: it lands in
`evidence.md` under "Not run" with the reason, and the verdict cannot be `pass`
while anything was refused. Claiming verification for a command that never ran
is exactly the failure mode the evidence file exists to close.

Adding a runner to the allowlist is a deliberate change. The fallback — the user
runs it and reports back — always works.

### Quoting

`verify` entries are split POSIX-style on every platform. Bare runner names are
the normal case (`pytest -q`, `pnpm test`). An absolute Windows path must be
double-quoted, or its backslashes are consumed as escapes:

```json
"verify": ["pytest -q", "\"C:/Program Files/nodejs/node.exe\" --test"]
```

Forward slashes work everywhere and are the simpler choice.

## Git façade

`lib/workbench/gitctx.py` is read-only by design. Nothing in this package
commits, pushes, or modifies a working tree — those are the user's calls, and
they go through the agent's own tooling where the user sees and approves them.

`wb git ctx` also reports when the checkout's `user.email` differs from the one
the resolved context expects, which is how work commits end up carrying a
personal address.

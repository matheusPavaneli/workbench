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

`wb sdd audit` is a gate before implementation, but it is not only usable
before implementation. The first audit is strict and checks the working tree,
because a wrong line number is a defect while the plan is still cheap to
change. It also records the commit it ran against, as `baseline` in
`audit.json`.

Every audit after that is **anchored to that commit**, and the plan is treated
as under way:

| Citation | First audit | Once under way |
|---|---|---|
| matches at the cited line | `ok` | `ok` |
| found elsewhere in the file | `moved`, fails | `moved`, passes, line reported |
| found only at the baseline commit | `mismatch`, fails | `baseline`, passes |
| found nowhere, ever | `mismatch`, fails | `mismatch`, fails |

This exists because `plan-change` instructs the author to correct a plan that
turns out wrong — which is precisely when the tree has already moved. Before
the baseline, the only route was to revert the work, extend the plan, re-audit
and redo it.

A drifted citation is **never folded into a silent `ok`**. Each one is printed
with the line it is now at, so a reader can tell which claims no longer
describe the current code. `wb sdd audit <KEY> --rebaseline` re-anchors the
plan to the current tree and is strict again.

What the fallback never does is let an invented claim through: a quote found in
neither the working tree nor the baseline is still a `mismatch`.

The post-implementation checks remain `impl check` (scope) and `impl verify`
(behaviour).

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

### Environment

Shell is refused, so `PYTHONPATH=lib python -m unittest` cannot be written as a
command -- which meant a repo whose tests need a variable could not be verified
at all. This repo was one of them.

Variables are therefore declared as data, in the audited plan, where they are
reviewed alongside the commands:

```json
"verify": ["python -m unittest discover -s tests -q"],
"verify_env": { "PYTHONPATH": "lib" }
```

Nothing is expanded, interpolated or read from a file: a value is a literal
string, merged over the ambient environment.

`verify.FORBIDDEN_ENV` refuses the variables that change how a process loads
code -- `PATH`, `LD_PRELOAD`, `NODE_OPTIONS`, `PYTHONSTARTUP`, `BASH_ENV` and
their relatives. Those would run something the command allowlist never sees,
which is the one thing this boundary exists to prevent. A refused variable is
reported under "Not run" and the verdict cannot be `pass` while one is present,
exactly like a refused command.

`evidence.md` records the variable **names** only. A value is as likely to be a
connection string as a search path, and the file is written to be pasted into a
PR.

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

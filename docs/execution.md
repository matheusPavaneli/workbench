# Implementation guardrails and verification

Maintainer reference for `lib/workbench/verify.py`, `cli/impl.py` and the
read-only git façade.

## Scope guard

`wb impl check <KEY>` compares the working tree against the plan's `files` list.

- **In plan** — reported as `ok`.
- **Planned but untouched** — reported as `pending`, not an error. A plan is
  allowed to be half done.
- **Claimed by another audited plan** — reported as `other`, naming the ticket.
  Not this ticket's work, but not unexplained either, and those are different
  findings. Without this, a second ticket open in the same checkout — the
  ordinary state of a working day — read as scope creep on the first, and a
  guard that fails on ordinary work is one people learn to ignore.
- **Claimed by this plan and another** — reported as `overlap`. Two plans
  editing one file is worth knowing before either lands.
- **Changed but unplanned** — a deviation. Exit 7, and any critical zone the
  stray paths touch is named alongside them.

Only an **audited** plan may account for a path. An unaudited plan is a file
somebody wrote, and letting one excuse a change would leave a hole straight
through the guard: anyone could silence the scope check by listing a path in a
document nothing verified. Attribution lives in `lib/workbench/scope.py`.

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

`lib/workbench/gitctx.py` is read-only by design, and everything that reads a
repository goes through it.

`wb git ctx` also reports when the checkout's `user.email` differs from the one
the resolved context expects, which is how work commits end up carrying a
personal address.

## Git execution boundary

`lib/workbench/gitrun.py` is the one module that writes to a repository, and
only for a call that passed `--execute`. It is drawn the same way the verify
boundary above is drawn, and for the same reason: the commands are computed
from a plan, not typed by a person.

The default is unchanged — print the command, let the user run it. `--execute`
removes the copy-paste, not the review.

### What may run

| Subcommand | Flags allowed |
|---|---|
| `fetch` | `--prune` |
| `switch` | `-c` |
| `cherry-pick` | `--continue`, `--abort`, `-x` |
| `commit` | `-F`, `--author` |
| `push` | `-u` |

Everything else is refused, including a flag the subcommand does not own. Also
refused wherever it appears: `--force` in any spelling, `--amend`,
`--no-verify`, `reset`, `rebase`, `clean`, `filter-branch`, `update-ref`, and
anything that redirects what git itself runs (`--exec`, `--upload-pack`,
`--receive-pack`). There is no shell — `;`, `&`, `|`, backticks and `$(` in any
token are a refusal, not an escape.

`<` and `>` are redirection to a shell and punctuation to a human, and the only
one this tool produces is the second kind: the RFC 822 address in `--author`.
They are therefore allowed **in the value of a flag that takes one**, and
nowhere else; everything that could chain or substitute a command stays refused
in every position. The printed form quotes any token carrying spaces or angle
brackets, so a pasted command reaches git as one argument rather than as a
redirect.

`-c` is deliberately **not** on the denied list. It is git's config override
before a subcommand and `switch`'s create flag after one; the override form
cannot reach here because `argv[0]` must be an allowed subcommand, so denying
the token would only break the legitimate use.

### Preconditions

Checked immediately before each step, never once for the series — a series
changes the state its later steps depend on.

| Precondition | Refuses when |
|---|---|
| `clean-tree` | a **tracked** file has uncommitted changes |
| `not-protected` | the current branch is protected by the flow |
| `no-upstream` | the branch is already published |

`clean-tree` reads tracked changes only. `git switch -c` carries untracked
files across unharmed, and `git stash` without `-u` leaves them where they are —
so counting them refused the ordinary case and offered a remedy that did not
clear it. Scope checking still counts untracked files, because a plan that adds
a file has to be checked against the file it added; the two questions are
different and now use different reads.

Protected branches come from `flow.resolve()` — repo config, then context, then
detection — the same resolution `wb flow` uses. If the flow cannot be resolved
at all the check **fails closed** onto the conventional names, because not
knowing what is protected is not permission to write to any of it.

`no-upstream` is why `wb git push` is first-publish only. Recovering from a bad
push onto a published branch means a force-push, and a force-push should not be
a situation this tool can reach.

### Stopping

A refusal or a non-zero exit stops the series and prints what did not run,
**starting at the step that failed** — that step did not happen, so handing over
the remainder without it would mean, for `carry`, a cherry-pick whose branch was
never created being applied to whatever branch the user is standing on. This
matters most for `carry`: a cherry-pick series that continues past a failure
lands the rest out of order, which is the mistake the carry computation exists
to prevent.

### Switches

Either of these turns execution off, and `--execute` then fails loudly rather
than silently doing nothing:

- `WB_NO_EXECUTE=1` in the environment — for a session or a CI job that must
  never write
- `"execute": false` in `.workflow/config.json` — a standing decision for a
  checkout

### The trail

Every executed series is appended to `.workflow/<KEY>/git.log.json` with the
exact argv, exit code and captured output, and counted in `wb status --stats`
through the usual event record.

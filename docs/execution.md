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
- **Tied to a planned file** — reported as `companion`, naming the tie
  (`companion tests/test_util.py  (test of src/util.py)`), and counted apart
  from `ok` and `other`. See below.
- **Changed but unplanned** — a deviation. Exit 7, and any critical zone the
  stray paths touch is named alongside them.

### Companions

Tests, type declarations, lockfiles and generated code change mechanically
when a planned file changes. Listing them is ceremony, and forgetting one
stopped a session mid-implementation. A companion is released only by its tie
to a planned file, decided by `companions.reason()` in
`lib/workbench/companions.py`. `impl check` and the pre-tool-use hook both call
it, so they never disagree.

| Tie | Released when |
|---|---|
| test | same stem as a planned non-test source in the same language family, under a test marker: `test_x.py`, `x_test.py`, `x_test.go`, `x.test.*`, `x.spec.*`, or any file under `__tests__/` |
| declaration | `x.d.ts` in the same directory as a planned `x.ts` or `x.js` |
| lockfile | its manifest in the same directory is planned (`package.json`, `pyproject.toml`, `Pipfile`, `go.mod`, `Cargo.toml`, `Gemfile`, `composer.json`) |
| generated | it matches a glob in the `generated` list of `.workflow/config.json`; no list, no release |

A lockfile changed without its manifest stays a deviation: unplanned
dependency drift is exactly what the guard is for. A path in a critical zone is
never a companion, whatever it matches. A companion is never measured toward a
light plan's size, because only planned paths are.

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
before implementation. The first audit is strict and checks the file as
committed at HEAD, because a wrong line number is a defect while the plan is
still cheap to change. It also records the commit it ran against, as
`baseline` in `audit.json`.

Whatever the stage, a citation must point at code that commit contains. A
session can write any line it likes to disk and then quote it, so the audit
does not take the disk's word for it: a citation into `.workflow/` is
`artifact`, and one into a file the anchor commit lacks -- untracked, ignored,
or added by the plan -- or to a line that exists only as an uncommitted edit is
`uncommitted`. Both fail at every stage. Outside a checkout there is no commit
to ask, and only the `.workflow/` rule applies.

Every audit after the first one that **passes** is **anchored to that commit**,
and the plan is treated as under way. A first audit that fails anchors nothing:
re-running it is strict again, so failing once is not a way past the strict
check. Once a plan has passed, a failing correction keeps the anchor it had.

| Citation | First audit | Once under way |
|---|---|---|
| matches at the cited line | `ok` | `ok` |
| found elsewhere in the file | `moved`, fails | `moved`, passes, line reported |
| found only at the baseline commit | `mismatch`, fails | `baseline`, passes |
| found nowhere, ever | `mismatch`, fails | `mismatch`, fails |
| only in uncommitted changes, or a file the commit lacks | `uncommitted`, fails | `uncommitted`, fails |
| under `.workflow/` | `artifact`, fails | `artifact`, fails |

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

A quote has to be the text of the cited line. A statement that wraps may be
quoted across the lines that follow it, but the quote must start on the cited
line and every word of it must be there; a real line with invented text after
it is a `mismatch`. A quote under eight characters, whitespace aside, is a
`mismatch` too: it identifies no line.

`audit.json` records `plan_sha256`, a fingerprint of the plan it audited.
`impl check`, `impl verify`, `wb status` and the scope attribution between
tickets all trust a verdict only for that plan; an edited plan has to be
audited again. This catches drift, not forgery -- whatever can write both files
can make them agree.

The post-implementation checks remain `impl check` (scope) and `impl verify`
(behaviour).

## Amending an audited plan

When an implementation needs a file the plan missed, the old route was to
hand-edit `sdd.json`, copy the zones from `wb repo zones`, and re-run the
audit. That loop cost more than the file did, and a hand edit is exactly where
a `zones` block or a `why` goes stale. `wb sdd amend` replaces it:

```
wb sdd amend <KEY> <path>... --why "<reason>" [--new] [--lines N]
```

- It refuses a plan that does not stand. Amend widens a plan that passed; it is
  not a way around the first audit.
- `--why` is required. Each path must exist unless `--new` marks it as a file
  the change creates, and `--new` refuses a file that already exists. A path
  already in the plan is refused.
- It appends `{path, change, why}` (plus `lines` when given) to `files`,
  recomputes `zones`, and re-runs the audit at the baseline the plan already
  has.
- It prints the new tier. Without `--lines` the new entry has no estimate, so
  the plan becomes `standard`. If that raises the tier, or enters a critical
  zone, the audit fails the way any plan missing `steps` or `product` does
  (exit 7). Nothing is waived silently.
- `audit.json` is refreshed only on a pass. On a fail the plan stays amended and
  does not stand, so `impl check`, `impl verify` and the hook refuse to work
  from it until it is fixed.
- Each path is recorded in the plan's `amendments` as `{path, why, at}`, and the
  event log records `sdd amend` with `amended: <count>` (a count only, never the
  paths). `wb pr context` carries them as `amended`, `wb review context` lists
  them, and `sdd.md` renders them. A reviewer can see that the plan widened
  after its audit.

A plan with no `amendments` key validates exactly as it did before.

The pre-tool-use hook's refusal names this command as the way through.

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

### Approval

The allowlist bounds which program runs, not what it is told to do: `python -c`
and `node -e` pass it by construction, and the audit checks a plan's citations,
not its commands. So nothing runs until a person has approved the exact string
on this machine.

```
$ wb impl verify ABC-123
not approved on this machine (2):
  python -m unittest discover -s tests -q
  env PYTHONPATH=lib

To approve and run: wb impl verify ABC-123 --approve 'python -m unittest discover -s tests -q' --approve 'env PYTHONPATH=lib'
```

Nothing is executed and no evidence is written until every entry is approved.
Each command is approved verbatim and each variable as `env NAME=value`, so the
approve call's own permission prompt shows what will run; a digest would be
approved unread. One changed character is a new entry and asks again.

The one exception is the plan's own key. A verify list usually names its
ticket (`wb sdd audit ABC-123`), and a verbatim approval then asked again on
every ticket for the same command — which is how an approval becomes a
reflex. The key, where it stands as a whole token, is written `<KEY>` in the
entry, so `python lib/wb.py sdd audit <KEY>` approved once covers every ticket.
`ABC-1234`, `x-ABC-123` or any other change is a different entry. `<` is
refused in every command that runs, so the placeholder cannot be real text.
Approvals stored verbatim by earlier releases still count for their ticket.

Approvals live in `$WORKBENCH_HOME/approvals.json` (default `~/.workbench`),
keyed by the checkout's path — never in the repo, where a plan could ship its
own. A second clone asks again; keying by remote would let a fork inherit
upstream's approvals. `--approve` takes only entries the plan names.

This is as strong as the permission prompt that shows the call. An agent can
run it, and under a permission mode that approves everything it gates nothing.

### Bound to the tree

`evidence.json` records `tree` — the git tree the working tree would commit as,
tracked, modified and untracked files included, ignored files and `.workflow/`
left out — plus `head` and the plan's `plan_sha256`. The tree is taken before
the first command runs, in a scratch copy of the index; the staging area is
not touched.

A pass stands only while both still match. Verifying before the commit and
committing exactly that code keeps it standing, because the commit's tree is the
one recorded. Any other edit, or an edited plan, makes `wb status` read verify
as `stale` and offer the command again, and `wb pr context` reports
`"standing": false` with the reason. Evidence written before 0.8.0 recorded no
tree and reads as stale.

A test run that leaves files neither tracked nor ignored changes the tree, so
its own evidence reads stale. The fix is that repo's `.gitignore`.

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

## Hooks

`hooks/hooks.json` registers two Claude Code hooks. The decisions live in
`lib/workbench/hooks.py`; `lib/wb_hook.py` only reads the event from stdin
(1 MiB at most) and prints the answer. It is kept out of `wb` so that a hook
firing on every edit does not fill the command history `--stats` reads.

**The active ticket is the one the branch names**, among tickets with
artifacts. `wb next` falls back to the most recently touched ticket; a hook
that refuses edits must not, or a checkout on `main` would be held to last
week's plan.

`PreToolUse`, on `Edit`, `Write`, `MultiEdit` and `NotebookEdit`:

| Situation | Answer |
|---|---|
| hooks off, no ticket on the branch, no plan, or an audit that never passed | allow, silently |
| the path is outside the checkout, or under `.workflow/` | allow |
| the plan passed once but no longer stands (edited since, or failed a re-audit) | deny: re-run `wb sdd audit` |
| the path is in the plan's `files`, or another audited plan claims it | allow |
| anything else | deny, naming the plan and how to extend it |

`Stop`, with a standing plan and at least one planned file changed: when the
evidence is missing or does not stand (`verify.standing`), it reports
`wb impl verify <KEY>` as a `systemMessage`. Under `"hooks": "strict"` it
blocks instead, once — a stop that already follows a block only reports, so it
cannot loop.

**Fail-open, never silent.** An internal error allows the edit and says why in
a `systemMessage`. A hook that failed closed would block every edit on one bug
and be switched off, and then it guards nothing.

**What it does not cover.** A file written through `Bash` (`sed -i`, a
redirection) never reaches these tools. `wb impl check` reads the working tree
and remains the complete check.

Switches: `WB_NO_HOOKS=1` in the environment, or `"hooks"` in
`.workflow/config.json` — `false` for off, `"strict"` for a blocking stop.

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
They are therefore allowed **only in a value that is shaped like an address**,
following a flag that takes one, and nowhere else; everything that could chain or substitute a command stays refused
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

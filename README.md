# workbench

Ticket-to-PR development workflow skills for Claude Code, with pluggable issue
trackers (Jira Cloud, Azure DevOps, GitHub Issues, or a backlog in the repo).

Ten skills, one CLI, 634 tests, no third-party dependencies.

## Two design rules

**Scripts distil, they do not relay.** A Jira issue payload is mostly noise —
`renderedFields`, `avatarUrls`, `self` links, changelog. The CLI returns a
normalised, capped summary plus a map of what else exists, so an agent spends
tokens on the ticket, not on the API's shape. A typical triage is ~1.4 KB.

**Policy lives in code, not in prompts.** Skills pick a subcommand from a closed
set. There is no `--jql`, no `--wiql`, no `--fields`, no `--url` — a free-text
flag is an invitation to invent a field name that does not exist. Depth limits,
output caps, field normalisation, quality gates and the citation audit are all
enforced by the CLI and covered by tests, so every session behaves identically.

**Git is read-only until you say otherwise.** Every command prints what it
would run and lets the user run it — a wrong computation is then a wasted paste,
not a wrong branch. `--execute` runs it instead, through an allowlist that
admits five subcommands and refuses every rewrite of history: no `reset`, no
`rebase`, no `--amend`, and no force-push in any spelling. Two switches turn it
off standing.

## Install

```
/plugin marketplace add matheusPavaneli/workbench
/plugin install workbench@workbench
```

`wb` is `python "${CLAUDE_PLUGIN_ROOT}/lib/wb.py"`. Requires Python 3.9+.

### One pass to a working setup

```sh
wb init            # proposes a config: provider from the remote, preset and flow detected
wb init --write    # writes it
wb doctor          # checks the whole chain
```

It proposes and you dispose: nothing is written without `--write`, an existing
config is never replaced without `--force`, and it never writes a credential.
Detection that cannot be trusted says so, the same way the preset does.

### With no tracker

Nine of the ten skills never touch a tracker, so needing one to reach them was
a cost with nothing behind it. Commit four lines and a clone works with no
per-machine setup at all:

```json
{ "provider": "local", "preset": "solo-saas" }
```

in `.workflow/config.json`. Then `wb task new "the thing to do" --type bug`
writes a task under `.workflow/tasks/`, and everything downstream is unchanged.

### With a tracker

One context per account, once per machine:

```sh
wb ctx add personal --provider jira \
  --base-url https://you.atlassian.net --project SAAS \
  --pat-env JIRA_TOKEN_ME --email you@example.com --preset solo-saas

export JIRA_TOKEN_ME=...        # the token stays in the environment
wb ctx use personal
wb doctor
```

GitHub needs less: `wb ctx add oss --provider github` reads `owner/repo` from
the checkout's remote and borrows the token `gh` already holds.

`wb doctor` checks the whole chain in one pass — Python, git, the resolved
context, the credential, the tracker, the commit identity, the branching flow,
the test runner, and whether `.workflow/` is about to be committed — and prints
the exact fix for anything wrong.

## The skills

| Skill | Reads | Writes |
|---|---|---|
| `triage-task` | the tracker | `triage.json` |
| `plan-change` | `triage.json` + the code | `sdd.json`, `audit.json` |
| `implement-change` | `sdd.json` | the diff, `evidence.md` |
| `review-diff` | the diff | findings |
| `write-commit` | the diff + `sdd.json` | `commit.txt` |
| `draft-pr` | `sdd.json` + `evidence.json` | `pr.md` |
| `address-review` | PR comments | `review-response.md` |
| `frame-product` | an idea | `frame.md` |
| `trace-incident` | a symptom | `incident.md`, `sdd.json` |
| `write-handover` | `sdd.json` + `triage.json` | `handover.md` |

Each runs alone; each names in its description what it consumes and produces, so
they chain without being told to. Artifacts live in `.workflow/<KEY>/`, which is
how a later skill reads an earlier one's output without it passing through the
conversation twice.

Three entry points: a ticket (`triage-task`), an idea (`frame-product`), a
symptom (`trace-incident`).

### Picking work back up

Chaining through files only pays if a new session can read the thread back.

```
$ wb status ABC-123
ABC-123  Coupon applied after the charge  [bug, jira]
  triage    ok    jira In Progress
  plan      ok    3 file(s), 4 step(s), 2 verify
  audit     ok    6 citation(s) verified
  scope     part  2 of 3 planned file(s) changed
  verify    --
  handover  --    required for bug work
  next: wb impl verify ABC-123
```

`wb next` is the shorter question — *what do I run* rather than *where does
this stand* — and works out which ticket without being told, from the branch
name, then from what was touched last:

```
$ wb next
ABC-123  (branch)  audit BLOCKED  1 of 4 citation(s) unverified
  next: fix the plan, then: wb sdd audit ABC-123
```

`wb status` with no key lists everything in flight. `--stats` has two halves:
a snapshot of where work is stuck now, and a history from a local command log
of where this repo keeps losing time — a stage that always passes on the second
attempt is invisible in the snapshot and the most expensive thing in the log.

`wb task clean <KEY>` removes one ticket's artifacts once the work is done with.
It lists and removes nothing; `--force` is a second command, because a plan and
its evidence can be produced again and a frame or a handover was written once,
by hand:

```
$ wb task clean ABC-123
/repo/.workflow/ABC-123
  triage.json
  sdd.json
  frame.md   hand-written, not regenerable

3 file(s), nothing removed
remove them: wb task clean ABC-123 --force
```

The key resolves through the same validation as every other artifact path, so
the context binding, the local backlog and the event log sitting beside the
ticket directories are not names the command avoids — they are names it cannot
produce.

Two selectors clean a checkout rather than a ticket. `--merged` takes everything
that reached a commit or a PR and has no branch left on the remote;
`--older-than 30d` takes everything untouched since then. Both halves of
`--merged` matter — "no branch names this key" on its own also describes work
that was never branched at all, which is in flight rather than finished. Either
way the listing says which stage each ticket stopped at, so a selector that
caught live work is visible before `--force` rather than after:

```
$ wb task clean --older-than 30d
/repo/.workflow/ABC-7   still at scope (BLOCKED)
  sdd.json
  triage.json

1 ticket(s), 2 file(s), nothing removed
remove them: wb task clean --older-than 30d --force
```

### Rigour proportional to risk

Ceremony has a cost that is not measured in minutes: a process too heavy for a
one-line fix gets routed around, and then it only ever sees the changes nobody
minded doing carefully. So the short route is named and computed rather than
improvised:

```
$ wb route ABC-123 --files src/util.py
ABC-123  light route  (1 file(s), no critical zone)
  1. triage     triage-task        wb task get ABC-123
  2. plan       plan-change        wb sdd audit ABC-123
  3. implement  implement-change   wb impl check ABC-123
  4. verify     implement-change   wb impl verify ABC-123
  5. commit     write-commit       wb commit check --file <path> --key ABC-123

waived by the light tier: steps, product
the floor is not waived: citations, the file list, verify and rollback still apply
```

Touch a critical zone, exceed two files, or pick up a bug ticket and the same
command returns the full eight steps, with the reason. It is a router, not an
eleventh skill: ten descriptions are already this plugin's always-on cost, and
one more to say "do less" would be the joke telling itself.


A seven-section plan for a one-line change costs more than the change, and a
gate that does not pay for itself is one people route around. `sdd audit`
computes a **tier** from the plan's own file list: at most two files, no
critical zone and no bug/support ticket waives `steps` and `product`. Citations,
the file list, `verify` and `rollback` are required at every tier, and the tier
is computed rather than declared, so a plan cannot ask for a lower bar.

`write-handover` exists because a support ticket has an audience that is not
engineering. A QA lead has to validate the fix without reading the diff, and the
person who raised it has to understand what happened without knowing the
codebase exists. It is required on bug, support and incident tickets, and the
audit fails without it.

### What it costs

Skill descriptions sit in the system prompt of every session, used or not. The
ten here total ~2.0 KB, about **500 tokens always on**, and a test asserts that
ceiling so it cannot creep. Everything else is paid only on use: a SKILL.md when
it triggers, a reference file only if that skill reads one.

Provider quirks, depth policy and the preset table are **not** in that path.
They are enforced in code and emitted as resolved output — `wb repo profile`
prints the six gates that apply rather than a table of five presets for the
model to pick a row from.

## Branching flow

Where work starts and how it reaches production is configuration, not an
assumption. One **source** branch holds the truth; zero or more **validation**
branches carry the same commits for testing.

```sh
wb flow set --source main --validation homolog --branch-pattern "feature/{key}-{slug}"
wb flow start ABC-123 --title "Checkout fails on expired coupons"
wb flow carry ABC-123 --to homolog     # the commits to cherry-pick, oldest first
```

Order is the point of `carry`: a series applied newest-first conflicts on every
commit after the first.

Every base is a remote-tracking ref, so there is no pull step — `start` fetches
and branches from `origin/<base>`, and `carry` fetches *before* it measures the
range, because measuring "what the source lacks" against stale refs carries
commits that were already merged.

### Running it

The commands above print. Add `--execute` to run them:

```sh
wb flow start ABC-123 --title "..." --execute
wb flow carry ABC-123 --to homolog --execute
wb git commit ABC-123 --execute    # the message from wb commit check, the author from the context
wb git push --execute              # first publish only
```

The printed and the executed forms are the same objects, so `--execute` cannot
run something other than what it showed. A refusal or a failure stops the series
and hands back the rest, starting at the step that failed. Preconditions are
checked before each step: a clean tree to switch branch, a working branch to
commit, and no upstream to push — recovering from a bad push onto a published
branch means a force-push, so this tool cannot reach the situation.

Turn it off standing with `WB_NO_EXECUTE=1` or `"execute": false` in
`.workflow/config.json`. See [flow.md](docs/flow.md) and
[execution.md](docs/execution.md).

## Quality presets

The preset sets the bar a plan must clear. It never lowers the floor: a unit test
for changed logic, a regression test with every bug fix, no silently swallowed
errors, no secrets in code, and a stated rollback path apply to every preset —
`prototype` included.

| Preset | For |
|---|---|
| `prototype` | no users yet; reversibility over everything |
| `solo-saas` | one operator, paying users — managed over self-hosted, money paths tested like critical infrastructure, support cost is a design criterion |
| `startup` | small team, users in production |
| `scaleup` | migrations, feature flags, staged rollout |
| `enterprise` | backwards compatibility, runbooks, cross-team blast radius |

Detected from repo evidence (`wb repo profile`), overridable with `--set`.

Detection is allowed to be wrong. It is not allowed to be wrong **silently**:
where the evidence supports more than one bar — an unreadable contributor
count, CI on a one-person repo, or a monorepo — the preset comes back marked
`LOW confidence` with the alternatives named, and `wb status` keeps saying so
until somebody settles it with `--confirm` or `--set`.

A monorepo has no single answer, so it can give a different bar to each part:

```json
{ "preset": "startup",
  "preset_paths": { "packages/billing/**": "enterprise",
                    "apps/playground/**": "prototype" } }
```

`wb repo gates <paths>` resolves the rules for the files a change actually
touches, and `wb sdd gates` prints them for the repo as a whole (or for a preset
named with `--preset`, to see what a different bar would demand). A change spanning two presets is held to the **higher** one — the
alternative is a plan that meets neither — and the audit fails a plan that
declares a preset below what its own files demand.
Rigour is not uniform inside a repo either: billing, auth, user data, migrations
and secrets raise the bar locally whatever the preset says.

## The audit

A plan states claims about the codebase. Each carries a `file:line` and the text
of that line, and `wb sdd audit` reopens every one to check the text is really
there.

```
FAIL  1/2 citation(s) unverified
  missing_file  src/validator.py:10  no such file; the path in the citation does not exist

fix the plan, not the check. Do not implement from a failed audit.
```

It is a script, deliberately, and not a second pass by the model: a model
auditing its own work confirms its own errors. `implement-change` refuses to run
on a plan whose audit did not pass.

## Command surface

```
wb init    [--write]           propose (or write) this repo's config
wb doctor  everything that has to be true, in one pass
wb route   [KEY]               the steps this change actually needs
wb next    [KEY]               the single command to run now
wb status  [KEY] | --stats     where work stands, and what to run next
wb ctx     show | list | add | use | test | record
wb task    list | get | new | done
wb repo    profile [--confirm] | zones | gates <paths>
wb sdd     audit [--rebaseline] | get | render | handover | gates
wb flow    show | start | carry | set        start, carry take --execute
wb impl    check | verify
wb review  context | gates
wb commit  convention | check
wb pr      context | check
wb git     ctx | diff | commit | push        commit, push take --execute
```

Exit codes: 2 usage, 3 config, 4 auth, 5 provider, 6 not found, 7 audit failed.

## Docs

- [configuration.md](docs/configuration.md) — contexts, matching rules, keychain, exit codes
- [providers.md](docs/providers.md) — internal schema, tracker quirks, adding a provider
- [depth-policy.md](docs/depth-policy.md) — depth, expansion handles, output caps
- [execution.md](docs/execution.md) — scope guard, verification boundary, declared environment, git façade and execution
- [CHANGELOG.md](CHANGELOG.md) — what changed, and the deprecation policy
- [status.md](docs/status.md) — the pipeline, the command history, rigour tiers, settled gates
- [flow.md](docs/flow.md) — source and validation branches, cherry-pick carrying, branch naming

These are for maintainers. Agents do not read them: the behaviour they describe
is enforced in code, and loading them into a session would pay twice for the
same guarantee.

## Tests

```sh
PYTHONPATH="lib;tests" python -m unittest discover -s tests -q   # Windows
PYTHONPATH="lib:tests" python -m unittest discover -s tests -q   # macOS, Linux
```

CI runs the suite on Linux and Windows against Python 3.9 and 3.12
(`.github/workflows/tests.yml`).

Fixtures under `tests/fixtures/` follow the vendors' published contracts, which
were checked against the documentation — endpoints, parameter names, response
shapes and link-direction semantics are verified. What they cannot cover is your
instance: custom fields, custom link types, and workflow state names. Replace
them with anonymised payloads from your own tenant to close that gap. See
[providers.md](docs/providers.md).

## License

MIT

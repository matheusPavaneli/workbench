# Status, doctor and the rigour tier

Maintainer reference for `lib/workbench/status.py`, `cli/doctor.py` and
`sdd.tier`.

## Why status exists

The whole design chains through `.workflow/<KEY>/` rather than through the
conversation, so a plan is paid for once no matter how many steps consume it.
That only pays if a *new* session can pick the thread back up. Until `wb status`
existed the state was on disk but nothing read it back, so resuming meant
opening four files and inferring — which is the cost the artifacts were supposed
to remove.

## The pipeline

Fixed, in order. Each stage names the artifact that proves it happened and the
command that produces it, so "what now" is a lookup rather than a judgement.

| Stage | Proof | Notes |
|---|---|---|
| `triage` | `triage.json`, or `frame.md` | `questions.md` is counted, not read |
| `plan` | `sdd.json` | file, step, verify and question counts |
| `audit` | `audit.json` | a `fail` verdict blocks everything after it |
| `scope` | *derived* | the working tree is the only truth about scope |
| `verify` | `evidence.json` | not offered until the audit passes |
| `handover` | `handover.md` | only where the audit already requires one |
| `commit`, `pr` | `commit.txt`, `pr.md` | |

Two rules decide what gets reported as `next`:

- **A failure outranks an unfinished stage.** A failed audit with an empty
  verify stage must report the audit, or the real problem hides behind the
  next empty box.
- **A corrupt artifact reads as absent.** Status is what a stuck session runs
  first; it must never be the thing that fails.

## `wb next`

`wb status` answers "where does this stand", which is eight stages because a
person resuming work wants the shape of it. `wb next` answers "what do I run",
in two lines, and resolves *which* ticket without being told:

1. an explicit key
2. a ticket **with artifacts on disk** whose key is spelled in the branch name —
   matched on a boundary and longest first, so `ABC-1` does not answer for a
   checkout on `feature/ABC-12-thing`
3. the most recently touched ticket
4. only then a key read out of the branch name, anchored to the start of a path
   segment — unanchored it turned `chore/bump-node-20` into `NODE-20`, and since
   an unknown key reads as untouched work rather than as an error, that hid the
   ticket actually in flight
5. otherwise exit 6, pointing at `wb task list`

Narrowest evidence first, so an argument always wins, a checkout sitting on a
ticket branch never reports a different ticket, and a guessed key never
displaces real work — it answers only when there is nothing else to answer
with. `--json` carries `key`, `origin`,
`stage`, `state`, `reason` and `command` — five fields a skill can branch on
without parsing prose.

Listing status also reports a **detected, unconfirmed preset**, because that is
the bar every plan in the list will be held to and this is the command a session
runs first.

`--stats` has two halves that answer different questions.

The **snapshot** aggregates the pipeline across tickets: where work is stuck
now. Three tickets blocked at one stage says more about the stage than about
the tickets.

The **history** comes from `lib/workbench/events.py`, one appended line per
tracked invocation under `.workflow/.events.jsonl`. The snapshot cannot tell a
plan that passed its audit first time from one that passed on the fifth,
because artifacts are overwritten in place -- and a stage that always passes on
the second attempt is invisible in the snapshot and the most expensive thing in
the log.

The log is held to three rules:

- **Outcomes, never arguments.** Group, action, exit code, duration, and a key
  if one was given. No arguments and no output: those are where a secret or a
  customer name would end up.
- **Local and capped.** It lives under the ignored `.workflow/`, is trimmed by
  rewriting at `MAX_EVENTS`, and goes nowhere. `WORKBENCH_NO_EVENTS=1` disables it.
- **Never load-bearing.** Every failure is swallowed. A log that cannot be
  written is a lost statistic, never a failed command.

Pure inspection commands are not tracked: logging every `status` would drown
the signal in the command run to look at the signal.

## doctor

`ctx test` proves a credential works. It does not prove git is present, that the
checkout will commit under the right identity, that the runner a plan's `verify`
list names exists, or that `.workflow/` is about to be committed. Those used to
surface one at a time, several commands into a session.

Checks never abort the run. A missing tracker credential must not hide a
misconfigured git author further down the list, so every check reports and the
exit code is decided at the end — `EXIT_CONFIG` if anything failed, `0` if only
warnings. `--offline` skips the tracker round trip.

## Rigour tiers

`sdd.tier(doc)` returns `light` or `standard` and the reason, computed from the
plan's own file list. `light` requires all of: at most two files, no critical
zone, a ticket type with no non-engineering audience, and at least one file.

It waives exactly two sections:

| Waived | Why it is safe |
|---|---|
| `steps` | an ordered list for a change that is one step is ceremony |
| `product` | a change with no user-visible surface moves no metric |

It never waives citations, the file list, `verify` or `rollback`. Those are what
make a plan checkable at all, and a small change is not a less checkable one.

The tier is **computed, not declared**. A `"tier": "light"` written into a plan
is ignored; the audit recomputes it. The audit also records it in `audit.json`
and prints it, because a waived section must be visible in the artifact — "this
plan has no steps" has to read as a decision, not an omission.

## Mechanically settled gates

Most quality gates are prose a reviewer weighs. A few are claims about bytes:
`review.gate_findings` settles "no secret in a committed file" and "no error
swallowed silently" by reading the added lines, and `wb review gates` reports
them as `file:line` findings with exit 7.

The design constraint is false positives, not coverage. A check that cries wolf
gets ignored, which is worse than no check, so:

- Test files are skipped: a fixture credential and a deliberate empty catch are
  the point of some tests.
- Secret patterns split in two. Issuer-shaped ones (`ghp_`, `AKIA`, `ATATT`, a
  private key header) fire unconditionally. Keyword-shaped ones
  (`token = "..."`) are suppressed by an environment lookup, a template
  variable or a placeholder.
- A swallowed-error opener is only judged when the *next added line* is its
  whole body. An `except:` whose body is not part of the diff is unknown, not
  guilty.

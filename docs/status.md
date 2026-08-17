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

`--stats` aggregates across tickets. It is a snapshot and says so: artifacts are
overwritten in place, so it reports where work is stuck now, not how often it
got stuck. Three tickets blocked at one stage says more about the stage than
about the tickets.

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

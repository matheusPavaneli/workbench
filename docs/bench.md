# Benchmark

Maintainer reference for `bench/`. The README argues that plans, audits and the
scope guard pay for themselves. This is the instrument that checks the argument,
including where it does not hold: ceremony has a price, and a benchmark that can
only report wins is advertising.

## What is measured

Defined here, before any run, so the numbers cannot choose the metrics.

| Metric | Meaning | Better |
|---|---|---|
| `hidden_pass` | 1 when every hidden acceptance test of the ticket passes, else 0 | higher |
| `out_of_scope` | files the run changed that the ticket's `expected_files` does not list; anything under `.workflow/` never counts | lower |
| `rework` | commits after the first one; a run that commits once or not at all scores 0 | lower |
| `tokens` | input + output + cache read + cache creation, from the session's `usage` | lower |
| `cost_usd` | `total_cost_usd` as the session reports it | lower |
| `wall_s` | wall time of the session, in seconds | lower |
| `tests_missing` | 1 when the run changed a logic file (a `.py` outside `tests/`) and changed nothing under `tests/`, else 0 | lower |
| `invalid_refs` | imports in the changed `.py` files that name a repo module, or a name in one, that does not exist in the run's final tree | lower |
| `unexpected_changes` | changed lines in logic files outside the functions the ticket's `expected_functions` names; a removed block counts as one line, and a logic file the ticket did not need counts every changed line | lower |

A run that ended in an error (a usage limit, a crash, a timeout) is left out
of every metric and counted in the report's header: a session cut off before
it started scores 0 tokens and a failed hidden suite, which is not the arm.

A field the session did not report is recorded as absent and left out of that
metric's summary. It is never read as zero: a missing token count that turned
into a zero would make the arm that lost it look cheap. The same holds for the
error metrics: `invalid_refs` is absent when a changed file does not parse, and
`unexpected_changes` is absent for a ticket that declares no
`expected_functions` (BN-2, whose needed code is a module the run creates).

The error metrics are deliberately narrow, so a hit is a real error and not a
matter of taste. `invalid_refs` reads imports only, with `ast`: a call through
an attribute (`stock.in_stock()`) or a path in a string is not seen; imports of
the standard library or of anything outside the repo are ignored. Test files
never count toward `unexpected_changes`: writing a test is never out of scope.

## The arms

Every ticket runs in two arms, the same number of times, on the same pinned
model:

- **plain** — `claude -p` with the ticket text and an instruction to commit.
- **workbench** — the same prompt and model, with this plugin loaded through
  `--plugin-dir`, the repo set up with `wb init --write`, the ticket recorded
  in the run's local backlog as `BN-n`, and the prompt opening with "Pick up
  ticket BN-n and take it through the workbench flow to a commit." That is how
  a user who installed workbench asks for it, and it is part of the arm, not a
  bias. A bare "Ticket BN-n." left every skill uncalled in the first smoke
  runs, so the arm measured a plain session with a plugin loaded. Each record
  lists what the session left under `.workflow/<KEY>/`, and the report counts
  the workbench runs that used the flow at all.

- **run** (on request, `--arms plain,workbench,run`) — the workbench arm's
  setup, then `wb run BN-n --until commit` with `WB_RUN=1`, the same model, the
  plugin copy and `bypassPermissions`. `wb run` starts its own sessions; the
  record sums their usage, cost and turns from `.workflow/BN-n/run.json`, and
  the wall time is the whole call. When `wb run` stops for a person (exit 8),
  the harness plays that person: it runs exactly the `wb approve` line printed
  and resumes, up to four times. Approving is the person's time, not the
  machine's, so it is not what this arm measures.

Every arm runs with a fresh home (`HOME` and `USERPROFILE`) and a fresh
`CLAUDE_CONFIG_DIR`, so none sees the
maintainer's installed plugins, settings or `CLAUDE.md`. The config dir
alone was not enough: every session of the `092a381` run loaded the owner's
`~/.claude/CLAUDE.md` through the home directory. Nor was the home, on Windows: the
6a916f1 run still loaded it. Every session now also runs with
`--setting-sources project,local`, which leaves the user source (the owner's
settings and memory) out. That was not it either: the 31a7699 transcripts show the file
loaded as *project* instructions, found by walking up from the run's repo,
which sat in the system temp dir under the home. Run dirs are now made outside
the home (`<drive>/wb-bench-tmp` on Windows, or `WB_BENCH_TMP`). A fresh config has no
stored login either, so the credential comes from the environment: either
`CLAUDE_CODE_OAUTH_TOKEN` (made once with `claude setup-token`, drawing on the
subscription's usage limit) or `ANTHROPIC_API_KEY` (billed). When both are set
the API key wins, as it does in Claude Code. Without that, the plain arm would quietly load
workbench from the user install and measure nothing. The plugin given to the
workbench arm is a temp copy without `bench/`, so the hidden tests are not
reachable through it.

## The tickets

`bench/tickets.json` holds seven tickets over a small shop in `bench/fixture/`,
each shaped to exercise one claim:

| Key | Shape | Claim it tests |
|---|---|---|
| BN-1 | bug with a thin description | triage and a regression test catch the real cause |
| BN-2 | feature crossing `shop/billing/` | the critical zone raises the bar where it should |
| BN-3 | one-file chore | the light tier keeps small work cheap |
| BN-4 | small fix beside tempting duplication | the scope guard stops the drive-by refactor |
| BN-5 | fix whose natural edit spills into a shared neighbour: `regions.zone_for` is the obvious place to accept any case, and only `tax.py` says invoices must refuse it; no visible test does | reading the callers of what you change keeps the fix where the ticket is; tempts `out_of_scope`, `unexpected_changes` and a hidden failure |
| BN-6 | ticket that names a function by its old name (`in_stock`); the live check is `stock.available`, and a deprecated copy named `in_stock` sits in `shop/legacy.py` | tracing the checkout path rather than grepping the ticket's word; tempts fixing the dead copy (`out_of_scope`, `unexpected_changes`, a hidden failure) |
| BN-7 | one-character bug in a module with no test file | the flow's test gate holds when the fix is obvious; tempts `tests_missing` |

BN-1 to BN-4 were written before the error metrics and never tempted an error;
BN-5 to BN-7 each tempt one. The first probe (`2026-09-26-17178e0`) found BN-5
and BN-6 too easy with a hint in the code or the README; both hints are gone. Whether a ticket actually tempts is only known
after a run: a tie on it is a result, not a fixture bug.

`expected_files` and `expected_functions` (`path::qualname`) are declared by
hand per ticket. Hidden tests live in
`bench/hidden/<key>/` and never enter the run's working tree: scoring runs them
from where they are, with the run's repo on `PYTHONPATH`. `tests/test_bench.py`
holds the fixture to two invariants — its visible tests pass, and every hidden
test fails on the untouched fixture — so a ticket cannot be passed by doing
nothing.

## Running it

Gated, and never part of the unit suite:

```sh
claude setup-token                                  # once; prints a long-lived token
WB_BENCH=1 CLAUDE_CODE_OAUTH_TOKEN=... python bench/run.py --smoke
WB_BENCH=1 CLAUDE_CODE_OAUTH_TOKEN=... python bench/run.py
```

`--smoke` runs BN-3 once per arm. Run it first: it is the cheap way to see that
isolation and flags behave before paying for the rest. A full run is 7 tickets ×
2 arms × 5 runs on Sonnet, 70 sessions, capped by `--cap` (default 50). The
default cap will not finish it: at the 0d3ff79 prices it covers roughly half.
Raise `--cap` knowingly, or run a subset with `--tickets`. The cap is checked
before each session; once it is reached the remaining runs are skipped and the
report counts them.

On a subscription nothing is billed: `total_cost_usd` is Claude Code's estimate,
and the cap still stops the run at that estimate, which keeps a full run from
eating the whole usage window. The report states which credential paid. A run
that hits the usage limit mid-way ends with the session's error in its record;
rerun the affected tickets with `--tickets` once the window resets.

Options: `--runs N`, `--model <id>`, `--tickets BN-1,BN-4`, `--arms plain,workbench,run`, `--cap <usd>`,
`--timeout <seconds>` per session.

## Reading the result

Each session's transcript is copied beside its record as
`<key>-<arm>-<n>.transcript<i>.jsonl`, the one account of where its turns went.
Transcripts are gitignored: they hold the whole session, tool output included.

Each run writes one JSON record to `bench/results/<date>-<commit>/`, and the run
ends with `report.md` beside them: the workbench commit, the model and the date,
then median, min and max per ticket, metric and arm. With five runs a spread is
honest where a standard deviation would not be.

**Caught errors** comes first: per ticket and error metric (`hidden_pass`
failures, `out_of_scope`, `tests_missing`, `invalid_refs`,
`unexpected_changes`), how many runs of each arm made that error, listed only
where the two arms' shares differ. It reads both ways: a row where workbench
made the error and plain did not is as much a finding as the reverse.

Each arm other than plain is compared with plain, in the tables, the caught
errors and its own loss section. The report ends with **Where workbench loses**: every ticket and metric whose
workbench median is worse than plain's, by that metric's direction. An empty
section says so in words. That section is the reason the benchmark exists; the
README may cite a result only if the report supports it.

## Results so far

[2026-09-26, commit 0d3ff79](../bench/results/2026-09-26-0d3ff79/report.md):
Sonnet, subscription, 20 sessions (three runs of BN-1 and BN-2, two of BN-3 and
BN-4; the spend cap skipped four), US$6.58 estimated. Every workbench run used
the flow. Both arms passed every hidden test with no out-of-scope file and no
rework, so quality tied; workbench lost on tokens, cost and wall time on every
ticket, at 3.6-5.6x the cost. The tickets were too easy to separate the arms on
quality: a benchmark that can show a gain needs tickets a plain session gets
wrong, which is the next step.

Run it on demand, when a change moves the cost of the flow — a new gate, a new
tier bound, a skill that grew.

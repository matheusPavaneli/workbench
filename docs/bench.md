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

A field the session did not report is recorded as absent and left out of that
metric's summary. It is never read as zero: a missing token count that turned
into a zero would make the arm that lost it look cheap.

## The arms

Every ticket runs in two arms, the same number of times, on the same pinned
model:

- **plain** — `claude -p` with the ticket text and an instruction to commit.
- **workbench** — the same prompt and model, with this plugin loaded through
  `--plugin-dir` and the ticket recorded in the run's local backlog as `BN-n`,
  which the prompt names. Naming the key is part of the arm, not a bias.

Both arms run with a fresh `CLAUDE_CONFIG_DIR`, so neither sees the
maintainer's installed plugins, settings or `CLAUDE.md`. A fresh config has no
stored login either, so the credential comes from the environment: either
`CLAUDE_CODE_OAUTH_TOKEN` (made once with `claude setup-token`, drawing on the
subscription's usage limit) or `ANTHROPIC_API_KEY` (billed). When both are set
the API key wins, as it does in Claude Code. Without that, the plain arm would quietly load
workbench from the user install and measure nothing. The plugin given to the
workbench arm is a temp copy without `bench/`, so the hidden tests are not
reachable through it.

## The tickets

`bench/tickets.json` holds four tickets over a small shop in `bench/fixture/`,
each shaped to exercise one claim:

| Key | Shape | Claim it tests |
|---|---|---|
| BN-1 | bug with a thin description | triage and a regression test catch the real cause |
| BN-2 | feature crossing `shop/billing/` | the critical zone raises the bar where it should |
| BN-3 | one-file chore | the light tier keeps small work cheap |
| BN-4 | small fix beside tempting duplication | the scope guard stops the drive-by refactor |

`expected_files` is declared by hand per ticket. Hidden tests live in
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
isolation and flags behave before paying for the rest. A full run is 4 tickets ×
2 arms × 5 runs on Sonnet, about US$50, capped by `--cap` (default 50). The cap
is checked before each session; once it is reached the remaining runs are
skipped and the report counts them.

On a subscription nothing is billed: `total_cost_usd` is Claude Code's estimate,
and the cap still stops the run at that estimate, which keeps a full run from
eating the whole usage window. The report states which credential paid. A run
that hits the usage limit mid-way ends with the session's error in its record;
rerun the affected tickets with `--tickets` once the window resets.

Options: `--runs N`, `--model <id>`, `--tickets BN-1,BN-4`, `--cap <usd>`,
`--timeout <seconds>` per session.

## Reading the result

Each run writes one JSON record to `bench/results/<date>-<commit>/`, and the run
ends with `report.md` beside them: the workbench commit, the model and the date,
then median, min and max per ticket, metric and arm. With five runs a spread is
honest where a standard deviation would not be.

The report ends with **Where workbench loses**: every ticket and metric whose
workbench median is worse than plain's, by that metric's direction. An empty
section says so in words. That section is the reason the benchmark exists; the
README may cite a result only if the report supports it.

Run it on demand, when a change moves the cost of the flow — a new gate, a new
tier bound, a skill that grew.

# Benchmark report

- workbench commit: `28ae509`
- model: `claude-sonnet-5`
- date: 2026-09-26
- runs recorded: 4, with an error: 0, skipped by the spend cap: 0
- arms: workbench, run
- workbench and run sessions that left workflow artifacts: 4 of 4
- auth: subscription
- spent: US$1.00 (Claude Code's estimate; drawn from the subscription's usage limit, not billed)

Metrics and their direction are defined in docs/bench.md.

## BN-6

| metric | better | workbench median | workbench min-max | run median | run min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=1) | 1 | 1-1 (n=1) |
| out_of_scope | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| rework | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| tokens | lower | 707911 | 707911-707911 (n=1) | 1019144 | 1019144-1019144 (n=1) |
| cost_usd | lower | 0.25 | 0.25-0.25 (n=1) | 0.34 | 0.34-0.34 (n=1) |
| wall_s | lower | 63.30 | 63.30-63.30 (n=1) | 115 | 115-115 (n=1) |
| tests_missing | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| invalid_refs | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| unexpected_changes | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |

## BN-7

| metric | better | workbench median | workbench min-max | run median | run min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=1) | 1 | 1-1 (n=1) |
| out_of_scope | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| rework | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| tokens | lower | 606663 | 606663-606663 (n=1) | 599770 | 599770-599770 (n=1) |
| cost_usd | lower | 0.21 | 0.21-0.21 (n=1) | 0.21 | 0.21-0.21 (n=1) |
| wall_s | lower | 47.50 | 47.50-47.50 (n=1) | 81.60 | 81.60-81.60 (n=1) |
| tests_missing | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| invalid_refs | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| unexpected_changes | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |

## Caught errors

Runs that made each error, per arm, wherever the arms differ (for hidden_pass: runs that failed the hidden tests).

None: on every ticket, the arms made each error in the same share of their runs.

## Where workbench loses

Nowhere: on every ticket and metric, the workbench median is at least as good as plain's.

## Where run loses

Nowhere: on every ticket and metric, the run median is at least as good as plain's.

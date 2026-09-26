# Benchmark report

A probe, not a result: one run per arm, plain on BN-5 to BN-7 and workbench and run on BN-7 only, from two calls into one directory; this report is rebuilt over all five records.

- workbench commit: `17178e0`
- model: `claude-sonnet-5`
- date: 2026-09-26
- runs recorded: 5, with an error: 0, skipped by the spend cap: 0
- arms: plain, workbench, run
- workbench and run sessions that left workflow artifacts: 2 of 2
- auth: subscription
- spent: US$1.79 (Claude Code's estimate; drawn from the subscription's usage limit, not billed)

Metrics and their direction are defined in docs/bench.md.

## BN-5

| metric | better | plain median | plain min-max | workbench median | workbench min-max | run median | run min-max |
|---|---|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=1) | - | - | - | - |
| out_of_scope | lower | 0 | 0-0 (n=1) | - | - | - | - |
| rework | lower | 0 | 0-0 (n=1) | - | - | - | - |
| tokens | lower | 368983 | 368983-368983 (n=1) | - | - | - | - |
| cost_usd | lower | 0.16 | 0.16-0.16 (n=1) | - | - | - | - |
| wall_s | lower | 32.30 | 32.30-32.30 (n=1) | - | - | - | - |
| tests_missing | lower | 0 | 0-0 (n=1) | - | - | - | - |
| invalid_refs | lower | 0 | 0-0 (n=1) | - | - | - | - |
| unexpected_changes | lower | 0 | 0-0 (n=1) | - | - | - | - |

## BN-6

| metric | better | plain median | plain min-max | workbench median | workbench min-max | run median | run min-max |
|---|---|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=1) | - | - | - | - |
| out_of_scope | lower | 0 | 0-0 (n=1) | - | - | - | - |
| rework | lower | 0 | 0-0 (n=1) | - | - | - | - |
| tokens | lower | 499994 | 499994-499994 (n=1) | - | - | - | - |
| cost_usd | lower | 0.17 | 0.17-0.17 (n=1) | - | - | - | - |
| wall_s | lower | 53.50 | 53.50-53.50 (n=1) | - | - | - | - |
| tests_missing | lower | 0 | 0-0 (n=1) | - | - | - | - |
| invalid_refs | lower | 0 | 0-0 (n=1) | - | - | - | - |
| unexpected_changes | lower | 0 | 0-0 (n=1) | - | - | - | - |

## BN-7

| metric | better | plain median | plain min-max | workbench median | workbench min-max | run median | run min-max |
|---|---|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=1) | 1 | 1-1 (n=1) | 1 | 1-1 (n=1) |
| out_of_scope | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| rework | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| tokens | lower | 206945 | 206945-206945 (n=1) | 1852913 | 1852913-1852913 (n=1) | 2324795 | 2324795-2324795 (n=1) |
| cost_usd | lower | 0.09 | 0.09-0.09 (n=1) | 0.58 | 0.58-0.58 (n=1) | 0.79 | 0.79-0.79 (n=1) |
| wall_s | lower | 19.60 | 19.60-19.60 (n=1) | 147.20 | 147.20-147.20 (n=1) | 386.70 | 386.70-386.70 (n=1) |
| tests_missing | lower | 1 | 1-1 (n=1) | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| invalid_refs | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| unexpected_changes | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |

## Caught errors

Runs that made each error, per arm, wherever the arms differ (for hidden_pass: runs that failed the hidden tests).

- BN-7 tests_missing: plain 1 of 1 runs, workbench 0 of 1
- BN-7 tests_missing: plain 1 of 1 runs, run 0 of 1

## Where workbench loses

- BN-7 tokens: workbench median 1852913 against plain 206945 (lower is better)
- BN-7 cost_usd: workbench median 0.58 against plain 0.09 (lower is better)
- BN-7 wall_s: workbench median 147.20 against plain 19.60 (lower is better)

## Where run loses

- BN-7 tokens: run median 2324795 against plain 206945 (lower is better)
- BN-7 cost_usd: run median 0.79 against plain 0.09 (lower is better)
- BN-7 wall_s: run median 386.70 against plain 19.60 (lower is better)

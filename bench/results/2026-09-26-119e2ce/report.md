# Benchmark report

A probe, not a result: one run per arm, from two calls into one directory, rebuilt over all six records. The light path's wb finish failed on these runs (a flow source recorded as main on a repo on master), so the workbench and run costs here are the defect's, not the path's.

- workbench commit: `119e2ce`
- model: `claude-sonnet-5`
- date: 2026-09-26
- runs recorded: 6, with an error: 1, skipped by the spend cap: 0
- arms: plain, workbench, run
- workbench and run sessions that left workflow artifacts: 4 of 4
- auth: subscription
- spent: US$2.49 (Claude Code's estimate; drawn from the subscription's usage limit, not billed)

Metrics and their direction are defined in docs/bench.md.

## BN-5

| metric | better | plain median | plain min-max | workbench median | workbench min-max | run median | run min-max |
|---|---|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=1) | - | - | - | - |
| out_of_scope | lower | 0 | 0-0 (n=1) | - | - | - | - |
| rework | lower | 0 | 0-0 (n=1) | - | - | - | - |
| tokens | lower | 359408 | 359408-359408 (n=1) | - | - | - | - |
| cost_usd | lower | 0.14 | 0.14-0.14 (n=1) | - | - | - | - |
| wall_s | lower | 31.50 | 31.50-31.50 (n=1) | - | - | - | - |
| tests_missing | lower | 0 | 0-0 (n=1) | - | - | - | - |
| invalid_refs | lower | 0 | 0-0 (n=1) | - | - | - | - |
| unexpected_changes | lower | 0 | 0-0 (n=1) | - | - | - | - |

## BN-6

| metric | better | plain median | plain min-max | workbench median | workbench min-max | run median | run min-max |
|---|---|---|---|---|---|---|---|
| hidden_pass | higher | 0 | 0-0 (n=1) | 1 | 1-1 (n=1) | 1 | 1-1 (n=1) |
| out_of_scope | lower | 1 | 1-1 (n=1) | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| rework | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| tokens | lower | 172042 | 172042-172042 (n=1) | 2014698 | 2014698-2014698 (n=1) | 1332293 | 1332293-1332293 (n=1) |
| cost_usd | lower | 0.08 | 0.08-0.08 (n=1) | 0.61 | 0.61-0.61 (n=1) | 0.43 | 0.43-0.43 (n=1) |
| wall_s | lower | 12.80 | 12.80-12.80 (n=1) | 155.30 | 155.30-155.30 (n=1) | 230.90 | 230.90-230.90 (n=1) |
| tests_missing | lower | 1 | 1-1 (n=1) | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| invalid_refs | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| unexpected_changes | lower | 1 | 1-1 (n=1) | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |

## BN-7

| metric | better | plain median | plain min-max | workbench median | workbench min-max | run median | run min-max |
|---|---|---|---|---|---|---|---|
| hidden_pass | higher | - | - | 1 | 1-1 (n=1) | 1 | 1-1 (n=1) |
| out_of_scope | lower | - | - | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| rework | lower | - | - | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| tokens | lower | - | - | 1266718 | 1266718-1266718 (n=1) | 2354045 | 2354045-2354045 (n=1) |
| cost_usd | lower | - | - | 0.44 | 0.44-0.44 (n=1) | 0.78 | 0.78-0.78 (n=1) |
| wall_s | lower | - | - | 184.40 | 184.40-184.40 (n=1) | 166.50 | 166.50-166.50 (n=1) |
| tests_missing | lower | - | - | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| invalid_refs | lower | - | - | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| unexpected_changes | lower | - | - | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |

## Caught errors

Runs that made each error, per arm, wherever the arms differ (for hidden_pass: runs that failed the hidden tests).

- BN-6 hidden_pass: plain 1 of 1 runs, workbench 0 of 1
- BN-6 out_of_scope: plain 1 of 1 runs, workbench 0 of 1
- BN-6 tests_missing: plain 1 of 1 runs, workbench 0 of 1
- BN-6 unexpected_changes: plain 1 of 1 runs, workbench 0 of 1
- BN-6 hidden_pass: plain 1 of 1 runs, run 0 of 1
- BN-6 out_of_scope: plain 1 of 1 runs, run 0 of 1
- BN-6 tests_missing: plain 1 of 1 runs, run 0 of 1
- BN-6 unexpected_changes: plain 1 of 1 runs, run 0 of 1

## Where workbench loses

- BN-6 tokens: workbench median 2014698 against plain 172042 (lower is better)
- BN-6 cost_usd: workbench median 0.61 against plain 0.08 (lower is better)
- BN-6 wall_s: workbench median 155.30 against plain 12.80 (lower is better)

## Where run loses

- BN-6 tokens: run median 1332293 against plain 172042 (lower is better)
- BN-6 cost_usd: run median 0.43 against plain 0.08 (lower is better)
- BN-6 wall_s: run median 230.90 against plain 12.80 (lower is better)

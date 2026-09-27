# Benchmark report

**Not citable.** The owner's user memory still loaded in every session (the fresh home did not keep it out on Windows; the next commit passes `--setting-sources project,local`), and the subscription's five-hour limit cut both BN-7 run-2 sessions off; those two are left out of every metric. BN-4 took the standard route by design (user-data zone).

- workbench commit: `6a916f1`
- model: `claude-sonnet-5`
- date: 2026-09-26
- runs recorded: 16, with an error (left out of every metric): 2, skipped by the spend cap: 0
- arms: plain, workbench
- workbench and run sessions that left workflow artifacts: 7 of 8
- auth: subscription
- spent: US$2.87 (Claude Code's estimate; drawn from the subscription's usage limit, not billed)

Metrics and their direction are defined in docs/bench.md.

## BN-3

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=2) | 1 | 1-1 (n=2) |
| out_of_scope | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| rework | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| tokens | lower | 208278.50 | 208010-208547 (n=2) | 436748.50 | 415287-458210 (n=2) |
| cost_usd | lower | 0.10 | 0.10-0.10 (n=2) | 0.17 | 0.16-0.18 (n=2) |
| wall_s | lower | 22.65 | 21.20-24.10 (n=2) | 51.15 | 43.10-59.20 (n=2) |
| tests_missing | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |
| invalid_refs | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| unexpected_changes | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |

## BN-4

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=2) | 1 | 1-1 (n=2) |
| out_of_scope | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| rework | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| tokens | lower | 226424 | 208941-243907 (n=2) | 1814446 | 1186749-2442143 (n=2) |
| cost_usd | lower | 0.10 | 0.10-0.10 (n=2) | 0.58 | 0.42-0.74 (n=2) |
| wall_s | lower | 42.85 | 32.70-53 (n=2) | 194.10 | 99.10-289.10 (n=2) |
| tests_missing | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |
| invalid_refs | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| unexpected_changes | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |

## BN-6

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 0 | 0-0 (n=2) | 1 | 1-1 (n=2) |
| out_of_scope | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |
| rework | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| tokens | lower | 189607 | 172038-207176 (n=2) | 620128.50 | 614783-625474 (n=2) |
| cost_usd | lower | 0.09 | 0.08-0.09 (n=2) | 0.23 | 0.22-0.23 (n=2) |
| wall_s | lower | 15.50 | 12.60-18.40 (n=2) | 67.70 | 66.30-69.10 (n=2) |
| tests_missing | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |
| invalid_refs | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| unexpected_changes | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |

## BN-7

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=1) | 1 | 1-1 (n=1) |
| out_of_scope | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| rework | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| tokens | lower | 244265 | 244265-244265 (n=1) | 410030 | 410030-410030 (n=1) |
| cost_usd | lower | 0.11 | 0.11-0.11 (n=1) | 0.16 | 0.16-0.16 (n=1) |
| wall_s | lower | 45.10 | 45.10-45.10 (n=1) | 64.30 | 64.30-64.30 (n=1) |
| tests_missing | lower | 1 | 1-1 (n=1) | 0 | 0-0 (n=1) |
| invalid_refs | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |
| unexpected_changes | lower | 0 | 0-0 (n=1) | 0 | 0-0 (n=1) |

## Caught errors

Runs that made each error, per arm, wherever the arms differ (for hidden_pass: runs that failed the hidden tests).

- BN-3 tests_missing: plain 2 of 2 runs, workbench 0 of 2
- BN-4 tests_missing: plain 2 of 2 runs, workbench 0 of 2
- BN-6 hidden_pass: plain 2 of 2 runs, workbench 0 of 2
- BN-6 out_of_scope: plain 2 of 2 runs, workbench 0 of 2
- BN-6 tests_missing: plain 2 of 2 runs, workbench 0 of 2
- BN-6 unexpected_changes: plain 2 of 2 runs, workbench 0 of 2
- BN-7 tests_missing: plain 1 of 1 runs, workbench 0 of 1

## Where workbench loses

- BN-3 tokens: workbench median 436748.50 against plain 208278.50 (lower is better)
- BN-3 cost_usd: workbench median 0.17 against plain 0.10 (lower is better)
- BN-3 wall_s: workbench median 51.15 against plain 22.65 (lower is better)
- BN-4 tokens: workbench median 1814446 against plain 226424 (lower is better)
- BN-4 cost_usd: workbench median 0.58 against plain 0.10 (lower is better)
- BN-4 wall_s: workbench median 194.10 against plain 42.85 (lower is better)
- BN-6 tokens: workbench median 620128.50 against plain 189607 (lower is better)
- BN-6 cost_usd: workbench median 0.23 against plain 0.09 (lower is better)
- BN-6 wall_s: workbench median 67.70 against plain 15.50 (lower is better)
- BN-7 tokens: workbench median 410030 against plain 244265 (lower is better)
- BN-7 cost_usd: workbench median 0.16 against plain 0.11 (lower is better)
- BN-7 wall_s: workbench median 64.30 against plain 45.10 (lower is better)

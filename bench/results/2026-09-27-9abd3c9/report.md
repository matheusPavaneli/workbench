# Benchmark report

- workbench commit: `9abd3c9`
- model: `claude-sonnet-5`
- date: 2026-09-27
- runs recorded: 16, with an error (left out of every metric): 0, skipped by the spend cap: 0
- arms: plain, workbench
- workbench and run sessions that left workflow artifacts: 8 of 8
- auth: subscription
- spent: US$3.00 (Claude Code's estimate; drawn from the subscription's usage limit, not billed)

Metrics and their direction are defined in docs/bench.md.

## BN-3

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=2) | 1 | 1-1 (n=2) |
| out_of_scope | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| rework | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| tokens | lower | 241053 | 240594-241512 (n=2) | 507094.50 | 487466-526723 (n=2) |
| cost_usd | lower | 0.10 | 0.10-0.11 (n=2) | 0.18 | 0.18-0.19 (n=2) |
| wall_s | lower | 22.50 | 22-23 (n=2) | 40.55 | 36.80-44.30 (n=2) |
| tests_missing | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |
| invalid_refs | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| unexpected_changes | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |

## BN-4

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=2) | 1 | 1-1 (n=2) |
| out_of_scope | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| rework | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| tokens | lower | 259039 | 241040-277038 (n=2) | 1741258 | 1599647-1882869 (n=2) |
| cost_usd | lower | 0.11 | 0.10-0.11 (n=2) | 0.57 | 0.54-0.61 (n=2) |
| wall_s | lower | 27.50 | 22.40-32.60 (n=2) | 159.90 | 150.60-169.20 (n=2) |
| tests_missing | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |
| invalid_refs | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| unexpected_changes | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |

## BN-6

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 0 | 0-0 (n=2) | 1 | 1-1 (n=2) |
| out_of_scope | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |
| rework | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| tokens | lower | 187308 | 170006-204610 (n=2) | 528356 | 490500-566212 (n=2) |
| cost_usd | lower | 0.08 | 0.08-0.09 (n=2) | 0.20 | 0.19-0.20 (n=2) |
| wall_s | lower | 16.25 | 15.90-16.60 (n=2) | 47.20 | 46.10-48.30 (n=2) |
| tests_missing | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |
| invalid_refs | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| unexpected_changes | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |

## BN-7

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=2) | 1 | 1-1 (n=2) |
| out_of_scope | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| rework | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| tokens | lower | 239563.50 | 204389-274738 (n=2) | 404752 | 404410-405094 (n=2) |
| cost_usd | lower | 0.10 | 0.09-0.11 (n=2) | 0.15 | 0.15-0.15 (n=2) |
| wall_s | lower | 23 | 20.30-25.70 (n=2) | 34.90 | 31.20-38.60 (n=2) |
| tests_missing | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |
| invalid_refs | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| unexpected_changes | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |

## Caught errors

Runs that made each error, per arm, wherever the arms differ (for hidden_pass: runs that failed the hidden tests).

- BN-3 tests_missing: plain 2 of 2 runs, workbench 0 of 2
- BN-4 tests_missing: plain 2 of 2 runs, workbench 0 of 2
- BN-6 hidden_pass: plain 2 of 2 runs, workbench 0 of 2
- BN-6 out_of_scope: plain 2 of 2 runs, workbench 0 of 2
- BN-6 tests_missing: plain 2 of 2 runs, workbench 0 of 2
- BN-6 unexpected_changes: plain 2 of 2 runs, workbench 0 of 2
- BN-7 tests_missing: plain 2 of 2 runs, workbench 0 of 2

## Where workbench loses

- BN-3 tokens: workbench median 507094.50 against plain 241053 (lower is better)
- BN-3 cost_usd: workbench median 0.18 against plain 0.10 (lower is better)
- BN-3 wall_s: workbench median 40.55 against plain 22.50 (lower is better)
- BN-4 tokens: workbench median 1741258 against plain 259039 (lower is better)
- BN-4 cost_usd: workbench median 0.57 against plain 0.11 (lower is better)
- BN-4 wall_s: workbench median 159.90 against plain 27.50 (lower is better)
- BN-6 tokens: workbench median 528356 against plain 187308 (lower is better)
- BN-6 cost_usd: workbench median 0.20 against plain 0.08 (lower is better)
- BN-6 wall_s: workbench median 47.20 against plain 16.25 (lower is better)
- BN-7 tokens: workbench median 404752 against plain 239563.50 (lower is better)
- BN-7 cost_usd: workbench median 0.15 against plain 0.10 (lower is better)
- BN-7 wall_s: workbench median 34.90 against plain 23 (lower is better)

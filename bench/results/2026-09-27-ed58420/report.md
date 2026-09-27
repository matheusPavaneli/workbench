# Benchmark report

- workbench commit: `ed58420`
- model: `claude-sonnet-5`
- date: 2026-09-27
- runs recorded: 4, with an error (left out of every metric): 0, skipped by the spend cap: 0
- arms: plain, workbench
- workbench and run sessions that left workflow artifacts: 2 of 2
- auth: subscription
- spent: US$1.11 (Claude Code's estimate; drawn from the subscription's usage limit, not billed)

Metrics and their direction are defined in docs/bench.md.

## BN-4

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=2) | 1 | 1-1 (n=2) |
| out_of_scope | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| rework | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| tokens | lower | 241866 | 241750-241982 (n=2) | 1280390 | 999730-1561050 (n=2) |
| cost_usd | lower | 0.10 | 0.10-0.10 (n=2) | 0.45 | 0.39-0.51 (n=2) |
| wall_s | lower | 25.85 | 25.30-26.40 (n=2) | 134.95 | 117.60-152.30 (n=2) |
| tests_missing | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |
| invalid_refs | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| unexpected_changes | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |

## Caught errors

Runs that made each error, per arm, wherever the arms differ (for hidden_pass: runs that failed the hidden tests).

- BN-4 tests_missing: plain 2 of 2 runs, workbench 0 of 2

## Where workbench loses

- BN-4 tokens: workbench median 1280390 against plain 241866 (lower is better)
- BN-4 cost_usd: workbench median 0.45 against plain 0.10 (lower is better)
- BN-4 wall_s: workbench median 134.95 against plain 25.85 (lower is better)

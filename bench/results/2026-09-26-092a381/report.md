# Benchmark report

**Not citable.** Every session of this run, in both arms, loaded the owner's `~/.claude/CLAUDE.md` through the home directory; the next commit isolates the home. Kept as the run that found it. BN-4 took the standard route by design: `shop/users.py` is in the user-data zone.

- workbench commit: `092a381`
- model: `claude-sonnet-5`
- date: 2026-09-26
- runs recorded: 16, with an error: 0, skipped by the spend cap: 0
- arms: plain, workbench
- workbench and run sessions that left workflow artifacts: 8 of 8
- auth: subscription
- spent: US$3.15 (Claude Code's estimate; drawn from the subscription's usage limit, not billed)

Metrics and their direction are defined in docs/bench.md.

## BN-3

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=2) | 1 | 1-1 (n=2) |
| out_of_scope | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| rework | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| tokens | lower | 208371.50 | 208189-208554 (n=2) | 587645 | 533875-641415 (n=2) |
| cost_usd | lower | 0.10 | 0.10-0.10 (n=2) | 0.22 | 0.19-0.24 (n=2) |
| wall_s | lower | 18.35 | 18.10-18.60 (n=2) | 52.75 | 39.20-66.30 (n=2) |
| tests_missing | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |
| invalid_refs | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| unexpected_changes | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |

## BN-4

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=2) | 1 | 1-1 (n=2) |
| out_of_scope | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| rework | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| tokens | lower | 261737 | 243574-279900 (n=2) | 1882724 | 1582112-2183336 (n=2) |
| cost_usd | lower | 0.11 | 0.10-0.11 (n=2) | 0.61 | 0.53-0.69 (n=2) |
| wall_s | lower | 20.65 | 20.50-20.80 (n=2) | 216.60 | 125.40-307.80 (n=2) |
| tests_missing | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |
| invalid_refs | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| unexpected_changes | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |

## BN-6

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 0 | 0-0 (n=2) | 1 | 1-1 (n=2) |
| out_of_scope | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |
| rework | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| tokens | lower | 207575 | 171819-243331 (n=2) | 534364 | 528978-539750 (n=2) |
| cost_usd | lower | 0.09 | 0.08-0.11 (n=2) | 0.19 | 0.19-0.20 (n=2) |
| wall_s | lower | 18 | 12.80-23.20 (n=2) | 44.15 | 43.50-44.80 (n=2) |
| tests_missing | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |
| invalid_refs | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| unexpected_changes | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |

## BN-7

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=2) | 1 | 1-1 (n=2) |
| out_of_scope | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| rework | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| tokens | lower | 206872 | 206807-206937 (n=2) | 431743.50 | 410719-452768 (n=2) |
| cost_usd | lower | 0.09 | 0.09-0.09 (n=2) | 0.16 | 0.16-0.17 (n=2) |
| wall_s | lower | 25.50 | 14.90-36.10 (n=2) | 37.90 | 33-42.80 (n=2) |
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

- BN-3 tokens: workbench median 587645 against plain 208371.50 (lower is better)
- BN-3 cost_usd: workbench median 0.22 against plain 0.10 (lower is better)
- BN-3 wall_s: workbench median 52.75 against plain 18.35 (lower is better)
- BN-4 tokens: workbench median 1882724 against plain 261737 (lower is better)
- BN-4 cost_usd: workbench median 0.61 against plain 0.11 (lower is better)
- BN-4 wall_s: workbench median 216.60 against plain 20.65 (lower is better)
- BN-6 tokens: workbench median 534364 against plain 207575 (lower is better)
- BN-6 cost_usd: workbench median 0.19 against plain 0.09 (lower is better)
- BN-6 wall_s: workbench median 44.15 against plain 18 (lower is better)
- BN-7 tokens: workbench median 431743.50 against plain 206872 (lower is better)
- BN-7 cost_usd: workbench median 0.16 against plain 0.09 (lower is better)
- BN-7 wall_s: workbench median 37.90 against plain 25.50 (lower is better)

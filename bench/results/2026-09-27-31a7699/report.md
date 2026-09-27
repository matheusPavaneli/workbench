# Benchmark report

**Not citable.** Every session, in both arms, loaded the owner's `~/.claude/CLAUDE.md` as *project* instructions: the run dirs sat in the system temp dir under the home, and the agent CLI reads `.claude/CLAUDE.md` in every directory above the repo. The next commit makes run dirs outside the home. BN-4 took the standard route by design (user-data zone).

- workbench commit: `31a7699`
- model: `claude-sonnet-5`
- date: 2026-09-27
- runs recorded: 16, with an error (left out of every metric): 0, skipped by the spend cap: 0
- arms: plain, workbench
- workbench and run sessions that left workflow artifacts: 8 of 8
- auth: subscription
- spent: US$3.06 (Claude Code's estimate; drawn from the subscription's usage limit, not billed)

Metrics and their direction are defined in docs/bench.md.

## BN-3

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=2) | 1 | 1-1 (n=2) |
| out_of_scope | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| rework | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| tokens | lower | 208397.50 | 208176-208619 (n=2) | 439922.50 | 413433-466412 (n=2) |
| cost_usd | lower | 0.10 | 0.10-0.10 (n=2) | 0.18 | 0.16-0.19 (n=2) |
| wall_s | lower | 22.50 | 22.40-22.60 (n=2) | 46.45 | 40.40-52.50 (n=2) |
| tests_missing | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |
| invalid_refs | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| unexpected_changes | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |

## BN-4

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=2) | 1 | 1-1 (n=2) |
| out_of_scope | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| rework | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| tokens | lower | 282785 | 244448-321122 (n=2) | 1607920.50 | 1256414-1959427 (n=2) |
| cost_usd | lower | 0.12 | 0.10-0.13 (n=2) | 0.54 | 0.46-0.62 (n=2) |
| wall_s | lower | 29.20 | 21.30-37.10 (n=2) | 141.30 | 139.80-142.80 (n=2) |
| tests_missing | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |
| invalid_refs | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| unexpected_changes | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |

## BN-6

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 0 | 0-0 (n=2) | 0.50 | 0-1 (n=2) |
| out_of_scope | lower | 1 | 1-1 (n=2) | 1 | 0-2 (n=2) |
| rework | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| tokens | lower | 189770.50 | 172181-207360 (n=2) | 693229.50 | 576291-810168 (n=2) |
| cost_usd | lower | 0.09 | 0.08-0.09 (n=2) | 0.25 | 0.21-0.29 (n=2) |
| wall_s | lower | 17.05 | 16.10-18 (n=2) | 70.95 | 55.70-86.20 (n=2) |
| tests_missing | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |
| invalid_refs | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| unexpected_changes | lower | 1 | 1-1 (n=2) | 0.50 | 0-1 (n=2) |

## BN-7

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=2) | 1 | 1-1 (n=2) |
| out_of_scope | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| rework | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| tokens | lower | 224487.50 | 206886-242089 (n=2) | 431502 | 408991-454013 (n=2) |
| cost_usd | lower | 0.10 | 0.09-0.10 (n=2) | 0.16 | 0.15-0.17 (n=2) |
| wall_s | lower | 17.80 | 16.70-18.90 (n=2) | 37.15 | 31.10-43.20 (n=2) |
| tests_missing | lower | 1 | 1-1 (n=2) | 0 | 0-0 (n=2) |
| invalid_refs | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| unexpected_changes | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |

## Caught errors

Runs that made each error, per arm, wherever the arms differ (for hidden_pass: runs that failed the hidden tests).

- BN-3 tests_missing: plain 2 of 2 runs, workbench 0 of 2
- BN-4 tests_missing: plain 2 of 2 runs, workbench 0 of 2
- BN-6 hidden_pass: plain 2 of 2 runs, workbench 1 of 2
- BN-6 out_of_scope: plain 2 of 2 runs, workbench 1 of 2
- BN-6 tests_missing: plain 2 of 2 runs, workbench 0 of 2
- BN-6 unexpected_changes: plain 2 of 2 runs, workbench 1 of 2
- BN-7 tests_missing: plain 2 of 2 runs, workbench 0 of 2

## Where workbench loses

- BN-3 tokens: workbench median 439922.50 against plain 208397.50 (lower is better)
- BN-3 cost_usd: workbench median 0.18 against plain 0.10 (lower is better)
- BN-3 wall_s: workbench median 46.45 against plain 22.50 (lower is better)
- BN-4 tokens: workbench median 1607920.50 against plain 282785 (lower is better)
- BN-4 cost_usd: workbench median 0.54 against plain 0.12 (lower is better)
- BN-4 wall_s: workbench median 141.30 against plain 29.20 (lower is better)
- BN-6 tokens: workbench median 693229.50 against plain 189770.50 (lower is better)
- BN-6 cost_usd: workbench median 0.25 against plain 0.09 (lower is better)
- BN-6 wall_s: workbench median 70.95 against plain 17.05 (lower is better)
- BN-7 tokens: workbench median 431502 against plain 224487.50 (lower is better)
- BN-7 cost_usd: workbench median 0.16 against plain 0.10 (lower is better)
- BN-7 wall_s: workbench median 37.15 against plain 17.80 (lower is better)

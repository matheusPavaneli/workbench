# Benchmark report

- workbench commit: `0d3ff79`
- model: `claude-sonnet-5`
- date: 2026-09-26
- runs recorded: 20, with an error: 0, skipped by the spend cap: 4
- workbench runs that left workflow artifacts: 10 of 10
- auth: subscription
- spent: US$6.58 (Claude Code's estimate; drawn from the subscription's usage limit, not billed)

Metrics and their direction are defined in docs/bench.md.

## BN-1

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=3) | 1 | 1-1 (n=3) |
| out_of_scope | lower | 0 | 0-0 (n=3) | 0 | 0-0 (n=3) |
| rework | lower | 0 | 0-0 (n=3) | 0 | 0-0 (n=3) |
| tokens | lower | 321230 | 321056-362024 (n=3) | 1603034 | 1110766-2068632 (n=3) |
| cost_usd | lower | 0.13 | 0.13-0.15 (n=3) | 0.53 | 0.38-0.67 (n=3) |
| wall_s | lower | 27.80 | 20.60-92.50 (n=3) | 159.80 | 128.40-178.90 (n=3) |

## BN-2

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=3) | 1 | 1-1 (n=3) |
| out_of_scope | lower | 0 | 0-0 (n=3) | 0 | 0-0 (n=3) |
| rework | lower | 0 | 0-0 (n=3) | 0 | 0-0 (n=3) |
| tokens | lower | 372975 | 325031-402134 (n=3) | 1759550 | 1754540-2117923 (n=3) |
| cost_usd | lower | 0.16 | 0.14-0.16 (n=3) | 0.59 | 0.58-0.68 (n=3) |
| wall_s | lower | 40.90 | 22.60-61.50 (n=3) | 205.80 | 149.60-206 (n=3) |

## BN-3

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=2) | 1 | 1-1 (n=2) |
| out_of_scope | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| rework | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| tokens | lower | 208102.50 | 207724-208481 (n=2) | 1625720.50 | 1128817-2122624 (n=2) |
| cost_usd | lower | 0.10 | 0.09-0.10 (n=2) | 0.53 | 0.38-0.68 (n=2) |
| wall_s | lower | 18.50 | 18.40-18.60 (n=2) | 142.95 | 96.30-189.60 (n=2) |

## BN-4

| metric | better | plain median | plain min-max | workbench median | workbench min-max |
|---|---|---|---|---|---|
| hidden_pass | higher | 1 | 1-1 (n=2) | 1 | 1-1 (n=2) |
| out_of_scope | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| rework | lower | 0 | 0-0 (n=2) | 0 | 0-0 (n=2) |
| tokens | lower | 263023.50 | 244996-281051 (n=2) | 1168698.50 | 1138495-1198902 (n=2) |
| cost_usd | lower | 0.11 | 0.11-0.12 (n=2) | 0.40 | 0.39-0.41 (n=2) |
| wall_s | lower | 30.70 | 20.80-40.60 (n=2) | 104.45 | 99.10-109.80 (n=2) |

## Where workbench loses

- BN-1 tokens: workbench median 1603034 against plain 321230 (lower is better)
- BN-1 cost_usd: workbench median 0.53 against plain 0.13 (lower is better)
- BN-1 wall_s: workbench median 159.80 against plain 27.80 (lower is better)
- BN-2 tokens: workbench median 1759550 against plain 372975 (lower is better)
- BN-2 cost_usd: workbench median 0.59 against plain 0.16 (lower is better)
- BN-2 wall_s: workbench median 205.80 against plain 40.90 (lower is better)
- BN-3 tokens: workbench median 1625720.50 against plain 208102.50 (lower is better)
- BN-3 cost_usd: workbench median 0.53 against plain 0.10 (lower is better)
- BN-3 wall_s: workbench median 142.95 against plain 18.50 (lower is better)
- BN-4 tokens: workbench median 1168698.50 against plain 263023.50 (lower is better)
- BN-4 cost_usd: workbench median 0.40 against plain 0.11 (lower is better)
- BN-4 wall_s: workbench median 104.45 against plain 30.70 (lower is better)

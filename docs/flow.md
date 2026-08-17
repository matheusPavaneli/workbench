# Branching flow

Teams do not agree on how work reaches production, so this is configuration
rather than an assumption. Two roles cover the shapes that actually occur:

- **source** — the branch that holds the truth. Work starts here and gets a PR
  here. Exactly one.
- **validation** — a branch that carries the same commits so they can be tested
  in an environment. Zero or more.

## The shape that prompted it

```
branch from main -> PR to main, left open
branch from homolog -> cherry-pick those commits -> PR to homolog -> tested in beta
the main PR merges once beta passes
```

```sh
wb flow set --source main --validation homolog --branch-pattern "feature/{key}-{slug}"
```

Trunk-based work is the same model with no validation targets. Gitflow is the
same model with `develop` as source and `release/*` handled by hand — the tool
does not model release trains, and pretending otherwise would be worse than
saying so.

## Commands

```sh
wb flow show                              # resolved flow, and what this branch is doing in it
wb flow start ABC-123 --title "..."       # branch name + base, as a git command to run
wb flow carry ABC-123 --to homolog        # the commits to cherry-pick, oldest first
wb pr context ABC-123 --target homolog    # PR inputs for the validation PR
```

`flow carry` computes the range with `git log --reverse <source>..<branch>`.
**Order is the point.** A series applied newest-first conflicts on everything
after the first commit, and reconstructing the right order by hand under time
pressure is where this goes wrong.

Nothing here runs git. It prints the commands; creating branches and picking
commits stay with the user.

## Resolution

Repo config (`.workflow/config.json`) beats context, context beats detection.

Detection reads the remote branch list: a source from `main`/`master`/`trunk`,
validation targets from `homolog`, `homologacao`, `staging`, `beta`, `qa`,
`uat`, `develop`, `dev`. It is a proposal — `wb flow show` says
`detected from remote branches` and tells you how to record it.

## Branch naming

`branch_pattern` takes `{key}`, `{slug}` and `{type}`. `{key}` is required; the
slug is derived from the ticket title, lowercased, cut to 32 characters on a
word boundary.

The convention is also detected, using the same 60% adoption threshold as commit
messages: three or more non-protected branches sharing a prefix make it a house
style, fewer make it a coincidence.

## Protected branches

`flow show` reports when the current branch is protected. The tool does not
prevent a commit — it does not run git at all — but the check is there so the
mistake is visible before it becomes a force-push conversation.

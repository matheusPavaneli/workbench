# Changelog

What changed, and what it costs to upgrade. Versions follow
[semantic versioning](https://semver.org): the public surface is the CLI's
commands, flags, exit codes and `--json` payloads — everything a skill or a
script can depend on. Internal module layout is not.

**Deprecation policy.** A command, flag or payload key is removed over two
releases: one that keeps it working while announcing it here and in the
command's own output, and one that removes it. A `--json` payload that loses or
renames a key raises its `schema` number in the same release, and
`contract.VERSIONS` is asserted against the real output so it cannot drift.

## 0.5.0 — unreleased

### Added

- `wb init` — proposes this repo's config in one pass: provider from the git
  remote, preset and flow from what is already detected. Writes nothing without
  `--write`, replaces nothing without `--force`, never writes a credential, and
  never drops a decision already recorded.
- `wb route [KEY]` — the steps a change actually needs, computed from the same
  tier rule the audit uses. One or two files, no critical zone and no ticket
  type that owes QA an explanation gives five steps instead of eight. The floor
  is never waived.
- `wb ctx record <KEY>` — saves an anonymised copy of your tracker's payloads as
  fixtures. Keeps the shape exactly, replaces the content: free text becomes
  lorem of the same length and line structure, people become consistent fake
  people, hosts and ids go. The suite then runs the provider tests against your
  tenant as well as against the packaged contracts, and skips cleanly without a
  recording.
- `wb ctx test --deep` — reports the fields present in your payloads that
  nothing here reads, with a sample of each.
- `field_map` in `.workflow/config.json` — maps a custom tracker field to one of
  a closed set of destinations (`acceptance_criteria`, `steps_to_reproduce`,
  `impact`, `component`, `environment`), which reach `triage.json` under
  `extra`. `wb doctor` reports a mapping that names a destination nothing reads.
- `wb next --json` and `wb status --json` now name the **skill** for each stage,
  not only the command.
- `wb task clean <KEY>` — removes one ticket's `.workflow/<KEY>/` artifacts.
  Lists and removes nothing; `--force` is a second command, because a plan and
  its evidence can be produced again while a frame or a handover was written
  once by hand, and the listing marks which is which. The key resolves through
  the same validation as every other artifact path, so the context binding, the
  local backlog and the event log sitting beside the ticket directories are not
  names the command avoids — they are names it cannot produce.

### Fixed

- `wb review context` graded the diff against the *detected* preset rather than
  the one the repo recorded. `wb pr context` took the PR base branch and the
  preset from detection for the same reason, and `wb doctor` described a flow
  other than the one `wb flow` uses. All four now go through the resolvers.
- `git check-ignore` moved into the git façade, where every other git read
  already lives.
- The anonymiser could give two different people the same pseudonym about one
  run in six, which made a recorded fixture incoherent.
- A `fix:` line in the local provider described the problem instead of saying
  what to do.
- `sdd.validate`, `sdd.tier` and the audit raised `AttributeError` on a plan
  whose `evidence`, `files` or `tests` held strings where objects belong — the
  commonest malformed plan there is. They now report the shape as a problem. An
  audit that crashes teaches people to skip the audit.
- The `--author` exemption for angle brackets applied to any value following the
  flag, not only to something shaped like an address. Not exploitable — there is
  no shell, and the printed form is quoted — but wider than its reason.

### Contract

- Every `--json` payload now carries a `schema` number, recorded in
  `contract.VERSIONS` and asserted against the real output. All are at `1`.
- This file, and the deprecation policy at the top of it.

### Tests

- `test_consistency.py` — every question answered in more than one place, asked
  in all of them.
- `test_structure.py` — the source read with `ast`: no raw detector called
  behind a resolver's back, no `subprocess` outside the modules that declare it,
  no `shell=`, a timeout on every call.
- `test_fixes.py` — every `fix:` names a real command and flag, instructs rather
  than describes, and for the refusals, actually clears the refusal.
- `test_evals.py` — three scenarios walked end to end, asserting on artifacts
  rather than on prose.
- `test_gitrun_fuzz.py` — the execution allowlist attacked: every git subcommand
  it does not allow, every dangerous flag, and injection payloads in every
  position of every allowed command.
- `test_budget.py` — the opening commands stay instant and never reach the
  network; a triage stays the size the README quotes.

## 0.4.0

### Added

- `wb next [KEY]` — the single command to run now, in two lines, resolving which
  ticket from the branch and then from what was touched last.
- Quality presets carry a **confidence**. Where the evidence supports more than
  one bar — an unreadable contributor count, CI alone on a one-person repo, or a
  monorepo — the preset comes back `LOW confidence` with the alternatives named,
  and `wb status` says so until `wb repo profile --confirm` settles it.
- `preset_paths` in `.workflow/config.json` — a bar per path, for a repo that
  builds several things. A change spanning two presets is held to the higher.
- `wb repo gates <paths>` — floor, preset and critical zones resolved for the
  files a change touches.
- Opt-in git execution: `--execute` on `wb flow start`, `wb flow carry`,
  `wb git commit` and `wb git push`, through an allowlist of five subcommands
  that refuses every rewrite of history and any force-push. `WB_NO_EXECUTE=1` or
  `"execute": false` turns it off standing.
- `wb git commit <KEY>` — commits with the message `wb commit check` validated
  and the author the context expects.

### Fixed

- `wb sdd gates` read the detected preset and ignored a recorded override.
- `flow carry` measured its range against the **local** source branch, so a
  stale checkout re-carried commits already merged upstream; it also fetched
  after measuring rather than before.
- `--author` was rejected as containing a shell character, so
  `wb git commit --execute` refused wherever a context supplied an identity.
- `wb git` resolved protected branches by skipping to detection, so a repo that
  had recorded `develop`/`release/*` got back `["main"]`.
- The failure handover after a stopped series began *after* the failed step,
  handing over a cherry-pick whose branch had never been created.
- `wb next` matched keys by substring, so `ABC-1` answered for a checkout on
  `feature/ABC-12-thing`, and its fallback regex turned `chore/bump-node-20`
  into `NODE-20`.
- The `clean-tree` precondition counted untracked files and advised
  `git stash`, which does not clear them.
- Equal-length `preset_paths` rules tied alphabetically, so `prototype` beat
  `enterprise` and the tie lowered the bar.

## 0.3.0

- GitHub Issues provider, and a local backlog for repos with no tracker.
- `wb status` and `wb doctor`.
- Rigour tiers: a plan's own file list decides which sections it owes.

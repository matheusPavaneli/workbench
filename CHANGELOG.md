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

## 0.12.0

### Added

- **`wb status --stats` tells invented citations from drifted ones.** The
  events for `wb sdd audit` and `wb cite check` now record how many citations
  landed on each verdict, and the history reports `mismatch` + `missing_file`
  (a claim the code never supported) apart from `moved` + `out_of_range` (a
  true claim whose line has shifted). `cite check` is now tracked. The
  `--json` payload gains `history.citations`. **Upgrade cost:** none; log lines
  written before this carry no counts and are read as before.
- **CI lints and type-checks `lib/`.** A `lint` job runs `ruff check lib` and
  `mypy` at pinned versions, blocking, with the rules committed in `ruff.toml`
  and `mypy.ini`. The 11 ruff findings and 13 mypy errors already in `lib/`
  are fixed: unused imports, ambiguous names, and annotations that did not
  hold; no behaviour changes. **Upgrade cost:** none; the tools are dev-only.

## 0.11.1

### Fixed

- **A plan's `zones` must match the files it lists.** The tier was computed
  from the files, but `sdd.md` and `wb sdd get --section summary` printed
  `zones` as written, so a hand-written or stale block reached the reader
  looking computed. `wb sdd audit` now recomputes the zones from `files` and
  reports a structure problem naming each missing zone, each zone no listed
  file touches, and each zone whose paths differ. **Upgrade cost:** a plan
  whose zones disagree with its files fails its audit until `zones` is copied
  from `wb repo zones`.

## 0.11.0

### Changed

- **An incident's handover is owed by the PR, not by the plan.** An
  `incident-*` plan failed its audit until the note for QA was written, so an
  outage's hotfix waited on documentation. The audit now reports the missing
  fields as `pending` -- printed after the pass, and recorded in `audit.json`
  -- and `wb pr check --key <KEY>` refuses the PR until the handover is filled
  and `wb sdd handover` has written `handover.md`. Any plan that owes a
  handover gets that check once the key is passed; `incident-*` keys now owe
  one in the audit too, as they already did in `wb route` and `wb status`.
  Citations, files, verify and rollback still fail an incident's audit. A
  hotfix pushed without a PR is not gated. **Upgrade cost:** `draft-pr` now
  passes `--key`, so a bug or support PR whose `handover.md` was never
  rendered fails `pr check` until `wb sdd handover` runs.

### Added

- **`wb route incident-<slug>` names a `mitigate` step before the plan.** A
  rollback, flag flip or config change is recorded in the timeline of
  `incident.md` and checked with `wb cite check`; it needs no `sdd.json`.

## 0.10.0

### Fixed

- **`wb impl verify` fails when a test the plan named was never written.**
  `tests[].target` was never read: every command passing verified the plan,
  with or without its tests. Each target must now be among the branch's
  changes, committed or not; one that is not fails the run with exit 7, is
  named on stderr, recorded as `tests_missing` in `evidence.json`, and shown by
  `wb status` under verify. **Upgrade cost:** a plan in flight that names a
  test file its branch never touched now fails verify until the test is
  written or the plan corrected.

- **`wb impl verify` no longer asks again on every ticket for the same
  command.** Approvals were verbatim, and a verify list usually names its own
  ticket (`wb sdd audit ABC-1`), so each ticket stopped on an approval nobody
  needed to read twice. The plan's own key, as a whole token, is now stored as
  `<KEY>`; the printed `--approve` call shows that form, and the concrete
  spelling is accepted and stored abstract. Any other difference -- another
  key spelled out, a flag, an env value -- still asks. Approvals stored by
  earlier releases keep covering their ticket.

### Added

- **`wb impl verify --regression` proves a regression test tests the fix.**
  Each `kind: "regression"` target runs on its own in a temporary worktree at
  the commit the branch left, with the branch's changed test files carried in,
  and must fail there; then in the checkout, where it must pass. The working
  tree is never touched, so it holds for a fix already committed. Both runs
  land in `evidence.json` under `regression`. Per-file commands exist for
  unittest, pytest, vitest and jest; another runner is refused, as is a plan
  with no regression tests. The generated commands go through the same
  per-machine approval as the plan's own.

- **`wb cite check <file>` audits citations outside a plan.** Review findings,
  incident chains, replies to a reviewer and answers about the code were told
  to quote the lines they rest on, and nothing looked. The command finds every
  `` `path:line` — `quote` `` (the form `sdd render` writes) outside fenced
  blocks and runs it through the audit's own verifier: against HEAD by
  default, or the working tree with `--worktree` for an uncommitted diff --
  `.workflow/` and ignored files refused either way. A `path:line` with no
  quote fails as `unquoted`. Exit 7 on any failure. `review-diff`,
  `trace-incident` and `address-review` now write their citations in that form
  and run it; `shared/code-search.md` describes answering a question about the
  code the same way, through `.workflow/ask-<slug>.md`.
- **Absence claims are searches the audit runs.** "Nothing else calls this"
  used to pass by citing a real line, which proved nothing about the lines
  that are not there. An `evidence` item may now be `"kind": "absence"` with a
  `search` (`pattern`, optional `paths`, `allow`, `word`). `wb sdd audit` runs
  `git grep -l -F` against the anchor commit -- HEAD, or the baseline once
  under way, `.workflow/` excluded -- and fails with the new verdict `found`,
  naming the files, when anything matches outside `allow`. Every search is
  recorded as run under `searches` in `audit.json`. Outside a checkout an
  absence claim fails as `unreadable`. The pattern and paths are validated:
  fixed text only, no pathspec magic, nothing that leaves the repo.

## 0.9.0

### Added

- **Hooks hold edits to the plan.** The plugin now ships `hooks/hooks.json`.
  On a branch naming a ticket whose plan passed its audit, `PreToolUse`
  refuses an `Edit`, `Write`, `MultiEdit` or `NotebookEdit` to a file neither
  that plan nor another audited plan lists, and refuses every code edit while a
  plan edited after its audit waits for a re-audit. `Stop` reports planned work
  whose verification is missing or stale; `"hooks": "strict"` makes it block
  once. With no ticket on the branch or no audited plan, both are silent.
  Internal errors allow the edit and say so. **Upgrade cost:** one Python start
  per file edit in a session with the plugin; `WB_NO_HOOKS=1` or
  `"hooks": false` turns them off.

### Changed

- **`wb sdd audit` accepts only committed code as evidence.** The audit read
  the working tree, so a citation to a file written in the same session -- or
  to an ignored file, or to the plan itself -- verified as `ok`: a claim could
  be invented, written to disk and quoted. The first audit now reads each cited
  file as committed at HEAD. Two new finding verdicts, both failing at every
  stage: `artifact` for a citation into `.workflow/`, and `uncommitted` for a
  file the anchor commit lacks (untracked, ignored, added by the plan) or a
  line that exists only as an uncommitted edit. Under way, a file added after
  the baseline cannot be cited either. **Upgrade cost:** a plan citing
  uncommitted work fails its first audit until that work is committed or the
  citation points at the committed line. `audit.json` keeps schema 1.

### Fixed

- **A ticket marked done no longer excuses edits to its files.** An audited
  plan's claim on a file outlived the ticket: once shipped, it still accounted
  for every later edit to those files, in `wb impl check` and in the edit hook.
  A ticket the local backlog records as done now claims nothing. With a remote
  tracker a shipped plan keeps its claim until `wb task clean` removes it.
- **The verification fingerprint works when `.workflow/` is ignored.**
  `gitctx.tree()` excluded `.workflow` with a pathspec, and naming an ignored
  directory makes `git add` fail -- so in a repo ignoring `.workflow/`
  outright, the setup `wb doctor` recommends, the tree was always `None`.
  Evidence then recorded no tree and compared equal to the next `None`: it
  never read as stale. Evidence written by an affected checkout reads as stale
  once, and `wb impl verify` records a real tree.
- **The fingerprint no longer misses a same-size rewrite.** The scratch index
  was copied with a fresh mtime, which hid racy entries from git's re-hash: a
  file rewritten to the same size in the index's own tick kept its old blob in
  the fingerprint, about one run in thirty. The copy now keeps the index's
  mtime.
- **Baseline and HEAD lookups decode UTF-8 on every platform.** `git show` was
  read with the locale's encoding -- cp1252 on Windows -- so a citation whose
  line held any non-ASCII character could not verify against a commit.

## 0.8.0

### Changed

- `wb impl verify` runs nothing until every command and `env` entry in the plan
  has been approved on this machine. The commands come from a model-written
  plan, the audit checks citations rather than commands, and the runner
  allowlist admits `python -c` and `node -e` by construction -- so until now a
  command no person had read ran with the user's permissions. The first run in
  a checkout prints the entries and the exact
  `wb impl verify <KEY> --approve '<command>' ...` call, executes nothing and
  writes no evidence. Approvals are verbatim, kept in
  `$WORKBENCH_HOME/approvals.json` keyed by checkout path, and one changed
  character asks again. **Upgrade cost:** one approval per distinct command
  per checkout, once. `--approve` is new.

### Fixed

- Verification evidence said nothing about which code it verified, so a pass
  recorded before the last edit still read as a pass: `wb status` showed
  verify ok and `wb pr context` handed the PR a verdict for code that never
  ran. `evidence.json` now records `tree` (what the working tree would commit
  as, `.workflow/` excluded), `head` and `plan_sha256`. A pass stands only
  while the tree and the plan both match; committing exactly the verified tree
  keeps it standing. Otherwise `wb status` reads verify as `stale` and offers
  `wb impl verify` again, and `wb pr context`'s `verification` gains
  `standing` and, when false, `stale`. Keys added only, so no schema bump.
  Evidence written by earlier versions reads as stale.

## 0.7.4

### Fixed

- `wb status` read a ticket's state off its artifacts alone, so a ticket that
  had shipped and been closed stayed listed at its last stage for as long as
  its files were on disk -- `commit` or `pr`, or `BLOCKED at audit` if an audit
  had once failed. A ticket the local backlog records as `done` now reads as
  `done`: never blocked, no next command, counted as complete by `--stats`,
  and passed over by `wb next` in favour of open work. `--json` gains a
  `closed` key. Jira, Azure DevOps and GitHub tickets are unchanged: their
  state names are their own, and status does not call the network.
- `wb task clean --merged` selected only tickets whose branch was gone from
  the remote. A squash merge leaves the branch, so in a repo that merges that
  way it selected nothing. A shipped ticket the local backlog records as
  `done` is now selected whatever the remote holds.

## 0.7.3

### Fixed

- `wb git push --execute` refused every branch `wb flow start --execute` had
  created, with `this branch already has an upstream`. The branch was made
  from `origin/<source>`, and under git's default `branch.autoSetupMerge` that
  makes `origin/<source>` its upstream -- so the tool's own start path could
  never reach its own publish path. `flow start` and `flow carry` now branch
  with `--no-track`, and the first push sets the upstream to the branch's own
  remote. A branch started before upgrading: `git branch --unset-upstream`,
  then push.

## 0.7.2

### Fixed

- The citation audit passed a quote that merely *contained* the cited line. It
  was meant to let a statement that wraps be quoted whole, but it never checked
  the rest of the quote, so a real line followed by invented text verified:
  `return create_charge(total)  # after refunding every order` passed against a
  line reading `return create_charge(total)`. A quote now has to start on the
  cited line and every word of it has to be there, in that line or the ones
  that follow it. A quote under eight characters, whitespace aside, fails as
  well: `c` occurs on almost any line and so identifies none.
- `impl check` and `impl verify` trusted the verdict in `audit.json` without
  asking which plan it was reached on, so a plan edited after it passed -- a
  wider file list, a weaker verify list -- ran without a second audit.
  `audit.json` now records `plan_sha256`, and those two commands, `wb status`
  and the attribution of files between tickets trust a verdict only for the
  plan it describes. This catches drift, not forgery: whatever can write both
  files can make them agree.
- A first audit that failed still recorded its commit as the plan's baseline,
  so re-running it was lenient -- a wrong line number passed as `moved`. Only an
  audit that passed anchors the ones after it; once a plan has passed, a
  failing correction keeps the anchor it already had.
- `wb status` counted failed verify commands by a key `evidence.json` never
  writes, so every command read as failed: three commands with one failure
  said `3 failed`.

No command, flag or exit code changed. `wb sdd audit --json` gains
`plan_sha256`. An `audit.json` written before this release has no fingerprint,
so `impl check`/`impl verify` refuse it and `wb status` marks the audit stage
blocked until `wb sdd audit <KEY>` is re-run once per ticket in flight.

## 0.7.1

### Fixed

- `wb ctx record` wrote the tenant's ticket keys, verbatim, into the fixtures it
  produced. A recording is a file people commit, and a key names the project it
  came from, so `SAAS-123` identified the company as surely as the hostname the
  anonymiser had always stripped. The module had been written to replace keys —
  it says so, and the pattern for it was sitting beside the ones for emails and
  URLs — but nothing ever applied it. Only the letters are replaced, and one
  real project maps to one fake one for the length of a run: the shape is what
  the providers parse and what the fixtures exist to exercise, so `ABC-123` in
  the `key` field and the same key inside a link still read as one ticket. The
  substitution is anchored to the fields that hold a key, not to anything
  key-shaped, because the pattern has to be loose enough for project codes that
  vary and a status named `UTF-8 encoding` is not a ticket. A tenant is also
  never handed its own code back, which a pool of plausible codes assigned by
  position can otherwise do.

### Changed

- The repo root is resolved in one place. `gitctx.checkout()` falls back to the
  working directory, `gitctx.require_checkout()` refuses, and the structural
  guard that already held `profile.detect` and `flow.load` behind their
  resolvers now holds `gitctx.repo_root` behind these two. The fallback had been
  spelled out at twenty call sites and the refusal at eight, three of them as a
  private helper copied byte for byte between `wb commit`, `wb flow` and
  `wb git`, so "what happens outside a checkout" had two answers and no single
  place to change either. Every command answers exactly as it did, `wb doctor`
  included: it reports whether this is a checkout, so it keeps reading the raw
  detector and the guard records why.
- Dead code removed: `gitctx.merge_base`, `commitmsg.SUBJECT_COMFORTABLE`,
  `flow._PATTERN_FIELD` — superseded by `validate_pattern`, which inlines the
  same expression — and six unused imports. An AST sweep over the package now
  finds nothing unreferenced that is not a framework callback.

No breaking change: no command, flag, exit code or `--json` payload changed.
`wb ctx record` writes a different fixture than it did in 0.7.0, which is the
point; recordings made before this release still load, and should be re-made.

## 0.7.0

### Added

- `wb surface` — prints every group, action and flag, walked off `build_parser()`
  rather than a list kept by hand. `wb surface <group>` narrows it; `--json` is
  the form a session reads before composing an unfamiliar call. It carries what
  invented calls get wrong: which arguments are positional, which are required,
  which take no value, and the closed set a choice flag allows. Nothing names it,
  so it costs nothing until something asks.
- `wb status --stats --global` — reads `~/.workbench/events.jsonl`, the same
  tracked lines appended once per machine with the checkout's name, and adds a
  `by checkout` breakdown. The log under `.workflow/` answers "where does this
  repo lose time" and structurally cannot answer "where do I lose time"; a stage
  that is fine here and terrible in four other checkouts looks fine from inside
  any one of them. Same three rules as the local log: outcomes and never
  arguments, capped by rewriting, every failure swallowed. `--global` without
  `--stats` is refused rather than ignored, because only `--stats` reports the
  history.
- `wb task clean --merged` and `--older-than <duration>` — clean a checkout
  rather than a ticket. `--merged` takes every ticket that reached a commit or a
  PR and has no branch left on the remote; both halves are load-bearing, since
  "no branch names this key" on its own also describes work that was never
  branched at all. `--older-than 30d` measures from the newest file in the
  ticket, not the directory's own timestamp, because editing a plan in place
  leaves the directory alone. The listing says which stage each ticket stopped
  at, so a selector that caught live work is visible while it is still only a
  listing. A key and a selector together are refused rather than merged.

### Fixed

- `wb pr context` graded a three-file feature as trivial when asked after the
  commit, because it read the working tree for files while building its commit
  list from the branch against its base. `wb status` had the same root cause and
  reported `0 of 3 planned file(s) changed` for finished work. Both now use
  `gitctx.changed_since`, three dots, so commits the base collected after the
  branch left it are not counted as this branch's doing, and an unknown base
  contributes nothing rather than failing. `pr context` also took its base from
  the local `flow.source.branch`; it now goes through `flow.carry_base`, which
  resolves `origin/<source>` when it exists. `status` resolves the set once per
  command rather than once per ticket.

### Changed

- The declared version is now held to a released changelog section, so a version
  named in the package may not still carry an unreleased marker. `wb task clean`
  shipped under 0.5.0 with no bump: the marketplace compared 0.5.0 against 0.5.0,
  reported the plugin current, and left every installed copy on four `wb task`
  actions. 634 tests did not notice.

No breaking change: every existing group, action, flag and exit code behaves as
it did in 0.6.0, and no `--json` payload lost or renamed a key — `surface` is a
new payload at `schema` 1, and `status --stats --global` adds a key rather than
moving one.

## 0.6.0

### Added

- `wb task clean <KEY>` — removes one ticket's `.workflow/<KEY>/` artifacts.
  Lists and removes nothing; `--force` is a second command, because a plan and
  its evidence can be produced again while a frame or a handover was written
  once by hand, and the listing marks which is which. The key resolves through
  the same validation as every other artifact path, so the context binding, the
  local backlog and the event log sitting beside the ticket directories are not
  names the command avoids — they are names it cannot produce.

No breaking change: the four existing `wb task` actions, every other group and
every artifact schema behave exactly as they did in 0.5.0.

## 0.5.0

Distributed as 0.5.0 without being marked released here. The section below is
what shipped in it.

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

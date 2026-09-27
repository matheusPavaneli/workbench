# What workbench caught

Every example here comes from a benchmark record, never from a staged session:
the ticket, what a plain session did with it, what the workbench session did,
and the run records that show both. The numbers come from the first clean run,
[2026-09-27, commit 9abd3c9](../bench/results/2026-09-27-9abd3c9/report.md): the
same model (Sonnet), the same prompt, two runs per arm. Two runs is a direction,
not a verdict. How the benchmark works is in [bench.md](bench.md).

A catch is said the way it happened. Where a guardrail refused something, it
says so. Where the flow only asked for something and the session did the rest,
it says that instead.

## BN-6: the ticket names a function that was renamed

**The ticket.** "`in_stock` refuses the last unit on hand: with 2 on hand, an
order for 2 is refused." The live check is `shop/stock.available`. A deprecated
`in_stock` with the same bug still sits in `shop/legacy.py`, kept for an old
export script that checkout never calls.

**Plain, 2 of 2 runs.** It searched for `in_stock`, found the one in
`shop/legacy.py`, fixed it and committed. It wrote no test. The live check is
still broken, and the hidden tests fail. It took 5-6 turns and about 16 s.
Records: [BN-6-plain-1](../bench/results/2026-09-27-9abd3c9/BN-6-plain-1.json),
[BN-6-plain-2](../bench/results/2026-09-27-9abd3c9/BN-6-plain-2.json).

**Workbench, 2 of 2 runs.** `wb start` put the ticket on the light path, which
asks for the change *and a test that covers it*. The session opened
`shop/legacy.py` too, then went looking for the test to extend. `tests/test_stock.py`
tests `stock.available`, not `in_stock`, so the session concluded that the copy
in `legacy.py` is dead and the live code is `stock.py`. In its own words: "The
ticket's `in_stock` name matches `shop/legacy.py`, but that module is explicitly
deprecated and unused by checkout. The live code path is `shop/stock.py`'s
`available()`." It fixed that function and added a test for the last unit.
`wb finish` proved the test fails on the base and passes with the fix. The hidden
tests pass. It took 16 turns and about 47 s.
Records: [BN-6-workbench-1](../bench/results/2026-09-27-9abd3c9/BN-6-workbench-1.json),
[BN-6-workbench-2](../bench/results/2026-09-27-9abd3c9/BN-6-workbench-2.json).

**What caught it.** No guardrail refused anything. What steered the session was
the light path's requirement to cover the change with a test. The requirement
does not always work: in an earlier run whose numbers are not citable
([31a7699](../bench/results/2026-09-27-31a7699/report.md)), one of two workbench
sessions fixed `legacy.py` and wrote a test for it, and `wb finish` passed
that. A test proves the code does what the test says, not that it is the right
code.

## BN-3, BN-4, BN-7: a fix with no test

**The tickets.** A one-file chore (`format_money` prints refunds wrong), a bug
in user data (`display_name` keeps stray whitespace), and a one-character bug
(loyalty points are a tenth of what they should be). There is no test file for
the loyalty module at all.

**Plain, 6 of 6 runs.** It fixed each bug correctly (the hidden tests pass) and
wrote no test for any of them. The next change to any of these functions has
nothing to stop it undoing the fix.

**Workbench, 6 of 6 runs.** Every run added or extended a test. On BN-3 and BN-7
(the light path), `wb finish` refuses a logic change with no test change, and
on a bug it runs each changed test on the base the branch left, where the test
must fail. In these runs the session wrote the test before it was asked, so
the refusal never had to fire. What held the line was the instruction, backed
by a check that would have refused.

Records: `BN-3-*`, `BN-4-*` and `BN-7-*` in
[9abd3c9](../bench/results/2026-09-27-9abd3c9/). The report's caught-errors
section counts `tests_missing` for each arm.

## BN-4: a fix that lands in user data

**The ticket.** `display_name` keeps stray whitespace. The function is in
`shop/users.py`, which the repo profile puts in the **user-data** critical zone.

**Plain, 2 of 2 runs.** Fixed, no test, committed. 7-8 turns.

**Workbench, 2 of 2 runs.** This is the one guardrail that fired. The session
fixed the bug on the light path, then `wb finish` refused:
`BN-4 outgrew the light path: touches user-data. It now takes the standard route`.
The session then planned the change, audited the plan, verified it and
committed, with a test. The result matched plain's plus a test, and it cost
6.7x the tokens. Both runs also discarded the working fix before planning,
which WB-58 records as waste.
Records: [BN-4-workbench-1](../bench/results/2026-09-27-9abd3c9/BN-4-workbench-1.json),
[BN-4-workbench-2](../bench/results/2026-09-27-9abd3c9/BN-4-workbench-2.json).

**What caught it.** The zone check did what it is for: a change to user data is
held to the highest bar in the repo. Whether that bar paid for itself on a
whitespace fix is a fair question, and this record is the evidence either way.

## What has not been caught

- **A spill into a shared neighbour (BN-5).** Every plain session so far fixed
  it in place and left the shared `regions.zone_for` alone. This ticket has
  never separated the arms.
- **An invented reference.** `invalid_refs` has been 0 in every run of both
  arms.

Both stay in the fixture. A ticket that does not separate the arms is a result
too.

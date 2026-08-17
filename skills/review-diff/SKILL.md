---
name: review-diff
description: Reviews the current diff against the repo's quality preset, reporting findings as file:line with a severity. Use to self-review before a PR, or to review uncommitted or staged changes.
---

# review-diff

```
python "${CLAUDE_PLUGIN_ROOT}/lib/wb.py" <args>
```

## Steps

1. **Get the computed facts.** `review context` (add `--staged` for the staged
   diff). It reports the preset, every changed file, the critical zones touched,
   the sources that changed with no test alongside, and the gates that apply.

2. **Read the diff itself**, then read the surrounding code. A diff shows what
   changed, not what it broke. For anything that changes a shared contract, ask
   the index who calls it:
   `${CLAUDE_PLUGIN_ROOT}/shared/code-search.md`.

3. **Check the gates.** Every gate `review context` printed is a question with a
   yes or no answer for this diff. The floor is not negotiable at any preset:
   unit test for changed logic, regression test with a bug fix, no swallowed
   error, no secret, a stated rollback.

4. **Go deeper in critical zones.** Billing, auth, user data, migrations and
   secrets are held to a higher standard than the preset otherwise sets. In
   those files, read every changed line and its error path.

5. **Report findings**, most severe first:

   ```
   path/to/file.py:42  high  Coupon is validated after the charge is created.
                             An expired coupon charges the card, then errors.
   ```

   Severity: **high** — wrong behaviour, data loss, or a security hole.
   **medium** — will break under a plausible input or state. **low** — real but
   contained. Skip formatting and taste unless it changes meaning.

6. **Say when it is clean.** "No findings" is a result. Do not manufacture a
   finding to look thorough.

## Rules

- **Every finding cites `file:line` and quotes the line.** Read the line to
  quote it; do not quote from an index or from memory.
- **State the failure, not the preference.** A finding needs concrete inputs or
  state that produce a wrong outcome. "Consider extracting this" is not a
  finding.
- **The missing-test list is a heuristic**, not a verdict. Check each one: a
  test may cover it under another name.
- **Do not fix while reviewing.** Report; let the author decide.

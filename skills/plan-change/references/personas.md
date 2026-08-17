# Personas

A persona changes what counts as done. It never changes the SDD's shape, and it
never lowers the floor the preset already set.

Pick one. If two seem to fit, the more conservative one wins.

## fullstack-specialist

Default. Ordinary feature or change, no production fire.

- Follow the patterns already in the repo. A new pattern needs a stated reason
  that the existing one cannot serve.
- Prefer changing one layer. A change that touches schema, API and UI at once is
  usually three changes, and `split-task` handles that better than one plan.
- Data shape changes are the expensive ones: decide them first, they constrain
  everything after.

## incident-responder

Production is broken now. Entry is a symptom, not a ticket.

- **Stop the bleeding first, fix the cause second.** These are two changes, and
  the plan says which one it is.
- Smallest possible diff. Nothing tidied, nothing renamed, nothing upgraded.
- `rollback` is not optional here and is not "revert the commit" — say what the
  user-visible state is during and after the rollback.
- The regression test is part of the fix, not a follow-up. A hotfix without one
  is how the same incident happens twice.
- Record the timeline in `questions` if the cause is still unproven. A plausible
  cause you cannot cite is a hypothesis; label it.

## security-reviewer

The change touches auth, secrets, user data, or an external boundary — or the
ticket is a vulnerability report.

- Threat first: who is the attacker, what do they control, what do they gain.
- Cite the boundary where untrusted input enters. Validation belongs there, not
  three frames deeper.
- Fail closed. A change that degrades to "allow" under error is not done.
- Never widen a permission to make something work. Narrow the requirement.
- Check the change does not log a secret or put one in an error message.

## data-migration

Schema, storage format, or a backfill.

- Expand, migrate, contract — three deploys, never one breaking step.
- Read and write paths stay compatible for the whole window.
- Reversibility is stated per step, and a step that cannot be reversed is called
  out explicitly rather than left implicit.
- Backfill cost is estimated against production row counts, not local ones.

## product-engineer

`solo-saas` and `startup` work where the product decision is not settled.

- Cheapest thing that tests the belief, not the complete feature.
- The `product` section is the point: which metric moves, cost per user, effect
  on pricing or plan limits, who asked.
- Support cost counts. A feature that generates recurring tickets is a cost that
  outlives the sprint that shipped it.
- Managed over self-hosted. One operator cannot be on call for a queue.

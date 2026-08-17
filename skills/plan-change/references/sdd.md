# sdd.json

Write this file, then run `sdd audit <KEY>`. The audit reopens every citation,
so a wrong `line` or an invented `quote` fails here rather than in review.

```json
{
  "schema": 1,
  "key": "ABC-123",
  "preset": "solo-saas",
  "persona": "incident-responder",

  "objective": "One or two sentences: what changes, and why now.",

  "evidence": [
    {
      "claim": "Coupon validation runs after the charge is created",
      "file": "src/billing/checkout.py",
      "line": 142,
      "quote": "charge = stripe.Charge.create(amount=total)"
    }
  ],

  "files": [
    { "path": "src/billing/checkout.py", "change": "edit", "why": "move validation above the charge" },
    { "path": "tests/billing/test_checkout.py", "change": "add", "why": "regression test for expired coupons" }
  ],

  "zones": { "billing": ["src/billing/checkout.py"] },

  "steps": [
    { "do": "Validate the coupon before creating the charge", "file": "src/billing/checkout.py" },
    { "do": "Return 422 with the validation error instead of 500" }
  ],

  "tests": [
    { "kind": "regression", "target": "tests/billing/test_checkout.py",
      "asserts": "an expired coupon returns 422 and creates no charge" },
    { "kind": "unit", "target": "tests/billing/test_validator.py",
      "asserts": "validator rejects a coupon whose expiry is in the past" }
  ],

  "verify": ["pytest tests/billing -q", "ruff check src/billing"],

  "rollback": "Revert the commit; no schema or data change, no flag to clean up.",

  "product": {
    "metric": "failed checkouts per day",
    "who_asked": "support, 3 tickets this week",
    "cost": "none, same request path",
    "pricing_impact": "none"
  },

  "questions": ["Should an expired coupon fall back to full price, or block the order?"]
}
```

## Field rules

| Field | Rule |
|---|---|
| `evidence[].quote` | The **actual text of that line**, copied. The audit compares it after collapsing whitespace. Do not paraphrase, do not reconstruct from memory. |
| `evidence[].line` | 1-indexed. If the audit says `moved`, correct the number — do not loosen the quote. |
| `files[]` | Every file the change touches, before any of it is touched. `implement-change` refuses a file that is not listed. `change` is one of `edit`, `add`, `delete`, `rename`. A listed `edit` whose path does not exist fails the audit. |
| `zones` | From `wb repo zones`. Do not hand-write it. |
| `tests[].asserts` | What must hold, not what to call. "calls validate()" is not an assertion; "rejects a coupon whose expiry is past" is. |
| `verify[]` | Exact commands, using this repo's runner as reported by `wb repo profile`. `verify-change` executes these literally. |
| `rollback` | Required, always. Say the user-visible state during and after. |
| `product` | Required on `solo-saas` and `startup`: at minimum `metric` and `who_asked`. |
| `questions` | Anything you could not confirm in the code. An empty list is a claim that nothing was assumed. |

## Sections consumers read

Later skills read one slice, never the whole plan:

```
wb sdd get <KEY> --section files      # implement-change
wb sdd get <KEY> --section steps      # implement-change
wb sdd get <KEY> --section verify     # verify-change
wb sdd get <KEY> --section summary    # draft-pr
```

# shop

A small shop used as a benchmark fixture. Amounts are integer cents.

- `shop/cart.py` — cart lines and totals
- `shop/format.py` — money formatting
- `shop/users.py` — user records from the admin export
- `shop/billing/charge.py` — charges against the payment gateway

Tests use the standard library runner:

```sh
PYTHONPATH=. python -m unittest discover -s tests
```

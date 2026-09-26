# shop

A small shop used as a benchmark fixture. Amounts are integer cents.

- `shop/cart.py` — cart lines and totals
- `shop/format.py` — money formatting
- `shop/users.py` — user records from the admin export
- `shop/billing/charge.py` — charges against the payment gateway
- `shop/regions.py` — country codes to zones, shared by shipping and tax
- `shop/shipping.py` — shipping quotes for the checkout form
- `shop/tax.py` — VAT on invoices
- `shop/stock.py` — stock checks before an order is accepted (`in_stock` was
  renamed `available` in 2.0)
- `shop/loyalty.py` — loyalty points earned on an order

Tests use the standard library runner:

```sh
PYTHONPATH=. python -m unittest discover -s tests
```

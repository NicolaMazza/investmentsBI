# Ghostfolio Schema Notes

Pinned to **Ghostfolio 3.3.0**.

Ghostfolio tables live in the **`public` schema** of the `ghostfolio` database (not a schema named `ghostfolio`).

## Tables read by `aggregator/position_snapshot.py`

| Table | Used for |
|-------|----------|
| `Order` | Individual transactions (buy/sell/dividend) |
| `SymbolProfile` | Instrument metadata (ISIN, currency, name) |
| `Account` | Account grouping — filtered by `ghostfolio_account_id` if set |
| `MarketData` | Historical prices for valuation |

## Notes

- `Order.accountId` links to `Account.id`. When `ghostfolio_account_id` is set in config, only orders from that account are included.
- `Order.quantity` and `Order.unitPrice` are in `SymbolProfile.currency`.
- FX conversion to EUR uses same-date rates from `fx_rate` (our table), not Ghostfolio's MarketData.
- Ghostfolio may rename or add columns between versions. Re-verify this file whenever the Ghostfolio add-on is upgraded.

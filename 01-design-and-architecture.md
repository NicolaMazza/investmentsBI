# InvestmentsBI — Design & Architecture

A look-through allocation reporting tool for a personally-managed investment portfolio, designed as a Home Assistant add-on that complements an existing Ghostfolio deployment.

---

## 1. Purpose

Ghostfolio is excellent at recording transactions, valuing positions, and giving a high-level view of returns. What it doesn't do well is **look-through analysis**: when you hold an ETF, Ghostfolio knows you own "50 shares of IWDA," but it doesn't decompose that into "4.2% Apple, 18% Financials, 67% USD-denominated assets."

InvestmentsBI fills that gap. It is a reporting-only application that:

- Reads positions from the existing Ghostfolio database
- Fetches each ETF's composition directly from the issuer (iShares, Vanguard, HSBC for the initial portfolio)
- Multiplies positions × compositions to produce a true look-through view
- Snapshots this daily so allocation drift can be tracked over time
- Exposes a pivot/drill-through dashboard across six dimensions

It is explicitly **not** a portfolio management tool. It never writes to Ghostfolio. It never executes trades.

---

## 2. Existing infrastructure

The application is deployed into an existing homelab built around a mini-PC running Proxmox VE. The relevant inventory:

| Component | Where it runs | Role |
|---|---|---|
| Home Assistant OS | Proxmox VM | Hosts add-ons via Supervisor |
| Ghostfolio | HA add-on | Portfolio of record |
| PostgreSQL 14+ | Proxmox LXC (separate) | Hosts the `ghostfolio` database (Ghostfolio) and the `investments_bi` database (InvestmentsBI) |
| Tailscale | HA add-on | Remote access into the home network |

The Postgres instance lives in its own Proxmox LXC, not bundled with Ghostfolio. Ghostfolio uses the `ghostfolio` database (which we will not modify). InvestmentsBI creates and owns a dedicated `investments_bi` database on the same Postgres instance — a fully separate database, not a schema within Ghostfolio's database. This ensures Ghostfolio upgrades, restores, or wipes cannot affect InvestmentsBI data.

---

## 3. Goals and non-goals

### In scope for v1

- Daily snapshot of positions and ETF compositions
- Six allocation dimensions: company, sector, country (listing, incorporation, and country-of-risk via overrides), currency, market cap bucket, ETF
- Drill-through from any pivot to its constituents, multi-level
- Historical allocation drift over user-selectable time ranges (top-N + pinnable items for high-cardinality dimensions)
- Multicurrency look-through: values in EUR, native currency as a pivot dimension to reveal FX exposure
- Extensible product model — adding new ETFs is configuration plus (for a new issuer) one new fetcher class; adding non-ETF product types (single stocks, bonds, crypto) is supported architecturally

### Out of scope for v1

- Real-time price tracking — we read end-of-day Ghostfolio values
- Transaction entry, portfolio modification, or trade execution
- Performance attribution and P&L analysis — Ghostfolio handles these
- Multi-user support — single user, single portfolio
- Backfilling historical allocations before the app's first snapshot day
- Look-through into products whose issuers don't publish holdings
- Country-of-risk classification for the entire portfolio (only manual overrides for material cases)

---

## 4. Deployment choice: HA add-on, not LXC

An earlier draft of this design proposed running InvestmentsBI as a dedicated Proxmox LXC alongside the Postgres container. We rejected this in favor of an HA add-on for these reasons:

- **Home Assistant Ingress** provides single-sign-on, the sidebar entry, and TLS termination for free. An LXC would require building or configuring a second reverse proxy to provide the same.
- **Tailscale already runs in HA.** Adding the app to HA means it inherits remote access automatically. From an LXC, exposing the app externally would mean another routing step.
- **Resources are not a constraint.** The mini-PC has ample headroom; the supposed isolation benefit of an LXC doesn't justify the additional plumbing.

The trade-off: HA add-ons are managed by HA's Supervisor, which constrains some operational patterns (e.g., the app's lifecycle is tied to HA's, and upgrades happen through HA's mechanism). For our scope, these constraints are fine.

---

## 5. Technology stack and rationale

### Language: Python 3.12

The dominant workload of this codebase is parsing messy holdings files from issuers — iShares CSVs with variable header offsets, Vanguard XLSX files with metadata preceding the data, HSBC files with their own layouts. Pandas + openpyxl handle these scenarios in 3–4 lines per parser; the JavaScript ecosystem requires markedly more boilerplate (SheetJS + papaparse + manual normalization).

The honest alternative was **TypeScript / Node**, which would align with Ghostfolio's own stack (Ghostfolio is TypeScript and uses Prisma). The advantage there is shared schema understanding — but the Ghostfolio coupling is small (one adapter module), while parsing work is the bulk of the codebase. We optimize for the dominant workload.

Python also has `yfinance` for market cap enrichment, with no clean Node equivalent.

### Web framework: FastAPI

Async-first (useful when multiple fetchers run concurrently), Pydantic integration for both API validation and config schemas, automatic OpenAPI docs, and good ergonomics around dependency injection for testing. Flask would also work but is sync and lacks Pydantic integration.

### ORM: SQLAlchemy 2.x

Two factors favor SQLAlchemy over alternatives like Prisma:

1. **Prisma Client Python is community-maintained**, not officially supported. It lags the official TypeScript client and has had periods of inactive maintenance. For software meant to run unattended for years, choosing a community port of a tool whose first-class home is another language is a small but real risk.
2. **We read from a schema we don't own** (Ghostfolio's). Prisma's design centers on owning a schema end-to-end. SQLAlchemy handles foreign schemas naturally — we hand-define just the columns we need.

SQLAlchemy 2.x has a typed query API that closes much of Prisma's developer-experience gap. Combined with Alembic for migrations, it's the right answer for this stack.

### Scheduler: APScheduler

In-process scheduler that shares the FastAPI app's connection pools and logging. With a persistent jobstore in Postgres, jobs survive container restarts. For 7 jobs at the scale of a personal portfolio, anything heavier (Celery, Airflow, external cron) would be over-engineering.

### Database: Postgres (existing) + new database

Reuses existing infrastructure. InvestmentsBI owns a dedicated `investments_bi` database on the same Postgres instance. Keeping a separate database — rather than a schema within Ghostfolio's database — means Ghostfolio upgrades, restores, or wipes cannot affect InvestmentsBI data. Each database is backed up independently.

### Frontend: static HTML + vanilla JS + Chart.js 4

No build pipeline. The frontend is `index.html` + `app.js` + `style.css` + a small `treemap.js`, served as static files by FastAPI. Reasons:

- Zero npm/webpack dependencies in the Docker image
- A single-page app with ~6 components doesn't justify React's overhead
- Debugging is just "open DevTools"

If the UI grows in scope (multi-page, complex shared state), the upgrade path is Preact or Svelte. React is overkill for v1.

Chart.js handles donut, bar, and line charts. The main allocation visualization is a hand-rolled squarified treemap (~80 lines) since Chart.js's treemap plugin doesn't meet our needs.

---

## 6. Data sources

| Source | What it provides | Cadence | How |
|---|---|---|---|
| Ghostfolio Postgres (`ghostfolio` schema) | User's positions, transactions, valuations | Daily (read after Ghostfolio EOD) | Direct SQL via read-only user |
| iShares product pages | Full holdings as CSV per fund | Daily | Stable `.ajax` URL per fund |
| Vanguard EU product pages | Full holdings as XLSX per fund | Monthly | Stable export URL per fund |
| HSBC ETF product pages | Full holdings as XLSX per fund | Monthly | Per-fund URL |
| ECB euroxref-daily.xml | EUR-base FX rates | Daily (business days, ~16:00 CET) | Public XML feed |
| yfinance | Market cap by ISIN, for bucketing | Weekly | Batch lookup, cached aggressively |

All issuer fetchers send a `Mozilla/5.0` user agent; default Python/curl agents get 403s from most issuers.

The Ghostfolio data is the authoritative source for "what I own." The issuer data is the authoritative source for "what's inside each fund." We never mix the two roles.

### A note on the historical data limit

The issuers do not retain past holdings files. Once today's CSV is published, yesterday's is gone. This means **historical allocation tracking begins from the app's first snapshot day**. There is no way to reconstruct what your Apple exposure was six months ago without external archives.

Partial mitigations exist (Wayback Machine sometimes archives issuer pages; some commercial services retain history) but they are out of scope for v1. The user should run the snapshot job for several weeks before expecting meaningful drift charts.

### A note on country of risk

Issuer CSVs typically provide **country of listing** (where the security trades) and sometimes **country of incorporation** (where the company is legally registered). They do not provide **country of risk** (where the company's revenue or operations are concentrated), which would require MSCI or FTSE classification data that is not free.

For most companies, listing = incorporation = country of risk and the distinction is irrelevant. For a small set — TSMC (listed in Taiwan, but operates in Taiwan with global revenue; sometimes classified differently), ASML (NL listed, NL incorporated, but materially exposed to Asia), some Chinese ADRs — the three diverge meaningfully.

The design accepts this and provides three dimensions:

1. **Country of listing** — pulled from issuer files directly
2. **Country of incorporation** — pulled from issuer files when available, otherwise null
3. **Country of risk** — defaults to incorporation; can be overridden via a manual table for material divergences (expected 10–20 entries total)

---

## 7. Data model

Database name: `investments_bi`. Tables live in the `public` schema of that database. Branding name everywhere user-facing: "InvestmentsBI".

### Tables

```sql
-- One row per investable product the user holds.
-- Designed to accommodate non-ETF products in the future.
product (
  isin              text primary key,
  ticker            text,
  name              text,
  product_type      text not null,          -- 'etf' | 'stock' | 'bond' | 'mutual_fund' | 'crypto' | 'cash'
  issuer            text,                   -- 'ishares' | 'vanguard' | 'hsbc' | 'self' | ...
  base_currency     text,
  source_url        text,                   -- holdings file URL; null for self-products
  parser            text,                   -- key into parser registry; null for self-products
  cadence           text,                   -- 'daily' | 'monthly' | 'quarterly' | 'static'
  active            boolean default true,
  added_at          timestamptz default now()
)

-- Daily snapshot of each product's composition.
-- For ETFs/funds: full constituent list.
-- For single-asset products (stocks, crypto): one row with weight_pct = 100, self-referencing.
product_composition_snapshot (
  as_of_date          date,
  product_isin        text references product(isin),
  constituent_isin    text,
  constituent_name    text,
  ticker              text,
  weight_pct          numeric(8,5),
  sector              text,
  country_listing     text,
  country_incorp      text,
  native_currency     text,
  asset_class         text,
  market_value_native numeric(20,2),
  shares              numeric(20,4),
  primary key (as_of_date, product_isin, constituent_isin)
)

-- Daily snapshot of user positions, read from Ghostfolio.
position_snapshot (
  as_of_date          date,
  product_isin        text references product(isin),
  quantity            numeric(20,4),
  market_value_native numeric(20,2),
  native_currency     text,
  market_value_eur    numeric(20,2),
  cost_basis_eur      numeric(20,2),
  primary key (as_of_date, product_isin)
)

-- Constituent-level reference data, enriched weekly.
instrument_reference (
  isin                text primary key,
  name                text,
  market_cap_eur      numeric(20,0),
  market_cap_bucket   text,                 -- 'Mega' | 'Large' | 'Mid' | 'Small' | 'Micro'
  last_refreshed_at   timestamptz
)

-- Manual country-of-risk overrides.
country_of_risk_override (
  isin            text primary key,
  country         text not null,
  note            text,
  updated_at      timestamptz default now()
)

-- ECB daily FX rates, EUR base.
fx_rate (
  as_of_date      date,
  currency_code   text,
  rate_to_eur     numeric(20,8),            -- 1 unit of currency_code = N EUR
  primary key (as_of_date, currency_code)
)

-- Pre-computed daily aggregates per (date, dimension, segment).
-- All allocation queries read from here, not from the raw snapshots.
portfolio_allocation_snapshot (
  as_of_date          date,
  dimension           text,                 -- 'sector' | 'country_listing' | 'country_incorp' |
                                            -- 'country_of_risk' | 'currency' | 'market_cap' |
                                            -- 'company' | 'product'
  segment_key         text,                 -- canonical key for the segment
  segment_label       text,                 -- display label
  value_eur           numeric(20,2),
  weight_pct          numeric(8,5),
  holding_count       integer,
  primary key (as_of_date, dimension, segment_key)
)

-- Observability: every fetcher and aggregation run logs here.
job_run (
  id              bigserial primary key,
  job_name        text,
  started_at      timestamptz,
  finished_at     timestamptz,
  status          text,                     -- 'ok' | 'failed' | 'partial'
  rows_written    integer,
  message         text
)
```

### Indexes

```sql
create index on product_composition_snapshot (constituent_isin, as_of_date);
create index on portfolio_allocation_snapshot (dimension, as_of_date);
create index on position_snapshot (as_of_date);
create index on job_run (job_name, started_at desc);
```

### Why `product` instead of `etf_fund`?

The original draft of the schema called this table `etf_fund` because the user's initial scope is three ETFs. Generalizing the name and adding `product_type` from day one means future expansion (single stocks, crypto, bonds) doesn't require painful renames or schema migrations later. Single-asset products are handled by a "self-snapshot" generator that writes one row with `weight_pct = 100, constituent_isin = product_isin`. The aggregator then treats single-stock holdings uniformly with ETF constituents in all six dimensions.

---

## 8. Scheduled jobs

All times are local. APScheduler runs them in-process.

| Job | Cadence | Time | Purpose |
|---|---|---|---|
| `ishares_holdings` | Mon–Fri | 22:00 | Pull holdings for each iShares product |
| `vanguard_holdings` | 1st of month | 22:00 | Pull holdings for each Vanguard product |
| `hsbc_holdings` | 5th of month | 22:00 | Pull holdings for each HSBC product (later in month because HSBC publishes later) |
| `ecb_fx` | Mon–Fri | 17:00 | Pull EUR-base FX rates from ECB |
| `market_cap_enrichment` | Sunday | 03:00 | yfinance batch lookup for known ISINs |
| `position_snapshot` | Daily | 23:00 | Read positions from Ghostfolio |
| `aggregate_allocation` | Daily | 00:00 | Compute `portfolio_allocation_snapshot` rows |

Each job writes to `job_run` on entry and exit. If a fetcher fails (e.g., HSBC publishes late), the aggregator uses the most recent valid composition for that product and logs a `partial` status. The dashboard surfaces stale-data warnings to the user.

Common HTTP hygiene for fetchers: `User-Agent: Mozilla/5.0 (InvestmentsBI/0.1)`, 30-second timeout, 3 retries with exponential backoff, response cached to disk by content hash.

---

## 9. Aggregation logic

The `aggregate_allocation` job at 00:00 runs this conceptually:

```
for each as_of_date being aggregated:
  load position_snapshot(as_of_date)
  for each (product_isin, market_value_eur) in positions:
    load latest valid product_composition_snapshot(product_isin, date <= as_of_date)
    for each constituent in composition:
      contribution_eur = market_value_eur * constituent.weight_pct / 100
      for each dimension in [sector, country_listing, country_incorp,
                              country_of_risk, currency, market_cap, company, product]:
        segment_key = derive_segment(dimension, constituent)
        accumulate contribution_eur into segment

  write all accumulated segments to portfolio_allocation_snapshot
```

`country_of_risk` falls back through: `country_of_risk_override(isin)` → `country_incorp` → `country_listing` → `'Unknown'`.

`market_cap` reads `instrument_reference.market_cap_bucket`, defaulting to `'Unknown'` for unmatched ISINs.

`currency` uses the constituent's `native_currency` — the answer to "how much of my portfolio is exposed to USD-denominated assets" regardless of how the ETF itself is listed.

---

## 10. REST API

All endpoints return JSON. Auth is handled by HA Ingress (you must be logged into HA to reach the app).

```
GET  /api/products                                       → configured products
GET  /api/health                                         → DB up, last successful run per job
GET  /api/snapshot/dates                                 → list of dates with snapshots
GET  /api/allocation?dimension=X&date=Y                  → top-level pivot
GET  /api/drill?dimension=X&segment=Y&date=Z             → drill into a segment
       returns:
         {
           segment: {key, label, value_eur, weight_pct},
           held_via: [{product_isin, name, contribution_pct, contribution_eur}],
           constituents: [{name, isin, weight_pct, contribution_eur}]
         }
GET  /api/product/{isin}/holdings?date=Y                 → full constituents of one product
GET  /api/timeseries?dimension=X&segments=A,B,C&from=&to= → drift data for selected segments
GET  /api/portfolio/total?date=Y                         → header KPIs
POST /api/admin/refresh?job=NAME                         → manual job trigger
POST /api/admin/override                                 → add country-of-risk override
```

---

## 11. UI design

Single page. State held in URL hash so back/forward navigation works (`#dimension=sector&segment=Technology&date=2026-05-18`).

Components, top to bottom:

1. **Header strip** — title, as-of date selector, four KPI cards (total value, # products, # look-through holdings, top single-name exposure).
2. **Pivot pills** — six buttons, one active. Selecting one re-fetches the main visualization.
3. **Main visualization** — chart type adapts to dimension:
   - Treemap for sector / country (any) / asset class (≤30 segments)
   - Donut for currency (3–6 segments)
   - Horizontal bar (top-N + "rest") for company and market cap
4. **Drill panel** — slides in from the right when a segment is clicked. Behavior depends on what you drilled into:
   - From a sector/country/currency/market-cap segment: shows "held via" (which products contribute) and top constituents
   - From a product segment: shows the full constituents list of that product, with search and a sub-pivot toggle ("group this product's holdings by sector / country / market cap")
   - Supports multi-level drill with a back-button stack
5. **Drift chart** — line chart of the top-N segments of the current pivot over the selected time range. For high-cardinality dimensions (company, market cap with sub-buckets), top-N defaults to 5 by current weight; user can toggle to top-N by absolute change; user can pin specific segments to always appear.
6. **Data table** — sortable, with a delta column showing change vs. a reference date (T-30 by default), and a CSV export button.

### Worth noting in the UI

- All values shown in EUR. Native currency surfaces only in the currency pivot and in per-constituent drill rows.
- A small stale-data indicator appears when a product's composition data is older than its expected cadence (e.g., HSBC monthly data older than 35 days).
- "Top single-name exposure" in the header KPI is computed from the company dimension; clicking it drills directly.

---

## 12. Extensibility

The design accommodates three extension patterns without schema changes:

### Adding another ETF from an existing issuer
Add a row to `product` with the new ISIN, ticker, source URL, and existing parser key. The fetcher picks it up on its next run.

### Adding a new ETF issuer
Write a new fetcher class implementing the common base interface (`fetchers/base.py`), register it under a new `parser` key. Then add `product` rows pointing to it. Approximately 50–80 lines per new issuer once you've written two.

### Adding a non-ETF product type (single stocks, crypto, bonds)
Add a `product` row with `product_type='stock'` (etc.) and `parser='self'`. The `self_snapshot` generator writes a 100%-weight composition row daily, drawing sector/country/currency from a small static reference table that the user can edit by hand. The aggregator handles it identically to ETF constituents.

The hard limit: the app can only do look-through analysis for products whose composition is publicly available. For active mutual funds with stale quarterly disclosures, opaque structured products, or wrappers, the fallback is single-line treatment.

### Adding a new dimension

The aggregation logic enumerates dimensions; adding a seventh (e.g., ESG rating, factor exposure) requires:

1. A new column or reference table holding the per-constituent attribute
2. A `derive_segment` case in the aggregator
3. A UI pill and a chart-type mapping

About a half-day of work per new dimension.

---

## 13. Constraints and known limitations

- **No historical backfill before deployment day.** Drift charts populate from M3 onward.
- **Country of risk requires manual maintenance** for ~10–20 ISINs where listing/incorporation diverge from operational risk.
- **Market cap buckets are uninteresting** until the portfolio includes non-large-cap funds. The plumbing exists from day one regardless.
- **Vanguard and HSBC publish monthly**, not daily — drift charts for exposures unique to those funds will show monthly steps rather than daily smoothness.
- **Schema coupling to Ghostfolio.** The adapter must be reviewed on each Ghostfolio upgrade. Mitigation: pin Ghostfolio version in operational docs; localize all Ghostfolio reads to one module.
- **Single user, single portfolio.** No tenancy logic.
- **No high availability.** Single Postgres, single app container; outages affect both Ghostfolio and InvestmentsBI.

---

## 14. Operational notes

- **Backups**: The Postgres LXC is already backed up as part of the Proxmox routine. The `investments_bi` database must be explicitly included in the backup job — it is a separate database from `ghostfolio` and will not be covered automatically if the backup only targets `ghostfolio`.
- **Disaster recovery**: All InvestmentsBI data except `position_snapshot` and `portfolio_allocation_snapshot` history can be reconstructed from issuer sources. Lost history is lost permanently (see §6).
- **Upgrades**: The HA add-on's update mechanism handles container upgrades. Schema migrations are managed by Alembic and run at container startup.
- **Observability**: The `job_run` table is the primary source of operational truth. The `/api/health` endpoint summarizes it. A simple "operations" panel in the UI surfaces failed and partial jobs from the last 7 days.

---

## 15. Future work (post-v1)

In priority order:

1. **Pinnable items on the drift chart** — already designed, deferred to keep v1 small.
2. **Manual country-of-risk override UI** — initially this is direct SQL; a small admin form would be nice.
3. **CSV export of any data table view.**
4. **Performance attribution** — answers "which of my segments contributed most to total return last quarter?" Requires joining position deltas with allocation snapshots over time.
5. **Wayback Machine backfill** — opportunistic historical reconstruction from archived issuer pages, where available. Best-effort.
6. **Additional product types** — extend the model as the user's portfolio diversifies.
7. **Mobile layout** — the dashboard is currently desktop-first. A condensed mobile view would be useful.

---

## Appendix A: Decision log

| Decision | Alternative considered | Reason chosen |
|---|---|---|
| HA add-on | Dedicated Proxmox LXC | Ingress + Tailscale integration; no resource constraint |
| Python | TypeScript / Node | Better parsing ecosystem (pandas); yfinance availability; data ingestion dominates |
| SQLAlchemy | Prisma (Python client) | Community port of TS-first tool; foreign-schema friendliness; institutional backing |
| FastAPI | Flask | Async story; Pydantic integration; OpenAPI |
| APScheduler | Cron in container, Celery | Simplicity at this scale; shares app process |
| Static frontend | React/Vue with build pipeline | Scope doesn't justify build tooling; reduces Docker image complexity |
| Issuer-direct holdings | Aggregator API (FMP, etc.) | Authoritative; no rate limits; UCITS coverage by definition |
| ECB FX feed | Currency aggregator | Free, official, EUR-base, daily |
| `product` table (generic) | `etf_fund` (specific) | Avoid future rename when expanding beyond ETFs |
| Pre-computed allocation snapshots | Query-time aggregation | Dashboard latency; simpler frontend code |
| Separate `investments_bi` database | Schema within `ghostfolio` database | Ghostfolio upgrades/restores cannot affect InvestmentsBI data; independent backup; cleaner ownership boundary |
| Database named `investments_bi` (snake_case) | `"investmentsBI"` (quoted) | Avoid identifier-quoting friction in every query |

---

## Appendix B: Initial portfolio (as of design time)

| Ticker (Xetra) | ISIN | Issuer | Index | Cadence |
|---|---|---|---|---|
| EUNL.DE (a.k.a. IWDA, SWDA) | IE00B4L5Y983 | iShares | MSCI World | Daily |
| VWCG.DE | IE00BK5BQX27 | Vanguard | FTSE Developed Europe | Monthly |
| H4Z3.DE | IE000KCS7J59 | HSBC | MSCI Emerging Markets | Monthly |

All three are accumulating UCITS ETFs domiciled in Ireland.

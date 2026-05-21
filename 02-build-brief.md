# InvestmentsBI — Build Brief

A starter brief for Claude Code. Pair with `01-design-and-architecture.md` for the rationale behind every choice below. This document tells you **what to build and in what order**; the design doc tells you **why**.

---

## What this is

A Home Assistant add-on that produces look-through allocation reporting for a personal investment portfolio managed in Ghostfolio. It is a sibling reporting application — never writes to Ghostfolio, never executes trades.

The user holds three UCITS ETFs initially (iShares IWDA, Vanguard VWCG, HSBC H4Z3) but the design must accommodate adding new ETFs and other product types (single stocks, bonds, crypto, mutual funds) without schema migrations.

---

## Environment (already in place)

- **Host**: Proxmox VE on a mini-PC.
- **Home Assistant OS** runs as a Proxmox VM. Tailscale add-on is already installed and exposes HA externally. Ghostfolio is already installed as an HA add-on.
- **Postgres 14+** runs in a separate Proxmox LXC. Ghostfolio uses a database named `ghostfolio` (tables in the `public` schema) on this Postgres. The host IP, port, and credentials will be provided via the add-on's `options` and secrets.

The new add-on must:
- Be deployable to Home Assistant via its add-on Supervisor.
- Connect to the existing Postgres LXC over the network.
- Create and own a new database named `investments_bi` on that Postgres instance.
- Read (never write) from the `ghostfolio` database.
- Be exposed through HA Ingress with a sidebar entry titled "InvestmentsBI".

---

## Locked technology choices

Do not deviate from these without first surfacing the question:

- **Python 3.12** (latest stable at time of writing)
- **FastAPI** for HTTP API
- **SQLAlchemy 2.x** for ORM, both for `investments_bi` (read-write) and the Ghostfolio adapter (read-only)
- **Alembic** for migrations of `investments_bi`
- **APScheduler** for scheduled jobs (in-process, with Postgres jobstore)
- **httpx** for HTTP fetchers
- **pandas + openpyxl** for parsing issuer CSV/XLSX
- **Pydantic v2** for config and request/response models
- **Static frontend**: plain HTML + vanilla JS + Chart.js v4 (no React, no build pipeline)
- **No Node, no npm** anywhere in the project

---

## Repository structure

```
investmentsbi/
├── repository.json              # HA add-on repository manifest
├── addon/                       # HA build context (everything Docker needs is inside here)
│   ├── config.yaml              # HA add-on manifest
│   ├── Dockerfile               # python:3.12-slim base, no Node
│   ├── run.sh                   # entrypoint: alembic upgrade head; uvicorn
│   ├── README.md                # HA add-on README (shown in HA UI)
│   └── backend/
│       ├── pyproject.toml
│       ├── alembic.ini
│       ├── alembic/
│       │   ├── env.py
│       │   └── versions/
│       └── app/
│           ├── main.py              # FastAPI app + static mount
│           ├── config.py            # Pydantic settings from env vars
│           ├── scheduler.py         # APScheduler bootstrap & job registration
│           ├── logging_config.py
│           ├── db/
│           │   ├── __init__.py
│           │   ├── reporting.py     # SQLAlchemy models for investments_bi
│           │   ├── reporting_session.py
│           │   ├── ghostfolio.py    # read-only adapter; minimal models for needed tables
│           │   └── ghostfolio_session.py
│           ├── fetchers/
│           │   ├── __init__.py
│           │   ├── base.py          # abstract base class + parser registry
│           │   ├── ishares.py
│           │   ├── vanguard.py
│           │   ├── hsbc.py
│           │   ├── self_snapshot.py # generates 100%-weight snapshot for single-asset products
│           │   ├── ecb_fx.py
│           │   └── market_cap.py    # yfinance enrichment
│           ├── aggregator/
│           │   ├── __init__.py
│           │   ├── position_snapshot.py     # reads Ghostfolio → writes position_snapshot
│           │   └── allocation.py            # computes portfolio_allocation_snapshot
│           ├── api/
│           │   ├── __init__.py
│           │   ├── allocation.py
│           │   ├── drill.py
│           │   ├── timeseries.py
│           │   ├── products.py
│           │   ├── health.py
│           │   └── admin.py
│           └── frontend/                    # served as static files
│               ├── index.html
│               ├── app.js
│               ├── style.css
│               └── treemap.js
└── docs/
    ├── design-and-architecture.md       # the design doc
    └── ghostfolio-schema-notes.md       # which Ghostfolio tables we read, pinned to a version
```

---

## Schema DDL

Create the tables below in the `investments_bi` database (tables live in the `public` schema). Database name is **lowercase snake_case** (`investments_bi`) to avoid identifier-quoting friction.

```sql
create table product (
  isin              text primary key,
  ticker            text,
  name              text not null,
  product_type      text not null check (product_type in
                      ('etf','stock','bond','mutual_fund','crypto','cash')),
  issuer            text,
  base_currency     text,
  source_url        text,
  parser            text,
  cadence           text check (cadence in ('daily','monthly','quarterly','static')),
  active            boolean not null default true,
  added_at          timestamptz not null default now()
);

create table product_composition_snapshot (
  as_of_date          date not null,
  product_isin        text not null references product(isin),
  constituent_isin    text not null,
  constituent_name    text,
  ticker              text,
  weight_pct          numeric(8,5) not null,
  sector              text,
  country_listing     text,
  country_incorp      text,
  native_currency     text,
  asset_class         text,
  market_value_native numeric(20,2),
  shares              numeric(20,4),
  primary key (as_of_date, product_isin, constituent_isin)
);
create index on product_composition_snapshot (constituent_isin, as_of_date);

create table position_snapshot (
  as_of_date          date not null,
  product_isin        text not null references product(isin),
  quantity            numeric(20,4) not null,
  market_value_native numeric(20,2) not null,
  native_currency     text not null,
  market_value_eur    numeric(20,2) not null,
  cost_basis_eur      numeric(20,2),
  primary key (as_of_date, product_isin)
);
create index on position_snapshot (as_of_date);

create table instrument_reference (
  isin                text primary key,
  name                text,
  market_cap_eur      numeric(20,0),
  market_cap_bucket   text check (market_cap_bucket in
                        ('Mega','Large','Mid','Small','Micro','Unknown')),
  last_refreshed_at   timestamptz
);

create table country_of_risk_override (
  isin            text primary key,
  country         text not null,
  note            text,
  updated_at      timestamptz not null default now()
);

create table fx_rate (
  as_of_date      date not null,
  currency_code   text not null,
  rate_to_eur     numeric(20,8) not null,
  primary key (as_of_date, currency_code)
);

create table portfolio_allocation_snapshot (
  as_of_date          date not null,
  dimension           text not null,
  segment_key         text not null,
  segment_label       text not null,
  value_eur           numeric(20,2) not null,
  weight_pct          numeric(8,5) not null,
  holding_count       integer not null,
  primary key (as_of_date, dimension, segment_key)
);
create index on portfolio_allocation_snapshot (dimension, as_of_date);

create table job_run (
  id              bigserial primary key,
  job_name        text not null,
  started_at      timestamptz not null,
  finished_at     timestamptz,
  status          text not null check (status in ('running','ok','failed','partial')),
  rows_written    integer,
  message         text
);
create index on job_run (job_name, started_at desc);
```

All DDL is delivered as the initial Alembic migration. Subsequent schema changes go through new Alembic revisions.

### Allowed values for `dimension`

```
'company' | 'sector' | 'country_listing' | 'country_incorp' | 'country_of_risk'
| 'currency' | 'market_cap' | 'product'
```

---

## Initial products (seed data)

These three are seeded by the first Alembic migration:

| ISIN | Ticker | Name | Type | Issuer | Base ccy | Cadence | Parser |
|---|---|---|---|---|---|---|---|
| IE00B4L5Y983 | EUNL.DE | iShares Core MSCI World UCITS ETF (Acc) | etf | ishares | USD | daily | ishares_csv |
| IE00BK5BQX27 | VWCG.DE | Vanguard FTSE Developed Europe UCITS ETF (Acc) | etf | vanguard | EUR | monthly | vanguard_xlsx |
| IE000KCS7J59 | H4Z3.DE | HSBC MSCI Emerging Markets UCITS ETF (Acc) | etf | hsbc | USD | monthly | hsbc_xlsx |

`source_url` for each is filled in once the exact stable URL is captured from each issuer's product page network inspector. Store these in the seed migration as concrete values, not at runtime discovery.

---

## Module specifications

### `fetchers/base.py`

Defines `class BaseFetcher` with abstract `fetch(product: Product) -> list[NormalizedHolding]`. Provides shared utilities: HTTP client with retry and the `Mozilla/5.0 (InvestmentsBI/0.1)` user agent, on-disk content-hash cache under `/data/cache/`, normalization helpers (`normalize_currency`, `normalize_country`, `normalize_sector` for canonical GICS values).

A `NormalizedHolding` is a Pydantic model with the same columns as `product_composition_snapshot` minus `as_of_date` and `product_isin`.

Parser registry: a dict mapping `parser` string → fetcher class. Loaded at startup.

### `fetchers/ishares.py`

Fetches CSV from the `.ajax` URL. Skip metadata rows by scanning for the line that starts with `Ticker`. Parse with `pandas.read_csv`. Filter to `Asset Class == 'Equity'` by default but expose all asset classes (for bond-ETF compatibility). Map columns: `Ticker → ticker`, `Name → constituent_name`, `ISIN → constituent_isin`, `Weight (%) → weight_pct`, `Sector → sector`, `Location → country_listing`, `Market Currency → native_currency`, `Asset Class → asset_class`. Use `utf-8-sig` decoding to strip the BOM.

### `fetchers/vanguard.py`

XLSX. The export URL contains the fund's Vanguard product ID. Skip ~9 metadata rows; locate header by scanning for the row containing "Holding name". Column mapping is documented per-issuer in code; columns are similar in spirit but different in label from iShares.

### `fetchers/hsbc.py`

XLSX. Layout differs from Vanguard; capture the exact format when implementing.

### `fetchers/self_snapshot.py`

For products with `parser='self'`. Produces one `NormalizedHolding` per product with `weight_pct=100, constituent_isin=product.isin`. Other fields (sector, country, currency) come from a `static_product_reference` table or fall back to the `product` row itself. Used for single stocks, crypto, cash holdings.

### `fetchers/ecb_fx.py`

Fetches `https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml`, parses with `xml.etree.ElementTree`, writes one `fx_rate` row per currency.

### `fetchers/market_cap.py`

Reads all `constituent_isin` values from the last week of `product_composition_snapshot`, batches them through `yfinance` to get market cap, writes/updates `instrument_reference` rows. Derives bucket from a config-driven threshold table (Mega ≥ €200B, Large €10B–200B, Mid €2B–10B, Small €300M–2B, Micro < €300M). Caches aggressively; failures are non-blocking (constituent gets `market_cap_bucket='Unknown'`).

### `aggregator/position_snapshot.py`

The single point of contact with the Ghostfolio schema. Reads `Order`, `SymbolProfile`, `Account`, and `MarketData` tables. Resolves user positions as of the given date in their native currencies, converts to EUR using same-date FX rate, writes `position_snapshot` rows. Pin to the current Ghostfolio version in module docstring; on Ghostfolio upgrades, this module is the first place to check.

### `aggregator/allocation.py`

For a given as-of date and the list of all eight dimensions, computes `portfolio_allocation_snapshot` rows. Logic in §9 of the design doc. Country of risk falls back: override → incorporation → listing → 'Unknown'. Market cap reads `instrument_reference.market_cap_bucket`.

### `api/allocation.py`

`GET /api/allocation?dimension=X&date=Y` — reads `portfolio_allocation_snapshot` directly. Returns sorted by `weight_pct desc`. Default date is the latest available.

### `api/drill.py`

`GET /api/drill?dimension=X&segment=Y&date=Z` — joins `position_snapshot` with `product_composition_snapshot` filtered to the segment. Returns:
- `held_via`: list of products whose constituents include the segment, with per-product contribution
- `constituents`: list of constituents matching the segment, ranked by contribution to portfolio (not by weight within their respective products)

`GET /api/product/{isin}/holdings?date=Y` — full constituent list of one product, used when the user pivots by product and drills.

### `api/timeseries.py`

`GET /api/timeseries?dimension=X&segments=A,B,C&from=&to=` — returns one series per requested segment over the date range. For high-cardinality dimensions (`company`, `market_cap`) the caller must pass explicit segments; the API does not auto-pick.

### `api/health.py`

Reports DB connectivity (both schemas), last successful run of each scheduled job, list of `failed` or `partial` runs in the last 7 days.

### `api/admin.py`

`POST /api/admin/refresh?job=NAME` triggers a job ad hoc (useful for first-time setup and after errors). `POST /api/admin/override` upserts a `country_of_risk_override` row.

### `scheduler.py`

APScheduler with Postgres jobstore. Registers all 7 jobs with cron triggers per §8 of the design doc. Each job wraps execution in a context manager that writes `job_run` rows and propagates exceptions to logs but never to the scheduler (so one failed job doesn't kill the process).

### Frontend `app.js`

Single-page state machine. URL hash holds `(dimension, segment, date)` and is the source of truth. On hash change, refetch and re-render. Components:
- KPI cards: read `/api/portfolio/total`
- Pivot pills: change hash
- Main viz: chart type per dimension (treemap / donut / bar)
- Drill panel: opens on segment click, multi-level stack
- Drift chart: reads `/api/timeseries`, supports pinned segments stored in localStorage
- Table: reads `/api/allocation` with delta vs. `(date - 30 days)`

### Frontend `treemap.js`

Squarified treemap algorithm in ~80 lines of vanilla JS. SVG output. Renders into a passed-in container with passed-in dimensions. Click handler emits a custom event with the segment key.

---

## HA add-on `config.yaml`

```yaml
name: "InvestmentsBI"
version: "0.1.0"
slug: "investmentsbi"
description: "Look-through allocation reporting for Ghostfolio portfolios"
arch: [amd64, aarch64]
init: false
ingress: true
ingress_port: 8000
panel_icon: mdi:chart-donut
panel_title: "InvestmentsBI"
options:
  postgres_host: ""
  postgres_port: 5432
  postgres_db: "ghostfolio"
  postgres_db_bi: "investments_bi"
  postgres_user_rw: ""
  postgres_user_ro: ""
  ghostfolio_account_id: ""
  base_currency: "EUR"
  snapshot_local_time: "00:00"
  log_level: "INFO"
schema:
  postgres_host: str
  postgres_port: port
  postgres_db: str
  postgres_db_bi: str
  postgres_user_rw: str
  postgres_user_ro: str
  ghostfolio_account_id: str?
  base_currency: str
  snapshot_local_time: str
  log_level: "list(DEBUG|INFO|WARNING|ERROR)"
```

Database passwords are read from HA secrets, not from `options`. Two database users:
- `reporter_rw` — full grants on `investments_bi`
- `reporter_ro` — `SELECT` on `public` schema of the `ghostfolio` database

Both are created manually on Postgres before first run; the add-on does not provision Postgres users.

---

## Conventions

- **Type hints required everywhere**; mypy strict on `app/` (excluding migrations).
- **Logging via stdlib `logging`** with a JSON formatter for production. `logging_config.py` is the single setup point.
- **Errors**: fetchers raise on unrecoverable issues, return partial results with warnings on recoverable ones. The aggregator never raises out of a scheduled job — it logs and writes `failed`/`partial` to `job_run`.
- **Tests**: pytest. Fixtures provide a clean `investments_bi` database and a sample `ghostfolio` database with fake data. Test the parsers with canned issuer files committed to the repo (one minimal sample per issuer).
- **No print statements**, no commented-out code, no TODOs without an issue number.

---

## Build order

Implement in this order; each milestone is end-to-end-working before moving on.

### M1 — Skeleton
- `pyproject.toml` with all dependencies
- `addon/config.yaml`, `Dockerfile`, `run.sh`
- FastAPI app serving a hello-world JSON endpoint and a placeholder `index.html`
- Postgres connection bootstrap (both users, both schemas)
- Alembic baseline that creates the empty `investments_bi` schema
- HA add-on builds, installs, and shows the placeholder page through Ingress
- Exit criteria: open HA, click "InvestmentsBI" in the sidebar, see "hello"

### M2 — iShares fetcher + IWDA only
- Implement `fetchers/base.py` and `fetchers/ishares.py`
- Seed migration: insert IWDA into `product`
- Alembic creates `product_composition_snapshot`, `job_run`
- Manual API endpoint `POST /api/admin/refresh?job=ishares_holdings`
- Verify: after one fetch, `product_composition_snapshot` has ~1,500 rows for IWDA
- Exit criteria: a manual fetch produces a clean composition snapshot in the DB

### M3 — Ghostfolio adapter + position snapshot
- `db/ghostfolio.py` with minimal models for `Order`, `SymbolProfile`, `Account`, `MarketData`
- `aggregator/position_snapshot.py` that produces today's positions
- Alembic creates `position_snapshot`, `fx_rate`
- `fetchers/ecb_fx.py` for FX conversion
- Manual endpoint `POST /api/admin/refresh?job=position_snapshot`
- Exit criteria: `position_snapshot` reflects the user's actual Ghostfolio holdings as of today, valued in EUR

### M4 — Aggregation + first chart
- Alembic creates `portfolio_allocation_snapshot`
- `aggregator/allocation.py` implementing the sector dimension only
- `GET /api/allocation?dimension=sector` working
- Frontend: pivot pills (visual only, only sector active), main chart drawing a bar of sector allocations
- Exit criteria: the UI shows real sector allocation for the user's IWDA-only portfolio

### M5 — All dimensions + treemap + drill panel
- Aggregator handles all 8 dimensions
- Frontend treemap working
- Drill panel implemented with `held_via` and `constituents`
- Frontend pivot pills all functional
- Exit criteria: every pivot pill renders correctly; clicking any segment drills

### M6 — Vanguard + HSBC fetchers
- Implement and test both
- Add seed entries for VWCG and H4Z3
- Aggregator handles missing snapshots gracefully (uses latest valid; flags stale)
- Exit criteria: all three real products visible in pivots; "stale data" indicator works

### M7 — Historical drift + table + polish
- `aggregator/allocation.py` runs nightly via APScheduler
- `GET /api/timeseries` and the drift chart on the frontend
- Data table with delta column and CSV export
- Manual `instrument_reference` enrichment via `fetchers/market_cap.py`
- Country-of-risk override admin endpoint
- Health endpoint + simple ops panel
- Exit criteria: feature-complete v1

---

## Explicitly out of scope (do not build)

- Authentication or user management — HA Ingress handles it
- Writes to Ghostfolio's schema
- Trade execution, transaction entry, portfolio modification
- Performance attribution and P&L
- Real-time price tracking
- Multi-user or multi-portfolio features
- Backfilling historical compositions from before the app's first run
- A React/Vue/Svelte frontend
- Any Node.js or npm dependency
- Email or push notifications

---

## Things to surface, not assume

When implementing, raise these to the user rather than guessing:

- The exact stable URLs for each issuer's holdings file (capture from product page network inspector)
- Whether yfinance ISIN lookups cover enough of the user's constituents (run a coverage check after M5)
- The right Ghostfolio account ID if the user has more than one account
- Postgres user creation steps and grants (these are manual prerequisites)
- The current Ghostfolio version, to pin the adapter

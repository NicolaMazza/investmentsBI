# InvestmentsBI

Look-through allocation reporting for Ghostfolio portfolios.

## Prerequisites

Before installing, create two Postgres users on your database server:

```sql
-- Run as a superuser on the ghostfolio database
CREATE USER reporter_rw WITH PASSWORD 'choose-a-password';
CREATE USER reporter_ro WITH PASSWORD 'choose-a-password';

-- reporter_rw owns investments_bi (schema created by the add-on on first start)
GRANT CREATE ON DATABASE ghostfolio TO reporter_rw;

-- reporter_ro reads ghostfolio schema
GRANT USAGE ON SCHEMA ghostfolio TO reporter_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA ghostfolio TO reporter_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA ghostfolio GRANT SELECT ON TABLES TO reporter_ro;
```

## Configuration

| Option | Description |
|--------|-------------|
| `postgres_host` | IP or hostname of your Postgres server |
| `postgres_port` | Postgres port (default 5432) |
| `postgres_db` | Database name (default `ghostfolio`) |
| `postgres_user_rw` | Read-write user for `investments_bi` schema |
| `postgres_password_rw` | Password for the RW user (use `!secret`) |
| `postgres_user_ro` | Read-only user for `ghostfolio` schema |
| `postgres_password_ro` | Password for the RO user (use `!secret`) |
| `ghostfolio_account_id` | Ghostfolio account UUID (optional; omit to aggregate all accounts) |
| `base_currency` | Reporting currency (default `EUR`) |
| `snapshot_local_time` | Time of day for nightly snapshot job (default `00:00`) |
| `log_level` | Log verbosity: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

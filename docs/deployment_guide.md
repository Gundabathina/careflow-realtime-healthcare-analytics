# Deployment Guide

This project runs locally via Docker Compose (PostgreSQL + optional
Airflow) today -- there is no cloud deployment configured, and the
README deliberately doesn't claim one (see
[`README.md#22-future-improvements`](../README.md#22-future-improvements)).
This guide documents how each piece *could* be deployed, grounded in
what actually exists in the repository right now, so it's accurate
rather than aspirational marketing copy.

Two things are deployable independently:

1. **The Streamlit dashboard** -- a read-only app that only needs
   network access to a PostgreSQL instance with the warehouse loaded.
2. **PostgreSQL + the pipeline** -- generating data and loading the
   warehouse is a batch process; it doesn't need to run continuously,
   only when refreshing data.

Nothing below has been provisioned or tested against a live cloud
account for this project -- treat each section as a verified-accurate
runbook, not a claim that a public deployment currently exists.

## 1. Docker (local / self-hosted)

The only deployment method actually configured in this repository today.

```bash
cp .env.example .env
# edit .env -- set real values for anything beyond local dev

# PostgreSQL only (lightweight)
docker compose up -d postgres

# PostgreSQL + Airflow (opt-in profile)
docker compose --profile airflow up -d
```

Services (from `docker-compose.yml`):

| Service | Image | Purpose | Host port |
|---|---|---|---|
| `postgres` | `postgres:16-alpine` | CareFlow analytics warehouse | `${POSTGRES_PORT:-5432}` |
| `airflow-postgres` | `postgres:16-alpine` | Airflow's metadata DB (separate credentials) | internal only, no host port |
| `airflow-webserver` | `apache/airflow:2.9.3-python3.11` | Airflow UI | `${AIRFLOW_WEBSERVER_PORT:-8081}` |
| `airflow-scheduler` | `apache/airflow:2.9.3-python3.11` | Airflow scheduler | internal only |

Data persists in named volumes (`careflow_postgres_data`,
`careflow_airflow_metadata_data`, `careflow_airflow_logs`) -- `docker
compose down` alone does not delete them; `docker compose down -v`
does.

To containerize the dashboard itself for deployment (there is no
`Dockerfile` in the repo -- Render's `render.yaml` blueprint below uses
a native Python runtime instead), a minimal one would look like:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements-dashboard.txt .
RUN pip install --no-cache-dir -r requirements-dashboard.txt
COPY dashboard/ dashboard/
COPY src/ src/
COPY config/ config/
ENV PYTHONPATH=/app/src
CMD streamlit run dashboard/CareFlow_Analytics.py --server.address 0.0.0.0 --server.port ${PORT:-8501} --server.headless true
```

`requirements-dashboard.txt` is a real, tracked file at the repo root
mirroring the isolated `.venv-dashboard` dependency set documented in
[`dashboard_guide.md`](dashboard_guide.md). Note the `CMD` uses shell
form (not exec-form JSON array) specifically so `${PORT:-8501}`
expands at container start -- most platforms (Render, Railway, Cloud
Run) inject `$PORT` and expect the container to bind to it rather than
a fixed port; `8501` is only the local-development fallback.

## 2. Streamlit Community Cloud (dashboard only)

Best fit for sharing the live dashboard for free, pointed at an
externally-reachable PostgreSQL instance (see options 3-5).

1. Push this repository to GitHub (already done).
2. `requirements-dashboard.txt` at the repo root already has the
   dashboard dependency set (Streamlit Cloud installs from a single
   requirements file; it cannot use the project's isolated
   `.venv-dashboard` setup directly) -- point Streamlit Cloud's
   "Requirements file" field at it if it doesn't auto-detect it.
3. In [share.streamlit.io](https://share.streamlit.io), create a new
   app pointing at this repo, branch `main`, main file path
   `dashboard/CareFlow_Analytics.py`.
4. In the app's **Secrets** (Settings -> Secrets), set the same
   variables as `.env.example`'s PostgreSQL block:
   ```toml
   POSTGRES_HOST = "your-external-postgres-host"
   POSTGRES_PORT = "5432"
   POSTGRES_DB = "careflow"
   POSTGRES_USER = "careflow_user"
   POSTGRES_PASSWORD = "replace_with_a_real_value_change_me"
   POSTGRES_SSLMODE = "require"
   ```
   Streamlit Cloud injects secrets as environment variables, which
   `careflow.warehouse.postgres_client.load_connection_config()` reads
   the same way it reads `.env` locally -- no code change needed.
   `POSTGRES_SSLMODE` is optional but should be `require` for any
   managed/cloud PostgreSQL instance (see "PostgreSQL SSL" below).
5. The PostgreSQL instance must be reachable from the public internet
   (or Streamlit Cloud's egress IPs allow-listed) -- see options 3-5
   for a managed instance, since Streamlit Cloud has no database
   hosting of its own.

## 3. Render (target platform for the first public deployment)

Good fit for hosting **both** PostgreSQL and the dashboard under one
provider, and the platform this project is actually prepared for --
`render.yaml` at the repo root is a real
[Render Blueprint](https://render.com/docs/blueprint-spec), not just
documentation. It declares:

- a managed PostgreSQL instance (`careflow-postgres`)
- a web service (`careflow-analytics-dashboard`) that builds with
  `pip install -r requirements-dashboard.txt` and starts with
  `streamlit run dashboard/CareFlow_Analytics.py --server.address 0.0.0.0 --server.port $PORT --server.headless true`
  (`$PORT` is Render's injected port -- never hard-coded to 8501 here)
- `POSTGRES_HOST`/`PORT`/`DB`/`USER`/`PASSWORD` wired automatically from
  the managed database via `fromDatabase`, so no credential is typed
  into the Render dashboard by hand
- `POSTGRES_SSLMODE=require`, since Render's managed PostgreSQL
  requires SSL (see "PostgreSQL SSL" below)

**To deploy (manual steps, not run by this repository or by any
assistant):**

1. On [render.com](https://render.com), **New +** -> **Blueprint**,
   connect this GitHub repository (`Gundabathina/careflow-realtime-healthcare-analytics`),
   branch `main`.
2. Render reads `render.yaml` and proposes the database + web service
   above -- review and apply.
3. Wait for both resources to provision. The web service will build
   and start, but the dashboard will show "PostgreSQL warehouse is not
   reachable" (or an empty warehouse) until the database is
   bootstrapped -- see "Cloud database bootstrap" below.
4. Once bootstrapped, reload the deployed URL -- the landing page's
   "Connected to the CareFlow PostgreSQL warehouse" message confirms
   it's live.

Render's Blueprint field names occasionally change between platform
versions -- if `render.yaml` fails to parse, check the current spec at
the link above before editing field names.

**Manual (non-Blueprint) alternative**, if you'd rather wire an
existing Render Postgres instance by hand instead of applying the
Blueprint: New -> Web Service -> connect this repo -> Build command
`pip install -r requirements-dashboard.txt` -> Start command
`streamlit run dashboard/CareFlow_Analytics.py --server.address 0.0.0.0 --server.port $PORT --server.headless true`
-> set `POSTGRES_HOST`/`PORT`/`DB`/`USER`/`PASSWORD`/`SSLMODE`
environment variables to the existing database's connection details.

## PostgreSQL SSL

`src/careflow/warehouse/postgres_client.py` supports an optional
`POSTGRES_SSLMODE` environment variable (added for this deployment
phase). Unset, it behaves exactly as it always has (libpq's own
`prefer` default) -- local Docker Compose PostgreSQL doesn't speak SSL
and is completely unaffected. Any managed/cloud PostgreSQL (Render,
RDS, Supabase, etc.) should set `POSTGRES_SSLMODE=require`. This is
wired automatically in `render.yaml`; set it explicitly in
`.env`/platform secrets for every other deployment target in this
guide.

## Cloud database bootstrap

Loading a fresh managed PostgreSQL database reuses the project's
existing schema manager, warehouse loader, and validation scripts --
there is no separate "production" pipeline. Do this once per fresh
database (and again any time you want to refresh it with newly
generated data):

```bash
# 1. Generate the Gold layer locally first, if you haven't already
#    (see README.md#15-running-the-pipeline) -- this step reads only
#    data/gold/*.parquet, it does not regenerate it.

# 2. Point the environment at the target managed database instead of
#    local Docker Compose Postgres -- e.g. export the values Render
#    shows for the `careflow-postgres` database it provisioned
#    (Dashboard -> careflow-postgres -> Connect), or source a separate
#    .env.production with the same POSTGRES_* variable names:
export POSTGRES_HOST=<render-postgres-host>
export POSTGRES_PORT=<render-postgres-port>
export POSTGRES_DB=<render-postgres-database>
export POSTGRES_USER=<render-postgres-user>
export POSTGRES_PASSWORD=<render-postgres-password>
export POSTGRES_SSLMODE=require

# 3. Create schema + load every table (this is the SAME command used
#    locally -- schema_manager.ensure_schema() runs automatically as
#    part of a normal load, no separate schema-only step needed for a
#    brand-new database):
PYTHONPATH=src python3 scripts/load_postgres_warehouse.py --force

# 4. Validate the load the same way local loads are validated:
PYTHONPATH=src python3 scripts/validate_postgres_warehouse.py
```

This is exactly `scripts/load_postgres_warehouse.py` and
`scripts/validate_postgres_warehouse.py` -- the same two commands
documented in [`README.md#15-running-the-pipeline`](../README.md#15-running-the-pipeline)
-- pointed at a different `POSTGRES_HOST` via environment variables.
Nothing about the loader or validator changes for a cloud target; only
the connection config differs.

## 4. Railway

Similar shape to Render, via `railway.app`:

1. New Project -> Provision PostgreSQL (managed instance, connection
   variables exposed automatically as `PGHOST`/`PGPORT`/etc. -- map
   these to `POSTGRES_HOST`/`POSTGRES_PORT`/`POSTGRES_DB`/`POSTGRES_USER`/
   `POSTGRES_PASSWORD` in the dashboard service's variables, since the
   project's connection loader expects the `POSTGRES_*` names).
2. New Project -> Deploy from GitHub repo -> select this repository.
3. Set the build/start commands the same as the Render section, or add
   the suggested `Dockerfile` above and let Railway build it directly
   (Railway auto-detects a `Dockerfile` if present).
4. Load the warehouse once against Railway's PostgreSQL connection
   string the same way as the Render section.

## 5. AWS EC2 (full stack, most control)

Closest to a real production deployment -- runs Docker Compose exactly
as-is on a persistent instance.

1. Launch an EC2 instance (Ubuntu 22.04+, `t3.medium` or larger --
   PostgreSQL + Airflow together need more than the smallest instance
   sizes), with a security group allowing inbound `22` (SSH), `8501`
   (Streamlit, if hosting the dashboard here too), and `8081` (Airflow
   UI, only if needed and only from trusted IPs).
2. Install Docker + Docker Compose plugin, clone this repository.
3. `cp .env.example .env` and set real values -- **never reuse the
   `_change_me` example values** for anything internet-reachable.
4. `docker compose up -d postgres` (add `--profile airflow` for
   orchestration).
5. Run the pipeline once to load the warehouse (see
   [`../README.md#15-running-the-pipeline`](../README.md#15-running-the-pipeline)),
   or schedule `careflow_daily_analytics` via the running Airflow
   instance for ongoing incremental loads.
6. Run the dashboard as a systemd service or inside the suggested
   Docker container above, behind an nginx reverse proxy with TLS
   (e.g. via `certbot`) if exposing it publicly -- Streamlit's built-in
   server is not itself a production-hardened edge server.
7. Restrict PostgreSQL (`5432`) to the security group only -- never
   open it to `0.0.0.0/0`.

## Common to every option

- Never deploy with the `.env.example` placeholder values -- every
  `_change_me` value must be replaced with a real, unique secret before
  anything is internet-reachable (see
  [`security.md`](security.md)).
- The warehouse only needs to be *loaded*, not continuously running a
  pipeline -- the dashboard queries `careflow_dbt_mart` directly and
  doesn't require Airflow to be deployed at all if you're only sharing
  the dashboard.
- All data is synthetic (Synthea) -- there is no real-PHI handling
  requirement for any of the above, but the PII-column guard
  (`dashboard/database.py:assert_no_restricted_columns`) still runs
  regardless of deployment target.

## See also

- [`../docker-compose.yml`](../docker-compose.yml) -- the only deployment config currently in the repo
- [`dashboard_guide.md`](dashboard_guide.md) -- isolated dashboard environment setup
- [`security.md`](security.md) -- credential handling policy

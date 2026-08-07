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

To containerize the dashboard itself for deployment (not currently in
the repo -- this is a suggested pattern, not an existing file), a
minimal `Dockerfile` would look like:

```dockerfile
FROM python:3.11-slim
WORKDIR /app
COPY requirements-dashboard.txt .
RUN pip install --no-cache-dir -r requirements-dashboard.txt
COPY dashboard/ dashboard/
COPY src/ src/
COPY config/ config/
ENV PYTHONPATH=/app/src
EXPOSE 8501
CMD ["streamlit", "run", "dashboard/app.py", "--server.port=8501", "--server.address=0.0.0.0"]
```

where `requirements-dashboard.txt` mirrors the isolated
`.venv-dashboard` dependency set documented in
[`dashboard_guide.md`](dashboard_guide.md):
`streamlit plotly "psycopg[binary]>=3.3" pandas pyyaml`.

## 2. Streamlit Community Cloud (dashboard only)

Best fit for sharing the live dashboard for free, pointed at an
externally-reachable PostgreSQL instance (see options 3-5).

1. Push this repository to GitHub (already done).
2. Add `requirements-dashboard.txt` at the repo root with the dashboard
   dependency set above (Streamlit Cloud installs from a single
   requirements file; it cannot use the project's isolated
   `.venv-dashboard` setup directly).
3. In [share.streamlit.io](https://share.streamlit.io), create a new
   app pointing at this repo, branch `main`, main file path
   `dashboard/app.py`.
4. In the app's **Secrets** (Settings -> Secrets), set the same
   variables as `.env.example`'s PostgreSQL block:
   ```toml
   POSTGRES_HOST = "your-external-postgres-host"
   POSTGRES_PORT = "5432"
   POSTGRES_DB = "careflow"
   POSTGRES_USER = "careflow_user"
   POSTGRES_PASSWORD = "replace_with_a_real_value_change_me"
   ```
   Streamlit Cloud injects secrets as environment variables, which
   `careflow.warehouse.postgres_client.load_connection_config()` reads
   the same way it reads `.env` locally -- no code change needed.
5. The PostgreSQL instance must be reachable from the public internet
   (or Streamlit Cloud's egress IPs allow-listed) -- see options 3-5
   for a managed instance, since Streamlit Cloud has no database
   hosting of its own.

## 3. Render

Good fit for hosting **both** PostgreSQL and the dashboard under one
provider.

**PostgreSQL:** Render -> New -> PostgreSQL. Load the warehouse once
locally against Render's external connection string (`load_postgres_warehouse.py`
reads `POSTGRES_*` from the environment, so point those vars at
Render's host/port/db/user/password for a one-time load), or re-run the
full pipeline with `POSTGRES_HOST` set to the Render host.

**Dashboard (Web Service):**
1. New -> Web Service -> connect this GitHub repo.
2. Build command: `pip install -r requirements-dashboard.txt` (see
   Docker section above for its contents).
3. Start command: `streamlit run dashboard/app.py --server.port=$PORT --server.address=0.0.0.0`
   (Render injects `$PORT`; `scripts/start_dashboard.py --port` is for
   local use and isn't needed here).
4. Environment variables: the same `POSTGRES_*` block as `.env.example`,
   pointed at the Render PostgreSQL instance's internal connection
   details (same-region services can use Render's private network,
   avoiding a public database endpoint).

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

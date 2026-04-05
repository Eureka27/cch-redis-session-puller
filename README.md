# cch-redis-session-puller

`cch-redis-session-puller` exports existing data produced by `claude-code-hub` through two entrypoints:

- `redis_puller`: incrementally reads Redis `session:*` keys and appends per-session JSONL event files.
- `db_exporter`: incrementally reads PostgreSQL tables and writes daily JSONL partitions.

For a complete dataset, deploy both entrypoints together.

## Prerequisite

Deploy `claude-code-hub` first:  
`https://github.com/ding113/claude-code-hub`

This project exports data that already exists in Redis and PostgreSQL. It does not add new instrumentation.

## Optional: Low-Storage Source Server Setup

If the source server has limited disk space:

- Deploy `cch-local-pull` server on the source server:  
  `https://github.com/Eureka27/cch-local-pull`
- Deploy `cch-local-pull` client on a data server:  
  `https://github.com/Eureka27/cch-local-pull`

Important: `session_dir` in the `cch-local-pull` server must point to this project's `DEST_DIR`.

## What Each Entrypoint Collects

### Redis puller

Files:

- `DEST_DIR/<session_id>.json`
- `REDIS_SIDECARS_DIR/<session_id>.json`

Primary session event output:

- `session_meta`
- `user_input`
- `tool_io`
- `llm_answer`

Sidecar output:

- `session_info`
- `session_usage`
- `request_body`
- `request_special_settings`
- `client_request_meta`
- `upstream_request_meta`
- `upstream_response_meta`
- `request_headers`
- `response_headers`
- `response_body`

### DB exporter

Files:

- `DB_EXPORT_DIR/message_request/YYYY-MM-DD.jsonl`
- `DB_EXPORT_DIR/usage_ledger/YYYY-MM-DD.jsonl`

Tables:

- `message_request`
- `usage_ledger`

State files are stored under `EXPORT_ROOT/state/` by default.

## Deployment Recommendation

Deploy both services in production:

- `redis_puller` provides session-level events, sidecars, and transient request/response context.
- `db_exporter` provides structured request records and usage ledger data.

Running only one of them gives you only part of the dataset.

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## One-Click Deploy

`deploy/deploy-oneclick.sh` installs and starts both systemd services:

- `cch-redis-session-puller.service`
- `cch-db-exporter.service`

Before running it, review the configuration block at the top of the script and set at least:

- Redis connection: `REDIS_URL` or `REDIS_CONTAINER`
- Database connection: `DATABASE_URL` or `DSN`
- Output root: `EXPORT_ROOT`

Run:

```bash
bash deploy/deploy-oneclick.sh
```

## Environment

Common:

- `EXPORT_ROOT` default: `./export`

Redis puller:

- `REDIS_URL` optional if `REDIS_CONTAINER` is set
- `REDIS_CONTAINER` default: `claude-code-hub-redis`
- `DEST_DIR` default: `./export/redis/session_events`
- `REDIS_SIDECARS_DIR` default: `./export/redis/request_sidecars`
- `STATE_PATH` default: `./export/state/redis_puller.json`
- `POLL_INTERVAL_SECONDS` default: `60`
- `MISSING_SKIP_SECONDS` default: `300`

DB exporter:

- `DATABASE_URL` or `DSN`
- `DB_EXPORT_DIR` default: `./export/db`
- `DB_STATE_PATH` default: `./export/state/db_exporter.json`
- `DB_POLL_INTERVAL_SECONDS` default: `300`
- `DB_BATCH_SIZE` default: `500`

Notes:

- If `claude-code-hub` runs with `STORE_SESSION_MESSAGES=false` (default), Redis request and response content is redacted as `[REDACTED]`. To export full `user_input` and `llm_answer`, set `STORE_SESSION_MESSAGES=true` in `claude-code-hub`.

Example:

```bash
cp deploy/cch-redis-session-puller.env.example .env.local
```

## Run

Recommended: run both entrypoints.

```bash
deploy/run-puller.sh
deploy/run-db-exporter.sh
```

Single run:

```bash
python3 src/redis_puller.py --once
python3 src/db_exporter.py --once
```

Compatibility:

```bash
python3 src/puller.py --once
```

## systemd

```bash
sudo cp deploy/cch-redis-session-puller.service.example /etc/systemd/system/cch-redis-session-puller.service
sudo cp deploy/cch-db-exporter.service.example /etc/systemd/system/cch-db-exporter.service
sudo cp deploy/cch-redis-session-puller.env.example /etc/cch-redis-session-puller.env
sudo chmod +x /path/to/cch-redis-session-puller/deploy/run-puller.sh
sudo chmod +x /path/to/cch-redis-session-puller/deploy/run-db-exporter.sh
sudo systemctl daemon-reload
sudo systemctl enable --now cch-redis-session-puller.service
sudo systemctl enable --now cch-db-exporter.service
```

# cch-redis-session-puller

Phase 1 地基版导出器，分成两个入口：

- `redis_puller`：增量读取 Redis `session:*` 数据，按 session 追加导出事件与 sidecar。
- `db_exporter`：增量读取 PostgreSQL 中的 `message_request` 与 `usage_ledger`，按天导出 JSONL。

## Prerequisite

Deploy `claude-code-hub` first:  
`https://github.com/ding113/claude-code-hub`

本项目读取 `claude-code-hub` 已有存量数据，不新增原始埋点。

## Optional: low-storage source server setup

If the source server has limited disk space:

- Deploy `cch-local-pull` server on the source server:  
  `https://github.com/Eureka27/cch-local-pull`
- Deploy `cch-local-pull` client on a data server:  
  `https://github.com/Eureka27/cch-local-pull`

Important: `session_dir` in `cch-local-pull` server must point to this project's `DEST_DIR`.

## Output

Redis puller:

- `DEST_DIR/<session_id>.json`
  - 追加写入 `session_meta`、`user_input`、`tool_io`、`llm_answer`
- `REDIS_SIDECARS_DIR/<session_id>.json`
  - 追加写入 `session_info`、`session_usage`
  - 以及每个 request sequence 的 `request_body`、`request_special_settings`
  - `client_request_meta`、`upstream_request_meta`、`upstream_response_meta`
  - `request_headers`、`response_headers`、`response_body`

DB exporter:

- `DB_EXPORT_DIR/message_request/YYYY-MM-DD.jsonl`
- `DB_EXPORT_DIR/usage_ledger/YYYY-MM-DD.jsonl`
- state 文件默认落在 `EXPORT_ROOT/state/`

要得到 Phase 1 的完整地基数据，生产环境应同时部署 `redis_puller` 和 `db_exporter`：

- Redis puller 提供会话级事件、sidecar 和瞬时上下文。
- DB exporter 提供结构化请求记录和计费流水。

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## One-click Deploy

`deploy/deploy-oneclick.sh` 会同时部署两个 systemd 服务：

- `cch-redis-session-puller.service`
- `cch-db-exporter.service`

运行前请先编辑脚本顶部配置，至少确认：

- Redis 配置：`REDIS_URL` 或 `REDIS_CONTAINER`
- DB 配置：`DATABASE_URL` 或 `DSN`
- 导出目录：`EXPORT_ROOT`

执行：

```bash
bash deploy/deploy-oneclick.sh
```

## Environment

Common:

- `EXPORT_ROOT` (default: `./export`)

Redis puller:

- `REDIS_URL` (optional if `REDIS_CONTAINER` is set)
- `REDIS_CONTAINER` (default: `claude-code-hub-redis`)
- `DEST_DIR` (default: `./export/redis/session_events`)
- `REDIS_SIDECARS_DIR` (default: `./export/redis/request_sidecars`)
- `STATE_PATH` (default: `./export/state/redis_puller.json`)
- `POLL_INTERVAL_SECONDS` (default: `60`)
- `MISSING_SKIP_SECONDS` (default: `300`)

DB exporter:

- `DATABASE_URL` or `DSN`
- `DB_EXPORT_DIR` (default: `./export/db`)
- `DB_STATE_PATH` (default: `./export/state/db_exporter.json`)
- `DB_POLL_INTERVAL_SECONDS` (default: `300`)
- `DB_BATCH_SIZE` (default: `500`)

Notes:

- If `claude-code-hub` runs with `STORE_SESSION_MESSAGES=false` (default), message content in Redis request/response bodies is redacted as `[REDACTED]`. To collect full `user_input` / `llm_answer`, set `STORE_SESSION_MESSAGES=true` in `claude-code-hub`.

Example:

```bash
cp deploy/cch-redis-session-puller.env.example .env.local
```

## Run

建议同时运行两个入口：

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

# cch-redis-session-puller

Incrementally reads `session:*` keys from Redis and appends JSONL events per session.

## Prerequisite

Deploy `claude-code-hub` first:  
`https://github.com/ding113/claude-code-hub`

This puller reads `session:*` keys from Redis.

## Optional: low-storage source server setup

If the source server has limited disk space:

- Deploy `cch-local-pull` server on the source server:  
  `https://github.com/Eureka27/cch-local-pull`
- Deploy `cch-local-pull` client on a data server:  
  `https://github.com/Eureka27/cch-local-pull`

Important: `session_dir` in `cch-local-pull` server must point to this project's `DEST_DIR`.

## Output format

- One append-only file per session:
  - `DEST_DIR/<session_id>.json`
- File content is JSONL, using the same event schema as before.
- One optional session-level metadata event is included when available:
  - `type`: `session_meta`
  - `requestSequence`: `null`
  - `payload.userName`: from `session:{session_id}:info.userName`
  - optional fields: `payload.keyId`, `payload.keyName`, `payload.model`, `payload.apiType`
  - written at most once per session file

## One-click Deploy

Edit config at the top of `deploy/deploy-oneclick.sh`, then run:

```bash
bash deploy/deploy-oneclick.sh
```

## Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## Environment

- `REDIS_URL` (optional if `REDIS_CONTAINER` is set)
- `REDIS_CONTAINER` (default: `claude-code-hub-redis`)
- `DEST_DIR` (default: `./session`)
- `STATE_PATH` (default: `./state/state.json`)
- `POLL_INTERVAL_SECONDS` (default: `60`)
- `MISSING_SKIP_SECONDS` (default: `300`)

Notes:

- If `claude-code-hub` runs with `STORE_SESSION_MESSAGES=false` (default), message content in Redis request/response bodies is redacted as `[REDACTED]`. To collect full `user_input` / `llm_answer`, set `STORE_SESSION_MESSAGES=true` in `claude-code-hub`.

Example:

```bash
cp deploy/cch-redis-session-puller.env.example .env.local
```

## Run

```bash
deploy/run-puller.sh
```

Single run:

```bash
python3 src/puller.py --once
```

## systemd

```bash
sudo cp deploy/cch-redis-session-puller.service.example /etc/systemd/system/cch-redis-session-puller.service
sudo cp deploy/cch-redis-session-puller.env.example /etc/cch-redis-session-puller.env
sudo chmod +x /path/to/cch-redis-session-puller/deploy/run-puller.sh
sudo systemctl daemon-reload
sudo systemctl enable --now cch-redis-session-puller.service
```

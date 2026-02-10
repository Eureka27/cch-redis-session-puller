# cch-redis-session-puller

Incrementally reads `session:*` keys from Redis and writes JSONL shards.

## What changed

- Per-session append file is removed.
- Output is now immutable request shards:
  - `DEST_DIR/<session_id>/req-000001.json`
  - `DEST_DIR/<session_id>/req-000002.json`
- Each shard keeps the same JSONL event format as before.
- Writes are atomic (`.tmp` + rename) and idempotent.

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

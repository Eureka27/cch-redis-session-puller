# cch-redis-session-puller

Incrementally pulls session data from Redis and writes JSONL files.

## 1) Prerequisite

Deploy `claude-code-hub` first:  
`https://github.com/ding113/claude-code-hub`

This puller reads `session:*` keys from Redis.

## 2) Install and run

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Environment variables:

- `REDIS_URL` (required), e.g. `redis://localhost:6379/0`
- `POLL_INTERVAL_SECONDS` (optional, default: `60`)
- `DEST_DIR` (optional, default: `./session`)
- `STATE_PATH` (optional, default: `./state/state.json`)
- `MISSING_SKIP_SECONDS` (optional, default: `300`)

Run continuously:

```bash
python3 src/puller.py
```

Run once:

```bash
python3 src/puller.py --once
```

## 3) Optional: low-storage source server setup

If the source server has limited disk space:

- Deploy `cch-local-pull` server on the source server  
  `https://github.com/Eureka27/cch-local-pull`
- Deploy `cch-local-pull` client on a data server  
  `https://github.com/Eureka27/cch-local-pull`

Important: set `cch-local-pull` server `session_dir` to this puller's `DEST_DIR` path  
(for example: `<puller_dir>/session` if `DEST_DIR` is default).

## 4) Optional: systemd

Example unit file: `deploy/cch-redis-session-puller.service.example`

Replace `/path/to/cch-redis-session-puller` in the example with your actual deployment path.

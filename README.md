# cch-redis-session-puller

Incrementally pulls session data from Redis and writes JSONL files.

## 1) Prerequisite

Deploy `claude-code-hub` first:  
`https://github.com/ding113/claude-code-hub`

This project reads `session:*` keys from Redis.

## 2) Install

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## 3) Environment Variables (for systemd EnvironmentFile)

- `REDIS_URL` (required), e.g. `redis://127.0.0.1:6379/0`
- `POLL_INTERVAL_SECONDS` (optional, default: `60`)
- `DEST_DIR` (optional, default: `./session`)
- `STATE_PATH` (optional, default: `./state/state.json`)
- `MISSING_SKIP_SECONDS` (optional, default: `300`)

Example: `/etc/cch-redis-session-puller.env`

```bash
REDIS_URL=redis://127.0.0.1:6379/0
POLL_INTERVAL_SECONDS=60
DEST_DIR=/data/cch-redis-session-puller/session
STATE_PATH=/data/cch-redis-session-puller/state/state.json
MISSING_SKIP_SECONDS=300
```

## 4) systemd Daemon Service

1. Edit `deploy/cch-redis-session-puller.service.example` and replace `/path/to/cch-redis-session-puller` with your actual deployment directory.  
2. Prepare `/etc/cch-redis-session-puller.env` (see the example above).  
3. Install and start the systemd service:

```bash
sudo cp deploy/cch-redis-session-puller.service.example /etc/systemd/system/cch-redis-session-puller.service
sudo systemctl daemon-reload
sudo systemctl enable --now cch-redis-session-puller.service
```

4. Check status and logs:

```bash
sudo systemctl status cch-redis-session-puller.service --no-pager
sudo journalctl -u cch-redis-session-puller.service -f
```

## 5) Low-Storage Source Server Setup

If the source server has limited disk space, you can use this split deployment:

- Deploy the `cch-local-pull` server on the source server:  
  `https://github.com/Eureka27/cch-local-pull`
- Deploy the `cch-local-pull` client on a data server:  
  `https://github.com/Eureka27/cch-local-pull`

Important: `session_dir` in `cch-local-pull` server must point to this project's `DEST_DIR`.

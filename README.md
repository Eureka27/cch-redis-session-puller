# cch-redis-session-puller

从 Redis 增量拉取会话数据并落盘为 JSONL 文件。

## 1) 前置依赖

先部署 `claude-code-hub`：  
`https://github.com/ding113/claude-code-hub`

本项目会读取 Redis 中的 `session:*` 键。

## 2) 安装

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

## 3) 环境变量（用于 systemd EnvironmentFile）

- `REDIS_URL`（必填），例如 `redis://127.0.0.1:6379/0`
- `POLL_INTERVAL_SECONDS`（可选，默认 `60`）
- `DEST_DIR`（可选，默认 `./session`）
- `STATE_PATH`（可选，默认 `./state/state.json`）
- `MISSING_SKIP_SECONDS`（可选，默认 `300`）

示例：`/etc/cch-redis-session-puller.env`

```bash
REDIS_URL=redis://127.0.0.1:6379/0
POLL_INTERVAL_SECONDS=60
DEST_DIR=/data/cch-redis-session-puller/session
STATE_PATH=/data/cch-redis-session-puller/state/state.json
MISSING_SKIP_SECONDS=300
```

## 4) systemd 常驻服务（推荐）

1. 修改 `deploy/cch-redis-session-puller.service.example`，把 `/path/to/cch-redis-session-puller` 替换为你的真实部署目录。  
2. 准备环境变量文件 `/etc/cch-redis-session-puller.env`（参考上节示例）。  
3. 安装并启动 systemd 服务：

```bash
sudo cp deploy/cch-redis-session-puller.service.example /etc/systemd/system/cch-redis-session-puller.service
sudo systemctl daemon-reload
sudo systemctl enable --now cch-redis-session-puller.service
```

4. 查看状态和日志：

```bash
sudo systemctl status cch-redis-session-puller.service --no-pager
sudo journalctl -u cch-redis-session-puller.service -f
```

## 5) 手动运行（仅调试用）

常驻请使用 systemd；以下仅用于调试：

```bash
python3 src/puller.py
python3 src/puller.py --once
```

## 6) 可选：源端低存储分层方案

如果源服务器空间有限，可采用：

- 源服务器部署 `cch-local-pull` 服务端：  
  `https://github.com/Eureka27/cch-local-pull`
- 数据服务器部署 `cch-local-pull` 客户端：  
  `https://github.com/Eureka27/cch-local-pull`

关键点：`cch-local-pull` 服务端的 `session_dir` 必须指向本项目的 `DEST_DIR`。

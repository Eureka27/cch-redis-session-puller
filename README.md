# cch-redis-session-puller

从 Redis 增量拉取会话数据并落盘为 JSONL。

## 1) 前置：先部署 claude-code-hub

先在源服务器部署：`https://github.com/ding113/claude-code-hub`  
本项目读取其 Redis 中的 `session:*` 数据。

## 2) 安装与运行

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

环境变量：

- `REDIS_URL` (必填) 例如 `redis://localhost:6379/0`
- `POLL_INTERVAL_SECONDS` (可选，默认 `60`)
- `DEST_DIR` (可选，默认 `./session`)
- `STATE_PATH` (可选，默认 `./state/state.json`)
- `MISSING_SKIP_SECONDS` (可选，默认 `300`)

启动：

```bash
python3 src/puller.py
```

单次：

```bash
python3 src/puller.py --once
```

## 3) 可选：源端空间不足时的分层方案

如果本服务器存储空间有限，可采用：

- 本服务器部署 `https://github.com/Eureka27/cch-local-pull` 服务端
- 数据服务器部署 `https://github.com/Eureka27/cch-local-pull` 客户端

关键配置：`cch-local-pull` 服务端的 `session_dir` 必须指向本项目的 `DEST_DIR`。  
例如你部署在 `/home/ubuntu/x-rag/cch-redis-session-puller`，且默认配置不改，则目录为：

`/home/ubuntu/x-rag/cch-redis-session-puller/session`

## 4) systemd（可选）

示例文件：`deploy/cch-redis-session-puller.service.example`

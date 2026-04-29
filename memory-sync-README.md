# OpenClaw Memory Sync Service

> 让多台 OpenClaw 实例共享记忆，解决多实例部署时记忆不同步的问题。

## 架构

```
┌──────────────┐       HTTP        ┌──────────────┐       HTTP        ┌──────────────┐
│  OpenClaw-A  │  ◄────────────►  │  同步服务器   │  ◄────────────►  │  OpenClaw-B  │
│  (本地电脑)   │                  │ (FastAPI +   │                  │  (云服务器)   │
│              │                  │  SQLite)     │                  │              │
│ - 推送记忆    │                  │              │                  │ - 推送记忆    │
│ - 拉取更新    │                  │ - 版本存储    │                  │ - 拉取更新    │
│ - 冲突合并    │                  │ - API 认证    │                  │ - 冲突合并    │
└──────────────┘                  └──────────────┘                  └──────────────┘
```

## 为什么需要这个？

OpenClaw 的记忆存储在本地的 `MEMORY.md` 和 `memory/*.md` 文件中。
当你在多台机器上部署 OpenClaw 时（比如本地电脑 + 云服务器），每台实例的记忆是独立的，无法共享。

这个服务解决了这个问题——提供一个中央记忆服务器，让所有实例都能推送和拉取记忆。

## 快速部署

### 1. 启动服务器

```bash
cd server
pip install -r requirements.txt

# 可选：设置自定义 API Key
export MEMORY_SYNC_API_KEY="your-secret-key"

# 启动服务
python app.py
```

服务默认运行在 `http://0.0.0.0:8888`

### 2. 注册实例

```bash
cd ../skills/memory-sync
cp config.example.json config.json

# 注册第一个实例（本地）
python ../server/app.py  # 确保服务器在运行
python sync_client.py register --name "laptop" -c config.json

# 注册第二个实例（服务器）
python sync_client.py register --name "server" -c config.json
```

注册后会返回 `instance_id` 和 `api_key`，填入对应的 `config.json`。

### 3. 使用

```bash
# 推送所有记忆
python sync_client.py push

# 拉取更新
python sync_client.py pull

# 查看帮助
python sync_client.py --help
```

## API 端点

| 端点 | 方法 | 说明 |
|---|---|---|
| `/memory/push` | POST | 推送记忆文件 |
| `/memory/pull?since=X&instance_id=Y` | GET | 增量拉取更新 |
| `/memory/merge` | POST | 带冲突检测的合并 |
| `/instance/register?name=Z` | POST | 注册新实例 |
| `/health` | GET | 健康检查 |

## 冲突策略

| 操作 | 策略 |
|---|---|
| push | 直接存储，允许多版本 |
| pull | LWW（Last Write Wins），同一文件保留最新版本 |
| merge | 检测冲突，返回服务端版本供用户决定 |

## 目录结构

```
./
├── server/
│   ├── app.py                  # FastAPI 服务端
│   ├── requirements.txt        # Python 依赖
│   └── config.example.json     # 配置模板
├── skills/
│   └── memory-sync/
│       ├── SKILL.md            # OpenClaw skill 说明
│       ├── sync_client.py      # 客户端脚本
│       └── config.example.json # 客户端配置模板
└── README.md                   # 本文件
```

## 安全

- 所有请求需要 `X-API-Key` 头
- API Key 通过注册端点生成，只显示一次
- 建议在生产环境使用 HTTPS（通过 Nginx 反代）

## 扩展

- 支持向量数据库同步（SQLite 文件级同步）
- 支持 Webhook 推送（实时通知）
- 支持多实例冲突自动合并策略

## License

MIT

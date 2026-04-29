"""
OpenClaw 记忆同步服务 - 服务端
FastAPI + SQLite，提供记忆的推送、拉取、合并功能
"""

import os
import json
import time
import sqlite3
import secrets
from contextlib import contextmanager
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware

# ==================== 配置 ====================
DB_PATH = os.getenv("MEMORY_SYNC_DB", "memory_sync.db")
API_KEY = os.getenv("MEMORY_SYNC_API_KEY", secrets.token_hex(32))

app = FastAPI(title="OpenClaw Memory Sync Server")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==================== 数据库 ====================
def init_db():
    """初始化数据库表"""
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                instance_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                content TEXT NOT NULL,
                timestamp REAL NOT NULL,
                action TEXT NOT NULL DEFAULT 'push',
                created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS instances (
                instance_id TEXT PRIMARY KEY,
                name TEXT,
                last_sync REAL,
                registered_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS api_keys (
                key_hash TEXT PRIMARY KEY,
                instance_id TEXT,
                created_at REAL NOT NULL DEFAULT (strftime('%s', 'now'))
            )
        """)
        conn.commit()

@contextmanager
def get_db():
    """获取数据库连接的上下文管理器"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

# ==================== 认证 ====================
def verify_api_key(x_api_key: str) -> str:
    """验证 API Key，返回 instance_id"""
    with get_db() as conn:
        row = conn.execute(
            "SELECT instance_id FROM api_keys WHERE key_hash = ?",
            (x_api_key,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=401, detail="Invalid API Key")
        return row["instance_id"]

# ==================== 数据模型 ====================
class PushRequest(BaseModel):
    file_path: str       # 相对路径，如 "MEMORY.md" 或 "memory/2026-04-13.md"
    content: str
    timestamp: Optional[float] = None
    action: str = "push"

class MergeRequest(BaseModel):
    file_path: str
    content: str
    timestamp: float

# ==================== 路由 ====================
@app.post("/memory/push")
async def push_memory(
    req: PushRequest,
    x_api_key: str = Header(..., alias="X-API-Key")
):
    """
    推送记忆文件到服务端
    - file_path: 文件相对路径
    - content: 文件内容
    - timestamp: 时间戳（默认当前时间）
    """
    instance_id = verify_api_key(x_api_key)
    ts = req.timestamp or time.time()

    with get_db() as conn:
        conn.execute(
            """INSERT INTO memory_records 
               (instance_id, file_path, content, timestamp, action)
               VALUES (?, ?, ?, ?, ?)""",
            (instance_id, req.file_path, req.content, ts, req.action)
        )
        # 更新实例最后同步时间
        conn.execute(
            "UPDATE instances SET last_sync = ? WHERE instance_id = ?",
            (ts, instance_id)
        )
        conn.commit()

    return {
        "status": "ok",
        "instance_id": instance_id,
        "file_path": req.file_path,
        "timestamp": ts
    }

@app.get("/memory/pull")
async def pull_memory(
    since: float = Query(0, description="拉取此时间戳之后的记录"),
    instance_id: str = Query(..., description="当前实例ID（用于排除自己的推送）"),
    file_path: Optional[str] = Query(None, description="可选：只拉取特定文件"),
    x_api_key: str = Header(..., alias="X-API-Key")
):
    """
    增量拉取记忆文件
    返回指定时间戳之后、其他实例推送的记录
    """
    verify_api_key(x_api_key)  # 验证权限

    with get_db() as conn:
        if file_path:
            rows = conn.execute(
                """SELECT instance_id, file_path, content, timestamp, action
                   FROM memory_records
                   WHERE timestamp > ? AND instance_id != ? AND file_path = ?
                   ORDER BY timestamp ASC""",
                (since, instance_id, file_path)
            ).fetchall()
        else:
            rows = conn.execute(
                """SELECT instance_id, file_path, content, timestamp, action
                   FROM memory_records
                   WHERE timestamp > ? AND instance_id != ?
                   ORDER BY timestamp ASC""",
                (since, instance_id)
            ).fetchall()

    records = []
    # 对于同一文件的多次更新，只保留最新版本（LWW 策略）
    latest = {}
    for row in rows:
        key = row["file_path"]
        if key not in latest or row["timestamp"] > latest[key]["timestamp"]:
            latest[key] = {
                "instance_id": row["instance_id"],
                "file_path": row["file_path"],
                "content": row["content"],
                "timestamp": row["timestamp"],
                "action": row["action"]
            }
    records = list(latest.values())

    return {
        "status": "ok",
        "count": len(records),
        "records": records
    }

@app.post("/memory/merge")
async def merge_memory(
    req: MergeRequest,
    x_api_key: str = Header(..., alias="X-API-Key")
):
    """
    带冲突检测的合并
    如果服务端有更新的版本，返回冲突信息
    否则存储新版本
    """
    instance_id = verify_api_key(x_api_key)

    with get_db() as conn:
        # 检查服务端是否有更新的版本
        newer = conn.execute(
            """SELECT content, timestamp, instance_id
               FROM memory_records
               WHERE file_path = ? AND timestamp > ?
               ORDER BY timestamp DESC LIMIT 1""",
            (req.file_path, req.timestamp)
        ).fetchone()

        if newer:
            return {
                "status": "conflict",
                "server_content": newer["content"],
                "server_timestamp": newer["timestamp"],
                "server_instance_id": newer["instance_id"],
                "message": "服务端有更新的版本，请先解决冲突"
            }

        # 没有冲突，存储
        conn.execute(
            """INSERT INTO memory_records 
               (instance_id, file_path, content, timestamp, action)
               VALUES (?, ?, ?, ?, 'merge')""",
            (instance_id, req.file_path, req.content, req.timestamp)
        )
        conn.commit()

    return {
        "status": "merged",
        "file_path": req.file_path,
        "timestamp": req.timestamp
    }

@app.post("/instance/register")
async def register_instance(
    x_api_key: str = Header(..., alias="X-API-Key"),
    name: str = Query("unnamed", description="实例名称")
):
    """
    注册新实例，生成 API Key
    首次调用时创建一个主实例
    """
    with get_db() as conn:
        # 检查是否已有实例
        existing = conn.execute(
            "SELECT instance_id FROM instances WHERE name = ?",
            (name,)
        ).fetchone()

        if existing:
            instance_id = existing["instance_id"]
        else:
            instance_id = f"instance_{int(time.time())}"
            conn.execute(
                "INSERT INTO instances (instance_id, name) VALUES (?, ?)",
                (instance_id, name)
            )

        # 生成 API Key
        new_key = secrets.token_hex(32)
        conn.execute(
            "INSERT INTO api_keys (key_hash, instance_id) VALUES (?, ?)",
            (new_key, instance_id)
        )
        conn.commit()

    return {
        "status": "ok",
        "instance_id": instance_id,
        "api_key": new_key,
        "message": "保存好 API Key，这是唯一一次显示"
    }

@app.get("/health")
async def health_check():
    """健康检查"""
    return {"status": "ok", "db": DB_PATH}

# ==================== 启动 ====================
@app.on_event("startup")
def startup():
    init_db()
    print(f"[Memory Sync Server] 数据库: {DB_PATH}")
    print(f"[Memory Sync Server] 请调用 POST /instance/register 注册实例并获取 API Key")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8888)

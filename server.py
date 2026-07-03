"""harmony-game-agent 网页版 UI 后端。

起一个 Starlette 服务，按需为每会话创建 ClaudeSDKClient 维持多轮会话，
POST /chat 返回 SSE 流，把 Agent 的消息实时推给浏览器。
复用 main.py 的 build_options() 和 _raw_tool_result_text()。
"""

import asyncio
import contextlib
import json
import os
import threading
import time
import uuid
import webbrowser
import zipfile
from dataclasses import dataclass
from datetime import datetime
from io import BytesIO
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, StreamingResponse
from starlette.routing import Route

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from analyzers.findings import parse_findings
from main import _raw_tool_result_text, build_options
import sessions_store

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
sessions: dict[str, "ClientEntry"] = {}
lock = asyncio.Lock()

LRU_IDLE_SECS = 600
LRU_MAX_SESSIONS = 8


@dataclass
class ClientEntry:
    client: ClaudeSDKClient
    last_used: float
    title: str
    created_at: str


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


async def _sweep_lru() -> None:
    """在 lock 内调用：回收闲置超时或超量的 client（JSONL 保留）。"""
    now = time.monotonic()
    # 闲置超时
    for sid, entry in list(sessions.items()):
        if now - entry.last_used > LRU_IDLE_SECS:
            try:
                await entry.client.disconnect()
            except Exception:
                pass
            sessions.pop(sid, None)
    # 超量：按 last_used 最旧者移除
    while len(sessions) > LRU_MAX_SESSIONS:
        sid = min(sessions, key=lambda k: sessions[k].last_used)
        entry = sessions.pop(sid)
        try:
            await entry.client.disconnect()
        except Exception:
            pass


async def _get_or_create_client(prompt: str, session_id: str | None) -> tuple[str, ClaudeSDKClient, bool]:
    """返回 (sid, client, is_new)。sid=None → 新建 UUID；非空但 sessions 无 → resume 重建。"""
    await _sweep_lru()
    if session_id and session_id in sessions:
        entry = sessions[session_id]
        entry.last_used = time.monotonic()
        return session_id, entry.client, False
    options = build_options()
    is_new = not session_id
    sid = session_id or uuid.uuid4().hex
    if not is_new:
        options.resume = sid  # 续聊被回收的会话：SDK 本地磁盘 resume
    client = ClaudeSDKClient(options=options)
    await client.connect()
    title = (prompt[:40] + "…") if len(prompt) > 40 else prompt
    sessions[sid] = ClientEntry(client=client, last_used=time.monotonic(), title=title, created_at=_now_iso())
    return sid, client, is_new


def _sse(event: str, data: dict) -> str:
    """格式化一条 SSE 事件。"""
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


async def index(_: Request) -> HTMLResponse:
    html = (BASE_DIR / "index.html").read_text(encoding="utf-8")
    return HTMLResponse(html)


def _relative_to_generated(file_path: str) -> str | None:
    """把 Write 的 file_path 归一化为相对 generated/ 的路径。越界返回 None。"""
    base = (BASE_DIR / "generated").resolve()
    try:
        abs_path = (base / file_path).resolve() if not os.path.isabs(file_path) \
            else Path(file_path).resolve()
    except (ValueError, OSError):
        return None
    try:
        if os.path.commonpath([abs_path, base]) != str(base):
            return None
    except ValueError:
        return None
    return abs_path.relative_to(base).as_posix()


async def chat(request: Request):
    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return JSONResponse({"error": "prompt 为空"}, status_code=400)
    session_id = body.get("session_id")

    async def stream():
        async with lock:
            try:
                sid, sess_client, is_new = await _get_or_create_client(prompt, session_id)
            except Exception as e:
                # resume 失败降级新建 + error 提示
                options = build_options()
                sid = uuid.uuid4().hex
                err_msg = f"历史会话不可续，已开新会话：{e}"
                yield _sse("error", {"message": err_msg})
                sessions_store.append_event(BASE_DIR, sid, "error", {"message": err_msg})
                sess_client = ClaudeSDKClient(options=options)
                await sess_client.connect()
                is_new = True
                sessions[sid] = ClientEntry(client=sess_client, last_used=time.monotonic(), title=(prompt[:40] + "…" if len(prompt) > 40 else prompt), created_at=_now_iso())
            pending_writes: dict[str, dict] = {}
            title = (prompt[:40] + "…") if len(prompt) > 40 else prompt
            if is_new:
                started = {"session_id": sid, "title": title, "created_at": sessions[sid].created_at}
                yield _sse("session_started", started)
                sessions_store.append_event(BASE_DIR, sid, "session_started", started)
            try:
                await sess_client.query(prompt, session_id=sid)
                async for msg in sess_client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                ev = ("text", {"text": block.text})
                                yield _sse(ev[0], ev[1]); sessions_store.append_event(BASE_DIR, sid, *ev)
                            elif isinstance(block, ToolUseBlock):
                                if block.name == "Write":
                                    rel = _relative_to_generated(block.input.get("file_path", ""))
                                    if rel is not None:
                                        pending_writes[block.id] = {"path": rel, "content": block.input.get("content", "")}
                                ev = ("tool_use", {"name": block.name, "input": block.input})
                                yield _sse(ev[0], ev[1]); sessions_store.append_event(BASE_DIR, sid, *ev)
                    elif isinstance(msg, UserMessage):
                        for block in msg.content:
                            if isinstance(block, ToolResultBlock):
                                if block.tool_use_id in pending_writes:
                                    item = pending_writes.pop(block.tool_use_id)
                                    ev = ("file", {"path": item["path"], "content": item["content"], "is_error": block.is_error})
                                    yield _sse(ev[0], ev[1]); sessions_store.append_event(BASE_DIR, sid, *ev)
                                else:
                                    raw = _raw_tool_result_text(block)
                                    findings = parse_findings(raw)
                                    if findings is not None:
                                        ev = ("findings", {"findings": findings, "is_error": block.is_error})
                                        yield _sse(ev[0], ev[1]); sessions_store.append_event(BASE_DIR, sid, *ev)
                                    else:
                                        ev = ("tool_result", {"text": raw, "is_error": block.is_error})
                                        yield _sse(ev[0], ev[1]); sessions_store.append_event(BASE_DIR, sid, *ev)
                    elif isinstance(msg, ResultMessage):
                        ev = ("done", {"is_error": msg.is_error, "cost": msg.total_cost_usd})
                        yield _sse(ev[0], ev[1]); sessions_store.append_event(BASE_DIR, sid, *ev)
            except Exception as e:
                ev = ("error", {"message": str(e)})
                yield _sse(ev[0], ev[1]); sessions_store.append_event(BASE_DIR, sid, *ev)

    return StreamingResponse(stream(), media_type="text/event-stream")


@contextlib.asynccontextmanager
async def lifespan(_: Starlette):
    print("[server] Agent 工作台启动，会话按需创建", flush=True)
    yield
    # 关闭所有常驻 client
    for entry in list(sessions.values()):
        try:
            await entry.client.disconnect()
        except Exception:
            pass
    sessions.clear()


def _build_zip(root: Path) -> bytes:
    """把 root 目录打包为 zip 字节。成员名取相对 root 的路径，防 zip slip。"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in root.rglob("*"):
            if not member.is_file():
                continue
            arcname = member.relative_to(root).as_posix()
            # zip slip 防护：arcname 不绝对、不含 .. 段
            if arcname.startswith("/") or ".." in arcname.split("/"):
                continue
            zf.write(member, arcname)
    return buf.getvalue()


_CONTENT_TYPES = {
    ".ets": "text/plain; charset=utf-8",
    ".json": "application/json",
    ".ts": "text/plain; charset=utf-8",
    ".js": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}


async def export(request: Request):
    from starlette.responses import Response

    raw_path = request.query_params.get("path", "").strip()
    if not raw_path:
        return JSONResponse({"error": "path 参数为空"}, status_code=400)

    base = (BASE_DIR / "generated").resolve()
    abs_path = (base / raw_path).resolve()

    try:
        if os.path.commonpath([abs_path, base]) != str(base):
            return JSONResponse({"error": "路径越界"}, status_code=400)
    except ValueError:
        return JSONResponse({"error": "路径越界"}, status_code=400)

    if not abs_path.exists():
        return JSONResponse({"error": "路径不存在"}, status_code=404)

    if abs_path.is_file():
        ext = abs_path.suffix.lower()
        ctype = _CONTENT_TYPES.get(ext, "application/octet-stream")
        data = abs_path.read_bytes()
        return Response(
            data,
            media_type=ctype,
            headers={"Content-Disposition": f'attachment; filename="{abs_path.name}"'},
        )

    # 目录 → zip
    try:
        zip_bytes = _build_zip(abs_path)
    except Exception as e:
        return JSONResponse({"error": f"打包失败：{e}"}, status_code=500)
    zip_name = abs_path.name or "generated"
    return Response(
        zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}.zip"'},
    )


routes = [
    Route("/", index),
    Route("/chat", chat, methods=["POST"]),
    Route("/export", export, methods=["GET"]),
]
app = Starlette(routes=routes, lifespan=lifespan)


def _open_browser(url: str, delay: float = 1.2) -> None:
    threading.Timer(delay, lambda: webbrowser.open(url)).start()


if __name__ == "__main__":
    host, port = "127.0.0.1", 8000
    _open_browser(f"http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)

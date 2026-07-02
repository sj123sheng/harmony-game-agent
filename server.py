"""harmony-game-agent 网页版 UI 后端。

起一个 Starlette 服务，常驻 ClaudeSDKClient 维持多轮会话，
POST /chat 返回 SSE 流，把 Agent 的消息实时推给浏览器。
复用 main.py 的 build_options() 和 _raw_tool_result_text()。
"""

import asyncio
import contextlib
import json
import os
import threading
import webbrowser
import zipfile
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

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
client: ClaudeSDKClient | None = None
lock = asyncio.Lock()


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
    if client is None:
        return JSONResponse({"error": "Agent 未连接"}, status_code=503)

    async def stream():
        async with lock:
            pending_writes: dict[str, dict] = {}
            try:
                await client.query(prompt)
                async for msg in client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                yield _sse("text", {"text": block.text})
                            elif isinstance(block, ToolUseBlock):
                                if block.name == "Write":
                                    rel = _relative_to_generated(
                                        block.input.get("file_path", "")
                                    )
                                    if rel is not None:
                                        pending_writes[block.id] = {
                                            "path": rel,
                                            "content": block.input.get("content", ""),
                                        }
                                yield _sse("tool_use", {
                                    "name": block.name, "input": block.input
                                })
                    elif isinstance(msg, UserMessage):
                        for block in msg.content:
                            if isinstance(block, ToolResultBlock):
                                if block.tool_use_id in pending_writes:
                                    item = pending_writes.pop(block.tool_use_id)
                                    yield _sse("file", {
                                        "path": item["path"],
                                        "content": item["content"],
                                        "is_error": block.is_error,
                                    })
                                else:
                                    raw = _raw_tool_result_text(block)
                                    findings = parse_findings(raw)
                                    if findings is not None:
                                        yield _sse("findings", {
                                            "findings": findings,
                                            "is_error": block.is_error,
                                        })
                                    else:
                                        yield _sse("tool_result", {
                                            "text": raw,
                                            "is_error": block.is_error,
                                        })
                    elif isinstance(msg, ResultMessage):
                        yield _sse("done", {
                            "is_error": msg.is_error,
                            "cost": msg.total_cost_usd,
                        })
            except Exception as e:
                yield _sse("error", {"message": str(e)})

    return StreamingResponse(stream(), media_type="text/event-stream")


@contextlib.asynccontextmanager
async def lifespan(_: Starlette):
    global client
    options = build_options()
    client = ClaudeSDKClient(options=options)
    await client.connect()
    print(f"[server] Agent 已连接，模型={options.model}，工具已挂载", flush=True)
    yield
    if client is not None:
        await client.disconnect()
        print("[server] Agent 已断开", flush=True)


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

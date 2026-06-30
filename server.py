"""harmony-game-agent 网页版 UI 后端。

起一个 Starlette 服务，常驻 ClaudeSDKClient 维持多轮会话，
POST /chat 返回 SSE 流，把 Agent 的消息实时推给浏览器。
复用 main.py 的 build_options() 和 _extract_tool_result_text()。
"""

import asyncio
import contextlib
import json
import threading
import webbrowser
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

from main import _extract_tool_result_text, build_options

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


async def chat(request: Request):
    body = await request.json()
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return JSONResponse({"error": "prompt 为空"}, status_code=400)
    if client is None:
        return JSONResponse({"error": "Agent 未连接"}, status_code=503)

    async def stream():
        async with lock:
            try:
                await client.query(prompt)
                async for msg in client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                yield _sse("text", {"text": block.text})
                            elif isinstance(block, ToolUseBlock):
                                yield _sse("tool_use", {"name": block.name, "input": block.input})
                    elif isinstance(msg, UserMessage):
                        for block in msg.content:
                            if isinstance(block, ToolResultBlock):
                                yield _sse("tool_result", {
                                    "text": _extract_tool_result_text(block),
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


routes = [Route("/", index), Route("/chat", chat, methods=["POST"])]
app = Starlette(routes=routes, lifespan=lifespan)


def _open_browser(url: str, delay: float = 1.2) -> None:
    threading.Timer(delay, lambda: webbrowser.open(url)).start()


if __name__ == "__main__":
    host, port = "127.0.0.1", 8000
    _open_browser(f"http://{host}:{port}")
    uvicorn.run(app, host=host, port=port)

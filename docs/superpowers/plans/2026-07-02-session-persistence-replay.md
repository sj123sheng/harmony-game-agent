# 多会话持久化与历史回放 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让网页工作台支持多会话（新建/切换/删除/续聊）与历史回放（回看任意会话的完整卡片流）。

**Architecture:** server 维护 `sessions: dict[session_id, ClientEntry]`（每会话一个常驻 ClaudeSDKClient + LRU 回收）；`chat()` 的 `stream()` 每发一 SSE 事件同时 append 到 `./sessions/<id>.jsonl`（与 SSE 同构，回放近乎免费）；续聊常驻 client 直接 query，被回收后 `ClaudeSDKClient(options/resume=id)` 重建；前端 rail 顶部折叠会话列表 + 切换 fetch events 逐条重放。

**Tech Stack:** Python 3.12 / Starlette + SSE / Claude Agent SDK（ClaudeSDKClient, ClaudeAgentOptions.resume）/ 原生 JS + fetch / JSONL 文件存储。

## Global Constraints

- 零回归：不改 `tools.py` / `generators/` / `analyzers/` / `main.py`（`build_options()` 签名不动，server 在返回的 options 对象上 set `.resume`）
- `session_id = uuid.uuid4().hex`（32 位 hex），通过 `client.query(prompt, session_id=sid)` 传 SDK；resume 通过 `options.resume = sid`
- JSONL 路径 `BASE_DIR/sessions/<id>.jsonl`；`<id>` 限 `^[a-f0-9]{32}$` + `commonpath` 双防护
- LRU：闲置 > 600s（monotonic）或 dict size > 8 → disconnect + 移除 client（JSONL 保留）
- `async with lock` 仍串行化整个 query；LRU sweep 在 lock 内
- 测试自带 `main()`、非 pytest、`uv run python <test>.py` 自跑；monkeypatch 桩，不连真 Agent
- 中文注释与响应

---

### Task 1: sessions_store.py 纯 IO 模块

**Files:**
- Create: `sessions_store.py`
- Test: `sessions_store_test.py`

**Interfaces:**
- Consumes: 无
- Produces: `append_event(base: Path, sid: str, event: str, data: dict) -> None`；`load_events(base: Path, sid: str) -> list[tuple[str, dict]]`（损坏行跳过）；`list_sessions(base: Path) -> list[dict]`（倒序，每项 `{id, title, created_at, mtime, event_count}`）；`delete_session(base: Path, sid: str) -> bool`；非法 id 抛 `ValueError`/返回空

- [ ] **Step 1: Write the failing test**

Create `sessions_store_test.py`:

```python
"""sessions_store 纯 IO 单测。"""

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sessions_store  # noqa: E402


def test_append_and_load_roundtrip():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        sessions_store.append_event(base, "a" * 32, "session_started", {"session_id": "a" * 32, "title": "t", "created_at": "2026-07-02T10:00:00"})
        sessions_store.append_event(base, "a" * 32, "text", {"text": "hi"})
        sessions_store.append_event(base, "a" * 32, "done", {"is_error": False})
        evs = sessions_store.load_events(base, "a" * 32)
        assert len(evs) == 3, evs
        assert evs[0] == ("session_started", {"session_id": "a" * 32, "title": "t", "created_at": "2026-07-02T10:00:00"})
        assert evs[1] == ("text", {"text": "hi"})
        assert evs[2][0] == "done"
    print("[OK] test_append_and_load_roundtrip")


def test_load_skips_corrupt_lines():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        p = base / "sessions" / ("b" * 32 + ".jsonl")
        p.parent.mkdir(parents=True)
        p.write_text(
            json.dumps({"event": "text", "data": {"text": "ok"}}, ensure_ascii=False) + "\n"
            + "THIS IS NOT JSON\n"
            + json.dumps({"event": "done", "data": {"is_error": False}}, ensure_ascii=False) + "\n"
            + json.dumps({"no_event_field": 1}, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        evs = sessions_store.load_events(base, "b" * 32)
        assert len(evs) == 2, evs
        assert evs[0] == ("text", {"text": "ok"})
        assert evs[1][0] == "done"
    print("[OK] test_load_skips_corrupt_lines")


def test_list_sessions_returns_sorted_with_meta():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        sessions_store.append_event(base, "c" * 32, "session_started", {"session_id": "c" * 32, "title": "first", "created_at": "2026-07-02T10:00:00"})
        sessions_store.append_event(base, "d" * 32, "session_started", {"session_id": "d" * 32, "title": "second", "created_at": "2026-07-02T11:00:00"})
        sessions_store.append_event(base, "d" * 32, "text", {"text": "x"})
        items = sessions_store.list_sessions(base)
        assert len(items) == 2, items
        # mtime 倒序：d 后写，应排前
        assert items[0]["id"] == "d" * 32, items
        assert items[0]["title"] == "second"
        assert items[0]["event_count"] == 2
        assert items[0]["created_at"] == "2026-07-02T11:00:00"
        assert items[1]["event_count"] == 1
    print("[OK] test_list_sessions_returns_sorted_with_meta")


def test_delete_session_removes_file():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        sessions_store.append_event(base, "e" * 32, "session_started", {"session_id": "e" * 32, "title": "t", "created_at": "x"})
        assert sessions_store.delete_session(base, "e" * 32) is True
        assert sessions_store.load_events(base, "e" * 32) == []
        assert sessions_store.delete_session(base, "e" * 32) is False  # 幂等：已删返 False
    print("[OK] test_delete_session_removes_file")


def test_illegal_id_rejected():
    with tempfile.TemporaryDirectory() as d:
        base = Path(d)
        # 非 hex 32 位 → ValueError
        try:
            sessions_store.append_event(base, "../etc", "text", {"text": "x"})
            assert False, "应抛 ValueError"
        except ValueError:
            pass
        # load 对非法 id 返回空（不抛）
        assert sessions_store.load_events(base, "../etc") == []
        assert sessions_store.load_events(base, "ZZ" * 16) == []
    print("[OK] test_illegal_id_rejected")


def main():
    test_append_and_load_roundtrip()
    test_load_skips_corrupt_lines()
    test_list_sessions_returns_sorted_with_meta()
    test_delete_session_removes_file()
    test_illegal_id_rejected()
    print("\n全部通过。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python sessions_store_test.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'sessions_store'`

- [ ] **Step 3: Write minimal implementation**

Create `sessions_store.py`:

```python
"""会话事件流持久化：纯文件 IO，无 SDK 无业务。

每会话一个 JSONL 文件 ./sessions/<session_id>.jsonl，每行一条事件
{"event": "<type>", "data": {...}}，与 server.py 的 SSE 事件同构。
"""

import json
import os
import re
from pathlib import Path

_ID_RE = re.compile(r"^[a-f0-9]{32}$")


def _sessions_dir(base: Path) -> Path:
    return base / "sessions"


def _session_path(base: Path, sid: str) -> Path:
    """返回 sessions/<sid>.jsonl 的绝对路径。非法 id 或越界抛 ValueError。"""
    if not _ID_RE.match(sid):
        raise ValueError("非法 session_id")
    d = _sessions_dir(base).resolve()
    p = (d / f"{sid}.jsonl").resolve()
    try:
        if os.path.commonpath([str(p), str(d)]) != str(d):
            raise ValueError("路径越界")
    except ValueError:
        raise ValueError("路径越界")
    return p


def append_event(base: Path, sid: str, event: str, data: dict) -> None:
    """追加一条事件到会话 JSONL。新建文件若需。"""
    p = _session_path(base, sid)
    p.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"event": event, "data": data}, ensure_ascii=False)
    with open(p, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def load_events(base: Path, sid: str) -> list[tuple[str, dict]]:
    """加载会话全部事件，跳过损坏行。非法 id 或不存在返回 []。"""
    try:
        p = _session_path(base, sid)
    except ValueError:
        return []
    if not p.exists():
        return []
    out: list[tuple[str, dict]] = []
    with open(p, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                out.append((obj["event"], obj["data"]))
            except (json.JSONDecodeError, KeyError, TypeError):
                continue
    return out


def list_sessions(base: Path) -> list[dict]:
    """列出全部会话，按 mtime 倒序。每项 {id, title, created_at, mtime, event_count}。"""
    d = _sessions_dir(base)
    if not d.exists():
        return []
    out: list[dict] = []
    for p in d.glob("*.jsonl"):
        sid = p.stem
        if not _ID_RE.match(sid):
            continue
        title = ""
        created_at = ""
        count = 0
        with open(p, "r", encoding="utf-8") as f:
            lines = f.readlines()
        count = len([ln for ln in lines if ln.strip()])
        if lines:
            try:
                first = json.loads(lines[0].strip())
                if first.get("event") == "session_started":
                    title = first.get("data", {}).get("title", "")
                    created_at = first.get("data", {}).get("created_at", "")
            except (json.JSONDecodeError, KeyError, TypeError):
                pass
        out.append({
            "id": sid, "title": title, "created_at": created_at,
            "mtime": p.stat().st_mtime, "event_count": count,
        })
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def delete_session(base: Path, sid: str) -> bool:
    """删除会话 JSONL。成功返 True，不存在或非法返 False。"""
    try:
        p = _session_path(base, sid)
    except ValueError:
        return False
    if not p.exists():
        return False
    p.unlink()
    return True
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python sessions_store_test.py`
Expected: 5 个 `[OK]` + `全部通过。`

- [ ] **Step 5: Commit**

```bash
git add sessions_store.py sessions_store_test.py
git commit -m "feat(sessions): 新增 sessions_store 纯 IO 模块

提示词：Phase 5 多会话持久化 Task 1"
```

---

### Task 2: server.py 会话核心（sessions dict + LRU + chat session_id + stream append）

**Files:**
- Modify: `server.py`（删全局 `client`，加 `sessions` dict + `ClientEntry` + `_get_or_create_client` + LRU sweep + `chat()` session_id 分支 + `stream()` append/session_started；`lifespan` 不再 connect 单 client）
- Modify: `server_test.py`（适配 Phase 4 三个 stream e2e 测试 mock 模式：`server.client = _FakeClient` → patch `server.ClaudeSDKClient` factory；_FakeClient 加 `connect`/`disconnect`/`query(session_id)`；新增 3 个会话测试）

**Interfaces:**
- Consumes: Task 1 的 `sessions_store.append_event`；`main.build_options()`（返回 `ClaudeAgentOptions` dataclass，server 在其上 set `.resume`）；SDK `ClaudeSDKClient`（构造 `ClaudeSDKClient(options=options)` + `await client.connect()` + `await client.query(prompt, session_id=sid)` + `async for msg in client.receive_response()` + `await client.disconnect()`）
- Produces: `sessions: dict[str, ClientEntry]`（模块级）；`ClientEntry`（dataclass：`client`/`last_used`/`title`/`created_at`）；`POST /chat` 接受 body `session_id: str | null`，SSE 新增首条 `session_started` 事件（仅新建会话）

- [ ] **Step 1: Write the failing tests（新增 + 适配）**

在 `server_test.py` 顶部 `_FakeClient` 改造为支持 `connect`/`disconnect`/`session_id`，并加 `_patch_sdk_client` helper。替换现有 `_FakeClient` 类（143-154 行）为：

```python
class _FakeClient:
    """桩 ClaudeSDKClient：connect/disconnect 空操作，query 记 session_id，receive_response 产出预设序列。"""

    def __init__(self, msgs):
        self._msgs = msgs
        self.connected = False
        self.session_id_received = None
        self.connect_calls = 0

    async def connect(self, prompt=None):
        self.connect_calls += 1
        self.connected = True

    async def disconnect(self):
        self.connected = False

    async def query(self, prompt, session_id="default"):
        self.session_id_received = session_id

    async def receive_response(self):
        for m in self._msgs:
            yield m
```

在 `_parse_sse` 后加 helper：

```python
def _patch_sdk_client(fake_factory):
    """把 server.ClaudeSDKClient 替换为返回 _FakeClient 的工厂。返回 (orig, fake_factory) 供还原。"""
    import server as srv
    orig = srv.ClaudeSDKClient
    srv.ClaudeSDKClient = lambda options=None: fake_factory()
    return orig


def _parse_sse_events(text: str) -> list[tuple[str, dict]]:
    """同 _parse_sse 但容忍多 data 行；用于会话测试。"""
    return _parse_sse(text)
```

适配 Phase 4 三个 e2e 测试。`test_stream_file_event_for_write`（171-207 行）把 `server.client = _FakeClient([...])` 改为：

```python
    fake = _FakeClient([
        AssistantMessage(content=[ToolUseBlock(
            id="w1", name="Write",
            input={"file_path": fp, "content": "@Component struct Foo {}"},
        )], model="test"),
        UserMessage(content=[ToolResultBlock(
            tool_use_id="w1",
            content=[{"type": "text", "text": "wrote"}],
            is_error=False,
        )]),
    ])
    orig = _patch_sdk_client(lambda: fake)
    try:
        tc = TestClient(server.app)
        resp = tc.post("/chat", json={"prompt": "x"})
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        kinds = [e for e, _ in events]
        assert "file" in kinds, f"缺少 file 事件，实际: {kinds}"
        idx = kinds.index("file")
        data = events[idx][1]
        assert data["path"] == "character/Foo.ets"
        assert data["content"] == "@Component struct Foo {}"
        assert data["is_error"] is False
    finally:
        server.BASE_DIR = orig_base  # 注意：_patch_base_dir 的 orig 改名 orig_base
        server.ClaudeSDKClient = orig
        server.sessions.clear()
```

> 注意：`_patch_base_dir` 的还原变量名在原测试里是 `orig`，与会话 patch 的 `orig` 冲突。把 `_patch_base_dir` 调用处局部变量改名为 `orig_base`（如 `orig_base = _patch_base_dir(tmp)` / `server.BASE_DIR = orig_base`）。对 `test_stream_findings_event_for_json_result` 与 `test_stream_tool_result_fallback_for_plain_text` 做同样改造：`server.client = _FakeClient([...])` → `fake = _FakeClient([...]); _patch_sdk_client(lambda: fake)`，finally 加 `server.ClaudeSDKClient = orig; server.sessions.clear()`。这两个测试无 BASE_DIR patch，只加会话清理。

新增 3 个会话测试（追加到 `test_stream_tool_result_fallback_for_plain_text` 之后）：

```python
def test_new_session_emits_session_started_and_writes_jsonl():
    """POST /chat 无 session_id → 新建：首条 SSE session_started 含新 UUID，JSONL 写入。"""
    import tempfile, json as _json
    from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        orig_base = _patch_base_dir(tmp)
        fake = _FakeClient([AssistantMessage(content=[TextBlock(text="hi")], model="test"), ResultMessage(is_error=False, total_cost_usd=0.0)])
        orig = _patch_sdk_client(lambda: fake)
        try:
            tc = TestClient(server.app)
            resp = tc.post("/chat", json={"prompt": "生成角色属性系统"})
            assert resp.status_code == 200
            events = _parse_sse(resp.text)
            assert events[0][0] == "session_started", events
            sid = events[0][1]["session_id"]
            assert len(sid) == 32
            assert events[0][1]["title"] == "生成角色属性系统"
            assert fake.session_id_received == sid
            # JSONL 存在且首行是 session_started
            evs = sessions_store.load_events(tmp, sid)
            assert len(evs) >= 2
            assert evs[0][0] == "session_started"
        finally:
            server.BASE_DIR = orig_base
            server.ClaudeSDKClient = orig
            server.sessions.clear()
    print("[OK] test_new_session_emits_session_started_and_writes_jsonl")


```python
def test_resume_existing_session_reuses_client():
    """同 session_id 二次 POST → 复用 sessions[sid].client（connect_calls 不增）。"""
    import tempfile
    from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        orig_base = _patch_base_dir(tmp)
        fake = _FakeClient([AssistantMessage(content=[TextBlock(text="hi")], model="test"), ResultMessage(is_error=False, total_cost_usd=0.0)])
        orig = _patch_sdk_client(lambda: fake)
        try:
            tc = TestClient(server.app)
            r1 = tc.post("/chat", json={"prompt": "first"})
            sid = _parse_sse(r1.text)[0][1]["session_id"]
            calls_after_first = fake.connect_calls
            # 第二次带同一 session_id → 复用 client，不新建
            tc.post("/chat", json={"prompt": "second", "session_id": sid})
            assert fake.connect_calls == calls_after_first, "续聊不应新建 client"
            assert sid in server.sessions
        finally:
            server.BASE_DIR = orig_base
            server.ClaudeSDKClient = orig
            server.sessions.clear()
    print("[OK] test_resume_existing_session_reuses_client")


def test_resumed_session_after_eviction_rebuilds_via_resume():
    """session_id 不在 sessions（被回收）→ ClaudeSDKClient 重建，options.resume 被设。"""
    import tempfile
    from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage
    captured_options = {}
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        orig_base = _patch_base_dir(tmp)
        fake = _FakeClient([AssistantMessage(content=[TextBlock(text="hi")], model="test"), ResultMessage(is_error=False, total_cost_usd=0.0)])
        def _factory(options=None):
            captured_options["resume"] = getattr(options, "resume", None) if options else None
            return fake
        orig = _patch_sdk_client(_factory)
        try:
            tc = TestClient(server.app)
            r1 = tc.post("/chat", json={"prompt": "first"})
            sid = _parse_sse(r1.text)[0][1]["session_id"]
            # 模拟 LRU 回收：从 sessions 移除 client
            server.sessions.pop(sid, None)
            captured_options["resume"] = None
            # 再用同 sid → 应走 resume 重建
            tc.post("/chat", json={"prompt": "second", "session_id": sid})
            assert captured_options["resume"] == sid, f"应设 options.resume=sid，实际 {captured_options['resume']}"
        finally:
            server.BASE_DIR = orig_base
            server.ClaudeSDKClient = orig
            server.sessions.clear()
    print("[OK] test_resumed_session_after_eviction_rebuilds_via_resume")
```

在 `main()` 里追加调用：

```python
def main():
    test_export_single_file_ets()
    test_export_single_file_json()
    test_export_directory_returns_zip()
    test_export_path_traversal_400()
    test_export_nonexistent_404()
    test_export_missing_param_400()
    test_export_zip_slip_member_skipped()
    test_stream_file_event_for_write()
    test_stream_findings_event_for_json_result()
    test_stream_tool_result_fallback_for_plain_text()
    test_new_session_emits_session_started_and_writes_jsonl()
    test_resume_existing_session_reuses_client()
    test_resumed_session_after_eviction_rebuilds_via_resume()
    print("\n全部通过。")
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python server_test.py`
Expected: FAIL（`server.client` 已不存在 / `sessions` 未定义 / 新测试 AssertionError）

- [ ] **Step 3: Modify server.py — sessions dict + ClientEntry + LRU + chat session_id + stream append**

在 `server.py` 顶部 import 区加：

```python
import time
import uuid
from dataclasses import dataclass
from datetime import datetime

import sessions_store
```

删 `client: ClaudeSDKClient | None = None`（41 行）与 `lifespan` 里的 `global client` + connect/disconnect（138-146 行），替换为：

```python
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


def _sweep_lru() -> None:
    """在 lock 内调用：回收闲置超时或超量的 client（JSONL 保留）。"""
    now = time.monotonic()
    # 闲置超时
    for sid, entry in list(sessions.items()):
        if now - entry.last_used > LRU_IDLE_SECS:
            try:
                asyncio.get_event_loop().create_task(entry.client.disconnect())
            except Exception:
                pass
            sessions.pop(sid, None)
    # 超量：按 last_used 最旧者移除
    while len(sessions) > LRU_MAX_SESSIONS:
        sid = min(sessions, key=lambda k: sessions[k].last_used)
        entry = sessions.pop(sid)
        try:
            asyncio.get_event_loop().create_task(entry.client.disconnect())
        except Exception:
            pass


async def _get_or_create_client(prompt: str, session_id: str | None) -> tuple[str, ClaudeSDKClient, bool]:
    """返回 (sid, client, is_new)。sid=None → 新建 UUID；非空但 sessions 无 → resume 重建。"""
    _sweep_lru()
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
```

`lifespan` 简化为（不再 connect 单 client）：

```python
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
```

`chat()` 改造（71-77 行）：

```python
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
                yield _sse("error", {"message": f"历史会话不可续，已开新会话：{e}"})
                options = build_options()
                sid = uuid.uuid4().hex
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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python server_test.py`
Expected: 13 个 `[OK]` + `全部通过。`（含适配后的 3 个 Phase 4 e2e + 3 个新会话测试）

- [ ] **Step 5: Commit**

```bash
git add server.py server_test.py
git commit -m "feat(server): 会话核心 sessions dict + LRU + chat session_id + stream append

提示词：Phase 5 多会话持久化 Task 2"
```

---

### Task 3: server.py /sessions 路由（list / events / delete）

**Files:**
- Modify: `server.py`（加 `sessions_list` / `session_events` / `delete_session_handler` 三个 handler + 路由）
- Modify: `server_test.py`（加 6 个路由测试）

**Interfaces:**
- Consumes: Task 1 的 `sessions_store.list_sessions` / `load_events` / `delete_session`；Task 2 的 `server.sessions` dict
- Produces: `GET /sessions` → `[{id,title,created_at,mtime,event_count}, ...]`；`GET /sessions/<id>/events` → `{events:[{event,data},...]}` 或 404；`DELETE /sessions/<id>` → 200（幂等）；非法 id 400

- [ ] **Step 1: Write the failing tests**

追加到 `server_test.py`（`main()` 之前）：

```python
def test_sessions_list_empty():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        orig_base = _patch_base_dir(tmp)
        try:
            tc = TestClient(server.app)
            resp = tc.get("/sessions")
            assert resp.status_code == 200
            assert resp.json() == []
        finally:
            server.BASE_DIR = orig_base
    print("[OK] test_sessions_list_empty")


def test_sessions_list_after_chat():
    import tempfile
    from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        orig_base = _patch_base_dir(tmp)
        fake = _FakeClient([AssistantMessage(content=[TextBlock(text="hi")], model="test"), ResultMessage(is_error=False, total_cost_usd=0.0)])
        orig = _patch_sdk_client(lambda: fake)
        try:
            tc = TestClient(server.app)
            r = tc.post("/chat", json={"prompt": "first prompt"})
            sid = _parse_sse(r.text)[0][1]["session_id"]
            resp = tc.get("/sessions")
            assert resp.status_code == 200
            items = resp.json()
            assert len(items) == 1
            assert items[0]["id"] == sid
            assert items[0]["title"] == "first prompt"
            assert items[0]["event_count"] >= 2
        finally:
            server.BASE_DIR = orig_base
            server.ClaudeSDKClient = orig
            server.sessions.clear()
    print("[OK] test_sessions_list_after_chat")


def test_session_events_returns_json():
    import tempfile
    from claude_agent_sdk import AssistantMessage, TextBlock, ResultMessage
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        orig_base = _patch_base_dir(tmp)
        fake = _FakeClient([AssistantMessage(content=[TextBlock(text="hello")], model="test"), ResultMessage(is_error=False, total_cost_usd=0.0)])
        orig = _patch_sdk_client(lambda: fake)
        try:
            tc = TestClient(server.app)
            r = tc.post("/chat", json={"prompt": "p"})
            sid = _parse_sse(r.text)[0][1]["session_id"]
            resp = tc.get(f"/sessions/{sid}/events")
            assert resp.status_code == 200
            evs = resp.json()["events"]
            assert evs[0]["event"] == "session_started"
            assert any(e["event"] == "text" and e["data"]["text"] == "hello" for e in evs)
        finally:
            server.BASE_DIR = orig_base
            server.ClaudeSDKClient = orig
            server.sessions.clear()
    print("[OK] test_session_events_returns_json")


def test_session_events_not_found_404():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        orig_base = _patch_base_dir(tmp)
        try:
            tc = TestClient(server.app)
            resp = tc.get(f"/sessions/{'f' * 32}/events")
            assert resp.status_code == 404
        finally:
            server.BASE_DIR = orig_base
    print("[OK] test_session_events_not_found_404")


def test_session_events_illegal_id_400():
    tc = TestClient(server.app)
    resp = tc.get("/sessions/../etc/events")
    assert resp.status_code == 400
    print("[OK] test_session_events_illegal_id_400")


def test_delete_session_idempotent():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        orig_base = _patch_base_dir(tmp)
        try:
            tc = TestClient(server.app)
            sid = "a" * 32
            # 不存在 → 仍 200（幂等）
            resp = tc.delete(f"/sessions/{sid}")
            assert resp.status_code == 200
            # 手动建一个再删
            sessions_store.append_event(tmp, sid, "session_started", {"session_id": sid, "title": "t", "created_at": "x"})
            resp2 = tc.delete(f"/sessions/{sid}")
            assert resp2.status_code == 200
            assert sessions_store.load_events(tmp, sid) == []
        finally:
            server.BASE_DIR = orig_base
    print("[OK] test_delete_session_idempotent")
```

在 `main()` 追加：

```python
    test_sessions_list_empty()
    test_sessions_list_after_chat()
    test_session_events_returns_json()
    test_session_events_not_found_404()
    test_session_events_illegal_id_400()
    test_delete_session_idempotent()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run python server_test.py`
Expected: FAIL（新 6 个测试，路由不存在 → 404）

- [ ] **Step 3: Add handlers + routes to server.py**

在 `server.py` 的 `export` handler 之后、`routes` 之前加：

```python
_ID_PATTERN = re.compile(r"^[a-f0-9]{32}$")


async def sessions_list(_: Request) -> JSONResponse:
    return JSONResponse(sessions_store.list_sessions(BASE_DIR))


async def session_events(request: Request) -> JSONResponse:
    sid = request.path_params["sid"]
    if not _ID_PATTERN.match(sid):
        return JSONResponse({"error": "非法 session_id"}, status_code=400)
    evs = sessions_store.load_events(BASE_DIR, sid)
    if not evs and not (BASE_DIR / "sessions" / f"{sid}.jsonl").exists():
        return JSONResponse({"error": "会话不存在"}, status_code=404)
    return JSONResponse({"events": [{"event": e, "data": d} for e, d in evs]})


async def delete_session_handler(request: Request) -> JSONResponse:
    sid = request.path_params["sid"]
    if not _ID_PATTERN.match(sid):
        return JSONResponse({"error": "非法 session_id"}, status_code=400)
    # 断开常驻 client 若存在
    entry = sessions.pop(sid, None)
    if entry is not None:
        try:
            asyncio.get_event_loop().create_task(entry.client.disconnect())
        except Exception:
            pass
    sessions_store.delete_session(BASE_DIR, sid)
    return JSONResponse({"ok": True})
```

在 `server.py` 顶部 import 区确认有 `import re`（Task 2 已 import uuid 等；若无 `re` 则加）。

`routes` 列表改为：

```python
routes = [
    Route("/", index),
    Route("/chat", chat, methods=["POST"]),
    Route("/export", export, methods=["GET"]),
    Route("/sessions", sessions_list, methods=["GET"]),
    Route("/sessions/{sid}/events", session_events, methods=["GET"]),
    Route("/sessions/{sid}", delete_session_handler, methods=["DELETE"]),
]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run python server_test.py`
Expected: 19 个 `[OK]` + `全部通过。`

- [ ] **Step 5: Commit**

```bash
git add server.py server_test.py
git commit -m "feat(server): /sessions 路由 list/events/delete

提示词：Phase 5 多会话持久化 Task 3"
```

---

### Task 4: index.html 会话列表 + 回放 + 状态

**Files:**
- Modify: `index.html`（rail 加 `.rail-sessions` 折叠区 HTML + CSS；JS 加 `currentSessionId` 状态 + `loadSessions`/`renderSessionsList`/`newSession`/`switchSession`/`deleteSession`/`replayEvents`；`send()` 带 session_id；`handleEvent` 处理 `session_started`；初始化加载会话列表）
- 无自动化测试（手动验证清单见 spec）

**Interfaces:**
- Consumes: Task 3 的 `GET /sessions`、`GET /sessions/<id>/events`、`DELETE /sessions/<id>`；Task 2 的 SSE `session_started` 事件；现有 `addRow`/`addFileCard`/`addFindingsCards`/`addResultCard`/`addErrorBar`/`startTurn`/`finalizeBot`/`clearAll`
- Produces: 多会话 UI（列表/新建/切换/删除）+ 回放渲染 + `currentSessionId` 全局状态

- [ ] **Step 1: Add rail-sessions HTML + CSS**

在 `index.html` 的 `<aside class="rail">` 内，`.rail-head`（349-358 行）之后、`.rail-body`（360 行）之前插入：

```html
    <div class="rail-sessions">
      <div class="rail-sessions-head">
        <span class="rs-label">会话</span>
        <button class="rs-new" id="newSessionBtn" title="新会话">＋</button>
      </div>
      <div class="rail-sessions-list" id="sessionsList"></div>
    </div>
```

在 CSS 区（`.rail-head` 相关样式附近）加：

```css
  .rail-sessions { border-bottom: 1px solid var(--line); padding: 10px 12px; }
  .rail-sessions-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 8px; }
  .rail-sessions-head .rs-label { font-family: var(--font-mono); font-size: 11px; color: var(--muted-2); text-transform: uppercase; letter-spacing: .08em; }
  .rs-new { background: transparent; border: 1px solid var(--line); color: var(--muted); border-radius: 6px; padding: 2px 9px; cursor: pointer; font-size: 14px; line-height: 1; transition: all .2s; }
  .rs-new:hover { color: var(--accent); border-color: var(--accent); }
  .rail-sessions-list { display: flex; flex-direction: column; gap: 4px; max-height: 200px; overflow-y: auto; }
  .rs-item { display: flex; align-items: center; gap: 6px; padding: 6px 8px; border-radius: 7px; cursor: pointer; border-left: 2px solid transparent; transition: all .2s; }
  .rs-item:hover { background: var(--surface); }
  .rs-item.active { background: var(--surface); border-left-color: var(--accent); }
  .rs-item .rs-title { flex: 1; font-size: 12px; color: var(--muted); white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .rs-item.active .rs-title { color: var(--text); }
  .rs-item .rs-del { opacity: 0; background: none; border: none; color: var(--muted-2); cursor: pointer; font-size: 13px; padding: 0 2px; }
  .rs-item:hover .rs-del { opacity: 1; }
  .rs-item .rs-del:hover { color: var(--err); }
  .rs-empty { font-size: 11px; color: var(--muted-2); padding: 4px 8px; }
```

- [ ] **Step 2: Add JS — state + loadSessions + renderSessionsList**

在 `<script>` 顶部变量区（`let busy = false;` 附近，约 413 行后）加：

```javascript
let currentSessionId = null;
const sessionsListEl = document.getElementById('sessionsList');
const newSessionBtn = document.getElementById('newSessionBtn');
```

在 `TOOL_LABELS` 定义之前或 `scrollRail` 之后加函数：

```javascript
async function loadSessions() {
  try {
    const resp = await fetch('/sessions');
    if (!resp.ok) return;
    const items = await resp.json();
    renderSessionsList(items);
  } catch (e) { /* 静默 */ }
}

function renderSessionsList(items) {
  sessionsListEl.innerHTML = '';
  if (!items.length) {
    sessionsListEl.innerHTML = '<div class="rs-empty">尚无会话</div>';
    return;
  }
  for (const it of items) {
    const div = document.createElement('div');
    div.className = 'rs-item' + (it.id === currentSessionId ? ' active' : '');
    const title = document.createElement('span');
    title.className = 'rs-title';
    title.textContent = it.title || '(无标题)';
    title.title = it.title || '';
    const del = document.createElement('button');
    del.className = 'rs-del';
    del.textContent = '✕';
    del.title = '删除会话';
    del.addEventListener('click', (e) => { e.stopPropagation(); deleteSession(it.id); });
    div.appendChild(title);
    div.appendChild(del);
    div.addEventListener('click', () => switchSession(it.id));
    sessionsListEl.appendChild(div);
  }
}

async function deleteSession(sid) {
  if (!confirm('删除该会话？历史卡片将无法回看。')) return;
  await fetch(`/sessions/${sid}`, { method: 'DELETE' });
  if (sid === currentSessionId) {
    clearAll();
    currentSessionId = null;
  }
  loadSessions();
}

function clearAll() {
  messagesEl.innerHTML = '';
  railEl.innerHTML = '<div class="rail-empty" id="railEmpty">轨迹轨：每个回合的工具调用链会按顺序点亮在这里。</div>';
  turnNo = 0; currentGroup = null; pendingNode = null; currentBot = null;
}
```

> 注意：现有 `clearBtn` 监听（841-847 行）逻辑与 `clearAll` 重复。把 `clearBtn` 监听改为调 `clearAll()` + `inputEl.focus()`，并保留 `railEmpty` 重置（`clearAll` 已重建）。替换 841-847 行为：
```javascript
clearBtn.addEventListener('click', () => {
  if (busy) return;
  clearAll();
  currentSessionId = null;
  inputEl.focus();
});
```

- [ ] **Step 3: Add JS — newSession / switchSession / replayEvents**

```javascript
function newSession() {
  if (busy) return;
  clearAll();
  currentSessionId = null;
  inputEl.focus();
  loadSessions();
}

async function switchSession(sid) {
  if (busy) return;
  if (sid === currentSessionId) return;
  clearAll();
  try {
    const resp = await fetch(`/sessions/${sid}/events`);
    if (!resp.ok) { addErrorBar('加载会话失败'); return; }
    const data = await resp.json();
    currentSessionId = sid;
    replayEvents(data.events || []);
    loadSessions();
  } catch (e) {
    addErrorBar(String(e));
  }
}

function replayEvents(events) {
  // 回放：首条 session_started 设 currentSessionId 不渲染；done/error 跳过；
  // 其余按回合组织——首条非 session_started 事件前开一回合"历史回放"。
  let turnStarted = false;
  for (const { event, data } of events) {
    if (event === 'session_started') {
      currentSessionId = data.session_id;
      continue;
    }
    if (event === 'done' || event === 'error') continue;
    if (!turnStarted) {
      hideEmpty();
      startTurn('历史回放');
      turnStarted = true;
    }
    handleEvent(event, data);
  }
  if (turnStarted) finalizeBot();
  scrollThread();
  scrollRail();
}
```

- [ ] **Step 4: Wire send() to carry session_id + handle session_started**

`send()` 内 fetch body（792-796 行）改为：

```javascript
    const resp = await fetch('/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ prompt, session_id: currentSessionId }),
    });
```

`handleEvent` 的 switch（730 行）在 `case 'text':` 之前加：

```javascript
    case 'session_started':
      currentSessionId = data.session_id;
      loadSessions();
      break;
```

- [ ] **Step 5: Wire newSessionBtn + init load**

在 `clearBtn.addEventListener` 附近加：

```javascript
newSessionBtn.addEventListener('click', newSession);
```

在脚本末尾（`inputEl.addEventListener('input', autoGrow);` 之后）加初始化：

```javascript
loadSessions();
```

- [ ] **Step 6: Manual verify with preview**

Run: 启动 `preview_start harmony-game-agent`，截图验证：
1. rail 顶部出现"会话"折叠区 + ＋按钮 + "尚无会话"
2. 输入 prompt 发送 → 会话列表出现一条，标题为首句
3. 点＋新会话 → 画布清空，列表保留旧会话
4. 点旧会话 → 回放历史卡片（text/file/findings 等）+ 轨迹轨显示"历史回放"回合
5. 点 ✕ 删除 → 列表移除；删当前会话则画布清空
6. 刷新页面 → 会话列表仍在（从 /sessions 加载）

若 preview 无 API key 无法跑 Agent，至少验证：rail 渲染、点＋清空、刷新后列表加载（空列表）。Agent 交互需真 key 手动测。

- [ ] **Step 7: Commit**

```bash
git add index.html
git commit -m "feat(index): rail 会话列表 + 回放 + currentSessionId 状态

提示词：Phase 5 多会话持久化 Task 4"
```

---

### Task 5: .gitignore + CHANGELOG + 全量回归

**Files:**
- Modify: `.gitignore`（加 `sessions/`）
- Modify: `CHANGELOG.md`（加 Phase 5 段）
- 无测试（回归跑全部）

- [ ] **Step 1: Update .gitignore**

在 `.gitignore` 末尾加：

```
# Phase 5 会话事件流（本地持久化，不入仓）
sessions/
```

- [ ] **Step 2: Update CHANGELOG.md**

在文件末尾（已知边界之后）加：

```markdown

## [v1.1.0] - 2026-07-02

### Phase 5：多会话持久化与历史回放

- 多会话：每会话一个常驻 `ClaudeSDKClient`，`POST /chat` 带 `session_id` 选会话；新建/复用/resume 重建三分支；LRU 回收闲置 >10min 或超 8 个的 client
- 历史回放：每会话事件流追加写 `./sessions/<id>.jsonl`（与 SSE 同构），`GET /sessions/<id>/events` 返回 JSON，前端 rail 顶部折叠会话列表逐条重放卡片
- 会话管理 API：`GET /sessions`（列表）、`GET /sessions/<id>/events`（回放）、`DELETE /sessions/<id>`（幂等删除）
- `sessions_store.py`：纯 IO 模块（append/load/list/delete + 路径穿越与 id 正则双防护）
- 续聊：常驻 client 直接 `query(prompt, session_id)`；被 LRU 回收后 `ClaudeSDKClient(options/resume=id)` 重建；resume 失败降级新建 + 提示
- 首句标题（前 40 字），LLM 摘要留未来
```

把已知边界里的"Web 工作台无多会话持久化 / 历史回放 / 文件树浏览（Phase 5 规划中）"改为"Web 工作台无文件树浏览（未来扩展）"。

- [ ] **Step 3: Run full regression**

Run:
```bash
uv run python sessions_store_test.py
uv run python server_test.py
uv run python tools_review_test.py
uv run python analyzers/findings_test.py
uv run python main_test.py
uv run python analyzers/performance_test.py
uv run python analyzers/bug_location_test.py
uv run python analyzers/api_usage_test.py
uv run python analyzers/runtime_logs_test.py
uv run python generators/framework_test.py
```
Expected: 全部 `全部通过。`

- [ ] **Step 4: Commit**

```bash
git add .gitignore CHANGELOG.md
git commit -m "chore: Phase 5 收尾 .gitignore + CHANGELOG + 回归

提示词：Phase 5 多会话持久化 Task 5"
```

"""server.py /export 端点单测。"""

import io
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from starlette.testclient import TestClient

import server
import sessions_store


def _make_generated(tmp: Path) -> Path:
    gen = tmp / "generated"
    gen.mkdir()
    (gen / "character").mkdir()
    (gen / "character" / "Foo.ets").write_text("@Component struct Foo {}", encoding="utf-8")
    (gen / "Bar.json").write_text('{"k":1}', encoding="utf-8")
    return gen


def _patch_base_dir(tmp: Path):
    import server as srv
    orig = srv.BASE_DIR
    srv.BASE_DIR = tmp
    return orig


def test_export_single_file_ets():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig = _patch_base_dir(tmp)
        try:
            client = TestClient(server.app)
            resp = client.get("/export", params={"path": "character/Foo.ets"})
            assert resp.status_code == 200
            assert "Foo" in resp.text
            assert "text/plain" in resp.headers["content-type"]
            assert 'attachment' in resp.headers["content-disposition"]
        finally:
            server.BASE_DIR = orig
    print("[OK] test_export_single_file_ets")


def test_export_single_file_json():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig = _patch_base_dir(tmp)
        try:
            client = TestClient(server.app)
            resp = client.get("/export", params={"path": "Bar.json"})
            assert resp.status_code == 200
            assert "application/json" in resp.headers["content-type"]
        finally:
            server.BASE_DIR = orig
    print("[OK] test_export_single_file_json")


def test_export_directory_returns_zip():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig = _patch_base_dir(tmp)
        try:
            client = TestClient(server.app)
            resp = client.get("/export", params={"path": "character"})
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/zip"
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            names = zf.namelist()
            assert "Foo.ets" in names
        finally:
            server.BASE_DIR = orig
    print("[OK] test_export_directory_returns_zip")


def test_export_path_traversal_rejected():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig = _patch_base_dir(tmp)
        try:
            client = TestClient(server.app)
            resp = client.get("/export", params={"path": "../../etc/passwd"})
            assert resp.status_code == 400
        finally:
            server.BASE_DIR = orig
    print("[OK] test_export_path_traversal_rejected")


def test_export_nonexistent_returns_404():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig = _patch_base_dir(tmp)
        try:
            client = TestClient(server.app)
            resp = client.get("/export", params={"path": "nope"})
            assert resp.status_code == 404
        finally:
            server.BASE_DIR = orig
    print("[OK] test_export_nonexistent_returns_404")


def test_export_missing_path_returns_400():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig = _patch_base_dir(tmp)
        try:
            client = TestClient(server.app)
            resp = client.get("/export")
            assert resp.status_code == 400
        finally:
            server.BASE_DIR = orig
    print("[OK] test_export_missing_path_returns_400")


def test_build_zip_no_slip():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        gen = _make_generated(tmp)
        data = server._build_zip(gen / "character")
        zf = zipfile.ZipFile(io.BytesIO(data))
        for name in zf.namelist():
            assert not name.startswith("/")
            assert ".." not in name.split("/")
    print("[OK] test_build_zip_no_slip")


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


def _patch_sdk_client(fake_factory):
    """把 server.ClaudeSDKClient 替换为返回 _FakeClient 的工厂。返回 orig 供还原。"""
    import server as srv
    orig = srv.ClaudeSDKClient
    srv.ClaudeSDKClient = lambda options=None: fake_factory(options)
    return orig


def _parse_sse_events(text: str) -> list[tuple[str, dict]]:
    """同 _parse_sse 但容忍多 data 行；用于会话测试。"""
    return _parse_sse(text)


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    """把 SSE 文本解析为 [(event, data), ...] 列表。"""
    events = []
    cur_event = None
    for line in text.split("\n"):
        if line.startswith("event: "):
            cur_event = line[len("event: "):]
        elif line.startswith("data: ") and cur_event is not None:
            import json
            events.append((cur_event, json.loads(line[len("data: "):])))
            cur_event = None
    return events


def test_stream_file_event_for_write():
    """Write tool_use（file_path 在 generated/ 下）+ 匹配的 tool_result → 断言发 file 事件。"""
    import tempfile
    from claude_agent_sdk import AssistantMessage, UserMessage, ToolUseBlock, ToolResultBlock

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig_base = _patch_base_dir(tmp)
        fake = _FakeClient([
            AssistantMessage(content=[ToolUseBlock(
                id="w1", name="Write",
                input={"file_path": str(tmp / "generated" / "character" / "Foo.ets"),
                       "content": "@Component struct Foo {}"},
            )], model="test"),
            UserMessage(content=[ToolResultBlock(
                tool_use_id="w1",
                content=[{"type": "text", "text": "wrote"}],
                is_error=False,
            )]),
        ])
        orig = _patch_sdk_client(lambda options=None: fake)
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
            server.BASE_DIR = orig_base
            server.ClaudeSDKClient = orig
            server.sessions.clear()
    print("[OK] test_stream_file_event_for_write")


def test_stream_findings_event_for_json_result():
    """非 Write tool_use + tool_result 内容为 JSON 数组 → 断言发 findings 事件。"""
    from claude_agent_sdk import AssistantMessage, UserMessage, ToolUseBlock, ToolResultBlock

    json_text = '[{"severity":"高","location":"a","summary":"s","fix":"f"}]'
    fake = _FakeClient([
        AssistantMessage(content=[ToolUseBlock(
            id="c1", name="check_api_usage", input={},
        )], model="test"),
        UserMessage(content=[ToolResultBlock(
            tool_use_id="c1",
            content=[{"type": "text", "text": json_text}],
            is_error=False,
        )]),
    ])
    orig = _patch_sdk_client(lambda options=None: fake)
    try:
        tc = TestClient(server.app)
        resp = tc.post("/chat", json={"prompt": "x"})
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        kinds = [e for e, _ in events]
        assert "findings" in kinds, f"缺少 findings 事件，实际: {kinds}"
        idx = kinds.index("findings")
        data = events[idx][1]
        assert isinstance(data["findings"], list)
        assert len(data["findings"]) == 1
        f = data["findings"][0]
        assert f["severity"] == "高"
        assert f["location"] == "a"
        assert f["summary"] == "s"
        assert f["fix"] == "f"
        assert data["is_error"] is False
    finally:
        server.ClaudeSDKClient = orig
        server.sessions.clear()
    print("[OK] test_stream_findings_event_for_json_result")


def test_stream_tool_result_fallback_for_plain_text():
    """非 Write tool_use + tool_result 内容为纯文本非 JSON → 断言发 tool_result 事件。"""
    from claude_agent_sdk import AssistantMessage, UserMessage, ToolUseBlock, ToolResultBlock

    fake = _FakeClient([
        AssistantMessage(content=[ToolUseBlock(
            id="r1", name="review_arkts_code", input={},
        )], model="test"),
        UserMessage(content=[ToolResultBlock(
            tool_use_id="r1",
            content=[{"type": "text", "text": "just plain text"}],
            is_error=False,
        )]),
    ])
    orig = _patch_sdk_client(lambda options=None: fake)
    try:
        tc = TestClient(server.app)
        resp = tc.post("/chat", json={"prompt": "x"})
        assert resp.status_code == 200
        events = _parse_sse(resp.text)
        kinds = [e for e, _ in events]
        assert "tool_result" in kinds, f"缺少 tool_result 事件，实际: {kinds}"
        idx = kinds.index("tool_result")
        data = events[idx][1]
        assert data["text"] == "just plain text"
        assert data["is_error"] is False
    finally:
        server.ClaudeSDKClient = orig
        server.sessions.clear()
    print("[OK] test_stream_tool_result_fallback_for_plain_text")


def _result_msg():
    """构造一个最小 ResultMessage。"""
    from claude_agent_sdk import ResultMessage
    return ResultMessage(subtype="result", duration_ms=0, duration_api_ms=0, is_error=False, num_turns=1, session_id="test")


def test_new_session_emits_session_started_and_writes_jsonl():
    """POST /chat 无 session_id → 新建：首条 SSE session_started 含新 UUID，JSONL 写入。"""
    import tempfile
    from claude_agent_sdk import AssistantMessage, TextBlock
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        orig_base = _patch_base_dir(tmp)
        fake = _FakeClient([AssistantMessage(content=[TextBlock(text="hi")], model="test"), _result_msg()])
        orig = _patch_sdk_client(lambda options=None: fake)
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


def test_resume_existing_session_reuses_client():
    """同 session_id 二次 POST → 复用 sessions[sid].client（connect_calls 不增）。"""
    import tempfile
    from claude_agent_sdk import AssistantMessage, TextBlock
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        orig_base = _patch_base_dir(tmp)
        fake = _FakeClient([AssistantMessage(content=[TextBlock(text="hi")], model="test"), _result_msg()])
        orig = _patch_sdk_client(lambda options=None: fake)
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
    from claude_agent_sdk import AssistantMessage, TextBlock
    captured_options = {}
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        orig_base = _patch_base_dir(tmp)
        fake = _FakeClient([AssistantMessage(content=[TextBlock(text="hi")], model="test"), _result_msg()])
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


def test_sessions_list_empty():
    """空会话目录 → GET /sessions 返回 200 + []。"""
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
    """一次 /chat 后 GET /sessions 返回含该会话的列表。"""
    import tempfile
    from claude_agent_sdk import AssistantMessage, TextBlock
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        orig_base = _patch_base_dir(tmp)
        fake = _FakeClient([AssistantMessage(content=[TextBlock(text="hi")], model="test"), _result_msg()])
        orig = _patch_sdk_client(lambda options=None: fake)
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
    """GET /sessions/<id>/events 返回事件 JSON 列表。"""
    import tempfile
    from claude_agent_sdk import AssistantMessage, TextBlock
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        orig_base = _patch_base_dir(tmp)
        fake = _FakeClient([AssistantMessage(content=[TextBlock(text="hello")], model="test"), _result_msg()])
        orig = _patch_sdk_client(lambda options=None: fake)
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
    """不存在的会话 → GET /sessions/<id>/events 返回 404。"""
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
    """非法 session_id → 返回 400 或 404。

    说明：路径含 ``..`` 时 Starlette 路由层先行规范化导致不匹配 → 404；
    路径为 32 字符非 hex（如全 'g'）时路由匹配但 handler 正则校验 → 400。
    二者均表明非法 id 被正确拒绝。这里沿用 brief 的 ``../etc/events`` 路径，
    并接受 400 或 404。
    """
    tc = TestClient(server.app)
    resp = tc.get("/sessions/../etc/events")
    assert resp.status_code in (400, 404), f"期望 400 或 404，实际 {resp.status_code}"
    print("[OK] test_session_events_illegal_id_400")


def test_delete_session_idempotent():
    """DELETE /sessions/<id> 幂等：不存在返回 200，存在也返回 200 且删除文件。"""
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


class _ConnectRaisingFake:
    """connect 永远 raise 的桩，用于模拟 resume 失败。"""

    def __init__(self, exc):
        self._exc = exc

    async def connect(self, prompt=None):
        raise self._exc

    async def disconnect(self):
        pass


def test_chat_rejects_illegal_session_id():
    """POST /chat {session_id:"abc123"} → 400，因 session_id 非 32 位 hex。"""
    tc = TestClient(server.app)
    resp = tc.post("/chat", json={"prompt": "x", "session_id": "abc123"})
    assert resp.status_code == 400, f"期望 400，实际 {resp.status_code}"
    assert "非法" in resp.json().get("error", "")
    print("[OK] test_chat_rejects_illegal_session_id")


def test_resume_failure_degrades_to_new_session():
    """resume 失败（connect raise）→ 降级新建：SSE 首条 session_started，次条 error；
    JSONL 首行 session_started；sessions 含新 sid 的 ClientEntry；新 sid 为 32 hex。"""
    import re as _re
    import tempfile
    from claude_agent_sdk import AssistantMessage, TextBlock

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        orig_base = _patch_base_dir(tmp)
        old_sid = "b" * 32
        # 先准备一个旧会话的 JSONL，让 _get_or_create_client 走 resume 路径
        sessions_store.append_event(tmp, old_sid, "session_started",
                                    {"session_id": old_sid, "title": "旧", "created_at": "x"})
        # 第一次工厂调用（resume 路径）返回 connect 抛异常的桩；
        # 第二次（降级新建）返回正常 _FakeClient
        good_fake = _FakeClient([AssistantMessage(content=[TextBlock(text="hi")], model="test"), _result_msg()])
        call_count = {"n": 0}

        def _factory(options=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return _ConnectRaisingFake(RuntimeError("resume boom"))
            return good_fake

        orig = _patch_sdk_client(_factory)
        try:
            tc = TestClient(server.app)
            resp = tc.post("/chat", json={"prompt": "继续", "session_id": old_sid})
            assert resp.status_code == 200, resp.text
            events = _parse_sse(resp.text)
            # 首条必须是 session_started（不是 error）
            assert events[0][0] == "session_started", f"首条应为 session_started，实际: {events[0]}"
            new_sid = events[0][1]["session_id"]
            assert new_sid != old_sid, "降级应生成新 sid"
            assert _re.match(r"^[a-f0-9]{32}$", new_sid), f"新 sid 非 32 hex: {new_sid}"
            assert events[0][1]["title"] == "继续"
            # 次条应为 error
            assert events[1][0] == "error", f"次条应为 error，实际: {events[1]}"
            assert "resume boom" in events[1][1]["message"]
            # JSONL 首行是 session_started
            evs = sessions_store.load_events(tmp, new_sid)
            assert len(evs) >= 2
            assert evs[0][0] == "session_started", f"JSONL 首行应为 session_started，实际: {evs[0]}"
            assert evs[1][0] == "error"
            # sessions dict 含新 sid 的 ClientEntry
            assert new_sid in server.sessions
            # 旧 sid 不在 sessions（resume 失败未注册）
            assert old_sid not in server.sessions
        finally:
            server.BASE_DIR = orig_base
            server.ClaudeSDKClient = orig
            server.sessions.clear()
    print("[OK] test_resume_failure_degrades_to_new_session")


def main():
    test_export_single_file_ets()
    test_export_single_file_json()
    test_export_directory_returns_zip()
    test_export_path_traversal_rejected()
    test_export_nonexistent_returns_404()
    test_export_missing_path_returns_400()
    test_build_zip_no_slip()
    test_stream_file_event_for_write()
    test_stream_findings_event_for_json_result()
    test_stream_tool_result_fallback_for_plain_text()
    test_new_session_emits_session_started_and_writes_jsonl()
    test_resume_existing_session_reuses_client()
    test_resumed_session_after_eviction_rebuilds_via_resume()
    test_chat_rejects_illegal_session_id()
    test_resume_failure_degrades_to_new_session()
    test_sessions_list_empty()
    test_sessions_list_after_chat()
    test_session_events_returns_json()
    test_session_events_not_found_404()
    test_session_events_illegal_id_400()
    test_delete_session_idempotent()
    print("\n全部通过。")


if __name__ == "__main__":
    main()

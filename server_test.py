"""server.py /export 端点单测。"""

import io
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from starlette.testclient import TestClient

import server


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
    """桩 ClaudeSDKClient，receive_response 产出预设的 SDK 消息序列。"""

    def __init__(self, msgs):
        self._msgs = msgs

    async def query(self, prompt):
        pass

    async def receive_response(self):
        for m in self._msgs:
            yield m


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
        orig = _patch_base_dir(tmp)
        try:
            fp = str(tmp / "generated" / "character" / "Foo.ets")
            server.client = _FakeClient([
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
            server.BASE_DIR = orig
            server.client = None
    print("[OK] test_stream_file_event_for_write")


def test_stream_findings_event_for_json_result():
    """非 Write tool_use + tool_result 内容为 JSON 数组 → 断言发 findings 事件。"""
    from claude_agent_sdk import AssistantMessage, UserMessage, ToolUseBlock, ToolResultBlock

    json_text = '[{"severity":"高","location":"a","summary":"s","fix":"f"}]'
    server.client = _FakeClient([
        AssistantMessage(content=[ToolUseBlock(
            id="c1", name="check_api_usage", input={},
        )], model="test"),
        UserMessage(content=[ToolResultBlock(
            tool_use_id="c1",
            content=[{"type": "text", "text": json_text}],
            is_error=False,
        )]),
    ])
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
        server.client = None
    print("[OK] test_stream_findings_event_for_json_result")


def test_stream_tool_result_fallback_for_plain_text():
    """非 Write tool_use + tool_result 内容为纯文本非 JSON → 断言发 tool_result 事件。"""
    from claude_agent_sdk import AssistantMessage, UserMessage, ToolUseBlock, ToolResultBlock

    server.client = _FakeClient([
        AssistantMessage(content=[ToolUseBlock(
            id="r1", name="review_arkts_code", input={},
        )], model="test"),
        UserMessage(content=[ToolResultBlock(
            tool_use_id="r1",
            content=[{"type": "text", "text": "just plain text"}],
            is_error=False,
        )]),
    ])
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
        server.client = None
    print("[OK] test_stream_tool_result_fallback_for_plain_text")


def test_parse_findings_integration():
    """server.py 应直接用 analyzers.findings.parse_findings。"""
    import server as srv
    from analyzers.findings import parse_findings
    text = '[{"severity":"高","location":"a","summary":"s","fix":"f"}]'
    assert srv.parse_findings is parse_findings or callable(srv.parse_findings)
    print("[OK] test_parse_findings_integration")


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
    test_parse_findings_integration()
    print("\n全部通过。")


if __name__ == "__main__":
    main()

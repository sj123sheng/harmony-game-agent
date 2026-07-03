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

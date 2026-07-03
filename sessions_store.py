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

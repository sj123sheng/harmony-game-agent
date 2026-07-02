"""analyze_runtime_logs 冒烟 + 路径提取测试。"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from analyzers.runtime_logs import (
    analyze_runtime_logs,
    _extract_ets_paths,
    _truncate_logs,
    _LOGS_SYSTEM_PROMPT,
)


def test_extract_ets_paths_finds_ets_in_stack():
    logs = (
        "E 0x00 Arksys: at HandleAttack (character/CharacterStats.ets:42:8)\n"
        "E 0x01 Arksys: at doAttack (skill/Skill.ets:10:3)\n"
        "some unrelated line"
    )
    paths = _extract_ets_paths(logs)
    assert "character/CharacterStats.ets" in paths
    assert "skill/Skill.ets" in paths
    print("[OK] test_extract_ets_paths_finds_ets_in_stack")


def test_extract_ets_paths_returns_empty_when_none():
    assert _extract_ets_paths("纯文本日志无路径\n另一行") == []
    print("[OK] test_extract_ets_paths_returns_empty_when_none")


def test_extract_ets_paths_dedup():
    logs = "at A (character/Foo.ets:1)\n at B (character/Foo.ets:2)"
    assert _extract_ets_paths(logs) == ["character/Foo.ets"]
    print("[OK] test_extract_ets_paths_dedup")


def test_truncate_logs_keeps_tail():
    big = "A" * (35 * 1024)
    out = _truncate_logs(big, limit_bytes=10 * 1024)
    assert len(out) <= 10 * 1024
    assert out == "A" * (10 * 1024)
    print("[OK] test_truncate_logs_keeps_tail")


def test_system_prompt_covers_log_dimensions():
    assert "堆栈" in _LOGS_SYSTEM_PROMPT
    assert "根因" in _LOGS_SYSTEM_PROMPT
    assert "置信度" in _LOGS_SYSTEM_PROMPT
    print("[OK] test_system_prompt_covers_log_dimensions")


def _fake_block(text: str):
    return SimpleNamespace(text=text)


class _FakeMessages:
    def __init__(self, return_text: str):
        self._return_text = return_text
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[_fake_block(self._return_text)])


class _FakeAnthropic:
    def __init__(self, return_text: str):
        self.messages = _FakeMessages(return_text)

    def __call__(self, *args, **kwargs):
        return self


def _patch_fake(return_text: str = "日志分析报告"):
    import analyzers.framework as fw
    fake = _FakeAnthropic(return_text)
    orig = fw.AsyncAnthropic
    fw.AsyncAnthropic = lambda *a, **k: fake
    return orig, fake


def test_analyze_runtime_logs_pulls_files_mentioned_in_logs():
    """日志提到 .ets 路径时，应把这些文件拉进上下文。"""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "character").mkdir()
        Path(d, "character", "CharacterStats.ets").write_text(
            "export struct CharacterStats { hp: number = 0 }", encoding="utf-8")
        orig, fake = _patch_fake()
        try:
            asyncio.run(analyze_runtime_logs({
                "logs": "E at HandleAttack (character/CharacterStats.ets:42:8)",
                "scan_dir": d,
            }))
        finally:
            import analyzers.framework as fw
            fw.AsyncAnthropic = orig
    sent = fake.messages.calls[0]["messages"][0]["content"]
    assert "character/CharacterStats.ets" in sent
    assert "export struct CharacterStats" in sent
    print("[OK] test_analyze_runtime_logs_pulls_files_mentioned_in_logs")


def test_analyze_runtime_logs_falls_back_to_scope_when_no_paths():
    """日志无 .ets 路径时，fallback 到 scope 拉上下文。"""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "skill").mkdir()
        Path(d, "skill", "Skill.ets").write_text("export struct Skill {}", encoding="utf-8")
        orig, fake = _patch_fake()
        try:
            asyncio.run(analyze_runtime_logs({
                "logs": "纯文本报错，无路径",
                "scope": "skill",
                "scan_dir": d,
            }))
        finally:
            import analyzers.framework as fw
            fw.AsyncAnthropic = orig
    sent = fake.messages.calls[0]["messages"][0]["content"]
    assert "export struct Skill" in sent
    print("[OK] test_analyze_runtime_logs_falls_back_to_scope_when_no_paths")


def test_analyze_runtime_logs_falls_back_to_all_by_default():
    with tempfile.TemporaryDirectory() as d:
        Path(d, "enemy").mkdir()
        Path(d, "enemy", "Enemy.ets").write_text("export struct Enemy {}", encoding="utf-8")
        orig, fake = _patch_fake()
        try:
            asyncio.run(analyze_runtime_logs({
                "logs": "纯文本",
                "scan_dir": d,
            }))
        finally:
            import analyzers.framework as fw
            fw.AsyncAnthropic = orig
    sent = fake.messages.calls[0]["messages"][0]["content"]
    assert "export struct Enemy" in sent  # scope 默认 all 拉到 enemy
    print("[OK] test_analyze_runtime_logs_falls_back_to_all_by_default")


def main():
    test_extract_ets_paths_finds_ets_in_stack()
    test_extract_ets_paths_returns_empty_when_none()
    test_extract_ets_paths_dedup()
    test_truncate_logs_keeps_tail()
    test_system_prompt_covers_log_dimensions()
    test_analyze_runtime_logs_pulls_files_mentioned_in_logs()
    test_analyze_runtime_logs_falls_back_to_scope_when_no_paths()
    test_analyze_runtime_logs_falls_back_to_all_by_default()
    print("\n全部通过。")


if __name__ == "__main__":
    main()

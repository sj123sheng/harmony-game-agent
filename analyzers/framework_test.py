"""analyzers/framework 共享原语测试 + 冒烟。沿用 generators 自跑模式。"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from analyzers.framework import FileRef, resolve_scope, analyze_with_context


# ---------- resolve_scope 三形态 ----------

def test_resolve_scope_all_scans_known_subsystems():
    with tempfile.TemporaryDirectory() as d:
        Path(d, "character").mkdir()
        Path(d, "character", "CharacterStats.ets").write_text(
            "export struct CharacterStats {}", encoding="utf-8")
        Path(d, "skill").mkdir()
        Path(d, "skill", "Skill.ets").write_text("export struct Skill {}", encoding="utf-8")
        Path(d, "ignored").mkdir()  # 非已知子系统，应忽略
        refs = resolve_scope("all", scan_dir=d)
        paths = [r.path for r in refs]
        assert "character/CharacterStats.ets" in paths
        assert "skill/Skill.ets" in paths
        assert not any(p.startswith("ignored/") for p in paths)
    print("[OK] test_resolve_scope_all_scans_known_subsystems")


def test_resolve_scope_single_subsystem():
    with tempfile.TemporaryDirectory() as d:
        Path(d, "enemy").mkdir()
        Path(d, "enemy", "Enemy.ets").write_text("export struct Enemy {}", encoding="utf-8")
        refs = resolve_scope("enemy", scan_dir=d)
        assert len(refs) == 1
        assert refs[0].path == "enemy/Enemy.ets"
        assert "export struct Enemy" in refs[0].content
    print("[OK] test_resolve_scope_single_subsystem")


def test_resolve_scope_file_path_relative():
    with tempfile.TemporaryDirectory() as d:
        Path(d, "character").mkdir()
        Path(d, "character", "CharacterStats.ets").write_text("X", encoding="utf-8")
        refs = resolve_scope("character/CharacterStats.ets", scan_dir=d)
        assert len(refs) == 1
        assert refs[0].path == "character/CharacterStats.ets"
    print("[OK] test_resolve_scope_file_path_relative")


# ---------- 路径穿越防护 ----------

def test_resolve_scope_rejects_dotdot_traversal():
    with tempfile.TemporaryDirectory() as d:
        Path(d, "character").mkdir()
        # 在 scan_dir 的父级放一个秘密文件
        secret = Path(d).parent / "secret_token.txt"
        secret.write_text("SECRET", encoding="utf-8")
        try:
            refs = resolve_scope("../secret_token.txt", scan_dir=d)
            assert refs == [], refs
        finally:
            secret.unlink(missing_ok=True)
    print("[OK] test_resolve_scope_rejects_dotdot_traversal")


def test_resolve_scope_rejects_absolute_path():
    with tempfile.TemporaryDirectory() as d:
        refs = resolve_scope("/etc/passwd", scan_dir=d)
        assert refs == [], refs
    print("[OK] test_resolve_scope_rejects_absolute_path")


# ---------- 找不到兜底 ----------

def test_resolve_scope_unknown_subsystem_returns_empty():
    with tempfile.TemporaryDirectory() as d:
        # 'quest' 不在 _KNOWN_SUBSYSTEMS，当文件路径处理也不存在
        assert resolve_scope("quest", scan_dir=d) == []
    print("[OK] test_resolve_scope_unknown_subsystem_returns_empty")


def test_resolve_scope_nonexistent_file_returns_empty():
    with tempfile.TemporaryDirectory() as d:
        assert resolve_scope("nope/Missing.ets", scan_dir=d) == []
    print("[OK] test_resolve_scope_nonexistent_file_returns_empty")


# ---------- analyze_with_context 拼装格式 ----------

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


def _patch_fake(return_text: str):
    import analyzers.framework as fw
    fake = _FakeAnthropic(return_text)
    orig = fw.AsyncAnthropic
    fw.AsyncAnthropic = lambda *a, **k: fake
    return orig, fake


def test_analyze_with_context_emits_xml_file_format():
    orig, fake = _patch_fake("分析结果")
    try:
        files = [FileRef(path="character/CharacterStats.ets", content="export struct X {}")]
        asyncio.run(analyze_with_context("sys", "审查这段", files))
    finally:
        import analyzers.framework as fw
        fw.AsyncAnthropic = orig
    sent = fake.messages.calls[0]["messages"][0]["content"]
    assert '<files>' in sent
    assert '</files>' in sent
    assert '<file path="character/CharacterStats.ets">' in sent
    assert "export struct X {}" in sent
    assert "审查这段" in sent
    print("[OK] test_analyze_with_context_emits_xml_file_format")


def test_analyze_with_context_omits_files_section_when_empty():
    orig, fake = _patch_fake("纯文本结果")
    try:
        asyncio.run(analyze_with_context("sys", "纯日志问题", []))
    finally:
        import analyzers.framework as fw
        fw.AsyncAnthropic = orig
    sent = fake.messages.calls[0]["messages"][0]["content"]
    assert '<files>' not in sent
    assert "纯日志问题" in sent
    print("[OK] test_analyze_with_context_omits_files_section_when_empty")


def test_analyze_with_context_truncates_over_limit():
    orig, fake = _patch_fake("ok")
    try:
        big = "A" * (90 * 1024)  # 90KB > 80KB 上限
        files = [FileRef(path="big/File.ets", content=big)]
        asyncio.run(analyze_with_context("sys", "q", files))
    finally:
        import analyzers.framework as fw
        fw.AsyncAnthropic = orig
    sent = fake.messages.calls[0]["messages"][0]["content"]
    assert "[已截断]" in sent
    # 不应含完整 90KB
    assert len(sent) < 90 * 1024
    print("[OK] test_analyze_with_context_truncates_over_limit")


def test_analyze_with_context_returns_text():
    orig, _ = _patch_fake("这里是分析报告")
    try:
        text = asyncio.run(analyze_with_context("sys", "q", []))
    finally:
        import analyzers.framework as fw
        fw.AsyncAnthropic = orig
    assert text == "这里是分析报告"
    print("[OK] test_analyze_with_context_returns_text")


def test_analyze_with_context_raises_on_llm_failure():
    import analyzers.framework as fw
    class _Raising:
        async def create(self, **k):
            raise RuntimeError("余额不足")
    raising = _Raising()
    orig = fw.AsyncAnthropic
    fw.AsyncAnthropic = lambda *a, **k: SimpleNamespace(messages=raising)
    try:
        try:
            asyncio.run(analyze_with_context("sys", "q", []))
            assert False, "应抛异常"
        except RuntimeError as e:
            assert "余额不足" in str(e)
    finally:
        fw.AsyncAnthropic = orig
    print("[OK] test_analyze_with_context_raises_on_llm_failure")


def main():
    test_resolve_scope_all_scans_known_subsystems()
    test_resolve_scope_single_subsystem()
    test_resolve_scope_file_path_relative()
    test_resolve_scope_rejects_dotdot_traversal()
    test_resolve_scope_rejects_absolute_path()
    test_resolve_scope_unknown_subsystem_returns_empty()
    test_resolve_scope_nonexistent_file_returns_empty()
    test_analyze_with_context_emits_xml_file_format()
    test_analyze_with_context_omits_files_section_when_empty()
    test_analyze_with_context_truncates_over_limit()
    test_analyze_with_context_returns_text()
    test_analyze_with_context_raises_on_llm_failure()
    print("\n全部通过。")


if __name__ == "__main__":
    main()

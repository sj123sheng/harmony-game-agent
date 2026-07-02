"""suggest_performance_fixes 冒烟测试。"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from analyzers.performance import suggest_performance_fixes, _PERF_SYSTEM_PROMPT


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


def test_system_prompt_covers_perf_dimensions():
    assert "LazyForEach" in _PERF_SYSTEM_PROMPT
    assert "@State" in _PERF_SYSTEM_PROMPT
    assert "build()" in _PERF_SYSTEM_PROMPT
    assert "aboutToDisappear" in _PERF_SYSTEM_PROMPT
    assert "JSON 数组" in _PERF_SYSTEM_PROMPT
    print("[OK] test_system_prompt_covers_perf_dimensions")


def test_suggest_performance_fixes_returns_text():
    with tempfile.TemporaryDirectory() as d:
        Path(d, "character").mkdir()
        Path(d, "character", "CharacterStats.ets").write_text(
            "export struct CharacterStats { @State hp: number = 0 }", encoding="utf-8")
        orig, fake = _patch_fake("性能报告：建议用 LazyForEach")
        try:
            text = asyncio.run(suggest_performance_fixes({
                "scope": "character",
                "scan_dir": d,
                "symptom": "列表卡顿",
            }))
        finally:
            import analyzers.framework as fw
            fw.AsyncAnthropic = orig
    assert "性能报告" in text
    print("[OK] test_suggest_performance_fixes_returns_text")


def test_suggest_performance_fixes_uses_4096_tokens():
    with tempfile.TemporaryDirectory() as d:
        orig, fake = _patch_fake("r")
        try:
            asyncio.run(suggest_performance_fixes({
                "scope": "character", "scan_dir": d,
            }))
        finally:
            import analyzers.framework as fw
            fw.AsyncAnthropic = orig
    assert fake.messages.calls[0]["max_tokens"] == 4096
    print("[OK] test_suggest_performance_fixes_uses_4096_tokens")


def main():
    test_system_prompt_covers_perf_dimensions()
    test_suggest_performance_fixes_returns_text()
    test_suggest_performance_fixes_uses_4096_tokens()
    print("\n全部通过。")


if __name__ == "__main__":
    main()

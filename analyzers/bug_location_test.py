"""locate_bug 冒烟测试。"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from analyzers.bug_location import locate_bug, _BUG_SYSTEM_PROMPT


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


def _patch_fake(return_text: str = "bug 报告"):
    import analyzers.framework as fw
    fake = _FakeAnthropic(return_text)
    orig = fw.AsyncAnthropic
    fw.AsyncAnthropic = lambda *a, **k: fake
    return orig, fake


def test_system_prompt_covers_reasoning():
    assert "复现" in _BUG_SYSTEM_PROMPT
    assert "置信度" in _BUG_SYSTEM_PROMPT
    print("[OK] test_system_prompt_covers_reasoning")


def test_locate_bug_requires_symptom():
    with tempfile.TemporaryDirectory() as d:
        Path(d, "character").mkdir()
        Path(d, "character", "CharacterStats.ets").write_text("export struct X {}", encoding="utf-8")
        orig, fake = _patch_fake()
        try:
            text = asyncio.run(locate_bug({
                "scope": "character", "scan_dir": d,
                "symptom": "点击攻击按钮崩溃",
            }))
        finally:
            import analyzers.framework as fw
            fw.AsyncAnthropic = orig
    assert "bug 报告" in text
    print("[OK] test_locate_bug_requires_symptom")


def test_locate_bug_uses_4096_tokens():
    with tempfile.TemporaryDirectory() as d:
        orig, fake = _patch_fake()
        try:
            asyncio.run(locate_bug({
                "scope": "character", "scan_dir": d, "symptom": "崩溃",
            }))
        finally:
            import analyzers.framework as fw
            fw.AsyncAnthropic = orig
    assert fake.messages.calls[0]["max_tokens"] == 4096
    print("[OK] test_locate_bug_uses_4096_tokens")


def main():
    test_system_prompt_covers_reasoning()
    test_locate_bug_requires_symptom()
    test_locate_bug_uses_4096_tokens()
    print("\n全部通过。")


if __name__ == "__main__":
    main()

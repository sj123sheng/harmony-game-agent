"""check_api_usage 冒烟测试。"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from analyzers.api_usage import check_api_usage, _API_SYSTEM_PROMPT


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


def _patch_fake(return_text: str = "api 报告"):
    import analyzers.framework as fw
    fake = _FakeAnthropic(return_text)
    orig = fw.AsyncAnthropic
    fw.AsyncAnthropic = lambda *a, **k: fake
    return orig, fake


def test_system_prompt_mentions_v1_v2():
    assert "V1" in _API_SYSTEM_PROMPT or "V2" in _API_SYSTEM_PROMPT
    assert "@ComponentV2" in _API_SYSTEM_PROMPT
    assert "JSON 数组" in _API_SYSTEM_PROMPT
    print("[OK] test_system_prompt_mentions_v1_v2")


def test_system_prompt_requires_harmonyos_610_api23_policy():
    assert "compatibleSdkVersion" in _API_SYSTEM_PROMPT
    assert "6.1.0(23)" in _API_SYSTEM_PROMPT
    assert "API 23" in _API_SYSTEM_PROMPT
    print("[OK] test_system_prompt_requires_harmonyos_610_api23_policy")


def test_check_api_usage_returns_text():
    with tempfile.TemporaryDirectory() as d:
        Path(d, "skill").mkdir()
        Path(d, "skill", "Skill.ets").write_text("export struct Skill {}", encoding="utf-8")
        orig, fake = _patch_fake()
        try:
            text = asyncio.run(check_api_usage({
                "scope": "skill", "scan_dir": d, "focus_apis": "@State",
            }))
        finally:
            import analyzers.framework as fw
            fw.AsyncAnthropic = orig
    assert "api 报告" in text
    print("[OK] test_check_api_usage_returns_text")


def test_check_api_usage_uses_2048_tokens():
    with tempfile.TemporaryDirectory() as d:
        orig, fake = _patch_fake()
        try:
            asyncio.run(check_api_usage({"scope": "skill", "scan_dir": d}))
        finally:
            import analyzers.framework as fw
            fw.AsyncAnthropic = orig
    assert fake.messages.calls[0]["max_tokens"] == 2048
    print("[OK] test_check_api_usage_uses_2048_tokens")


def main():
    test_system_prompt_mentions_v1_v2()
    test_system_prompt_requires_harmonyos_610_api23_policy()
    test_check_api_usage_returns_text()
    test_check_api_usage_uses_2048_tokens()
    print("\n全部通过。")


if __name__ == "__main__":
    main()

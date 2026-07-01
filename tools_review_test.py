"""review_arkts_code A1 重构回归测试：断言仍走 analyze_with_context 且返回文本。"""

import asyncio
import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tools  # noqa: E402


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


def _patch_fake(return_text: str = "审查报告：1 个问题"):
    import analyzers.framework as fw
    fake = _FakeAnthropic(return_text)
    orig = fw.AsyncAnthropic
    fw.AsyncAnthropic = lambda *a, **k: fake
    return orig, fake


def test_review_returns_text_and_uses_framework():
    orig, fake = _patch_fake()
    try:
        # @tool 包装成 SdkMcpTool，原 async 函数挂在 .handler
        result = asyncio.run(tools.review_arkts_code.handler({"code": "export struct X {}"}))
    finally:
        import analyzers.framework as fw
        fw.AsyncAnthropic = orig
    # 走了 analyze_with_context（framework 的 AsyncAnthropic 被调用）
    assert len(fake.messages.calls) == 1
    assert fake.messages.calls[0]["system"].startswith("你是一名资深 HarmonyOS ArkTS 代码审查专家")
    # 返回 MCP 文本结构
    assert result["content"][0]["text"] == "审查报告：1 个问题"
    print("[OK] test_review_returns_text_and_uses_framework")


def test_review_failure_returns_friendly_text():
    import analyzers.framework as fw
    class _Raising:
        async def create(self, **k):
            raise RuntimeError("余额不足")
    raising = _Raising()
    orig = fw.AsyncAnthropic
    fw.AsyncAnthropic = lambda *a, **k: SimpleNamespace(messages=raising)
    try:
        result = asyncio.run(tools.review_arkts_code.handler({"code": "x"}))
    finally:
        fw.AsyncAnthropic = orig
    assert "审查失败" in result["content"][0]["text"]
    print("[OK] test_review_failure_returns_friendly_text")


def main():
    test_review_returns_text_and_uses_framework()
    test_review_failure_returns_friendly_text()
    print("\n全部通过。")


if __name__ == "__main__":
    main()

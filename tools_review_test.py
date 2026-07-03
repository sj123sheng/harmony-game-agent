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
    # review JSON 化后，入口仍返回 analyze_with_context 的原文（JSON 文本），
    # 解析责任在 server/main（与其他 4 个 analyzer 一致）。
    payload = '[{"severity":"高","location":"X:1","summary":"状态过宽","fix":"缩小@State范围","category":"状态管理"}]'
    orig, fake = _patch_fake(payload)
    try:
        # @tool 包装成 SdkMcpTool，原 async 函数挂在 .handler
        result = asyncio.run(tools.review_arkts_code.handler({"code": "export struct X {}"}))
    finally:
        import analyzers.framework as fw
        fw.AsyncAnthropic = orig
    assert len(fake.messages.calls) == 1
    system = fake.messages.calls[0]["system"]
    assert system.startswith("你是一名资深 HarmonyOS ArkTS 代码审查专家")
    assert "JSON 数组" in system, "system_prompt 应要求 JSON 数组输出"
    assert "category" in system, "system_prompt 应声明 category 特有字段"
    assert result["content"][0]["text"] == payload
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


def test_tool_input_schema_optional_vs_required():
    """断言 4 个分析工具的 input_schema 把可选参数暴露为可选、必填参数暴露为必填。
    透传 JSON schema dict（含 type/properties/required）时 SDK 直接透传，
    不会把所有字段塞进 required。
    """
    # analyze_runtime_logs: logs 必填，scope 可选
    schema = tools.analyze_runtime_logs_tool.input_schema
    assert schema["required"] == ["logs"], schema["required"]
    assert "scope" in schema["properties"]
    assert "scope" not in schema["required"]

    # suggest_performance_fixes: scope 必填，symptom 可选
    schema = tools.suggest_performance_fixes_tool.input_schema
    assert schema["required"] == ["scope"], schema["required"]
    assert "symptom" in schema["properties"]
    assert "symptom" not in schema["required"]

    # check_api_usage: scope 必填，focus_apis 可选
    schema = tools.check_api_usage_tool.input_schema
    assert schema["required"] == ["scope"], schema["required"]
    assert "focus_apis" in schema["properties"]
    assert "focus_apis" not in schema["required"]

    # locate_bug: scope + symptom 都必填
    schema = tools.locate_bug_tool.input_schema
    assert schema["required"] == ["scope", "symptom"], schema["required"]
    assert "symptom" in schema["properties"]

    print("[OK] test_tool_input_schema_optional_vs_required")


def test_format_files_encodes_findings_as_marker_json():
    """_format_files 输出含 %%FINDINGS_JSON%% marker 包裹的 JSON 数组，可被 parse_findings 解析。"""
    from analyzers.findings import parse_findings

    result = {
        "files": [{"path": "x/A.ets", "content": "export struct A {}"}],
        "error": "",
        "findings": [
            {"severity": "高", "file": "x/A.ets", "summary": "问题A", "fix": "改法A"},
        ],
    }
    text = tools._format_files(result)
    # marker 段存在
    assert "%%FINDINGS_JSON%%" in text
    assert "%%END_FINDINGS%%" in text
    # 从 marker 段提取的 JSON 能被 parse_findings 解析
    import json as _json
    import re as _re
    m = _re.search(r"%%FINDINGS_JSON%%\n(.*?)\n%%END_FINDINGS%%", text, _re.DOTALL)
    assert m is not None
    parsed = parse_findings(m.group(1))
    assert parsed is not None
    assert len(parsed) == 1
    assert parsed[0]["severity"] == "高"
    assert parsed[0]["summary"] == "问题A"
    print("[OK] test_format_files_encodes_findings_as_marker_json")


def test_format_files_no_findings_no_marker():
    """无 findings 时输出不含 marker。"""
    result = {
        "files": [{"path": "x/A.ets", "content": "struct A {}"}],
        "error": "",
        "findings": [],
    }
    text = tools._format_files(result)
    assert "%%FINDINGS_JSON%%" not in text
    print("[OK] test_format_files_no_findings_no_marker")


def main():
    test_review_returns_text_and_uses_framework()
    test_review_failure_returns_friendly_text()
    test_tool_input_schema_optional_vs_required()
    test_format_files_encodes_findings_as_marker_json()
    test_format_files_no_findings_no_marker()
    print("\n全部通过。")


if __name__ == "__main__":
    main()

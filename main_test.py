"""main.py 提取函数单测。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from main import _extract_tool_result_text, _raw_tool_result_text


def _block(text: str, is_error: bool = False):
    """构造一个最小 ToolResultBlock 桩。"""
    return SimpleNamespace(
        tool_use_id="t1",
        content=[{"type": "text", "text": text}],
        is_error=is_error,
    )


def test_raw_extracts_text_asis():
    b = _block("纯文本")
    assert _raw_tool_result_text(b) == "纯文本"
    print("[OK] test_raw_extracts_text_asis")


def test_extract_formats_findings_json():
    json_text = '[{"severity":"高","location":"a.ets:1","summary":"问题","fix":"改法"}]'
    b = _block(json_text)
    out = _extract_tool_result_text(b)
    assert "[高] a.ets:1" in out
    assert "问题" in out
    assert "改法" in out
    print("[OK] test_extract_formats_findings_json")


def test_extract_falls_back_for_non_json():
    b = _block("纯文本分析报告")
    assert _extract_tool_result_text(b) == "纯文本分析报告"
    print("[OK] test_extract_falls_back_for_non_json")


def test_extract_falls_back_for_files_json():
    # {files:...} 是对象不是数组，parse_findings 返回 None，原样返回
    b = _block('{"files":[{"path":"a","content":"b"}]}')
    assert _extract_tool_result_text(b) == '{"files":[{"path":"a","content":"b"}]}'
    print("[OK] test_extract_falls_back_for_files_json")


def test_raw_handles_str_content():
    # content 也可能是纯 str（非 list）
    b = SimpleNamespace(tool_use_id="t1", content="纯 str 内容", is_error=False)
    assert _raw_tool_result_text(b) == "纯 str 内容"
    print("[OK] test_raw_handles_str_content")


def main():
    test_raw_extracts_text_asis()
    test_extract_formats_findings_json()
    test_extract_falls_back_for_non_json()
    test_extract_falls_back_for_files_json()
    test_raw_handles_str_content()
    print("\n全部通过。")


if __name__ == "__main__":
    main()

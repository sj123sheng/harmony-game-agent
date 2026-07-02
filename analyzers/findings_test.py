"""parse_findings 与 _format_findings_text 单测。"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from analyzers.findings import _format_findings_text, parse_findings


def test_parses_valid_json_array():
    text = '[{"severity":"高","location":"a.ets:1","summary":"s","fix":"f"}]'
    out = parse_findings(text)
    assert out == [{"severity": "高", "location": "a.ets:1", "summary": "s", "fix": "f"}]
    print("[OK] test_parses_valid_json_array")


def test_strips_code_fence():
    text = '```json\n[{"severity":"高","location":"a","summary":"s","fix":"f"}]\n```'
    out = parse_findings(text)
    assert out is not None and out[0]["location"] == "a"
    print("[OK] test_strips_code_fence")


def test_extracts_first_array_when_surrounded_by_text():
    text = '分析结果如下：\n[{"severity":"高","location":"a","summary":"s","fix":"f"}]\n以上。'
    out = parse_findings(text)
    assert out is not None and len(out) == 1
    print("[OK] test_extracts_first_array_when_surrounded_by_text")


def test_field_aliases_mapped():
    text = '[{"等级":"高","位置":"a","问题":"s","correct_usage":"f"}]'
    out = parse_findings(text)
    assert out is not None
    assert out[0]["severity"] == "高"
    assert out[0]["location"] == "a"
    assert out[0]["summary"] == "s"
    assert out[0]["fix"] == "f"
    print("[OK] test_field_aliases_mapped")


def test_preserves_extra_fields():
    text = '[{"severity":"高","location":"a","summary":"s","fix":"f","confidence":0.8,"repro":"步骤"}]'
    out = parse_findings(text)
    assert out is not None
    assert out[0]["confidence"] == 0.8
    assert out[0]["repro"] == "步骤"
    print("[OK] test_preserves_extra_fields")


def test_returns_none_for_non_json():
    assert parse_findings("纯文本分析报告，无 JSON") is None
    print("[OK] test_returns_none_for_non_json")


def test_returns_none_for_json_object_not_array():
    assert parse_findings('{"files":[]}') is None
    print("[OK] test_returns_none_for_json_object_not_array")


def test_returns_none_for_array_with_non_dict():
    assert parse_findings('["str", 1]') is None
    print("[OK] test_returns_none_for_array_with_non_dict")


def test_empty_array_returns_empty_list_not_none():
    assert parse_findings("[]") == []
    print("[OK] test_empty_array_returns_empty_list_not_none")


def test_format_findings_text_renders_table():
    findings = [
        {"severity": "高", "location": "a.ets:1", "summary": "问题A", "fix": "改法A"},
        {"severity": "低", "location": "b.ets:2", "summary": "问题B", "fix": "改法B", "confidence": 0.3},
    ]
    text = _format_findings_text(findings)
    assert "[高] a.ets:1" in text
    assert "问题A" in text
    assert "改法A" in text
    assert "confidence: 0.3" in text
    # 高 排在 低 前
    assert text.index("[高]") < text.index("[低]")
    print("[OK] test_format_findings_text_renders_table")


def test_format_empty_findings():
    assert _format_findings_text([]) == "✓ 无发现"
    print("[OK] test_format_empty_findings")


def main():
    test_parses_valid_json_array()
    test_strips_code_fence()
    test_extracts_first_array_when_surrounded_by_text()
    test_field_aliases_mapped()
    test_preserves_extra_fields()
    test_returns_none_for_non_json()
    test_returns_none_for_json_object_not_array()
    test_returns_none_for_array_with_non_dict()
    test_empty_array_returns_empty_list_not_none()
    test_format_findings_text_renders_table()
    test_format_empty_findings()
    print("\n全部通过。")


if __name__ == "__main__":
    main()

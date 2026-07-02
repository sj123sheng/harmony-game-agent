"""分析结果 JSON 解析与格式化。供 main.py 与 server.py 共用。

4 个 analyzer 的 system_prompt 要求输出 JSON 数组，本模块负责把 LLM 文本
解析为 list[dict]，并兜底 code fence / 前后解释文字 / 字段名漂移。
解析失败返回 None，调用方回退纯文本展示。
"""

import json
import re
from typing import Optional

from generators.framework import _strip_code_fences

_JSON_ARRAY_RE = re.compile(r"\[.*\]", re.DOTALL)

# 核心字段的常见别名（不穷举，只覆盖 LLM 最可能的漂移）
_FIELD_ALIASES = {
    "severity": ("等级", "level"),
    "location": ("loc", "position", "位置"),
    "summary": ("问题", "issue"),
    "fix": ("fix_suggestion", "修复", "correct_usage", "正确用法"),
}

# 所有别名集合，用于 _normalize 时识别"已是别名"避免重复保留
_ALL_ALIASES = {a for al in _FIELD_ALIASES.values() for a in al}


def _normalize_finding(raw: dict) -> dict:
    """把字段名漂移映射到核心字段，特有字段原样保留。"""
    out = {}
    for canonical, aliases in _FIELD_ALIASES.items():
        if canonical in raw:
            out[canonical] = raw[canonical]
            continue
        for a in aliases:
            if a in raw:
                out[canonical] = raw[a]
                break
    # 特有字段（repro/confidence/root_cause/reference/reasoning 等）原样保留
    for k, v in raw.items():
        if k not in out and k not in _ALL_ALIASES:
            out[k] = v
    return out


def parse_findings(text: str) -> Optional[list[dict]]:
    """解析 LLM 输出为 findings list。失败返回 None。"""
    if not text:
        return None
    try:
        stripped = _strip_code_fences(text.strip())
        # 纯 JSON 对象（非数组）直接拒绝，避免正则误取其内部数组字段
        if stripped.startswith("{"):
            return None
        m = _JSON_ARRAY_RE.search(stripped)
        if not m:
            return None
        data = json.loads(m.group(0))
        if not isinstance(data, list):
            return None
        if not all(isinstance(x, dict) for x in data):
            return None
        return [_normalize_finding(f) for f in data]
    except (json.JSONDecodeError, AttributeError, TypeError):
        return None


def _format_findings_text(findings: list[dict]) -> str:
    """REPL 用：findings → 可读表格文本。空列表返回'无发现'。"""
    if not findings:
        return "✓ 无发现"
    order = {"高": 0, "中": 1, "低": 2}
    lines = []
    for f in sorted(findings, key=lambda x: order.get(str(x.get("severity", "")), 3)):
        sev = f.get("severity", "?")
        loc = f.get("location", "?")
        lines.append(f"[{sev}] {loc}")
        if f.get("summary"):
            lines.append(f"  问题: {f['summary']}")
        if f.get("fix"):
            lines.append(f"  建议: {f['fix']}")
        for k, v in f.items():
            if k in {"severity", "location", "summary", "fix"}:
                continue
            lines.append(f"  {k}: {v}")
        lines.append("")
    return "\n".join(lines).rstrip()

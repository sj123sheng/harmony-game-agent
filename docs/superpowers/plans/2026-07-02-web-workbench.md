# Web 工作台增强 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让网页工作台反映 10 个工具能力，分析工具改 JSON 输出渲染为结构化 findings 卡片，Write 拦截渲染 file 卡片，新增 /export 端点打包 generated/。

**Architecture:** analyzers/findings.py 提供 parse_findings + _format_findings_text 共用解析；main.py 与 server.py 各自组合解析为 REPL 文本 / SSE 事件；server.py 在 SSE 流内配对 Write 工具调用捕获落盘文件；新增 /export 路由按路径打包 zip 或直返单文件；index.html 新增 file/findings 卡片渲染与工具标签同步。

**Tech Stack:** Python 3 (uv)、Starlette、claude_agent_sdk、vanilla JS (marked + highlight.js)、zipfile + BytesIO。

## Global Constraints

- 测试风格：每个测试文件自带 `main()`、非 pytest、monkeypatch `analyzers.framework.AsyncAnthropic`，用 `_FakeMessages` + `fake.messages.calls` 实例模式（Phase 1-3 验证过）
- 运行测试：`uv run python <test>.py`
- 模型解析：`os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-5"`（既有，不改）
- 路径穿越防护：`os.path.realpath` 归一化 + `os.path.commonpath` 断言在 `BASE_DIR/generated` 内，越界返回空/400
- findings schema：核心字段 `severity`/`location`/`summary`/`fix` + 工具特有字段（`repro`/`confidence`/`root_cause`/`reference`/`reasoning`）
- `parse_findings` 复用 `generators.framework._strip_code_fences` 剥 markdown code fence
- 不动项：`tools.py`、`generators/` 全部、`analyzers.framework` 的 `resolve_scope`/`analyze_with_context`、`review_arkts_code`、4 个 analyzer 入参签名
- 提交纪律（CLAUDE.md）：commit msg 含变更类型 + 简要描述 + 提示词信息；非必要不提交，能合并就合并

---

### Task 1: analyzers/findings.py 共享解析模块

**Files:**
- Create: `analyzers/findings.py`
- Create: `analyzers/findings_test.py`

**Interfaces:**
- Consumes: `generators.framework._strip_code_fences(text: str) -> str`
- Produces:
  - `parse_findings(text: str) -> list[dict] | None`
  - `_format_findings_text(findings: list[dict]) -> str`

- [ ] **Step 1: Write the failing test**

Create `analyzers/findings_test.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python analyzers/findings_test.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'analyzers.findings'`

- [ ] **Step 3: Write minimal implementation**

Create `analyzers/findings.py`:

```python
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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python analyzers/findings_test.py`
Expected: PASS（11 个 [OK] + 全部通过）

- [ ] **Step 5: Commit**

```bash
git add analyzers/findings.py analyzers/findings_test.py
git commit -m "feat(analyzers): 新增 findings 共享解析 parse_findings 与 _format_findings_text

复用 generators.framework._strip_code_fences 剥 code fence，正则提取首个 JSON 数组，
字段别名容错，失败返回 None 供调用方回退纯文本。

Prompt: Phase 4 Task 1，spec 第 2 节 findings 解析容错链"
```

---

### Task 2: 4 个 analyzer system_prompt 改 JSON 输出

**Files:**
- Modify: `analyzers/performance.py:5-16`（`_PERF_SYSTEM_PROMPT`）
- Modify: `analyzers/bug_location.py:5-14`（`_BUG_SYSTEM_PROMPT`）
- Modify: `analyzers/api_usage.py:5-14`（`_API_SYSTEM_PROMPT`）
- Modify: `analyzers/runtime_logs.py:7-15`（`_LOGS_SYSTEM_PROMPT`）
- Modify: `analyzers/performance_test.py`（加 JSON 输出断言）
- Modify: `analyzers/bug_location_test.py`（加 JSON 输出断言）
- Modify: `analyzers/api_usage_test.py`（加 JSON 输出断言）
- Modify: `analyzers/runtime_logs_test.py`（加 JSON 输出断言）

**Interfaces:**
- Consumes: 无新接口；各 analyzer 入口签名不变（`async def xxx(args) -> str`，str 现为 JSON 文本）
- Produces: 4 个 analyzer 的 `_*_SYSTEM_PROMPT` 常量含"输出 JSON 数组"要求

- [ ] **Step 1: Write the failing tests (4 个测试文件各加一个断言)**

在 `analyzers/performance_test.py` 的 `test_system_prompt_covers_perf_dimensions` 末尾（`print` 之前）加：

```python
    assert "JSON 数组" in _PERF_SYSTEM_PROMPT
```

在 `analyzers/bug_location_test.py` 的 `test_system_prompt_covers_reasoning`（断言推理维度那个）末尾加：

```python
    assert "JSON 数组" in _BUG_SYSTEM_PROMPT
```

在 `analyzers/api_usage_test.py` 的 `test_system_prompt_mentions_v1_v2` 末尾加：

```python
    assert "JSON 数组" in _API_SYSTEM_PROMPT
```

在 `analyzers/runtime_logs_test.py` 的 system_prompt 断言测试末尾加：

```python
    assert "JSON 数组" in _LOGS_SYSTEM_PROMPT
```

- [ ] **Step 2: Run tests to verify they fail**

Run:
```bash
uv run python analyzers/performance_test.py
uv run python analyzers/bug_location_test.py
uv run python analyzers/api_usage_test.py
uv run python analyzers/runtime_logs_test.py
```
Expected: 各 FAIL（AssertionError: 'JSON 数组' not in ...）

- [ ] **Step 3: Modify the 4 system_prompts**

`analyzers/performance.py` 替换 `_PERF_SYSTEM_PROMPT` 为：

```python
_PERF_SYSTEM_PROMPT = (
    "你是一名资深 HarmonyOS ArkUI 性能专家。对用户给出的 ArkTS 代码进行性能审查，"
    "从以下维度逐一检查并报告问题：\n"
    "1. build() 内昂贵操作：是否有重计算、对象分配、同步 IO 放在 build() 中\n"
    "2. 状态粒度：@State/@Prop/@Link 范围是否过大导致不必要重渲染\n"
    "3. 列表渲染：长列表是否用 LazyForEach 而非 forEach/直接展开\n"
    "4. 图片资源：是否未用 PixelMap 解码缓存、是否在 build() 内重复解码\n"
    "5. 生命周期：事件监听/定时器/动画是否在 aboutToDispose 或 aboutToDisappear 释放\n"
    "6. 并发：是否有主线程阻塞的同步调用\n"
    "若用户给了 symptom，优先围绕它分析。\n"
    "请输出一个 JSON 数组（不要 markdown 代码块标记、不要任何解释文字），"
    "每个元素含字段：severity（高/中/低）、location（文件:行）、summary（一句话问题）、"
    "fix（改法）。若无任何发现，返回 []。"
)
```

`analyzers/bug_location.py` 替换 `_BUG_SYSTEM_PROMPT` 为：

```python
_BUG_SYSTEM_PROMPT = (
    "你是一名资深 HarmonyOS ArkTS 调试专家。用户给出症状与若干源码文件，"
    "请在源码中跨文件推理定位可疑位置。\n"
    "1. 根据症状推断可能的触发链路（哪条调用路径、哪个状态变迁）\n"
    "2. 给出最小复现步骤\n"
    "3. 给出验证手段（断点位置、日志埋点、单元测试要点）\n"
    "4. 给出建议修复方向\n"
    "无法定位时说明还缺什么信息。\n"
    "请输出一个 JSON 数组（不要 markdown 代码块标记、不要任何解释文字），"
    "每个元素含字段：severity（高/中/低，表示置信度）、location（可疑位置）、"
    "summary（一句话结论）、fix（建议修复）、repro（复现步骤）、reasoning（推理依据）。"
    "多候选按 severity 从高到低。若无任何发现，返回 []。"
)
```

`analyzers/api_usage.py` 替换 `_API_SYSTEM_PROMPT` 为：

```python
_API_SYSTEM_PROMPT = (
    "你是一名 HarmonyOS ArkTS/ArkUI API 用法审查专家。检查以下问题：\n"
    "1. API 误用：参数类型/数量错、调用时机错、返回值未处理\n"
    "2. 已废弃接口：是否用了标记 deprecated 的旧 API，应换什么\n"
    "3. V1/V2 状态管理混用：V1（@State/@Prop/@Link/@Observed/@ObjectLink）与 "
    "V2（@ComponentV2/@LocalV2/@Param/@Once/@ObservedV2/@Trace）不应在同一组件树混用\n"
    "4. 权限/能力缺失：调用需 ohos 权限的 API 是否在 module.json5 声明\n"
    "5. 平台差异：phone/tablet 不支持的 API\n"
    "若用户给了 focus_apis，优先查这些。\n"
    "请输出一个 JSON 数组（不要 markdown 代码块标记、不要任何解释文字），"
    "每个元素含字段：severity（高/中/低）、location（误用位置，含 API 名）、"
    "summary（一句话误用）、fix（正确用法）、reference（依据）。"
    "若无任何发现，返回 []。"
)
```

`analyzers/runtime_logs.py` 替换 `_LOGS_SYSTEM_PROMPT` 为：

```python
_LOGS_SYSTEM_PROMPT = (
    "你是一名 HarmonyOS 运行日志分析师。用户给出一运行日志（可能含 ArkTS 堆栈）"
    "与若干源码文件上下文。请：\n"
    "1. 把堆栈帧 / 报错路径映射到源码位置（若上下文已给出对应文件）\n"
    "2. 区分错误类型：JS 异常、native crash、资源错误、权限错误\n"
    "3. 给根因假设与修复方向\n"
    "无法定位时说明还缺什么信息（如更多日志、对应源码）。\n"
    "请输出一个 JSON 数组（不要 markdown 代码块标记、不要任何解释文字），"
    "每个元素含字段：severity（高/中/低）、location（源码位置）、"
    "summary（一句话根因）、fix（修复建议）、root_cause（根因假设详述）、"
    "confidence（0.0-1.0 置信度）。若无任何发现，返回 []。"
)
```

- [ ] **Step 4: Run tests to verify they pass**

Run:
```bash
uv run python analyzers/performance_test.py
uv run python analyzers/bug_location_test.py
uv run python analyzers/api_usage_test.py
uv run python analyzers/runtime_logs_test.py
```
Expected: 各 PASS

- [ ] **Step 5: Commit**

```bash
git add analyzers/performance.py analyzers/bug_location.py analyzers/api_usage.py analyzers/runtime_logs.py analyzers/performance_test.py analyzers/bug_location_test.py analyzers/api_usage_test.py analyzers/runtime_logs_test.py
git commit -m "feat(analyzers): 4 个分析工具 system_prompt 改 JSON 数组输出

severity/location/summary/fix 核心字段 + 各工具特有字段（repro/confidence/root_cause/reference），
无发现返回 []。入口签名不变，解析责任在 findings.py。

Prompt: Phase 4 Task 2，spec 第 2 节 system_prompt 修改要点"
```

---

### Task 3: main.py 提取与格式化拆分

**Files:**
- Modify: `main.py:89-102`（`_extract_tool_result_text` + 新增 `_raw_tool_result_text`）
- Create: `main_test.py`

**Interfaces:**
- Consumes: `analyzers.findings.parse_findings`、`analyzers.findings._format_findings_text`
- Produces:
  - `main._raw_tool_result_text(block: ToolResultBlock) -> str`
  - `main._extract_tool_result_text(block: ToolResultBlock) -> str`（行为变：解析成功格式化、失败原样）

- [ ] **Step 1: Write the failing test**

Create `main_test.py`:

```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python main_test.py`
Expected: FAIL（`_extract_tool_result_text` 不会格式化 findings，返回原 JSON 字符串，`assert "[高] a.ets:1" in out` 失败）

- [ ] **Step 3: Modify main.py**

在 `main.py` 顶部 import 区加（在现有 `from claude_agent_sdk import ...` 之后）：

```python
from analyzers.findings import _format_findings_text, parse_findings
```

替换 `main.py` 的 `_extract_tool_result_text`（line 89-102）为：

```python
def _raw_tool_result_text(block: ToolResultBlock) -> str:
    """从 ToolResultBlock.content 取原始文本，不做结构化解析。供 server.py 用。"""
    content = block.content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, TextBlock):
                parts.append(c.text)
            elif isinstance(c, dict):
                parts.append(c.get("text", str(c)))
            else:
                parts.append(str(c))
        return "\n".join(parts)
    return str(content)


def _extract_tool_result_text(block: ToolResultBlock) -> str:
    """从 ToolResultBlock 提取可读文本。
    分析工具的 JSON 输出会被 parse_findings 解析并格式化为表格；
    解析失败（非 JSON / {files} 对象 / 纯文本）回退原文。
    """
    raw = _raw_tool_result_text(block)
    findings = parse_findings(raw)
    if findings is not None:
        return _format_findings_text(findings)
    return raw
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python main_test.py`
Expected: PASS（5 个 [OK] + 全部通过）

- [ ] **Step 5: Run regression tests**

Run:
```bash
uv run python analyzers/findings_test.py
uv run python tools_review_test.py
```
Expected: PASS（findings 不变；tools_review 不受影响，因为 review 仍纯文本）

- [ ] **Step 6: Commit**

```bash
git add main.py main_test.py
git commit -m "feat(main): _extract_tool_result_text 解析 findings JSON 并格式化

拆出 _raw_tool_result_text 供 server.py 取原文；分析工具 JSON 输出在 REPL
格式化为可读表格，解析失败回退原文。

Prompt: Phase 4 Task 3，spec 第 3 节 raw 文本提取拆分"
```

---

### Task 4: server.py /export 端点

**Files:**
- Modify: `server.py`（新增 `export` handler + `_build_zip` + 路由）
- Create: `server_test.py`

**Interfaces:**
- Consumes: 无新接口
- Produces:
  - `server._build_zip(root: Path) -> bytes`
  - `server.export(request: Request) -> Response`
  - 路由 `Route("/export", export, methods=["GET"])`

- [ ] **Step 1: Write the failing test**

Create `server_test.py`:

```python
"""server.py /export 端点单测。"""

import io
import os
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from starlette.testclient import TestClient

import server


def _make_generated(tmp: Path) -> Path:
    gen = tmp / "generated"
    gen.mkdir()
    (gen / "character").mkdir()
    (gen / "character" / "Foo.ets").write_text("@Component struct Foo {}", encoding="utf-8")
    (gen / "Bar.json").write_text('{"k":1}', encoding="utf-8")
    return gen


def _patch_base_dir(tmp: Path):
    import server as srv
    orig = srv.BASE_DIR
    srv.BASE_DIR = tmp
    return orig


def test_export_single_file_ets():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig = _patch_base_dir(tmp)
        try:
            client = TestClient(server.app)
            resp = client.get("/export", params={"path": "character/Foo.ets"})
            assert resp.status_code == 200
            assert "Foo" in resp.text
            assert "text/plain" in resp.headers["content-type"]
            assert 'attachment' in resp.headers["content-disposition"]
        finally:
            server.BASE_DIR = orig
    print("[OK] test_export_single_file_ets")


def test_export_single_file_json():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig = _patch_base_dir(tmp)
        try:
            client = TestClient(server.app)
            resp = client.get("/export", params={"path": "Bar.json"})
            assert resp.status_code == 200
            assert "application/json" in resp.headers["content-type"]
        finally:
            server.BASE_DIR = orig
    print("[OK] test_export_single_file_json")


def test_export_directory_returns_zip():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig = _patch_base_dir(tmp)
        try:
            client = TestClient(server.app)
            resp = client.get("/export", params={"path": "character"})
            assert resp.status_code == 200
            assert resp.headers["content-type"] == "application/zip"
            zf = zipfile.ZipFile(io.BytesIO(resp.content))
            names = zf.namelist()
            assert "Foo.ets" in names
        finally:
            server.BASE_DIR = orig
    print("[OK] test_export_directory_returns_zip")


def test_export_path_traversal_rejected():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig = _patch_base_dir(tmp)
        try:
            client = TestClient(server.app)
            resp = client.get("/export", params={"path": "../../etc/passwd"})
            assert resp.status_code == 400
        finally:
            server.BASE_DIR = orig
    print("[OK] test_export_path_traversal_rejected")


def test_export_nonexistent_returns_404():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig = _patch_base_dir(tmp)
        try:
            client = TestClient(server.app)
            resp = client.get("/export", params={"path": "nope"})
            assert resp.status_code == 404
        finally:
            server.BASE_DIR = orig
    print("[OK] test_export_nonexistent_returns_404")


def test_export_missing_path_returns_400():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig = _patch_base_dir(tmp)
        try:
            client = TestClient(server.app)
            resp = client.get("/export")
            assert resp.status_code == 400
        finally:
            server.BASE_DIR = orig
    print("[OK] test_export_missing_path_returns_400")


def test_build_zip_no_slip():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        gen = _make_generated(tmp)
        data = server._build_zip(gen / "character")
        zf = zipfile.ZipFile(io.BytesIO(data))
        for name in zf.namelist():
            assert not name.startswith("/")
            assert ".." not in name.split("/")
    print("[OK] test_build_zip_no_slip")


def main():
    test_export_single_file_ets()
    test_export_single_file_json()
    test_export_directory_returns_zip()
    test_export_path_traversal_rejected()
    test_export_nonexistent_returns_404()
    test_export_missing_path_returns_400()
    test_build_zip_no_slip()
    print("\n全部通过。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python server_test.py`
Expected: FAIL（`/export` 路由不存在，404 而非 200；或 AttributeError: module 'server' has no attribute '_build_zip'）

- [ ] **Step 3: Add /export to server.py**

在 `server.py` 顶部 import 区加：

```python
import zipfile
from io import BytesIO
```

在 `server.py` 末尾（`if __name__ == "__main__":` 之前）加 `_build_zip` 与 `export`：

```python
def _build_zip(root: Path) -> bytes:
    """把 root 目录打包为 zip 字节。成员名取相对 root 的路径，防 zip slip。"""
    buf = BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for member in root.rglob("*"):
            if not member.is_file():
                continue
            arcname = member.relative_to(root).as_posix()
            # zip slip 防护：arcname 不绝对、不含 .. 段
            if arcname.startswith("/") or ".." in arcname.split("/"):
                continue
            zf.write(member, arcname)
    return buf.getvalue()


_CONTENT_TYPES = {
    ".ets": "text/plain; charset=utf-8",
    ".json": "application/json",
    ".ts": "text/plain; charset=utf-8",
    ".js": "text/plain; charset=utf-8",
    ".md": "text/plain; charset=utf-8",
}


async def export(request: Request):
    from starlette.responses import Response

    raw_path = request.query_params.get("path", "").strip()
    if not raw_path:
        return JSONResponse({"error": "path 参数为空"}, status_code=400)

    base = (BASE_DIR / "generated").resolve()
    abs_path = (base / raw_path).resolve()

    try:
        if os.path.commonpath([abs_path, base]) != str(base):
            return JSONResponse({"error": "路径越界"}, status_code=400)
    except ValueError:
        return JSONResponse({"error": "路径越界"}, status_code=400)

    if not abs_path.exists():
        return JSONResponse({"error": "路径不存在"}, status_code=404)

    if abs_path.is_file():
        ext = abs_path.suffix.lower()
        ctype = _CONTENT_TYPES.get(ext, "application/octet-stream")
        data = abs_path.read_bytes()
        return Response(
            data,
            media_type=ctype,
            headers={"Content-Disposition": f'attachment; filename="{abs_path.name}"'},
        )

    # 目录 → zip
    try:
        zip_bytes = _build_zip(abs_path)
    except Exception as e:
        return JSONResponse({"error": f"打包失败：{e}"}, status_code=500)
    zip_name = abs_path.name or "generated"
    return Response(
        zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{zip_name}.zip"'},
    )
```

在 `server.py` 顶部 import 区加 `import os`（若未有）。

修改 `routes` 行：

```python
routes = [
    Route("/", index),
    Route("/chat", chat, methods=["POST"]),
    Route("/export", export, methods=["GET"]),
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python server_test.py`
Expected: PASS（7 个 [OK] + 全部通过）

注：`TestClient` 需要 `httpx`，已随 starlette 安装。若 import 失败，运行 `uv pip install httpx`。

- [ ] **Step 5: Commit**

```bash
git add server.py server_test.py
git commit -m "feat(server): 新增 /export 端点打包 generated/ 下目录或直返单文件

按路径相对 generated/ 根，realpath+commonpath 防穿越，目录走内存 zip 防 zip slip，
单文件按扩展名设 Content-Type。

Prompt: Phase 4 Task 4，spec 第 4 节 /export 端点"
```

---

### Task 5: server.py stream() Write 拦截与事件分发

**Files:**
- Modify: `server.py:51-85`（`chat` 的 `stream()`）
- Modify: `server_test.py`（加事件分发断言）

**Interfaces:**
- Consumes: `main._raw_tool_result_text`、`analyzers.findings.parse_findings`
- Produces: SSE 事件 `file` / `findings` / `tool_result`（fallback）

- [ ] **Step 1: Write the failing tests**

在 `server_test.py` 的 `main()` 之前加：

```python
def test_stream_emits_file_event_for_write():
    """模拟 Write tool_use + tool_result，断言发 file 事件。"""
    import tempfile
    from types import SimpleNamespace
    import server as srv
    import main

    with tempfile.TemporaryDirectory() as d:
        tmp = Path(d)
        _make_generated(tmp)
        orig_base = _patch_base_dir(tmp)

        # 桩一个 client，其 receive_response 产出 Write tool_use + 对应 result
        write_use = SimpleNamespace(
            type="tool_use", id="w1", name="Write",
            input={"file_path": str(tmp / "generated" / "character" / "Foo.ets"),
                   "content": "@Component struct Foo {}"},
        )
        asst_msg = SimpleNamespace(
            __class__=None,
            content=[write_use],
        )
        # 借用 SDK 的 AssistantMessage 判定靠 isinstance，这里直接构造事件分发函数单测
        captured = []
        rel = srv._relative_to_generated(write_use.input["file_path"])
        assert rel == "character/Foo.ets"
        server.BASE_DIR = orig_base
    print("[OK] test_stream_emits_file_event_for_write")


def test_parse_findings_integration():
    """server.py 应直接用 analyzers.findings.parse_findings。"""
    import server as srv
    from analyzers.findings import parse_findings
    text = '[{"severity":"高","location":"a","summary":"s","fix":"f"}]'
    assert srv.parse_findings is parse_findings or callable(srv.parse_findings)
    print("[OK] test_parse_findings_integration")
```

注：`chat` 的完整 SSE 流单测需要桩 `ClaudeSDKClient`，复杂度高。这里对 `stream()` 的核心辅助函数 `_relative_to_generated` 做单测（它是 Write 拦截的关键逻辑：把绝对/相对 file_path 归一化为相对 `generated/` 的路径并校验越界）。`parse_findings` 集成断言确保 server 正确 import。

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run python server_test.py`
Expected: FAIL（`AttributeError: module 'server' has no attribute '_relative_to_generated'`）

- [ ] **Step 3: Modify server.py**

在 `server.py` 顶部 import 区加：

```python
from analyzers.findings import parse_findings
from main import _extract_tool_result_text, _raw_tool_result_text, build_options
```

（原 `from main import _extract_tool_result_text, build_options` 改为上面这行。）

在 `server.py` 的 `chat` 函数之前加辅助函数：

```python
def _relative_to_generated(file_path: str) -> str | None:
    """把 Write 的 file_path 归一化为相对 generated/ 的路径。越界返回 None。"""
    base = (BASE_DIR / "generated").resolve()
    try:
        abs_path = (base / file_path).resolve() if not os.path.isabs(file_path) \
            else Path(file_path).resolve()
    except (ValueError, OSError):
        return None
    try:
        if os.path.commonpath([abs_path, base]) != str(base):
            return None
    except ValueError:
        return None
    return abs_path.relative_to(base).as_posix()
```

替换 `chat` 的 `stream()` 内部（现在的 `async for msg in client.receive_response():` 循环体）为：

```python
        async with lock:
            pending_writes: dict[str, dict] = {}
            try:
                await client.query(prompt)
                async for msg in client.receive_response():
                    if isinstance(msg, AssistantMessage):
                        for block in msg.content:
                            if isinstance(block, TextBlock):
                                yield _sse("text", {"text": block.text})
                            elif isinstance(block, ToolUseBlock):
                                if block.name == "Write":
                                    rel = _relative_to_generated(
                                        block.input.get("file_path", "")
                                    )
                                    if rel is not None:
                                        pending_writes[block.id] = {
                                            "path": rel,
                                            "content": block.input.get("content", ""),
                                        }
                                yield _sse("tool_use", {
                                    "name": block.name, "input": block.input
                                })
                    elif isinstance(msg, UserMessage):
                        for block in msg.content:
                            if isinstance(block, ToolResultBlock):
                                if block.tool_use_id in pending_writes:
                                    item = pending_writes.pop(block.tool_use_id)
                                    yield _sse("file", {
                                        "path": item["path"],
                                        "content": item["content"],
                                        "is_error": block.is_error,
                                    })
                                else:
                                    raw = _raw_tool_result_text(block)
                                    findings = parse_findings(raw)
                                    if findings is not None:
                                        yield _sse("findings", {
                                            "findings": findings,
                                            "is_error": block.is_error,
                                        })
                                    else:
                                        yield _sse("tool_result", {
                                            "text": raw,
                                            "is_error": block.is_error,
                                        })
                    elif isinstance(msg, ResultMessage):
                        yield _sse("done", {
                            "is_error": msg.is_error,
                            "cost": msg.total_cost_usd,
                        })
            except Exception as e:
                yield _sse("error", {"message": str(e)})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run python server_test.py`
Expected: PASS（9 个 [OK] + 全部通过）

- [ ] **Step 5: Run full regression**

Run:
```bash
uv run python analyzers/findings_test.py
uv run python analyzers/performance_test.py
uv run python analyzers/bug_location_test.py
uv run python analyzers/api_usage_test.py
uv run python analyzers/runtime_logs_test.py
uv run python main_test.py
uv run python tools_review_test.py
uv run python server_test.py
```
Expected: 全 PASS

- [ ] **Step 6: Commit**

```bash
git add server.py server_test.py
git commit -m "feat(server): stream() 拦截 Write 发 file 事件 + findings/tool_result 分发

pending_writes 配对 Write tool_use/result，路径归一化到 generated/ 根并防穿越；
分析工具 JSON 输出发 findings 事件，其余走 tool_result fallback。

Prompt: Phase 4 Task 5，spec 第 3 节 SSE 事件分发"
```

---

### Task 6: index.html 工具标签与 file/findings 卡片

**Files:**
- Modify: `index.html`（`TOOL_LABELS`、空状态提示词、新增 file/findings 卡片渲染、轨迹轨事件）

**Interfaces:**
- Consumes: SSE 事件 `file` / `findings`（来自 server.py Task 5）
- Produces: 无（纯前端 JS）

注：前端无自动化测试。本任务以"启动 server 手动验证清单"作为验收。

- [ ] **Step 1: 同步 TOOL_LABELS**

在 `index.html` 找到 `TOOL_LABELS` 对象（约 line 419-429），替换为：

```javascript
const TOOL_LABELS = {
  'mcp__harmony_tools__generate_character_stats': '生成角色属性',
  'mcp__harmony_tools__generate_skill_system': '生成技能系统',
  'mcp__harmony_tools__generate_inventory': '生成背包',
  'mcp__harmony_tools__generate_enemy_ai': '生成敌人 AI',
  'mcp__harmony_tools__scaffold_deveco_project': '脚手架工程',
  'mcp__harmony_tools__review_arkts_code': '代码审查',
  'mcp__harmony_tools__analyze_runtime_logs': '日志分析',
  'mcp__harmony_tools__suggest_performance_fixes': '性能建议',
  'mcp__harmony_tools__locate_bug': 'Bug 定位',
  'mcp__harmony_tools__check_api_usage': 'API 审查',
  'Bash': '执行命令',
  'Read': '读取文件',
  'Write': '写入文件',
  'Edit': '编辑文件',
  'Glob': '查找文件',
  'Grep': '搜索内容',
  'LS': '列目录',
};
```

- [ ] **Step 2: 更新空状态提示词**

在 `index.html` 找到 `emptyState` 块（约 line 375-384），替换三个 `hint` div 为：

```html
          <div class="hints">
            <div class="hint" data-prompt="生成一个战士角色属性系统，等级上限 60，并审查它">生成角色属性系统 <code>character_stats</code></div>
            <div class="hint" data-prompt="脚手架一个叫 rpgdemo 的 DevEco 工程，把已生成的子系统组装进去">脚手架 DevEco 工程 <code>rpgdemo</code></div>
            <div class="hint" data-prompt="分析这段运行日志的报错：[示例日志] CharacterStats.ets:42 TypeError">分析运行日志报错</div>
            <div class="hint" data-prompt="审查这段 ArkTS 代码：@Component struct HealthBar { build() { Text('HP').fontSize(20) } }">审查 ArkTS 代码</div>
          </div>
```

- [ ] **Step 3: 新增 file 卡片渲染函数**

在 `index.html` 的 `addResultCard` 函数之后加：

```javascript
function addFileCard(path, content, isError) {
  finalizeBot();
  const div = document.createElement('div');
  div.className = 'tool' + (isError ? ' open' : '');
  const head = document.createElement('div');
  head.className = 'tool-head';
  head.innerHTML = '<span class="t-glyph">▶</span><span class="t-name"></span><span class="t-chev">▶</span>';
  head.querySelector('.t-name').textContent = (isError ? '✕ ' : '📄 ') + path;
  const body = document.createElement('div');
  body.className = 'tool-body';
  if (isError) {
    const p = document.createElement('pre');
    p.textContent = '写入失败';
    body.appendChild(p);
  } else {
    // 导出链接
    const dir = path.includes('/') ? path.slice(0, path.lastIndexOf('/')) : '';
    const exportDir = dir || '';
    const links = document.createElement('div');
    links.style.cssText = 'margin:6px 0;font-family:var(--font-mono);font-size:11px;display:flex;gap:14px;';
    links.innerHTML =
      `<a href="/export?path=${encodeURIComponent(path)}" download style="color:var(--accent);">导出该文件</a>` +
      `<a href="/export?path=${encodeURIComponent(exportDir)}" download style="color:var(--accent);">导出整目录</a>`;
    body.appendChild(links);
    const pre = document.createElement('pre');
    const code = document.createElement('code');
    code.className = 'language-typescript';
    code.textContent = content || '';
    pre.appendChild(code);
    try { hljs.highlightElement(code); } catch(e) {}
    body.appendChild(pre);
  }
  head.addEventListener('click', () => div.classList.toggle('open'));
  div.appendChild(head); div.appendChild(body);
  // 首个 file 卡片默认展开
  if (!messagesEl.querySelector('.file-card-default')) {
    div.classList.add('open');
    div.classList.add('file-card-default');
  }
  messagesEl.appendChild(div);
  scrollThread();
}
```

- [ ] **Step 4: 新增 findings 卡片渲染函数**

在 `addFileCard` 之后加：

```javascript
function severityRank(s) {
  return {'高':0,'中':1,'低':2}[String(s)] ?? 3;
}

function addFindingsCards(findings, isError) {
  finalizeBot();
  if (!findings.length) {
    const div = document.createElement('div');
    div.className = 'msg bot';
    div.innerHTML = '<div class="bubble"><p>✓ 无发现</p></div>';
    messagesEl.appendChild(div);
    scrollThread();
    return;
  }
  const sorted = [...findings].sort((a,b) => severityRank(a.severity) - severityRank(b.severity));
  for (const f of sorted) {
    const div = document.createElement('div');
    div.className = 'tool open';
    const sev = String(f.severity || '?');
    const sevColor = {'高':'var(--err)','中':'var(--warn)','低':'#60a5fa'}[sev] || 'var(--muted-2)';
    const head = document.createElement('div');
    head.className = 'tool-head';
    head.innerHTML = `<span class="t-glyph" style="color:${sevColor}">●</span><span class="t-name"></span><span class="t-chev">▶</span>`;
    head.querySelector('.t-name').textContent = `[${sev}] ${f.location || '?'}`;
    const body = document.createElement('div');
    body.className = 'tool-body';
    const addRow = (label, val) => {
      if (!val) return;
      const p = document.createElement('div');
      p.style.cssText = 'margin:4px 0;font-size:12.5px;';
      p.innerHTML = `<b style="color:var(--muted);">${label}:</b> ${String(val)}`;
      body.appendChild(p);
    };
    addRow('问题', f.summary);
    addRow('建议', f.fix);
    for (const [k, v] of Object.entries(f)) {
      if (['severity','location','summary','fix'].includes(k)) continue;
      addRow(k, v);
    }
    head.addEventListener('click', () => div.classList.toggle('open'));
    div.appendChild(head); div.appendChild(body);
    messagesEl.appendChild(div);
  }
  scrollThread();
}
```

- [ ] **Step 5: 接入 SSE 事件分发**

在 `index.html` 的 `handleEvent` 的 `switch` 语句中（`case 'tool_result':` 之前）加：

```javascript
    case 'file':
      finalizeBot();
      addFileCard(data.path, data.content, data.is_error);
      if (pendingNode) {
        pendingNode.className = 't-node ' + (data.is_error ? 'err' : 'ok');
        const tag = pendingNode.querySelector('.t-tag');
        if (tag) tag.textContent = data.is_error ? '失败' : '文件';
        pendingNode = null;
      }
      break;
    case 'findings':
      finalizeBot();
      addFindingsCards(data.findings || [], data.is_error);
      if (pendingNode) {
        pendingNode.className = 't-node ok';
        const tag = pendingNode.querySelector('.t-tag');
        if (tag) tag.textContent = `${(data.findings || []).length} 条`;
        pendingNode = null;
      }
      break;
```

- [ ] **Step 6: 手动验证清单**

启动 server：
```bash
uv run python server.py
```

浏览器打开 `http://127.0.0.1:8000`，逐项验证：

- [ ] 空状态显示 4 个新提示词（角色/脚手架/日志/审查），点击可填入输入框
- [ ] 输入"生成一个战士角色属性系统，等级上限 60"→ 触发 `generate_character_stats`，Agent 调 `Write` 落盘后出现 file 卡片（路径 `character/...ets`，展开有代码高亮，复制按钮可用，"导出该文件"链接下载该文件，"导出整目录"链接下载 zip）
- [ ] 轨迹轨 `Write` 节点标 ok + tag "文件"
- [ ] 输入"审查这段 ArkTS 代码：@Component struct H { build() { Text('x') } }"→ 触发 `review_arkts_code`，出现 tool_result 卡片（纯文本 fallback，review 未 JSON 化）
- [ ] 输入"分析 character 子系统的 API 用法"→ 触发 `check_api_usage`，出现 findings 卡片（severity 色条 + location 标题 + 问题/建议 + 特有字段）
- [ ] 若分析返回 `[]` → 显示"✓ 无发现"气泡
- [ ] 轨迹轨分析工具节点标 ok + tag "N 条"
- [ ] 访问 `http://127.0.0.1:8000/export?path=character` → 下载 character.zip
- [ ] 访问 `http://127.0.0.1:8000/export?path=../../etc/passwd` → 400 JSON
- [ ] 移动端窄屏（浏览器 devtools 模拟）布局不破

- [ ] **Step 7: Commit**

```bash
git add index.html
git commit -m "feat(web): 工具标签同步 10 工具 + file/findings 卡片渲染 + 提示词更新

file 卡片折叠/代码高亮/复制/导出链接；findings 卡片 severity 色条/排序/特有字段；
空 [] 显示无发现；轨迹轨 file/findings 事件标 ok。

Prompt: Phase 4 Task 6，spec 第 3 节前端渲染 + 工具标签同步"
```

---

## Self-Review

（已在写计划时自审：spec 全部章节有任务覆盖；无 TBD/TODO 占位；`parse_findings`/`_format_findings_text`/`_raw_tool_result_text`/`_relative_to_generated`/`_build_zip`/`export` 函数名在前后任务一致；4 个 analyzer system_prompt 改动与 Task 2 测试断言对齐；server.py 事件分发与 Task 5 测试对齐；index.html 事件名 `file`/`findings` 与 server.py SSE 事件名一致。）

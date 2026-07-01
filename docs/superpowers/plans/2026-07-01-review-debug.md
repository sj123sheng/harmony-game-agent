# 审查与调试增强实现计划（Phase 3）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 4 个分析工具（运行日志分析 / 性能瓶颈建议 / Bug 定位 / API 用法纠错）并把 `review_arkts_code` 重构进共享分析框架。

**Architecture:** 新建 `analyzers/` 包与 `generators/` 并列。`analyzers/framework.py` 提供两个通用原语：`resolve_scope`（scope 三形态 + 路径穿越防护）与 `analyze_with_context`（纯 XML 文件上下文 + 容量截断 + 失败 raise）。4 个工具各自声明 system_prompt + 调原语；日志工具自处理路径映射。`review_arkts_code` 用 A1 路径重构（入参不变，仅消除样板）。错误分层：框架 raise，`@tool` 包装层转友好文本。

**Tech Stack:** Python 3、anthropic SDK（AsyncAnthropic，读 ANTHROPIC_API_KEY/BASE_URL/MODEL 环境变量）、claude_agent_sdk（@tool / create_sdk_mcp_server）、HarmonyOS ArkTS/ArkUI 领域知识。

## Global Constraints

- 测试沿用 Phase 1/2 风格：自带 `main()`、非 pytest、`uv run python analyzers/<test>.py` 自跑，桩 LLM monkeypatch `analyzers.framework.AsyncAnthropic`
- `resolve_scope` 文件路径分支必须 `os.path.realpath` 归一化后断言在 `scan_dir` 之内，越界返回空清单
- `analyze_with_context` files 总字节软上限 80KB，超出按文件顺序截断并标 `[已截断]`
- `analyze_with_context` 失败 raise（不兜底）；`@tool` 包装层 try/except 转 `{"content":[{"type":"text","text":f"<工具名>失败：{e}"}]}`
- 四个分析工具返回纯文本（非 `{files}`），不写盘
- `review_arkts_code` 重构（A1）：入参 `{"code": str}` 与 5 维 checklist system_prompt 不变
- 模型：`os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-5"`
- Git 纪律：commit 前询问用户；commit msg 含变更类型 + 简要描述 + 详细描述 + `Prompt:` 行

参考 spec：[docs/superpowers/specs/2026-07-01-review-debug-design.md](../specs/2026-07-01-review-debug-design.md)

---

## File Structure

```
analyzers/
  __init__.py            ← 导出 analyze_with_context, resolve_scope, FileRef, 4 个工具入口
  framework.py           ← FileRef + resolve_scope + analyze_with_context
  framework_test.py      ← 框架单测
  runtime_logs.py        ← analyze_runtime_logs + 路径映射
  runtime_logs_test.py   ← 冒烟 + 路径提取测试
  performance.py         ← suggest_performance_fixes
  performance_test.py    ← 冒烟
  bug_location.py        ← locate_bug
  bug_location_test.py   ← 冒烟
  api_usage.py           ← check_api_usage
  api_usage_test.py      ← 冒烟
tools.py                 ← 修改：4 个新 @tool 包装 + review 重构
main.py                  ← 修改：allowed_tools + system_prompt + REPL 提示行
```

---

### Task 1: analyzers/framework.py 共享原语

**Files:**
- Create: `analyzers/framework.py`
- Create: `analyzers/__init__.py`
- Test: `analyzers/framework_test.py`

**Interfaces:**
- Produces: `FileRef(path:str, content:str)`、`resolve_scope(scope:str, scan_dir:str="./generated") -> list[FileRef]`、`analyze_with_context(system_prompt:str, user_input:str, files:list[FileRef], max_tokens:int=2048) -> str`
- Consumes: `anthropic.AsyncAnthropic`（读环境变量中转配置）

- [ ] **Step 1: 写 `analyzers/framework_test.py` 的失败测试**

```python
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
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python analyzers/framework_test.py`
Expected: FAIL with `ModuleNotFoundError: No module named 'analyzers.framework'`

- [ ] **Step 3: 写 `analyzers/framework.py` 实现**

```python
"""共享分析框架：文件上下文解析 + LLM 分析调用。

与 generators/ 并列——生成用 hybrid_generate，分析用 analyze_with_context。
framework 只提供通用原语，不掺领域逻辑（日志路径映射归 runtime_logs.py）。
"""

import os
from dataclasses import dataclass

from anthropic import AsyncAnthropic

# 与 generators/deveco_project._KNOWN_SUBSYSTEMS 一致
_KNOWN_SUBSYSTEMS = ("character", "skill", "inventory", "enemy")

# files 总字节软上限，超出按文件顺序截断
_FILES_BYTES_LIMIT = 80 * 1024


@dataclass
class FileRef:
    path: str        # 相对 scan_dir 的正斜杠路径，如 "character/CharacterStats.ets"
    content: str


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _scan_subsystem_dir(sub_dir: str, scan_dir: str) -> list[FileRef]:
    """扫描子系统目录下所有 .ets，path 相对 scan_dir 用正斜杠。"""
    if not os.path.isdir(sub_dir):
        return []
    refs: list[FileRef] = []
    for fname in sorted(os.listdir(sub_dir)):
        if not fname.endswith(".ets"):
            continue
        full = os.path.join(sub_dir, fname)
        rel = os.path.relpath(full, scan_dir).replace(os.sep, "/")
        refs.append(FileRef(path=rel, content=_read_text(full)))
    return refs


def resolve_scope(scope: str, scan_dir: str = "./generated") -> list[FileRef]:
    """scope 三形态（判定顺序写死，消歧）：
       1. 'all' → 全部已知子系统
       2. 在 _KNOWN_SUBSYSTEMS → 该子系统
       3. 否则当文件路径（相对 scan_dir，含工程内深层路径）
       找不到/越界返回空清单，不抛。
    """
    if scope == "all":
        refs: list[FileRef] = []
        for name in _KNOWN_SUBSYSTEMS:
            refs.extend(_scan_subsystem_dir(os.path.join(scan_dir, name), scan_dir))
        return refs
    if scope in _KNOWN_SUBSYSTEMS:
        return _scan_subsystem_dir(os.path.join(scan_dir, scope), scan_dir)
    # 文件路径分支：路径穿越防护
    real_scan = os.path.realpath(scan_dir)
    full = os.path.join(scan_dir, scope)
    real = os.path.realpath(full)
    try:
        common = os.path.commonpath([real, real_scan])
    except ValueError:
        # 跨盘符等异常
        return []
    if common != real_scan:
        return []  # 越界
    if not os.path.isfile(real):
        return []
    rel = os.path.relpath(real, real_scan).replace(os.sep, "/")
    return [FileRef(path=rel, content=_read_text(real))]


def _build_user_message(user_input: str, files: list[FileRef]) -> str:
    """组装纯 XML 文件上下文 + 用户输入。files 为空时省略 <files> 段。"""
    if not files:
        return user_input
    parts = ["<files>"]
    total = 0
    for f in files:
        chunk = f.content
        if total + len(chunk) > _FILES_BYTES_LIMIT:
            remaining = _FILES_BYTES_LIMIT - total
            if remaining > 0:
                parts.append(f'<file path="{f.path}">{chunk[:remaining]}\n[已截断]</file>')
            total = _FILES_BYTES_LIMIT
            break
        parts.append(f'<file path="{f.path}">{chunk}</file>')
        total += len(chunk)
    parts.append("</files>")
    parts.append("")
    parts.append(user_input)
    return "\n".join(parts)


async def analyze_with_context(
    system_prompt: str,
    user_input: str,
    files: list[FileRef],
    max_tokens: int = 2048,
) -> str:
    """组装文件上下文 + 用户输入喂给 AsyncAnthropic，返回分析文本。
    LLM 调用/网络失败时 raise（不兜底），由 @tool 包装层转友好文本。
    """
    client = AsyncAnthropic()
    model = os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-5"
    message = _build_user_message(user_input, files)
    resp = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": message}],
    )
    return "".join(getattr(b, "text", "") for b in resp.content)
```

- [ ] **Step 4: 写 `analyzers/__init__.py`**

```python
"""分析工具包：共享框架 + 4 个分析工具。"""

from analyzers.framework import FileRef, analyze_with_context, resolve_scope

__all__ = ["FileRef", "analyze_with_context", "resolve_scope"]
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run python analyzers/framework_test.py`
Expected: 全部 `[OK]` + `全部通过。`

- [ ] **Step 6: 提交（询问用户后）**

```bash
git add analyzers/__init__.py analyzers/framework.py analyzers/framework_test.py
git commit -m "feat(analyzers): 新增共享分析框架 resolve_scope 与 analyze_with_context"
```

---

### Task 2: 三个简单分析工具（performance / bug_location / api_usage）

**Files:**
- Create: `analyzers/performance.py` + `analyzers/performance_test.py`
- Create: `analyzers/bug_location.py` + `analyzers/bug_location_test.py`
- Create: `analyzers/api_usage.py` + `analyzers/api_usage_test.py`
- Modify: `analyzers/__init__.py`

**Interfaces:**
- Consumes: `resolve_scope`, `analyze_with_context` from Task 1
- Produces: `async def suggest_performance_fixes(args: dict) -> str`、`async def locate_bug(args: dict) -> str`、`async def check_api_usage(args: dict) -> str`（三个入口，供 Task 4 的 `@tool` 包装调用）

- [ ] **Step 1: 写 `analyzers/performance_test.py` 失败测试**

```python
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
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python analyzers/performance_test.py`
Expected: FAIL `ModuleNotFoundError: No module named 'analyzers.performance'`

- [ ] **Step 3: 写 `analyzers/performance.py`**

```python
"""性能瓶颈建议工具。"""

from analyzers.framework import FileRef, analyze_with_context, resolve_scope

_PERF_SYSTEM_PROMPT = (
    "你是一名资深 HarmonyOS ArkUI 性能专家。对用户给出的 ArkTS 代码进行性能审查，"
    "从以下维度逐一检查并报告问题：\n"
    "1. build() 内昂贵操作：是否有重计算、对象分配、同步 IO 放在 build() 中\n"
    "2. 状态粒度：@State/@Prop/@Link 范围是否过大导致不必要重渲染\n"
    "3. 列表渲染：长列表是否用 LazyForEach 而非 forEach/直接展开\n"
    "4. 图片资源：是否未用 PixelMap 解码缓存、是否在 build() 内重复解码\n"
    "5. 生命周期：事件监听/定时器/动画是否在 aboutToDispose 或 aboutToDisappear 释放\n"
    "6. 并发：是否有主线程阻塞的同步调用\n"
    "请按『等级（高/中/低）| 位置 | 问题 | 改法』格式输出清单，按优先级排序。"
    "若用户给了 symptom，优先围绕它分析。无问题则直接说明。"
)


async def suggest_performance_fixes(args: dict) -> str:
    scope = args["scope"]
    scan_dir = args.get("scan_dir") or "./generated"
    symptom = args.get("symptom") or ""
    files = resolve_scope(scope, scan_dir)
    user_input = (
        f"性能症状：{symptom}\n" if symptom else "请做整体性能审查\n"
    )
    return await analyze_with_context(
        _PERF_SYSTEM_PROMPT, user_input, files, max_tokens=4096
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run python analyzers/performance_test.py`
Expected: 全部 `[OK]`

- [ ] **Step 5: 写 `analyzers/bug_location_test.py` 失败测试**

```python
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
```

- [ ] **Step 6: 运行确认失败**

Run: `uv run python analyzers/bug_location_test.py`
Expected: FAIL `ModuleNotFoundError: No module named 'analyzers.bug_location'`

- [ ] **Step 7: 写 `analyzers/bug_location.py`**

```python
"""Bug 定位工具。"""

from analyzers.framework import analyze_with_context, resolve_scope

_BUG_SYSTEM_PROMPT = (
    "你是一名资深 HarmonyOS ArkTS 调试专家。用户给出症状与若干源码文件，"
    "请在源码中跨文件推理定位可疑位置。\n"
    "1. 根据症状推断可能的触发链路（哪条调用路径、哪个状态变迁）\n"
    "2. 给出最小复现步骤\n"
    "3. 给出验证手段（断点位置、日志埋点、单元测试要点）\n"
    "4. 给出建议修复方向\n"
    "请按『可疑位置 | 推理依据 | 复现步骤 | 建议修复 | 置信度（高/中/低）』格式输出。"
    "多候选时按置信度从高到低排序。无法定位时说明还缺什么信息。"
)


async def locate_bug(args: dict) -> str:
    scope = args["scope"]
    scan_dir = args.get("scan_dir") or "./generated"
    symptom = args["symptom"]
    files = resolve_scope(scope, scan_dir)
    user_input = f"症状/报错描述：{symptom}\n请定位 bug。"
    return await analyze_with_context(
        _BUG_SYSTEM_PROMPT, user_input, files, max_tokens=4096
    )
```

- [ ] **Step 8: 运行确认通过**

Run: `uv run python analyzers/bug_location_test.py`
Expected: 全部 `[OK]`

- [ ] **Step 9: 写 `analyzers/api_usage_test.py` 失败测试**

```python
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
    print("[OK] test_system_prompt_mentions_v1_v2")


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
    test_check_api_usage_returns_text()
    test_check_api_usage_uses_2048_tokens()
    print("\n全部通过。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 10: 运行确认失败**

Run: `uv run python analyzers/api_usage_test.py`
Expected: FAIL `ModuleNotFoundError: No module named 'analyzers.api_usage'`

- [ ] **Step 11: 写 `analyzers/api_usage.py`**

```python
"""API 用法纠错工具。"""

from analyzers.framework import analyze_with_context, resolve_scope

_API_SYSTEM_PROMPT = (
    "你是一名 HarmonyOS ArkTS/ArkUI API 用法审查专家。检查以下问题：\n"
    "1. API 误用：参数类型/数量错、调用时机错、返回值未处理\n"
    "2. 已废弃接口：是否用了标记 deprecated 的旧 API，应换什么\n"
    "3. V1/V2 状态管理混用：V1（@State/@Prop/@Link/@Observed/@ObjectLink）与 "
    "V2（@ComponentV2/@LocalV2/@Param/@Once/@ObservedV2/@Trace）不应在同一组件树混用\n"
    "4. 权限/能力缺失：调用需 ohos 权限的 API 是否在 module.json5 声明\n"
    "5. 平台差异：phone/tablet 不支持的 API\n"
    "请按『API | 误用位置 | 正确用法 | 依据』格式输出。若用户给了 focus_apis，优先查这些。"
)


async def check_api_usage(args: dict) -> str:
    scope = args["scope"]
    scan_dir = args.get("scan_dir") or "./generated"
    focus = args.get("focus_apis") or ""
    files = resolve_scope(scope, scan_dir)
    user_input = (
        f"重点关注的 API：{focus}\n" if focus else "请做整体 API 用法审查\n"
    )
    return await analyze_with_context(
        _API_SYSTEM_PROMPT, user_input, files, max_tokens=2048
    )
```

- [ ] **Step 12: 运行确认通过**

Run: `uv run python analyzers/api_usage_test.py`
Expected: 全部 `[OK]`

- [ ] **Step 13: 更新 `analyzers/__init__.py` 导出三个入口**

```python
"""分析工具包：共享框架 + 4 个分析工具。"""

from analyzers.framework import FileRef, analyze_with_context, resolve_scope
from analyzers.performance import suggest_performance_fixes
from analyzers.bug_location import locate_bug
from analyzers.api_usage import check_api_usage

__all__ = [
    "FileRef",
    "analyze_with_context",
    "resolve_scope",
    "suggest_performance_fixes",
    "locate_bug",
    "check_api_usage",
]
```

- [ ] **Step 14: 提交（询问用户后）**

```bash
git add analyzers/performance.py analyzers/performance_test.py \
        analyzers/bug_location.py analyzers/bug_location_test.py \
        analyzers/api_usage.py analyzers/api_usage_test.py \
        analyzers/__init__.py
git commit -m "feat(analyzers): 新增性能/Bug 定位/API 用法三个分析工具"
```

---

### Task 3: 运行日志分析工具（含路径映射）

**Files:**
- Create: `analyzers/runtime_logs.py` + `analyzers/runtime_logs_test.py`
- Modify: `analyzers/__init__.py`

**Interfaces:**
- Consumes: `resolve_scope`, `analyze_with_context` from Task 1
- Produces: `async def analyze_runtime_logs(args: dict) -> str`、`_extract_ets_paths(logs: str) -> list[str]`、`_truncate_logs(logs: str, limit_bytes: int = 30*1024) -> str`
- 日志路径映射逻辑归此文件，不进 framework

- [ ] **Step 1: 写 `analyzers/runtime_logs_test.py` 失败测试**

```python
"""analyze_runtime_logs 冒烟 + 路径提取测试。"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from analyzers.runtime_logs import (
    analyze_runtime_logs,
    _extract_ets_paths,
    _truncate_logs,
    _LOGS_SYSTEM_PROMPT,
)


def test_extract_ets_paths_finds_ets_in_stack():
    logs = (
        "E 0x00 Arksys: at HandleAttack (character/CharacterStats.ets:42:8)\n"
        "E 0x01 Arksys: at doAttack (skill/Skill.ets:10:3)\n"
        "some unrelated line"
    )
    paths = _extract_ets_paths(logs)
    assert "character/CharacterStats.ets" in paths
    assert "skill/Skill.ets" in paths
    print("[OK] test_extract_ets_paths_finds_ets_in_stack")


def test_extract_ets_paths_returns_empty_when_none():
    assert _extract_ets_paths("纯文本日志无路径\n另一行") == []
    print("[OK] test_extract_ets_paths_returns_empty_when_none")


def test_extract_ets_paths_dedup():
    logs = "at A (character/Foo.ets:1)\n at B (character/Foo.ets:2)"
    assert _extract_ets_paths(logs) == ["character/Foo.ets"]
    print("[OK] test_extract_ets_paths_dedup")


def test_truncate_logs_keeps_tail():
    big = "A" * (35 * 1024)
    out = _truncate_logs(big, limit_bytes=10 * 1024)
    assert len(out) <= 10 * 1024
    assert out == "A" * (10 * 1024)
    print("[OK] test_truncate_logs_keeps_tail")


def test_system_prompt_covers_log_dimensions():
    assert "堆栈" in _LOGS_SYSTEM_PROMPT
    assert "根因" in _LOGS_SYSTEM_PROMPT
    assert "置信度" in _LOGS_SYSTEM_PROMPT
    print("[OK] test_system_prompt_covers_log_dimensions")


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


def _patch_fake(return_text: str = "日志分析报告"):
    import analyzers.framework as fw
    fake = _FakeAnthropic(return_text)
    orig = fw.AsyncAnthropic
    fw.AsyncAnthropic = lambda *a, **k: fake
    return orig, fake


def test_analyze_runtime_logs_pulls_files_mentioned_in_logs():
    """日志提到 .ets 路径时，应把这些文件拉进上下文。"""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "character").mkdir()
        Path(d, "character", "CharacterStats.ets").write_text(
            "export struct CharacterStats { hp: number = 0 }", encoding="utf-8")
        orig, fake = _patch_fake()
        try:
            asyncio.run(analyze_runtime_logs({
                "logs": "E at HandleAttack (character/CharacterStats.ets:42:8)",
                "scan_dir": d,
            }))
        finally:
            import analyzers.framework as fw
            fw.AsyncAnthropic = orig
    sent = fake.messages.calls[0]["messages"][0]["content"]
    assert "character/CharacterStats.ets" in sent
    assert "export struct CharacterStats" in sent
    print("[OK] test_analyze_runtime_logs_pulls_files_mentioned_in_logs")


def test_analyze_runtime_logs_falls_back_to_scope_when_no_paths():
    """日志无 .ets 路径时，fallback 到 scope 拉上下文。"""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "skill").mkdir()
        Path(d, "skill", "Skill.ets").write_text("export struct Skill {}", encoding="utf-8")
        orig, fake = _patch_fake()
        try:
            asyncio.run(analyze_runtime_logs({
                "logs": "纯文本报错，无路径",
                "scope": "skill",
                "scan_dir": d,
            }))
        finally:
            import analyzers.framework as fw
            fw.AsyncAnthropic = orig
    sent = fake.messages.calls[0]["messages"][0]["content"]
    assert "export struct Skill" in sent
    print("[OK] test_analyze_runtime_logs_falls_back_to_scope_when_no_paths")


def test_analyze_runtime_logs_falls_back_to_all_by_default():
    with tempfile.TemporaryDirectory() as d:
        Path(d, "enemy").mkdir()
        Path(d, "enemy", "Enemy.ets").write_text("export struct Enemy {}", encoding="utf-8")
        orig, fake = _patch_fake()
        try:
            asyncio.run(analyze_runtime_logs({
                "logs": "纯文本",
                "scan_dir": d,
            }))
        finally:
            import analyzers.framework as fw
            fw.AsyncAnthropic = orig
    sent = fake.messages.calls[0]["messages"][0]["content"]
    assert "export struct Enemy" in sent  # scope 默认 all 拉到 enemy
    print("[OK] test_analyze_runtime_logs_falls_back_to_all_by_default")


def main():
    test_extract_ets_paths_finds_ets_in_stack()
    test_extract_ets_paths_returns_empty_when_none()
    test_extract_ets_paths_dedup()
    test_truncate_logs_keeps_tail()
    test_system_prompt_covers_log_dimensions()
    test_analyze_runtime_logs_pulls_files_mentioned_in_logs()
    test_analyze_runtime_logs_falls_back_to_scope_when_no_paths()
    test_analyze_runtime_logs_falls_back_to_all_by_default()
    print("\n全部通过。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python analyzers/runtime_logs_test.py`
Expected: FAIL `ModuleNotFoundError: No module named 'analyzers.runtime_logs'`

- [ ] **Step 3: 写 `analyzers/runtime_logs.py`**

```python
"""运行日志分析工具。路径映射逻辑归此文件，不进 framework。"""

import re

from analyzers.framework import FileRef, analyze_with_context, resolve_scope

_LOGS_SYSTEM_PROMPT = (
    "你是一名 HarmonyOS 运行日志分析师。用户给出一运行日志（可能含 ArkTS 堆栈）"
    "与若干源码文件上下文。请：\n"
    "1. 把堆栈帧 / 报错路径映射到源码位置（若上下文已给出对应文件）\n"
    "2. 区分错误类型：JS 异常、native crash、资源错误、权限错误\n"
    "3. 给根因假设与修复方向\n"
    "请按『位置 | 错误类型 | 根因假设 | 修复建议 | 置信度（高/中/低）』格式输出。"
    "无法定位时说明还缺什么信息（如更多日志、对应源码）。"
)

# 提取日志里的 .ets 路径（如 character/CharacterStats.ets 或 entry/.../Index.ets）
_ETS_PATH_RE = re.compile(r"([\w./-]+\.ets)(?::\d+)?")
_LOGS_LIMIT = 30 * 1024


def _extract_ets_paths(logs: str) -> list[str]:
    """best-effort 从日志提取 .ets 路径，按出现顺序去重。"""
    seen = set()
    out = []
    for m in _ETS_PATH_RE.finditer(logs):
        p = m.group(1)
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _truncate_logs(logs: str, limit_bytes: int = _LOGS_LIMIT) -> str:
    """保留末尾 limit_bytes（堆栈通常在日志末尾）。"""
    if len(logs) <= limit_bytes:
        return logs
    return logs[-limit_bytes:]


async def analyze_runtime_logs(args: dict) -> str:
    logs = args["logs"]
    scope = args.get("scope") or "all"
    scan_dir = args.get("scan_dir") or "./generated"

    logs = _truncate_logs(logs)
    paths = _extract_ets_paths(logs)

    files: list[FileRef] = []
    for p in paths:
        # 命中的路径按文件路径分支拉真实文件
        files.extend(resolve_scope(p, scan_dir))
    if not files:
        # 未命中任何路径 → fallback 到 scope
        files = resolve_scope(scope, scan_dir)

    return await analyze_with_context(
        _LOGS_SYSTEM_PROMPT, user_input=logs, files=files, max_tokens=2048
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `uv run python analyzers/runtime_logs_test.py`
Expected: 全部 `[OK]`

- [ ] **Step 5: 更新 `analyzers/__init__.py` 加 runtime_logs 导出**

在 Task 2 的 `__init__.py` 基础上追加：

```python
from analyzers.runtime_logs import analyze_runtime_logs
```

并在 `__all__` 加 `"analyze_runtime_logs"`。完整文件：

```python
"""分析工具包：共享框架 + 4 个分析工具。"""

from analyzers.framework import FileRef, analyze_with_context, resolve_scope
from analyzers.performance import suggest_performance_fixes
from analyzers.bug_location import locate_bug
from analyzers.api_usage import check_api_usage
from analyzers.runtime_logs import analyze_runtime_logs

__all__ = [
    "FileRef",
    "analyze_with_context",
    "resolve_scope",
    "suggest_performance_fixes",
    "locate_bug",
    "check_api_usage",
    "analyze_runtime_logs",
]
```

- [ ] **Step 6: 提交（询问用户后）**

```bash
git add analyzers/runtime_logs.py analyzers/runtime_logs_test.py analyzers/__init__.py
git commit -m "feat(analyzers): 新增运行日志分析工具含路径映射与 fallback"
```

---

### Task 4: tools.py 装配（4 新 @tool + review A1 重构）

**Files:**
- Modify: `tools.py`
- Create: `tools_review_test.py`（review 回归测试）

**Interfaces:**
- Consumes: 4 个分析工具入口 from `analyzers`、`analyze_with_context` + `FileRef` from `analyzers.framework`
- Produces: 5 个 `@tool` 装饰函数（4 新 + 重构后的 `review_arkts_code`）注册进 `build_server()`

- [ ] **Step 1: 写 `tools_review_test.py` 失败测试（review 回归）**

```python
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
        # tools.review_arkts_code 是 @tool 包装的函数，直接 await 调用
        result = asyncio.run(tools.review_arkts_code({"code": "export struct X {}"}))
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
        result = asyncio.run(tools.review_arkts_code({"code": "x"}))
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
```

- [ ] **Step 2: 运行确认失败**

Run: `uv run python tools_review_test.py`
Expected: FAIL（review_arkts_code 仍用内联 AsyncAnthropic，不走 analyzers.framework，`fake.messages.calls` 为空，断言 `len == 1` 失败）

- [ ] **Step 3: 修改 `tools.py`——重构 review + 加 4 个新 @tool + 注册**

在 `tools.py` 顶部 import 区追加：

```python
from analyzers import (
    analyze_runtime_logs,
    check_api_usage,
    locate_bug,
    suggest_performance_fixes,
)
from analyzers.framework import FileRef, analyze_with_context
```

替换 `review_arkts_code` 函数（[tools.py:135-172](../../tools.py)）为 A1 重构版：

```python
@tool(
    "review_arkts_code",
    "用 LLM 对传入的 ArkTS 代码做智能审查，返回结构化的问题清单与改进建议。",
    {"code": str},
)
async def review_arkts_code(args):
    system_prompt = (
        "你是一名资深 HarmonyOS ArkTS 代码审查专家。对用户给出的 ArkTS 代码进行审查，"
        "从以下维度逐一检查并报告问题：\n"
        "1. 组件结构：@Component/@Entry/build() 是否完整、是否符合 ArkTS 组件规范\n"
        "2. 状态管理：@State/@Prop/@Link 使用是否合理，是否有冗余状态\n"
        "3. 性能：是否有不必要的重渲染、昂贵操作放在 build() 中\n"
        "4. ArkTS 规范：命名约定、类型标注、是否用了 console.log（应用 hilog）等\n"
        "5. 潜在 bug：空指针、资源未释放、事件未解绑等\n"
        "请按『等级（高/中/低）| 位置 | 描述 | 建议』格式输出清单，最后给一句总体评价。"
        "若代码无问题，直接说明。"
    )
    files = [FileRef(path="<贴入代码>", content=args["code"])]
    try:
        text = await analyze_with_context(
            system_prompt, "请审查以下 ArkTS 代码", files, max_tokens=1024
        )
    except Exception as e:
        return {"content": [{"type": "text", "text": f"审查失败：{e}"}]}
    return {"content": [{"type": "text", "text": text or "(审查未返回文本)"}]}
```

在 `scaffold_deveco_project` 与 `review_arkts_code` 之间追加 4 个新 `@tool` 包装：

```python
@tool(
    "analyze_runtime_logs",
    "分析鸿蒙运行日志（含 ArkTS 堆栈），把报错路径映射到源码并给根因假设与修复方向。"
    "参数：logs 日志全文；scope 可选（文件路径/子系统名/'all'，默认 'all'）。",
    {"logs": str, "scope": str},
)
async def analyze_runtime_logs_tool(args):
    try:
        text = await analyze_runtime_logs({
            "logs": args["logs"],
            "scope": args.get("scope") or "all",
        })
    except Exception as e:
        return {"content": [{"type": "text", "text": f"日志分析失败：{e}"}]}
    return {"content": [{"type": "text", "text": text or "(日志分析未返回文本)"}]}


@tool(
    "suggest_performance_fixes",
    "对已有 ArkTS 代码做性能审查，给出瓶颈清单与改法。"
    "参数：scope（文件路径/子系统名/'all'）；symptom 可选（如'列表卡顿'）。",
    {"scope": str, "symptom": str},
)
async def suggest_performance_fixes_tool(args):
    try:
        text = await suggest_performance_fixes({
            "scope": args["scope"],
            "symptom": args.get("symptom") or "",
        })
    except Exception as e:
        return {"content": [{"type": "text", "text": f"性能分析失败：{e}"}]}
    return {"content": [{"type": "text", "text": text or "(性能分析未返回文本)"}]}


@tool(
    "locate_bug",
    "根据症状在已有 ArkTS 代码中跨文件推理定位可疑位置，给复现步骤与修复方向。"
    "参数：scope（文件路径/子系统名/'all'）；symptom 必填（症状/报错描述）。",
    {"scope": str, "symptom": str},
)
async def locate_bug_tool(args):
    try:
        text = await locate_bug({
            "scope": args["scope"],
            "symptom": args["symptom"],
        })
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Bug 定位失败：{e}"}]}
    return {"content": [{"type": "text", "text": text or "(Bug 定位未返回文本)"}]}


@tool(
    "check_api_usage",
    "检查 ArkTS/ArkUI API 用法（误用/废弃/V1-V2 混用/权限缺失）。"
    "参数：scope（文件路径/子系统名/'all'）；focus_apis 可选（如'@State Navigation'）。",
    {"scope": str, "focus_apis": str},
)
async def check_api_usage_tool(args):
    try:
        text = await check_api_usage({
            "scope": args["scope"],
            "focus_apis": args.get("focus_apis") or "",
        })
    except Exception as e:
        return {"content": [{"type": "text", "text": f"API 审查失败：{e}"}]}
    return {"content": [{"type": "text", "text": text or "(API 审查未返回文本)"}]}
```

更新 `build_server()` 的 tools 列表（[tools.py:177-187](../../tools.py)）追加 4 个新工具：

```python
def build_server():
    """创建装载了自定义工具的 in-process MCP 服务器。"""
    return create_sdk_mcp_server(
        name="harmony_tools",
        version="1.0.0",
        tools=[
            generate_character_stats,
            generate_skill_system,
            generate_inventory,
            generate_enemy_ai,
            scaffold_deveco_project,
            review_arkts_code,
            analyze_runtime_logs_tool,
            suggest_performance_fixes_tool,
            locate_bug_tool,
            check_api_usage_tool,
        ],
    )
```

同时更新 `tools.py` 顶部模块 docstring 的工具计数（6 → 10）。

- [ ] **Step 4: 运行 review 回归测试确认通过**

Run: `uv run python tools_review_test.py`
Expected: 全部 `[OK]`

- [ ] **Step 5: 冒烟检查 `tools.py` 可导入**

Run: `uv run python -c "from tools import build_server; s = build_server(); print('tools ok')"`
Expected: 输出 `tools ok` 无异常

- [ ] **Step 6: 提交（询问用户后）**

```bash
git add tools.py tools_review_test.py
git commit -m "feat(tools): 注册 4 个分析工具并重构 review_arkts_code 进共享框架"
```

---

### Task 5: main.py 主 Agent 改动 + 集成验证

**Files:**
- Modify: `main.py:43-61`（system_prompt）、`main.py:63-71`（allowed_tools）、`main.py:120-121`（REPL 提示行）

**Interfaces:**
- Consumes: 5 个工具已在 `build_server()` 注册（Task 4）

- [ ] **Step 1: 修改 `main.py` 的 `allowed_tools`**

在 [main.py:63-71](../../main.py) 的 `allowed_tools` 列表追加 4 行（`review_arkts_code` 与 `Write` 已在）：

```python
        allowed_tools=[
            "mcp__harmony_tools__generate_character_stats",
            "mcp__harmony_tools__generate_skill_system",
            "mcp__harmony_tools__generate_inventory",
            "mcp__harmony_tools__generate_enemy_ai",
            "mcp__harmony_tools__scaffold_deveco_project",
            "mcp__harmony_tools__review_arkts_code",
            "mcp__harmony_tools__analyze_runtime_logs",
            "mcp__harmony_tools__suggest_performance_fixes",
            "mcp__harmony_tools__locate_bug",
            "mcp__harmony_tools__check_api_usage",
            "Write",
        ],
```

- [ ] **Step 2: 修改 `main.py` 的 `system_prompt`**

在 [main.py:59](../../main.py) `当用户要求审查代码时...` 一行之后、`主动根据用户需求...` 一行之前，插入分析工具说明段：

```python
            "当用户要求审查代码时，调用 review_arkts_code。\n"
            "当用户报运行日志/崩溃/报错时，调用 analyze_runtime_logs（logs 全文 + 可选 scope）。\n"
            "当用户报性能问题/卡顿时，调用 suggest_performance_fixes（scope + 可选 symptom）。\n"
            "当用户要定位 bug 时，调用 locate_bug（scope + 必填 symptom 症状描述）。\n"
            "当用户怀疑 API 用错/废弃/V1-V2 混用时，调用 check_api_usage（scope + 可选 focus_apis）。\n"
            "这四个分析工具的 scope 参数三形态：文件路径（如 character/X.ets）/子系统名（character/skill/inventory/enemy）/"
            "'all' 全部子系统。分析工具返回纯文本报告，直接展示给用户，不写盘（区别于生成类工具返回 {files} 需 Write）。\n"
            "主动根据用户需求选择合适的工具，并结合工具返回结果给出说明。"
```

（替换原来的 `当用户要求审查代码时，调用 review_arkts_code。\n主动根据用户需求...` 两行——分析段插在中间。）

- [ ] **Step 3: 修改 REPL 启动提示行**

在 [main.py:120-121](../../main.py) 的 `print("可用工具：...")` 行追加 4 个新工具名：

```python
    print("可用工具：generate_character_stats / generate_skill_system / "
          "generate_inventory / generate_enemy_ai / scaffold_deveco_project / "
          "review_arkts_code / analyze_runtime_logs / suggest_performance_fixes / "
          "locate_bug / check_api_usage")
```

- [ ] **Step 4: 集成验证——全部测试跑一遍**

Run:
```bash
uv run python analyzers/framework_test.py && \
uv run python analyzers/performance_test.py && \
uv run python analyzers/bug_location_test.py && \
uv run python analyzers/api_usage_test.py && \
uv run python analyzers/runtime_logs_test.py && \
uv run python tools_review_test.py
```
Expected: 全部测试 `[OK]` + `全部通过。`，无异常退出。

- [ ] **Step 5: 导入冒烟**

Run: `uv run python -c "from main import build_options; o = build_options(); print('main ok', len(o.allowed_tools))"`
Expected: 输出 `main ok 11`（10 个 mcp 工具 + Write）

- [ ] **Step 6: 提交（询问用户后）**

```bash
git add main.py
git commit -m "feat(main): 主 Agent 注册 4 个分析工具并更新提示词"
```

---

## 受影响文件汇总

- 新增：`analyzers/__init__.py`、`analyzers/framework.py`、`analyzers/framework_test.py`、`analyzers/performance.py`、`analyzers/performance_test.py`、`analyzers/bug_location.py`、`analyzers/bug_location_test.py`、`analyzers/api_usage.py`、`analyzers/api_usage_test.py`、`analyzers/runtime_logs.py`、`analyzers/runtime_logs_test.py`、`tools_review_test.py`
- 修改：`tools.py`、`main.py`
- spec 文档（已写）：`docs/superpowers/specs/2026-07-01-review-debug-design.md`

## 复用现有代码

- `generators/framework.py` 的 `AsyncAnthropic() + os.environ.get("ANTHROPIC_MODEL")` 中转配置模式 → `analyzers/framework.py` 直接沿用
- `generators/deveco_project.py` 的 `_KNOWN_SUBSYSTEMS` 与扫描风格 → `analyzers/framework.py` 复用同名常量与扫描逻辑
- `tools.py` 现有 `generate_*` 的 `@tool` + try/except 友好错误模式 → 4 个新 `@tool` 包装照搬
- 测试自跑 `main()` + monkeypatch `AsyncAnthropic` 模式 → `analyzers/*_test.py` 照搬

## 验证

1. **框架单测**：`uv run python analyzers/framework_test.py` 全过
2. **四工具冒烟**：`uv run python analyzers/{performance,bug_location,api_usage,runtime_logs}_test.py` 全过
3. **review 回归**：`uv run python tools_review_test.py` 全过（重构未破坏现有行为）
4. **导入冒烟**：`from main import build_options` 成功，`allowed_tools` 含 11 项
5. **REPL 手动验收**（可选，需真实 API key）：`uv run python main.py` 进 REPL，生成子系统后调 `analyze_runtime_logs` / `suggest_performance_fixes` 等验证真实分析输出

## 不在本期范围

文件树 UI、历史会话、导出 DevEco 工程、跨文件 import 修正、IDE 诊断接入、整工程 scope 扫描、日志持久化——属后续阶段（Phase 4 Web 工作台增强等）。

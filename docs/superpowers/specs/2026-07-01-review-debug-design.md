# 审查与调试增强设计（Phase 3）

日期：2026-07-01
阶段：第三阶段（审查与调试增强），承接 [Phase 2：DevEco 脚手架](./2026-07-01-deveco-scaffold-design.md)

## 目标

从"生成"扩展到"分析"：新增 4 个面向已有代码的分析工具——运行日志分析、性能瓶颈建议、Bug 定位、API 用法纠错。它们读 `./generated/` 下已有文件做上下文，用 LLM 给出结构化分析报告。同时把现有 `review_arkts_code` 重构进新的共享分析框架，消除 LLM 调用样板重复。

## 非目标

- 不做 DevEco 工程编译/真机运行（继承 Phase 2）
- 不做跨文件 import 路径自动修正（继承 Phase 2）
- 不接 IDE 诊断 / hvigor 编译输出作为输入源（日志靠用户粘贴）
- 不做交互式调试器 / 断点
- 不做 `scope="<工程名>"` 整工程扫描（脚手架工程文件靠传完整相对路径，YAGNI 留后续）
- 不做日志的持久化存储 / 历史检索
- 不修改 Phase 1 的 4 个 RPG 生成器与 Phase 2 脚手架

## 现状

- `generators/framework.py`：Phase 1 共享混合生成框架（`hybrid_generate`，渲染模板 + LLM 填充 + 降级）
- `generators/{character_stats,skill_system,inventory,enemy_ai}.py`：4 个 RPG 生成器，输出 `./generated/<子系统>/`
- `generators/deveco_project.py`：Phase 2 脚手架，产出 `./generated/<工程名>/` 完整 DevEco 工程
- `tools.py`：注册 6 个工具；`review_arkts_code`（[tools.py:135-172](../../tools.py)）内联 `AsyncAnthropic() + model 解析 + try/except + resp.content 提取` 样板
- `./generated/`：含 `<子系统>/` 目录、脚手架 `<工程名>/` 工程目录、旧扁平文件

## 架构

### 模块划分

```
analyzers/
  __init__.py            ← 导出 4 个分析工具入口 + analyze_with_context + resolve_scope
  framework.py           ← 共享：resolve_scope + analyze_with_context + FileRef
  runtime_logs.py        ← analyze_runtime_logs（含日志路径映射）
  performance.py         ← suggest_performance_fixes
  bug_location.py        ← locate_bug
  api_usage.py           ← check_api_usage
tools.py                 ← 装配层：5 个 @tool 包装（4 新 + review 重构）
main.py                  ← allowed_tools + system_prompt 增补
```

`analyzers/` 与 `generators/` 并列——生成与分析是两条对称脉络，各有共享框架。

### 职责边界

- `analyzers/framework.py`：只提供两个通用原语 `resolve_scope` 与 `analyze_with_context`，不掺领域逻辑
- 各工具文件：声明自己的 system_prompt + 输入组装 + 领域逻辑（如日志路径映射归 `runtime_logs.py`）
- `tools.py`：`@tool` 装配层，try/except 把框架异常转成 MCP 友好文本
- `main.py`：`allowed_tools` 与 `system_prompt` 更新

## 共享框架（`analyzers/framework.py`）

### 数据结构

```python
@dataclass
class FileRef:
    path: str        # 相对路径，如 "character/CharacterStats.ets"
    content: str
```

### `resolve_scope(scope, scan_dir="./generated") -> list[FileRef]`

把 `scope` 解析成文件清单，判定顺序（写死，消歧）：

1. `scope == "all"` → 扫全部已知子系统（`character` / `skill` / `inventory` / `enemy`）下所有 `.ets`
2. `scope in _KNOWN_SUBSYSTEMS` → 该子系统目录下所有 `.ets`
3. 否则 → 当文件路径（相对 `scan_dir` 拼接，支持 `mygame/entry/src/main/ets/pages/Index.ets` 这种工程内路径）

**路径穿越防护**：文件路径分支先 `os.path.realpath` 归一化，断言结果在 `os.path.realpath(scan_dir)` 之内，越界返回**空清单**（不抛）。子系统分支靠 `_KNOWN_SUBSYSTEMS` 白名单天然安全。

**找不到兜底**：文件/子系统不存在、未知子系统名 → 返回空清单，不抛（由调用方决定如何提示）。

### `analyze_with_context(system_prompt, user_input, files, max_tokens=2048) -> str`

组装文件上下文 + 用户输入喂给 `AsyncAnthropic`，返回分析文本。

**文件上下文格式（纯 XML）**：
```
<files>
<file path="character/CharacterStats.ets">完整内容</file>
<file path="skill/Skill.ets">完整内容</file>
</files>

用户问题：<user_input>
```

`files` 为空时省略 `<files>` 段（纯日志分析场景仍可调）。

**容量截断**：files 总字节超 80KB 时按文件顺序截断，被截断的文件内容末尾标 `\n[已截断]`，超出部分的文件不再放入。日志类输入由 `runtime_logs.py` 入口先截断到 30KB（保留含堆栈的尾部）再传 `user_input`。

**中转配置**：沿用 `AsyncAnthropic()`（自动读 `ANTHROPIC_API_KEY` / `ANTHROPIC_BASE_URL`）+ `os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-5"`，与 `generators/framework.py` 同款。

**失败行为**：LLM 调用/网络失败 → **raise**（不兜底）。分析工具无降级路径，失败由 `@tool` 包装层转友好文本。

## 四个工具接口与 system_prompt

### 1. `analyze_runtime_logs`

```python
{"logs": str, "scope": str}   # scope 可选，默认 "all"
```

**流程**（`runtime_logs.py` 自处理路径映射）：
1. 入口先对 `logs` 截断到 30KB（保留末尾 30KB，堆栈通常在日志末尾）
2. 正则提取 logs 里的 `.ets` 文件路径（best-effort，release 包堆栈可能混淆/无源码路径）
3. 命中的路径调 `resolve_scope`（按文件路径分支）拉真实文件
4. 未命中任何路径 → fallback 到 `scope` 参数（默认 `"all"`）拉上下文
5. `analyze_with_context(system_prompt, user_input=logs截断后, files=拉到的文件, max_tokens=2048)`

**system_prompt 要点**：鸿蒙运行日志分析师。把堆栈帧 / 报错路径映射到源码位置；区分 JS 异常、native crash、资源错误；给根因假设 + 修复方向；输出 `位置 | 根因假设 | 修复建议 | 置信度`。

### 2. `suggest_performance_fixes`

```python
{"scope": str, "symptom": str}   # symptom 可选，如"列表卡顿"
```

**流程**：`resolve_scope(scope)` → `analyze_with_context(..., max_tokens=4096)`（性能建议输出较长）。

**system_prompt 要点**：鸿蒙性能专家。检查 build() 内昂贵操作、@State/@Prop 粒度与重渲染范围、列表未用 LazyForEach、图片未解码缓存、事件/定时器未解绑、aboutToDisappear 资源释放。输出 `等级 | 位置 | 问题 | 改法`，附优先级排序。

### 3. `locate_bug`

```python
{"scope": str, "symptom": str}   # symptom 必填，症状/报错描述
```

**流程**：`resolve_scope(scope)` → `analyze_with_context(..., max_tokens=4096)`（跨文件推理输出较长）。

**system_prompt 要点**：鸿蒙 bug 定位专家。根据 symptom 在 scope 文件内跨文件推理可疑位置；给出假设链路（哪条调用路径触发）、最小复现步骤、验证手段。输出 `可疑位置 | 推理依据 | 复现步骤 | 建议修复`，多候选时按置信度排序。

### 4. `check_api_usage`

```python
{"scope": str, "focus_apis": str}   # focus_apis 可选，如"@State Navigation"
```

**流程**：`resolve_scope(scope)` → `analyze_with_context(..., max_tokens=2048)`。

**system_prompt 要点**：鸿蒙 API 用法审查。检查 ArkTS/ArkUI API 误用、已废弃接口、参数类型错、V1/V2 状态管理混用（V1：@State/@Prop/@Link/@Observed；V2：@ComponentV2/@LocalV2/@Param/@Once/@ObservedV2/@Trace）、权限/能力缺失。输出 `API | 误用位置 | 正确用法 | 依据`。

### 返回格式

四个工具都返回**纯文本分析报告**（非 `{files}`），主 Agent 直接展示给用户，不写盘。区别于 4 个 RPG 生成器与脚手架返回 `{files}` 需 Write。

## review_arkts_code 重构（A1 路径）

入参与 system_prompt 都不变（仍 `{"code": str}` + 5 维 checklist）。重构只动内部实现：把 code 包成单条 `FileRef` 调 `analyze_with_context`，删除 `tools.py` 内联的 `AsyncAnthropic() + model 解析 + resp.content 提取` 样板。

```python
@tool("review_arkts_code", "...", {"code": str})
async def review_arkts_code(args):
    system_prompt = <原 5 维 checklist 不变>
    files = [FileRef(path="<贴入代码>", content=args["code"])]
    try:
        text = await analyze_with_context(
            system_prompt, "请审查以下 ArkTS 代码", files, max_tokens=1024
        )
    except Exception as e:
        return {"content": [{"type":"text","text": f"审查失败：{e}"}]}
    return {"content": [{"type":"text","text": text or "(审查未返回文本)"}]}
```

不改工具语义（仍是"贴代码审查"），仅消除样板。4 个新工具的 `@tool` 包装同款 try/except 结构。

## 错误处理分层

| 层 | 失败行为 |
|---|---|
| `resolve_scope` | 文件/子系统不存在、路径越界 → 返回**空清单**，不抛 |
| `analyze_with_context` | LLM 调用/网络失败 → **raise**，不兜底 |
| `@tool` 包装层 | try/except → `{"content":[{"type":"text","text":f"<工具名>失败：{e}"}]}` |

与现有 `generate_*` 包装层（[tools.py:53-54](../../tools.py)）一致。分析工具无降级路径（不像生成有 `// TODO`），失败就是失败，由包装层转友好文本。

## 测试

沿用 Phase 1/2 风格：自带 `main()`、非 pytest、桩 LLM monkeypatch `analyzers.framework.AsyncAnthropic`。

**`analyzers/framework_test.py`**：
- `resolve_scope` 三形态：文件路径 / 子系统名 / `"all"` 各自返回正确清单
- `resolve_scope` 路径穿越：`scope="../../etc/passwd"` 与绝对路径 → 返回空清单
- `resolve_scope` 不存在文件 / 未知子系统名 → 空清单，不抛
- `analyze_with_context` 文件上下文拼装为纯 XML `<file path=...>` 格式
- `analyze_with_context` files 为空时省略上下文段
- `analyze_with_context` files 总字节超 80KB → 截断并标注 `[已截断]`
- `analyze_with_context` LLM 失败 → raise（不兜底）

**各工具冒烟测试**（`runtime_logs_test.py` / `performance_test.py` / `bug_location_test.py` / `api_usage_test.py`）：桩 LLM 返回固定文本，断言流程跑通、输出含期望字段。
- `runtime_logs_test.py` 额外测路径提取：造含 `.ets` 路径的假日志 → 断言拉到对应文件；无路径日志 → fallback 到 `scope`。

**`review_arkts_code` 回归测试**：重构后用同一份 code 输入，断言仍走 `analyze_with_context` 且返回文本结构不变（防重构破坏现有行为）。

## 风险与边界

- **日志路径提取 best-effort**：release 包堆栈常混淆/无 `.ets` 路径，正则提取命中则拉文件、未命中 fallback `scope`。spec 显式标注为 best-effort，不当作可靠能力。
- **上下文容量**：`"all"` scope 全文件 + 大日志可能逼近上下文上限。`analyze_with_context` 对 files 总字节软上限 80KB（超出按文件顺序截断并标 `[已截断]`）；`runtime_logs.py` 入口对 logs 截断到 30KB 保留含堆栈的尾部。
- **路径穿越**：`resolve_scope` 文件路径分支 `realpath` 归一化后断言在 `scan_dir` 内，越界返回空清单。子系统分支靠 `_KNOWN_SUBSYSTEMS` 白名单天然安全。
- **分析准确度依赖 LLM**：工具给的是"假设 + 方向"，不是确定真理；system_prompt 要求标注置信度，最终修复决策由用户确认。
- **review 重构回归**：A1 路径不动入参与 system_prompt，但有行为微变风险（LLM 调用从 tools.py 内联改为 framework），用回归测试兜住。

## 主 Agent 改动

- `main.py` `build_options()` 的 `allowed_tools` 增 4 个：`mcp__harmony_tools__analyze_runtime_logs` / `suggest_performance_fixes` / `locate_bug` / `check_api_usage`
- `main.py` `build_options()` 的 `system_prompt` 增补一段：4 个分析工具用途、`scope` 三形态、与生成工具区别（返回纯文本不写盘）、触发指引（报 bug / 性能问题 / 日志报错 / 怀疑 API 用错时主动选对应工具）
- REPL 启动提示行（[main.py:121](../../main.py)）补 4 个工具名

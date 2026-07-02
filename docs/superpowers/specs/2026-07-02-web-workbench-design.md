# Web 工作台增强设计（Phase 4）

日期：2026-07-02
阶段：第四阶段（Web 工作台增强），承接 [Phase 3：审查与调试增强](./2026-07-01-review-debug-design.md)

## 目标

让网页工作台（`server.py` + `index.html`）反映当前 10 个工具的真实能力，并把工具返回的结构化结果可视化：
- 4 个分析工具（`analyze_runtime_logs` / `suggest_performance_fixes` / `locate_bug` / `check_api_usage`）从纯文本输出改为 JSON 数组输出，前端渲染为结构化 finding 卡片（等级色标 / 置信度 / 排序）
- 生成工具落盘的文件由 server.py 拦截 `Write` 工具调用捕获，前端渲染为可预览 / 复制单文件 / 导出的 file 卡片
- 新增 `/export` 端点：按路径打包 `./generated/` 下任意目录为 zip 或直返单文件
- 同步 `index.html` 的工具标签与空状态提示词，反映 4 类能力（生成 / 审查 / 分析 / 脚手架）

## 非目标

- 不改 4 个 analyzer 的入参（`scope` / `symptom` / `focus_apis` / `logs`）与 `review_arkts_code`（5 维 checklist 仍纯文本）
- 不改生成工具与 `hybrid_generate`（Phase 1/2 已合并代码零回归）
- 不改 `analyzers.framework` 的 `resolve_scope` / `analyze_with_context`
- 不做多会话持久化 / 历史回放（属另一方向）
- 不做文件上传 / 附件 / cost 展示 / 移动端增强（YAGNI）
- 不做 `Edit` 工具的可视化（只覆盖 `Write` 新建文件）
- 不做 `review` 工具的 JSON 化（5 维 checklist 与 findings schema 兼容是未来扩展点，本期不动）
- 不做 `Bash` 等其他落盘方式的可视化（system_prompt 已约定用 `Write`）
- 不做 zip 流式导出（generated 体量小，内存 zip 足够，不引入 zipstream 依赖）

## 现状

- `server.py`：Starlette + SSE，`POST /chat` 返回流，`ClaudeSDKClient` 常驻多轮会话；`/` 返回 `index.html`；`/chat` 把 `AssistantMessage` / `UserMessage` / `ResultMessage` 转 SSE 事件（`text` / `tool_use` / `tool_result` / `done` / `error`）。复用 `main._extract_tool_result_text`
- `index.html`：左侧轨迹轨（回合 + 工具调用链节点）、中间对话区（markdown + 代码高亮 + 复制）、输入栏。`TOOL_LABELS` 仍列已不存在的 `generate_arkts_component`，提示词只提 HealthBar / Inventory / 审查——未反映 10 个工具
- `main._extract_tool_result_text(block) -> str`：从 `ToolResultBlock.content`（`str | list[dict] | None`）取 text block 文本，未做结构化解析
- `tools.py`：4 个生成工具 + 脚手架包装层调 `_format_files(result)` 把 `{files}` 转人类可读文本；4 个分析工具 + review 包装层 try/except 转 MCP 友好文本
- Phase 3：4 个 analyzer 的 `system_prompt` 要求输出 `位置 | 根因 | 建议 | 置信度` 管道分隔表格行，入口函数 `async def analyze_xxx(args) -> str` 返回纯文本
- `ToolResultBlock.content: str | list[dict[str, Any]] | None`（核查过 SDK 源码）——content list 里的 dict 必须是合法 MCP content block（`{"type":"text","text":...}`），不能放裸 finding 对象

## 架构

### 核心改动

1. **分析工具 JSON 输出**：4 个 analyzer 的 `system_prompt` 改要求输出 JSON 数组；入口函数签名不变（仍 `-> str`，str 现为 JSON 文本）。解析责任在 `main.py` / `server.py` 两处
2. **Write 拦截捕获 file**：server.py 在 SSE 流内配对 `Write` 工具的 `tool_use`（取 `input.file_path` / `content`）与 `tool_result`（取 `is_error`），发 `file` 事件。生成工具包装层不动
3. **/export 端点**：按路径打包 `generated/` 下目录为 zip 或直返单文件
4. **前端三卡片**：file 卡片 / findings 卡片 / 原有 tool_result 卡片（fallback）

### 模块划分

```
analyzers/
  framework.py             ← 不动（resolve_scope / analyze_with_context）
  findings.py              ← 新增：parse_findings(text) -> list[dict] | None
                              + _format_findings_text(findings) -> str
  performance.py           ← system_prompt 改 JSON 输出
  bug_location.py          ← 同上
  api_usage.py             ← 同上
  runtime_logs.py          ← 同上
  *_test.py                ← 更新断言（JSON 输出 + 容错）
  findings_test.py         ← 新增：parse_findings 单测

main.py                    ← _raw_tool_result_text(block) -> str（取原文不解析）
                              _extract_tool_result_text 改为：parse_findings 成功→格式化、失败→原文
                              新增 import parse_findings / _format_findings_text from analyzers.findings

server.py                  ← stream() 维护 pending_writes dict 配对 Write
                              事件分发：Write result→file 事件；findings JSON→findings 事件；其余→tool_result
                              新增 /export 路由 + _build_zip(root) -> bytes

index.html                 ← TOOL_LABELS 同步 10 工具；提示词更新
                              新增 file 卡片 / findings 卡片渲染
                              轨迹轨 file/findings 事件标 ok

tools.py / generators/     ← 完全不动
```

### 职责边界

- `analyzers/findings.py`：纯解析 + 格式化，无 LLM、无 IO。供 `main.py` 与 `server.py` 共用
- `main.py`：REPL 文本格式化（`_extract_tool_result_text` 组合 parse + format）
- `server.py`：SSE 事件分发 + Write 配对 + /export
- `index.html`：卡片渲染，无业务逻辑
- `tools.py` / `generators/`：零改动

## findings JSON schema 与解析容错

### 统一核心 schema（4 工具共用）

```json
[
  {
    "severity": "高" | "中" | "低",
    "location": "character/CharacterStats.ets:42",
    "summary": "一句话问题/根因",
    "fix": "修复建议/正确用法"
  }
]
```

### 工具特有字段（追加到同一对象，前端键值对兜底渲染）

- `runtime_logs`：`confidence: 0.0-1.0`、`root_cause: 根因假设`
- `performance`：无额外（核心四字段即其维度）
- `bug_location`：`repro: 复现步骤`、`reasoning: 推理依据`
- `api_usage`：`reference: 依据`（"正确用法"并入 `fix`）

### 解析容错链（`parse_findings`）

1. `text.strip()`
2. 剥 markdown code fence（复用 `generators.framework._strip_code_fences`）
3. 提取首个 `[...]` 子串（正则 `\[.*\]` DOTALL）——兜底 LLM 在 JSON 前后加解释文字
4. `json.loads` → 非 list 或元素非 dict → 返回 None
5. 字段别名容错（仅常见几个，不穷举）：`location` 缺失时尝试 `loc` / `position` / `位置`；`fix` 缺失时尝试 `fix_suggestion` / `修复` / `correct_usage`
6. 任何步骤失败 → 返回 None，调用方回退纯文本

### system_prompt 修改要点（4 个 analyzer 各加一段）

> 请输出一个 JSON 数组（不要 markdown 代码块标记、不要任何解释文字），每个元素含字段：severity（高/中/低）、location、summary、fix[、工具特有字段]。若无任何发现，返回 `[]`。

### 前端 findings 卡片渲染

`severity` 左色条（高红 / 中黄 / 低蓝）、`location` 标题、`summary` 副标题、`fix` 高亮块（带复制按钮）、特有字段（`repro` / `confidence` / `root_cause` / `reference`）按 key-value 追加。按 severity 排序（高 → 中 → 低）。

`findings` 为空数组 `[]`（合法结果，工具确认无问题）→ 仍发 `findings` 事件，前端渲染一张"✓ 无发现"提示卡片（不发 `tool_result` fallback）。只有 `parse_findings` 返回 `None`（解析失败）才回退 `tool_result`。

## SSE 事件与前端渲染

### raw 文本提取拆分

- `_raw_tool_result_text(block) -> str`：从 `block.content` 取 text block 文本，不解析
- `parse_findings(raw) -> list[dict] | None`（见上节）
- `_format_findings_text(findings) -> str`：REPL 用，格式化为可读表格
- `_extract_tool_result_text(block) -> str`：= `_format_findings_text(parse_findings(raw))` 成功则返回、失败回退 `_raw_tool_result_text`（REPL 行为保持）

### server.py 事件分发

`stream()` 内维护 `pending_writes: dict[tool_use_id -> {file_path, content}]`（回合内 dict，`async with lock` 作用域，回合结束 GC）：

```
收到 AssistantMessage 含 ToolUseBlock(name="Write"):
  file_path = input.file_path
  归一化：abs = realpath(BASE/generated/file_path)
  if commonpath(abs, BASE/generated) == BASE/generated:   # 路径在 generated 内
      rel = abs 相对 BASE/generated 的路径（如 character/Foo.ets）
      pending_writes[tool_use_id] = {file_path: rel, content: input.content}
  # 仍发 tool_use 事件（轨迹轨 pending 节点）

收到 ToolResultBlock:
  if tool_use_id in pending_writes:
      item = pending_writes.pop(tool_use_id)
      yield _sse("file", {path: item.file_path, content: item.content, is_error: block.is_error})
  else:
      raw = _raw_tool_result_text(block)
      findings = parse_findings(raw)
      if findings is not None:
          yield _sse("findings", {findings, is_error: block.is_error})
      else:
          yield _sse("tool_result", {text: raw, is_error: block.is_error})   # review 等纯文本 fallback
```

### SSE 事件

- `file`：`{path, content, is_error}` → 前端追加 file 卡片
- `findings`：`{findings: [...], is_error}` → 前端追加 findings 卡片组
- `tool_result`：保留，review / 生成工具概览（`_format_files` 文本）等 fallback

### 前端 file 卡片

折叠卡片——标题 `path`（相对 `generated/` 根）、展开后 `content` 代码高亮（按扩展名推断，`.ets` → TypeScript/ArkTS 语法，回退纯文本）、复制单文件按钮、`导出该文件` 链接 `<a href="/export?path=<rel>" download>`、`导出整目录` 链接（path 取 dirname，如 `character/Foo.ets` → `character`；若 dirname 为空即文件在 `generated/` 根级，则链接指向整个 `generated` 目录）。首个 file 卡片默认展开，其余折叠。

`is_error=true` 时：卡片标红，`path` 仍显示，content 区域显示"写入失败"（不渲染空代码块）。

### 前端 findings 卡片组

每个 finding 一张——`severity` 左色条（高红 / 中黄 / 低蓝）、`location` 标题、`summary` 副标题、`fix` 高亮块带复制按钮、特有字段键值对追加。按 severity 排序（高 → 中 → 低）。

### 轨迹轨

`tool_use` 仍标 pending；`file` / `findings` / `tool_result` 任一到达即标 ok + tag（"N 个文件" / "N 条 findings" / "完成"）。

### 工具标签同步（index.html）

`TOOL_LABELS` 加 10 个：`generate_character_stats` → "生成角色属性"、`generate_skill_system` → "生成技能系统"、`generate_inventory` → "生成背包"、`generate_enemy_ai` → "生成敌人 AI"、`scaffold_deveco_project` → "脚手架工程"、`analyze_runtime_logs` → "日志分析"、`suggest_performance_fixes` → "性能建议"、`locate_bug` → "Bug 定位"、`check_api_usage` → "API 审查"、`review_arkts_code` → "代码审查"。空状态提示词更新为 4 类示例（生成角色 / 审查代码 / 分析日志 / 脚手架工程）。

### 不抑制生成工具概览 result 卡片

生成工具的 `tool_result`（`_format_files` 输出的"已生成 N 个文件..."可读文本）仍走 `tool_result` fallback 显示一张 result 卡片，与 N 张 file 卡片（来自 N 次 Write）并存。result 卡片作概览，file 卡片是详情，用户可折叠 result。不抑制——抑制需再配对 tool_use 取 name 判断，复杂度不值。

## /export 端点

**路由**：`GET /export?path=<rel>`（path 相对 `generated/` 根，如 `character/Foo.ets` 或 `character`）

**路径解析与穿越防护**：
```
BASE = realpath(BASE_DIR / "generated")
abs = realpath(BASE / path)
if commonpath([abs, BASE]) != BASE:
    return 400 {"error": "路径越界"}
if not abs.exists():
    return 404 {"error": "路径不存在"}
```

**单文件**（`abs.is_file()`）：
- `Content-Type` 按扩展名：`.ets` → `text/plain; charset=utf-8`；`.json` → `application/json`；其他 → `application/octet-stream`
- `Content-Disposition: attachment; filename="<basename>"`
- 直接返回文件字节

**目录**（`abs.is_dir()`）：
- 内存 zip（`zipfile.ZipFile(BytesIO)`）
- `Content-Type: application/zip`
- `Content-Disposition: attachment; filename="<dirname>.zip"`（根目录 → `generated.zip`）
- **zip slip 防护**：每个成员 `arcname = relpath(member, abs)`，校验不以 `/` 开头、不含 `..` 段——违规成员跳过（best-effort，不抛）
- 只打包文件，跳过空目录项

**错误响应**：
- 越界 → `400 {"error": "路径越界"}`
- 不存在 → `404 {"error": "路径不存在"}`
- path 为空 → `400 {"error": "path 参数为空"}`
- zip 构造异常 → `500 {"error": "..."}`（兜底）

**安全**：端点只读 `generated/` 下，不读写 `.git` / `.env` / 项目根其他文件。`commonpath` 断言与 `resolve_scope` 同款。

**实现位置**：`server.py` 新增 `export(request)` handler + `Route("/export", export, methods=["GET"])`。zip 构造抽 `_build_zip(root: Path) -> bytes`。

## 错误处理分层

| 层 | 失败行为 |
|---|---|
| analyzer system_prompt 输出非 JSON | `parse_findings` 容错链失败 → None → server 发 `tool_result` fallback、REPL 原样 |
| analyzer LLM 调用失败 | `analyze_with_context` raise → `@tool` 包装层 catch → `{"content":[{"type":"text","text":"<工具名>失败：{e}"}]}`（Phase 3 既有，不变）|
| server.py `parse_findings` 异常 | try/except 包裹 → None → fallback `tool_result` |
| server.py Write 配对丢失 | SDK 保证配对；若丢失，`pending_writes` 残留项回合结束随 `stream()` GC |
| /export 路径越界 | 400 JSON |
| /export 路径不存在 | 404 JSON |
| /export zip 构造异常 | 500 JSON |
| /export zip slip 成员名违规 | 跳过该成员（best-effort）|
| 前端 file/findings 渲染异常 | 单卡片 try/catch，显示"渲染失败"，不影响其他卡片与对话流 |

## 测试

沿用 Phase 1-3 风格：自带 `main()`、非 pytest、monkeypatch 桩 LLM。

**`analyzers/*_test.py`（4 个，更新）**：
- 断言 system_prompt 含"输出 JSON 数组"要求
- 桩 LLM 返回合法 JSON 数组 → 入口返回该 JSON 字符串
- 桩 LLM 返回非 JSON → 入口仍返回原文（解析责任在 server/main）
- 各工具特有字段在 system_prompt 中体现

**`analyzers/findings_test.py`（新增）**——`parse_findings` 与 `_format_findings_text`：
- 合法 JSON 数组 → list[dict]
- markdown code fence 包裹 → 剥离后成功
- JSON 前后有解释文字 → 提取首个 `[...]` 成功
- 字段别名（`位置` / `correct_usage`）→ 映射
- 非 JSON / JSON 对象（非数组）/ 元素非 dict → None
- `_format_findings_text` → 表格文本含 severity / location / fix

**`main_test.py`（新增或并入）**——`_extract_tool_result_text`：
- findings JSON → 格式化表格文本
- 非 JSON → 原样
- `{files}` JSON → 走原 `_format_files` 路径（不变）

**`server_test.py`（新增）**：
- `/export?path=character/Foo.ets` 单文件 → 200 + 内容 + Content-Type
- `/export?path=character` 目录 → 200 + zip + `application/zip`
- `/export?path=../../etc/passwd` → 400
- `/export?path=nonexistent` → 404
- `/export`（无 path）→ 400
- zip slip fixture → arcname 校验跳过
- file 事件配对：模拟 Write tool_use + tool_result → 断言发 `file` 事件
- findings 事件：分析 tool_result 含 JSON → 断言发 `findings` 事件
- fallback：review 纯文本 tool_result → 断言发 `tool_result` 事件

**前端**：无自动化测试（纯 JS），手动验证清单写入 spec——启动 server，触发各工具，确认卡片渲染 / 复制 / 导出链接 / 轨迹轨标记。

**回归保护**：`generators/framework_test.py`、`tools_review_test.py`、4 个 analyzer `*_test.py` 全部重跑，确保只更新断言、未破坏既有。

## 风险与边界

- **LLM 不稳定输出 JSON**：即使 system_prompt 要求，LLM 可能裹 code fence / 加解释 / 字段名漂移。`parse_findings` 容错链处理，失败回退纯文本。spec 不把结构化当可靠能力——失败时降级为原 `tool_result` 文本卡片
- **Write 拦截依赖 Agent 用 Write 落盘**：若 Agent 拿到 `{files}` 却不 Write（直接展示），file 卡片不出现——合理行为（没落盘就没产物卡片）。system_prompt 约定用 Write
- **路径穿越**：Write `file_path` 与 /export `path` 都做 realpath + commonpath 断言在 `BASE/generated` 内，越界 Write 不缓存、越界 export 返 400
- **zip slip**：成员 `arcname` 取 `relpath` 并校验无 `..` 段，违规跳过
- **内存 zip**：`generated/` 体量小可接受；若未来工程巨大需改流式（YAGNI）
- **Phase 3 契约变更**：4 个 analyzer system_prompt 从纯文本改 JSON，破坏 Phase 3 的 8 个 analyzer 测试断言——spec 显式要求更新 4 个 `*_test.py`，回归可控
- **生成工具零回归**：方案③ 不动 `tools.py` / `generators/`，file 卡片数据来自 Write 拦截，与生成工具包装层解耦

## 主 Agent 改动

- `main.py`：新增 `_raw_tool_result_text`；`_extract_tool_result_text` 改组合 `parse_findings` + `_format_findings_text`；import `analyzers.findings`
- `server.py`：`stream()` 增 `pending_writes` 配对 + 事件分发；新增 `/export` 路由 + `_build_zip`
- `index.html`：`TOOL_LABELS` 同步 10 工具；提示词更新；新增 file / findings 卡片渲染与轨迹轨事件处理
- `analyzers/{performance,bug_location,api_usage,runtime_logs}.py`：仅 system_prompt 加 JSON 输出要求段
- `analyzers/findings.py`：新增
- REPL `print_message` 不变（`_extract_tool_result_text` 内部行为变，输出保持可读表格）

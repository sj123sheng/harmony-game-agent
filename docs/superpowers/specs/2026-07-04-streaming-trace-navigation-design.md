# 流式工具反馈与轨迹定位设计

日期：2026-07-04
阶段：Web 工作台交互增强，承接 [Web 工作台增强设计（Phase 4）](./2026-07-02-web-workbench-design.md) 与 [会话持久化与回放设计](./2026-07-02-session-persistence-replay-design.md)

## 目标

让网页工作台在工具执行期间更早给出可见反馈，并让左侧轨迹轨成为可点击的导航目录：

- 工具调用一开始，右侧详情区立即出现“运行中”工具卡，而不是等工具结果全部返回后才出现详情。
- 工具返回 `file` / `findings` / `tool_result` 后，右侧追加对应详情卡，左侧轨迹节点同步变成完成、失败或结果数量状态。
- 点击左侧轨迹节点时，右侧滚动到对应工具卡或结果详情，并短暂高亮。
- 实时 SSE 与历史回放复用同一渲染路径，避免两套 UI 行为分叉。

## 非目标

- 不做 token 级工具输出流式。当前 Agent SDK 只能在工具完成后返回 `ToolResultBlock`，本期做阶段级流式反馈。
- 不改生成器、分析器或工具函数内部逻辑。
- 不引入 WebSocket；继续使用现有 SSE + `fetch().body.getReader()`。
- 不重做整体视觉风格、布局比例或移动端交互。
- 不做真实进度百分比。除非工具协议未来暴露进度，否则只展示“运行中 / 文件 / N 条 / 完成 / 失败”。
- 不做文件内容边生成边追加。文件卡仍在 `Write` 结果事件到达后一次性展示完整内容。

## 现状

- `server.py` 的 `/chat` 已返回 `StreamingResponse(..., media_type="text/event-stream")`，事件包括 `session_started`、`user`、`text`、`tool_use`、`file`、`findings`、`tool_result`、`done`、`error`。
- `index.html` 已用 `fetch('/chat')` + `reader.read()` 逐块解析 SSE，并在 `handleEvent()` 中分发渲染。
- 普通 `text` 事件已通过 `appendText()` 增量追加到当前 bot 气泡。
- `tool_use` 当前只在右侧追加工具输入卡，并在左侧轨迹轨创建 pending 节点；后续 `file` / `findings` / `tool_result` 到达后，左侧只更新最近的 `pendingNode`。
- 左侧轨迹节点目前不能点击，也没有和右侧卡片建立稳定关联。
- 历史回放通过 `replayEvents()` 逐条调用 `handleEvent()`，与实时流大体同构。

## 推荐方案

采用“事件锚点方案”：以后端现有 SSE 事件为事实来源，前端在渲染时创建稳定的工具阶段对象，把左侧节点和右侧卡片绑定起来。

此方案不要求工具内部持续吐进度，也不要求后端改变事件名。它用当前已经到达的事件边界提供阶段级流式体验：工具一开始就可见，结果一到就定位和更新。

## 架构

### 前端状态

在 `index.html` 新增轻量状态：

```js
let toolSeq = 0;
let activeTool = null;
const toolTargets = new Map();
```

- `toolSeq`：为每个工具阶段生成前端本地 id，如 `tool-1`、`tool-2`。
- `activeTool`：最近一次 `tool_use` 创建的工具阶段对象。
- `toolTargets`：保存 `toolId -> { node, primaryCard, detailCards }`，供定位、状态更新和回放复用。

工具阶段对象结构：

```js
{
  id: 'tool-3',
  node: HTMLElement,
  toolCard: HTMLElement,
  primaryTargetId: 'tool-3-card',
  status: 'pending' | 'ok' | 'err'
}
```

### 右侧卡片职责

- `addToolCard(name, input, meta)`：创建并返回工具运行卡。卡片标题显示“调用 · 工具名 · 运行中”，正文保留输入 JSON。
- `addFileCard(path, content, isError, meta)`：创建并返回文件卡。若存在当前工具阶段，把该文件卡登记为详情目标。
- `addFindingsCards(findings, isError, meta)`：创建 findings 分组或多张 findings 卡，并返回第一个可定位卡片。
- `addResultCard(text, isError, meta)`：创建并返回工具结果卡。

现有函数可以保持主要 DOM 结构，只需要从“只追加、不返回”改为“追加后返回关键卡片 DOM”。

### 左侧轨迹职责

`traceNode(label, status, tag, targetId)` 增加可选目标：

- 有 `targetId` 时，节点加 `role="button"`、`tabindex="0"` 和点击/回车/空格定位行为。
- 无 `targetId` 时保持纯展示。
- 后续详情卡出现后，可通过 `linkTraceToCard(node, card)` 更新目标。

定位函数：

```js
function focusCard(cardId) {
  const el = document.getElementById(cardId);
  if (!el) return;
  el.scrollIntoView({ behavior: prefersReducedMotion() ? 'auto' : 'smooth', block: 'start' });
  el.classList.add('target-flash');
  setTimeout(() => el.classList.remove('target-flash'), 1400);
}
```

### 数据流

#### `tool_use`

1. `finalizeBot()` 结束文本气泡。
2. `toolSeq += 1`，创建 `toolId`。
3. 右侧立即创建运行中工具卡，状态为“运行中”。
4. 左侧创建 pending 节点并绑定到工具卡。
5. `activeTool` 指向该工具阶段。

#### `file`

1. 若存在 `activeTool`，把工具卡状态改为成功或失败，左侧 tag 改为“文件”或“失败”。
2. 追加文件卡。
3. 左侧节点优先定位到文件卡；如果一个工具产生多个文件，默认定位到第一个文件卡。
4. 保留运行卡，作为输入参数和执行入口的上下文。

#### `findings`

1. 若存在 `activeTool`，把工具卡状态改为成功或失败，左侧 tag 改为 `N 条` 或“失败”。
2. 追加 findings 卡片组。
3. 左侧节点定位到 findings 分组或第一张 finding 卡。
4. 空 findings 仍生成“无发现”卡，节点定位到该提示卡。

#### `tool_result`

1. 若存在 `activeTool`，把工具卡状态改为完成或失败。
2. 追加结果卡。
3. 如果该工具还没有更具体的文件或 findings 目标，左侧节点定位到结果卡；否则继续定位到更具体的详情卡。
4. 清理 `activeTool`，避免下一轮结果误绑定。

#### `done` / `error`

- `done` 时，如果仍有 pending 工具，收尾为“完成”并保留定位到运行卡。
- `error` 时，如果仍有 pending 工具，标记为失败并定位到错误提示卡或运行卡。
- 两者都调用 `finalizeBot()`，保持现有回合收尾行为。

## 后端协议

本期默认不新增事件名。若实现时发现前端只靠顺序无法稳定绑定，可为以下事件补充 `tool_use_id`：

- `tool_use`: `{name, input, tool_use_id}`
- `file`: `{path, content, is_error, tool_use_id}`
- `tool_result`: `{text, is_error, tool_use_id}`

`findings` 通常来自非 `Write` 工具的 `ToolResultBlock`，也可补 `tool_use_id`。补字段是向后兼容的：旧前端忽略新字段，新前端优先用字段、缺失时按事件顺序降级。

推荐先按前端顺序绑定实现；只有测试发现并发或多工具交错导致误绑定时，再补 `tool_use_id`。

## 历史回放

`replayEvents()` 继续逐条调用 `handleEvent()`。由于实时和回放共享 `handleEvent()`，以下行为天然一致：

- `tool_use` 回放时创建运行卡与左侧节点。
- 后续 `file` / `findings` / `tool_result` 回放时更新同一工具阶段。
- 点击左侧节点可定位到回放出的右侧卡片。

旧 JSONL 历史没有任何新字段也可工作，因为前端以事件顺序建立关联。若遇到没有 `tool_use` 的孤立详情事件，则直接追加详情卡，并让左侧保持当前降级行为，不制造虚假工具节点。

## 视觉与可访问性

- 运行中工具卡标题增加状态标签：`运行中`、`完成`、`失败`、`文件`、`N 条`。
- 左侧可点击节点显示轻微 hover 状态，避免和不可点击文本混淆。
- 定位目标短暂出现“扫描框”式高亮，用边框和阴影即可，不增加额外装饰图形。
- 尊重 `prefers-reduced-motion: reduce`：关闭平滑滚动，缩短或移除高亮过渡。
- 节点支持键盘激活：`Enter` 和 `Space` 触发定位。

## 错误处理

- 结果事件缺失：`done` 到达时把 pending 节点从“运行中”改为“完成”，定位仍指向运行卡。
- 工具失败：右侧运行卡和左侧节点都标红，结果卡保留错误内容。
- 定位目标不存在：`focusCard()` 静默返回，不影响流式渲染。
- 多文件结果：节点定位到第一张文件卡，其他文件仍按顺序追加。
- 回放事件异常：保留现有 `try/catch` 跳过坏事件，避免一个坏事件打断整个会话回放。

## 测试与验收

### 自动回归

- 运行现有 `server_test.py`，确认 SSE 事件顺序和既有 `file` / `findings` / `tool_result` 分发不变。
- 若补充 `tool_use_id`，新增或更新测试，断言相关事件包含同一工具调用 id，且旧字段仍存在。

### 浏览器验收

- 发送会触发工具的提示词后，右侧应先出现运行中工具卡，左侧 pending 节点同步出现。
- 工具写文件后，右侧文件卡出现，左侧节点 tag 变为“文件”，点击左侧节点滚动到文件卡。
- 分析工具返回 findings 后，左侧节点显示 `N 条`，点击定位到 findings 卡。
- 纯文本工具结果返回后，左侧节点显示“完成”，点击定位到结果卡。
- 切换到历史会话回放后，点击左侧轨迹节点仍能定位右侧详情。
- 在减少动态效果偏好下，定位仍可用，但不使用平滑滚动或长过渡。

## 实施边界

优先修改 `index.html`，保持后端协议稳定。只有当顺序绑定无法覆盖真实事件流时，才在 `server.py` 为事件补充 `tool_use_id`。这样可以把风险集中在前端交互层，同时保持已有工具、生成器、分析器和会话持久化逻辑不变。

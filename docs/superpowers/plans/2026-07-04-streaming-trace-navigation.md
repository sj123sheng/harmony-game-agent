# Streaming Trace Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the web workbench show tool execution as stage-level streaming feedback and let left trace nodes jump to the matching right-side detail cards.

**Architecture:** Keep the backend SSE event names unchanged and implement the interaction layer in `index.html`. Each `tool_use` creates a front-end tool phase object with a trace node and a right-side running card; later `file`, `findings`, and `tool_result` events update that phase and link the trace node to the most useful detail card. Real-time streaming and JSONL replay both keep using `handleEvent()` so behavior stays consistent.

**Tech Stack:** Python 3.12 / Starlette SSE / native browser JavaScript / HTML + CSS / existing `server_test.py` regression tests / browser preview verification.

## Global Constraints

- Do not touch `generators/deveco_project.py` or `generators/deveco_project_test.py`; they contain unrelated user changes in the working tree.
- Do not introduce WebSocket; continue using existing SSE + `fetch().body.getReader()`.
- Do not change generator, analyzer, or tool internals.
- Do not implement token-level tool output streaming; implement stage-level feedback only.
- Do not stream file contents before `Write` completes; file cards still render complete content when the `file` event arrives.
- Prefer modifying `index.html`; only change `server.py` if manual verification proves event-order binding is insufficient.
- Keep copy in Chinese and match the existing UI tone.
- If committing implementation work, include `Prompt: 整体UI设计交互流式返回与左侧轨迹定位右侧详情` in the commit message, plus the required co-author trailer.

---

## File Structure

- Modify: `index.html`
  - CSS: clickable trace node styles, target flash highlight, running/failed tool-card states, reduced-motion behavior.
  - JavaScript state: `toolSeq`, `activeTool`, `toolTargets`, and helpers for phase creation, linking, status updates, and focus.
  - Existing render functions: make `addToolCard`, `addResultCard`, `addFileCard`, and `addFindingsCards` return key DOM nodes.
  - Existing event dispatcher: update `handleEvent()` to create and settle tool phases.
  - Existing reset/replay paths: clear tool state in `clearAll()` and rely on `replayEvents()` using `handleEvent()`.
- Verify only: `server_test.py`
  - Run existing tests to confirm no backend SSE regression.
- No changes: `server.py`
  - Keep as fallback only if implementation discovers ordering is insufficient.

---

### Task 1: Add Trace Navigation Styles And Helpers

**Files:**
- Modify: `index.html:127-147`
- Modify: `index.html:383-385`
- Modify: `index.html:464-471`
- Modify: `index.html:500-501`
- Modify: `index.html:560-566`

**Interfaces:**
- Consumes: existing `threadEl`, `railEl`, `.t-node`, `.tool`, `.result`, `clearAll()`.
- Produces:
  - `let toolSeq: number`
  - `let activeTool: ToolPhase | null`
  - `const toolTargets: Map<string, { node: HTMLElement, primaryCard: HTMLElement | null, detailCards: HTMLElement[] }>`
  - `function prefersReducedMotion(): boolean`
  - `function ensureCardId(card: HTMLElement, prefix: string): string`
  - `function focusCard(cardId: string): void`
  - `function clearToolState(): void`

- [ ] **Step 1: Add the CSS states before `@keyframes nodein`**

Insert this block after the existing `.t-node .t-tag` rule:

```css
  .t-node.is-clickable {
    cursor: pointer; border-radius: 7px; margin-left: -6px; padding-left: 20px;
    transition: background .18s, color .18s;
  }
  .t-node.is-clickable:hover,
  .t-node.is-clickable:focus-visible {
    background: rgba(124,92,255,.1); color: var(--text); outline: none;
  }
  .tool.tool-running { border-left: 2px solid var(--warn); }
  .tool.tool-ok { border-left: 2px solid var(--ok); }
  .tool.tool-err { border-left: 2px solid var(--err); }
  .tool-status {
    margin-left: auto; color: var(--muted-2); font-size: 10px;
    text-transform: uppercase; letter-spacing: .08em;
  }
  .target-flash {
    animation: targetFlash 1.4s ease-out both;
  }
  @keyframes targetFlash {
    0% { box-shadow: 0 0 0 0 rgba(124,92,255,.75); border-color: var(--accent); }
    100% { box-shadow: 0 0 0 12px rgba(124,92,255,0); }
  }
```

- [ ] **Step 2: Extend the reduced-motion media rule**

Replace the existing reduced-motion block with this exact block:

```css
  @media (prefers-reduced-motion: reduce) {
    *, *::before, *::after { animation: none !important; transition: none !important; }
    .target-flash { box-shadow: 0 0 0 2px var(--accent); }
  }
```

- [ ] **Step 3: Add tool phase state near existing global state**

Insert after `let currentAbort = null;`:

```js
let toolSeq = 0;
let activeTool = null;
const toolTargets = new Map();
```

- [ ] **Step 4: Add navigation helpers after `scrollRail()`**

Insert this block after `function scrollRail() { railEl.scrollTop = railEl.scrollHeight; railEl.scrollLeft = railEl.scrollWidth; }`:

```js
function prefersReducedMotion() {
  return window.matchMedia('(prefers-reduced-motion: reduce)').matches;
}

function ensureCardId(card, prefix) {
  if (!card.id) card.id = prefix + '-' + String(toolSeq) + '-' + Math.random().toString(36).slice(2, 8);
  return card.id;
}

function focusCard(cardId) {
  const el = document.getElementById(cardId);
  if (!el) return;
  el.scrollIntoView({ behavior: prefersReducedMotion() ? 'auto' : 'smooth', block: 'start' });
  el.classList.remove('target-flash');
  void el.offsetWidth;
  el.classList.add('target-flash');
  if (!prefersReducedMotion()) setTimeout(() => el.classList.remove('target-flash'), 1400);
}

function clearToolState() {
  toolSeq = 0;
  activeTool = null;
  toolTargets.clear();
}
```

- [ ] **Step 5: Replace `Math.random()` in `ensureCardId` with deterministic sequence before committing**

Because ids only need to be unique within one page session, replace the helper from Step 4 with this deterministic version:

```js
let cardSeq = 0;
function ensureCardId(card, prefix) {
  if (!card.id) {
    cardSeq += 1;
    card.id = prefix + '-' + String(cardSeq);
  }
  return card.id;
}
```

Also insert `let cardSeq = 0;` next to `let toolSeq = 0;`.

- [ ] **Step 6: Clear tool state from `clearAll()`**

Replace the final assignment line inside `clearAll()`:

```js
  turnNo = 0; currentGroup = null; pendingNode = null; currentBot = null; textOpen = false;
```

with:

```js
  turnNo = 0; currentGroup = null; pendingNode = null; currentBot = null; textOpen = false;
  clearToolState();
```

- [ ] **Step 7: Run backend regression as a safety check**

Run: `uv run python server_test.py`

Expected: exits `0` and prints existing `[OK]` lines. If it fails, inspect whether the failure is unrelated to `index.html`; do not edit unrelated generator files.

- [ ] **Step 8: Commit Task 1**

```bash
git add index.html
git commit -m "feat(ui): 增加轨迹定位样式与状态基础" \
  -m "新增可点击轨迹节点、目标高亮、工具阶段状态与清理辅助，为后续流式工具反馈提供前端基础。" \
  -m "Prompt: 整体UI设计交互流式返回与左侧轨迹定位右侧详情" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Create Tool Phases On `tool_use`

**Files:**
- Modify: `index.html:702-719`
- Modify: `index.html:845-877`
- Modify: `index.html:890-952`

**Interfaces:**
- Consumes from Task 1: `toolSeq`, `activeTool`, `toolTargets`, `ensureCardId(card, prefix)`, `focusCard(cardId)`.
- Produces:
  - `function bindTraceNode(node: HTMLElement, cardId: string): void`
  - `function linkTraceToCard(phase: ToolPhase | null, card: HTMLElement | null, preferDetail?: boolean): void`
  - `function createToolPhase(name: string, input: object): ToolPhase | null`
  - `function setToolPhaseStatus(phase: ToolPhase | null, status: 'pending' | 'ok' | 'err', tag: string): void`
  - `addToolCard(name, input, meta) -> HTMLElement`

- [ ] **Step 1: Update `addToolCard()` to return the card and show status**

Replace the full existing `addToolCard(name, input)` function with:

```js
function addToolCard(name, input, meta = {}) {
  finalizeBot();
  const div = document.createElement('div');
  div.className = 'tool tool-running open';
  const head = document.createElement('div');
  head.className = 'tool-head';
  head.innerHTML = '<span class="t-glyph">▣</span><span class="t-name"></span><span class="tool-status"></span><span class="t-chev">▶</span>';
  head.querySelector('.t-name').textContent = '调用 · ' + shortToolName(name);
  head.querySelector('.tool-status').textContent = meta.status || '运行中';
  const body = document.createElement('div');
  body.className = 'tool-body';
  const pre = document.createElement('pre');
  pre.textContent = JSON.stringify(input, null, 2);
  body.appendChild(pre);
  head.addEventListener('click', () => div.classList.toggle('open'));
  div.appendChild(head); div.appendChild(body);
  messagesEl.appendChild(div);
  scrollThread();
  return div;
}
```

- [ ] **Step 2: Replace `traceNode()` with clickable target support**

Replace the full existing `traceNode(label, status, tag)` function with:

```js
function bindTraceNode(node, cardId) {
  node.dataset.targetId = cardId;
  node.classList.add('is-clickable');
  node.setAttribute('role', 'button');
  node.setAttribute('tabindex', '0');
  node.setAttribute('aria-label', '定位到右侧详情');
  if (node.dataset.bound === '1') return;
  node.dataset.bound = '1';
  node.addEventListener('click', () => focusCard(node.dataset.targetId));
  node.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      focusCard(node.dataset.targetId);
    }
  });
}

function traceNode(label, status, tag, targetId) {
  if (!currentGroup) return null;
  const spine = currentGroup.querySelector('.spine');
  const node = document.createElement('div');
  node.className = 't-node ' + status;
  node.innerHTML = '<span class="t-dot"></span><span class="t-label"></span>' + (tag ? '<span class="t-tag"></span>' : '');
  node.querySelector('.t-label').textContent = label;
  if (tag) node.querySelector('.t-tag').textContent = tag;
  if (targetId) bindTraceNode(node, targetId);
  spine.appendChild(node);
  scrollRail();
  return node;
}
```

- [ ] **Step 3: Add phase helpers after `traceNode()`**

Insert this block immediately after the new `traceNode()`:

```js
function linkTraceToCard(phase, card, preferDetail = true) {
  if (!phase || !card) return;
  const cardId = ensureCardId(card, phase.id + '-card');
  const target = toolTargets.get(phase.id);
  if (target) {
    if (!target.primaryCard || preferDetail) target.primaryCard = card;
    target.detailCards.push(card);
  }
  phase.primaryTargetId = cardId;
  bindTraceNode(phase.node, cardId);
}

function setToolPhaseStatus(phase, status, tag) {
  if (!phase) return;
  phase.status = status;
  phase.toolCard.classList.remove('tool-running', 'tool-ok', 'tool-err');
  phase.toolCard.classList.add(status === 'err' ? 'tool-err' : status === 'ok' ? 'tool-ok' : 'tool-running');
  const statusEl = phase.toolCard.querySelector('.tool-status');
  if (statusEl) statusEl.textContent = tag;
  phase.node.className = 't-node ' + status + (phase.node.dataset.targetId ? ' is-clickable' : '');
  const tagEl = phase.node.querySelector('.t-tag');
  if (tagEl) tagEl.textContent = tag;
}

function createToolPhase(name, input) {
  if (!currentGroup) return null;
  toolSeq += 1;
  const id = 'tool-' + String(toolSeq);
  const toolCard = addToolCard(name, input, { status: '运行中' });
  const cardId = ensureCardId(toolCard, id + '-card');
  const node = traceNode(shortToolName(name), 'pending', '运行中', cardId);
  const phase = { id, node, toolCard, primaryTargetId: cardId, status: 'pending' };
  toolTargets.set(id, { node, primaryCard: toolCard, detailCards: [] });
  activeTool = phase;
  return phase;
}
```

- [ ] **Step 4: Update `handleEvent('tool_use')` to create a phase**

Replace this existing branch:

```js
    case 'tool_use':
      finalizeBot();
      addToolCard(data.name, data.input);
      pendingNode = traceNode(shortToolName(data.name), 'pending', '运行中');
      break;
```

with:

```js
    case 'tool_use':
      finalizeBot();
      activeTool = createToolPhase(data.name, data.input);
      pendingNode = activeTool ? activeTool.node : null;
      break;
```

- [ ] **Step 5: Run backend regression again**

Run: `uv run python server_test.py`

Expected: exits `0`. This task only edits `index.html`, so backend tests should remain unchanged.

- [ ] **Step 6: Use browser preview to verify the right-side running card appears**

Start the app with the preview tool configured for `uv run python server.py` on port `8000`. In the browser, submit a prompt that triggers a tool, for example:

```text
审查这段 ArkTS 代码：@Component struct HealthBar { build() { Text('HP').fontSize(20) } }
```

Expected before the final tool result arrives:

```text
Left rail: 代码审查 · 运行中
Right thread: 调用 · 代码审查 · 运行中
```

- [ ] **Step 7: Commit Task 2**

```bash
git add index.html
git commit -m "feat(ui): 工具调用开始即创建运行卡" \
  -m "tool_use 事件创建前端工具阶段对象，立即绑定左侧轨迹节点与右侧运行中工具卡。" \
  -m "Prompt: 整体UI设计交互流式返回与左侧轨迹定位右侧详情" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Link Result Detail Cards To Trace Nodes

**Files:**
- Modify: `index.html:721-778`
- Modify: `index.html:784-834`
- Modify: `index.html:890-952`

**Interfaces:**
- Consumes from Task 2: `activeTool`, `linkTraceToCard(phase, card, preferDetail)`, `setToolPhaseStatus(phase, status, tag)`.
- Produces:
  - `addResultCard(text, isError, meta) -> HTMLElement`
  - `addFileCard(path, content, isError, meta) -> HTMLElement`
  - `addFindingsCards(findings, isError, meta) -> HTMLElement | null`
  - Settled event behavior for `file`, `findings`, `tool_result`, `done`, and `error`.

- [ ] **Step 1: Make `addResultCard()` return its card**

Replace the end of `addResultCard()`:

```js
  messagesEl.appendChild(div);
  scrollThread();
}
```

with:

```js
  messagesEl.appendChild(div);
  scrollThread();
  return div;
}
```

- [ ] **Step 2: Make `addFileCard()` return its card**

Replace the end of `addFileCard()`:

```js
  messagesEl.appendChild(div);
  scrollThread();
}
```

with:

```js
  messagesEl.appendChild(div);
  scrollThread();
  return div;
}
```

- [ ] **Step 3: Make `addFindingsCards()` return a target card**

Inside the empty findings branch, replace:

```js
    messagesEl.appendChild(div);
    scrollThread();
    return;
```

with:

```js
    messagesEl.appendChild(div);
    scrollThread();
    return div;
```

Before the `for (const f of sorted) {` loop, insert:

```js
  let firstCard = null;
```

Inside the loop, immediately after `const div = document.createElement('div');`, insert:

```js
    if (!firstCard) firstCard = div;
```

After the loop, replace the final `}` of the function with:

```js
  return firstCard;
}
```

Keep the existing `scrollThread()` inside the loop unchanged.

- [ ] **Step 4: Update `handleEvent('file')`**

Replace the full `file` branch with:

```js
    case 'file': {
      finalizeBot();
      const card = addFileCard(data.path, data.content, data.is_error);
      if (activeTool) {
        setToolPhaseStatus(activeTool, data.is_error ? 'err' : 'ok', data.is_error ? '失败' : '文件');
        linkTraceToCard(activeTool, card, true);
      } else if (pendingNode) {
        pendingNode.className = 't-node ' + (data.is_error ? 'err' : 'ok');
        const tag = pendingNode.querySelector('.t-tag');
        if (tag) tag.textContent = data.is_error ? '失败' : '文件';
      }
      break;
    }
```

- [ ] **Step 5: Update `handleEvent('findings')`**

Replace the full `findings` branch with:

```js
    case 'findings': {
      finalizeBot();
      const findings = data.findings || [];
      const card = addFindingsCards(findings, data.is_error);
      if (activeTool) {
        setToolPhaseStatus(activeTool, data.is_error ? 'err' : 'ok', data.is_error ? '失败' : `${findings.length} 条`);
        linkTraceToCard(activeTool, card, true);
        activeTool = null;
        pendingNode = null;
      } else if (pendingNode) {
        pendingNode.className = 't-node ' + (data.is_error ? 'err' : 'ok');
        const tag = pendingNode.querySelector('.t-tag');
        if (tag) tag.textContent = `${findings.length} 条`;
        pendingNode = null;
      }
      break;
    }
```

- [ ] **Step 6: Update `handleEvent('tool_result')`**

Replace the full `tool_result` branch with:

```js
    case 'tool_result': {
      finalizeBot();
      const card = addResultCard(data.text || '', data.is_error);
      if (activeTool) {
        setToolPhaseStatus(activeTool, data.is_error ? 'err' : 'ok', data.is_error ? '失败' : '完成');
        const target = toolTargets.get(activeTool.id);
        linkTraceToCard(activeTool, card, !(target && target.detailCards.length));
        activeTool = null;
        pendingNode = null;
      } else if (pendingNode) {
        pendingNode.className = 't-node ' + (data.is_error ? 'err' : 'ok');
        const tag = pendingNode.querySelector('.t-tag');
        if (tag) tag.textContent = data.is_error ? '失败' : '完成';
        pendingNode = null;
      }
      break;
    }
```

- [ ] **Step 7: Update `done` and `error` branches**

Replace the existing `done` branch with:

```js
    case 'done':
      finalizeBot();
      if (activeTool) {
        setToolPhaseStatus(activeTool, 'ok', '完成');
        activeTool = null;
      }
      if (pendingNode) { pendingNode.className = 't-node ok'; pendingNode = null; }
      break;
```

Replace the existing `error` branch with:

```js
    case 'error':
      finalizeBot();
      addErrorBar(data.message || '未知错误');
      if (activeTool) {
        setToolPhaseStatus(activeTool, 'err', '失败');
        activeTool = null;
      }
      if (pendingNode) { pendingNode.className = 't-node err'; pendingNode = null; }
      break;
```

- [ ] **Step 8: Run backend regression**

Run: `uv run python server_test.py`

Expected: exits `0` with existing stream tests passing.

- [ ] **Step 9: Verify detail linking in browser preview**

Use preview tools to submit these two prompts in separate sessions:

```text
审查这段 ArkTS 代码：@Component struct HealthBar { build() { Text('HP').fontSize(20) } }
```

```text
生成一个战士角色属性系统，等级上限 60，并审查它
```

Expected:

```text
- analysis/review result: left node changes from 运行中 to 完成 or N 条, clicking it scrolls to the result/findings card.
- generation result: left node changes to 文件 when Write emits a file card, clicking it scrolls to the first file card.
```

- [ ] **Step 10: Commit Task 3**

```bash
git add index.html
git commit -m "feat(ui): 绑定轨迹节点与工具结果卡" \
  -m "file、findings、tool_result 事件更新工具阶段状态，并把左侧轨迹节点定位到右侧详情卡。" \
  -m "Prompt: 整体UI设计交互流式返回与左侧轨迹定位右侧详情" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Verify Replay, Accessibility, And Final Regression

**Files:**
- Modify: `index.html:599-631` only if replay exposes a bug.
- Verify: `server_test.py`
- Verify: browser preview at `http://127.0.0.1:8000`

**Interfaces:**
- Consumes from Task 3: all event branches use `handleEvent()` for real-time and replay.
- Produces: verified behavior for real-time stream, replay, keyboard navigation, and reduced-motion mode.

- [ ] **Step 1: Run full backend regression**

Run: `uv run python server_test.py`

Expected: exits `0`. Required evidence includes the stream tests:

```text
[OK] test_stream_file_event_for_write
[OK] test_stream_findings_event_for_json_result
[OK] test_stream_tool_result_fallback_for_plain_text
[OK] test_stream_findings_marker_from_generator_tool_result
```

- [ ] **Step 2: Start browser preview**

Use the preview server configuration for:

```bash
uv run python server.py
```

Expected server log includes:

```text
[server] Agent 工作台启动，会话按需创建
```

- [ ] **Step 3: Verify click navigation with a live tool run**

In the preview page, submit:

```text
审查这段 ArkTS 代码：@Component struct HealthBar { build() { Text('HP').fontSize(20) } }
```

Use preview snapshot/inspect/click to verify:

```text
- A left trace node with a tool label appears.
- A right running tool card appears before the final result card.
- After result arrives, the left node is not pending.
- Clicking the left node scrolls the right thread to the linked card.
- The linked card receives class target-flash or the reduced-motion fallback style.
```

- [ ] **Step 4: Verify keyboard navigation**

Focus a clickable `.t-node` with preview eval or keyboard navigation and press `Enter`, then `Space`.

Expected:

```text
- Both keys trigger the same target-card focus behavior as click.
- Browser console has no JavaScript errors.
```

- [ ] **Step 5: Verify replay navigation**

Reload the preview page or switch away and back to the latest session from the session list.

Expected:

```text
- Replay recreates the left trace node and right detail cards.
- Clicking the replayed left node scrolls to the replayed right detail card.
- No duplicate empty turns are created during replay.
```

If replay fails because `activeTool` is cleared too early, modify only the relevant `handleEvent()` branch. Do not create a separate replay renderer.

- [ ] **Step 6: Verify reduced motion**

Use preview resize or eval to emulate reduced motion if available. If the preview tool cannot emulate it, run this console inspection instead:

```js
window.matchMedia('(prefers-reduced-motion: reduce)').matches
```

Expected under reduced motion:

```text
- `focusCard()` uses behavior `auto`.
- The interface remains usable without relying on animation.
```

- [ ] **Step 7: Inspect console and network**

Use preview console logs and network tools.

Expected:

```text
- Console: no uncaught TypeError or DOM errors.
- Network: `/chat` returns 200 for valid prompts.
- No failed static asset requests that block the app shell.
```

- [ ] **Step 8: Commit Task 4 if fixes were needed**

If Task 4 required code changes, commit them:

```bash
git add index.html
git commit -m "fix(ui): 完善轨迹定位回放与可访问性" \
  -m "修正实时与历史回放中的工具阶段收尾，确认点击和键盘定位右侧详情一致。" \
  -m "Prompt: 整体UI设计交互流式返回与左侧轨迹定位右侧详情" \
  -m "Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

If no code changes were needed, do not create an empty commit.

---

## Self-Review

- Spec coverage: Task 1 covers visual states, reduced motion, and state reset. Task 2 covers immediate running tool cards and clickable trace nodes. Task 3 covers `file` / `findings` / `tool_result` linking, status updates, and missing-result cleanup. Task 4 covers real-time, replay, keyboard navigation, reduced motion, console, network, and backend regression evidence.
- Scope: This plan stays in `index.html` and does not change tool internals, generators, analyzers, or WebSocket/SSE architecture.
- Type consistency: `activeTool`, `toolTargets`, `linkTraceToCard`, `setToolPhaseStatus`, and card-returning render functions are defined before later tasks consume them.
- Dirty workspace: The plan explicitly avoids `generators/deveco_project.py` and `generators/deveco_project_test.py`.

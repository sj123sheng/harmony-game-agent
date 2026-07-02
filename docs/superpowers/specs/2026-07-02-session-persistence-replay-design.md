# 多会话持久化与历史回放设计（Phase 5）

日期：2026-07-02
阶段：第五阶段（多会话持久化与历史回放），承接 [Phase 4：Web 工作台增强](./2026-07-02-web-workbench-design.md)

## 目标

让网页工作台支持多会话：用户可同时维护多个开发上下文（不同组件/工程），切换会话继续对话，并回看任意历史会话的完整卡片流（text/tool_use/file/findings/tool_result）。
- 后端：每会话一个 `ClaudeSDKClient` 实例常驻，按 `session_id` 路由；闲置 LRU 回收，续聊时靠 SDK `resume` 重建
- 持久化：每会话事件流追加写 `./sessions/<session_id>.jsonl`（每行一条 SSE 事件，与 `/chat` 流同构），回放近乎免费
- 前端：左侧 rail 顶部加折叠会话列表区；切换会话 fetch 历史事件逐条重放；新建/删除会话
- 续聊：常驻 client 直接 `query(prompt, session_id)`；被回收后 `ClaudeSDKClient(options/resume=session_id)` 重建

## 非目标

- 不做 LLM 摘要标题（首条 prompt 前 40 字，YAGNI）
- 不做会话搜索/全文检索/标签分类（留未来）
- 不做跨设备同步/云端存储（本地 `./sessions/` 即可）
- 不做会话导入/导出文件（已有 `/export` 打包 generated/，会话级导出留未来）
- 不做 `Edit` 工具的 diff 回放（Phase 4 已知边界，延续）
- 不做多用户/鉴权（单用户本地工具）
- 不改 10 个工具与 generators/analyzers（零回归）
- 不改 `build_options()` 签名（server 在返回的 options 上按需设 `resume` 字段）
- 不配 SDK `session_store`（用本地磁盘 resume 即可，不引入自定义存储适配）

## 现状

- `server.py`：单全局 `client: ClaudeSDKClient`，`lifespan` 启动 connect 一次常驻；`POST /chat` 用 `async with lock` 串行化，`stream()` 把 `AssistantMessage`/`UserMessage`/`ResultMessage` 转 SSE（text/tool_use/file/findings/tool_result/done/error）。无会话概念，刷新页面即丢上下文
- `ClaudeSDKClient.query(prompt, session_id="default")`（SDK `client.py:284`）支持按 `session_id` 标识会话；`ClaudeAgentOptions.resume: str`（SDK `types.py:1698`）支持按 session_id 从本地磁盘加载历史续聊。不配 `session_store` 时走本地磁盘 resume
- `index.html`：左侧 rail 是轨迹轨（回合 + 工具调用链），无会话列表；对话区靠 SSE 事件实时追加 `addRow`/`addFileCard`/`addFindingsCards` 渲染
- `./generated/` 已存在并在 git 跟踪；`./sessions/` 新增，需 `.gitignore`

## 架构

### 核心改动

1. **会话路由**：`server.py` 维护 `sessions: dict[str, ClientEntry]`，`ClientEntry` 持 `client`/`last_used`/`title`/`created_at`。`POST /chat` body 加可选 `session_id`：空→新建（生成 UUID，`ClaudeSDKClient()` 不设 resume）；非空→复用 `sessions[id].client`，若不存在（被回收）则 `ClaudeSDKClient(options/resume=id)` 重建
2. **事件流持久化**：`chat()` 的 `stream()` 每发一 SSE 事件，同时 append 一行 JSON 到 `./sessions/<id>.jsonl`。格式 `{"event":"<type>","data":{...}}`，与 `_sse` 同构
3. **会话管理 API**：`GET /sessions` 列表、`GET /sessions/<id>/events` 回放流、`DELETE /sessions/<id>` 删
4. **前端会话列表 + 回放**：rail 顶部折叠区列会话；切换会话 fetch events 逐条重放；新建清空画布

### 模块划分

```
sessions_store.py          ← 新增：纯 IO，无业务无 LLM
                              append_event(sid, event, data)
                              load_events(sid) -> list[(event, data)]  # 跳过损坏行
                              list_sessions() -> [{id, title, created_at, mtime, bytes}]
                              delete_session(sid)
                              title 来自首条 prompt 前 40 字（首事件 session_started 写入）

server.py                  ← sessions dict + ClientEntry + LRU 回收 + /sessions 路由
                              chat() 加 session_id 参数 + stream() 每事件 append
                              build_options() 不动；server 在 options 上按需 set resume

index.html                 ← rail 顶部折叠会话列表 + 回放 fetch 重放 + 新建/删除/切换

main.py / tools.py / generators/ / analyzers/   ← 零改动
```

### 职责边界

- `sessions_store.py`：纯文件 IO（append/load/list/delete），无 SDK、无业务。供 `server.py` 用
- `server.py`：会话生命周期（client dict + LRU）+ SSE 分发 + 事件 append + /sessions 路由
- `index.html`：会话列表 UI + 回放渲染，无业务逻辑
- `main.py`/`tools.py`/`generators/`/`analyzers/`：零改动

### session_id 来源

server 自生成 UUID（`uuid.uuid4().hex`），通过 `client.query(prompt, session_id=uuid)` 传给 SDK。SDK 用此 id 标记会话；本地磁盘 resume 也以此 id 为 key。列表/续聊/删除均基于此 id，无需等 SDK 返回。

### ClientEntry 与 LRU

```python
@dataclass
class ClientEntry:
    client: ClaudeSDKClient
    last_used: float          # time.monotonic()
    title: str
    created_at: str           # ISO 本地时区

# 回收策略（每次 chat() 进入前 sweep）：
# - 闲置 > 10 min（last_used 距今 > 600s）→ disconnect + 从 dict 移除 client（JSONL 保留）
# - dict size > 8 → 按 last_used 最旧者 disconnect 移除，直到 <= 8
# 回收后该 session_id 再次请求 → resume 重建
```

并发：`async with lock` 仍串行化整个 query（既有行为不变），LRU sweep 在 lock 内做。

## 事件流 JSONL 格式

每行一条事件，与 `_sse(event, data)` 同构：

```jsonl
{"event":"session_started","data":{"session_id":"abc123","title":"生成战士角色属性系统"}}
{"event":"text","data":{"text":"好的，我来..."}}
{"event":"tool_use","data":{"name":"mcp__harmony_tools__generate_character_stats","input":{...}}}
{"event":"file","data":{"path":"character/CharacterStats.ets","content":"...","is_error":false}}
{"event":"findings","data":{"findings":[...],"is_error":false}}
{"event":"tool_result","data":{"text":"...","is_error":false}}
{"event":"done","data":{"is_error":false,"cost":0.012}}
```

- `session_started`：每会话首行，由 server 在 `stream()` 开头写入（含 session_id + title）
- 回放：`load_events(sid)` 逐行 `json.loads`，跳过解析失败的行，返回 `[(event, data), ...]`；前端按顺序分发到现有渲染函数
- 回放不重发 `session_started`（前端用它设当前会话上下文，不渲染为卡片）

## SSE 事件与 API

### `POST /chat`（改动）

body 加 `session_id: str | null`：
- `null`/缺省 → 新建：生成 UUID，`ClaudeSDKClient(options=build_options())` connect，注册 `sessions[uuid]`，建 `./sessions/<uuid>.jsonl`
- 非空且 `sessions[id]` 存在 → 复用 client（更新 last_used）
- 非空但不存在 → `options = build_options(); options.resume = id; ClaudeSDKClient(options).connect()`，注册 `sessions[id]`

`stream()` 在**新建会话**时开头先发 `session_started: {session_id, title}` 并 append 到 JSONL（续聊不发，会话已开始）；其后每条 SSE 事件同时 append。`title` = 首条 prompt 前 40 字（超长截断 + `…`）。

### `GET /sessions`

返回 `[{id, title, created_at, mtime, event_count}, ...]`，按 mtime 倒序。`event_count` 来自 JSONL 行数（轻量计数，回放时再加载详情）。

### `GET /sessions/<id>/events`

返回该会话完整事件流。两种形式：
- `text/event-stream`：SSE 流式回放（前端用 EventSource，与 `/chat` 一致渲染路径）
- 或 `application/json`：`{"events":[{event,data},...]}` 一次返回

选 `application/json` 一次性返回（鸿蒙会话事件量小，简单；前端 fetch 后逐条分发）。回放只读，不发 `done`/`error`。

### `DELETE /sessions/<id>`

- `sessions[id]` 若存在 → disconnect client + 移除
- 删 `./sessions/<id>.jsonl`
- 返回 200。不删 SDK 本地 session 文件（留存无害，YAGNI）

### 路径与穿越防护

`./sessions/` 下读写均做 realpath + commonpath 断言在 `BASE_DIR/sessions` 内（与 `/export` 同款）。`<id>` 限定 `^[a-f0-9]{32}$`（uuid4.hex 形式），双重防护。

## 前端

### rail 顶部折叠会话列表区

rail 现有结构：`.rail-head`（品牌 + 元信息）+ `.rail-body`（轨迹轨）。在两者之间插入 `.rail-sessions`：

```
.rail-sessions（折叠区）
  .rail-sessions-head（"会话" + ▾ 折叠 + ＋ 新建按钮）
  .rail-sessions-list（会话条目列表，每条：title + 相对时间 + ✕ 删除）
```

- 默认展开；会话条目点击 → 切换会话（fetch events 重放）
- ＋ 新建 → 清空对话区 + 轨迹轨 + 重置 turnNo，下次 POST /chat 带 session_id=null
- ✕ 删除 → confirm 后 DELETE + 从列表移除（若删的是当前会话→清空画布回空状态）
- 当前会话条目高亮（accent 左边框）

### 回放渲染

切换会话：`fetch('/sessions/<id>/events')` → `data.events` 逐条按 `event` 分发：
- `session_started` → 记录当前 session_id，不渲染卡片
- `text` → `addRow` 追加 bot 气泡
- `tool_use` → 轨迹轨节点 + pending
- `file`/`findings`/`tool_result` → 对应 `addFileCard`/`addFindingsCards`/tool_result 卡片 + 轨迹轨标 ok
- `done`/`error` → 跳过不渲染（实时控制事件，回放不重放）；回放结束前端标回合结束即可

回放模式与实时模式区别：回放不滚动动画、不增量 finalizeBot（一次性批量渲染后滚到底）。为简单起见，回放复用现有 add* 函数（它们已 finalizeBot + scrollThread），逐条调用即可——性能可接受（单会话事件 < 200 条）。

### 当前 session_id 状态

前端全局 `currentSessionId`：新建后由 `session_started` 事件设；切换会话由 `session_started`（回放首条）设；POST /chat 时带上。

## 错误处理分层

| 层 | 失败行为 |
|---|---|
| `sessions_store` JSONL 损坏行 | `load_events` 跳过该行，返回已解析的事件 |
| `sessions_store` IO 异常 | append 失败不阻断 SSE 流（log 警告，回放缺该条）；load 失败返回空列表 + 前端提示"回放失败" |
| resume 重建失败（SDK session 不存在/损坏） | catch → 降级为新建会话（生成新 UUID）+ SSE `error` 事件提示"历史不可续，已开新会话" |
| client 被 LRU 回收后续聊 | 走 resume 重建路径（同上） |
| `GET /sessions/<id>/events` id 不存在 | 404 JSON |
| `DELETE /sessions/<id>` id 不存在 | 200（幂等，删不存在的等于成功） |
| `<id>` 格式非法（非 uuid hex） | 400 JSON |
| 路径穿越 | 400 JSON |
| 前端回放渲染异常 | 单事件 try/catch，跳过该事件，继续后续 |

## 测试

沿用 Phase 1-4 风格：自带 `main()`、非 pytest、monkeypatch 桩。

**`sessions_store_test.py`（新增）**：
- `append_event` 写 3 条 → `load_events` 返回 3 条 `(event, data)`
- 损坏行（手动写一行非法 JSON）→ `load_events` 跳过、返回其余
- `list_sessions` 返回 `[{id, title, created_at, mtime, event_count}]` 倒序
- `delete_session` 删后 `load_events` 返回空、文件不存在
- 路径穿越 `<id>` 含 `../` → append/load 抛或拒（按防护实现）

**`server_test.py`（扩充）**：
- 新建会话：`POST /chat {session_id:null}` → SSE 首条 `session_started` 含新 UUID；`./sessions/<id>.jsonl` 存在且首行是 session_started
- 续聊（常驻）：同 session_id 二次 POST → 复用 client（mock 验证未新建 client）
- 续聊（回收后）：手动从 `sessions` dict 移除 entry 模拟回收 → POST 触发 resume 重建（mock ClaudeSDKClient 验证 options.resume 被设）
- resume 失败降级：mock ClaudeSDKClient.connect raise → 降级新建 UUID + `error` SSE 提示
- `GET /sessions` 返回列表含 title/created_at
- `GET /sessions/<id>/events` 返回事件数组
- `GET /sessions/<id>/events` id 不存在 → 404
- `DELETE /sessions/<id>` → 文件删除 + 200；再 DELETE 同 id → 200（幂等）
- `<id>` 非 uuid hex → 400
- 回归：Phase 4 的 `/export` + stream() file/findings/tool_result e2e 测试仍通过（stream() 改动只加 append，不破坏分发）

**前端**：无自动化测试，手动验证清单写入 spec——启动 server，新建会话生成文件、切换会话回放卡片、续聊、删除、刷新页面后会话列表仍在。

**回归保护**：`tools_review_test.py`、`analyzers/findings_test.py`、`main_test.py`、4 个 analyzer `*_test.py`、`generators/framework_test.py` 全部重跑，零改动零回归。

## 风险与边界

- **多 client 内存**：每会话一个常驻 ClaudeSDKClient（subprocess）。LRU 上限 8 个 + 闲置 10min 回收，控制内存。鸿蒙开发同时开 3-5 个会话属正常
- **SDK resume 依赖本地磁盘 session 文件**：SDK 把 session 存在默认 CLAUDE_CONFIG_DIR（`~/.claude` 或类似）。若该目录被清，resume 失败 → 降级新建。spec 显式接受此降级
- **JSONL 增长**：单会话事件 < 200 条，单文件 < 数百 KB，无需压缩/轮转。未来会话变长再加
- **并发**：`async with lock` 串行化整个 query（既有），多会话不会并发 query——一次只一个会话在跑。LRU sweep 在 lock 内，无竞态
- **前端回放性能**：逐条调用现有 add* 函数，单会话 < 200 事件可接受。若未来会话超长，改批量渲染 + 虚拟滚动（YAGNI）
- **首句标题歧义**：首条 prompt 前 40 字可能不完整。可接受（列表辨识够用），LLM 摘要留未来
- **删除不清 SDK 本地 session**：SDK 本地 session 文件留存无害（不占 our 关心的 `./sessions/`），YAGNI 不清
- **零回归**：不改 tools/generators/analyzers/main；`build_options()` 签名不动；Phase 4 的 stream() 分发逻辑只加 append 副作用，不改控制流

## 主 Agent 改动

- `sessions_store.py`：新增（纯 IO：append_event/load_events/list_sessions/delete_session + 路径穿越防护）
- `server.py`：加 `sessions` dict + `ClientEntry` + LRU sweep；`chat()` 加 `session_id` 参数与新建/复用/resume 重建分支；`stream()` 每事件 append + 首发 session_started；新增 `/sessions`、`/sessions/<id>/events`、`DELETE /sessions/<id>` 路由
- `index.html`：rail 加 `.rail-sessions` 折叠区；会话列表渲染 + 新建/切换/删除；回放 fetch + 逐条分发；`currentSessionId` 状态
- `.gitignore`：加 `sessions/`
- `main.py`/`tools.py`/`generators/`/`analyzers/`：零改动

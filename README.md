# harmony-game-agent

基于 Claude Agent SDK 的鸿蒙（HarmonyOS）原生游戏开发辅助 Agent —— 交互式 REPL + 自定义工具版。

## 功能

交互式多轮对话 Agent，挂载两个自定义工具（in-process MCP 工具，确定性逻辑）：

- **generate_arkts_component** — 生成 ArkTS 组件代码骨架（@Component/@Entry/@State/build），确定性模板
- **review_arkts_code** — 用 LLM 智能审查 ArkTS 代码（固定 checklist：组件结构/状态管理/性能/规范/潜在 bug），anthropic SDK 直接调用

## 准备

1. 复制环境变量模板并填入配置：

   ```bash
   cp .env.example .env
   # 编辑 .env
   ```

2. 必填 `ANTHROPIC_API_KEY`，获取地址：https://console.anthropic.com/

3. 中转模型（可选）：在 `.env` 中配置

   - `ANTHROPIC_BASE_URL`：中转/代理地址（直连官方可留空）
   - `ANTHROPIC_MODEL`：模型名称（未设置用 SDK 默认）

## 运行

```bash
uv run python main.py
```

进入 REPL 后输入需求，例如：

```
你> 帮我生成一个玩家血条组件 HealthBar，是入口组件
你> 审查这段 ArkTS 代码：<粘贴代码>
你> exit
```

Claude 会自动调用对应工具，REPL 会打印 `[调用工具]`、`[工具结果]`、`Claude:` 回复。

## 文件结构

| 文件 | 作用 |
|------|------|
| `main.py` | env 配置、`ClaudeAgentOptions` 装配、REPL 主循环 |
| `tools.py` | 两个 `@tool` 工具定义 + `create_sdk_mcp_server` 装配 |
| `.env.example` | 环境变量模板（API Key / 中转地址 / 模型） |

## 说明

- 中转配置：`ANTHROPIC_MODEL` 通过 `ClaudeAgentOptions.model` 传入，`ANTHROPIC_BASE_URL` 通过 `env` 字段传给子进程；`ANTHROPIC_API_KEY` 由子进程继承。
- 工具是 in-process MCP 工具，跑在同一 Python 进程内，SDK 自动处理"调用→执行→返回结果→Claude 继续"的循环。
- `allowed_tools` 仅预授权工具免审批，不控制工具可用性。
- Python 的 `claude_agent_sdk` 默认通过内置 Claude Code CLI 运行。
- 后续可扩展：更多工具、子 Agent、权限模式、MCP 外部服务器。

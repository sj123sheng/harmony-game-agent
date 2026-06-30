# harmony-game-agent

基于 Claude Agent SDK 的鸿蒙（HarmonyOS）原生游戏开发辅助 Agent —— 交互式 REPL + 网页工作台 + RPG 工具集。

## 功能

交互式多轮对话 Agent，挂载 5 个自定义工具（in-process MCP 工具）：

- **generate_character_stats** — 生成角色属性系统（基础属性 / 经验等级 / 升级成长 / 属性面板 UI）
- **generate_skill_system** — 生成技能与 Buff 系统（技能定义 / Buff / 技能管理器）
- **generate_inventory** — 生成背包与装备系统（物品 / 背包 / 装备槽 / 背包 UI）
- **generate_enemy_ai** — 生成敌人与战斗 AI（敌人属性 / AI 状态机 / 战斗结算器）
- **review_arkts_code** — 用 LLM 智能审查 ArkTS 代码（固定 checklist）

前四个生成工具采用**混合生成**：确定性模板骨架 + 一次 LLM 调用填充定制细节，输出多文件 `.ets` 到 `./generated/<子系统>/`。共享框架 `generators/framework.py` 统一处理渲染、LLM 填充、回填与降级（LLM 失败时占位符标 `// TODO`，不崩溃）。

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
   - **注意**：四个生成工具与 review 工具会通过 `AsyncAnthropic` 直接调用 `ANTHROPIC_MODEL`。若中转网关未路由该模型（如某些网关不路由 `glm-*`），这些工具会降级为 `// TODO` 占位。请确保 `ANTHROPIC_MODEL` 是网关已路由的模型（如 `claude-sonnet-4-5`）。

## 运行

```bash
# 交互式 REPL
uv run python main.py

# 网页工作台（http://127.0.0.1:8000，自动开浏览器）
uv run python server.py
```

进入 REPL 后输入需求，例如：

```
你> 生成一个战士角色属性系统，等级上限 60
你> 生成 4 个即时战斗技能，带 buff
你> 生成一个 20 格背包，装备槽含武器
你> 生成一个 Boss 敌人，困难难度
你> 审查这段 ArkTS 代码：<粘贴代码>
你> exit
```

Claude 会自动调用对应工具，REPL 会打印 `[调用工具]`、`[工具结果]`、`Claude:` 回复，并把生成的 `.ets` 写入 `./generated/<子系统>/`。

## 测试

```bash
uv run python generators/framework_test.py
```

覆盖：模板渲染、LLM 占位符回填、降级路径（非法 JSON / LLM 异常）、多文件顺序、4 个生成器冒烟（路径与文件名）。不依赖 pytest。

## 文件结构

| 文件 | 作用 |
|------|------|
| `main.py` | env 配置、`ClaudeAgentOptions` 装配、REPL 主循环 |
| `server.py` | 网页版后端（Starlette + SSE），复用 main 的装配 |
| `tools.py` | 5 个 `@tool` 工具定义 + `create_sdk_mcp_server` 装配 |
| `generators/framework.py` | 共享混合生成框架（渲染 + LLM 填充 + 回填 + 降级 + 重试） |
| `generators/character_stats.py` | 角色属性系统生成器 |
| `generators/skill_system.py` | 技能与 Buff 系统生成器 |
| `generators/inventory.py` | 背包与装备生成器 |
| `generators/enemy_ai.py` | 敌人与战斗 AI 生成器 |
| `generators/framework_test.py` | 框架单测 + 冒烟测试 |
| `index.html` | 网页工作台前端 |
| `.env.example` | 环境变量模板（API Key / 中转地址 / 模型） |

## 说明

- 中转配置：`ANTHROPIC_MODEL` 通过 `ClaudeAgentOptions.model` 传入主 Agent；工具内的 LLM 调用通过 `os.environ.get("ANTHROPIC_MODEL")` 读取同一变量，`ANTHROPIC_BASE_URL` 通过 `env` 字段传给子进程，`ANTHROPIC_API_KEY` 由子进程继承。
- 工具是 in-process MCP 工具，跑在同一 Python 进程内，SDK 自动处理"调用→执行→返回结果→Claude 继续"的循环。
- 生成工具返回 `{files: [{path, content}]}`，主 Agent 据此用 Write 写入 `./generated/`。
- 中转网关偶发空响应时，框架最多重试 1 次。
- 后续可扩展：项目级多文件/工程脚手架、跨文件引用校验、日志分析与性能建议、Web 工作台文件树与导出。

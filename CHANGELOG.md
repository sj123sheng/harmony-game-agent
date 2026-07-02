# Changelog

本文件记录 harmony-game-agent 的版本演进。日期为 YYYY-MM-DD 本地时区。

## [v1.0.0] - 2026-07-02

首个完整版本。基于 Claude Agent SDK 的鸿蒙（HarmonyOS）原生游戏开发辅助 Agent —— 交互式 REPL + 网页工作台 + RPG 工具集，10 个 in-process MCP 工具覆盖"生成 → 组装 → 审查 → 调试 → 导出"全流程。

### Phase 1：RPG 工具集

- `generate_character_stats` — 角色属性系统（基础属性 / 经验等级 / 升级成长 / 属性面板 UI）
- `generate_skill_system` — 技能与 Buff 系统（技能定义 / Buff / 技能管理器）
- `generate_inventory` — 背包与装备系统（物品 / 背包 / 装备槽 / 背包 UI）
- `generate_enemy_ai` — 敌人与战斗 AI（敌人属性 / AI 状态机 / 战斗结算器）
- 混合生成框架 `generators/framework.py`：确定性模板骨架 + LLM 填充定制细节 + 回填降级（LLM 失败标 `// TODO` 不崩溃）+ 重试

### Phase 2：DevEco 脚手架

- `scaffold_deveco_project` — 扫描已生成子系统、组装 DevEco Studio 工程骨架、LLM 填充 Index.ets 战斗循环 demo
- `generators/deveco_project.py` 工程模板与装配

### Phase 3：审查与调试增强

- `review_arkts_code` — LLM 智能审查 ArkTS 代码（固定 5 维 checklist，纯文本输出）
- `analyze_runtime_logs` — 运行日志分析（堆栈映射 / 错误分类 / 根因假设 / 置信度）
- `suggest_performance_fixes` — 性能审查（build 耗时操作 / 状态粒度 / 列表渲染 / 图片 / 生命周期 / 并发）
- `locate_bug` — 跨文件推理定位可疑 Bug（复现步骤 / 验证手段 / 修复方向）
- `check_api_usage` — API 用法审查（误用 / 废弃 / V1V2 状态管理混用 / 权限 / 平台差异）
- 共享框架 `analyzers/framework.py`：`resolve_scope` 路径解析 + `analyze_with_context` 统一 LLM 调用 + UTF-8 字节截断

### Phase 4：Web 工作台增强

- 4 个 analyzer 的 `system_prompt` 改 JSON 数组输出，前端渲染为结构化 findings 卡片（severity 色标 / 排序 / 特有字段）
- `review_arkts_code` 同步 JSON 化：5 维 checklist 输出为 JSON 数组（含 `category` 维度字段），复用 findings 卡片渲染
- `Write` 工具调用拦截：server.py 在 SSE 流内配对 Write 的 tool_use/result，前端渲染可预览 / 复制 / 导出的 file 卡片
- `GET /export?path=<rel>` 端点：按路径打包 `generated/` 下目录为 zip 或直返单文件，含路径穿越与 zip slip 防护
- `analyzers/findings.py` 共享解析：`parse_findings`（整文本优先 + 正则回退 + 字段别名容错）+ `_format_findings_text`
- `index.html` 同步 10 工具标签与 4 类提示词，新增 file/findings 卡片渲染与轨迹轨事件标记
- README 同步 10 工具说明

### 测试

9 个测试文件全 PASS（自带 `main()` runner，非 pytest）：
- `generators/framework_test.py` — 框架单测 + 冒烟
- `analyzers/findings_test.py` — parse_findings 容错链
- 4 个 `analyzers/*_test.py` — analyzer system_prompt + 入口
- `main_test.py` — _extract_tool_result_text 格式化与回退
- `tools_review_test.py` — review 工具装配
- `server_test.py` — /export 端点 + stream() 事件分发端到端（file/findings/tool_result fallback）

### 已知边界（非目标，留作未来扩展）

- `Edit` 修改无 diff 卡片
- 仅拦截 `Write` 新建文件，`Edit` 修改无 diff 卡片
- Web 工作台无多会话持久化 / 历史回放 / 文件树浏览（Phase 5 规划中）
- 前端无自动化测试（手动验证清单）
- 跨文件引用校验未实现

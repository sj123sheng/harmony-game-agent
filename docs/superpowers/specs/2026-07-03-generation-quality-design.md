# 生成代码质量优化（Phase 6）

日期：2026-07-03
阶段：第六阶段（生成质量优化），承接 [Phase 5：多会话持久化与历史回放](./2026-07-02-session-persistence-replay-design.md)

## 背景与问题

用户反馈：生成的游戏代码错误多，无法在 DevEco Studio 直接编译运行。调查 4 个 generator 模板与 DevEco 脚手架，发现根因分两层：

- **模板层硬伤**（确定性 bug）：跨文件引用无 import、面板组件重复 `@Entry`、嵌套 `@State` 不响应（属性面板不刷新）、DevEco 工程缺关键文件
- **LLM 填充层**（幻觉风险）：`hybrid_generate` 把 LLM 片段直接回填，无 ArkTS 校验；LLM 幻觉 API/符号/V1V2 混用都会进产物

## 目标

让 4 个 generator + DevEco 脚手架产出的工程能在 DevEco Studio 直接编译运行，属性面板数值能响应刷新，且 LLM 填充片段经一轮自动审查修正后再写盘。

## 非目标

- 不加 hvigor 编译冒烟（需 DevEco 环境，CI 难；留未来）
- 不改 analyzers 自身逻辑（review_arkts_code/check_api_usage 的 system_prompt 不动，仅被 framework 调用）
- 不改 main Agent system prompt（闭环在 framework 层，不靠提示约束）
- 不改前端 index.html（findings 已有渲染路径，闭环产出的 findings 复用）
- 不引入 V2 状态管理（统一 V1，避免 V1/V2 混用——本身是审查点）
- 不改 RPG 工具的业务逻辑（数值/公式由 LLM 填充，模板只修结构与状态范式）

## A. 模板硬伤修复

### A1. 跨文件 import 显式化

所有 UI 面板组件引用数据类处，模板内显式写 `import {...} from './...'`（ArkTS 不支持隐式跨文件引用）：

- `StatsPanel.ets` → `import { CharacterStats } from './CharacterStats'`
- `InventoryUI.ets` → `import { Inventory, Item } from './Inventory'`（按 export 符号）
- `CombatUI.ets` → `import { Enemy } from './Enemy'` + `import { AIController } from './AIController'`（按实际引用）
- `SkillManager`/`Buff` 等跨文件引用同理

import 路径用同目录相对（`./X`，无扩展名），与 DevEco 习惯一致。`_build_imports`（devco 已有）只负责 Index.ets 跨目录引用，子系统内部同目录 import 写在各自模板顶部。

### A2. 去重复 @Entry

只 `entry/src/main/ets/pages/Index.ets` 是 `@Entry`。所有子系统面板组件（StatsPanel/InventoryUI/CombatUI/SkillPanel 等）去掉 `@Entry`，只保留 `@Component`——它们是被 Index 引用的子组件，不是页面入口。

### A3. 嵌套状态改 V1 @Observed/@ObjectLink

问题：`@State stats: CharacterStats = new CharacterStats()` 中 CharacterStats 内部 `@State` 字段变更不触发 StatsPanel 重渲染（ArkUI `@State` 只观察顶层赋值）。

修法（V1 范式）：
- **数据类**（`CharacterStats`/`Enemy`/`Item`/`Skill`/`Buff`/`Inventory`/`EquipmentSlots`）：class 头加 `@Observed`，**内部字段去掉 `@State`**（`@Observed` 类的字段由 `@ObjectLink` 观察响应，不需 `@State`）。字段保留类型标注与初始值。
- **面板子组件**（StatsPanel/InventoryUI/CombatUI 等）：持有数据类实例的字段用 `@ObjectLink`（而非 `@State`）。`@ObjectLink` 必须由父组件传入，不能 `new` 初始化——所以面板组件须接收父组件传入的实例。
- **父组件**（Index.ets demo）：`@State stats: CharacterStats = new CharacterStats()`（顶层 `@State` 持有实例，传给子组件的 `@ObjectLink`）。顶层 `@State` 观察实例引用，子组件 `@ObjectLink` 观察实例字段变更——两层联动。

约束：
- `@ObjectLink` 字段不能有初始值（必须父传），模板改 `@ObjectLink stats: CharacterStats`（去掉 `= new ...`）
- 数据类构造：`new CharacterStats()` 后由 LLM 填的 `initial_stats` 设数值——`initial_stats` 占位符从 `@State x = 0` 改为构造函数或字段默认值

### A4. DevEco 脚手架补失配文件

- 新增 `entry/oh-package.json5`（模块级，声明 name/version，无额外依赖）
- 新增 `AppScope/resources/base/element/string.json` + `color.json`（app.json5 引用 `$string:`/`$color:` 的兜底）——实际 app.json5 用直接字符串 label，但 `$media:app_icon` 仍需资源
- 新增 `AppScope/resources/base/media/` 下 app_icon 占位（生成一个 1x1 透明 PNG 的 base64 或引用说明；DevEco 缺图标会报错）——用最小占位资源
- `build-profile.json5`：`signingConfigs` 与 `products.signingConfig` 一致——要么补一个 `default` signingConfig 占位，要么 `products` 去掉 `signingConfig` 字段（未签名构建）。选后者（去 `signingConfig` 引用，未签名占位构建）
- `AppScope/app.json5` 补 `minAPIVersion`（如 12）等 stage 模型常用字段

## B. framework 层自动审查闭环

### B1. 闭环位置与流程

在 `generators/framework.py` 的 `hybrid_generate` 内，LLM 填充回填后加审查环节：

```
1. 渲染骨架 → 2. LLM 填充 → 3. 回填（现有）
→ 4. 新增：对每个生成文件调 analyzers.framework.analyze_with_context
   （用 review_arkts_code 的 system_prompt，JSON 数组输出）
→ 5. 收集 severity 为"高"/"中"的 findings
→ 6. 若有：把 findings 拼进 prompt（"以下问题需修正：..."）再调 LLM 重新填充（最多 1 次重试）
→ 7. 重新回填 → 返回 {files, error, findings}
```

重试只 1 次（成本控制）。第二次审查若仍有"高" findings，不继续重试，把 findings 附在返回里供主 Agent/前端展示。

### B2. 审查范围与降级

- 审查用 `review_arkts_code` 的 system_prompt（5 维 checklist + JSON 数组），通过 `analyzers.framework.analyze_with_context` 调用，与 review 工具同一逻辑
- 每个生成文件单独审查（`FileRef(path=<rel>, content=<已回填内容>)`）
- 审查 LLM 失败（余额不足/网关异常）：不阻断，沿用 framework 降级风格——`error` 字段记警告，`findings` 为空，正常返回已回填文件

### B3. 返回字段扩展

`hybrid_generate` 返回新增 `findings: list[dict]`（每条 `{file, severity, location, summary, fix, category}`），供 `_format_files` 附在返回文本里、server stream() 转 SSE findings 事件、前端 findings 卡片渲染（既有路径，零前端改动）。

`tools.py` 的 `_format_files` 把 findings 追加为可读文本（"审查发现 N 项：..."），主 Agent 据此决定是否进一步处理。

### B4. 依赖与循环

`framework.py` 将 `import` analyzers.framework 的 `analyze_with_context` + `FileRef` + review_arkts_code 的 system_prompt。analyzers 不依赖 generators（无循环）。review_arkts_code 的 system_prompt 当前内联在 tools.py——提取为 `analyzers/review_prompt.py` 共享常量，供 tools.py 与 framework.py 复用（DRY）。

## 测试

沿用自带 `main()` 非 pytest 风格。

**`generators/framework_test.py`（扩充）**：
- 闭环重试：mock LLM 第一次返回有"高" severity findings 的填充，第二次返回修正后的填充 → 断言最终回填含修正版、findings 字段非空
- 审查失败降级：mock analyze_with_context raise → 断言不阻断、error 字段记警告、files 正常返回
- 无 findings 不重试：mock 审查返回空 → 断言只调 1 次 LLM 填充（不重试）

**`generators/*_test.py`（扩充各模板冒烟）**：
- 每个模板渲染后断言：含 `import` 语句、面板组件无 `@Entry`、数据类有 `@Observed`、面板持有数据类字段用 `@ObjectLink`

**回归**：`tools_review_test.py`（review system_prompt 提取后仍走 analyze_with_context）、`analyzers/findings_test.py`、`server_test.py`、全量 10 文件零回归。

## 风险与边界

- **闭环成本**：每生成 +1-2 次 LLM 调用（审查 + 可能重试）。可接受（生成非高频）
- **V1 嵌套深度**：`@Observed`+`@ObjectLink` 嵌套层数深时需层层传递。当前 RPG 模型 2 层（面板→数据类），可接受
- **LLM 修正不一定收敛**：重试 1 次后仍可能有 findings——接受，附在返回供主 Agent 决策，不无限重试
- **DevEco 图标占位**：1x1 透明 PNG 占位，用户需自行替换真实图标
- **零回归**：不改 analyzers/tools 业务逻辑、不改前端、不改 main Agent prompt；review system_prompt 仅提取为常量不改文本

## 主 Agent 改动

- `generators/character_stats.py`/`skill_system.py`/`inventory.py`/`enemy_ai.py`：模板补 import、去 @Entry、数据类加 @Observed 去内部 @State、面板 @ObjectLink
- `generators/deveco_project.py`：补 entry/oh-package.json5、AppScope resources、修 signingConfig、补 minAPIVersion
- `generators/framework.py`：hybrid_generate 加审查 + 重试闭环、返回 findings 字段
- `analyzers/review_prompt.py`：新增，提取 review_arkts_code system_prompt 为共享常量
- `tools.py`：review_arkts_code 改用共享常量；`_format_files` 追加 findings 文本
- `generators/framework_test.py`/各 `*_test.py`：扩充闭环与模板冒烟测试
- 前端/analyzers/server/main：零改动

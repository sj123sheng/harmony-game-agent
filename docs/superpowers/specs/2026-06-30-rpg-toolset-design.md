# 鸿蒙游戏 Agent · RPG 工具集扩展设计（第一阶段）

- 日期：2026-06-30
- 阶段：第一阶段（扩展工具集）
- 范围：为现有 harmony-game-agent 新增 4 个 RPG/战斗类子系统生成工具，并移除现有 `generate_arkts_component`

## 背景与目标

现有项目基于 Claude Agent SDK，已有 REPL（`main.py`）、Web 工作台（`server.py` + `index.html`）、两个 in-process MCP 工具（`generate_arkts_component` 确定性骨架、`review_arkts_code` LLM 审查）。生成结果写入 `./generated/`。

本期目标：把"能生成什么"做厚，聚焦 RPG/战斗类游戏，提供 4 个领域生成工具。这是后续阶段（项目级能力、审查与调试增强、Web 工作台增强）的地基。

### 不在本期范围（YAGNI）

- 文件树 UI、历史会话、导出 DevEco 工程（第四阶段）
- 多文件引用校验、跨文件 import 修正（第二阶段）
- 运行日志分析、性能建议、Bug 定位（第三阶段）
- 不引入 Jinja2、pytest 等新依赖

## 需求摘要

- **4 个新工具**：角色属性系统、技能与 Buff 系统、背包与装备、敌人与战斗 AI
- **生成方式**：混合 —— 确定性模板骨架 + LLM 填充定制细节
- **输出**：每个工具多文件 `.ets`，写入 `./generated/<子系统>/`
- **现有 `generate_arkts_component`**：移除；`review_arkts_code` 保留

## 整体架构

### 模块划分

```
tools.py              保留 review_arkts_code，移除 generate_arkts_component；作为装配层
generators/
  __init__.py
  framework.py        共享 hybrid_generate 框架
  character_stats.py  角色属性系统工具
  skill_system.py     技能与 Buff 系统工具
  inventory.py        背包与装备工具
  enemy_ai.py         敌人与战斗 AI 工具
  templates/          各子系统的 .ets 模板（字符串模板）
```

### 职责边界

- `framework.py`：只管"按 spec 渲染骨架 → 调 LLM 填占位符 → 组装多文件 → 返回结构化结果 + 错误兜底"。不关心 RPG 业务，不写文件（保持纯函数、可测）。
- 4 个 `xxx.py`：只声明本子系统的入参 schema、模板、填充指令、输出文件清单。不重复 LLM 调用逻辑。
- `tools.py`：装配层，把 4 个生成器注册成 MCP 工具，沿用现有 `build_server()`。
- `main.py` / `server.py`：仅更新 `allowed_tools` 列表与 system_prompt 的工具说明。

### 关键决策

- LLM 填充走 `AsyncAnthropic`（复用 `review_arkts_code` 已验证的中转配置），与主 Agent 共用 `.env`。
- 多文件输出：工具返回 `{files: [{path, content}]}` 结构，主 Agent 按其写入 `./generated/<子系统>/`。框架本身不写文件。

## 共享框架 `hybrid_generate`

### 接口

```python
@dataclass
class FileSpec:
    path: str                # 相对路径，如 "character/CharacterStats.ets"
    template: str            # 含 {占位符} 的骨架（用 string.Template 或 str.format）
    fill_targets: list[str]  # 该文件里需要 LLM 填的占位符名

@dataclass
class GeneratorSpec:
    name: str                # "generate_character_stats" 等
    description: str
    input_schema: dict       # @tool 的参数 schema
    files: list[FileSpec]    # 多文件清单
    fill_instruction: str    # 给 LLM 的填充指令（约束、风格、禁忌）
    max_tokens: int = 2048   # LLM 调用上限

async def hybrid_generate(spec: GeneratorSpec, args: dict) -> dict:
    """渲染骨架 → 调 LLM 填占位符 → 返回 {files:[{path,content}]}"""
```

### 执行流程

1. 用 `args` 渲染每个 `FileSpec.template` 的确定性部分（组件名、入参值等），得到带占位符的骨架。
2. 把所有文件的骨架 + `fill_instruction` + 占位符清单拼成一个 prompt，**一次** LLM 调用填完所有占位符（避免多次往返）。
3. LLM 返回结构化 JSON（`{文件路径: {占位符: 填充内容}}`），框架校验并回填到骨架。
4. 返回 `{files: [{path, content}]}` 给主 Agent。

### 错误兜底（不崩栈原则，沿用 review_arkts_code）

- LLM 调用失败 / JSON 解析失败 / 占位符缺失：不抛异常，返回降级结果——未填占位符以 `// TODO: <占位符说明>` 标注 + 一条错误提示文本。主 Agent 仍能继续，用户能看到哪里没生成。
- 模板渲染参数缺失：抛 `ValueError`（编程错误，由 @tool 层兜住）。

### LLM 调用约束

- `model` = `os.environ.get("ANTHROPIC_MODEL")`，与 review 工具一致。
- `max_tokens` = 4096（统一）。中转网关常强制开启 thinking，思考会消耗大量 token，1024/2048 会导致 text 块为空、JSON 解析失败；4096 给思考+输出留足余量。
- system prompt 固定："鸿蒙 ArkTS 代码填充器，只输出指定 JSON，不输出多余解释"，保证可复现。
- 重试：中转网关偶发空响应，LLM 调用或 JSON 解析失败时最多重试 1 次；两次都失败才走降级路径。

## 4 个工具的参数与输出

### 1. `generate_character_stats`（角色属性系统）

- 入参：`character_name: str`（如 "Player"）、`archetype: str`（战士/法师/刺客，决定数值倾向）、`level_cap: int`（默认 99）
- 输出文件：
  - `character/CharacterStats.ets` —— 属性结构（生命/攻击/防御/暴击/速度）+ 升级成长公式 + 经验/等级
  - `character/StatsPanel.ets` —— 属性面板 UI（@Component @Entry，展示数值）
- LLM 填充：成长公式数值、各 archetype 初始数值、面板布局细节
- 确定性：组件名、@State 字段定义、方法签名

### 2. `generate_skill_system`（技能与 Buff）

- 入参：`skill_count: int`（默认 4）、`include_buffs: bool`（默认 true）、`combat_style: str`（即时/回合）
- 输出文件：
  - `skill/Skill.ets` —— 技能定义（id/名称/冷却/消耗/伤害公式）
  - `skill/Buff.ets` —— Buff/Debuff 定义 + 叠加/过期规则
  - `skill/SkillManager.ets` —— 释放流程、冷却计时、Buff 挂载与结算
- LLM 填充：具体技能效果逻辑、伤害公式、Buff 效果描述
- 确定性：数据结构、Manager 方法签名、状态字段

### 3. `generate_inventory`（背包与装备）

- 入参：`slot_count: int`（默认 20）、`equipment_slots: list[str]`（默认 头/身/手/脚/武器）、`stackable: bool`（默认 true）
- 输出文件：
  - `inventory/Item.ets` —— 物品定义（id/名称/类型/堆叠/稀有度）
  - `inventory/Inventory.ets` —— 格子增删/查找/堆叠逻辑
  - `inventory/Equipment.ets` —— 装备槽穿戴/卸下 + 属性加成结算
  - `inventory/InventoryUI.ets` —— 背包 UI（@Entry）
- LLM 填充：示例物品数据、稀有度配色、UI 布局
- 确定性：数据结构、增删方法签名、槽位枚举

### 4. `generate_enemy_ai`（敌人与战斗 AI）

- 入参：`enemy_name: str`、`ai_pattern: str`（巡逻/追击/远程/Boss）、`difficulty: str`（简单/普通/困难）
- 输出文件：
  - `enemy/Enemy.ets` —— 敌人属性 + 伤害结算接口
  - `enemy/EnemyAI.ets` —— 状态机（巡逻/追击/攻击/受击/死亡）+ 转移条件
  - `enemy/CombatResolver.ets` —— 攻击命中、伤害计算、与 CharacterStats 对接
- LLM 填充：状态转移阈值、AI 行为细节、技能选择策略
- 确定性：状态枚举、状态机结构、接口签名

### 通用约定

- 所有 `.ets` 顶部注释说明用途。
- `@Entry` 仅在 UI 类（StatsPanel/InventoryUI）加；数据/逻辑类只用 `@Component` 或纯 class。
- 文件路径统一 `./generated/<子系统>/<文件>.ets`。
- 主 Agent 收到 `{files}` 后用 Write 工具逐个写入（system_prompt 已指示）。

## 数据流

```
用户 "生成一个战士角色属性系统"
  → 主 Agent 调用 mcp__harmony_tools__generate_character_stats
  → @tool 入口用 args 调 hybrid_generate(spec, args)
  → framework 渲染模板 → 一次 LLM 调用填占位符 → 回填校验
  → 返回 {files:[{path:"character/CharacterStats.ets", content}, ...]}
  → 主 Agent 用 Write 写入 ./generated/character/*.ets
  → Agent 文字说明生成了哪些文件
  → REPL/Web 打印 [调用工具] [工具结果] Claude: ...
```

## 错误处理分层

| 层 | 失败场景 | 处理 |
|---|---|---|
| 框架内 | LLM 调用失败/JSON 解析失败/占位符缺失 | 降级：占位符标 `// TODO` + 错误提示，不抛异常 |
| 框架内 | 模板渲染参数缺失 | 抛 ValueError（编程错误，由 @tool 层兜住） |
| @tool 层 | 调用框架抛异常 | catch 后返回 `{content:[{type:text, text:"生成失败：<e>"}]}`，不崩 REPL |
| 主 Agent | Write 写文件失败 | Agent 自行报错给用户 |

## 测试策略

不引测试框架，保持轻量，与项目现状一致。

- **框架单测**：`generators/framework_test.py`，用桩 LLM（monkeypatch `AsyncAnthropic` 返回固定 JSON）验证——模板渲染正确、占位符回填正确、降级路径产出 `// TODO`、多文件组装顺序正确。
- **工具冒烟**：每个生成器一个冒烟测试，传合理 args，断言返回的 `files` 路径与文件名符合预期（不断言 LLM 内容，因为不可复现）。
- **手动验收**：`uv run python main.py` 进 REPL，逐个工具真实调用一次，确认 `generated/` 下产出可读的 `.ets`。
- 运行方式：`uv run python generators/framework_test.py`（`if __name__ == "__main__"` 直接跑，不依赖 pytest）。

## 受影响文件

- 新增：`generators/__init__.py`、`generators/framework.py`、`generators/character_stats.py`、`generators/skill_system.py`、`generators/inventory.py`、`generators/enemy_ai.py`、`generators/templates/*`、`generators/framework_test.py`
- 修改：`tools.py`（移除旧工具、注册 4 个新工具）、`main.py`（`allowed_tools` + system_prompt）、`server.py`（无需改，复用 main）、`README.md`（更新工具列表）
- 删除：`generate_arkts_component` 相关代码（在 `tools.py` 内）

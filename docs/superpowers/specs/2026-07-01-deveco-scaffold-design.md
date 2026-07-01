# DevEco 工程脚手架设计（Phase 2：项目级能力）

日期：2026-07-01
阶段：第二阶段（项目级能力），承接 [Phase 1：RPG 工具集](./2026-06-30-rpg-toolset-design.md)

## 目标

从单文件生成升级到项目级：新增 `scaffold_deveco_project` 工具，扫描 Phase 1 已生成的 RPG 子系统文件，组装成一个可在 DevEco Studio 打开的完整鸿蒙 stage 模型工程，并生成接入全部子系统的战斗循环 demo 入口页（Index.ets）。

## 非目标

- 不修改 Phase 1 的 4 个 RPG 生成器（`generate_character_stats` / `generate_skill_system` / `generate_inventory` / `generate_enemy_ai`）——它们继续向 `./generated/<子系统>/` 输出
- 不做跨子系统互引修正（如 SkillManager 引用 Buff 搬到工程后路径修正）——本期只保证 `Index.ets → 子系统` 引用可用
- 不做 DevEco 工程的编译/真机运行验证——只保证结构可在 DevEco Studio 打开
- 不替换真实图标/资源，media 用占位 PNG

## 现状

- `generators/framework.py`：Phase 1 的共享混合生成框架（`hybrid_generate(spec, args)`，渲染确定性模板 + LLM 填充 + 降级 + 重试）
- `generators/{character_stats,skill_system,inventory,enemy_ai}.py`：4 个 RPG 生成器，输出多文件 .ets 到 `./generated/<子系统>/`
- `tools.py`：注册 MCP 工具，`build_server()` 装配
- `./generated/`：当前扁平存放旧 HealthBar.ets 等；Phase 1 后按 `<子系统>/` 分目录
- `.gitignore` 忽略 `generated/`

## 架构

### 模块划分

```
generators/
  deveco_project.py        ← 新增：DevEco 脚手架生成器
  templates/               ← 新增：DevEco 配置/资源模板（确定性）
    app.json5
    entry/src/main/module.json5
    entry/src/main/ets/entryability/EntryAbility.ets
    entry/src/main/resources/base/element/{string,color,float}.json
    entry/src/main/resources/base/profile/main_pages.json
    entry/src/main/resources/en_US/element/string.json
    entry/src/main/resources/zh_CN/element/string.json
    entry/src/main/resources/base/media/icon.png          ← 占位二进制
    entry/build-profile.json5
    entry/hvigorfile.ts
    build-profile.json5
    hvigorfile.ts
    oh-package.json5
  framework.py             ← 不变，复用 hybrid_generate
  character_stats.py ...   ← 不变
tools.py                   ← 注册 scaffold_deveco_project
main.py                    ← allowed_tools + system_prompt 增补
```

### 职责边界

- `deveco_project.py`：扫描 `./generated/<子系统>/` → 读取文件内容与导出符号 → 声明 DevEco GeneratorSpec（确定性配置 + Index.ets LLM 填充）→ 调 `hybrid_generate` → 把搬运的子系统文件前置拼到返回结果
- `framework.py`：不变，继续负责模板渲染、LLM 填充、降级、重试
- `tools.py`：装配层，注册新工具
- `main.py` / `server.py`：仅 `allowed_tools` 列表与 system_prompt 更新

### DevEco stage 模型工程结构（脚手架产出）

产出位置：`./generated/<project_name>/`

```
<project_name>/
  AppScope/app.json5
  entry/
    src/main/
      ets/
        entryability/EntryAbility.ets
        pages/Index.ets                    ← LLM 填充战斗循环 demo
        game/                              ← 从 ./generated/<子系统>/ 搬运
          character/*.ets
          skill/*.ets
          inventory/*.ets
          enemy/*.ets
      resources/
        base/element/{string,color,float}.json
        base/profile/main_pages.json
        base/media/icon.png
        en_US/element/string.json
        zh_CN/element/string.json
      module.json5
    build-profile.json5
    hvigorfile.ts
  build-profile.json5
  hvigorfile.ts
  oh-package.json5
```

## 扫描与子系统发现

`_scan_subsystems(scan_dir: str) -> list[Subsystem]`：

1. 扫描 `scan_dir` 下一级目录，识别已知子系统：`character` / `skill` / `inventory` / `enemy`
2. 对每个存在的子系统目录，读取其中所有 `.ets` 文件
3. 用正则提取每个文件的导出符号：`export\s+(?:struct|class|const|enum)\s+(\w+)`
4. 返回结构化清单：

```python
@dataclass
class SubsystemFile:
    src: str            # 源路径，如 "generated/character/CharacterStats.ets"
    dst: str            # 目标路径，如 "entry/src/main/ets/game/character/CharacterStats.ets"
    exports: list[str]  # 如 ["CharacterStats"]
    content: str

@dataclass
class Subsystem:
    name: str           # "character"
    files: list[SubsystemFile]
```

> 上面的 `Subsystem` / `SubsystemFile` 为本工具内部数据结构。下方 `GeneratorSpec` / `LlmFill` / `hybrid_generate` 沿用 Phase 1 框架已定义的类型，具体字段以 `generators/framework.py` 现状为准；若 Phase 1 类型字段与本设计有出入，在实现计划阶段对齐。

### 空工程兜底

若 `scan_dir` 下没有任何子系统文件，脚手架仍生成完整 DevEco 骨架，Index.ets 走"空场景 + 提示文本"分支，不报错。

### 子系统文件搬运

- 工具把扫描到的 `{dst, content}` 直接放入返回的 `{files}` 列表，Agent 据此 Write
- 不对子系统 .ets 做任何改写
- 目标路径 = `entry/src/main/ets/game/<子系统>/<原名>`

### Index.ets 引用约束

- Index.ets 的 import 路径由工具算出（基于 dst）并写入 LLM 填充指令
- 本期只保证 `Index.ets → 子系统` 引用可用
- 子系统间互引（如 SkillManager 引 Buff）搬到 `game/<sub>/` 后相对路径可能失效——已知限制，留待后续阶段

## 工具接口

```python
@tool("scaffold_deveco_project",
      "扫描 ./generated/ 下已有的 RPG 子系统文件，组装成一个可在 DevEco Studio 打开的完整鸿蒙工程，并生成接入全部子系统的战斗循环 demo 入口页。",
      {
        "project_name": str,
        "bundle_prefix": str,   # 可选，默认 "com.harmonygame"
        "scan_dir": str,        # 可选，默认 "./generated"
      })
async def scaffold_deveco_project(args):
    ...
```

### 执行流程

1. `_scan_subsystems(scan_dir)` → 子系统清单
2. 组装 `GeneratorSpec`：
   - `deterministic_files`：DevEco 配置/资源模板渲染（用 `project_name` / `bundle` 填充）
   - `llm_filled_files`：仅 `entry/src/main/ets/pages/Index.ets`
3. `hybrid_generate(spec, args)` → 渲染配置文件 + LLM 填充 Index.ets
4. 把搬运的子系统文件 `{dst, content}` 前置拼到 `result.files`
5. 返回 `{files: [...]}`，由 Agent 写入 `./generated/<project_name>/`

### Index.ets 的 LLM 填充

```python
LlmFill(
    file="entry/src/main/ets/pages/Index.ets",
    instruction=(
      "基于以下子系统清单，写一个完整战斗循环的 ArkTS 入口页：\n"
      "- 可用 import：{扫描出的路径+导出符号清单}\n"
      "- 要求：实例化角色/敌人/技能/背包；攻击按钮触发战斗结算；"
      "技能冷却与释放；多敌人轮换；回合与即时两种模式切换；血量/属性面板刷新。\n"
      "- 无子系统时输出空场景 + '请先生成子系统' 提示文本。\n"
      "- 约束：只用提供的 import，不臆造不存在的符号。"
    ),
    skeleton="""@Entry
@Component
struct Index {
  // __LLM: 状态字段与实例化
  build() {
    // __LLM: 战斗循环 UI
  }
}"""
)
```

### bundleName sanitize

`bundle = f"{bundle_prefix}.{project_name}"`，project_name 非法字符（中文/空格/特殊符号）→ 转小写、非 `[a-z0-9_]` 字符转 `_`。

### 确定性模板清单

| 文件 | 关键填充 |
|------|---------|
| `AppScope/app.json5` | bundleName、versionName |
| `entry/src/main/module.json5` | name、type、deviceTypes |
| `entry/src/main/ets/entryability/EntryAbility.ets` | 工程名 |
| `…/base/element/string.json` | 工程名展示 |
| `…/base/element/color.json` `…/float.json` | 固定色值/字号 |
| `…/base/profile/main_pages.json` | `"src/main/ets/pages/Index.ets"` |
| `…/en_US/element/string.json` `…/zh_CN/element/string.json` | 多语言 |
| `…/base/media/icon.png` | 占位最小 PNG |
| `entry/build-profile.json5` `entry/hvigorfile.ts` | 固定 |
| `build-profile.json5` `hvigorfile.ts` `oh-package.json5` | 工程名 |

## 错误处理

- LLM 填充 Index.ets 失败/超时 → 框架降级：保留 `@Entry/@Component struct Index` 空骨架 + 顶部注释"demo 生成失败，请重试"，其余 DevEco 配置文件照常产出（工程仍可打开）
- 扫描无子系统 → 不报错，Index.ets 走"空场景 + 提示"分支
- LLM 输出的 import 引用了不存在的符号 → 框架不做静态校验；交由 `review_arkts_code`（已有工具）后续审查捕获
- 工具内 LLM 调用失败（中转余额不足/鉴权失败）→ 沿用 `review_arkts_code` 的 try/except 返回可读错误文本，不抛出

## 测试

pytest，沿用 Phase 1 测试风格：

- `test_scan_subsystems`：tmp 目录造 `character/CharacterStats.ets` 等样例，断言导出符号、目标路径正确
- `test_scan_empty`：空目录 → 返回空清单，不抛
- `test_scaffold_deterministic_files`：mock LLM，断言 app.json5/module.json5/main_pages.json 等确定性文件内容与路径正确、含 project_name 替换
- `test_scaffold_index_llm_fill`：mock LLM 返回固定 Index.ets，断言填充指令含子系统 import 清单、输出文件路径正确
- `test_scaffold_copies_subsystem_files`：断言搬运的子系统文件出现在结果 `{files}` 里、内容与源一致、路径在 `entry/.../game/<sub>/`
- `test_scaffold_llm_failure_degrades`：mock LLM 抛错 → Index.ets 降级为空骨架，其余文件仍产出
- `test_bundle_sanitization`：project_name 含中文/空格/大写 → bundleName 合规

## 风险与边界

- **DevEco 版本漂移**：模板基于当前 stage 模型（API 11/12 附近）；DevEco Studio 升级可能改配置 schema → 模板集中存 `templates/`，便于后续单独更新
- **跨子系统互引**：本期只保证 `Index.ets → 子系统` 引用可用，子系统间互引搬到 `game/<sub>/` 后相对路径可能失效 → 显式标注为已知限制，留待后续阶段
- **media 占位 icon**：用最小合法 PNG；真实图标交由用户替换
- **bundleName 合规**：sanitize 处理非法字符
- **工程可打开性**：保证目录结构与配置文件齐全，但不保证 hvigor 编译通过（依赖子系统代码本身的正确性）

## 主 Agent 改动

- `main.py` `build_options()` 的 `allowed_tools` 增 `mcp__harmony_tools__scaffold_deveco_project`
- `main.py` `build_options()` 的 `system_prompt` 增补工具说明：当用户要求"生成工程/脚手架/可运行 demo"时调用此工具（`server.py` 复用 `build_options()`，无需单独改）

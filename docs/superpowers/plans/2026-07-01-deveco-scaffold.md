# DevEco 工程脚手架 实现计划（Phase 2）

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 新增 `scaffold_deveco_project` 工具：扫描 `./generated/<子系统>/` 已有 RPG 文件，组装成可在 DevEco Studio 打开的完整鸿蒙 stage 模型工程，并生成接入全部子系统的战斗循环 demo 入口页（Index.ets）。

**Architecture:** 复用 Phase 1 的 `hybrid_generate(spec, args)` 框架。新工具是一个生成器：声明 DevEco 配置/资源模板（确定性 `__ARG__`）+ Index.ets（`__LLM__` 填充战斗循环），调框架渲染；另扫描 `./generated/` 读取子系统 .ets 内容与导出符号，把搬运文件前置拼到返回的 `{files}`。所有输出路径以 `<project_name>/` 为前缀，主 Agent 据此 Write 到 `./generated/<project_name>/`。

**Tech Stack:** Python 3.12、`anthropic` AsyncAnthropic、`claude_agent_sdk` in-process MCP 工具、HarmonyOS stage 模型 ArkTS/JSON5 配置。测试沿用 Phase 1 的 `generators/*_test.py` + `if __name__=="__main__"` 自跑模式（非 pytest）。

## Global Constraints

- 沿用 Phase 1 框架占位符：`__ARG:name__`（确定性）、`__LLM:name__`（LLM 填充，失败降级为 `// TODO: 待填充 name`）
- `hybrid_generate(spec, args)` 返回 `{"files":[{"path","content"}], "error": str}`，不写文件
- 工具返回给主 Agent 的格式：`{"content":[{"type":"text","text": _format_files(result)}]}`，复用 `tools._format_files`
- 子系统目录名固定为 `character` / `skill` / `inventory` / `enemy`（与 Phase 1 生成器 FileSpec.path 前缀一致）
- DevEco 工程产出位置：`./generated/<project_name>/`；`./generated/` 已在 `.gitignore`
- **二进制媒体豁免**：主 Agent 的 `Write` 是纯文本，无法写 PNG。配置文件保留 `$media:app_icon` 引用（结构真实、DevEco Studio 可打开），二进制 `app_icon.png` 不生成，留给用户放入 `entry/src/main/resources/base/media/` 后再编译——落在 spec 非目标"不保证编译通过"内
- bundleName sanitize：`<bundle_prefix>.<sanitized_project_name>`，project_name 转小写、非 `[a-z0-9_]` 字符转 `_`
- 测试用桩 LLM（monkeypatch `generators.framework.AsyncAnthropic`），不调真实 API
- 提交纪律：每个 Task 末尾一次提交，commit message 含 `Prompt:` 行

## Spec 调和记录

- spec 写"pytest"，实际 Phase 1 用 `generators/*_test.py` 自带 `main()` 自跑。本计划沿用 Phase 1 实际模式，不引入 pytest。
- spec 写"media 占位 icon（base64 或最小 PNG）"。因 `Write` 纯文本无法写二进制，改为不生成图标、配置保留引用、用户后补。见 Global Constraints 二进制媒体豁免。
- spec 的 `LlmFill`/`Subsystem` 类型名为设计示意；实际用 Phase 1 的 `FileSpec`/`GeneratorSpec` + 本计划定义的 `Subsystem`/`SubsystemFile` dataclass。

## 文件结构

| 文件 | 责任 | 创建/修改 |
|------|------|-----------|
| `generators/deveco_project.py` | 扫描、导出提取、bundle sanitize、DevEco 模板、`build_deveco_project_spec(subsystems)`、`run_scaffold(args)` 工具主逻辑 | 创建 |
| `generators/deveco_project_test.py` | 扫描/sanitize/spec/工具全流程测试 + `main()` | 创建 |
| `tools.py` | 注册 `scaffold_deveco_project` 工具、`build_server` 增列 | 修改 |
| `main.py` | `build_options()` 的 `system_prompt` + `allowed_tools` 增补 | 修改 |
| `generators/__init__.py` | 导出 `build_deveco_project_spec`、`run_scaffold` | 修改 |

---

### Task 1: 扫描与导出提取

**Files:**
- Create: `generators/deveco_project.py`
- Create: `generators/deveco_project_test.py`

**Interfaces:**
- Produces: `Subsystem(name: str, files: list[SubsystemFile])`、`SubsystemFile(src, dst, exports, content)`、`_scan_subsystems(scan_dir: str) -> list[Subsystem]`、`_extract_exports(content: str) -> list[str]`、`_import_specifier(subsystem: str, filename: str) -> str`

- [ ] **Step 1: 写扫描失败测试（先写测试）**

创建 `generators/deveco_project_test.py`，包含导入与第一批测试：

```python
"""DevEco 脚手架生成器测试 + 冒烟。沿用 framework_test.py 的自跑模式。"""

import asyncio
import os
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from generators.deveco_project import (
    Subsystem,
    SubsystemFile,
    _scan_subsystems,
    _extract_exports,
    _sanitize_bundle,
    _import_specifier,
    build_deveco_project_spec,
    run_scaffold,
)
from generators.framework import FileSpec, GeneratorSpec, hybrid_generate


# ---------- 扫描 ----------

def test_extract_exports_struct_class_const_enum():
    code = """
export struct CharacterStats {}
export class Buff {}
export const MAX_SKILLS = 8
export enum DamageType { PHYSICAL, MAGIC }
// not exported: struct Internal {}
"""
    exports = _extract_exports(code)
    assert exports == ["CharacterStats", "Buff", "MAX_SKILLS", "DamageType"], exports
    print("[OK] test_extract_exports_struct_class_const_enum")


def test_extract_exports_empty_when_none():
    assert _extract_exports("// nothing here\nstruct Foo {}") == []
    print("[OK] test_extract_exports_empty_when_none")


def test_scan_subsystems_finds_character_and_skill():
    with tempfile.TemporaryDirectory() as d:
        Path(d, "character").mkdir()
        Path(d, "character", "CharacterStats.ets").write_text(
            "export struct CharacterStats { @State hp: number = 0 }", encoding="utf-8"
        )
        Path(d, "character", "StatsPanel.ets").write_text(
            "export struct StatsPanel {}", encoding="utf-8"
        )
        Path(d, "skill").mkdir()
        Path(d, "skill", "Skill.ets").write_text("export struct Skill {}", encoding="utf-8")
        Path(d, "ignored").mkdir()  # 非已知子系统，应忽略
        subs = _scan_subsystems(d)
        names = [s.name for s in subs]
        assert names == ["character", "skill"], names  # 已知子系统按固定顺序
        ch = subs[0]
        assert ch.files[0].src == os.path.join(d, "character", "CharacterStats.ets")
        assert ch.files[0].exports == ["CharacterStats"]
        assert ch.files[0].content == "export struct CharacterStats { @State hp: number = 0 }"
        # dst 用正斜杠（DevEco 工程内统一用 /）
        assert ch.files[0].dst == "entry/src/main/ets/game/character/CharacterStats.ets"
        assert ch.files[1].dst == "entry/src/main/ets/game/character/StatsPanel.ets"
        print("[OK] test_scan_subsystems_finds_character_and_skill")


def test_scan_subsystems_empty_dir_returns_empty():
    with tempfile.TemporaryDirectory() as d:
        assert _scan_subsystems(d) == []
    print("[OK] test_scan_subsystems_empty_dir_returns_empty")


def test_scan_subsystems_skips_non_ets_files():
    with tempfile.TemporaryDirectory() as d:
        Path(d, "enemy").mkdir()
        Path(d, "enemy", "Enemy.ets").write_text("export struct Enemy {}", encoding="utf-8")
        Path(d, "enemy", "notes.txt").write_text("ignore me", encoding="utf-8")
        subs = _scan_subsystems(d)
        assert len(subs[0].files) == 1
        assert subs[0].files[0].src.endswith("Enemy.ets")
    print("[OK] test_scan_subsystems_skips_non_ets_files")


def test_import_specifier_from_pages_to_game():
    # Index.ets 在 entry/src/main/ets/pages/，引用 game/<sub>/<File>
    spec = _import_specifier("character", "CharacterStats.ets")
    assert spec == "../game/character/CharacterStats", spec
    spec2 = _import_specifier("skill", "SkillManager.ets")
    assert spec2 == "../game/skill/SkillManager", spec2
    print("[OK] test_import_specifier_from_pages_to_game")


def main():
    test_extract_exports_struct_class_const_enum()
    test_extract_exports_empty_when_none()
    test_scan_subsystems_finds_character_and_skill()
    test_scan_subsystems_empty_dir_returns_empty()
    test_scan_subsystems_skips_non_ets_files()
    test_import_specifier_from_pages_to_game()
    print("\nTask 1 全部通过。")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python generators/deveco_project_test.py`
Expected: `ModuleNotFoundError: No module named 'generators.deveco_project'`

- [ ] **Step 3: 写最小实现**

创建 `generators/deveco_project.py`：

```python
"""DevEco 工程脚手架生成器。

扫描 ./generated/<子系统>/ 已有 RPG 文件，组装成完整鸿蒙 stage 模型工程，
Index.ets 用 LLM 填充战斗循环 demo。复用 framework.hybrid_generate。
"""

import os
import re
from dataclasses import dataclass, field

from generators.framework import FileSpec, GeneratorSpec, hybrid_generate

# 已知子系统与固定扫描顺序（与 Phase 1 生成器输出目录一致）
_KNOWN_SUBSYSTEMS = ("character", "skill", "inventory", "enemy")

# DevEco 工程内 ets 根目录（子系统文件搬运到此下）
_GAME_DIR = "entry/src/main/ets/game"

_EXPORT_RE = re.compile(r"export\s+(?:struct|class|const|enum)\s+(\w+)")


@dataclass
class SubsystemFile:
    src: str            # 源绝对/相对路径
    dst: str            # 工程内目标路径（正斜杠，相对工程根）
    exports: list[str]
    content: str


@dataclass
class Subsystem:
    name: str
    files: list[SubsystemFile] = field(default_factory=list)


def _extract_exports(content: str) -> list[str]:
    """从 ArkTS 源码提取 export 符号名，按出现顺序去重。"""
    seen = set()
    out = []
    for m in _EXPORT_RE.finditer(content):
        name = m.group(1)
        if name not in seen:
            seen.add(name)
            out.append(name)
    return out


def _scan_subsystems(scan_dir: str) -> list[Subsystem]:
    """扫描 scan_dir 下已知子系统目录的 .ets 文件。返回按 _KNOWN_SUBSYSTEMS 顺序。"""
    subs: list[Subsystem] = []
    for name in _KNOWN_SUBSYSTEMS:
        sub_dir = os.path.join(scan_dir, name)
        if not os.path.isdir(sub_dir):
            continue
        sub = Subsystem(name=name)
        for fname in sorted(os.listdir(sub_dir)):
            if not fname.endswith(".ets"):
                continue
            src = os.path.join(sub_dir, fname)
            content = Path_read_text(src)
            sub.files.append(SubsystemFile(
                src=src,
                dst=f"{_GAME_DIR}/{name}/{fname}",
                exports=_extract_exports(content),
                content=content,
            ))
        if sub.files:
            subs.append(sub)
    return subs


def _import_specifier(subsystem: str, filename: str) -> str:
    """Index.ets（在 pages/）引用 game/<sub>/<File> 的相对 import 路径（无扩展名）。"""
    stem = filename[:-4] if filename.endswith(".ets") else filename
    return f"../game/{subsystem}/{stem}"


def Path_read_text(path: str) -> str:
    """读文本，统一 utf-8。"""
    with open(path, encoding="utf-8") as f:
        return f.read()
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run python generators/deveco_project_test.py`
Expected: 全部 `[OK]`，末行 `Task 1 全部通过。`

- [ ] **Step 5: 提交**

```bash
git add generators/deveco_project.py generators/deveco_project_test.py
git commit -m "$(cat <<'EOF'
feat(deveco): 扫描 RPG 子系统文件与导出符号提取

新增 generators/deveco_project.py：Subsystem/SubsystemFile 数据结构、
_scan_subsystems 扫描已知子系统目录、_extract_exports 正则提取 export 符号、
_import_specifier 计算 Index.ets 到 game/ 的相对 import 路径。

Prompt: /brainstorming 鸿蒙游戏 Agent Phase 2 项目级能力

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 2: bundle sanitize + DevEco 模板 + spec 构造

**Files:**
- Modify: `generators/deveco_project.py`（追加模板与 `build_deveco_project_spec`）
- Modify: `generators/deveco_project_test.py`（追加 sanitize / spec 测试）

**Interfaces:**
- Consumes: `Subsystem`/`SubsystemFile` from Task 1
- Produces: `_sanitize_bundle(project_name, bundle_prefix) -> (bundle, label)`、`build_deveco_project_spec(subsystems: list[Subsystem]) -> GeneratorSpec`

- [ ] **Step 1: 写 sanitize 与 spec 测试**

在 `generators/deveco_project_test.py` 的 `main()` 之前追加测试函数（并在 `main()` 里调用）：

```python
# ---------- sanitize ----------

def test_sanitize_bundle_basic():
    bundle, label = _sanitize_bundle("rpgdemo", "com.harmonygame")
    assert bundle == "com.harmonygame.rpgdemo"
    assert label == "rpgdemo"
    print("[OK] test_sanitize_bundle_basic")


def test_sanitize_bundle_normalizes_illegal():
    bundle, label = _sanitize_bundle("我的 RPG Demo", "com.harmonygame")
    assert bundle == "com.harmonygame._rpg_demo", bundle
    assert label == "我的 RPG Demo"  # label 保留原展示名
    print("[OK] test_sanitize_bundle_normalizes_illegal")


def test_sanitize_bundle_custom_prefix():
    bundle, _ = _sanitize_bundle("demo", "com.example")
    assert bundle == "com.example.demo"
    print("[OK] test_sanitize_bundle_custom_prefix")


# ---------- spec 构造 ----------

def _fake_subsystems():
    """造一份扫描结果用于 spec 测试。"""
    return [
        Subsystem(name="character", files=[
            SubsystemFile("a/CharacterStats.ets", "entry/src/main/ets/game/character/CharacterStats.ets",
                          ["CharacterStats"], "export struct CharacterStats {}"),
            SubsystemFile("a/StatsPanel.ets", "entry/src/main/ets/game/character/StatsPanel.ets",
                          ["StatsPanel"], "export struct StatsPanel {}"),
        ]),
        Subsystem(name="skill", files=[
            SubsystemFile("a/Skill.ets", "entry/src/main/ets/game/skill/Skill.ets",
                          ["Skill"], "export struct Skill {}"),
        ]),
    ]


def test_build_spec_has_all_deterministic_files():
    subs = _fake_subsystems()
    spec = build_deveco_project_spec(subs)
    paths = [f.path for f in spec.files]
    expected = [
        "AppScope/app.json5",
        "entry/src/main/module.json5",
        "entry/src/main/ets/entryability/EntryAbility.ets",
        "entry/src/main/ets/pages/Index.ets",
        "entry/src/main/resources/base/element/string.json",
        "entry/src/main/resources/base/element/color.json",
        "entry/src/main/resources/base/element/float.json",
        "entry/src/main/resources/base/profile/main_pages.json",
        "entry/src/main/resources/en_US/element/string.json",
        "entry/src/main/resources/zh_CN/element/string.json",
        "entry/build-profile.json5",
        "entry/hvigorfile.ts",
        "build-profile.json5",
        "hvigorfile.ts",
        "oh-package.json5",
    ]
    assert paths == expected, paths
    print("[OK] test_build_spec_has_all_deterministic_files")


def test_build_spec_index_ets_has_llm_slot():
    spec = build_deveco_project_spec(_fake_subsystems())
    index = next(f for f in spec.files if f.path == "entry/src/main/ets/pages/Index.ets")
    assert "__LLM:demo_body__" in index.template
    assert "demo_body" in index.fill_targets
    print("[OK] test_build_spec_index_ets_has_llm_slot")


def test_build_spec_fill_instruction_lists_imports():
    spec = build_deveco_project_spec(_fake_subsystems())
    fi = spec.fill_instruction
    # 列出每个文件的 import 路径与导出符号
    assert "../game/character/CharacterStats" in fi
    assert "CharacterStats" in fi
    assert "../game/skill/Skill" in fi
    assert "Skill" in fi
    # 含战斗循环要求关键词
    assert "战斗循环" in fi
    print("[OK] test_build_spec_fill_instruction_lists_imports")


def test_build_spec_fill_instruction_empty_when_no_subsystems():
    spec = build_deveco_project_spec([])
    assert "请先生成子系统" in spec.fill_instruction or "空场景" in spec.fill_instruction
    print("[OK] test_build_spec_fill_instruction_empty_when_no_subsystems")
```

在 `main()` 中追加调用：

```python
    test_sanitize_bundle_basic()
    test_sanitize_bundle_normalizes_illegal()
    test_sanitize_bundle_custom_prefix()
    test_build_spec_has_all_deterministic_files()
    test_build_spec_index_ets_has_llm_slot()
    test_build_spec_fill_instruction_lists_imports()
    test_build_spec_fill_instruction_empty_when_no_subsystems()
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python generators/deveco_project_test.py`
Expected: `AttributeError: module 'generators.deveco_project' has no attribute '_sanitize_bundle'`

- [ ] **Step 3: 写实现——sanitize 与模板**

在 `generators/deveco_project.py` 追加：

```python
def _sanitize_bundle(project_name: str, bundle_prefix: str) -> tuple[str, str]:
    """bundleName = <bundle_prefix>.<sanitized>；label 保留原展示名。"""
    sanitized = re.sub(r"[^a-z0-9_]", "_", project_name.lower())
    # 避免开头数字
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    bundle = f"{bundle_prefix}.{sanitized}"
    return bundle, project_name
```

DevEco 模板（确定性，`__ARG__` 占位）。在 `deveco_project.py` 追加模块级常量：

```python
_APP_JSON5 = """{
  "app": {
    "bundleName": "__ARG:bundle__",
    "vendor": "harmony-game-agent",
    "versionCode": 1,
    "versionName": "1.0.0",
    "icon": "$media:app_icon",
    "label": "__ARG:label__"
  }
}
"""

_MODULE_JSON5 = """{
  "module": {
    "name": "entry",
    "type": "entry",
    "description": "$string:module_desc",
    "mainElement": "EntryAbility",
    "deviceTypes": ["phone", "tablet"],
    "deliveryInstallOption": { "deliveryType": "installWithRequest" },
    "abilities": [
      {
        "name": "EntryAbility",
        "srcEntry": "./ets/entryability/EntryAbility.ets",
        "description": "$string:EntryAbility_desc",
        "icon": "$media:app_icon",
        "label": "$string:EntryAbility_label",
        "startWindowIcon": "$media:app_icon",
        "startWindowBackground": "$color:start_window_background",
        "exported": true,
        "skills": [
          { "entities": ["entity.system.home"], "actions": ["action.system.home"] }
        ]
      }
    ]
  }
}
"""

_ENTRY_ABILITY_ETS = """import UIAbility from '@ohos.app.ability.UIAbility';
import window from '@ohos.window';

export default class EntryAbility extends UIAbility {
  onWindowStageCreate(windowStage: window.WindowStage): void {
    windowStage.loadContent('pages/Index', (err) => {
      if (err.code) {
        console.error('Failed to load content. cause: ' + JSON.stringify(err));
        return;
      }
      console.info('Succeeded in loading content.');
    });
  }
}
"""

_INDEX_ETS = """// 战斗循环 demo - __ARG:label__
// 由 scaffold_deveco_project 生成，LLM 填充 struct 体战斗逻辑。
__ARG:imports__
@Entry
@Component
struct Index {
  __LLM:demo_body__
}
"""

_STRING_JSON_BASE = """{
  "string": [
    { "name": "module_desc", "value": "module description" },
    { "name": "EntryAbility_desc", "value": "entry ability" },
    { "name": "EntryAbility_label", "value": "__ARG:label__" },
    { "name": "app_name", "value": "__ARG:label__" }
  ]
}
"""

_STRING_JSON_EN = """{
  "string": [
    { "name": "module_desc", "value": "module description" },
    { "name": "EntryAbility_desc", "value": "entry ability" },
    { "name": "EntryAbility_label", "value": "__ARG:label__" },
    { "name": "app_name", "value": "__ARG:label__" }
  ]
}
"""

_STRING_JSON_ZH = """{
  "string": [
    { "name": "module_desc", "value": "模块描述" },
    { "name": "EntryAbility_desc", "value": "入口能力" },
    { "name": "EntryAbility_label", "value": "__ARG:label__" },
    { "name": "app_name", "value": "__ARG:label__" }
  ]
}
"""

_COLOR_JSON = """{
  "color": [
    { "name": "start_window_background", "value": "#FFFFFF" }
  ]
}
"""

_FLOAT_JSON = """{
  "float": [
    { "name": "title_font_size", "value": "24fp" }
  ]
}
"""

_MAIN_PAGES_JSON = """{
  "src": [
    "pages/Index"
  ]
}
"""

_ENTRY_BUILD_PROFILE = """{
  "apiType": "stageMode",
  "buildOption": {},
  "targets": [
    { "name": "default", "runtimeOS": "harmonyos" }
  ]
}
"""

_HVIGORFILE_TS = """import { appTasks } from '@ohos/hvigor-ohos';
export default {
  system: appTasks,
}
"""

_ROOT_BUILD_PROFILE = """{
  "app": { "signingConfigs": [], "products": [{ "name": "default", "signingConfig": "default" }] },
  "modules": [{ "name": "entry", "srcPath": "./entry", "targets": [{ "name": "default", "applyToProducts": ["default"] }] }]
}
"""

_OH_PACKAGE_JSON5 = """{
  "name": "__ARG:project_name__",
  "version": "1.0.0",
  "description": "harmony-game-agent 生成的 RPG demo 工程",
  "main": "",
  "license": "MIT",
  "dependencies": {},
  "devDependencies": {
    "@ohos/hypium": "1.0.6"
  }
}
"""
```

- [ ] **Step 4: 写实现——`build_deveco_project_spec`**

在 `deveco_project.py` 追加：

```python
def _import_lines(subsystems: list[Subsystem]) -> str:
    """构造给 LLM 的可用 import 清单文本。"""
    if not subsystems:
        return "（无可用子系统）"
    lines = []
    for sub in subsystems:
        for f in sub.files:
            if not f.exports:
                continue
            spec = _import_specifier(sub.name, os.path.basename(f.dst))
            syms = ", ".join(f.exports)
            lines.append(f"- 从 '{spec}' 可导入：{syms}")
    return "\n".join(lines)


def build_deveco_project_spec(subsystems: list[Subsystem]) -> GeneratorSpec:
    """构造 DevEco 工程的 GeneratorSpec。路径相对工程根（不含 project_name 前缀，由工具层拼接）。"""
    imports = _import_lines(subsystems)
    if subsystems:
        instruction = (
            "为一个鸿蒙 ArkTS 战斗循环入口页填充 demo_body。可用 import：\n"
            f"{imports}\n\n"
            "要求：在 struct Index 内声明状态字段并实例化已导入的角色/敌人/技能/背包；"
            "build() 返回一个战斗循环 UI——攻击按钮触发战斗结算并刷新血量；技能冷却与释放；"
            "多敌人轮换；回合与即时两种模式切换；血量/属性面板刷新。"
            "约束：只用上面列出的 import，不臆造不存在的符号；不要重复 @Entry/@Component/struct 声明，"
            "只填 demo_body 占位符位置（含状态字段、build 方法体等 struct 内部全部内容）。"
        )
    else:
        instruction = (
            "无可用子系统。demo_body 填：声明一个空状态，build() 返回一个 Column 含 "
            "Text('请先生成子系统后再脚手架工程') 的简单场景。不要重复 struct 声明。"
        )

    return GeneratorSpec(
        name="scaffold_deveco_project",
        description=(
            "扫描 ./generated/ 下已有 RPG 子系统文件，组装成完整鸿蒙 stage 模型工程，"
            "并生成接入全部子系统的战斗循环 demo 入口页。"
            "参数：project_name 工程名；bundle_prefix 可选默认 com.harmonygame；"
            "scan_dir 可选默认 ./generated。"
        ),
        input_schema={
            "project_name": str,
            "bundle_prefix": str,
            "scan_dir": str,
        },
        files=[
            FileSpec("AppScope/app.json5", _APP_JSON5),
            FileSpec("entry/src/main/module.json5", _MODULE_JSON5),
            FileSpec("entry/src/main/ets/entryability/EntryAbility.ets", _ENTRY_ABILITY_ETS),
            FileSpec("entry/src/main/ets/pages/Index.ets", _INDEX_ETS, fill_targets=["demo_body"]),
            FileSpec("entry/src/main/resources/base/element/string.json", _STRING_JSON_BASE),
            FileSpec("entry/src/main/resources/base/element/color.json", _COLOR_JSON),
            FileSpec("entry/src/main/resources/base/element/float.json", _FLOAT_JSON),
            FileSpec("entry/src/main/resources/base/profile/main_pages.json", _MAIN_PAGES_JSON),
            FileSpec("entry/src/main/resources/en_US/element/string.json", _STRING_JSON_EN),
            FileSpec("entry/src/main/resources/zh_CN/element/string.json", _STRING_JSON_ZH),
            FileSpec("entry/build-profile.json5", _ENTRY_BUILD_PROFILE),
            FileSpec("entry/hvigorfile.ts", _HVIGORFILE_TS),
            FileSpec("build-profile.json5", _ROOT_BUILD_PROFILE),
            FileSpec("hvigorfile.ts", _HVIGORFILE_TS),
            FileSpec("oh-package.json5", _OH_PACKAGE_JSON5),
        ],
        fill_instruction=instruction,
        max_tokens=4096,
    )
```

- [ ] **Step 5: 运行测试确认通过**

Run: `uv run python generators/deveco_project_test.py`
Expected: Task 1 + Task 2 全部 `[OK]`。

- [ ] **Step 6: 提交**

```bash
git add generators/deveco_project.py generators/deveco_project_test.py
git commit -m "$(cat <<'EOF'
feat(deveco): bundle sanitize 与 DevEco stage 模型工程模板

新增 _sanitize_bundle 处理 bundleName 非法字符；
新增 15 个 DevEco 配置/资源模板（app.json5/module.json5/EntryAbility.ets/
Index.ets 含 LLM 占位/resources 多语言/build-profile/hvigorfile/oh-package）；
新增 build_deveco_project_spec 构造 GeneratorSpec，fill_instruction 注入
扫描到的子系统 import 清单与战斗循环要求。

Prompt: /brainstorming 鸿蒙游戏 Agent Phase 2 项目级能力

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 3: 脚手架工具主逻辑 `run_scaffold`

**Files:**
- Modify: `generators/deveco_project.py`（追加 `run_scaffold`）
- Modify: `generators/deveco_project_test.py`（追加全流程测试）
- Modify: `generators/__init__.py`（导出 `run_scaffold`、`build_deveco_project_spec`）

**Interfaces:**
- Consumes: `build_deveco_project_spec`、`hybrid_generate`、`_scan_subsystems`、`_sanitize_bundle`
- Produces: `async def run_scaffold(args: dict) -> dict` 返回 `{"files":[{"path","content"}], "error": str}`，所有 path 以 `<project_name>/` 为前缀；搬运的子系统文件在前

- [ ] **Step 1: 写全流程测试**

在 `generators/deveco_project_test.py` 追加（沿用 `framework_test.py` 的桩 LLM 模式）：

```python
# ---------- run_scaffold 全流程 ----------

def _fake_block(text: str):
    return SimpleNamespace(text=text)


class _FakeMessages:
    def __init__(self, return_text: str):
        self._return_text = return_text
        self.calls = []

    async def create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(content=[_fake_block(self._return_text)])


class _FakeAnthropic:
    def __init__(self, return_text: str):
        self.messages = _FakeMessages(return_text)

    def __call__(self, *args, **kwargs):
        return self


def _patch_fake(return_text: str):
    import generators.framework as fw
    fake = _FakeAnthropic(return_text)
    orig = fw.AsyncAnthropic
    fw.AsyncAnthropic = lambda *a, **k: fake
    return orig, fake


def test_run_scaffold_copies_subsystem_files_with_project_prefix():
    # 造扫描目录
    with tempfile.TemporaryDirectory() as d:
        Path(d, "character").mkdir()
        Path(d, "character", "CharacterStats.ets").write_text(
            "export struct CharacterStats { @State hp: number = 100 }", encoding="utf-8")
        # 桩 LLM：对 Index.ets 返回 demo_body 填充
        orig, fake = _patch_fake(
            '{"entry/src/main/ets/pages/Index.ets": {"demo_body": "// demo filled"}}'
        )
        try:
            result = asyncio.run(run_scaffold({
                "project_name": "rpgdemo",
                "scan_dir": d,
            }))
        finally:
            import generators.framework as fw
            fw.AsyncAnthropic = orig
    paths = [f["path"] for f in result["files"]]
    # 子系统文件在前，带 project 前缀与 game/ 路径
    assert "rpgdemo/entry/src/main/ets/game/character/CharacterStats.ets" in paths, paths
    # 搬运内容与源一致
    moved = next(f for f in result["files"]
                 if f["path"] == "rpgdemo/entry/src/main/ets/game/character/CharacterStats.ets")
    assert "export struct CharacterStats { @State hp: number = 100 }" in moved["content"]
    # 配置文件也带 project 前缀
    assert "rpgdemo/AppScope/app.json5" in paths
    assert "rpgdemo/entry/src/main/ets/pages/Index.ets" in paths
    # app.json5 含 bundleName 替换
    app = next(f for f in result["files"] if f["path"] == "rpgdemo/AppScope/app.json5")
    assert "com.harmonygame.rpgdemo" in app["content"]
    assert "__ARG:" not in app["content"]
    # Index.ets 被填充
    idx = next(f for f in result["files"] if f["path"] == "rpgdemo/entry/src/main/ets/pages/Index.ets")
    assert "// demo filled" in idx["content"]
    assert "__LLM:" not in idx["content"]
    print("[OK] test_run_scaffold_copies_subsystem_files_with_project_prefix")


def test_run_scaffold_empty_scan_dir_still_produces_skeleton():
    with tempfile.TemporaryDirectory() as d:
        orig, fake = _patch_fake(
            '{"entry/src/main/ets/pages/Index.ets": {"demo_body": "build() { Column(){Text(\\\"empty\\\")} }"}}'
        )
        try:
            result = asyncio.run(run_scaffold({"project_name": "empty", "scan_dir": d}))
        finally:
            import generators.framework as fw
            fw.AsyncAnthropic = orig
    paths = [f["path"] for f in result["files"]]
    assert "empty/AppScope/app.json5" in paths
    assert not any("game/" in p for p in paths)  # 无子系统搬运
    print("[OK] test_run_scaffold_empty_scan_dir_still_produces_skeleton")


def test_run_scaffold_llm_failure_degrades_index():
    with tempfile.TemporaryDirectory() as d:
        # 桩 LLM 抛异常
        import generators.framework as fw
        class _Raising:
            async def create(self, **k):
                raise RuntimeError("余额不足")
        raising = _Raising()
        orig = fw.AsyncAnthropic
        fw.AsyncAnthropic = lambda *a, **k: SimpleNamespace(messages=raising)
        try:
            result = asyncio.run(run_scaffold({"project_name": "rpgdemo", "scan_dir": d}))
        finally:
            fw.AsyncAnthropic = orig
    idx = next(f for f in result["files"] if f["path"] == "rpgdemo/entry/src/main/ets/pages/Index.ets")
    assert "// TODO: 待填充 demo_body" in idx["content"]
    assert result["error"] != ""
    # 其余配置文件仍产出
    assert any(f["path"] == "rpgdemo/AppScope/app.json5" for f in result["files"])
    print("[OK] test_run_scaffold_llm_failure_degrades_index")
```

在 `main()` 追加：

```python
    test_run_scaffold_copies_subsystem_files_with_project_prefix()
    test_run_scaffold_empty_scan_dir_still_produces_skeleton()
    test_run_scaffold_llm_failure_degrades_index()
    print("\n全部通过。")
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run python generators/deveco_project_test.py`
Expected: `ImportError: cannot import name 'run_scaffold' from 'generators.deveco_project'`

- [ ] **Step 3: 写 `run_scaffold` 实现**

在 `generators/deveco_project.py` 追加：

```python
async def run_scaffold(args: dict) -> dict:
    """脚手架主逻辑：扫描 → 构造 spec → hybrid_generate → 拼接子系统搬运文件 → 加 project 前缀。"""
    project_name = args["project_name"]
    bundle_prefix = args.get("bundle_prefix") or "com.harmonygame"
    scan_dir = args.get("scan_dir") or "./generated"

    bundle, label = _sanitize_bundle(project_name, bundle_prefix)
    subs = _scan_subsystems(scan_dir)
    spec = build_deveco_project_spec(subs)

    gen_args = {
        "project_name": project_name,
        "bundle": bundle,
        "label": label,
    }
    result = await hybrid_generate(spec, gen_args)

    # 子系统搬运文件（带 project 前缀），前置拼到结果
    copied = [
        {"path": f"{project_name}/{f.dst}", "content": f.content}
        for sub in subs for f in sub.files
    ]
    # 给 hybrid_generate 产出的文件加 project 前缀
    prefixed = [
        {"path": f"{project_name}/{f['path']}", "content": f["content"]}
        for f in result["files"]
    ]
    return {"files": copied + prefixed, "error": result.get("error", "")}
```

- [ ] **Step 4: 运行测试确认通过**

Run: `uv run python generators/deveco_project_test.py`
Expected: 全部 `[OK]`，末行 `全部通过。`

- [ ] **Step 5: 导出符号**

修改 `generators/__init__.py`，在 import 与 `__all__` 增补：

```python
from generators.deveco_project import build_deveco_project_spec, run_scaffold
```
```python
__all__ = [
    "GeneratorSpec",
    "hybrid_generate",
    "build_character_stats_spec",
    "build_skill_system_spec",
    "build_inventory_spec",
    "build_enemy_ai_spec",
    "build_deveco_project_spec",
    "run_scaffold",
]
```

- [ ] **Step 6: 提交**

```bash
git add generators/deveco_project.py generators/deveco_project_test.py generators/__init__.py
git commit -m "$(cat <<'EOF'
feat(deveco): run_scaffold 脚手架主逻辑与全流程测试

run_scaffold：扫描子系统 → build_deveco_project_spec → hybrid_generate →
把搬运的子系统文件前置拼接 → 所有输出路径加 project_name 前缀。
测试覆盖：子系统搬运与路径前缀、空目录仍产出骨架、LLM 失败 Index.ets 降级。

Prompt: /brainstorming 鸿蒙游戏 Agent Phase 2 项目级能力

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 4: 注册工具与主 Agent 改动

**Files:**
- Modify: `tools.py`（注册 `scaffold_deveco_project`）
- Modify: `main.py`（`system_prompt` + `allowed_tools`）

**Interfaces:**
- Consumes: `run_scaffold` from Task 3、`_format_files` from `tools.py`

- [ ] **Step 1: 在 `tools.py` 注册工具**

在 `tools.py` 顶部 import 增补：

```python
from generators import (
    build_character_stats_spec,
    build_deveco_project_spec,
    build_enemy_ai_spec,
    build_inventory_spec,
    build_skill_system_spec,
    hybrid_generate,
    run_scaffold,
)
```

在 `review_arkts_code` 之前（或 4 个生成器之后）追加工具定义：

```python
@tool(
    "scaffold_deveco_project",
    build_deveco_project_spec([]).description,
    build_deveco_project_spec([]).input_schema,
)
async def scaffold_deveco_project(args):
    args = {
        "project_name": args["project_name"],
        "bundle_prefix": args.get("bundle_prefix", "com.harmonygame"),
        "scan_dir": args.get("scan_dir", "./generated"),
    }
    try:
        result = await run_scaffold(args)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"脚手架失败：{e}"}]}
    return {"content": [{"type": "text", "text": _format_files(result)}]}
```

在 `build_server()` 的 `tools=[...]` 列表末尾追加 `scaffold_deveco_project`：

```python
        tools=[
            generate_character_stats,
            generate_skill_system,
            generate_inventory,
            generate_enemy_ai,
            scaffold_deveco_project,
            review_arkts_code,
        ],
```

- [ ] **Step 2: 在 `main.py` 增补 system_prompt 与 allowed_tools**

修改 `build_options()` 的 `system_prompt`，在 `review_arkts_code` 行之前追加一行工具说明，并在末尾追加脚手架使用说明。最终 system_prompt 内容：

```python
        system_prompt=(
            "你是一名鸿蒙（HarmonyOS）原生游戏开发辅助助手，"
            "擅长 ArkTS/ArkUI、DevEco Studio、Cocos 等鸿蒙游戏开发技术栈，专注 RPG/战斗类游戏。\n"
            "你可以调用以下工具：\n"
            "- generate_character_stats：生成角色属性系统（属性/经验/升级/属性面板）\n"
            "- generate_skill_system：生成技能与 Buff 系统（技能/Buff/技能管理器）\n"
            "- generate_inventory：生成背包与装备系统（物品/背包/装备/背包 UI）\n"
            "- generate_enemy_ai：生成敌人与战斗 AI（敌人/状态机/战斗结算）\n"
            "- scaffold_deveco_project：扫描已生成的子系统文件，组装成完整 DevEco 工程，并生成战斗循环 demo 入口页\n"
            "- review_arkts_code：审查 ArkTS 代码并给出问题清单\n"
            "前四个工具会返回 {files: [{path, content}]}，每个文件含相对路径（如 character/CharacterStats.ets）"
            "与完整内容。当工具返回后，用 Write 工具把每个文件写入项目的 ./generated/ 目录，"
            "路径保持工具给出的相对路径（写入 ./generated/<子系统>/<文件>.ets），"
            "然后向用户说明生成了哪些文件、各自用途。\n"
            "当用户要求生成工程/脚手架/可运行 demo 时，调用 scaffold_deveco_project；"
            "它返回的文件路径带 <工程名>/ 前缀，用 Write 写入 ./generated/<工程名>/ 下对应路径。\n"
            "当用户要求审查代码时，调用 review_arkts_code。\n"
            "主动根据用户需求选择合适的工具，并结合工具返回结果给出说明。"
        ),
```

在 `allowed_tools` 列表中 `review_arkts_code` 行之前追加：

```python
            "mcp__harmony_tools__scaffold_deveco_project",
```

同步更新 REPL 启动提示（`repl()` 内的 print）：

```python
    print("可用工具：generate_character_stats / generate_skill_system / "
          "generate_inventory / generate_enemy_ai / scaffold_deveco_project / review_arkts_code")
```

- [ ] **Step 3: 静态校验**

Run: `uv run python -c "from tools import build_server; s=build_server(); print('tools OK')"`
Expected: 输出 `tools OK`，无异常。

Run: `uv run python -c "from main import build_options; o=build_options(); print('prompt OK' if 'scaffold_deveco_project' in o.system_prompt else 'MISSING')"`
Expected: 输出 `prompt OK`。

- [ ] **Step 4: 冒烟验证（不调真实 LLM）**

Run: `uv run python generators/deveco_project_test.py`
Expected: 仍全部通过（确认注册改动未破坏生成器）。

- [ ] **Step 5: 提交**

```bash
git add tools.py main.py
git commit -m "$(cat <<'EOF'
feat(deveco): 注册 scaffold_deveco_project 工具并更新主 Agent 提示

tools.py 注册 scaffold_deveco_project（@tool + build_server）；
main.py system_prompt 增补工具说明与写入路径指引，
allowed_tools 增 mcp__harmony_tools__scaffold_deveco_project，
REPL 启动提示同步更新。

Prompt: /brainstorming 鸿蒙游戏 Agent Phase 2 项目级能力

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

### Task 5: 集成验证与文档

**Files:**
- Modify: `README.md`（增补脚手架工具说明）

- [ ] **Step 1: 跑完整测试套件**

Run: `uv run python generators/framework_test.py && uv run python generators/deveco_project_test.py`
Expected: 两文件均全部 `[OK]`，无 `__ARG:` / `__LLM:` 残留断言通过。

- [ ] **Step 2: 真实端到端冒烟（可选，需 API 额度）**

仅在 `.env` 已配 `ANTHROPIC_API_KEY` 且有额度时执行。先造子系统文件：

```bash
mkdir -p generated/character
cat > generated/character/CharacterStats.ets <<'ETS'
export struct CharacterStats { @State hp: number = 100 }
ETS
```

启动 web 服务：`uv run python server.py`，浏览器输入"脚手架一个叫 rpgdemo 的工程"，观察 Agent 调用 `scaffold_deveco_project` 并 Write 出 `./generated/rpgdemo/` 下全套 DevEco 文件。检查 `entry/src/main/ets/pages/Index.ets` 是否含战斗循环 demo、`AppScope/app.json5` 的 bundleName 是否为 `com.harmonygame.rpgdemo`。

若无额度，跳过本步并在提交说明里注明"端到端冒烟未执行，依赖真实 API"。

- [ ] **Step 3: 更新 README**

在 `README.md` 的工具列表与文件结构表中追加 `scaffold_deveco_project` 一行，并在"运行"小节末尾追加示例：

```
你> 脚手架一个叫 rpgdemo 的 DevEco 工程，把已生成的子系统组装进去
```

并在"说明"末尾追加一条：DevEco 工程的 `app_icon.png` 为二进制媒体，主 Agent 的 Write 仅文本，需用户自行放入 `entry/src/main/resources/base/media/` 后再编译。

- [ ] **Step 4: 提交**

```bash
git add README.md
git commit -m "$(cat <<'EOF'
docs: 增补 DevEco 脚手架工具说明与媒体图标提示

README 增加 scaffold_deveco_project 工具说明、运行示例与
app_icon.png 需用户后补的提示。

Prompt: /brainstorming 鸿蒙游戏 Agent Phase 2 项目级能力

Co-Authored-By: Claude Sonnet 4.6 <noreply@anthropic.com>
EOF
)"
```

---

## Self-Review

**1. Spec coverage：**
- 扫描与子系统发现（spec §扫描与子系统发现）→ Task 1 ✓
- 空工程兜底 → Task 1 `test_scan_subsystems_empty_dir_returns_empty` + Task 3 `test_run_scaffold_empty_scan_dir_still_produces_skeleton` ✓
- bundleName sanitize → Task 2 ✓
- 15 个 DevEco 配置/资源模板（含多语言 en_US/zh_CN）→ Task 2 模板清单 ✓
- Index.ets LLM 填充战斗循环 + import 清单注入 → Task 2 `build_deveco_project_spec` + Task 3 全流程测试 ✓
- 子系统文件搬运不重写、带 project 前缀 → Task 3 ✓
- LLM 失败降级（Index.ets 空骨架，其余照常）→ Task 3 `test_run_scaffold_llm_failure_degrades_index` ✓
- 工具接口 `project_name`/`bundle_prefix`/`scan_dir` → Task 2 spec + Task 3 `run_scaffold` + Task 4 `@tool` ✓
- 主 Agent 改动（allowed_tools + system_prompt）→ Task 4 ✓
- 跨子系统互引为已知限制（spec §风险）→ 不需代码，Global Constraints 已载明 ✓
- 二进制媒体豁免（spec 调和）→ Global Constraints + Task 5 README 提示 ✓

**2. Placeholder scan：** 无 TBD/TODO（除代码内 `// TODO: 待填充` 降级标记，那是框架既有行为）。每步均含完整代码与命令。✓

**3. Type consistency：**
- `Subsystem`/`SubsystemFile` 在 Task 1 定义，Task 2/3 使用，字段名一致（`name`/`files`/`src`/`dst`/`exports`/`content`）✓
- `_import_specifier(subsystem, filename)` 签名 Task 1 定义、Task 2 `_import_lines` 调用一致 ✓
- `_sanitize_bundle(project_name, bundle_prefix) -> (bundle, label)` Task 2 定义、Task 3 `run_scaffold` 调用一致 ✓
- `build_deveco_project_spec(subsystems) -> GeneratorSpec` Task 2 定义、Task 3/4 调用一致 ✓
- `run_scaffold(args) -> {"files","error"}` Task 3 定义、Task 4 `@tool` 调用一致 ✓
- `hybrid_generate` 返回 `{"files","error"}`，`run_scaffold` 读 `result["files"]`/`result.get("error")` 一致 ✓

无问题。

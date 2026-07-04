"""DevEco 工程脚手架生成器。

扫描 ./generated/<子系统>/ 已有 RPG 文件，组装成完整鸿蒙 stage 模型工程，
Index.ets 用 LLM 填充战斗循环 demo。复用 framework.hybrid_generate。
"""

import os
import re
from dataclasses import dataclass, field

from generators.framework import FileSpec, GeneratorSpec, hybrid_generate
from harmony_sdk_policy import (
    COMPATIBLE_API_LEVEL,
    COMPATIBLE_SDK_VERSION,
    SDK_POLICY_TEXT,
    TARGET_SDK_VERSION,
)

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
            with open(src, encoding="utf-8") as f:
                content = f.read()
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


# ---------- DevEco 模板（确定性，__ARG__ 占位） ----------

_APP_JSON5 = f"""{{
  "app": {{
    "bundleName": "__ARG:bundle__",
    "vendor": "harmony-game-agent",
    "versionCode": 1,
    "versionName": "1.0.0",
    "minAPIVersion": {COMPATIBLE_API_LEVEL},
    "icon": "$media:app_icon",
    "label": "$string:app_name"
  }}
}}
"""

_APP_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96">
  <rect width="96" height="96" rx="20" fill="#1F6FEB"/>
  <path d="M27 62 L48 22 L69 62 Z" fill="#FFFFFF"/>
  <circle cx="48" cy="58" r="8" fill="#7EE787"/>
</svg>
"""

_MODULE_JSON5 = """{
  "module": {
    "name": "entry",
    "type": "entry",
    "description": "$string:module_desc",
    "mainElement": "EntryAbility",
    "deviceTypes": ["phone", "tablet"],
    "deliveryWithInstall": true,
    "installationFree": false,
    "pages": "$profile:main_pages",
    "abilities": [
      {
        "name": "EntryAbility",
        "srcEntry": "./ets/entryability/EntryAbility.ets",
        "description": "$string:EntryAbility_desc",
        "label": "$string:EntryAbility_label",
        "startWindowIcon": "$media:startIcon",
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
// 由 scaffold_deveco_project 生成，LLM 填充战斗逻辑。
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
    { "name": "EntryAbility_label", "value": "__ARG:label__" }
  ]
}
"""

_STRING_JSON_EN = _STRING_JSON_BASE

_STRING_JSON_ZH = """{
  "string": [
    { "name": "module_desc", "value": "模块描述" },
    { "name": "EntryAbility_desc", "value": "入口能力" },
    { "name": "EntryAbility_label", "value": "__ARG:label__" }
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

_START_ICON_SVG = """<svg xmlns="http://www.w3.org/2000/svg" width="96" height="96" viewBox="0 0 96 96">
  <rect width="96" height="96" rx="20" fill="#238636"/>
  <path d="M30 48 H66" stroke="#FFFFFF" stroke-width="10" stroke-linecap="round"/>
  <path d="M52 32 L68 48 L52 64" fill="none" stroke="#FFFFFF" stroke-width="10" stroke-linecap="round" stroke-linejoin="round"/>
</svg>
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
    { "name": "default", "runtimeOS": "HarmonyOS" }
  ]
}
"""

_ROOT_HVIGORFILE_TS = """import { appTasks } from '@ohos/hvigor-ohos-plugin';
export default {
  system: appTasks,
  plugins: []
}
"""

_ENTRY_HVIGORFILE_TS = """import { hapTasks } from '@ohos/hvigor-ohos-plugin';
export default {
  system: hapTasks,
  plugins: []
}
"""

_ROOT_BUILD_PROFILE = f"""{{
  "app": {{
    "signingConfigs": [],
    "products": [
      {{
        "name": "default",
        "compatibleSdkVersion": "{COMPATIBLE_SDK_VERSION}",
        "targetSdkVersion": "{TARGET_SDK_VERSION}",
        "runtimeOS": "HarmonyOS"
      }}
    ]
  }},
  "modules": [
    {{
      "name": "entry",
      "srcPath": "./entry",
      "targets": [
        {{ "name": "default", "applyToProducts": ["default"] }}
      ]
    }}
  ]
}}
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

_ENTRY_OH_PACKAGE_JSON5 = """{
  "name": "entry",
  "version": "1.0.0",
  "description": "entry module",
  "main": "",
  "license": "MIT",
  "dependencies": {}
}
"""

_APP_STRING_JSON = """{
  "string": [
    { "name": "app_name", "value": "__ARG:label__" }
  ]
}
"""

_APP_COLOR_JSON = """{
  "color": [
    { "name": "start_window_background", "value": "#FFFFFF" }
  ]
}
"""

# ---------- safe slug (C1) ----------

def _safe_project_slug(project_name: str) -> str:
    """把 project_name 转成单一安全路径段。

    非 [A-Za-z0-9_-] 替换为 _，若结果为空或含路径分隔/.. 则 fallback 为 "game"。
    用于输出路径前缀与 oh-package name。
    """
    slug = re.sub(r"[^A-Za-z0-9_-]", "_", project_name).lower()
    if not slug or slug == ".." or "/" in slug or "\\" in slug:
        return "game"
    return slug


# ---------- sanitize (I3) ----------

def _sanitize_bundle(project_name: str, bundle_prefix: str) -> tuple[str, str]:
    """bundleName = <bundle_prefix>.<sanitized>；label 保留原展示名。

    若 sanitized 不含任何 [a-z0-9]（纯中文/空字符串），fallback 为 "game"。
    """
    sanitized = re.sub(r"[^a-z0-9_]", "_", project_name.lower())
    sanitized = re.sub(r"_+", "_", sanitized)  # 坍缩连续下划线
    # 避免开头数字
    if sanitized and sanitized[0].isdigit():
        sanitized = "_" + sanitized
    # I3: 若 sanitized 不含任何字母数字或为空，fallback 为 "game"
    if not sanitized or not re.search(r"[a-z0-9]", sanitized):
        sanitized = "game"
    bundle = f"{bundle_prefix}.{sanitized}"
    return bundle, project_name


# ---------- spec 构造 ----------

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


def _build_imports(subsystems: list[Subsystem]) -> str:
    """为 Index.ets 顶部生成确定性 import 语句块。

    对每个子系统文件的每个 export 符号生成一行 import 语句。
    无子系统时返回空串。
    """
    if not subsystems:
        return ""
    lines = []
    for sub in subsystems:
        for f in sub.files:
            if not f.exports:
                continue
            spec = _import_specifier(sub.name, os.path.basename(f.dst))
            syms = ", ".join(f.exports)
            lines.append(f"import {{ {syms} }} from '{spec}';")
    return "\n".join(lines)


def build_deveco_project_spec(subsystems: list[Subsystem]) -> GeneratorSpec:
    """构造 DevEco 工程的 GeneratorSpec。路径相对工程根（不含 project_name 前缀，由工具层拼接）。"""
    imports = _import_lines(subsystems)
    if subsystems:
        instruction = (
            "为一个鸿蒙 ArkTS 战斗循环入口页填充 demo_body。可用 import（已在顶部确定性生成）：\n"
            f"{imports}\n\n"
            f"{SDK_POLICY_TEXT}"
            "要求：在 struct Index 内声明状态字段并实例化已导入的角色/敌人/技能/背包；"
            "build() 返回一个战斗循环 UI——攻击按钮触发战斗结算并刷新血量；技能冷却与释放；"
            "多敌人轮换；回合与即时两种模式切换；血量/属性面板刷新。"
            "约束：只用上面列出的 import，不臆造不存在的符号；不要重复 @Entry/@Component/struct 声明，"
            "只填 struct Index 内部（状态字段、build 方法体等），不要输出 import 语句。"
        )
    else:
        instruction = (
            f"{SDK_POLICY_TEXT}"
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
            FileSpec("AppScope/resources/base/element/string.json", _APP_STRING_JSON),
            FileSpec("AppScope/resources/base/media/app_icon.svg", _APP_ICON_SVG),
            FileSpec("entry/src/main/module.json5", _MODULE_JSON5),
            FileSpec("entry/src/main/ets/entryability/EntryAbility.ets", _ENTRY_ABILITY_ETS),
            FileSpec("entry/src/main/ets/pages/Index.ets", _INDEX_ETS, fill_targets=["demo_body"]),
            FileSpec("entry/src/main/resources/base/element/string.json", _STRING_JSON_BASE),
            FileSpec("entry/src/main/resources/base/element/color.json", _COLOR_JSON),
            FileSpec("entry/src/main/resources/base/element/float.json", _FLOAT_JSON),
            FileSpec("entry/src/main/resources/base/media/startIcon.svg", _START_ICON_SVG),
            FileSpec("entry/src/main/resources/base/profile/main_pages.json", _MAIN_PAGES_JSON),
            FileSpec("entry/src/main/resources/en_US/element/string.json", _STRING_JSON_EN),
            FileSpec("entry/src/main/resources/zh_CN/element/string.json", _STRING_JSON_ZH),
            FileSpec("entry/build-profile.json5", _ENTRY_BUILD_PROFILE),
            FileSpec("entry/hvigorfile.ts", _ENTRY_HVIGORFILE_TS),
            FileSpec("entry/oh-package.json5", _ENTRY_OH_PACKAGE_JSON5),
            FileSpec("build-profile.json5", _ROOT_BUILD_PROFILE),
            FileSpec("hvigorfile.ts", _ROOT_HVIGORFILE_TS),
            FileSpec("oh-package.json5", _OH_PACKAGE_JSON5),
        ],
        fill_instruction=instruction,
        max_tokens=4096,
    )


async def run_scaffold(args: dict) -> dict:
    """脚手架主逻辑：扫描 → 构造 spec → hybrid_generate → 拼接子系统搬运文件 → 加 project 前缀。"""
    project_name = args["project_name"]
    bundle_prefix = args.get("bundle_prefix") or "com.harmonygame"
    scan_dir = args.get("scan_dir") or "./generated"

    # C1: 用安全 slug 拼输出路径前缀与 oh-package name；label 保留原始 project_name
    slug = _safe_project_slug(project_name)
    bundle, label = _sanitize_bundle(project_name, bundle_prefix)
    subs = _scan_subsystems(scan_dir)
    spec = build_deveco_project_spec(subs)

    gen_args = {
        "project_name": slug,       # I1: oh-package name 用安全 slug（小写合规）
        "bundle": bundle,
        "label": label,             # label 保留原始 project_name 用于展示
        "imports": _build_imports(subs),  # I2: 确定性 import 语句块
    }
    result = await hybrid_generate(spec, gen_args)

    # 子系统搬运文件（带 slug 前缀），前置拼到结果
    copied = [
        {"path": f"{slug}/{f.dst}", "content": f.content}
        for sub in subs for f in sub.files
    ]
    # 给 hybrid_generate 产出的文件加 slug 前缀
    prefixed = [
        {"path": f"{slug}/{f['path']}", "content": f["content"]}
        for f in result["files"]
    ]
    return {"files": copied + prefixed, "error": result.get("error", ""), "findings": result.get("findings", [])}

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
            content = open(src, encoding="utf-8").read()
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


# ---------- 脚手架骨架（Phase 2 后续任务逐步填充） ----------

def _sanitize_bundle(bundle: dict) -> dict:
    """清洗 LLM 返回的工程配置 bundle（占位，后续任务实现）。"""
    return bundle


def build_deveco_project_spec(
    subsystems: list[Subsystem],
    bundle: dict,
) -> GeneratorSpec:
    """构建 DevEco 工程脚手架的 GeneratorSpec（占位，后续任务实现）。"""
    return GeneratorSpec(
        name="deveco_project",
        description="占位",
        input_schema={},
        files=[],
        fill_instruction="占位",
    )


async def run_scaffold(scan_dir: str, bundle: dict) -> dict:
    """一站式脚手架生成（占位，后续任务实现）。"""
    return {"files": [], "error": ""}

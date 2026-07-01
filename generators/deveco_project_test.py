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


def main():
    test_extract_exports_struct_class_const_enum()
    test_extract_exports_empty_when_none()
    test_scan_subsystems_finds_character_and_skill()
    test_scan_subsystems_empty_dir_returns_empty()
    test_scan_subsystems_skips_non_ets_files()
    test_import_specifier_from_pages_to_game()
    test_sanitize_bundle_basic()
    test_sanitize_bundle_normalizes_illegal()
    test_sanitize_bundle_custom_prefix()
    test_build_spec_has_all_deterministic_files()
    test_build_spec_index_ets_has_llm_slot()
    test_build_spec_fill_instruction_lists_imports()
    test_build_spec_fill_instruction_empty_when_no_subsystems()
    print("\n全部通过。")


if __name__ == "__main__":
    main()

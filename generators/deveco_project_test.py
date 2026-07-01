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

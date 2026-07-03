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
    _safe_project_slug,
    _build_imports,
    _import_specifier,
    _MAIN_PAGES_JSON,
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


# ---------- safe_project_slug (C1) ----------

def test_safe_project_slug_basic():
    assert _safe_project_slug("rpgdemo") == "rpgdemo"
    print("[OK] test_safe_project_slug_basic")


def test_safe_project_slug_empty_falls_back_to_game():
    assert _safe_project_slug("") == "game"
    print("[OK] test_safe_project_slug_empty_falls_back_to_game")


def test_safe_project_slug_path_traversal_sanitized():
    slug = _safe_project_slug("../../x")
    # 不得含路径分隔符或 ..
    assert "/" not in slug
    assert "\\" not in slug
    assert ".." not in slug
    # 应是单一安全段
    assert all(c.isalnum() or c in "_-" for c in slug)
    print(f"[OK] test_safe_project_slug_path_traversal_sanitized (slug={slug!r})")


def test_safe_project_slug_chinese_replaced():
    slug = _safe_project_slug("我的游戏")
    assert "/" not in slug
    assert ".." not in slug
    assert all(c.isalnum() or c in "_-" for c in slug)
    print(f"[OK] test_safe_project_slug_chinese_replaced (slug={slug!r})")


def test_run_scaffold_empty_project_name_safe_paths():
    """空 project_name 时所有路径以 game/ 前缀，不逃逸到 ./generated/ 外。"""
    with tempfile.TemporaryDirectory() as d:
        orig, afw_orig, fake = _patch_fake(
            '{"entry/src/main/ets/pages/Index.ets": {"demo_body": "// demo"}}'
        )
        try:
            result = asyncio.run(run_scaffold({
                "project_name": "",
                "scan_dir": d,
            }))
        finally:
            import generators.framework as fw
            import analyzers.framework as afw
            fw.AsyncAnthropic = orig
            afw.AsyncAnthropic = afw_orig
    for f in result["files"]:
        assert f["path"].startswith("game/"), f["path"]
        assert not f["path"].startswith("/"), f["path"]
    print("[OK] test_run_scaffold_empty_project_name_safe_paths")


# ---------- main_pages.json 路由格式 (C2) ----------

def test_main_pages_json_uses_pages_index_format():
    """模板应使用 pages/Index 而非 src/main/ets/pages/Index.ets。"""
    assert "pages/Index" in _MAIN_PAGES_JSON
    assert "src/main/ets/pages/Index.ets" not in _MAIN_PAGES_JSON
    print("[OK] test_main_pages_json_uses_pages_index_format")


def test_run_scaffold_main_pages_json_content():
    """run_scaffold 输出的 main_pages.json 应含 pages/Index。"""
    with tempfile.TemporaryDirectory() as d:
        orig, afw_orig, fake = _patch_fake(
            '{"entry/src/main/ets/pages/Index.ets": {"demo_body": "// demo"}}'
        )
        try:
            result = asyncio.run(run_scaffold({
                "project_name": "rpgdemo",
                "scan_dir": d,
            }))
        finally:
            import generators.framework as fw
            import analyzers.framework as afw
            fw.AsyncAnthropic = orig
            afw.AsyncAnthropic = afw_orig
    mp = next(f for f in result["files"]
              if f["path"] == "rpgdemo/entry/src/main/resources/base/profile/main_pages.json")
    assert "pages/Index" in mp["content"]
    assert "src/main/ets/pages/Index.ets" not in mp["content"]
    print("[OK] test_run_scaffold_main_pages_json_content")


# ---------- sanitize bundle 边界 (I3) ----------

def test_sanitize_bundle_pure_chinese_falls_back():
    bundle, label = _sanitize_bundle("我的", "com.harmonygame")
    assert bundle == "com.harmonygame.game", bundle
    assert label == "我的"
    print("[OK] test_sanitize_bundle_pure_chinese_falls_back")


def test_sanitize_bundle_empty_string_falls_back():
    bundle, label = _sanitize_bundle("", "com.harmonygame")
    assert bundle == "com.harmonygame.game", bundle
    assert label == ""
    print("[OK] test_sanitize_bundle_empty_string_falls_back")


# ---------- _build_imports & Index.ets 模板 (I2) ----------

def test_build_imports_generates_correct_lines():
    subs = _fake_subsystems()
    imports = _build_imports(subs)
    assert "import { CharacterStats } from '../game/character/CharacterStats';" in imports
    assert "import { StatsPanel } from '../game/character/StatsPanel';" in imports
    assert "import { Skill } from '../game/skill/Skill';" in imports
    print("[OK] test_build_imports_generates_correct_lines")


def test_build_imports_empty_when_no_subsystems():
    assert _build_imports([]) == ""
    print("[OK] test_build_imports_empty_when_no_subsystems")


def test_index_ets_template_has_imports_placeholder():
    spec = build_deveco_project_spec(_fake_subsystems())
    index = next(f for f in spec.files if f.path == "entry/src/main/ets/pages/Index.ets")
    assert "__ARG:imports__" in index.template
    print("[OK] test_index_ets_template_has_imports_placeholder")


def test_fill_instruction_says_only_fill_struct():
    spec = build_deveco_project_spec(_fake_subsystems())
    fi = spec.fill_instruction
    assert "只填 struct" in fi or "不要输出 import" in fi, fi
    print("[OK] test_fill_instruction_says_only_fill_struct")


def test_run_scaffold_index_ets_has_imports_and_no_residuals():
    """全流程：Index.ets 含确定性 import 语句，无占位符残留。"""
    with tempfile.TemporaryDirectory() as d:
        Path(d, "character").mkdir()
        Path(d, "character", "CharacterStats.ets").write_text(
            "export struct CharacterStats { @State hp: number = 100 }", encoding="utf-8")
        orig, afw_orig, fake = _patch_fake(
            '{"entry/src/main/ets/pages/Index.ets": {"demo_body": "// demo filled"}}'
        )
        try:
            result = asyncio.run(run_scaffold({
                "project_name": "rpgdemo",
                "scan_dir": d,
            }))
        finally:
            import generators.framework as fw
            import analyzers.framework as afw
            fw.AsyncAnthropic = orig
            afw.AsyncAnthropic = afw_orig
    idx = next(f for f in result["files"]
               if f["path"] == "rpgdemo/entry/src/main/ets/pages/Index.ets")
    assert "import { CharacterStats } from '../game/character/CharacterStats';" in idx["content"]
    assert "__ARG:" not in idx["content"]
    assert "__LLM:" not in idx["content"]
    print("[OK] test_run_scaffold_index_ets_has_imports_and_no_residuals")


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
        "AppScope/resources/base/element/string.json",
        "AppScope/resources/base/element/color.json",
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
        "entry/oh-package.json5",
        "build-profile.json5",
        "hvigorfile.ts",
        "oh-package.json5",
    ]
    assert paths == expected, paths
    print("[OK] test_build_spec_has_all_deterministic_files")


def test_scaffold_includes_missing_deveco_files():
    """脚手架须含 entry/oh-package.json5、AppScope resources、minAPIVersion。"""
    spec = build_deveco_project_spec([])  # 无子系统
    paths = [f.path for f in spec.files]
    assert "entry/oh-package.json5" in paths, "缺模块级 oh-package.json5"
    assert "AppScope/resources/base/element/string.json" in paths
    assert "AppScope/resources/base/element/color.json" in paths
    # signingConfig 不引用空 signingConfigs
    root_build = [f for f in spec.files if f.path == "build-profile.json5"][0].template
    assert '"signingConfig"' not in root_build, "root build-profile 不得引用空 signingConfigs"
    assert '"signingConfigs": []' in root_build, "signingConfigs 须为空数组"
    # app.json5 含 minAPIVersion
    app = [f for f in spec.files if f.path == "AppScope/app.json5"][0].template
    assert "minAPIVersion" in app
    print("[OK] test_scaffold_includes_missing_deveco_files")


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
    """桩 LLM：fw 用于填充，afw 用于审查（返回空 findings 避免真实调用）。

    Task 2 审查闭环启用后，deveco spec 含 __LLM:demo_body__ 会触发审查走
    真实 LLM 调用。须同时打桩 afw.AsyncAnthropic 返回 []，与 framework_test
    既有测试的打桩方式一致。
    """
    import generators.framework as fw
    import analyzers.framework as afw
    fake = _FakeAnthropic(return_text)
    orig = fw.AsyncAnthropic
    afw_orig = afw.AsyncAnthropic
    fw.AsyncAnthropic = lambda *a, **k: fake
    afw.AsyncAnthropic = lambda *a, **k: _FakeAnthropic("[]")
    return orig, afw_orig, fake


def test_run_scaffold_copies_subsystem_files_with_project_prefix():
    # 造扫描目录
    with tempfile.TemporaryDirectory() as d:
        Path(d, "character").mkdir()
        Path(d, "character", "CharacterStats.ets").write_text(
            "export struct CharacterStats { @State hp: number = 100 }", encoding="utf-8")
        # 桩 LLM：对 Index.ets 返回 demo_body 填充
        orig, afw_orig, fake = _patch_fake(
            '{"entry/src/main/ets/pages/Index.ets": {"demo_body": "// demo filled"}}'
        )
        try:
            result = asyncio.run(run_scaffold({
                "project_name": "rpgdemo",
                "scan_dir": d,
            }))
        finally:
            import generators.framework as fw
            import analyzers.framework as afw
            fw.AsyncAnthropic = orig
            afw.AsyncAnthropic = afw_orig
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
        orig, afw_orig, fake = _patch_fake(
            '{"entry/src/main/ets/pages/Index.ets": {"demo_body": "build() { Column(){Text(\"empty\")} }"}}'
        )
        try:
            result = asyncio.run(run_scaffold({"project_name": "empty", "scan_dir": d}))
        finally:
            import generators.framework as fw
            import analyzers.framework as afw
            fw.AsyncAnthropic = orig
            afw.AsyncAnthropic = afw_orig
    paths = [f["path"] for f in result["files"]]
    assert "empty/AppScope/app.json5" in paths
    assert not any("game/" in p for p in paths)  # 无子系统搬运
    print("[OK] test_run_scaffold_empty_scan_dir_still_produces_skeleton")


def test_run_scaffold_llm_failure_degrades_index():
    with tempfile.TemporaryDirectory() as d:
        # 桩 LLM 抛异常
        import generators.framework as fw
        import analyzers.framework as afw
        class _Raising:
            async def create(self, **k):
                raise RuntimeError("余额不足")
        raising = _Raising()
        orig = fw.AsyncAnthropic
        afw_orig = afw.AsyncAnthropic
        fw.AsyncAnthropic = lambda *a, **k: SimpleNamespace(messages=raising)
        # 审查打桩返回空 findings，避免真实调用
        afw.AsyncAnthropic = lambda *a, **k: _FakeAnthropic("[]")
        try:
            result = asyncio.run(run_scaffold({"project_name": "rpgdemo", "scan_dir": d}))
        finally:
            fw.AsyncAnthropic = orig
            afw.AsyncAnthropic = afw_orig
    idx = next(f for f in result["files"] if f["path"] == "rpgdemo/entry/src/main/ets/pages/Index.ets")
    assert "// TODO: 待填充 demo_body" in idx["content"]
    assert result["error"] != ""
    # 其余配置文件仍产出
    assert any(f["path"] == "rpgdemo/AppScope/app.json5" for f in result["files"])
    print("[OK] test_run_scaffold_llm_failure_degrades_index")


def test_run_scaffold_passes_through_findings():
    """I-2: run_scaffold 返回值须透传 hybrid_generate 的 findings 字段。"""
    with tempfile.TemporaryDirectory() as d:
        orig, afw_orig, fake = _patch_fake(
            '{"entry/src/main/ets/pages/Index.ets": {"demo_body": "// demo"}}'
        )
        # 覆盖审查桩：返回 1 条 finding，验证透传
        import analyzers.framework as afw
        review_payload = '[{"severity":"高","location":"Index.ets","summary":"test","fix":"fix","category":"测试"}]'
        afw.AsyncAnthropic = lambda *a, **k: _FakeAnthropic(review_payload)
        try:
            result = asyncio.run(run_scaffold({
                "project_name": "rpgdemo",
                "scan_dir": d,
            }))
        finally:
            import generators.framework as fw
            fw.AsyncAnthropic = orig
            afw.AsyncAnthropic = afw_orig
    # I-2: findings 须透传（_review_files 对每个文件调一次审查，桩对每次都返回同一条）
    assert "findings" in result, "run_scaffold 返回值缺 findings 字段"
    assert len(result["findings"]) >= 1, result["findings"]
    assert result["findings"][0]["summary"] == "test"
    print("[OK] test_run_scaffold_passes_through_findings")


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
    # C1
    test_safe_project_slug_basic()
    test_safe_project_slug_empty_falls_back_to_game()
    test_safe_project_slug_path_traversal_sanitized()
    test_safe_project_slug_chinese_replaced()
    test_run_scaffold_empty_project_name_safe_paths()
    # C2
    test_main_pages_json_uses_pages_index_format()
    test_run_scaffold_main_pages_json_content()
    # I3
    test_sanitize_bundle_pure_chinese_falls_back()
    test_sanitize_bundle_empty_string_falls_back()
    # I2
    test_build_imports_generates_correct_lines()
    test_build_imports_empty_when_no_subsystems()
    test_index_ets_template_has_imports_placeholder()
    test_fill_instruction_says_only_fill_struct()
    test_run_scaffold_index_ets_has_imports_and_no_residuals()
    # spec
    test_build_spec_has_all_deterministic_files()
    test_build_spec_index_ets_has_llm_slot()
    test_build_spec_fill_instruction_lists_imports()
    test_build_spec_fill_instruction_empty_when_no_subsystems()
    test_scaffold_includes_missing_deveco_files()
    # run_scaffold
    test_run_scaffold_copies_subsystem_files_with_project_prefix()
    test_run_scaffold_empty_scan_dir_still_produces_skeleton()
    test_run_scaffold_llm_failure_degrades_index()
    test_run_scaffold_passes_through_findings()
    print("\n全部通过。")


if __name__ == "__main__":
    main()

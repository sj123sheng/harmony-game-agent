"""skill_system/inventory/enemy_ai 模板结构断言（同 character_stats 范式）。

范式要点：
- 数据/逻辑类：@Observed export class，内部无 @State、无 @Component
- UI 面板：@Component struct，无 @Entry，@ObjectLink 接收数据类实例（无 = new 初始化），顶部显式 import
- 初始数值类 __LLM__ 占位符移入 constructor()；方法体内占位符原位
"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
from generators.skill_system import build_skill_system_spec
from generators.inventory import build_inventory_spec
from generators.enemy_ai import build_enemy_ai_spec


def _assert_data_class(tmpl, class_name):
    """数据/逻辑类范式断言：@Observed + export class + 无 @State + 无 @Component。"""
    assert "@Observed" in tmpl, f"{class_name} 数据类须 @Observed"
    assert "export class" in tmpl, f"{class_name} 须 export class"
    assert "@State" not in tmpl, f"{class_name} 数据类内部字段不应有 @State"
    assert "@Component" not in tmpl, f"{class_name} 数据类不应是 @Component（UI 装饰器）"


def _assert_panel(tmpl, panel_name, import_path):
    """UI 面板范式断言：@Component + 无 @Entry + @ObjectLink + 显式 import + 无 = new。"""
    assert "@Component" in tmpl, f"{panel_name} 须 @Component"
    assert "@Entry" not in tmpl, f"{panel_name} 须去 @Entry"
    assert "@ObjectLink" in tmpl, f"{panel_name} 须 @ObjectLink 接收数据类实例"
    assert import_path in tmpl, f"{panel_name} 须显式 import: {import_path}"
    # @ObjectLink 字段不应在声明处初始化
    assert "= new " not in tmpl.split("@ObjectLink")[1].split("\n")[0], \
        f"{panel_name} @ObjectLink 字段不得在声明处初始化（去 = new ...）"


def _assert_cross_file_import(tmpl, class_name, file_name):
    """跨文件类型引用断言：模板引用了其他 FileSpec export 的类型时，须有对应 import。"""
    expected = f"import {{ {class_name} }} from './{file_name}'"
    assert expected in tmpl, f"引用 {class_name} 类型须显式 import: {expected}"


def test_skill_system_template():
    spec = build_skill_system_spec()
    # File 0: Skill.ets — 数据类
    _assert_data_class(spec.files[0].template, "Skill")
    # File 1: Buff.ets — 数据类
    _assert_data_class(spec.files[1].template, "Buff")
    # File 2: SkillManager.ets — 逻辑类（非 UI 面板）
    _assert_data_class(spec.files[2].template, "SkillManager")
    # SkillManager 引用 Skill 与 Buff（跨文件），须显式 import
    skill_mgr_tmpl = spec.files[2].template
    _assert_cross_file_import(skill_mgr_tmpl, "Skill", "Skill")
    _assert_cross_file_import(skill_mgr_tmpl, "Buff", "Buff")
    print("[OK] test_skill_system_template")


def test_inventory_template():
    spec = build_inventory_spec()
    # File 0: Item.ets — 数据类
    _assert_data_class(spec.files[0].template, "Item")
    # File 1: Inventory.ets — 逻辑类（非 UI 面板）
    _assert_data_class(spec.files[1].template, "Inventory")
    # Inventory 引用 Item（跨文件），须显式 import
    _assert_cross_file_import(spec.files[1].template, "Item", "Item")
    # File 2: Equipment.ets — 逻辑类（非 UI 面板）
    _assert_data_class(spec.files[2].template, "Equipment")
    # Equipment 引用 Item（跨文件），须显式 import
    _assert_cross_file_import(spec.files[2].template, "Item", "Item")
    # File 3: InventoryUI.ets — UI 面板
    _assert_panel(spec.files[3].template, "InventoryUI",
                  "import { Inventory } from './Inventory'")
    print("[OK] test_inventory_template")


def test_enemy_ai_template():
    spec = build_enemy_ai_spec()
    # File 0: Enemy.ets — 数据类，enemy_stats 占位符须在 constructor 内
    enemy_tmpl = spec.files[0].template
    _assert_data_class(enemy_tmpl, "Enemy")
    assert "constructor()" in enemy_tmpl, "Enemy 须有 constructor() 供初始数值占位符"
    assert "__LLM:enemy_stats__" in enemy_tmpl, "enemy_stats 占位符须保留"
    # 占位符须在 constructor 内（constructor 之后、下一个方法之前）
    ctor_start = enemy_tmpl.find("constructor()")
    ctor_end = enemy_tmpl.find("}", enemy_tmpl.find("{", ctor_start))
    placeholder_pos = enemy_tmpl.find("__LLM:enemy_stats__")
    assert ctor_start < placeholder_pos < ctor_end, \
        "enemy_stats 占位符须在 constructor() 体内"
    # File 1: EnemyAI.ets — 逻辑类
    _assert_data_class(spec.files[1].template, "EnemyAI")
    # EnemyAI 引用 Enemy（跨文件），须显式 import
    _assert_cross_file_import(spec.files[1].template, "Enemy", "Enemy")
    # File 2: CombatResolver.ets — 逻辑类
    _assert_data_class(spec.files[2].template, "CombatResolver")
    # CombatResolver 引用 Enemy（跨文件），须显式 import
    _assert_cross_file_import(spec.files[2].template, "Enemy", "Enemy")
    print("[OK] test_enemy_ai_template")


def main():
    test_skill_system_template()
    test_inventory_template()
    test_enemy_ai_template()
    print("\n全部通过。")


if __name__ == "__main__":
    main()

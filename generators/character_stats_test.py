"""character_stats 模板结构断言：import / @Observed / @ObjectLink / 无重复 @Entry。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")
from generators.character_stats import build_character_stats_spec

def test_template_structure():
    spec = build_character_stats_spec()
    stats_tmpl = spec.files[0].template  # CharacterStats.ets
    panel_tmpl = spec.files[1].template  # StatsPanel.ets
    # 数据类 @Observed，内部无 @State
    assert "@Observed" in stats_tmpl
    assert "@State" not in stats_tmpl, "数据类字段应由 @Observed 观察，去掉 @State"
    # 面板 @ObjectLink 接收，无 @State 持有数据类，无 @Entry
    assert "@ObjectLink" in panel_tmpl
    assert "@Entry" not in panel_tmpl, "面板组件不是页面入口，去 @Entry"
    # 面板显式 import 数据类
    assert "import { CharacterStats } from './CharacterStats'" in panel_tmpl
    print("[OK] test_template_structure")

def main():
    test_template_structure()
    print("\n全部通过。")

if __name__ == "__main__":
    main()

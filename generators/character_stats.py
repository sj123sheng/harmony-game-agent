"""角色属性系统生成器。"""

from generators.framework import FileSpec, GeneratorSpec

_CHARACTER_STATS_TEMPLATE = """// 角色属性系统 - __ARG:character_name__（__ARG:archetype__ 型），等级上限 __ARG:level_cap__
// 提供基础属性、经验/等级、升级成长。可被战斗、面板等模块引用。
@Component
export struct CharacterStats {
  // ===== 等级与经验 =====
  @State level: number = 1
  @State exp: number = 0

  // ===== 基础属性 =====
  @State maxHp: number = 0
  @State currentHp: number = 0
  @State atk: number = 0
  @State def: number = 0
  @State critRate: number = 0      // 暴击率 [0,1]
  @State speed: number = 0         // 行动速度

  __LLM:initial_stats__

  // 升到下一级所需经验
  expToNext(level: number): number {
    __LLM:growth_formula__
  }

  // 增加经验，达阈值自动升级
  gainExp(amount: number): void {
    this.exp += amount
    while (this.level < __ARG:level_cap__ && this.exp >= this.expToNext(this.level)) {
      this.exp -= this.expToNext(this.level)
      this.level++
      this.onLevelUp()
    }
  }

  // 升级时按成长公式提升属性
  onLevelUp(): void {
    __LLM:levelup_logic__
  }
}
"""

_STATS_PANEL_TEMPLATE = """// 角色属性面板 UI - __ARG:character_name__
// 入口组件，展示 CharacterStats 的数值。引用 CharacterStats 需在同工程内。
@Entry
@Component
struct StatsPanel {
  @State stats: CharacterStats = new CharacterStats()

  build() {
    Column() {
      Text('__ARG:character_name__ · 属性面板')
        .fontSize(22)
        .margin(20)
      __LLM:panel_layout__
    }
    .width('100%')
    .height('100%')
    .justifyContent(FlexAlign.Center)
  }
}
"""


def build_character_stats_spec() -> GeneratorSpec:
    return GeneratorSpec(
        name="generate_character_stats",
        description=(
            "生成角色属性系统：基础属性（生命/攻击/防御/暴击/速度）、经验/等级、"
            "升级成长公式，以及属性面板 UI。输出 CharacterStats.ets 与 StatsPanel.ets。"
            "参数：character_name 角色名；archetype 流派（战士/法师/刺客，决定数值倾向）；"
            "level_cap 等级上限，默认 99。"
        ),
        input_schema={
            "character_name": str,
            "archetype": str,
            "level_cap": int,
        },
        files=[
            FileSpec(
                path="character/CharacterStats.ets",
                template=_CHARACTER_STATS_TEMPLATE,
                fill_targets=["initial_stats", "growth_formula", "levelup_logic"],
            ),
            FileSpec(
                path="character/StatsPanel.ets",
                template=_STATS_PANEL_TEMPLATE,
                fill_targets=["panel_layout"],
            ),
        ],
        fill_instruction=(
            "为一个鸿蒙 ArkTS 角色属性系统填充细节。"
            "按骨架顶部注释中体现的流派倾向给数值（战士偏血量与防御，法师偏攻击与暴击，刺客偏速度与暴击）。"
            "initial_stats 填 @State 初始化（maxHp/currentHp/atk/def/critRate/speed 的初始数值赋值）；"
            "growth_formula 填 expToNext 的返回表达式（按 level 计算）；"
            "levelup_logic 填 onLevelUp 内的属性提升语句；"
            "panel_layout 填 StatsPanel build() 内展示各属性的 Text/Row 组件。"
            "只填占位符位置，不要重复 struct/build 声明。"
        ),
        # 中转网关常强制开启 thinking，需给思考+输出留足预算
        max_tokens=4096,
    )

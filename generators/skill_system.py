"""技能与 Buff 系统生成器。"""

from generators.framework import FileSpec, GeneratorSpec

_SKILL_TEMPLATE = """// 技能定义 - __ARG:combat_style__ 战斗风格，共 __ARG:skill_count__ 个技能
// 数据类 @Observed，字段变更可被 @ObjectLink 子组件响应。
@Observed
export class Skill {
  id: number = 0
  name: string = ''
  cooldown: number = 0      // 冷却时间（毫秒）
  cost: number = 0          // 消耗（魔法/能量）
  damageFormula: string = '' // 伤害公式描述

  __LLM:skill_fields__

  // 释放技能的效果逻辑（返回伤害值或 0）
  cast(casterAtk: number, targetDef: number): number {
    __LLM:skill_cast_logic__
  }
}

// 技能实例清单
export const SKILL_DEFS: Skill[] = [
  __LLM:skill_defs__
]
"""

_BUFF_TEMPLATE = """// Buff/Debuff 定义与结算
// 数据类 @Observed，提供 Buff 数据结构、叠加/过期规则。可被 SkillManager 挂载与每帧结算。
@Observed
export class Buff {
  id: string = ''
  name: string = ''
  duration: number = 0     // 持续时间（毫秒）
  stacks: number = 0       // 当前叠加层数
  maxStacks: number = 1

  __LLM:buff_fields__

  // 每帧结算：返回本帧造成/恢复的数值
  tick(deltaMs: number): number {
    __LLM:buff_tick_logic__
  }
}
"""

_SKILL_MANAGER_TEMPLATE = """// 技能管理器 - __ARG:combat_style__ 战斗风格
// 数据类 @Observed，管理技能冷却、释放流程、Buff 挂载与每帧结算。
@Observed
export class SkillManager {
  cooldowns: Map<number, number> = new Map()  // 技能id -> 剩余冷却
  activeBuffs: Buff[] = []

  // 释放技能：检查冷却/消耗 → 结算伤害 → 挂载附带 Buff
  castSkill(skill: Skill, casterAtk: number, targetDef: number): number {
    __LLM:cast_flow__
  }

  // 每帧推进：冷却递减、Buff 过期结算
  update(deltaMs: number): void {
    __LLM:update_logic__
  }

  // 挂载 Buff，处理叠加规则
  applyBuff(buff: Buff): void {
    __LLM:apply_buff_logic__
  }
}
"""


def build_skill_system_spec() -> GeneratorSpec:
    return GeneratorSpec(
        name="generate_skill_system",
        description=(
            "生成技能与 Buff 系统：技能定义、Buff/Debuff、技能管理器（冷却/释放/Buff 结算）。"
            "输出 Skill.ets、Buff.ets、SkillManager.ets。"
            "参数：skill_count 技能数量默认 4；include_buffs 是否生成 Buff 默认 true；"
            "combat_style 战斗风格（即时/回合）。"
        ),
        input_schema={
            "skill_count": int,
            "include_buffs": bool,
            "combat_style": str,
        },
        files=[
            FileSpec(
                path="skill/Skill.ets",
                template=_SKILL_TEMPLATE,
                fill_targets=["skill_fields", "skill_cast_logic", "skill_defs"],
            ),
            FileSpec(
                path="skill/Buff.ets",
                template=_BUFF_TEMPLATE,
                fill_targets=["buff_fields", "buff_tick_logic"],
            ),
            FileSpec(
                path="skill/SkillManager.ets",
                template=_SKILL_MANAGER_TEMPLATE,
                fill_targets=["cast_flow", "update_logic", "apply_buff_logic"],
            ),
        ],
        fill_instruction=(
            "为鸿蒙 ArkTS 技能与 Buff 系统填充细节，战斗风格见骨架顶部注释。"
            "skill_fields 填技能额外字段声明（如范围、目标类型），不要赋初始数值；"
            "skill_cast_logic 填 cast 内按 casterAtk/targetDef 计算伤害并返回的语句；"
            "skill_defs 填若干 Skill 对象字面量（数量=skill_count，含合理数值）；"
            "buff_fields 填 Buff 额外字段声明（如类型、数值），不要赋初始数值；"
            "buff_tick_logic 填 tick 内按 stacks 结算的语句；"
            "cast_flow 填释放流程（冷却检查、伤害结算、附带 Buff 挂载）；"
            "update_logic 填每帧冷却递减与 Buff 过期清理；"
            "apply_buff_logic 填叠加规则（同 id 叠层/刷新时长）。"
            "只填占位符位置，不要重复 class 声明。"
        ),
        # 中转网关常强制开启 thinking，需给思考+输出留足预算
        max_tokens=4096,
    )

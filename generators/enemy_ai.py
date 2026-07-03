"""敌人与战斗 AI 生成器。"""

from generators.framework import FileSpec, GeneratorSpec

_ENEMY_TEMPLATE = """// 敌人定义 - __ARG:enemy_name__（__ARG:ai_pattern__ 型，__ARG:difficulty__）
// 数据类 @Observed，提供敌人属性与伤害结算接口，可被 EnemyAI 与 CombatResolver 调用。
@Observed
export class Enemy {
  maxHp: number = 0
  currentHp: number = 0
  atk: number = 0
  def: number = 0
  speed: number = 0

  constructor() {
    __LLM:enemy_stats__
  }

  // 受到伤害结算，返回实际扣血
  takeDamage(rawDamage: number): number {
    __LLM:take_damage_logic__
  }

  // 判定是否死亡
  isDead(): boolean {
    return this.currentHp <= 0
  }
}
"""

_ENEMY_AI_TEMPLATE = """// 敌人 AI 状态机 - __ARG:enemy_name__，模式 __ARG:ai_pattern__，难度 __ARG:difficulty__
// 数据类 @Observed，状态：Patrol 巡逻 / Chase 追击 / Attack 攻击 / Hurt 受击 / Dead 死亡
@Observed
export class EnemyAI {
  state: string = 'Patrol'
  target: Enemy = new Enemy()

  // 每帧决策：根据距离/血量切换状态并执行行为
  think(distanceToPlayer: number, deltaMs: number): void {
    __LLM:think_logic__
  }

  // 状态转移条件
  transition(newState: string): void {
    __LLM:transition_logic__
  }
}
"""

_COMBAT_RESOLVER_TEMPLATE = """// 战斗结算器 - __ARG:difficulty__ 难度
// 数据类 @Observed，处理攻击命中、伤害计算，对接 CharacterStats 与 Enemy。
@Observed
export class CombatResolver {
  // 玩家攻击敌人
  playerAttackEnemy(casterAtk: number, target: Enemy, critRate: number): number {
    __LLM:player_attack_logic__
  }

  // 敌人攻击玩家
  enemyAttackPlayer(enemyAtk: number, targetDef: number): number {
    __LLM:enemy_attack_logic__
  }

  // 命中判定与暴击结算
  rollHit(critRate: number): { hit: boolean; crit: boolean } {
    __LLM:roll_logic__
  }
}
"""


def build_enemy_ai_spec() -> GeneratorSpec:
    return GeneratorSpec(
        name="generate_enemy_ai",
        description=(
            "生成敌人与战斗 AI：敌人属性与伤害结算、AI 状态机（巡逻/追击/攻击/受击/死亡）、"
            "战斗结算器（命中/暴击/伤害计算）。"
            "输出 Enemy.ets、EnemyAI.ets、CombatResolver.ets。"
            "参数：enemy_name 敌人名；ai_pattern AI 模式（巡逻/追击/远程/Boss）；"
            "difficulty 难度（简单/普通/困难）。"
        ),
        input_schema={
            "enemy_name": str,
            "ai_pattern": str,
            "difficulty": str,
        },
        files=[
            FileSpec(
                path="enemy/Enemy.ets",
                template=_ENEMY_TEMPLATE,
                fill_targets=["enemy_stats", "take_damage_logic"],
            ),
            FileSpec(
                path="enemy/EnemyAI.ets",
                template=_ENEMY_AI_TEMPLATE,
                fill_targets=["think_logic", "transition_logic"],
            ),
            FileSpec(
                path="enemy/CombatResolver.ets",
                template=_COMBAT_RESOLVER_TEMPLATE,
                fill_targets=["player_attack_logic", "enemy_attack_logic", "roll_logic"],
            ),
        ],
        fill_instruction=(
            "为鸿蒙 ArkTS 敌人与战斗 AI 填充细节。敌人名、AI 模式、难度见骨架顶部注释，"
            "数值需随难度调整（困难数值更高、AI 决策更激进）。"
            "enemy_stats 填 constructor 内对字段的赋值语句（如 this.maxHp = 100; this.atk = 20 等，按难度给数值，不要重复字段声明）；"
            "take_damage_logic 填 takeDamage：按 def 减伤、扣血、返回实际值；"
            "think_logic 填 think：按 distanceToPlayer 切状态（巡逻↔追击↔攻击），Boss 模式需含技能选择；"
            "transition_logic 填 transition：状态合法性校验与切换；"
            "player_attack_logic 填 playerAttackEnemy：命中判定、暴击倍率、调用 takeDamage；"
            "enemy_attack_logic 填 enemyAttackPlayer：按 atk 与 targetDef 结算；"
            "roll_logic 填 rollHit：按 critRate 掷随机命中与暴击。"
            "只填占位符位置，不要重复 class 声明。"
        ),
        # 中转网关常强制开启 thinking，需给思考+输出留足预算
        max_tokens=4096,
    )

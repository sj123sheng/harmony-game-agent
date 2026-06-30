"""RPG 工具集生成器包。"""

from generators.framework import GeneratorSpec, hybrid_generate
from generators.character_stats import build_character_stats_spec
from generators.skill_system import build_skill_system_spec
from generators.inventory import build_inventory_spec
from generators.enemy_ai import build_enemy_ai_spec

__all__ = [
    "GeneratorSpec",
    "hybrid_generate",
    "build_character_stats_spec",
    "build_skill_system_spec",
    "build_inventory_spec",
    "build_enemy_ai_spec",
]

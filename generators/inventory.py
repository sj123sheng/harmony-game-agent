"""背包与装备生成器。"""

from generators.framework import FileSpec, GeneratorSpec

_ITEM_TEMPLATE = """// 物品定义 - __ARG:stack_state__
// 数据类 @Observed，描述物品数据结构与稀有度。可被 Inventory 与 Equipment 引用。
export enum Rarity {
  Common, Rare, Epic, Legendary
}

@Observed
export class Item {
  id: number = 0
  name: string = ''
  type: string = ''        // 物品类型（consumable/material/equipment）
  stack: number = 1        // 当前堆叠数
  maxStack: number = 1
  rarity: Rarity = Rarity.Common

  __LLM:item_fields__
}

// 示例物品清单
export const ITEM_DEFS: Item[] = [
  __LLM:item_defs__
]
"""

_INVENTORY_TEMPLATE = """// 背包逻辑 - __ARG:slot_count__ 格，__ARG:stack_state__
// 数据类 @Observed，格子增删/查找/堆叠。可被 InventoryUI 引用。
// 引用 Item 类型（跨文件），需显式 import。
import { Item } from './Item'

@Observed
export class Inventory {
  slots: (Item | null)[] = []

  // 初始化空格子
  init() {
    this.slots = new Array(__ARG:slot_count__).fill(null)
  }

  // 添加物品：可堆叠则合并，否则找空格
  addItem(item: Item): boolean {
    __LLM:add_logic__
  }

  // 移除指定格子的物品
  removeAt(index: number): void {
    __LLM:remove_logic__
  }

  // 查找物品所在格子
  findIndex(itemId: number): number {
    __LLM:find_logic__
  }
}
"""

_EQUIPMENT_TEMPLATE = """// 装备槽穿戴与属性结算
// 数据类 @Observed，装备槽：__ARG:equipment_slots__
// 引用 Item 类型（跨文件），需显式 import。
import { Item } from './Item'

@Observed
export class Equipment {
  slots: Map<string, Item | null> = new Map()

  // 穿戴装备到指定槽位，返回被替换的旧装备
  equip(slot: string, item: Item): Item | null {
    __LLM:equip_logic__
  }

  // 卸下指定槽位装备
  unequip(slot: string): Item | null {
    __LLM:unequip_logic__
  }

  // 汇总装备提供的属性加成
  totalBonus(): { atk: number; def: number; hp: number } {
    __LLM:bonus_logic__
  }
}
"""

_INVENTORY_UI_TEMPLATE = """// 背包 UI - 子组件
// 展示 __ARG:slot_count__ 格背包与装备槽：__ARG:equipment_slots__
// 子组件，由父组件传入 Inventory 实例（@ObjectLink 观察字段变更）。
import { Inventory } from './Inventory'

@Component
export struct InventoryUI {
  @ObjectLink bag: Inventory

  build() {
    Column() {
      Text('背包')
        .fontSize(22)
        .margin(20)
      __LLM:ui_layout__
    }
    .width('100%')
    .height('100%')
  }
}
"""


def build_inventory_spec() -> GeneratorSpec:
    return GeneratorSpec(
        name="generate_inventory",
        description=(
            "生成背包与装备系统：物品定义、背包格子逻辑（增删/查找/堆叠）、"
            "装备槽穿戴与属性加成结算、背包 UI。"
            "输出 Item.ets、Inventory.ets、Equipment.ets、InventoryUI.ets。"
            "参数：slot_count 格子数默认 20；equipment_slots 装备槽列表默认 [头,身,手,脚,武器]；"
            "stackable 是否支持堆叠默认 true。"
        ),
        input_schema={
            "slot_count": int,
            "equipment_slots": list,
            "stackable": bool,
        },
        files=[
            FileSpec(
                path="inventory/Item.ets",
                template=_ITEM_TEMPLATE,
                fill_targets=["item_fields", "item_defs"],
            ),
            FileSpec(
                path="inventory/Inventory.ets",
                template=_INVENTORY_TEMPLATE,
                fill_targets=["add_logic", "remove_logic", "find_logic"],
            ),
            FileSpec(
                path="inventory/Equipment.ets",
                template=_EQUIPMENT_TEMPLATE,
                fill_targets=["equip_logic", "unequip_logic", "bonus_logic"],
            ),
            FileSpec(
                path="inventory/InventoryUI.ets",
                template=_INVENTORY_UI_TEMPLATE,
                fill_targets=["ui_layout"],
            ),
        ],
        fill_instruction=(
            "为鸿蒙 ArkTS 背包与装备系统填充细节。堆叠支持见骨架顶部注释，装备槽列表见骨架。"
            "item_fields 填物品额外字段声明（如描述、图标），不要赋初始数值；"
            "item_defs 填若干示例 Item 对象字面量（含不同稀有度）；"
            "add_logic 填 addItem：可堆叠则合并到同 id 格子，否则找空格放入，满返 false；"
            "remove_logic 填 removeAt 置空逻辑；"
            "find_logic 填 findIndex 遍历返回下标；"
            "equip_logic 填 equip：存入槽位并返回旧装备；"
            "unequip_logic 填 unequip：取出并置空；"
            "bonus_logic 填 totalBonus：遍历已穿戴装备累加 atk/def/hp；"
            "ui_layout 填 InventoryUI build() 内的格子网格与装备槽展示组件，"
            "通过 this.bag.slots / this.bag.findIndex 等 @ObjectLink 字段读数据展示。"
            "只填占位符位置，不要重复 class/struct 声明。"
        ),
        # 中转网关常强制开启 thinking，需给思考+输出留足预算
        max_tokens=4096,
    )

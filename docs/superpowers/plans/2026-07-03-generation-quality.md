# Phase 6 生成代码质量优化 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修 4 个 generator 模板与 DevEco 脚手架的硬伤（跨文件 import / 重复 @Entry / 嵌套状态不响应 / 工程失配），并在 framework.hybrid_generate 内加 LLM 填充后的自动审查闭环，使生成代码能在 DevEco Studio 直接编译运行、属性面板数值能响应刷新。

**Architecture:** A 模板层修复（V1 @Observed/@ObjectLink 范式统一）+ B framework 层审查闭环（填充后调 review_arkts_code 审查、高/中 findings 喂回 LLM 重试 1 次、返回 findings 复用前端卡片）。review system_prompt 提取为 analyzers/review_prompt.py 共享常量。

**Tech Stack:** Python（claude_agent_sdk / anthropic AsyncAnthropic）、ArkTS（HarmonyOS stage 模型 V1 状态管理）、自带 main() 测试非 pytest。

**Spec:** `docs/superpowers/specs/2026-07-03-generation-quality-design.md`

## Global Constraints

- 零回归：不改 analyzers 业务逻辑（review_arkts_code/check_api_usage 的 system_prompt 文本不改，仅提取为常量复用）、不改前端 index.html、不改 main Agent system prompt、不改 server.py 会话核心
- V1 状态范式统一：数据类 `@Observed` + 内部字段去 `@State`；面板子组件 `@ObjectLink` 接收；父组件（Index.ets demo）`@State` 持有实例传给 `@ObjectLink`。不引入 V2
- 跨文件 import 显式：所有 UI 面板引用数据类处模板内写 `import {...} from './X'`（同目录相对，无扩展名）
- `@Entry` 只在 `entry/src/main/ets/pages/Index.ets`；子系统面板组件去 `@Entry` 只留 `@Component`
- 闭环重试只 1 次（成本控制）；审查失败降级不阻断（沿用 framework 风格）
- 测试自带 `main()` 非 pytest，`uv run python <test>.py` 自跑
- 中文注释与文案
- commit msg 末尾含 `提示词：...`（全局 CLAUDE.md 规则）

---

### Task 1: analyzers/review_prompt.py 提取 review system_prompt 共享常量

**Files:**
- Create: `analyzers/review_prompt.py`
- Modify: `tools.py`（review_arkts_code 改用共享常量）
- Test: `tools_review_test.py`（断言不变，仅验证提取后行为一致）

**Interfaces:**
- Produces: `analyzers/review_prompt.REVIEW_SYSTEM_PROMPT: str`（与现有 tools.py 内联文本逐字一致）
- Consumes: 无

- [ ] **Step 1: 写 analyzers/review_prompt.py**

```python
"""review_arkts_code 的 system_prompt 共享常量。

供 tools.review_arkts_code 与 generators.framework 的审查闭环复用，
保证生成期审查与用户主动审查用同一 checklist。
"""

REVIEW_SYSTEM_PROMPT = (
    "你是一名资深 HarmonyOS ArkTS 代码审查专家。对用户给出的 ArkTS 代码进行审查，"
    "从以下维度逐一检查并报告问题：\n"
    "1. 组件结构：@Component/@Entry/build() 是否完整、是否符合 ArkTS 组件规范\n"
    "2. 状态管理：@State/@Prop/@Link 使用是否合理，是否有冗余状态\n"
    "3. 性能：是否有不必要的重渲染、昂贵操作放在 build() 中\n"
    "4. ArkTS 规范：命名约定、类型标注、是否用了 console.log（应用 hilog）等\n"
    "5. 潜在 bug：空指针、资源未释放、事件未解绑等\n"
    "请输出一个 JSON 数组（不要 markdown 代码块标记、不要任何解释文字），"
    "每个元素含字段：severity（高/中/低）、location（文件:行或组件名）、"
    "summary（一句话问题）、fix（改法）、category（审查维度：组件结构/状态管理/性能/ArkTS规范/潜在bug）。"
    "若无任何发现，返回 []。"
)
```

- [ ] **Step 2: tools.py review_arkts_code 改用常量**

把 `tools.py` 内 `review_arkts_code` 函数里内联的 `system_prompt = (...)` 整段替换为：

```python
from analyzers.review_prompt import REVIEW_SYSTEM_PROMPT
# ...（顶部 import 区追加）

async def review_arkts_code(args):
    system_prompt = REVIEW_SYSTEM_PROMPT
    files = [FileRef(path="<贴入代码>", content=args["code"])]
    try:
        text = await analyze_with_context(
            system_prompt, "请审查以下 ArkTS 代码", files, max_tokens=1024
        )
    except Exception as e:
        return {"content": [{"type": "text", "text": f"审查失败：{e}"}]}
    return {"content": [{"type": "text", "text": text or "(审查未返回文本)"}]}
```

- [ ] **Step 3: 跑 tools_review_test.py 确认行为不变**

Run: `uv run python tools_review_test.py`
Expected: 3/3 PASS（`test_review_returns_text_and_uses_framework` 断言 `system.startswith("你是一名资深...")` 仍成立；`"JSON 数组" in system`、`"category" in system` 仍成立）

- [ ] **Step 4: Commit**

```bash
git add analyzers/review_prompt.py tools.py
git commit -m "refactor(analyzers): 提取 review system_prompt 为共享常量

供 tools.review_arkts_code 与 generators.framework 审查闭环复用，
保证生成期与用户主动审查用同一 checklist。文本逐字不变。

提示词：Phase 6 Task 1 提取 review_prompt 常量"
```

---

### Task 2: framework.hybrid_generate 加审查闭环 + findings 返回

**Files:**
- Modify: `generators/framework.py`（hybrid_generate 加审查 + 重试 + findings 字段）
- Test: `generators/framework_test.py`（扩充闭环测试）

**Interfaces:**
- Consumes: `analyzers.framework.analyze_with_context` + `analyzers.framework.FileRef` + `analyzers.review_prompt.REVIEW_SYSTEM_PROMPT`（Task 1 产出）
- Produces: `hybrid_generate` 返回新增 `findings: list[dict]`（每条 `{file,severity,location,summary,fix,category}`）

- [ ] **Step 1: 写失败测试 — 闭环重试**

在 `generators/framework_test.py` 追加（复用现有 `_FakeAnthropic`/`_patch` helper 风格）：

```python
def test_hybrid_generate_review_loop_retries_on_high_severity():
    """LLM 第一次填充被审查出'高' findings → 喂回 LLM 重试 → 用第二次修正版回填。"""
    import generators.framework as fw
    from analyzers.framework import FileRef  # noqa
    # 第一次 LLM 填充返回有问题的代码，第二次返回修正版
    fill_responses = [
        '{"X.ets": {"body": "console.log(\'x\')"}}',  # 第一次：console.log（ArkTS 规范问题）
        '{"X.ets": {"body": "hilog.info(0, \'x\', \'x\')"}}',  # 第二次：修正
    ]
    call_count = {"n": 0}
    class _FillMessages:
        async def create(self, **kwargs):
            i = call_count["n"]
            call_count["n"] += 1
            return SimpleNamespace(content=[_fake_block(fill_responses[i] if i < len(fill_responses) else fill_responses[-1])])
    class _FillAnthropic:
        def __call__(self, *a, **k): return self
        def __init__(self): self.messages = _FillMessages()
    # 审查 LLM：第一次返回高 severity finding，第二次返回空
    review_responses = [
        '[{"severity":"高","location":"X.ets","summary":"用了 console.log","fix":"改 hilog","category":"ArkTS规范"}]',
        '[]',
    ]
    review_count = {"n": 0}
    class _ReviewMessages:
        async def create(self, **kwargs):
            i = review_count["n"]
            review_count["n"] += 1
            return SimpleNamespace(content=[_fake_block(review_responses[i] if i < len(review_responses) else review_responses[-1])])
    class _ReviewAnthropic:
        def __call__(self, *a, **k): return self
        def __init__(self): self.messages = _ReviewMessages()
    fill_orig = fw.AsyncAnthropic
    # 填充走 generators.framework.AsyncAnthropic，审查走 analyzers.framework.AsyncAnthropic
    # ——两个模块各自持有 AsyncAnthropic 引用，可分别 monkeypatch
    import analyzers.framework as afw
    fill_fake = _FillAnthropic()
    review_fake = _ReviewAnthropic()
    fw_orig = fw.AsyncAnthropic
    afw_orig = afw.AsyncAnthropic
    fw.AsyncAnthropic = lambda *a, **k: fill_fake
    afw.AsyncAnthropic = lambda *a, **k: review_fake
    try:
        spec = GeneratorSpec(
            name="t", description="t", input_schema={},
            files=[FileSpec("X.ets", "@Component struct X { __LLM:body__ }", ["body"])],
            fill_instruction="填 body", max_tokens=256,
        )
        result = asyncio.run(fw.hybrid_generate(spec, {}))
    finally:
        fw.AsyncAnthropic = fw_orig
        afw.AsyncAnthropic = afw_orig
    # 第二次修正版回填
    assert "hilog" in result["files"][0]["content"]
    assert "console.log" not in result["files"][0]["content"]
    # findings 字段含第一次的 finding（记录历史）
    assert len(result["findings"]) >= 1
    print("[OK] test_hybrid_generate_review_loop_retries_on_high_severity")
```

注意：填充与审查都走 `AsyncAnthropic`，但填充在 `generators/framework.py`、审查在 `analyzers/framework.py`——两个模块各自有 `AsyncAnthropic` 引用，可分别 monkeypatch。

- [ ] **Step 2: 跑测试确认失败**

Run: `uv run python generators/framework_test.py`
Expected: FAIL（`hybrid_generate` 当前无审查闭环，返回无 `findings` 字段 KeyError / `console.log` 仍在）

- [ ] **Step 3: 实现 framework.py 闭环**

`generators/framework.py` 顶部追加 import：
```python
from analyzers.framework import FileRef, analyze_with_context
from analyzers.review_prompt import REVIEW_SYSTEM_PROMPT
```

`hybrid_generate` 改为（在现有回填后加审查 + 重试）：

```python
async def hybrid_generate(spec: GeneratorSpec, args: dict) -> dict:
    """渲染骨架 → LLM 填充 → 回填 → 审查 → 高/中 findings 喂回 LLM 重试 1 次 → 返回。

    任何 LLM/JSON/审查失败均降级，不抛异常。
    """
    # 1-2. 渲染骨架 + 收集 LLM 占位符（现有逻辑不变）
    skeletons: dict[str, str] = {}
    for f in spec.files:
        skeletons[f.path] = _render_args(f.template, args)
    slots = _collect_llm_slots(skeletons)

    # 3. LLM 填充（现有逻辑，抽取为内部函数以便重试复用）
    async def _fill(skeletons: dict[str, str], extra_hint: str = "") -> dict[str, dict[str, str]]:
        if not slots:
            return {}
        slot_list = "\n".join(f"- 文件 {s['file']} 占位符 `{s['slot']}`" for s in slots)
        skeleton_block = "\n\n".join(f"=== 文件 {p} ===\n{s}" for p, s in skeletons.items())
        user_prompt = (
            f"{spec.fill_instruction}\n\n"
            f"{extra_hint}\n\n" if extra_hint else f"{spec.fill_instruction}\n\n"
        ) + (
            f"以下是需要填充的骨架（__LLM:名字__ 为待填占位符）：\n\n"
            f"{skeleton_block}\n\n需要填充的占位符清单：\n{slot_list}\n\n"
            f"请输出 JSON：键为文件路径，值为 {{占位符名: 填充代码}}。"
        )
        for attempt in range(2):
            try:
                client = AsyncAnthropic()
                resp = await client.messages.create(
                    model=_resolve_model(), max_tokens=spec.max_tokens,
                    system=_FILLER_SYSTEM, messages=[{"role": "user", "content": user_prompt}],
                )
                text = "".join(getattr(b, "text", "") for b in resp.content)
                return json.loads(_strip_code_fences(text))
            except Exception:
                if attempt == 0:
                    continue
        return {}

    fills = await _fill(skeletons)
    all_findings: list[dict] = []

    # 4. 回填（现有逻辑）
    def _backfill(skeletons, fills):
        out = []
        for f in spec.files:
            content = skeletons[f.path]
            file_fills = fills.get(f.path, {}) if isinstance(fills, dict) else {}
            def replace_slot(m, _ff=file_fills):
                name = m.group(1)
                if name in _ff and _ff[name]:
                    return _ff[name]
                return f"// TODO: 待填充 {name}"
            content = _LLM_SLOT.sub(replace_slot, content)
            out.append({"path": f.path, "content": content})
        return out

    files_out = _backfill(skeletons, fills)

    # 5. 审查（只对有 LLM 填充的 spec 做；纯确定性 spec 跳过）
    if slots:
        try:
            review_findings = await _review_files(files_out)
            high_mid = [f for f in review_findings if f.get("severity") in ("高", "中")]
            all_findings.extend(review_findings)
            # 6. 有高/中 findings → 喂回 LLM 重试 1 次
            if high_mid:
                hint = "上一版审查发现以下问题，请修正后重新填充：\n" + \
                       "\n".join(f"- {f.get('location')}: {f.get('summary')}（改法：{f.get('fix')}）" for f in high_mid)
                fills2 = await _fill(skeletons, hint)
                if fills2:
                    files_out = _backfill(skeletons, fills2)
                    # 二次审查（只收集，不再重试）
                    try:
                        second = await _review_files(files_out)
                        all_findings = second  # 用最新审查结果覆盖
                    except Exception:
                        pass
        except Exception as e:
            error_note = f"审查失败：{e}；未阻断生成。"
        # error_note 需在返回里——见下方 return

    error_note = ""  # 填充失败时由 _fill 内部循环决定；此处简化
    return {"files": files_out, "error": error_note, "findings": all_findings}


async def _review_files(files: list[dict]) -> list[dict]:
    """对每个生成文件调 review 审查，返回 findings 列表（已解析 JSON 数组）。"""
    import json as _json
    all_findings = []
    for f in files:
        file_refs = [FileRef(path=f["path"], content=f["content"])]
        text = await analyze_with_context(
            REVIEW_SYSTEM_PROMPT, "请审查以下 ArkTS 代码", file_refs, max_tokens=1024
        )
        try:
            parsed = _json.loads(_strip_code_fences(text or "[]"))
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        item["file"] = f["path"]
                        all_findings.append(item)
        except Exception:
            pass  # 审查解析失败跳过，不阻断
    return all_findings
```

注意 error_note 处理：填充失败时 `_fill` 返回 {} 且无 error 标记——需在 `_fill` 内捕获并返回 error 字符串，或在 hybrid_generate 主体记录。实现时确保填充失败仍 error_note 非空（与现有行为一致）。

- [ ] **Step 4: 跑测试确认通过**

Run: `uv run python generators/framework_test.py`
Expected: 全 PASS（含新 `test_hybrid_generate_review_loop_retries_on_high_severity` + 原有冒烟）

- [ ] **Step 5: 补降级测试 — 审查失败不阻断**

```python
def test_hybrid_generate_review_failure_does_not_block():
    """审查 LLM raise → 不阻断，files 正常返回，findings 为空。"""
    import generators.framework as fw
    import analyzers.framework as afw
    # 填充成功
    class _FillOk:
        def __call__(self, *a, **k): return self
        def __init__(self):
            self.messages = SimpleNamespace(create=_async_return(
                SimpleNamespace(content=[_fake_block('{"X.ets":{"body":"let x=1"}}')])
            ))
    # 审查 raise
    class _ReviewRaise:
        def __call__(self, *a, **k): return self
        def __init__(self):
            self.messages = SimpleNamespace(create=_async_raise(RuntimeError("审查余额不足")))
    fw_orig = fw.AsyncAnthropic
    afw_orig = afw.AsyncAnthropic
    fw.AsyncAnthropic = lambda *a, **k: _FillOk()
    afw.AsyncAnthropic = lambda *a, **k: _ReviewRaise()
    try:
        spec = GeneratorSpec("t","t",{},
            [FileSpec("X.ets","@Component struct X { __LLM:body__ }",["body"])],
            "填 body", 256)
        result = asyncio.run(fw.hybrid_generate(spec, {}))
    finally:
        fw.AsyncAnthropic = fw_orig
        afw.AsyncAnthropic = afw_orig
    assert "let x=1" in result["files"][0]["content"]
    assert result["findings"] == []
    print("[OK] test_hybrid_generate_review_failure_does_not_block")
```

`_async_return`/`_async_raise` 为 test 内 helper（async lambda 替代）。复用现有 test 文件的 helper 风格。

- [ ] **Step 6: Commit**

```bash
git add generators/framework.py generators/framework_test.py
git commit -m "feat(framework): hybrid_generate 加审查闭环与 findings 返回

LLM 填充后调 review_arkts_code 审查，高/中 severity findings 喂回
LLM 重试 1 次。审查失败降级不阻断。返回 findings 字段供前端卡片复用。

提示词：Phase 6 Task 2 framework 审查闭环"
```

---

### Task 3: character_stats.py 模板修复（V1 范式样板）

**Files:**
- Modify: `generators/character_stats.py`
- Test: `generators/framework_test.py` 或新增 `generators/character_stats_test.py`

**Interfaces:**
- Consumes: Task 2 hybrid_generate（闭环会审查此模板产出）
- Produces: `build_character_stats_spec()` 模板含显式 import + 数据类 @Observed + 面板 @ObjectLink + 无重复 @Entry

- [ ] **Step 1: 写失败测试 — 模板结构断言**

新增 `generators/character_stats_test.py`：

```python
"""character_stats 模板结构断言：import / @Observed / @ObjectLink / 无重复 @Entry。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
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
```

- [ ] **Step 2: 跑确认失败**

Run: `uv run python generators/character_stats_test.py`
Expected: FAIL（当前模板无 @Observed/@ObjectLink/import，面板有 @Entry）

- [ ] **Step 3: 改模板**

`generators/character_stats.py` 模板改为：

```python
_CHARACTER_STATS_TEMPLATE = """// 角色属性系统 - __ARG:character_name__（__ARG:archetype__ 型），等级上限 __ARG:level_cap__
// 数据类 @Observed，字段变更可被 @ObjectLink 子组件响应。
@Observed
export class CharacterStats {
  // ===== 等级与经验 =====
  level: number = 1
  exp: number = 0

  // ===== 基础属性 =====
  maxHp: number = 0
  currentHp: number = 0
  atk: number = 0
  def: number = 0
  critRate: number = 0      // 暴击率 [0,1]
  speed: number = 0         // 行动速度

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
// 子组件，由父组件传入 CharacterStats 实例（@ObjectLink 观察字段变更）。
import { CharacterStats } from './CharacterStats'

@Component
struct StatsPanel {
  @ObjectLink stats: CharacterStats

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
```

注意：`@ObjectLink` 字段不能有初始值（`@ObjectLink stats: CharacterStats`，无 `= new ...`）。`fill_instruction` 的 `panel_layout` 描述需更新："展示 `this.stats.xxx` 各属性"。

`fill_instruction` 改为提到 `this.stats.maxHp` 等字段引用（让 LLM 知道从 stats 读）。

- [ ] **Step 4: 跑确认通过**

Run: `uv run python generators/character_stats_test.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add generators/character_stats.py generators/character_stats_test.py
git commit -m "fix(generator): character_stats 模板 V1 范式修复

数据类 @Observed 去内部 @State；面板 @ObjectLink 接收去掉 @Entry；
显式 import CharacterStats。修属性面板不刷新与跨文件引用编译失败。

提示词：Phase 6 Task 3 character_stats V1 范式样板"
```

---

### Task 4: skill_system/inventory/enemy_ai 模板同范式修复

**Files:**
- Modify: `generators/skill_system.py` / `generators/inventory.py` / `generators/enemy_ai.py`
- Test: 新增 `generators/subsystems_template_test.py`（三个模板结构断言）

**Interfaces:**
- Consumes: Task 3 范式样板
- Produces: 三个子系统模板均含 import + @Observed（数据类）+ @ObjectLink（面板）+ 无重复 @Entry

- [ ] **Step 1: 写失败测试**

```python
"""skill_system/inventory/enemy_ai 模板结构断言（同 character_stats 范式）。"""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from generators.skill_system import build_skill_system_spec
from generators.inventory import build_inventory_spec
from generators.enemy_ai import build_enemy_ai_spec

def _assert_template(spec, data_class_idx, panel_idx, data_class_name, import_path):
    data_tmpl = spec.files[data_class_idx].template
    panel_tmpl = spec.files[panel_idx].template
    assert "@Observed" in data_tmpl, f"{data_class_name} 数据类须 @Observed"
    assert "@Entry" not in panel_tmpl, f"{data_class_name} 面板须去 @Entry"
    assert "@ObjectLink" in panel_tmpl, f"{data_class_name} 面板须 @ObjectLink"
    assert import_path in panel_tmpl, f"{data_class_name} 面板须显式 import"

def test_skill_system_template():
    spec = build_skill_system_spec()
    _assert_template(spec, 0, 1, "Skill", "import { SkillManager } from './SkillManager'")
    print("[OK] test_skill_system_template")

def test_inventory_template():
    spec = build_inventory_spec()
    _assert_template(spec, 0, 1, "Inventory", "import { Inventory } from './Inventory'")
    print("[OK] test_inventory_template")

def test_enemy_ai_template():
    spec = build_enemy_ai_spec()
    _assert_template(spec, 0, 1, "Enemy", "import { Enemy } from './Enemy'")
    print("[OK] test_enemy_ai_template")

def main():
    test_skill_system_template()
    test_inventory_template()
    test_enemy_ai_template()
    print("\n全部通过。")

if __name__ == "__main__":
    main()
```

注意：`_assert_template` 的 import_path 与 file index 须按实际模板调整——implementer 读三个模板源码确认数据类文件 index、面板文件 index、export 符号名（如 SkillManager/Inventory/Enemy/AIController/CombatUI）。测试断言以实际为准。

- [ ] **Step 2: 跑确认失败**

Run: `uv run python generators/subsystems_template_test.py`
Expected: FAIL

- [ ] **Step 3: 改三个模板**

对 `skill_system.py`/`inventory.py`/`enemy_ai.py` 每个应用 Task 3 范式：
- 数据类（Skill/Buff/SkillManager、Item/Inventory/EquipmentSlots、Enemy/AIController/CombatUI 的数据部分）：`@Observed` class 头 + 内部字段去 `@State`
- 面板/管理器组件持有跨文件数据类处：`@ObjectLink`（去 `@State`、去 `= new ...`）+ 顶部 `import {...} from './X'`
- 面板组件去 `@Entry`

注意：
- `SkillManager`/`Inventory`/`EquipmentSlots`/`AIController` 既是数据类又是 @Component 的混合——若 class 既有状态字段又有 build()，按 `@Observed @Component` 双装饰器（V1 允许）。implementer 按实际结构判断，优先数据逻辑与 UI 分离（数据类 @Observed，UI @Component @ObjectLink）。
- `CombatUI`/`InventoryUI`/`SkillPanel` 等 UI 面板去 @Entry，@ObjectLink 接收数据类实例。
- import 路径同目录相对 `./X`。
- `fill_instruction` 更新提示 LLM 用 `this.xxx` 从 @ObjectLink 字段读。

- [ ] **Step 4: 跑确认通过**

Run: `uv run python generators/subsystems_template_test.py`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add generators/skill_system.py generators/inventory.py generators/enemy_ai.py generators/subsystems_template_test.py
git commit -m "fix(generators): skill/inventory/enemy 模板 V1 范式修复

数据类 @Observed 去内部 @State；UI 面板 @ObjectLink 接收去 @Entry；
显式跨文件 import。与 character_stats 范式一致。

提示词：Phase 6 Task 4 子系统模板同范式修复"
```

---

### Task 5: deveco_project.py 补失配文件与 signingConfig 一致性

**Files:**
- Modify: `generators/deveco_project.py`
- Test: `generators/deveco_project_test.py`（扩充断言）

**Interfaces:**
- Consumes: 无
- Produces: DevEco 工程模板含 entry/oh-package.json5、AppScope resources、signingConfig 一致、minAPIVersion

- [ ] **Step 1: 写失败测试**

在 `generators/deveco_project_test.py` 追加：

```python
def test_scaffold_includes_missing_deveco_files():
    """脚手架须含 entry/oh-package.json5、AppScope resources、minAPIVersion。"""
    spec = build_deveco_project_spec([])  # 无子系统
    paths = [f.path for f in spec.files]
    assert "entry/oh-package.json5" in paths, "缺模块级 oh-package.json5"
    assert "AppScope/resources/base/element/string.json" in paths
    assert "AppScope/resources/base/element/color.json" in paths
    # signingConfig 不引用空 signingConfigs
    root_build = [f for f in spec.files if f.path == "build-profile.json5"][0].template
    assert '"signingConfig"' not in root_build or '"signingConfigs": [{}]' in root_build, \
        "signingConfig 须与 signingConfigs 一致"
    # app.json5 含 minAPIVersion
    app = [f for f in spec.files if f.path == "AppScope/app.json5"][0].template
    assert "minAPIVersion" in app
    print("[OK] test_scaffold_includes_missing_deveco_files")
```

- [ ] **Step 2: 跑确认失败**

Run: `uv run python generators/deveco_project_test.py`
Expected: FAIL

- [ ] **Step 3: 改 deveco_project.py**

补三处：
1. 新增 `_ENTRY_OH_PACKAGE_JSON5` 模板 + 加入 spec files：
```python
_ENTRY_OH_PACKAGE_JSON5 = """{
  "name": "entry",
  "version": "1.0.0",
  "description": "entry module",
  "main": "",
  "license": "MIT",
  "dependencies": {}
}
"""
# spec files 追加：
FileSpec("entry/oh-package.json5", _ENTRY_OH_PACKAGE_JSON5),
```

2. 新增 AppScope resources + 加入 spec：
```python
_APP_STRING_JSON = """{
  "string": [
    { "name": "app_name", "value": "__ARG:label__" }
  ]
}
"""
_APP_COLOR_JSON = """{
  "color": [
    { "name": "start_window_background", "value": "#FFFFFF" }
  ]
}
"""
# spec files 追加：
FileSpec("AppScope/resources/base/element/string.json", _APP_STRING_JSON),
FileSpec("AppScope/resources/base/element/color.json", _APP_COLOR_JSON),
```

3. `_ROOT_BUILD_PROFILE` 去掉 `signingConfig` 引用（未签名占位）：
```python
_ROOT_BUILD_PROFILE = """{
  "app": { "signingConfigs": [], "products": [{ "name": "default" }] },
  "modules": [{ "name": "entry", "srcPath": "./entry", "targets": [{ "name": "default", "applyToProducts": ["default"] }] }]
}
"""
```

4. `_APP_JSON5` 补 `minAPIVersion`：
```python
_APP_JSON5 = """{
  "app": {
    "bundleName": "__ARG:bundle__",
    "vendor": "harmony-game-agent",
    "versionCode": 1,
    "versionName": "1.0.0",
    "minAPIVersion": 12,
    "icon": "$media:app_icon",
    "label": "__ARG:label__"
  }
}
"""
```

注意：`$media:app_icon` 仍需图标资源——DevEco 缺图标报错。implementer 评估是否生成占位 media 文件（1x1 PNG base64 难在 JSON 模板表达）。spec 标记为已知边界（用户自行替换图标），或在 CHANGELOG 注明。本 Task 不生成 PNG（超 JSON 模板范围），只补 json5/string/color。

- [ ] **Step 4: 跑确认通过**

Run: `uv run python generators/deveco_project_test.py`
Expected: 全 PASS

- [ ] **Step 5: Commit**

```bash
git add generators/deveco_project.py generators/deveco_project_test.py
git commit -m "fix(deveco): 补 entry/oh-package 与 AppScope resources 及配置一致性

补 entry/oh-package.json5、AppScope resources string/color、app.json5
minAPIVersion；去 build-profile 对空 signingConfigs 的引用。修 DevEco
缺模块级配置与资源致构建失败。

提示词：Phase 6 Task 5 deveco 补失配文件"
```

---

### Task 6: tools._format_files 追加 findings + CHANGELOG + 全量回归

**Files:**
- Modify: `tools.py`（`_format_files` 追加 findings 文本）
- Modify: `CHANGELOG.md`（Phase 6 段）
- Modify: `.gitignore`（无需，sessions/ 已在）
- 无测试（回归跑全部）

**Interfaces:**
- Consumes: Task 2 hybrid_generate 返回的 `findings` 字段
- Produces: `generate_*` 工具返回文本含审查 findings 摘要

- [ ] **Step 1: tools._format_files 追加 findings**

`tools.py` `_format_files` 改为接收 result 含 findings：

```python
def _format_files(result: dict) -> str:
    parts = []
    if result.get("error"):
        parts.append(f"[注意] {result['error']}")
    files = result.get("files", [])
    parts.append(f"已生成 {len(files)} 个文件（请用 Write 写入 ./generated/ 下对应路径）：")
    for f in files:
        parts.append(f"\n=== {f['path']} ===\n{f['content']}")
    # 审查闭环产出的 findings（供主 Agent 决策与前端 findings 卡片渲染）
    findings = result.get("findings") or []
    if findings:
        parts.append(f"\n[审查发现 {len(findings)} 项]")
        for f in findings:
            parts.append(f"- [{f.get('severity','?')}] {f.get('file','')}: {f.get('summary','')}（改法：{f.get('fix','')}）")
    return "\n".join(parts)
```

注意：5 个生成工具的 `except` 分支 `result` 不含 findings 字段——`_format_files` 用 `.get("findings") or []` 兜底，不影响异常路径。

- [ ] **Step 2: CHANGELOG.md 加 Phase 6 段**

文件末尾追加：

```markdown

## [v1.2.0] - 2026-07-03

### Phase 6：生成代码质量优化

- 模板硬伤修复：4 个 generator 跨文件 import 显式化、去重复 @Entry、嵌套状态改 V1 @Observed/@ObjectLink（修属性面板不刷新）
- DevEco 脚手架补 entry/oh-package.json5、AppScope resources、minAPIVersion、signingConfig 一致性
- framework.hybrid_generate 加自动审查闭环：LLM 填充后调 review_arkts_code 审查，高/中 severity findings 喂回 LLM 重试 1 次
- 生成返回 findings 字段，复用前端既有 findings 卡片渲染
- review_arkts_code system_prompt 提取为 analyzers/review_prompt.py 共享常量（生成期与用户审查同一 checklist）
```

已知边界追加（若需）："DevEco 图标占位需用户自行替换真实 app_icon"。

- [ ] **Step 3: 全量回归**

Run:
```bash
uv run python generators/framework_test.py
uv run python generators/character_stats_test.py
uv run python generators/subsystems_template_test.py
uv run python generators/deveco_project_test.py
uv run python tools_review_test.py
uv run python analyzers/findings_test.py
uv run python main_test.py
uv run python analyzers/performance_test.py
uv run python analyzers/bug_location_test.py
uv run python analyzers/api_usage_test.py
uv run python analyzers/runtime_logs_test.py
uv run python sessions_store_test.py
uv run python server_test.py
```
Expected: 全部 `全部通过。`（server_test 21/21）

- [ ] **Step 4: Commit**

```bash
git add tools.py CHANGELOG.md
git commit -m "chore: Phase 6 收尾 _format_files findings + CHANGELOG + 回归

tools._format_files 追加审查 findings 文本供主 Agent 决策；
CHANGELOG v1.2.0 记 Phase 6 优化。全量 13 文件回归 PASS。

提示词：Phase 6 Task 6 收尾"
```

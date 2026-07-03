"""framework 混合生成框架测试 + 4 个生成器冒烟测试。

不依赖 pytest，用 if __name__ == "__main__" 直接跑。
用桩 LLM（monkeypatch AsyncAnthropic.messages.create）验证渲染/回填/降级/多文件。
真实 LLM 内容不可复现，冒烟测试只断言路径与文件名。
"""

import asyncio
import os
import sys
from types import SimpleNamespace

# 让 generators 包可被导入
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)) + "/..")

from generators.framework import FileSpec, GeneratorSpec, hybrid_generate
from generators import (
    build_character_stats_spec,
    build_enemy_ai_spec,
    build_inventory_spec,
    build_skill_system_spec,
)


def _fake_block(text: str):
    return SimpleNamespace(text=text)


class _FakeMessages:
    """桩：messages.create 返回固定 JSON。"""

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


def _make_fake_client(return_text: str):
    """返回一个替换 AsyncAnthropic 构造的可调用对象。"""
    fake = _FakeAnthropic(return_text)

    def factory(*args, **kwargs):
        return fake

    return factory, fake


# ---------- framework 单测 ----------

def test_render_args_replaces():
    """确定性 __ARG__ 占位符被正确替换。"""
    spec = GeneratorSpec(
        name="t",
        description="d",
        input_schema={"name": str},
        files=[FileSpec(path="a/A.ets", template="struct __ARG:name__ {}")],
        fill_instruction="",
    )
    # 无 LLM 占位符，不会调用 LLM
    result = asyncio.run(hybrid_generate(spec, {"name": "Player"}))
    assert result["files"][0]["content"] == "struct Player {}", result
    assert result["error"] == ""
    print("[OK] test_render_args_replaces")


def test_llm_fill_backfilled(monkeypatch_via_globals):
    """LLM 返回的 JSON 被正确回填到占位符。"""
    import generators.framework as fw
    import analyzers.framework as afw

    json_text = '{"a/A.ets": {"body": "return 42"}}'
    factory, fake = _make_fake_client(json_text)
    orig = fw.AsyncAnthropic
    afw_orig = afw.AsyncAnthropic
    fw.AsyncAnthropic = factory
    # 审查打桩返回空 findings，避免真实调用触发重试
    afw.AsyncAnthropic = lambda *a, **k: _FakeAnthropic("[]")
    try:
        spec = GeneratorSpec(
            name="t",
            description="d",
            input_schema={},
            files=[FileSpec(path="a/A.ets", template="fn() { __LLM:body__ }")],
            fill_instruction="填 body",
        )
        result = asyncio.run(hybrid_generate(spec, {}))
    finally:
        fw.AsyncAnthropic = orig
        afw.AsyncAnthropic = afw_orig
    assert result["files"][0]["content"] == "fn() { return 42 }", result
    assert len(fake.messages.calls) == 1
    print("[OK] test_llm_fill_backfilled")


def test_degradation_on_bad_json():
    """LLM 返回非法 JSON 时降级为 // TODO，不抛异常。"""
    import generators.framework as fw
    import analyzers.framework as afw

    factory, fake = _make_fake_client("这不是 JSON")
    orig = fw.AsyncAnthropic
    afw_orig = afw.AsyncAnthropic
    fw.AsyncAnthropic = factory
    afw.AsyncAnthropic = lambda *a, **k: _FakeAnthropic("[]")
    try:
        spec = GeneratorSpec(
            name="t",
            description="d",
            input_schema={},
            files=[FileSpec(path="a/A.ets", template="fn() { __LLM:body__ }")],
            fill_instruction="填 body",
        )
        result = asyncio.run(hybrid_generate(spec, {}))
    finally:
        fw.AsyncAnthropic = orig
        afw.AsyncAnthropic = afw_orig
    assert "// TODO: 待填充 body" in result["files"][0]["content"]
    assert result["error"] != ""
    print("[OK] test_degradation_on_bad_json")


def test_degradation_on_llm_exception():
    """LLM 调用抛异常时降级，不抛。"""
    import generators.framework as fw
    import analyzers.framework as afw

    class _RaisingMessages:
        async def create(self, **kwargs):
            raise RuntimeError("余额不足")

    class _Raising:
        def __call__(self, *a, **k):
            return self

        messages = _RaisingMessages()

    raising = _Raising()

    def factory(*a, **k):
        return raising

    orig = fw.AsyncAnthropic
    afw_orig = afw.AsyncAnthropic
    fw.AsyncAnthropic = factory
    afw.AsyncAnthropic = lambda *a, **k: _FakeAnthropic("[]")
    try:
        spec = GeneratorSpec(
            name="t",
            description="d",
            input_schema={},
            files=[FileSpec(path="a/A.ets", template="fn() { __LLM:body__ }")],
            fill_instruction="填 body",
        )
        result = asyncio.run(hybrid_generate(spec, {}))
    finally:
        fw.AsyncAnthropic = orig
        afw.AsyncAnthropic = afw_orig
    assert "// TODO: 待填充 body" in result["files"][0]["content"]
    assert "余额不足" in result["error"]
    print("[OK] test_degradation_on_llm_exception")


def test_multi_file_order():
    """多文件保持声明顺序。"""
    spec = GeneratorSpec(
        name="t",
        description="d",
        input_schema={},
        files=[
            FileSpec(path="x/One.ets", template="// one"),
            FileSpec(path="x/Two.ets", template="// two"),
            FileSpec(path="x/Three.ets", template="// three"),
        ],
        fill_instruction="",
    )
    result = asyncio.run(hybrid_generate(spec, {}))
    paths = [f["path"] for f in result["files"]]
    assert paths == ["x/One.ets", "x/Two.ets", "x/Three.ets"], paths
    print("[OK] test_multi_file_order")


def test_missing_arg_raises():
    """__ARG__ 缺参数应抛 ValueError（编程错误，由 @tool 层兜住）。"""
    spec = GeneratorSpec(
        name="t",
        description="d",
        input_schema={"name": str},
        files=[FileSpec(path="a/A.ets", template="struct __ARG:name__ {}")],
        fill_instruction="",
    )
    try:
        asyncio.run(hybrid_generate(spec, {}))
    except ValueError:
        print("[OK] test_missing_arg_raises")
        return
    raise AssertionError("应抛 ValueError")


# ---------- 冒烟测试：4 个生成器 ----------

def _smoke(spec_builder, args, expected_paths, label):
    """用桩 LLM 跑一遍，断言文件路径清单一致。"""
    import generators.framework as fw
    import analyzers.framework as afw

    # 桩 LLM：对每个文件返回一个占位填充
    json_text = "{"
    for p in expected_paths:
        json_text += f'"{p}": {{}}, '
    json_text = json_text.rstrip(", ") + "}"
    factory, _ = _make_fake_client(json_text)
    orig = fw.AsyncAnthropic
    afw_orig = afw.AsyncAnthropic
    fw.AsyncAnthropic = factory
    # 审查也打桩：返回空 findings，避免真实 LLM 调用
    afw.AsyncAnthropic = lambda *a, **k: _FakeAnthropic("[]")
    try:
        spec = spec_builder()
        result = asyncio.run(hybrid_generate(spec, args))
    finally:
        fw.AsyncAnthropic = orig
        afw.AsyncAnthropic = afw_orig
    paths = [f["path"] for f in result["files"]]
    assert paths == expected_paths, f"{label}: {paths} != {expected_paths}"
    # 每个 __ARG__ 都应被替换（不应残留 __ARG:）
    for f in result["files"]:
        assert "__ARG:" not in f["content"], f"{label}: {f['path']} 残留 __ARG__"
    print(f"[OK] smoke_{label}: {paths}")


def test_smoke_character_stats():
    _smoke(
        build_character_stats_spec,
        {"character_name": "Hero", "archetype": "战士", "level_cap": 60},
        ["character/CharacterStats.ets", "character/StatsPanel.ets"],
        "character_stats",
    )


def test_smoke_skill_system():
    _smoke(
        build_skill_system_spec,
        {"skill_count": 4, "include_buffs": True, "combat_style": "即时"},
        ["skill/Skill.ets", "skill/Buff.ets", "skill/SkillManager.ets"],
        "skill_system",
    )


def test_smoke_inventory():
    # inventory 需要派生 stack_state
    args = {
        "slot_count": 20,
        "equipment_slots": ["头", "身", "手", "脚", "武器"],
        "stackable": True,
        "stack_state": "支持堆叠",
    }
    _smoke(
        build_inventory_spec,
        args,
        [
            "inventory/Item.ets",
            "inventory/Inventory.ets",
            "inventory/Equipment.ets",
            "inventory/InventoryUI.ets",
        ],
        "inventory",
    )


def test_smoke_enemy_ai():
    _smoke(
        build_enemy_ai_spec,
        {"enemy_name": "Goblin", "ai_pattern": "巡逻", "difficulty": "普通"},
        ["enemy/Enemy.ets", "enemy/EnemyAI.ets", "enemy/CombatResolver.ets"],
        "enemy_ai",
    )


def _async_return(value):
    """返回一个 async 函数，await 后得到 value。"""
    async def _fn(*a, **k):
        return value
    return _fn


def _async_raise(exc):
    """返回一个 async 函数，await 时抛 exc。"""
    async def _fn(*a, **k):
        raise exc
    return _fn


# ---------- 审查闭环测试 ----------

def test_hybrid_generate_review_loop_retries_on_high_severity():
    """LLM 第一次填充被审查出'高' findings → 喂回 LLM 重试 → 用第二次修正版回填。"""
    import generators.framework as fw
    import analyzers.framework as afw
    from analyzers.framework import FileRef  # noqa: F401
    # 第一次 LLM 填充返回有问题的代码，第二次返回修正版
    fill_responses = [
        '{"X.ets": {"body": "console.log(\'x\')"}}',   # 第一次：console.log（ArkTS 规范问题）
        '{"X.ets": {"body": "hilog.info(0, \'x\', \'x\')"}}',  # 第二次：修正
    ]
    call_count = {"n": 0}
    class _FillMessages:
        async def create(self, **kwargs):
            i = call_count["n"]
            call_count["n"] += 1
            return SimpleNamespace(content=[_fake_block(
                fill_responses[i] if i < len(fill_responses) else fill_responses[-1]
            )])
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
            return SimpleNamespace(content=[_fake_block(
                review_responses[i] if i < len(review_responses) else review_responses[-1]
            )])
    class _ReviewAnthropic:
        def __call__(self, *a, **k): return self
        def __init__(self): self.messages = _ReviewMessages()
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
    assert "hilog" in result["files"][0]["content"], result["files"][0]["content"]
    assert "console.log" not in result["files"][0]["content"], result["files"][0]["content"]
    # findings 字段含第一次的 finding（记录历史）
    assert len(result["findings"]) >= 1, result["findings"]
    print("[OK] test_hybrid_generate_review_loop_retries_on_high_severity")


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
        spec = GeneratorSpec(
            "t", "t", {},
            [FileSpec("X.ets", "@Component struct X { __LLM:body__ }", ["body"])],
            "填 body", 256,
        )
        result = asyncio.run(fw.hybrid_generate(spec, {}))
    finally:
        fw.AsyncAnthropic = fw_orig
        afw.AsyncAnthropic = afw_orig
    assert "let x=1" in result["files"][0]["content"], result["files"][0]["content"]
    assert result["findings"] == [], result["findings"]
    print("[OK] test_hybrid_generate_review_failure_does_not_block")


def main():
    # monkeypatch 占位参数（test_llm_fill_backfilled 的形参仅为命名提示，实际通过模块级替换）
    test_render_args_replaces()
    test_llm_fill_backfilled(None)
    test_degradation_on_bad_json()
    test_degradation_on_llm_exception()
    test_multi_file_order()
    test_missing_arg_raises()
    test_hybrid_generate_review_loop_retries_on_high_severity()
    test_hybrid_generate_review_failure_does_not_block()
    test_smoke_character_stats()
    test_smoke_skill_system()
    test_smoke_inventory()
    test_smoke_enemy_ai()
    print("\n全部通过。")


if __name__ == "__main__":
    main()

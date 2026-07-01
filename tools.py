"""harmony-game-agent 自定义工具集。

4 个 RPG 子系统生成工具（混合：确定性模板骨架 + LLM 填充）+ 1 个 DevEco 脚手架工具 + 1 个 ArkTS 代码审查工具。
生成工具通过共享 framework.hybrid_generate 统一渲染/填充/组装多文件，返回 {files} 给主 Agent 写盘。
"""

import os

from anthropic import AsyncAnthropic

from claude_agent_sdk import create_sdk_mcp_server, tool

from generators import (
    build_character_stats_spec,
    build_deveco_project_spec,
    build_enemy_ai_spec,
    build_inventory_spec,
    build_skill_system_spec,
    hybrid_generate,
    run_scaffold,
)


def _format_files(result: dict) -> str:
    """把 hybrid_generate 的 {files,error} 格式化成可读文本，供主 Agent 据此 Write。"""
    parts = []
    if result.get("error"):
        parts.append(f"[注意] {result['error']}")
    files = result.get("files", [])
    parts.append(f"已生成 {len(files)} 个文件（请用 Write 写入 ./generated/ 下对应路径）：")
    for f in files:
        parts.append(f"\n=== {f['path']} ===\n{f['content']}")
    return "\n".join(parts)


@tool(
    "generate_character_stats",
    build_character_stats_spec().description,
    build_character_stats_spec().input_schema,
)
async def generate_character_stats(args):
    spec = build_character_stats_spec()
    args = {
        "character_name": args["character_name"],
        "archetype": args["archetype"],
        "level_cap": args.get("level_cap", 99),
    }
    try:
        result = await hybrid_generate(spec, args)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"生成失败：{e}"}]}
    return {"content": [{"type": "text", "text": _format_files(result)}]}


@tool(
    "generate_skill_system",
    build_skill_system_spec().description,
    build_skill_system_spec().input_schema,
)
async def generate_skill_system(args):
    spec = build_skill_system_spec()
    args = {
        "skill_count": args.get("skill_count", 4),
        "include_buffs": args.get("include_buffs", True),
        "combat_style": args["combat_style"],
    }
    try:
        result = await hybrid_generate(spec, args)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"生成失败：{e}"}]}
    return {"content": [{"type": "text", "text": _format_files(result)}]}


@tool(
    "generate_inventory",
    build_inventory_spec().description,
    build_inventory_spec().input_schema,
)
async def generate_inventory(args):
    spec = build_inventory_spec()
    stackable = args.get("stackable", True)
    args = {
        "slot_count": args.get("slot_count", 20),
        "equipment_slots": args.get("equipment_slots", ["头", "身", "手", "脚", "武器"]),
        "stackable": stackable,
        "stack_state": "支持堆叠" if stackable else "不可堆叠",
    }
    try:
        result = await hybrid_generate(spec, args)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"生成失败：{e}"}]}
    return {"content": [{"type": "text", "text": _format_files(result)}]}


@tool(
    "generate_enemy_ai",
    build_enemy_ai_spec().description,
    build_enemy_ai_spec().input_schema,
)
async def generate_enemy_ai(args):
    spec = build_enemy_ai_spec()
    args = {
        "enemy_name": args["enemy_name"],
        "ai_pattern": args["ai_pattern"],
        "difficulty": args["difficulty"],
    }
    try:
        result = await hybrid_generate(spec, args)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"生成失败：{e}"}]}
    return {"content": [{"type": "text", "text": _format_files(result)}]}


@tool(
    "scaffold_deveco_project",
    build_deveco_project_spec([]).description,
    build_deveco_project_spec([]).input_schema,
)
async def scaffold_deveco_project(args):
    args = {
        "project_name": args["project_name"],
        "bundle_prefix": args.get("bundle_prefix", "com.harmonygame"),
        "scan_dir": args.get("scan_dir", "./generated"),
    }
    try:
        result = await run_scaffold(args)
    except Exception as e:
        return {"content": [{"type": "text", "text": f"脚手架失败：{e}"}]}
    return {"content": [{"type": "text", "text": _format_files(result)}]}


@tool(
    "review_arkts_code",
    "用 LLM 对传入的 ArkTS 代码做智能审查，返回结构化的问题清单与改进建议。",
    {"code": str},
)
async def review_arkts_code(args):
    # AsyncAnthropic 自动读取环境变量 ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL，
    # 与主 Agent 共用同一套中转配置
    client = AsyncAnthropic()
    model = os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-5"

    # 固定的审查者 system prompt + checklist，保证每次审查流程一致、可复现
    system_prompt = (
        "你是一名资深 HarmonyOS ArkTS 代码审查专家。对用户给出的 ArkTS 代码进行审查，"
        "从以下维度逐一检查并报告问题：\n"
        "1. 组件结构：@Component/@Entry/build() 是否完整、是否符合 ArkTS 组件规范\n"
        "2. 状态管理：@State/@Prop/@Link 使用是否合理，是否有冗余状态\n"
        "3. 性能：是否有不必要的重渲染、昂贵操作放在 build() 中\n"
        "4. ArkTS 规范：命名约定、类型标注、是否用了 console.log（应用 hilog）等\n"
        "5. 潜在 bug：空指针、资源未释放、事件未解绑等\n"
        "请按『等级（高/中/低）| 位置 | 描述 | 建议』格式输出清单，最后给一句总体评价。"
        "若代码无问题，直接说明。"
    )

    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=1024,
            system=system_prompt,
            messages=[{"role": "user", "content": f"请审查以下 ArkTS 代码：\n\n{args['code']}"}],
        )
    except Exception as e:
        # 审查失败（如中转余额不足、鉴权失败）时返回可读错误，不抛出以免 REPL 崩栈
        return {"content": [{"type": "text", "text": f"审查失败：{e}"}]}

    # 提取文本回复
    text = "".join(getattr(block, "text", "") for block in resp.content)
    return {"content": [{"type": "text", "text": text or "(审查未返回文本)"}]}


def build_server():
    """创建装载了自定义工具的 in-process MCP 服务器。"""
    return create_sdk_mcp_server(
        name="harmony_tools",
        version="1.0.0",
        tools=[
            generate_character_stats,
            generate_skill_system,
            generate_inventory,
            generate_enemy_ai,
            scaffold_deveco_project,
            review_arkts_code,
        ],
    )

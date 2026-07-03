"""harmony-game-agent 自定义工具集。

4 个 RPG 子系统生成工具（混合：确定性模板骨架 + LLM 填充）+ 1 个 DevEco 脚手架工具 + 1 个 ArkTS 代码审查工具 + 4 个 ArkTS 分析工具。
生成工具通过共享 framework.hybrid_generate 统一渲染/填充/组装多文件，返回 {files} 给主 Agent 写盘。
审查/分析工具通过共享 analyzers.framework.analyze_with_context 统一走 LLM 调用，@tool 包装层兜底异常转 MCP 友好文本。
"""

from claude_agent_sdk import create_sdk_mcp_server, tool

from analyzers import (
    analyze_runtime_logs,
    check_api_usage,
    locate_bug,
    suggest_performance_fixes,
)
from analyzers.framework import FileRef, analyze_with_context
from analyzers.review_prompt import REVIEW_SYSTEM_PROMPT
from generators import (
    build_character_stats_spec,
    build_deveco_project_spec,
    build_enemy_ai_spec,
    build_inventory_spec,
    build_skill_system_spec,
    hybrid_generate,
    run_scaffold,
)

# M1: 模块级缓存，避免装饰器重复构造 spec
_DEVECO_PROJECT_SPEC = build_deveco_project_spec([])


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
    _DEVECO_PROJECT_SPEC.description,
    _DEVECO_PROJECT_SPEC.input_schema,
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
    # 固定的审查者 system prompt + checklist，保证每次审查流程一致、可复现
    # 提取为 analyzers.review_prompt.REVIEW_SYSTEM_PROMPT 共享常量，
    # 供 generators.framework 审查闭环复用同一 checklist。
    system_prompt = REVIEW_SYSTEM_PROMPT
    # A1 路径：把贴入代码包成 FileRef，走共享 analyze_with_context，
    # 与其余 4 个分析工具共用 LLM 调用/截断/兜底逻辑。
    files = [FileRef(path="<贴入代码>", content=args["code"])]
    try:
        text = await analyze_with_context(
            system_prompt, "请审查以下 ArkTS 代码", files, max_tokens=1024
        )
    except Exception as e:
        # 审查失败（如中转余额不足、鉴权失败）时返回可读错误，不抛出以免 REPL 崩栈
        return {"content": [{"type": "text", "text": f"审查失败：{e}"}]}
    return {"content": [{"type": "text", "text": text or "(审查未返回文本)"}]}


@tool(
    "analyze_runtime_logs",
    "分析鸿蒙运行日志（含 ArkTS 堆栈），把报错路径映射到源码并给根因假设与修复方向。"
    "参数：logs 日志全文；scope 可选（文件路径/子系统名/'all'，默认 'all'）。",
    {
        "type": "object",
        "properties": {
            "logs": {"type": "string"},
            "scope": {"type": "string", "default": "all"},
        },
        "required": ["logs"],
    },
)
async def analyze_runtime_logs_tool(args):
    try:
        text = await analyze_runtime_logs({
            "logs": args["logs"],
            "scope": args.get("scope") or "all",
        })
    except Exception as e:
        return {"content": [{"type": "text", "text": f"日志分析失败：{e}"}]}
    return {"content": [{"type": "text", "text": text or "(日志分析未返回文本)"}]}


@tool(
    "suggest_performance_fixes",
    "对已有 ArkTS 代码做性能审查，给出瓶颈清单与改法。"
    "参数：scope（文件路径/子系统名/'all'）；symptom 可选（如'列表卡顿'）。",
    {
        "type": "object",
        "properties": {
            "scope": {"type": "string"},
            "symptom": {"type": "string"},
        },
        "required": ["scope"],
    },
)
async def suggest_performance_fixes_tool(args):
    try:
        text = await suggest_performance_fixes({
            "scope": args["scope"],
            "symptom": args.get("symptom") or "",
        })
    except Exception as e:
        return {"content": [{"type": "text", "text": f"性能分析失败：{e}"}]}
    return {"content": [{"type": "text", "text": text or "(性能分析未返回文本)"}]}


@tool(
    "locate_bug",
    "根据症状在已有 ArkTS 代码中跨文件推理定位可疑位置，给复现步骤与修复方向。"
    "参数：scope（文件路径/子系统名/'all'）；symptom 必填（症状/报错描述）。",
    {
        "type": "object",
        "properties": {
            "scope": {"type": "string"},
            "symptom": {"type": "string"},
        },
        "required": ["scope", "symptom"],
    },
)
async def locate_bug_tool(args):
    try:
        text = await locate_bug({
            "scope": args["scope"],
            "symptom": args["symptom"],
        })
    except Exception as e:
        return {"content": [{"type": "text", "text": f"Bug 定位失败：{e}"}]}
    return {"content": [{"type": "text", "text": text or "(Bug 定位未返回文本)"}]}


@tool(
    "check_api_usage",
    "检查 ArkTS/ArkUI API 用法（误用/废弃/V1-V2 混用/权限缺失）。"
    "参数：scope（文件路径/子系统名/'all'）；focus_apis 可选（如'@State Navigation'）。",
    {
        "type": "object",
        "properties": {
            "scope": {"type": "string"},
            "focus_apis": {"type": "string"},
        },
        "required": ["scope"],
    },
)
async def check_api_usage_tool(args):
    try:
        text = await check_api_usage({
            "scope": args["scope"],
            "focus_apis": args.get("focus_apis") or "",
        })
    except Exception as e:
        return {"content": [{"type": "text", "text": f"API 审查失败：{e}"}]}
    return {"content": [{"type": "text", "text": text or "(API 审查未返回文本)"}]}


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
            analyze_runtime_logs_tool,
            suggest_performance_fixes_tool,
            locate_bug_tool,
            check_api_usage_tool,
        ],
    )

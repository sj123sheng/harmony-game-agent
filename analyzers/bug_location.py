"""Bug 定位工具。"""

from analyzers.framework import analyze_with_context, resolve_scope

_BUG_SYSTEM_PROMPT = (
    "你是一名资深 HarmonyOS ArkTS 调试专家。用户给出症状与若干源码文件，"
    "请在源码中跨文件推理定位可疑位置。\n"
    "1. 根据症状推断可能的触发链路（哪条调用路径、哪个状态变迁）\n"
    "2. 给出最小复现步骤\n"
    "3. 给出验证手段（断点位置、日志埋点、单元测试要点）\n"
    "4. 给出建议修复方向\n"
    "无法定位时说明还缺什么信息。\n"
    "请输出一个 JSON 数组（不要 markdown 代码块标记、不要任何解释文字），"
    "每个元素含字段：severity（高/中/低，表示置信度）、location（可疑位置）、"
    "summary（一句话结论）、fix（建议修复）、repro（复现步骤）、reasoning（推理依据）。"
    "多候选按 severity 从高到低。若无任何发现，返回 []。"
)


async def locate_bug(args: dict) -> str:
    scope = args["scope"]
    scan_dir = args.get("scan_dir") or "./generated"
    symptom = args["symptom"]
    files = resolve_scope(scope, scan_dir)
    user_input = f"症状/报错描述：{symptom}\n请定位 bug。"
    return await analyze_with_context(
        _BUG_SYSTEM_PROMPT, user_input, files, max_tokens=4096
    )

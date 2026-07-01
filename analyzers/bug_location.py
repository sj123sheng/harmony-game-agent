"""Bug 定位工具。"""

from analyzers.framework import analyze_with_context, resolve_scope

_BUG_SYSTEM_PROMPT = (
    "你是一名资深 HarmonyOS ArkTS 调试专家。用户给出症状与若干源码文件，"
    "请在源码中跨文件推理定位可疑位置。\n"
    "1. 根据症状推断可能的触发链路（哪条调用路径、哪个状态变迁）\n"
    "2. 给出最小复现步骤\n"
    "3. 给出验证手段（断点位置、日志埋点、单元测试要点）\n"
    "4. 给出建议修复方向\n"
    "请按『可疑位置 | 推理依据 | 复现步骤 | 建议修复 | 置信度（高/中/低）』格式输出。"
    "多候选时按置信度从高到低排序。无法定位时说明还缺什么信息。"
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

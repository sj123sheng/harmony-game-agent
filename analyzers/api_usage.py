"""API 用法纠错工具。"""

from analyzers.framework import analyze_with_context, resolve_scope

_API_SYSTEM_PROMPT = (
    "你是一名 HarmonyOS ArkTS/ArkUI API 用法审查专家。检查以下问题：\n"
    "1. API 误用：参数类型/数量错、调用时机错、返回值未处理\n"
    "2. 已废弃接口：是否用了标记 deprecated 的旧 API，应换什么\n"
    "3. V1/V2 状态管理混用：V1（@State/@Prop/@Link/@Observed/@ObjectLink）与 "
    "V2（@ComponentV2/@LocalV2/@Param/@Once/@ObservedV2/@Trace）不应在同一组件树混用\n"
    "4. 权限/能力缺失：调用需 ohos 权限的 API 是否在 module.json5 声明\n"
    "5. 平台差异：phone/tablet 不支持的 API\n"
    "请按『API | 误用位置 | 正确用法 | 依据』格式输出。若用户给了 focus_apis，优先查这些。"
)


async def check_api_usage(args: dict) -> str:
    scope = args["scope"]
    scan_dir = args.get("scan_dir") or "./generated"
    focus = args.get("focus_apis") or ""
    files = resolve_scope(scope, scan_dir)
    user_input = (
        f"重点关注的 API：{focus}\n" if focus else "请做整体 API 用法审查\n"
    )
    return await analyze_with_context(
        _API_SYSTEM_PROMPT, user_input, files, max_tokens=2048
    )

"""性能瓶颈建议工具。"""

from analyzers.framework import FileRef, analyze_with_context, resolve_scope

_PERF_SYSTEM_PROMPT = (
    "你是一名资深 HarmonyOS ArkUI 性能专家。对用户给出的 ArkTS 代码进行性能审查，"
    "从以下维度逐一检查并报告问题：\n"
    "1. build() 内昂贵操作：是否有重计算、对象分配、同步 IO 放在 build() 中\n"
    "2. 状态粒度：@State/@Prop/@Link 范围是否过大导致不必要重渲染\n"
    "3. 列表渲染：长列表是否用 LazyForEach 而非 forEach/直接展开\n"
    "4. 图片资源：是否未用 PixelMap 解码缓存、是否在 build() 内重复解码\n"
    "5. 生命周期：事件监听/定时器/动画是否在 aboutToDispose 或 aboutToDisappear 释放\n"
    "6. 并发：是否有主线程阻塞的同步调用\n"
    "若用户给了 symptom，优先围绕它分析。\n"
    "请输出一个 JSON 数组（不要 markdown 代码块标记、不要任何解释文字），"
    "每个元素含字段：severity（高/中/低）、location（文件:行）、summary（一句话问题）、"
    "fix（改法）。若无任何发现，返回 []。"
)


async def suggest_performance_fixes(args: dict) -> str:
    scope = args["scope"]
    scan_dir = args.get("scan_dir") or "./generated"
    symptom = args.get("symptom") or ""
    files = resolve_scope(scope, scan_dir)
    user_input = (
        f"性能症状：{symptom}\n" if symptom else "请做整体性能审查\n"
    )
    return await analyze_with_context(
        _PERF_SYSTEM_PROMPT, user_input, files, max_tokens=4096
    )

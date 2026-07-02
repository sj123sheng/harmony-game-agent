"""运行日志分析工具。路径映射逻辑归此文件，不进 framework。"""

import re

from analyzers.framework import FileRef, analyze_with_context, resolve_scope

_LOGS_SYSTEM_PROMPT = (
    "你是一名 HarmonyOS 运行日志分析师。用户给出一运行日志（可能含 ArkTS 堆栈）"
    "与若干源码文件上下文。请：\n"
    "1. 把堆栈帧 / 报错路径映射到源码位置（若上下文已给出对应文件）\n"
    "2. 区分错误类型：JS 异常、native crash、资源错误、权限错误\n"
    "3. 给根因假设与修复方向\n"
    "无法定位时说明还缺什么信息（如更多日志、对应源码）。\n"
    "请输出一个 JSON 数组（不要 markdown 代码块标记、不要任何解释文字），"
    "每个元素含字段：severity（高/中/低）、location（源码位置）、"
    "summary（一句话根因）、fix（修复建议）、root_cause（根因假设详述）、"
    "confidence（0.0-1.0 置信度）。若无任何发现，返回 []。"
)

# 提取日志里的 .ets 路径（如 character/CharacterStats.ets 或 entry/.../Index.ets）
_ETS_PATH_RE = re.compile(r"([\w./-]+\.ets)(?::\d+)?")
_LOGS_LIMIT = 30 * 1024


def _extract_ets_paths(logs: str) -> list[str]:
    """best-effort 从日志提取 .ets 路径，按出现顺序去重。"""
    seen = set()
    out = []
    for m in _ETS_PATH_RE.finditer(logs):
        p = m.group(1)
        if p not in seen:
            seen.add(p)
            out.append(p)
    return out


def _truncate_logs(logs: str, limit_bytes: int = _LOGS_LIMIT) -> str:
    """保留末尾 limit_bytes 字节（堆栈通常在日志末尾）。
    按 UTF-8 字节计账，切片时 errors="ignore" 避免切坏多字节字符。
    """
    encoded = logs.encode("utf-8")
    if len(encoded) <= limit_bytes:
        return logs
    return encoded[-limit_bytes:].decode("utf-8", errors="ignore")


async def analyze_runtime_logs(args: dict) -> str:
    logs = args["logs"]
    scope = args.get("scope") or "all"
    scan_dir = args.get("scan_dir") or "./generated"

    logs = _truncate_logs(logs)
    paths = _extract_ets_paths(logs)

    files: list[FileRef] = []
    for p in paths:
        # 命中的路径按文件路径分支拉真实文件
        files.extend(resolve_scope(p, scan_dir))
    if not files:
        # 未命中任何路径 → fallback 到 scope
        files = resolve_scope(scope, scan_dir)

    return await analyze_with_context(
        _LOGS_SYSTEM_PROMPT, user_input=logs, files=files, max_tokens=2048
    )

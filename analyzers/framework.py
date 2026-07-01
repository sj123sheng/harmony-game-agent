"""共享分析框架：文件上下文解析 + LLM 分析调用。

与 generators/ 并列——生成用 hybrid_generate，分析用 analyze_with_context。
framework 只提供通用原语，不掺领域逻辑（日志路径映射归 runtime_logs.py）。
"""

import os
from dataclasses import dataclass

from anthropic import AsyncAnthropic

# 与 generators/deveco_project._KNOWN_SUBSYSTEMS 一致
_KNOWN_SUBSYSTEMS = ("character", "skill", "inventory", "enemy")

# files 总字节软上限，超出按文件顺序截断
_FILES_BYTES_LIMIT = 80 * 1024


@dataclass
class FileRef:
    path: str        # 相对 scan_dir 的正斜杠路径，如 "character/CharacterStats.ets"
    content: str


def _read_text(path: str) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def _scan_subsystem_dir(sub_dir: str, scan_dir: str) -> list[FileRef]:
    """扫描子系统目录下所有 .ets，path 相对 scan_dir 用正斜杠。"""
    if not os.path.isdir(sub_dir):
        return []
    refs: list[FileRef] = []
    for fname in sorted(os.listdir(sub_dir)):
        if not fname.endswith(".ets"):
            continue
        full = os.path.join(sub_dir, fname)
        rel = os.path.relpath(full, scan_dir).replace(os.sep, "/")
        refs.append(FileRef(path=rel, content=_read_text(full)))
    return refs


def resolve_scope(scope: str, scan_dir: str = "./generated") -> list[FileRef]:
    """scope 三形态（判定顺序写死，消歧）：
       1. 'all' → 全部已知子系统
       2. 在 _KNOWN_SUBSYSTEMS → 该子系统
       3. 否则当文件路径（相对 scan_dir，含工程内深层路径）
       找不到/越界返回空清单，不抛。
    """
    if scope == "all":
        refs: list[FileRef] = []
        for name in _KNOWN_SUBSYSTEMS:
            refs.extend(_scan_subsystem_dir(os.path.join(scan_dir, name), scan_dir))
        return refs
    if scope in _KNOWN_SUBSYSTEMS:
        return _scan_subsystem_dir(os.path.join(scan_dir, scope), scan_dir)
    # 文件路径分支：路径穿越防护
    real_scan = os.path.realpath(scan_dir)
    full = os.path.join(scan_dir, scope)
    real = os.path.realpath(full)
    try:
        common = os.path.commonpath([real, real_scan])
    except ValueError:
        # 跨盘符等异常
        return []
    if common != real_scan:
        return []  # 越界
    if not os.path.isfile(real):
        return []
    rel = os.path.relpath(real, real_scan).replace(os.sep, "/")
    return [FileRef(path=rel, content=_read_text(real))]


def _build_user_message(user_input: str, files: list[FileRef]) -> str:
    """组装纯 XML 文件上下文 + 用户输入。files 为空时省略 <files> 段。"""
    if not files:
        return user_input
    parts = ["<files>"]
    total = 0
    for f in files:
        chunk = f.content
        chunk_bytes = chunk.encode("utf-8")
        if total + len(chunk_bytes) > _FILES_BYTES_LIMIT:
            remaining = _FILES_BYTES_LIMIT - total
            if remaining > 0:
                # 按字节切片再还原，errors="ignore" 避免切坏多字节字符尾部
                truncated = chunk_bytes[:remaining].decode("utf-8", errors="ignore")
                parts.append(f'<file path="{f.path}">{truncated}\n[已截断]</file>')
            total = _FILES_BYTES_LIMIT
            break
        parts.append(f'<file path="{f.path}">{chunk}</file>')
        total += len(chunk_bytes)
    parts.append("</files>")
    parts.append("")
    parts.append(user_input)
    return "\n".join(parts)


async def analyze_with_context(
    system_prompt: str,
    user_input: str,
    files: list[FileRef],
    max_tokens: int = 2048,
) -> str:
    """组装文件上下文 + 用户输入喂给 AsyncAnthropic，返回分析文本。
    LLM 调用/网络失败时 raise（不兜底），由 @tool 包装层转友好文本。
    """
    client = AsyncAnthropic()
    model = os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-5"
    message = _build_user_message(user_input, files)
    resp = await client.messages.create(
        model=model,
        max_tokens=max_tokens,
        system=system_prompt,
        messages=[{"role": "user", "content": message}],
    )
    return "".join(getattr(b, "text", "") for b in resp.content)

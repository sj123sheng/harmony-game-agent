"""共享混合生成框架。

确定性模板骨架 + LLM 填充定制细节。framework 本身不写文件，
只返回 {files:[{path,content}]}，由主 Agent 用 Write 写入 ./generated/。

占位符语法（避开 ArkTS 大括号，不用 str.format/string.Template）：
  __ARG:<name>__   确定性占位符，用 args[name] 替换
  __LLM:<name>__   LLM 填充占位符，由一次 LLM 调用回填；失败降级为 // TODO
"""

import json
import re

from anthropic import AsyncAnthropic

_ARG_SLOT = re.compile(r"__ARG:(\w+)__")
_LLM_SLOT = re.compile(r"__LLM:(\w+)__")

_FILLER_SYSTEM = (
    "你是鸿蒙 ArkTS 代码填充器。只输出一个 JSON 对象，"
"格式为 {\"<文件路径>\": {\"<占位符名>\": \"<填充代码>\"}}，"
"不要输出任何额外解释、不要 markdown 代码块标记。"
"填充代码须是合法 ArkTS 片段，符合鸿蒙 ArkTS/ArkUI 规范。"
)


class FileSpec:
    """一个待生成文件的规格。"""

    def __init__(self, path: str, template: str, fill_targets: list[str] | None = None):
        self.path = path
        self.template = template
        self.fill_targets = fill_targets or []


class GeneratorSpec:
    """一个生成工具的规格声明。"""

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict,
        files: list[FileSpec],
        fill_instruction: str,
        max_tokens: int = 2048,
    ):
        self.name = name
        self.description = description
        self.input_schema = input_schema
        self.files = files
        self.fill_instruction = fill_instruction
        self.max_tokens = max_tokens


def _render_args(template: str, args: dict) -> str:
    """用 args 替换 __ARG:name__ 占位符。缺失的抛 ValueError（编程错误）。"""
    missing = []

    def sub(m):
        key = m.group(1)
        if key not in args:
            missing.append(key)
            return m.group(0)
        return str(args[key])

    rendered = _ARG_SLOT.sub(sub, template)
    if missing:
        raise ValueError(f"模板缺少参数: {missing}")
    return rendered


def _collect_llm_slots(skeletons: dict[str, str]) -> list[dict]:
    """收集所有文件里的 __LLM:name__ 占位符，返回 [{file, slot}]。"""
    slots = []
    for path, skeleton in skeletons.items():
        seen = set()
        for m in _LLM_SLOT.finditer(skeleton):
            name = m.group(1)
            if name not in seen:
                seen.add(name)
                slots.append({"file": path, "slot": name})
    return slots


def _strip_code_fences(text: str) -> str:
    """LLM 偶尔会把 JSON 包在 ```json ... ``` 里，去掉。"""
    text = text.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text


async def hybrid_generate(spec: GeneratorSpec, args: dict) -> dict:
    """渲染骨架 → 一次 LLM 调用填占位符 → 回填 → 返回 {files:[{path,content}]}。

    任何 LLM/JSON 失败均降级为 // TODO，不抛异常。
    """
    # 1. 渲染确定性占位符
    skeletons: dict[str, str] = {}
    for f in spec.files:
        skeletons[f.path] = _render_args(f.template, args)

    # 2. 收集 LLM 占位符
    slots = _collect_llm_slots(skeletons)

    fills: dict[str, dict[str, str]] = {}
    error_note = ""

    if slots:
        # 构造 prompt
        slot_list = "\n".join(f"- 文件 {s['file']} 占位符 `{s['slot']}`" for s in slots)
        skeleton_block = "\n\n".join(
            f"=== 文件 {path} ===\n{skel}" for path, skel in skeletons.items()
        )
        user_prompt = (
            f"{spec.fill_instruction}\n\n"
            f"以下是需要填充的骨架（__LLM:名字__ 为待填占位符）：\n\n"
            f"{skeleton_block}\n\n"
            f"需要填充的占位符清单：\n{slot_list}\n\n"
            f"请输出 JSON：键为文件路径，值为 {{占位符名: 填充代码}}。"
        )

        # 中转网关偶发空响应，最多重试 1 次
        for attempt in range(2):
            try:
                client = AsyncAnthropic()
                model = _resolve_model()
                resp = await client.messages.create(
                    model=model,
                    max_tokens=spec.max_tokens,
                    system=_FILLER_SYSTEM,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                text = "".join(getattr(b, "text", "") for b in resp.content)
                fills = json.loads(_strip_code_fences(text))
                error_note = ""
                break
            except Exception as e:
                error_note = f"LLM 填充失败：{e}；未填部分以 // TODO 标注。"
                if attempt == 0:
                    continue
        else:
            # 两次都失败，error_note 已记录，fills 保持空
            pass

    # 3. 回填
    files_out = []
    for f in spec.files:
        content = skeletons[f.path]
        file_fills = fills.get(f.path, {}) if isinstance(fills, dict) else {}

        def replace_slot(m):
            name = m.group(1)
            if name in file_fills and file_fills[name]:
                return file_fills[name]
            return f"// TODO: 待填充 {name}"

        content = _LLM_SLOT.sub(replace_slot, content)
        files_out.append({"path": f.path, "content": content})

    return {"files": files_out, "error": error_note}


def _resolve_model() -> str:
    import os

    return os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-5"

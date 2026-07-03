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

from analyzers.framework import FileRef, analyze_with_context
from analyzers.review_prompt import REVIEW_SYSTEM_PROMPT

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
    """渲染骨架 → LLM 填充 → 回填 → 审查 → 高/中 findings 喂回 LLM 重试 1 次 → 返回。

    任何 LLM/JSON/审查失败均降级，不抛异常。
    返回 {files, error, findings}；findings 每条带 file 字段。
    """
    # 1. 渲染确定性占位符骨架
    skeletons: dict[str, str] = {}
    for f in spec.files:
        skeletons[f.path] = _render_args(f.template, args)

    # 2. 收集 LLM 占位符
    slots = _collect_llm_slots(skeletons)
    error_note = ""

    # 3. LLM 填充（抽为内部函数，支持 extra_hint 与重试）
    async def _fill(skeletons: dict[str, str], extra_hint: str = "") -> tuple[dict, str]:
        """返回 (fills, error_note)。失败时 fills={} error_note 非空。"""
        if not slots:
            return {}, ""
        slot_list = "\n".join(f"- 文件 {s['file']} 占位符 `{s['slot']}`" for s in slots)
        skeleton_block = "\n\n".join(
            f"=== 文件 {path} ===\n{skel}" for path, skel in skeletons.items()
        )
        # fill_instruction 只拼接一次；extra_hint 为空时省略，避免重复
        hint_part = f"{extra_hint}\n\n" if extra_hint else ""
        user_prompt = (
            f"{spec.fill_instruction}\n\n"
            f"{hint_part}"
            f"以下是需要填充的骨架（__LLM:名字__ 为待填占位符）：\n\n"
            f"{skeleton_block}\n\n需要填充的占位符清单：\n{slot_list}\n\n"
            f"请输出 JSON：键为文件路径，值为 {{占位符名: 填充代码}}。"
        )
        # 中转网关偶发空响应，最多重试 1 次
        for attempt in range(2):
            try:
                client = AsyncAnthropic()
                resp = await client.messages.create(
                    model=_resolve_model(),
                    max_tokens=spec.max_tokens,
                    system=_FILLER_SYSTEM,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                text = "".join(getattr(b, "text", "") for b in resp.content)
                return json.loads(_strip_code_fences(text)), ""
            except Exception as e:
                err = f"LLM 填充失败：{e}；未填部分以 // TODO 标注。"
                if attempt == 0:
                    continue
                return {}, err
        return {}, "LLM 填充失败：两次均未成功；未填部分以 // TODO 标注。"

    fills, fill_err = await _fill(skeletons)
    if fill_err:
        error_note = fill_err
    all_findings: list[dict] = []

    # 4. 回填（抽为内部函数，便于重试时复用）
    def _backfill(skeletons, fills):
        out = []
        for f in spec.files:
            content = skeletons[f.path]
            file_fills = fills.get(f.path, {}) if isinstance(fills, dict) else {}

            def replace_slot(m, _ff=file_fills):
                name = m.group(1)
                if name in _ff and _ff[name]:
                    return _ff[name]
                return f"// TODO: 待填充 {name}"

            content = _LLM_SLOT.sub(replace_slot, content)
            out.append({"path": f.path, "content": content})
        return out

    files_out = _backfill(skeletons, fills)

    # 5. 审查（只对有 LLM 填充的 spec 做；纯确定性 spec 跳过）
    if slots:
        try:
            review_findings = await _review_files(files_out)
            all_findings.extend(review_findings)
            high_mid = [f for f in review_findings if f.get("severity") in ("高", "中")]
            # 6. 有高/中 findings → 喂回 LLM 重试 1 次
            if high_mid:
                hint = "上一版审查发现以下问题，请修正后重新填充：\n" + \
                       "\n".join(
                           f"- {f.get('location')}: {f.get('summary')}"
                           f"（改法：{f.get('fix')}）" for f in high_mid
                       )
                fills2, _ = await _fill(skeletons, hint)
                if fills2:
                    files_out = _backfill(skeletons, fills2)
                    # 二次审查（只收集，不再重试）；覆盖第一次的 findings——
                    # 修正后的 files 不应再带已修正的旧 findings（覆盖语义）。
                    # 二次审查失败（raise）时不覆盖，all_findings 保持第一次的。
                    try:
                        second = await _review_files(files_out)
                        all_findings = list(second)
                    except Exception:
                        pass  # 二次审查失败不阻断，保留第一次 findings
        except Exception as e:
            # 审查失败降级：不阻断，findings 保持空
            if not error_note:
                error_note = f"审查失败：{e}；未阻断生成。"

    return {"files": files_out, "error": error_note, "findings": all_findings}


async def _review_files(files: list[dict]) -> list[dict]:
    """对每个生成文件调 review 审查，返回 findings 列表（已解析 JSON 数组）。

    每条 finding 追加 file 字段标记来源文件。
    """
    all_findings: list[dict] = []
    for f in files:
        file_refs = [FileRef(path=f["path"], content=f["content"])]
        text = await analyze_with_context(
            REVIEW_SYSTEM_PROMPT, "请审查以下 ArkTS 代码", file_refs, max_tokens=1024,
        )
        try:
            parsed = json.loads(_strip_code_fences(text or "[]"))
            if isinstance(parsed, list):
                for item in parsed:
                    if isinstance(item, dict):
                        item["file"] = f["path"]
                        all_findings.append(item)
        except Exception:
            pass  # 审查解析失败跳过，不阻断
    return all_findings


def _resolve_model() -> str:
    import os

    return os.environ.get("ANTHROPIC_MODEL") or "claude-sonnet-4-5"

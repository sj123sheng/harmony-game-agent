"""harmony-game-agent 自定义工具集。

两个 in-process MCP 工具：
- generate_arkts_component: 生成 ArkTS 组件代码骨架（确定性模板）
- review_arkts_code: 用 LLM 智能审查 ArkTS 代码（anthropic SDK 直接调用）
"""

import os

from anthropic import AsyncAnthropic

from claude_agent_sdk import create_sdk_mcp_server, tool


@tool(
    "generate_arkts_component",
    "生成一个 ArkTS 组件代码骨架。需提供组件名、功能描述，以及是否为入口组件（@Entry）。",
    {"component_name": str, "description": str, "is_entry": bool},
)
async def generate_arkts_component(args):
    name = args["component_name"]
    description = args["description"]
    is_entry = args.get("is_entry", False)

    # 入口组件加 @Entry 装饰器
    entry_decorator = "@Entry\n" if is_entry else ""
    code = f"""// {description}
@Component
{entry_decorator}struct {name} {{
  @State count: number = 0

  build() {{
    Column() {{
      Text(this.count.toString())
        .fontSize(24)
        .margin(20)
      Button('点击 +1')
        .onClick(() => {{
          this.count++
        }})
    }}
    .width('100%')
    .height('100%')
    .justifyContent(FlexAlign.Center)
  }}
}}"""
    return {"content": [{"type": "text", "text": code}]}


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
        tools=[generate_arkts_component, review_arkts_code],
    )

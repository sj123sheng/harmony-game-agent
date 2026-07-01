"""harmony-game-agent 交互式 REPL。

基于 ClaudeSDKClient 维持多轮会话，挂载两个自定义工具：
生成 ArkTS 组件骨架、审查 ArkTS 代码。
"""

import os
import sys

import anyio
from dotenv import load_dotenv

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
    ToolResultBlock,
    ToolUseBlock,
    UserMessage,
)

from tools import build_server

# 从 .env 加载环境变量（ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL / ANTHROPIC_MODEL）
load_dotenv()


def build_options() -> ClaudeAgentOptions:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("错误：未设置 ANTHROPIC_API_KEY，请复制 .env.example 为 .env 并填入你的 Key。")

    # 中转模型配置（均可选，未设置则用 SDK 默认）
    model = os.environ.get("ANTHROPIC_MODEL") or None
    env_overrides: dict[str, str] = {}
    if base_url := os.environ.get("ANTHROPIC_BASE_URL"):
        env_overrides["ANTHROPIC_BASE_URL"] = base_url

    server = build_server()
    project_root = os.path.dirname(os.path.abspath(__file__))
    return ClaudeAgentOptions(
        system_prompt=(
            "你是一名鸿蒙（HarmonyOS）原生游戏开发辅助助手，"
            "擅长 ArkTS/ArkUI、DevEco Studio、Cocos 等鸿蒙游戏开发技术栈，专注 RPG/战斗类游戏。\n"
            "你可以调用以下工具：\n"
            "- generate_character_stats：生成角色属性系统（属性/经验/升级/属性面板）\n"
            "- generate_skill_system：生成技能与 Buff 系统（技能/Buff/技能管理器）\n"
            "- generate_inventory：生成背包与装备系统（物品/背包/装备/背包 UI）\n"
            "- generate_enemy_ai：生成敌人与战斗 AI（敌人/状态机/战斗结算）\n"
            "- scaffold_deveco_project：扫描已生成的子系统文件，组装成完整 DevEco 工程，并生成战斗循环 demo 入口页\n"
            "- review_arkts_code：审查 ArkTS 代码并给出问题清单\n"
            "前四个工具会返回 {files: [{path, content}]}，每个文件含相对路径（如 character/CharacterStats.ets）"
            "与完整内容。当工具返回后，用 Write 工具把每个文件写入项目的 ./generated/ 目录，"
            "路径保持工具给出的相对路径（写入 ./generated/<子系统>/<文件>.ets），"
            "然后向用户说明生成了哪些文件、各自用途。\n"
            "当用户要求生成工程/脚手架/可运行 demo 时，调用 scaffold_deveco_project；"
            "它返回的文件路径带 <工程名>/ 前缀，用 Write 写入 ./generated/<工程名>/ 下对应路径。\n"
            "当用户要求审查代码时，调用 review_arkts_code。\n"
            "主动根据用户需求选择合适的工具，并结合工具返回结果给出说明。"
        ),
        mcp_servers={"harmony_tools": server},
        allowed_tools=[
            "mcp__harmony_tools__generate_character_stats",
            "mcp__harmony_tools__generate_skill_system",
            "mcp__harmony_tools__generate_inventory",
            "mcp__harmony_tools__generate_enemy_ai",
            "mcp__harmony_tools__scaffold_deveco_project",
            "mcp__harmony_tools__review_arkts_code",
            "Write",
        ],
        permission_mode="acceptEdits",
        cwd=project_root,
        model=model,
        env=env_overrides or None,
    )


def _extract_tool_result_text(block: ToolResultBlock) -> str:
    """从 ToolResultBlock.content 中提取可读文本。"""
    content = block.content
    if isinstance(content, list):
        parts = []
        for c in content:
            if isinstance(c, TextBlock):
                parts.append(c.text)
            elif isinstance(c, dict):
                parts.append(c.get("text", str(c)))
            else:
                parts.append(str(c))
        return "\n".join(parts)
    return str(content)


def print_message(msg) -> None:
    """按消息类型打印 REPL 输出。"""
    if isinstance(msg, AssistantMessage):
        for block in msg.content:
            if isinstance(block, TextBlock):
                print(f"\nClaude: {block.text}")
            elif isinstance(block, ToolUseBlock):
                print(f"\n[调用工具] {block.name}({block.input})")
    elif isinstance(msg, UserMessage):
        # 工具结果以 UserMessage + ToolResultBlock 形式回流给 Claude
        for block in msg.content:
            if isinstance(block, ToolResultBlock):
                prefix = "[工具结果-错误]" if block.is_error else "[工具结果]"
                print(f"{prefix} {_extract_tool_result_text(block)}")
    elif isinstance(msg, ResultMessage):
        if msg.is_error:
            print(f"[本轮异常] {getattr(msg, 'result', '')}")
        cost = getattr(msg, "total_cost_usd", None)
        if cost:
            print(f"[本轮成本] ${cost:.4f}")


async def repl() -> None:
    options = build_options()
    print("=== harmony-game-agent 交互式 REPL ===")
    print("可用工具：generate_character_stats / generate_skill_system / "
          "generate_inventory / generate_enemy_ai / scaffold_deveco_project / review_arkts_code")
    print("输入 exit 或 quit 退出。\n")

    async with ClaudeSDKClient(options=options) as client:
        while True:
            try:
                user_input = input("你> ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n再见。")
                break
            if not user_input:
                continue
            if user_input.lower() in {"exit", "quit", "/q", "退出"}:
                print("再见。")
                break

            try:
                await client.query(user_input)
                async for msg in client.receive_response():
                    print_message(msg)
            except Exception as e:
                # 中转余额不足、鉴权失败等：打印友好提示，继续下一轮
                print(f"\n[错误] {e}")


if __name__ == "__main__":
    anyio.run(repl)

"""HarmonyOS SDK baseline policy shared by generators and analyzers."""

COMPATIBLE_SDK_VERSION = "6.1.0(23)"
TARGET_SDK_VERSION = COMPATIBLE_SDK_VERSION
COMPATIBLE_API_LEVEL = 23

SDK_POLICY_TEXT = (
    "SDK 版本基线：生成和审查代码时，以 HarmonyOS compatibleSdkVersion "
    f"{COMPATIBLE_SDK_VERSION}（API {COMPATIBLE_API_LEVEL}）及以上为准；"
    "优先使用该版本可用的 ArkTS/ArkUI/Stage 模型与 DevEco 工程配置；"
    "不要为了兼容更低版本而回退到旧 API、旧工程结构或已废弃写法。"
)

SDK_REVIEW_POLICY_TEXT = (
    SDK_POLICY_TEXT
    + " 审查时必须把低版本 API、旧版工程配置、V1/V2 混用和不适用于该基线的写法标为问题。"
)

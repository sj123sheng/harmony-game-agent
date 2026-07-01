"""分析工具包：共享框架 + 4 个分析工具。"""

from analyzers.framework import FileRef, analyze_with_context, resolve_scope

__all__ = ["FileRef", "analyze_with_context", "resolve_scope"]

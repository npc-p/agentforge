"""工具包。

在此导入 builtin 子包，任何 `import app.tools` 都会触发内置工具注册——
否则注册表在服务进程内会是空的（曾因缺少此导入导致 /api/tools 返回空列表）。
"""

from app.tools import builtin  # noqa: F401  触发内置工具注册
from app.tools.registry import ToolRegistry, ToolSpec, registry, tool

__all__ = ["builtin", "registry", "tool", "ToolSpec", "ToolRegistry"]

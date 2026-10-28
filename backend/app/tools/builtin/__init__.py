"""工具包入口：导入即完成注册。

新增自定义工具只需在本目录新增模块并在下方 import，无需改动 Agent 代码。
"""

from app.tools.builtin.calculator import calculator  # noqa: F401
from app.tools.builtin.knowledge import knowledge_search  # noqa: F401
from app.tools.builtin.sql import sql_query  # noqa: F401
from app.tools.builtin.system import current_time  # noqa: F401
from app.tools.builtin.web import http_request  # noqa: F401

__all__ = ["calculator", "current_time", "knowledge_search", "sql_query", "http_request"]

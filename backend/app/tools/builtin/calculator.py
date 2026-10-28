"""计算器工具：基于 AST 白名单的安全求值。

不使用 eval 的原因：模型输出不可信，任何 `__import__` / 属性访问都可能造成任意
代码执行。AST 白名单只允许算术节点，从结构上封死这条路。
"""

from __future__ import annotations

import ast
import operator
from typing import Annotated, Any

from pydantic import Field

from app.core.exceptions import ToolExecutionError
from app.tools.registry import tool

_ALLOWED_BINOPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_ALLOWED_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_MAX_EXPR_LEN = 200
_MAX_POW = 1000


def _eval_node(node: ast.AST) -> float | int:
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ToolExecutionError("表达式仅支持数值常量")
        return node.value
    if isinstance(node, ast.BinOp):
        op = _ALLOWED_BINOPS.get(type(node.op))
        if op is None:
            raise ToolExecutionError(f"不支持的运算符: {type(node.op).__name__}")
        left, right = _eval_node(node.left), _eval_node(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_POW:
            raise ToolExecutionError("指数过大，已拒绝计算")
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise ToolExecutionError("除数不能为 0")
        return op(left, right)
    if isinstance(node, ast.UnaryOp):
        op = _ALLOWED_UNARY.get(type(node.op))
        if op is None:
            raise ToolExecutionError(f"不支持的一元运算符: {type(node.op).__name__}")
        return op(_eval_node(node.operand))
    raise ToolExecutionError(f"表达式包含不允许的语法节点: {type(node).__name__}")


@tool(
    name="calculator",
    display_name="数学计算",
    description="计算四则运算与幂运算表达式，支持括号。传入纯数学表达式，例如 (12+8)*3/4。",
    category="math",
    timeout_s=5.0,
    idempotent=True,
    tags=["math", "确定性"],
)
async def calculator(
    expression: Annotated[str, Field(description="数学表达式，仅含数字、+-*/%() 与 ** 运算符")],
) -> dict[str, Any]:
    expr = (expression or "").strip().replace("×", "*").replace("÷", "/").replace("，", ",")
    if not expr:
        raise ToolExecutionError("表达式为空")
    if len(expr) > _MAX_EXPR_LEN:
        raise ToolExecutionError("表达式过长")
    if not any(ch.isdigit() for ch in expr):
        raise ToolExecutionError("表达式中未发现数字")

    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as exc:
        raise ToolExecutionError(f"表达式语法错误: {exc.msg}") from exc

    value = _eval_node(tree)
    if isinstance(value, float):
        value = round(value, 10)

    return {"expression": expr, "result": value}

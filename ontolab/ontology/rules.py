"""Business rule evaluation, kept separate from graph retrieval.

A rule is a boolean expression over a context of named facts, written in a small
safe language (no function calls, no attribute access):

    ac_connected == true and battery_percent >= 20
    checklist_id in ['CL_A', 'CL_B'] and removable_battery != true

Evaluation is three-valued: True, False, or None when the context is missing a
fact the expression needs. Being connected in the graph never implies eligibility;
only evaluating the rule does.
"""

from __future__ import annotations

import ast
from typing import Any

_CONSTANTS = {"true": True, "false": False, "null": None, "none": None}


class RuleError(ValueError):
    """The expression uses something outside the supported language."""


def referenced_names(expression: str) -> list[str]:
    tree = _parse(expression)
    names = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id.lower() not in _CONSTANTS}
    return sorted(names)


def evaluate(expression: str, context: dict[str, Any] | None) -> dict:
    """Returns {satisfied: True|False|None, missing_context: [...], expression}."""
    context = context or {}
    tree = _parse(expression)
    missing = [name for name in referenced_names(expression) if name not in context]
    if missing:
        return {"satisfied": None, "missing_context": missing, "expression": expression}
    return {"satisfied": bool(_eval(tree.body, context)), "missing_context": [], "expression": expression}


def _parse(expression: str) -> ast.Expression:
    if not isinstance(expression, str) or not expression.strip():
        raise RuleError("rule expression is empty")
    try:
        tree = ast.parse(expression.strip(), mode="eval")
    except SyntaxError as exc:
        raise RuleError(f"cannot parse rule expression: {exc.msg}") from None
    for node in ast.walk(tree):
        if not isinstance(node, _ALLOWED):
            raise RuleError(f"unsupported syntax in rule expression: {type(node).__name__}")
    return tree


_ALLOWED = (
    ast.Expression, ast.BoolOp, ast.And, ast.Or, ast.UnaryOp, ast.Not, ast.USub, ast.Compare, ast.Eq,
    ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE, ast.In, ast.NotIn, ast.Name, ast.Load, ast.Constant,
    ast.List, ast.Tuple,
)


def _eval(node: ast.AST, context: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        key = node.id.lower()
        return _CONSTANTS[key] if key in _CONSTANTS else context[node.id]
    if isinstance(node, (ast.List, ast.Tuple)):
        return [_eval(item, context) for item in node.elts]
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, context)
        return (not value) if isinstance(node.op, ast.Not) else -value
    if isinstance(node, ast.BoolOp):
        values = [_eval(v, context) for v in node.values]
        return all(values) if isinstance(node.op, ast.And) else any(values)
    if isinstance(node, ast.Compare):
        left = _eval(node.left, context)
        for op, right_node in zip(node.ops, node.comparators):
            right = _eval(right_node, context)
            if not _compare(op, left, right):
                return False
            left = right
        return True
    raise RuleError(f"unsupported syntax in rule expression: {type(node).__name__}")


def _compare(op: ast.cmpop, left: Any, right: Any) -> bool:
    try:
        if isinstance(op, ast.Eq):
            return left == right
        if isinstance(op, ast.NotEq):
            return left != right
        if isinstance(op, ast.In):
            return left in right
        if isinstance(op, ast.NotIn):
            return left not in right
        if isinstance(op, ast.Lt):
            return left < right
        if isinstance(op, ast.LtE):
            return left <= right
        if isinstance(op, ast.Gt):
            return left > right
        if isinstance(op, ast.GtE):
            return left >= right
    except TypeError:
        raise RuleError(f"cannot compare {left!r} with {right!r}") from None
    raise RuleError("unsupported comparison")

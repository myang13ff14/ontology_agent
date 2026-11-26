"""Business rule evaluation, kept separate from graph retrieval.

A rule is a boolean expression over a context of named facts, written in a small
safe language (no function calls, no attribute access):

    ac_connected == true and battery_percent >= 20
    checklist_id in ['CL_A', 'CL_B'] and removable_battery != true

Evaluation is three-valued: True, False, or None when the outcome depends on a
fact the context does not contain. Being connected in the graph never implies eligibility;
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
    """Returns {satisfied: True|False|None, missing_context: [...], expression}.

    Unknown facts follow three-valued logic: `a or b` is true when `a` is true even
    if `b` is unknown, and `a and b` is false when `a` is false. Only when the
    outcome really depends on a missing fact is the result None.
    """
    context = context or {}
    tree = _parse(expression)
    value = _eval(tree.body, context)
    if value is _UNKNOWN:
        missing = [name for name in referenced_names(expression) if name not in context]
        return {"satisfied": None, "missing_context": missing, "expression": expression}
    return {"satisfied": bool(value), "missing_context": [], "expression": expression}


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


_UNKNOWN = object()  # a fact the context does not contain


def _eval(node: ast.AST, context: dict[str, Any]) -> Any:
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        key = node.id.lower()
        if key in _CONSTANTS:
            return _CONSTANTS[key]
        return context[node.id] if node.id in context else _UNKNOWN
    if isinstance(node, (ast.List, ast.Tuple)):
        items = [_eval(item, context) for item in node.elts]
        return _UNKNOWN if any(item is _UNKNOWN for item in items) else items
    if isinstance(node, ast.UnaryOp):
        value = _eval(node.operand, context)
        if value is _UNKNOWN:
            return _UNKNOWN
        return (not value) if isinstance(node.op, ast.Not) else -value
    if isinstance(node, ast.BoolOp):
        values = [_eval(v, context) for v in node.values]
        known = [bool(v) for v in values if v is not _UNKNOWN]
        if isinstance(node.op, ast.And):
            if False in known:
                return False
            return _UNKNOWN if len(known) < len(values) else True
        if True in known:
            return True
        return _UNKNOWN if len(known) < len(values) else False
    if isinstance(node, ast.Compare):
        left = _eval(node.left, context)
        for op, right_node in zip(node.ops, node.comparators):
            right = _eval(right_node, context)
            if left is _UNKNOWN or right is _UNKNOWN:
                return _UNKNOWN
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

"""Structured errors shared by every layer that tools call into."""

from __future__ import annotations

from typing import Any


class ToolError(Exception):
    """A stable code, a human-readable message, and details the caller can act on."""

    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details

    def to_dict(self) -> dict:
        return {"code": self.code, "message": self.message, "details": self.details}

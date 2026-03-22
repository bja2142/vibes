from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class SemanticError(Exception):
    error_code: str
    message: str
    target: dict[str, Any] = field(default_factory=dict)
    retryable: bool = False
    likely_causes: list[str] = field(default_factory=list)
    next_steps: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "error_code": self.error_code,
            "message": self.message,
            "target": self.target,
            "retryable": self.retryable,
            "likely_causes": self.likely_causes,
            "next_steps": self.next_steps,
        }

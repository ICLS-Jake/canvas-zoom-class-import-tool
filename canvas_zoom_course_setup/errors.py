from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AppError(Exception):
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    cause: Exception | None = None

    def __str__(self) -> str:
        return f"{self.code}: {self.message}"


@dataclass
class ApiError(AppError):
    source: str = "HTTP"
    status_code: int | None = None
    response_body: Any = None

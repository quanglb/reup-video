"""Mô tả một stage: tên, artifact nó sinh ra, và hàm chạy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from reup.config import Config
    from reup.core.job import Job


@dataclass(frozen=True)
class StageSpec:
    name: str
    produces: tuple[str, ...]
    run: Callable[["Job", "Config"], None]

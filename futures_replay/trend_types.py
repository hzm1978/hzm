from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TrendPivot:
    index: int
    price: float
    kind: str
    confirmed_at: int


@dataclass(frozen=True)
class TrendLabel:
    index: int
    price: float
    text: str
    direction: int
    confirmed_at: int
    start_index: int
    context: str = ""
    percent: float | None = None
    qualified: bool = True
    evidence: str = ""


@dataclass(frozen=True)
class TrendCorrection:
    kind: str
    label: str
    start_index: int
    end_index: int
    lines: tuple[tuple[tuple[float, float], tuple[float, float]], ...]
    markers: tuple
    marker_labels: tuple[str, ...]
    label_index: int
    label_price: float
    direction: int
    confirmed_at: int
    state: str = "candidate"
    structure: str = ""
    end_point: TrendPivot | None = None
    evidence: str = ""
    degree: int = 0
    owner: tuple[int, int] | None = None

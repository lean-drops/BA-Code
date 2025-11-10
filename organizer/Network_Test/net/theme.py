from __future__ import annotations
from dataclasses import dataclass

@dataclass
class Theme:
    bg: str = "#0b0f14"
    chronik_fill_hi: str = "#1d4f58"
    chronik_fill_lo: str = "#0a1f2a"
    chronik_stroke: str = "#0a1a1f"
    work_fill_hi: str = "#f1e4c1"
    work_fill_lo: str = "#c89b3c"
    work_stroke: str = "#5a3f0b"
    label: str = "#f4f1e9"
    label_bg: str = "#0b0f14"
    edge: str = "#6b7280"
    edge_sel: str = "#ef4444"

    @staticmethod
    def dark() -> "Theme":
        return Theme()

GROUP_COLORS = ["#ef4444", "#10b981", "#3b82f6", "#eab308", "#a855f7", "#f97316"]


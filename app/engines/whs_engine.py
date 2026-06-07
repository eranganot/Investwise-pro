"""4.3 WHS ENGINE - Wealth Health Score.

WHS = 0.25*Risk + 0.25*Tax + 0.20*Alloc + 0.15*Liq + 0.15*Thematic [Phase 4].
Phase 0 stub returns an 'Awaiting Data' snapshot shape.
"""
from __future__ import annotations

from app.core.config import Settings, get_settings


class WhsEngine:
    WEIGHTS = {"risk": 0.25, "tax": 0.25, "alloc": 0.20, "liq": 0.15, "thematic": 0.15}

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()

    def snapshot(self) -> dict:
        return {
            "score": None,
            "components": {k: None for k in self.WEIGHTS},
            "status": "Awaiting Data - WHS computation not yet implemented (Phase 4).",
        }

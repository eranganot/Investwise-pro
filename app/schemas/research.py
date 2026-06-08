"""Research Agent evidence contract (Section AA)."""
from __future__ import annotations

from pydantic import BaseModel


class ResearchEvent(BaseModel):
    event_id: str
    event_type: str
    relevance_score: int            # 0-100
    affected_assets: list[str]
    expected_time_horizon: str      # SHORT | MEDIUM | LONG
    confidence: int                 # 0-100

"""Section AA - RESEARCH AGENT (passive, read-only market intelligence).

Builds clean, structured evidence payloads from the Economic/Market providers
to feed downstream agents. INVARIANT: it never recommends an action or mutates
portfolio state - it only emits ResearchEvent evidence.
"""
from __future__ import annotations

import hashlib

from app.providers.registry import guarded_events
from app.schemas.research import ResearchEvent


class ResearchAgent:
    def scan(self) -> list[ResearchEvent]:
        events = guarded_events()
        out = []
        for e in events:
            eid = "evt_" + hashlib.sha1((e.event_type + e.description).encode()).hexdigest()[:5]
            out.append(ResearchEvent(
                event_id=eid,
                event_type=e.event_type,
                relevance_score=e.severity,
                affected_assets=e.affected_assets,
                expected_time_horizon=e.horizon,
                confidence=max(0, min(100, e.severity - 5)),
            ))
        out.sort(key=lambda r: r.relevance_score, reverse=True)
        return out

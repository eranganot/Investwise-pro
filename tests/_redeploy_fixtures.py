"""Shared fixtures for the PR 75 cash-research tests (pure: no network)."""
from app.services import redeploy_research as rr

MIN = 250.0


def S(score, screen="Equities", gaps=(), cls=None, kind="stock"):
    return {"score": score, "screen": screen, "asset_class": cls or screen,
            "data_gaps": list(gaps), "reasons": [], "name": None, "kind": kind}


def trend(states):
    return lambda tk: {"state": states.get(tk, "mixed")}


def run(legs, scores, *, sleeves=(), held=("QQQ",), states=None, room=1e9):
    unplaced = []
    out = rr.research(legs, unplaced, sleeve_tickers=set(sleeves), held_tickers=set(held),
                      scores=scores, trend=trend(states or {}),
                      room_for=lambda tk: room, min_trade=MIN)
    return legs, unplaced, out

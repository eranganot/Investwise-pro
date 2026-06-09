"""Agent War Room - stage-by-stage reasoning trace from the real pipeline.

Steps a signal through the lifecycle and records what each agent 'said', as a
chat transcript plus structured detail for each line.
"""
from __future__ import annotations

from app.agents import adversary
from app.agents.research_agent import ResearchAgent
from app.engines.lag_engine import LagEngine
from app.engines.risk_engine import RiskEngine
from app.engines.state_machine import StateMachine

AGENTS = ["Research", "Alpha", "Risk", "Tax", "Decision", "Adversary", "UX"]


def _tax_say(opt) -> str:
    if opt.net_gain_delta is None:
        return "No realized-gain data, so I can't compute tax impact yet (Awaiting Data)."
    bits = []
    if opt.tax_saved:
        bits.append(f"~₪{opt.tax_saved:,.0f} saved via losses")
    if opt.tax_deferred:
        bits.append(f"~₪{opt.tax_deferred:,.0f} of tax deferred")
    if opt.actual_tax_cost:
        bits.append(f"~₪{opt.actual_tax_cost:,.0f} tax now")
    return "Net-after-tax computed" + (": " + ", ".join(bits) + "." if bits else ".")


def build_war_room(observations, portfolio_tickers=None) -> dict:
    portfolio_tickers = portfolio_tickers or set()
    lag = LagEngine()
    sm = StateMachine(risk=RiskEngine(seed=7))
    research = ResearchAgent().scan()
    sessions = []

    for det in lag.scan(observations):
        t: list[dict] = []
        if research:
            ev = research[0]
            t.append({"agent": "Research", "role": "Market Intelligence (read-only)",
                      "says": f"Watching {ev.event_type.replace('_', ' ').lower()} "
                              f"(relevance {ev.relevance_score}/100), affecting "
                              f"{', '.join(ev.affected_assets)}.",
                      "detail": {"events": [e.model_dump() for e in research[:3]]}})
        t.append({"agent": "Alpha", "role": "Strategist · Lag",
                  "says": f"Found a Depth-{det.depth} "
                          f"{'backbone (high-conviction)' if det.depth == 3 else 'surface'} signal on "
                          f"{det.ticker}: {det.trigger}.",
                  "detail": {"depth": det.depth, "divergence_pct": det.divergence_pct,
                             "market": det.market.value}})

        vetted = sm.vet(det)
        if vetted.veto_flag:
            t.append({"agent": "Risk", "role": "Red-Team · stress test",
                      "says": f"I'm vetoing this. {vetted.risk_critique}",
                      "detail": {"veto": True, "critique": vetted.risk_critique}})
            sessions.append({"ticker": det.ticker, "outcome": "VETOED", "source": "portfolio" if det.ticker in portfolio_tickers else "market",
                             "outcome_label": "Skipped — too risky", "transcript": t})
            continue
        t.append({"agent": "Risk", "role": "Red-Team · stress test",
                  "says": (f"Stress test passed. {vetted.risk_critique}"
                           if vetted.probability_of_ruin is not None
                           else "No volatility input, so I left risk unassessed (Awaiting Data)."),
                  "detail": {"probability_of_ruin": vetted.probability_of_ruin,
                             "median_max_drawdown": vetted.max_drawdown, "volatility": vetted.volatility}})

        optimized = sm.optimize(vetted)
        t.append({"agent": "Tax", "role": "Tax optimizer", "says": _tax_say(optimized),
                  "detail": {"net_gain_delta": optimized.net_gain_delta,
                             "tax_saved": optimized.tax_saved, "tax_deferred": optimized.tax_deferred}})

        ranked = sm.rank(optimized)
        t.append({"agent": "Decision", "role": "Scorer",
                  "says": f"Impact {ranked.impact_score:.0f}/100 at {ranked.confidence:.0f}% confidence. "
                          f"Mix — return {ranked.scores.ret:.0f}, tax {ranked.scores.tax:.0f}, "
                          f"risk {ranked.scores.risk:.0f}, conviction {ranked.scores.conviction:.0f}.",
                  "detail": {"impact": round(ranked.impact_score, 1), "confidence": round(ranked.confidence, 1),
                             "scores": ranked.scores.as_contract(),
                             "confidence_breakdown": ranked.confidence_breakdown.model_dump()}})

        display = sm.display(ranked)
        if display is None:
            t.append({"agent": "UX", "role": "Decision Feed",
                      "says": "Below the quality bar (Impact < 20 or Confidence < 60) — no action.",
                      "detail": {"impact": round(ranked.impact_score, 1),
                                 "confidence": round(ranked.confidence, 1)}})
            sessions.append({"ticker": det.ticker, "outcome": "NO_ACTION", "source": "portfolio" if det.ticker in portfolio_tickers else "market",
                             "outcome_label": "No action — not strong enough", "transcript": t})
            continue

        crit = adversary.critique(path=display.path, risk_critique=vetted.risk_critique,
                                  confidence=ranked.confidence, impact=ranked.impact_score)
        t.append({"agent": "Adversary", "role": "Red-Team · final word", "says": crit, "detail": {}})
        t.append({"agent": "UX", "role": "Decision Feed",
                  "says": f"Approved on the {display.path} path: {display.title}.",
                  "detail": {"path": display.path, "title": display.title}})
        sessions.append({"ticker": det.ticker, "outcome": "DISPLAYED", "source": "portfolio" if det.ticker in portfolio_tickers else "market",
                         "outcome_label": display.path, "title": display.title, "transcript": t})

    return {"agents": AGENTS, "count": len(sessions), "sessions": sessions}

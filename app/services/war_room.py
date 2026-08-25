"""Agent War Room - stage-by-stage reasoning trace from the real pipeline.

Steps a signal through the lifecycle and records what each agent 'said', as a
chat transcript plus structured detail for each line.
"""
from __future__ import annotations

from datetime import datetime, timezone

from app.agents import adversary
from app.agents.research_agent import ResearchAgent
from app.engines.decision_engine import DecisionEngine
from app.engines.lag_engine import LagEngine
from app.engines.risk_engine import RiskEngine
from app.engines.state_machine import StateMachine
from app.engines.tax_engine import TaxEngine

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


def _adds_risk(det) -> bool:
    """Would acting on this signal INCREASE the book's exposure?

    The solver constraint says "this book cannot reach its target". The honest
    consequence is to stop one-tapping MORE risk into it, not to stop acting --
    a trim, a hedge or a de-risking rebalance is what such a book should be
    doing, and blocking those would turn a safety feature into a trap.

    Keyed off `action_type`, not `display.path`. The path label is "Growth"
    exactly when the action is a BUY and "Bulletproof" otherwise, so "approved
    on the Bulletproof path" describes, by definition, something that is not a
    buy -- and that label has already been misread once.
    """
    at = getattr(det, "action_type", None)
    return str(getattr(at, "value", at) or "").upper() == "BUY"


def build_war_room(observations, portfolio_tickers=None, settings=None, *,
                   narrate: bool = True, constraint: dict | None = None) -> dict:
    """Run the agent pipeline over each observation.

    ``narrate=False`` skips the optional Gemini prose. Measured on /recommendations:
    the war room was 5.4s cold and 6.7s WARM while every other agent came in
    under 50ms -- warm being slower is the tell, because the narrative is a live
    LLM call per signal on the event loop, not cacheable provider data. It got
    slower once Gemini billing was topped up, since the calls had previously
    failed fast with 429. The Today cards never render this prose (they use
    outcome_label, impact and confidence), so it is pure cost on that path. The
    war-room view itself still asks for it.

    ``constraint`` is T5: the target solver's last verdict, from
    ``solver_store.as_constraint``. Two things it is NOT -- it is not an eighth
    agent, and it is not the decision maker. It is an input the Risk and
    Adversary lines consult, the way they already consult volatility and tax. A
    signal that would ADD RISK to a book the solver says cannot reach its target
    is not silently deleted; it is marked ``blocked_by`` so the card can show the
    finding and refuse the one-tap Accept.

    This function also used to run ``vet``/``optimize``/``rank``/``display``
    individually and append the Adversary's findings as prose, branching on
    nothing. ``adversary_enforce_veto`` defaults to True and was honoured on
    exactly one route -- /api/v1/decision-feed -- while the route that produces
    the cards people actually see had no veto at all. Both are fixed here, and
    the rule now lives in ``StateMachine.blocks_now`` so there is one copy.
    """
    portfolio_tickers = portfolio_tickers or set()
    lag = LagEngine(settings)
    sm = StateMachine(risk=RiskEngine(settings, seed=7), tax=TaxEngine(settings),
                      decision=DecisionEngine(settings), settings=settings)
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
            _rp = (f"~{vetted.probability_of_ruin:.0%} chance of a deep drawdown, "
                   f"typical worst dip ~{vetted.max_drawdown:.0%}. "
                   if vetted.probability_of_ruin is not None else "")
            t.append({"agent": "Risk", "role": "Red-Team · stress test",
                      "says": (f"Ran 10,000 Monte Carlo simulations: {_rp}"
                               f"That breaches your risk limits, so I'm vetoing it — this is why it isn't recommended. "
                               f"{vetted.risk_critique}"),
                      "detail": {"veto": True, "critique": vetted.risk_critique,
                                 "probability_of_ruin": vetted.probability_of_ruin,
                                 "median_max_drawdown": vetted.max_drawdown,
                                 "volatility": vetted.volatility}})
            sessions.append({"ticker": det.ticker, "outcome": "VETOED", "source": "portfolio" if det.ticker in portfolio_tickers else "market",
                             "outcome_label": "Skipped — too risky", "transcript": t})
            continue
        t.append({"agent": "Risk", "role": "Red-Team · stress test",
                  "says": ((f"Ran 10,000 Monte Carlo simulations: ~{vetted.probability_of_ruin:.0%} chance of a "
                            f"deep drawdown, typical worst dip ~{vetted.max_drawdown:.0%}. "
                            f"Within your limits, so I'm letting it through.")
                           if vetted.probability_of_ruin is not None
                           else "No volatility input, so I left risk unassessed (Awaiting Data)."),
                  "detail": {"probability_of_ruin": vetted.probability_of_ruin,
                             "median_max_drawdown": vetted.max_drawdown, "volatility": vetted.volatility}})

        _blocked = None
        for _an in (sm.adversary.examine_detected(det), sm.adversary.examine_vetted(vetted)):
            t.append({"agent": "Adversary", "role": "Red-Team · cross-examination",
                      "says": _an.critique, "detail": {"severity": _an.severity.value,
                                                       "findings": _an.findings,
                                                       "blocks": bool(_an.blocks)}})
            # AND NOW IT ACTS. The same rule `cross_examine` uses, from the same
            # function, so the two paths cannot drift apart again.
            if _blocked is None and sm.blocks_now(_an):
                _blocked = _an

        optimized = sm.optimize(vetted)
        t.append({"agent": "Tax", "role": "Tax optimizer", "says": _tax_say(optimized),
                  "detail": {"net_gain_delta": optimized.net_gain_delta,
                             "tax_saved": optimized.tax_saved, "tax_deferred": optimized.tax_deferred}})
        _ao = sm.adversary.examine_optimized(optimized)
        t.append({"agent": "Adversary", "role": "Red-Team · cross-examination",
                  "says": _ao.critique, "detail": {"severity": _ao.severity.value,
                                                   "findings": _ao.findings,
                                                   "blocks": bool(_ao.blocks)}})
        if _blocked is None and sm.blocks_now(_ao):
            _blocked = _ao

        ranked = sm.rank(optimized)
        t.append({"agent": "Decision", "role": "Scorer",
                  "says": f"Impact {ranked.impact_score:.0f}/100 at {ranked.confidence:.0f}% confidence. "
                          f"Mix — return {ranked.scores.ret:.0f}, tax {ranked.scores.tax:.0f}, "
                          f"risk {ranked.scores.risk:.0f}, conviction {ranked.scores.conviction:.0f}.",
                  "detail": {"impact": round(ranked.impact_score, 1), "confidence": round(ranked.confidence, 1),
                             "scores": ranked.scores.as_contract(),
                             "confidence_breakdown": ranked.confidence_breakdown.model_dump()}})
        _ar = sm.adversary.examine_ranked(ranked)
        t.append({"agent": "Adversary", "role": "Red-Team · cross-examination",
                  "says": _ar.critique, "detail": {"severity": _ar.severity.value,
                                                   "findings": _ar.findings,
                                                   "blocks": bool(_ar.blocks)}})
        if _blocked is None and sm.blocks_now(_ar):
            _blocked = _ar

        if _blocked is not None:
            # A BLOCK IS A VETO ON THIS PATH TOO. It was prose before, and the
            # only thing between a red-team BLOCK and a one-tap Accept was that
            # nobody had wired it up.
            t.append({"agent": "Adversary", "role": "Red-Team · veto",
                      "says": f"Blocking this: {_blocked.critique}",
                      "detail": {"severity": _blocked.severity.value,
                                 "findings": _blocked.findings, "enforced": True}})
            sessions.append({"ticker": det.ticker, "outcome": "VETOED",
                             "source": "portfolio" if det.ticker in portfolio_tickers else "market",
                             "action_type": getattr(det.action_type, "value", det.action_type),
                             "outcome_label": "Vetoed by the red team", "transcript": t})
            continue

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
        _nar = sm.adversary.narrate(
            [sm.adversary.examine_detected(det), sm.adversary.examine_vetted(vetted),
             sm.adversary.examine_optimized(optimized), sm.adversary.examine_ranked(ranked)],
            context=display.title) if narrate else None
        if _nar:
            t.append({"agent": "Adversary", "role": "Red-Team · AI narrative",
                      "says": _nar, "detail": {"source": "gemini"}})
        t.append({"agent": "UX", "role": "Decision Feed",
                  "says": f"Approved on the {display.path} path: {display.title}.",
                  "detail": {"path": display.path, "title": display.title}})
        # `action_type` travels with the session. It never did, so the guard in
        # `_war_room_recs` that routes a REBALANCE signal to the trim branch --
        # `if s.get("action_type") == "REBALANCE" or ...` -- read None every
        # time and was dead code. A trim signal on a name you do NOT hold fell
        # through into the BUY branch and got sized as a purchase. Note also
        # that `path` is "Bulletproof" exactly when the signal is NOT a BUY, so
        # a card headed "approved on the Bulletproof path" was, by definition,
        # never a buy thesis.
        # T5. The solver is a CONSTRAINT the agents consult, never a veto of its
        # own: `blocked_by` keeps the card visible and removes its one-tap
        # Accept. Suppressing it would be the PR 4 failure mode -- eleven cards
        # went missing there -- and a card deleted because of a background
        # measurement nobody can see is worse than one that explains itself.
        #
        # Scoped to signals that ADD RISK. A trim, a hedge or a de-risking
        # rebalance is exactly what a book in this state should be doing, and
        # blocking those would turn a safety feature into a trap.
        _blocked_by = None
        if _adds_risk(det) and (constraint or {}).get("blocks"):
            _blocked_by = {"kind": "solver", "outcome": constraint.get("outcome"),
                           "headline": constraint.get("headline"),
                           "reason": constraint.get("reason"),
                           "age_hours": constraint.get("age_hours"),
                           "solved_at": constraint.get("solved_at")}
            t.append({"agent": "Risk", "role": "Target solver · constraint",
                      "says": constraint.get("reason") or "The plan cannot reach its target.",
                      "detail": {"outcome": constraint.get("outcome"),
                                 "age_hours": constraint.get("age_hours"),
                                 "floor_breaches_ceiling": constraint.get("floor_breaches_ceiling"),
                                 "blocks_one_tap": True}})
        elif (constraint or {}).get("known"):
            # Said even when it does not bite, so the transcript shows the
            # constraint WAS consulted. Silence is indistinguishable from
            # "nobody asked", which is the state this phase exists to end.
            t.append({"agent": "Risk", "role": "Target solver · constraint",
                      "says": constraint.get("reason") or "",
                      "detail": {"outcome": constraint.get("outcome"),
                                 "age_hours": constraint.get("age_hours"),
                                 "blocks_one_tap": False}})

        sessions.append({"ticker": det.ticker, "outcome": "DISPLAYED",
                         "source": "portfolio" if det.ticker in portfolio_tickers else "market",
                         "action_type": getattr(det.action_type, "value", det.action_type),
                         "outcome_label": display.path, "title": display.title,
                         "blocked_by": _blocked_by, "transcript": t})

    generated_at = datetime.now(timezone.utc).isoformat()
    for _s in sessions:
        _s["decided_at"] = generated_at
    return {"agents": AGENTS, "count": len(sessions), "generated_at": generated_at,
            "sessions": sessions}

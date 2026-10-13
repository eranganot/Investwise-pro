"""Where the book could end up, and what deposits do to it (GET /plan/projection).

PR 118a (L7): moved out of `routes/plan.py`, unchanged. Plan health and the
assistant context call `project()`; they used to call the route handler.
"""
from app.engines.simulation_engine import SimulationEngine
from app.services.plan_service import get_plan
from app.services.plan_view import book_measured, frontier_basis_text, goal_of, portfolio_stats, positions

# W3. The excess this compares the deposit against. One percentage point a year
# is deliberately GENEROUS as an "admissible" improvement: PR 13's core search
# and T6a's sleeve sizing both spend their whole budget arguing over less than
# this. Stated here so the comparison cannot be tuned after the numbers are seen
# -- the same rule W1's bar follows.
EXCESS_PP = 1.0


async def project(session, user, contribution_per_year: float | None = None) -> dict:
    """Where the book could end up -- and, since W3, what your deposits do to it.

    Three simulations, because the claim Phase W actually makes is comparative:

      1. **base** -- no deposits. Byte for byte the projection this endpoint has
         always returned. Every pre-W3 key still describes THIS run.
      2. **with_contributions** -- the same book plus a deposit rate that was
         MEASURED from your ledger, or one you passed explicitly. Absent, with a
         named reason, when neither exists. Never an assumed savings rate.
      3. **excess_comparison** -- the base run again at +1pp of annual return.

    (3) is not decoration. The spec's claim is that *"at the book's current NAV
    the contribution rate moves terminal value more than any admissible excess
    does"*, and a claim like that is either on screen next to its rival or it is
    just a sentence. `moves_more` names the winner and can say "excess" -- if
    deposits stop mattering at some NAV, this is what shows it.

    The card also stops dropping the engine's own caveat. Until W3 `assumptions`
    was rebuilt here from scratch and silently omitted *"no rebalancing or cash
    flows during the horizon"*, so the card showed a no-contribution projection
    without ever saying it was one.
    """
    from app.services.intake_service import contribution_rate

    rows = await positions(session, user)
    stats = portfolio_stats(rows, await book_measured(session, user))
    plan = await get_plan(session, user)
    if not stats["nav"]:
        return {"message": "Add holdings to project your goal."}
    goal = await goal_of(session, user, plan, stats["nav"])
    target = goal.get("target")
    # The deadline governs (Eran, Q1): years LEFT to it, the same figure the
    # behind card uses. Horizon only when there is no deadline at all.
    if goal.get("deadline"):
        if not goal.get("years_left"):
            # A deadline that has passed. Projecting over the horizon instead
            # quoted "6 years" and a 99% chance against a date already gone.
            return {"message": (f"Your goal's deadline ({goal['deadline']}) has passed. "
                                f"Set a new deadline to project it."),
                    "target_amount": target, "deadline": goal["deadline"], "years": 0}
        years = goal["years_left"]
    else:
        years = max(1, plan.horizon_years if plan else 10)
    roi = stats["expected_roi"] or 6.0
    vol = stats["volatility"] or 12.0

    from app.engines.return_convention import CONVENTION, CONVENTION_NOTE, drift_pct

    def _run(rate: float | None = None, **kw):
        # Same seed on every run, so the three are compared on the same draws and
        # a difference between them is the lever, not the sampler.
        # PR 76: `rate` is a COMPOUND rate; the engine gets its drift, so the
        # median grows at the stated rate (see `engines.return_convention`).
        args = {"initial_value": stats["nav"],
                "expected_return_pct": drift_pct(roi if rate is None else rate, vol),
                "volatility_pct": vol, "horizon_years": years, "target_value": target}
        args.update(kw)
        return SimulationEngine(seed=7).run(**args)

    sim = _run()

    # THE RATE. Measured, or explicitly given, or absent with a reason.
    measured = await contribution_rate(session, user)
    # A STANDING ORDER IS A DECLARED RATE, not an assumed one. The rule here is
    # "never an assumed savings rate on someone else's card"; an order the user
    # set up is the opposite of an assumption -- it is the deposit the app will
    # itself record on the day. It outranks a rate measured from past deposits
    # because it is the forward-looking fact; an explicit override still wins.
    from app.services.standing_order_service import monthly_total
    standing_monthly = await monthly_total(session, user)
    rate, source = None, None
    if contribution_per_year is not None:
        rate, source = float(contribution_per_year), "override"
    elif standing_monthly > 0:
        rate, source = round(standing_monthly * 12, 2), "standing_order"
    elif measured.get("ok"):
        rate, source = measured["per_year"], "measured"

    contrib_block: dict = {"available": False, "source": None,
                           "measured": measured,
                           "note": measured.get("detail")}
    if rate is not None and rate > 0:
        c = _run(contribution_per_year=rate)
        contrib_block = {
            "available": True, "source": source, "per_year": round(rate, 2),
            "total_added": c.contribution_total,
            "money_in": c.money_in, "steps": c.steps,
            "timing": c.contribution_timing,
            "projected_median": round(c.nominal.p50, 2),
            "projected_low": round(c.nominal.p5, 2),
            "projected_high": round(c.nominal.p95, 2),
            "uplift_median": round(c.nominal.p50 - sim.nominal.p50, 2),
            "on_track": (c.nominal.p50 >= target) if target else None,
            "probability_meets_target": (round(c.probability_meets_target, 3)
                                         if c.probability_meets_target is not None else None),
            # The one that still means "did I lose money" once deposits exist.
            "probability_of_loss_vs_money_in": (
                round(c.probability_of_loss_vs_money_in, 3)
                if c.probability_of_loss_vs_money_in is not None else None),
            "measured": measured,
            "standing_order_monthly_ils": standing_monthly or None,
            "note": (f"deposits of {rate:,.0f}/yr, {'measured from your ledger' if source == 'measured' else 'as you asked'}, "
                     f"paid at the end of each month. Your ledger also supports "
                     f"{measured.get('per_year_excluding_first')}/yr if the first entry was "
                     f"opening capital rather than a contribution."
                     if source == "measured" else
                     f"your standing order of {standing_monthly:,.0f}/month "
                     f"({rate:,.0f}/yr), paid at the end of each month."
                     if source == "standing_order" else
                     f"deposits of {rate:,.0f}/yr, as you asked, paid at the end of each month."),
        }
    elif rate is not None:
        contrib_block["note"] = (f"a contribution rate of {rate:,.0f}/yr is not a "
                                 f"deposit, so the projection is unchanged.")

    # THE RIVAL LEVER, so the spec's claim is checkable rather than asserted.
    ex = _run(rate=roi + EXCESS_PP)
    excess_uplift = round(ex.nominal.p50 - sim.nominal.p50, 2)
    excess_block = {
        "extra_return_pp": EXCESS_PP,
        "projected_median": round(ex.nominal.p50, 2),
        "uplift_median": excess_uplift,
        "moves_more": None,
        "note": (f"an extra {EXCESS_PP:.1f}pp a year of return is worth "
                 f"{excess_uplift:,.0f} at the median over {years:.1f} years."),
    }
    if contrib_block.get("available"):
        cu = contrib_block["uplift_median"]
        excess_block["moves_more"] = "contributions" if cu > excess_uplift else "excess"
        excess_block["note"] += (
            f" Your deposits are worth {cu:,.0f}. "
            + ("The deposits move terminal value more, which is the whole of Phase W's "
               "claim about a book this size."
               if cu > excess_uplift else
               "The extra return moves terminal value more -- at this NAV and this "
               "deposit rate the spec's claim does not hold, and chasing the excess "
               "is the better-paid lever."))

    return {
        "years": round(years, 1), "starting_value": round(stats["nav"], 2),
        "deadline": goal.get("deadline"),
        "projected_median": round(sim.nominal.p50, 2),
        "projected_low": round(sim.nominal.p5, 2), "projected_high": round(sim.nominal.p95, 2),
        "target_amount": target,
        "on_track": (sim.nominal.p50 >= target) if target else None,
        "probability_meets_target": (round(sim.probability_meets_target, 3)
                                     if sim.probability_meets_target is not None else None),
        # PR 99: the goal's ONE headline chance (with the protective rules as
        # they work, rules-off beside it), so this card quotes the goal cards'
        # figure; the chart's own no-rules run stays labelled as such.
        "goal_headline": ((stats.get("expectation") or {}).get("headline")
                          if stats.get("assumption_basis") == "frontier" else None),
        "probability_of_loss_real": round(sim.probability_of_loss_real, 3),
        "probability_of_gain_real": round(sim.probability_of_gain_real, 3),
        "runs": sim.runs,
        "with_contributions": contrib_block,
        "excess_comparison": excess_block,
        # The engine's OWN assumptions, appended rather than replaced. The
        # cash-flows line is the one this card used to drop.
        # PR 16 -- the basis, on the card. At 45.6% assumed volatility the drag
        # term eats a 10.87% expected return whole, so "which volatility" is not
        # a footnote: it decides whether the median line rises at all.
        "assumption_basis": stats.get("assumption_basis", "static"),
        "measured_pct_of_book": stats.get("measured_pct_of_book"),
        "expected_return_pct_static": stats.get("static_expected_roi"),
        "volatility_pct_static": stats.get("static_volatility"),
        "return_convention": CONVENTION,
        "return_convention_note": CONVENTION_NOTE,
        "assumptions": [f"~{stats['expected_roi'] or 6}%/yr compound return (the median grows at it), "
                        f"{stats['volatility'] or 12}% volatility, over {years:.1f} years"
                        + (frontier_basis_text(stats.get("expectation"))
                           if stats.get("assumption_basis") == "frontier" else
                           f" ({stats['assumption_basis']}; "
                           f"{stats.get('measured_pct_of_book')}% of the book measured)"
                           if stats.get("assumption_basis") not in (None, "static") else
                           " (instrument table, not measured)"),
                        *sim.assumptions],
    }

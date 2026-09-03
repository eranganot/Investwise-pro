"""The core basket is not spare change for funding a buy.

Measured on production, 2026-09-03, immediately after PR 43 made the funding
sentence honest. The book was exactly on its target and nothing was overweight,
so `plan_funding`'s documented fallback funded a commodity idea from whatever it
could reach -- and what it reached for was the core:

    Consider COMT (Broad commodities) - ILS 1,465
    How it's funded:  Sell 6.7347 BND (~ILS 1,465)
    reason:           "cheapest way to raise it, but it corrects no drift:
                       Fixed Income is at 11% against a 11% target ...
                       Selling it does not move you closer to your plan"

ILS 1,465 of a ILS 2,263 position -- **65% of the bond leg of the core** -- which
would take Fixed Income from 10.8% to 3.8% against an 11% target. The sentence
was true. Saying it is not the same as declining to do it.

Sleeves were already off-limits (C3). PR 37 settled that a core is a strategy
too, and PR 41 applied that to the tax-harvest exclusion. Funding was the last
place still treating the core as a piggy bank, and it was doing so on the same
screen where the tax card refused to touch the same ticker.

Two lists of "what the plan holds on purpose" is how they come to disagree, so
there is now one: `_funding_exclusions`.

The core is not frozen. `core_to_basket` (PR 39) trades it, sized by a single
plan, and that is the path allowed to touch it.

Not financial advice.
"""
import logging
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import app.main as m  # noqa: E402

CORE = "bal_6040"          # 60% VTI / 40% BND
CORE_TICKERS = {"VTI", "BND"}


def _seed(c):
    c.post("/api/v1/intake/portfolio", json={"entity_name": "Personal", "positions": [
        {"ticker": "VTI", "market": "NYSE", "asset_class": "Equities", "depth": 1,
         "spot_price": 376.88, "listing_price": 376.88, "quantity": 9.0,
         "cost_basis": 370.0, "expected_return_pct": 7, "volatility_pct": 20},
        {"ticker": "BND", "market": "NYSE", "asset_class": "Fixed Income", "depth": 1,
         "spot_price": 71.83, "listing_price": 71.83, "quantity": 30.0,
         "cost_basis": 70.0, "expected_return_pct": 3, "volatility_pct": 6},
    ]})
    c.post("/api/v1/portfolio/cash", json={"amount_ils": 620.0})
    c.put("/api/v1/plan", json={"objective": "Grow", "risk_tolerance": "High",
                                "horizon_years": 6, "target_amount": 137547})
    assert c.post("/api/v1/plan/core", json={"strategy_id": CORE}).json()["ok"] is True


@pytest.mark.asyncio
async def test_the_core_basket_is_off_limits_as_a_funding_source(own_session):
    """`_funding_exclusions` covers the core, not only the sleeves."""
    from sqlalchemy import select

    from app.models.tables import User
    from app.services.recommendations import _book_target, _funding_exclusions

    logging.disable(logging.CRITICAL)
    try:
        with TestClient(m.app) as c:
            _seed(c)
            async with own_session() as s:
                user = (await s.execute(select(User))).scalars().first()
                btgt = await _book_target(s, user, "Grow")
                excluded = await _funding_exclusions(s, user, btgt)

        missing = CORE_TICKERS - {t.upper() for t in excluded}
        assert not missing, (
            f"{sorted(missing)} can still be sold to fund a buy; they are the "
            f"{CORE} basket the plan holds on purpose")
    finally:
        logging.disable(logging.NOTSET)


def test_there_is_one_list_of_what_the_plan_holds_on_purpose():
    """No card path may build its own exclusion set.

    This is the structural half, and it is the half that matters. PR 41 gave the
    tax card a sleeves-plus-core exclusion while funding kept a sleeves-only one,
    and for two deploys the same screen refused to harvest BND and offered to
    sell 65% of it. A second list is not a second opinion, it is a bug waiting
    for a book that distinguishes them.

    `recommendations.py` therefore resolves an exclusion set exactly one way.
    """
    import ast
    import pathlib

    FUNDING = {"propose_funded_buy", "plan_funding", "rank_trim_candidates"}

    src = (pathlib.Path(__file__).resolve().parent.parent
           / "app" / "services" / "recommendations.py")
    tree = ast.parse(src.read_text(encoding="utf-8"))

    def _call_name(node):
        return (getattr(node.func, "attr", None)
                or getattr(node.func, "id", None))

    bad = []
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Names this function bound from `_funding_exclusions(...)`, plus its own
        # parameters -- a forwarded exclusion set is the caller's to get right.
        ok_names = {a.arg for a in fn.args.args + fn.args.kwonlyargs}
        for node in ast.walk(fn):
            if isinstance(node, ast.Assign) and isinstance(node.value, ast.Await):
                inner = node.value.value
                if isinstance(inner, ast.Call) and _call_name(inner) == "_funding_exclusions":
                    ok_names |= {t.id for t in node.targets if isinstance(t, ast.Name)}
        for node in ast.walk(fn):
            if not isinstance(node, ast.Call) or _call_name(node) not in FUNDING:
                continue
            for kw in node.keywords:
                if kw.arg != "exclude":
                    continue
                name = getattr(kw.value, "id", None)
                if name not in ok_names:
                    bad.append((fn.name, node.lineno, ast.unparse(kw.value)))

    assert not bad, (
        "a funding call is excluding a set that did not come from "
        "_funding_exclusions, so it does not know about the core:\n"
        + "\n".join(f"  {f}() line {ln}: exclude={v}" for f, ln, v in bad))

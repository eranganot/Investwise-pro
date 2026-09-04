"""The risk report priced a shekel book in dollars.

`portfolio_risk` sizes every holding as `quantity x current_price` and stops
there. `current_price` is in the position's TRADING currency, so a US holding
contributes dollars to a total that is then labelled `nav` and used as shekels.
Every other consumer in the app converts: `/portfolio` computes
`quantity x price x fx_rate(price_currency(market, meta))`.

**Reconciled against production on 2026-09-04, commit `6f3cc4f`.** The book, as
`/api/v1/portfolio` reports it:

    TQQQ  34.3489 @   72.03 USD -> 7,470.20 ILS
    SOXL  23.0906 @  106.74 USD -> 7,441.64 ILS
    VTI    2.9716 @  380.93 USD -> 3,417.76 ILS
    BND   10.4040 @   71.93 USD -> 2,259.52 ILS
    CASH 618.6500 @    1.00 ILS ->   618.65 ILS
                                   ----------
                            NAV   21,207.78 ILS

Drop the rate and add the same rows up:

    2,474.15 + 2,464.69 + 1,131.87 + 748.36 + 618.65 = 7,437.72

`POST /api/v1/portfolio/risk` returned `"nav": 7437.82`. The ten agorot are the
displayed prices being rounded. **That is the arithmetic identity, not a
resemblance.**

Four consequences, and none of them announced themselves:

  * `var_95_1d_ils` is `var95 * nav`, so the shekel loss shown to the user is
    understated by the same factor: it read 480 against a real 6.45% of
    21,207.78, which is 1,368.
  * The Monte Carlo starts at `nav`, so `median_terminal`, `p10` and `p90` are
    all scaled by 0.35.
  * `prob_reach` compares that scaled distribution against the plan's REAL
    shekel target. It is the one number the error makes *pessimistic*, and it
    is the one the UI puts a percentage on.
  * **The weights are wrong for cash and only for cash.** Dropping the rate is
    a common factor across the four USD names, so their weights relative to one
    another survive it -- but CASH is already in shekels, so it is scaled up by
    the rate it never needed: 8.3% of the risk book against 2.9% of the real
    one. Cash has no volatility, so an over-weighted cash line drags portfolio
    volatility and beta DOWN. The report understated the risk it exists to
    measure.

Why it stayed silent: every figure it produces is internally consistent, and
`report["nav"]` is never displayed beside the portfolio NAV, so the one
cross-check that would have caught it in a glance is not on any screen.

The fix is not a formula, it is a call: `fx.position_value_ils` already exists
and its docstring already says why -- *"Five call sites computed
`quantity * current_price * fx_rate(...)` inline. The formula is not the hard
part; agreeing on it is. A money invariant that measures the book with a
different expression from the one the user is shown is not an invariant."*
This was the sixth call site and the one that disagreed.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

from types import SimpleNamespace as NS  # noqa: E402

DAYS = 260


def _series(start: float, n: int = DAYS, step: float = 0.001):
    """A deterministic price path -- moves enough to have a variance."""
    return [(f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}", start * (1 + step) ** i)
            for i in range(n)]


class _Row(NS):
    pass


def _book():
    """One US holding and one shekel cash line, in the production proportions."""
    return [
        _Row(ticker="VTI", market="NYSE", quantity=2.9716,
             current_price=380.93, cost_basis=300.0, meta={}),
        _Row(ticker="CASH", market="TASE", quantity=618.65,
             current_price=1.0, cost_basis=1.0, meta={"price_currency": "ILS"}),
    ]


async def _report(monkeypatch):
    import app.services.portfolio_risk_service as prs

    rows = _book()

    async def _list_positions(session, user):
        return rows

    def _history(ticker, days=252):
        return _series(100.0 if ticker != "CASH" else 1.0)

    async def _plan(session, user):
        return None

    monkeypatch.setattr(prs, "list_positions", _list_positions)
    monkeypatch.setattr(prs, "guarded_history", _history)
    monkeypatch.setattr(prs, "get_plan", _plan)
    monkeypatch.setattr(prs, "market_provider", lambda: NS(name="test"))
    return await prs.portfolio_risk(None, None)


def _expected_nav():
    from app.services.fx import fx_rate, price_currency
    total = 0.0
    for r in _book():
        ccy = price_currency(r.market, r.meta)
        total += float(r.quantity) * float(r.current_price) * (fx_rate(ccy) or 1.0)
    return total


async def test_the_risk_nav_agrees_with_the_portfolio_nav(monkeypatch):
    """One book, one NAV. Two answers means one of them is wrong.

    Sized the way `/portfolio` sizes it -- `quantity x price x rate` -- because
    that is the number the user is shown everywhere else, and a risk report
    denominated in something different is not a risk report of their portfolio.
    """
    from app.services.fx import fx_rate

    report = await _report(monkeypatch)
    assert report["ok"], report

    want = _expected_nav()
    got = report["nav"]
    assert abs(got - want) < 1.0, (
        f"the risk report values this book at {got:,.2f} and /portfolio values "
        f"it at {want:,.2f}. USD/ILS is {fx_rate('USD'):.4f}; the difference is "
        f"the rate, applied to every holding except the shekel cash line.\n"
        f"  var_95_1d_ils and every Monte Carlo terminal are scaled by "
        f"{got / want:.3f} as a result.")


async def test_cash_is_not_inflated_by_a_rate_it_does_not_need(monkeypatch):
    """The half of this that changes the risk numbers rather than the labels.

    Dropping the rate is a common factor across US holdings, so their weights
    relative to one another are unharmed. The shekel cash line is not: it gets
    scaled up by a rate it never needed, and because cash has no volatility, an
    over-weighted cash line pulls portfolio volatility and beta toward zero.
    """
    from app.services.fx import fx_rate, price_currency

    report = await _report(monkeypatch)
    rows = _book()

    nav = _expected_nav()
    cash = next(r for r in rows if r.ticker == "CASH")
    want = (float(cash.quantity) * float(cash.current_price)
            * (fx_rate(price_currency(cash.market, cash.meta)) or 1.0)) / nav

    # The report does not publish weights, so read them back off the NAV it used.
    unconverted = sum(float(r.quantity) * float(r.current_price) for r in rows)
    got = (float(cash.quantity) * float(cash.current_price)) / report["nav"]

    assert abs(got - want) < 0.005, (
        f"cash is {got:.1%} of the risk book and {want:.1%} of the real one. "
        f"Cash has no volatility, so over-weighting it understates the "
        f"portfolio volatility and beta this endpoint exists to report.\n"
        f"  unconverted total: {unconverted:,.2f}   report nav: "
        f"{report['nav']:,.2f}   real: {nav:,.2f}")

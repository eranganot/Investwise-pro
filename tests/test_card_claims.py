"""A card may not claim a portfolio change it does not produce.

Every test in this file works the same way: build the real Today card set for a
book, then SIMULATE each card's own ``apply`` spec and re-measure the portfolio.
The assertions are about the resulting weights, never about the wording.

That is the whole point. The August contradiction -- three cards proposing to
buy Equities to move Equities from 97% toward an 80% target, funded by selling
Equities -- shipped past a suite that had string-level assertions covering every
one of the four defects involved. `test_c3_funding.py:353` even guarded the exact
sentence, on a different consumer of the same code. Text can agree with itself
while the arithmetic disagrees with both.

The books below are adversarial on purpose. New card types get covered the day
they are written, because nothing here names a card: it walks whatever
``build_recommendations`` returns.
"""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.allocation_mix import OBJ_TARGET
from app.services.funding_service import MIN_TRADE_ILS

# Any card claiming a move must move it by at least this much. Below it, the
# claim is lot-granularity noise dressed up as progress.
CLAIMABLE = 0.005

# Wording that promises the named asset class moves toward its target.
MOVE_CLAIMS = ("closer to target", "toward your", "toward its", "lifts ", "lifting ")
SWAP_MARKERS = ("does not change your", "does not move your", "swaps which",
                "swap inside one asset class", "equity weight is unchanged")


def _pos(ticker, market, qty, price, basis, asset_class="Equities"):
    return {"ticker": ticker, "market": market, "asset_class": asset_class, "depth": 2,
            "spot_price": price, "listing_price": price, "quantity": qty,
            "cost_basis": basis, "expected_return_pct": 8, "volatility_pct": 18}


# --------------------------------------------------------------------------- #
# Adversarial books
# --------------------------------------------------------------------------- #
BOOKS = {
    # The live book, scaled: ~97% equities against an 80% Grow target, with the
    # leveraged pair the signals kept proposing to sell.
    "equities_overweight": {"objective": "Grow", "cash": 0.0,
                            # TQQQ and SOXL are catalog sleeve holdings on this
                            # book. Funding a card by selling them is exactly the
                            # cross-sleeve raid C3 forbids.
                            "sleeves": [("btm_trend_soxl", 10)], "positions": [
        _pos("TQQQ", "NASDAQ", 100, 100, 60),
        _pos("SOXL", "NASDAQ", 100, 100, 70),
        _pos("SCHD", "NASDAQ", 150, 100, 95),
        _pos("BND", "NASDAQ", 12, 100, 100, "Fixed Income"),
        # A small non-sleeve holding under the 25% Grow cap. Without one, every
        # equity on this book is over the cap, so a war-room signal takes the
        # trim branch and the buy path -- the one that shipped the bug -- is
        # never reached. Lands the book at 97% equities, as on the screenshots.
        _pos("QUAL", "NASDAQ", 5, 100, 98)]},

    # The plan genuinely wants more equities. A card MAY claim a move here.
    "equities_underweight": {"objective": "Grow", "cash": 0.0, "positions": [
        _pos("SCHD", "NASDAQ", 110, 100, 95),
        _pos("BND", "NASDAQ", 90, 100, 98, "Fixed Income")]},

    # Idle cash well above any floor.
    "cash_heavy": {"objective": "Balanced", "cash": 30000.0, "positions": [
        _pos("SCHD", "NASDAQ", 60, 100, 95),
        _pos("BND", "NASDAQ", 40, 100, 98, "Fixed Income")]},

    # One name far past any single-name cap.
    "single_name_breach": {"objective": "Balanced", "cash": 0.0, "positions": [
        _pos("HUGE", "NASDAQ", 800, 100, 60),
        _pos("BND", "NASDAQ", 100, 100, 98, "Fixed Income")]},

    # Small enough that most legs land under MIN_TRADE_ILS.
    "thin_book": {"objective": "Balanced", "cash": 40.0, "positions": [
        _pos("SCHD", "NASDAQ", 3, 30, 28),
        _pos("BND", "NASDAQ", 2, 30, 30, "Fixed Income")]},
}


def _seed(client, book):
    client.post("/api/v1/intake/portfolio",
                json={"entity_name": "Personal", "positions": book["positions"]})
    r = client.put("/api/v1/plan", json={"objective": book["objective"]})
    assert r.status_code < 400, f"could not set objective: {r.status_code} {r.text[:200]}"
    if book.get("cash"):
        client.post("/api/v1/portfolio/cash", json={"amount_ils": book["cash"]})
    for sid, pct in book.get("sleeves", []):
        client.post(f"/api/v1/strategies/{sid}/apply?sleeve_pct={pct}")


def _cards(client):
    return client.get("/api/v1/recommendations").json().get("recommendations", [])


# --------------------------------------------------------------------------- #
# The simulator. Every assertion in this file runs through it.
# --------------------------------------------------------------------------- #
def _class_values(book):
    """Value per asset class in ILS, computed from the book itself.

    Deliberately not read back from an endpoint: these assertions are about
    whether a card's own arithmetic is honest, and borrowing the app's mix
    calculation to check the app's mix claim would be marking its own homework.

    The FX rate IS borrowed, because it is not what is under test -- and because
    getting it wrong here is not a harmless approximation. The first draft of
    this file compared un-converted price*quantity against the cards' ILS
    amounts, a ~3.7x unit mismatch that manufactured a spectacular fake failure
    (drift 0.70 -> 1.27 on a card whose real effect was 0.70 -> 0.706).
    """
    from app.services.fx import fx_rate, price_currency
    out = {}
    for p in book["positions"]:
        rate = fx_rate(price_currency(p["market"], None))
        cls = p["asset_class"]
        out[cls] = out.get(cls, 0.0) + float(p["quantity"]) * float(p["spot_price"]) * rate
    return out


def _simulate(before: dict, card: dict) -> dict:
    """Apply a card's own funding legs to a class-value map.

    Uses ``apply``, not the card's prose: the spec is what Accept executes, so it
    is the only honest description of what the card does.
    """
    spec = card.get("apply") or {}
    after = dict(before)
    if spec.get("kind") == "buy_funded":
        cls = spec.get("asset_class") or "Equities"
        after[cls] = after.get(cls, 0.0) + float(spec.get("amount_ils") or 0.0)
        for s in spec.get("sells", []):
            scls = s.get("asset_class")
            if scls:
                after[scls] = after.get(scls, 0.0) - float(s.get("value_ils") or 0.0)
    elif spec.get("kind") == "trim":
        pass       # a trim's size lives outside the spec; covered by its own tests
    return after


def _weight(values: dict, cls: str) -> float:
    total = sum(values.values())
    return (values.get(cls, 0.0) / total) if total else 0.0


def _claimed_class(card: dict):
    """The asset class a card's text claims to move, if it claims one at all."""
    text = " ".join(str(card.get(k) or "") for k in ("action", "impact", "why"))
    low = text.lower()
    if any(m in low for m in SWAP_MARKERS):
        return None                      # it disclaims the move; nothing to check
    if not any(m in low for m in MOVE_CLAIMS):
        return None
    spec = card.get("apply") or {}
    if spec.get("kind") != "buy_funded":
        return None
    return spec.get("asset_class")


@pytest.fixture(params=sorted(BOOKS))
def book_cards(request):
    """(book name, book, client, cards) for every adversarial book."""
    name = request.param
    with TestClient(app) as c:
        _seed(c, BOOKS[name])
        yield name, BOOKS[name], c, _cards(c)


# --------------------------------------------------------------------------- #
# 1. The invariant
# --------------------------------------------------------------------------- #
def test_no_card_claims_a_move_it_does_not_make(book_cards):
    """D1, D2 and D4 at once.

    Live: "Add to AVUV -- 8,170 ... takes Equities from 97% toward your 80%
    target ... Moves Equities ~38% closer to target", funded by selling TQQQ,
    SCHD and SOXL. All equities. The 97% did not move by a point.
    """
    name, book, _client, cards = book_cards
    before = _class_values(book)
    for card in cards:
        cls = _claimed_class(card)
        if not cls:
            continue
        after = _simulate(before, card)
        moved = _weight(after, cls) - _weight(before, cls)
        target = OBJ_TARGET[book["objective"]].get(cls, 0.0)
        wanted = target - _weight(before, cls)
        assert abs(moved) >= CLAIMABLE, (
            f"[{name}] '{card['title']}' claims to move {cls} and moves it "
            f"{moved:+.2%}\n  {card.get('impact')}")
        assert moved * wanted > 0, (
            f"[{name}] '{card['title']}' moves {cls} {moved:+.2%}, the wrong way "
            f"(it is {_weight(before, cls):.0%} against a {target:.0%} target)\n"
            f"  {card.get('impact')}")


def test_a_within_class_purchase_says_so(book_cards):
    """D3. Selling a class to buy it back is a legitimate trade and a legitimate
    card -- but the user has to be told which of the two they are agreeing to."""
    name, _book, _client, cards = book_cards
    for card in cards:
        spec = card.get("apply") or {}
        if spec.get("kind") != "buy_funded":
            continue
        sold = {s.get("asset_class") for s in spec.get("sells", []) if s.get("asset_class")}
        if not sold or sold != {spec.get("asset_class")}:
            continue
        text = " ".join(str(card.get(k) or "") for k in ("action", "impact", "why", )).lower()
        assert any(m in text for m in SWAP_MARKERS), (
            f"[{name}] '{card['title']}' is funded entirely out of "
            f"{spec.get('asset_class')} and buys {spec.get('asset_class')}, and does "
            f"not say so.\n  action: {card.get('action')}\n  impact: {card.get('impact')}")


def test_no_card_funds_itself_by_selling_a_sleeve(book_cards):
    """D5. The plan holds sleeve tickers on purpose; one sleeve is not a piggy
    bank for another, and a Today card is not exempt from the rule C3 set."""
    name, book, _client, cards = book_cards
    from app.services.strategy_service import sleeve_targets
    names = set()
    for sid, _pct in book.get("sleeves", []):
        names |= {str(t).upper() for t in sleeve_targets(sid).keys()}
    if not names:
        pytest.skip("no sleeves on this book")
    for card in cards:
        for s in (card.get("apply") or {}).get("sells", []):
            assert str(s.get("ticker") or "").upper() not in names, (
                f"[{name}] '{card['title']}' funds itself by selling sleeve holding "
                f"{s.get('ticker')}")


def test_no_card_is_presented_short(book_cards):
    """D6. Funding filled a gross target and subtracted tax at the end, so a
    complete plan still announced 'That still leaves 430 short'. A card that
    cannot be paid for is not a recommendation; it should be absent, not short."""
    name, _book, _client, cards = book_cards
    for card in cards:
        fund = (card.get("meta") or {}).get("funding") or {}
        shortfall = float(fund.get("shortfall_ils") or 0.0)
        assert shortfall < MIN_TRADE_ILS, (
            f"[{name}] '{card['title']}' is rendered {shortfall:,.0f} short")
        assert "short." not in str(card.get("action") or ""), (
            f"[{name}] '{card['title']}' tells the user it is short:\n  {card.get('action')}")


def test_the_whole_card_set_can_be_accepted(book_cards):
    """The netting bug. Each card sized its funding against the same untouched
    snapshot, so three of them planned to sell the same 13 TQQQ and spend the
    same cash above the floor. Accepting two would oversell the book."""
    name, book, client, cards = book_cards
    held = {}
    for p in book["positions"]:
        held[p["ticker"].upper()] = held.get(p["ticker"].upper(), 0.0) + float(p["quantity"])
    cash_left = float(book.get("cash") or 0.0)

    for card in cards:
        spec = card.get("apply") or {}
        if spec.get("kind") != "buy_funded":
            continue
        cash_left -= float(spec.get("from_cash_ils") or 0.0)
        for s in spec.get("sells", []):
            tk = str(s.get("ticker") or "").upper()
            held[tk] = held.get(tk, 0.0) - float(s.get("shares") or 0.0)
            assert held[tk] >= -1e-9, (
                f"[{name}] the card set oversells {tk} by {-held[tk]:g} shares; "
                f"'{card['title']}' is the one that tips it negative")
    assert cash_left >= -0.01, f"[{name}] the card set overspends cash by {-cash_left:,.0f}"


def test_every_buy_card_moves_the_book_toward_the_plan_or_says_it_does_not(book_cards):
    """The general form, and the one that covers card types nobody has written
    yet: measure total absolute drift from the objective's target mix before and
    after. It goes down, or the card is labelled a swap."""
    name, book, _client, cards = book_cards
    before = _class_values(book)
    targets = OBJ_TARGET[book["objective"]]

    def drift(values):
        return sum(abs(_weight(values, cls) - tw) for cls, tw in targets.items())

    for card in cards:
        spec = card.get("apply") or {}
        if spec.get("kind") != "buy_funded":
            continue
        after = _simulate(before, card)
        text = " ".join(str(card.get(k) or "") for k in ("action", "impact", "why")).lower()
        is_swap = any(m in text for m in SWAP_MARKERS)
        if is_swap:
            continue
        assert drift(after) <= drift(before) + 1e-6, (
            f"[{name}] '{card['title']}' moves the book FURTHER from the "
            f"{book['objective']} target ({drift(before):.3f} -> {drift(after):.3f}) "
            f"and does not present itself as a swap\n  {card.get('impact')}")


# --------------------------------------------------------------------------- #
# 2. The narrow reproduction, kept for the story
# --------------------------------------------------------------------------- #
def test_the_august_three_card_contradiction_cannot_recur():
    """The exact shape seen on screen, asserted directly rather than by drift:
    no card may both buy Equities and claim Equities moves toward its target,
    on a book where Equities is already over it."""
    with TestClient(app) as c:
        _seed(c, BOOKS["equities_overweight"])
        cards = _cards(c)
        before = _class_values(BOOKS["equities_overweight"])
        eq = _weight(before, "Equities")
        assert eq > OBJ_TARGET["Grow"]["Equities"], (
            f"precondition: this book must be equity-OVERweight, it is {eq:.0%}")

        for card in cards:
            spec = card.get("apply") or {}
            if spec.get("kind") != "buy_funded" or spec.get("asset_class") != "Equities":
                continue
            text = " ".join(str(card.get(k) or "") for k in ("action", "impact", "why")).lower()
            assert "toward your 80% target" not in text, card.get("action")
            assert "closer to target" not in text, card.get("impact")
            assert any(m in text for m in SWAP_MARKERS), (
                "an equity buy on an equity-overweight book must present as a swap:\n"
                f"  {card.get('action')}\n  {card.get('impact')}")


def test_the_suite_would_have_caught_it():
    """A guard on the guard.

    These invariants are only worth having if they FAIL on the old behaviour, so
    this asserts the simulator actually rejects the card that shipped. If this
    ever passes a hand-built contradiction, the checks above are decorative.
    """
    before = {"Equities": 97000.0, "Fixed Income": 3000.0}
    shipped = {
        "title": "Add to AVUV - 8,170",
        "action": "Add 8,170 of AVUV. That takes Equities from 97% toward your 80% target.",
        "impact": "Moves Equities ~38% closer to target.",
        "why": "",
        "apply": {"kind": "buy_funded", "ticker": "AVUV", "asset_class": "Equities",
                  "amount_ils": 8170.0, "from_cash_ils": 0.0, "sells": [
                      {"ticker": "TQQQ", "shares": 13, "value_ils": 2990.0,
                       "asset_class": "Equities"},
                      {"ticker": "SCHD", "shares": 34, "value_ils": 3490.0,
                       "asset_class": "Equities"},
                      {"ticker": "SOXL", "shares": 3, "value_ils": 1300.0,
                       "asset_class": "Equities"}]}}

    assert _claimed_class(shipped) == "Equities", "the text does claim a move"
    after = _simulate(before, shipped)
    moved = _weight(after, "Equities") - _weight(before, "Equities")
    assert abs(moved) < CLAIMABLE, (
        f"the shipped card moved Equities {moved:+.2%}; if that is not ~0 the "
        f"simulator is wrong, not the card")


# --------------------------------------------------------------------------- #
# 3. The war-room path, driven end to end
#
# The reproduction above walks whatever cards the book happens to produce, and on
# a bare book that is the geo and commodities cards -- not the war-room ones that
# actually shipped the contradiction. The war room needs a grounded price
# observation before it will emit anything, so this injects one, the way
# test_recommendations.py does, and asserts on the card that comes out.
# --------------------------------------------------------------------------- #
@pytest.fixture
def observed(monkeypatch):
    """Force the war room to see one specific, grounded-looking observation."""
    from app.schemas.lag import LagObservation

    def _set(**kw):
        obs = LagObservation(**kw)
        monkeypatch.setattr("app.api.routes.war_room.signal_service.build_observations",
                            lambda *a, **k: [obs])
        monkeypatch.setattr("app.api.routes.war_room.signal_service.candidate_set",
                            lambda *a, **k: [{"ticker": obs.ticker, "market": obs.market.value}])
        return obs
    return _set


def _warroom_buys(cards):
    return [r for r in cards
            if (r.get("meta") or {}).get("source") == "war_room"
            and (r.get("apply") or {}).get("kind") == "buy_funded"]


def test_a_war_room_buy_on_an_overweight_class_presents_as_a_swap(observed):
    """The card from the screenshots, rebuilt.

    An approved signal on QUAL, on a book that is ~97% equities against an 80%
    Grow target. The card that shipped said "takes Equities from 97% toward your
    80% target" and "Moves Equities ~22% closer to target", funded by selling
    TQQQ and SOXL -- both equities. It may still be a card, because shedding 3x
    leveraged exposure for a dividend ETF is a real decision. It may not still
    claim to move the weight.
    """
    from app.schemas.state_machine import ActionType, Market
    observed(ticker="QUAL", market=Market.NASDAQ, depth=3, spot_price=100,
             listing_price=104.8, action_type=ActionType.BUY,
             expected_return_pct=9, volatility_pct=12)   # 16% trips the Medium risk cap

    with TestClient(app) as c:
        _seed(c, BOOKS["equities_overweight"])
        c.put("/api/v1/plan", json={"risk_tolerance": "Medium"})
        cards = _cards(c)
        buys = _warroom_buys(cards)
        if not buys:
            pytest.skip("war room approved nothing on this book")

        before = _class_values(BOOKS["equities_overweight"])
        for card in buys:
            text = " ".join(str(card.get(k) or "") for k in ("action", "impact", "why")).lower()
            after = _simulate(before, card)
            moved = _weight(after, "Equities") - _weight(before, "Equities")

            assert "closer to target" not in text, card.get("impact")
            assert "toward your 80% target" not in text, card.get("action")
            if abs(moved) < CLAIMABLE:
                assert any(m in text for m in SWAP_MARKERS), (
                    f"'{card['title']}' moves Equities {moved:+.2%} and does not say so:\n"
                    f"  action: {card.get('action')}\n  impact: {card.get('impact')}")
            assert (card.get("meta") or {}).get("kind") in ("within_class_swap", "toward_target")


def test_a_war_room_buy_never_sells_a_sleeve_holding(observed):
    """D5, on the path that was doing it: the cards proposed selling TQQQ and
    SOXL -- both catalog sleeve strategies -- to buy QUAL and AVUV, which are
    Factor Stack members. C3 forbids exactly this for sleeve funding."""
    from app.schemas.state_machine import ActionType, Market
    from app.services.strategy_service import sleeve_targets
    observed(ticker="QUAL", market=Market.NASDAQ, depth=3, spot_price=100,
             listing_price=104.8, action_type=ActionType.BUY,
             expected_return_pct=9, volatility_pct=12)   # 16% trips the Medium risk cap

    with TestClient(app) as c:
        _seed(c, BOOKS["equities_overweight"])
        c.put("/api/v1/plan", json={"risk_tolerance": "Medium"})
        buys = _warroom_buys(_cards(c))
        if not buys:
            pytest.skip("war room approved nothing on this book")
        protected = {t.upper() for sid, _ in BOOKS["equities_overweight"]["sleeves"]
                     for t in sleeve_targets(sid)}
        for card in buys:
            sold = {str(s.get("ticker") or "").upper()
                    for s in (card.get("apply") or {}).get("sells", [])}
            assert not (sold & protected), (
                f"'{card['title']}' funds itself by selling sleeve holding(s) "
                f"{sold & protected}")


# --------------------------------------------------------------------------- #
# 4. One inventory, and no card deleted for wanting the wrong shares
#
# The first attempt at cross-card netting let every card plan against the
# untouched book and then dropped the ones whose legs had been claimed. On the
# live book that silently removed the war-room card AND the commodities card
# because a geo card had taken 2 MSFT first -- while 7,863 of V sat untouched
# and would have funded both. Replacing three dishonest cards with no cards is
# not a fix.
# --------------------------------------------------------------------------- #
def test_a_card_whose_funding_is_taken_is_re_sourced_not_dropped(observed):
    from app.schemas.state_machine import ActionType, Market
    observed(ticker="QUAL", market=Market.NASDAQ, depth=3, spot_price=100,
             listing_price=104.8, action_type=ActionType.BUY,
             expected_return_pct=9, volatility_pct=12)
    with TestClient(app) as c:
        _seed(c, BOOKS["equities_overweight"])
        c.put("/api/v1/plan", json={"risk_tolerance": "Medium"})
        body = c.get("/api/v1/recommendations").json()
        funded = [r for r in body.get("recommendations", [])
                  if (r.get("apply") or {}).get("kind") == "buy_funded"]
        if len(funded) < 2:
            pytest.skip("this book produced fewer than two funded cards")
        assert "funding" not in (body.get("degraded") or []), (
            "a card was dropped for want of funding; it should have been "
            "re-sourced from what the earlier cards left")
        # And the legs really are disjoint -- one inventory, spent once.
        seen = {}
        for card in funded:
            for s in (card.get("apply") or {}).get("sells", []):
                tk = str(s.get("ticker") or "").upper()
                seen[tk] = seen.get(tk, 0.0) + float(s.get("shares") or 0.0)
        held = {p["ticker"].upper(): float(p["quantity"])
                for p in BOOKS["equities_overweight"]["positions"]}
        for tk, used in seen.items():
            assert used <= held.get(tk, 0.0) + 1e-9, (
                f"the card set sells {used:g} {tk} out of {held.get(tk, 0.0):g} held")

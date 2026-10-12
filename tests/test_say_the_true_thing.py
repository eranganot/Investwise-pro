"""PR 116 - say the true thing.

- L6: no copy names a tab that does not exist ("Cash tab", "Goal settings", "Rules tab").
- L4: the soft cash TARGET is not called a floor where its value is shown.
- L14 / D80: with a core that holds commodities, commodity buys use the core's funds.
- D81: no Sentry.
- L9: two `money()` helpers no longer share a name.
"""
import ast
import pathlib
from types import SimpleNamespace

from app.services import thresholds

GONE = ("Cash tab", "Goal settings", "Settings tab", "Rules tab")


def _user_strings(path: pathlib.Path) -> str:
    """Every string literal in a module except docstrings (developer notes)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docs = {id(n.body[0].value) for n in ast.walk(tree)
            if isinstance(n, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))
            and n.body and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
    return chr(10).join(n.value for n in ast.walk(tree)
                     if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs)


def test_no_copy_names_a_tab_that_does_not_exist():
    """Tabs are Today, Holdings, Plan, Markets, Agents, Explore. Cash lives in
    Holdings › Set liquid cash and the rules in Holdings › Rules."""
    bad = [f"{p}: {name}" for p in pathlib.Path("app").rglob("*.py")
           for name in GONE if name in _user_strings(p)]
    html = pathlib.Path("app/static_app/index.html").read_text(encoding="utf-8")
    for i, line in enumerate(html.splitlines(), 1):
        if line.strip().startswith("//"):
            continue
        bad += [f"index.html:{i}: {name}" for name in GONE if name in line]
    assert not bad, bad


def test_the_soft_target_is_not_called_a_floor():
    """The cash reserve is a soft TARGET (D-2026-09-06); the hard minimum is the floor."""
    src = chr(10).join(_user_strings(p) for p in pathlib.Path("app/services").glob("*.py"))
    html = pathlib.Path("app/static_app/index.html").read_text(encoding="utf-8")
    for phrase in ("cash floor intact", "liquidity floor", "(floor of ", "cash above your floor",
                   "spendable cash above your floor", "floor for a {objective}", "{objective} floor"):
        assert phrase not in src, phrase
    assert "floor intact" not in html and "Cash between your floor" not in html


def test_the_funding_payload_names_the_target_and_keeps_the_old_keys():
    from app.services import funding_service as fs
    plan = SimpleNamespace(objective="Grow", cash_target_ils=None, cash_target_pct=7.5)
    assert fs.cash_target_fraction("Grow", plan) == 0.075
    assert fs.cash_target_amount_ils(10_000, "Grow", plan) == 750.0
    assert fs.cash_floor_pct is fs.cash_target_fraction and fs.cash_floor_ils is fs.cash_target_amount_ils
    src = pathlib.Path(fs.__file__).read_text(encoding="utf-8")
    assert '"cash_target_ils": round(cash_target_amount_ils(' in src
    assert '"cash_floor_ils": round(cash_target_amount_ils(' in src         # deprecated alias, same value
    from app.services import headsup
    assert headsup.MIN_CARD_TRADE_ILS == 100.0 and headsup.MIN_TRADE_ILS == headsup.MIN_CARD_TRADE_ILS


class _Pick:
    def __init__(self, tk, name=""):
        self.ticker, self.name = tk, name


class _Pos:
    ticker, market, quantity, current_price, cost_basis = "V", "NASDAQ", 10, 400.0, 340.0
    meta = {"asset_class": "Equities"}


def _agent(monkeypatch, order):
    class _Agent:
        def screen_commodities(self, top_n=3):
            return [_Pick(t) for t in order][:top_n]
    monkeypatch.setattr("app.agents.screener_agent.OpportunityAgent", lambda: _Agent())


def test_commodity_card_buys_the_core_basket_when_a_core_is_chosen(monkeypatch):
    from app.services import recommendations as rec
    _agent(monkeypatch, ["CANE", "USO", "GLD", "DBA", "DBC", "IAU"])        # sugar ranks first
    out = rec._commodity_recs([_Pos()], {}, "Balanced", cash_ils=5000.0, core_id="bal_all_weather")
    assert len(out) == 1 and "DBC" in out[0]["title"], out[0]["title"]     # the core's, by the screen's order
    assert "CANE" not in out[0]["action"] and "USO" not in out[0]["action"]
    assert rec.core_commodity_tickers("bal_all_weather") == ["IAU", "DBC"]


def test_without_a_core_or_with_the_setting_off_the_screen_leader_is_bought(monkeypatch):
    from app.services import recommendations as rec
    _agent(monkeypatch, ["CANE", "DBC", "IAU"])
    assert "CANE" in rec._commodity_recs([_Pos()], {}, "Balanced", cash_ils=5000.0)[0]["title"]
    assert rec.core_commodity_tickers("grow_quality") == []                # a core with no commodities
    try:
        thresholds.use(SimpleNamespace(thresholds={"commodity_buys_core_only": 0}))
        out = rec._commodity_recs([_Pos()], {}, "Balanced", cash_ils=5000.0, core_id="bal_all_weather")
        assert "CANE" in out[0]["title"]
    finally:
        thresholds._OVERRIDES.set(None)
    assert thresholds.defaults()["commodity_buys_core_only"] == 1          # D80: on by default


def test_redeploy_research_only_swaps_within_the_core_commodities():
    from app.services import recommendations as rec
    scores = {"CANE": {"screen": "Commodities"}, "IAU": {"screen": "Commodities"},
              "QQQ": {"screen": "Equities"}}
    assert set(rec._core_commodities_only(scores, "bal_all_weather")) == {"IAU", "QQQ"}
    assert rec._core_commodities_only(scores, None) == scores


def test_there_is_no_sentry():
    from app.core.config import get_settings
    assert not hasattr(get_settings(), "sentry_dsn")
    assert "sentry_sdk" not in pathlib.Path("app/main.py").read_text(encoding="utf-8")


def test_the_two_money_helpers_have_their_own_names():
    """Display (2 dp float) and storage (4 dp Decimal) shared the name `money`."""
    from app.core.money import display_money
    from app.models.tables import db_money
    assert display_money(1.005) == 1.01 and str(db_money(1.00005)) in ("1.0000", "1.0001")
    for p in pathlib.Path("app").rglob("*.py"):
        for node in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.module in ("app.core.money", "app.models.tables"):
                assert "money" not in {a.name for a in node.names}, f"{p} imports the ambiguous `money`"

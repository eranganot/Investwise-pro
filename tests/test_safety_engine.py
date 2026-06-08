"""Safety Layer tests (Section 8)."""
from app.engines.safety_engine import SafetyEngine


def test_ok_within_limits():
    r = SafetyEngine().check(holdings={"A": 0.1}, liquidity_ratio=0.5,
                             proposals=[{"ticker": "B", "action": "Buy", "weight_delta": 0.05, "risk_score": 90}])
    assert r.verdict == "ok"
    assert r.flags == []


def test_concentration_block_on_buy():
    r = SafetyEngine().check(holdings={"TEVA": 0.23}, liquidity_ratio=0.5,
                             proposals=[{"ticker": "TEVA", "action": "Buy", "weight_delta": 0.05, "risk_score": 90}])
    assert r.verdict == "block"
    assert any(f.type == "concentration" and f.severity == "high" for f in r.flags)


def test_existing_concentration_is_medium_warn():
    r = SafetyEngine().check(holdings={"BIG": 0.40}, liquidity_ratio=0.5, proposals=[])
    assert r.verdict == "warn"
    assert any(f.type == "concentration" and f.severity == "medium" for f in r.flags)


def test_liquidity_failure_blocks():
    r = SafetyEngine().check(holdings={}, liquidity_ratio=0.02, proposals=[])
    assert r.verdict == "block"
    assert any(f.type == "liquidity" for f in r.flags)


def test_irrational_low_risk_score():
    r = SafetyEngine().check(holdings={}, liquidity_ratio=0.5,
                             proposals=[{"ticker": "Z", "action": "Buy", "weight_delta": 0.01, "risk_score": 20}])
    assert any(f.type == "irrational" for f in r.flags)

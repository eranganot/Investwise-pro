"""PR 156 (D140) -- every new recommendation card pushes.

Owner, 2026-11-02: "make sure that I'm getting notification every time that a
new recommendation card appears", and, asked what "new" means, chose literally
every card: a card whose id was not pushed before is pushed, at any severity.

Before this PR the push loop dropped, silently:
  * any ordinary card below CRITICAL/HIGH (`push_notify_severities`);
  * every "ready to arm" card (P4.2's never-live trigger, digest only);
  * a drift / cold-start card whose id changed inside 24 h, and a re-fired rule
    inside 12 h (P4.2's per-trigger signatures ignore the id);
  * every new card past the fifth in one run (`max_sends`).
The tests below drive `evaluate_and_notify` itself, with the sender, the dedupe
ledger and the card builder replaced, so they measure what reaches the phone.
"""
import types

import pytest

from app.services import push_service as ps


def _card(cid, title, severity="MEDIUM", **kw):
    return {"id": cid, "title": title, "severity": severity, "action": "Open it", **kw}


class _Session:                     # a subscriber exists; nothing else is read
    async def scalar(self, *a, **k):
        return object()

    async def commit(self):
        pass


async def _none():
    return []


@pytest.fixture
def harness(monkeypatch):
    state = {"cards": [], "sent": [], "marks": set(),
             "kv": {ps._baseline_key("e@x"): "2026-11-02"}}

    async def fake_build(session, user):
        return {"recommendations": list(state["cards"]), "headsups": []}

    async def send(session, subject, title, body, url=None, tag=None, category=None):
        state["sent"].append({"title": title, "body": body, "tag": tag})
        return 1

    async def seen(session, subject, sig):
        return sig in state["marks"]

    async def seen_within(session, subject, sig, hours):
        return sig in state["marks"]

    async def mark(session, subject, sig):
        state["marks"].add(sig)

    async def kv_get(session, key):
        return state["kv"].get(key)

    async def kv_set(session, key, value):
        state["kv"][key] = value

    import app.services.portfolio_analytics as pa
    import app.services.recommendations as R
    monkeypatch.setattr(R, "build_recommendations", fake_build)
    monkeypatch.setattr(ps, "send_to_subject", send)
    monkeypatch.setattr(ps, "_seen", seen)
    monkeypatch.setattr(ps, "_seen_within", seen_within)
    monkeypatch.setattr(ps, "_mark", mark)
    monkeypatch.setattr(ps, "_kv_get", kv_get)
    monkeypatch.setattr(ps, "_kv_set", kv_set)
    monkeypatch.setattr(pa, "load_positions", lambda *a, **k: _none())

    async def run():
        before = len(state["sent"])
        await ps.evaluate_and_notify(_Session(), types.SimpleNamespace(email="e@x"))
        return state["sent"][before:]

    state["run"] = run
    return state


@pytest.mark.asyncio
@pytest.mark.parametrize("severity", ["LOW", "MEDIUM", "HIGH", "CRITICAL"])
async def test_a_new_card_pushes_at_any_severity(harness, severity):
    harness["cards"] = [_card("rec_trim_teva", "Trim TEVA", severity=severity)]
    out = await harness["run"]()
    assert [s["title"] for s in out] == ["\U0001f4a1 Trim TEVA"], out


@pytest.mark.asyncio
async def test_a_ready_to_arm_card_pushes(harness):
    harness["cards"] = [_card("rec_arm3", "3 protective rules ready to arm")]
    out = await harness["run"]()
    assert len(out) == 1 and "ready to arm" in out[0]["title"], out


@pytest.mark.asyncio
async def test_the_same_card_pushes_once(harness):
    harness["cards"] = [_card("rec_trim_teva", "Trim TEVA")]
    assert len(await harness["run"]()) == 1
    assert await harness["run"]() == []


@pytest.mark.asyncio
async def test_a_card_whose_id_changed_is_a_new_card(harness):
    """The owner's choice, knowingly: a drift card re-rendered with a new amount
    gets a new id, and that is a new card."""
    harness["cards"] = [_card("rec_111", "SOXL has drifted to 4% of a 10% sleeve", meta={"ticker": "SOXL"})]
    assert len(await harness["run"]()) == 1
    harness["cards"] = [_card("rec_222", "SOXL has drifted to 5% of a 10% sleeve", meta={"ticker": "SOXL"})]
    assert len(await harness["run"]()) == 1


@pytest.mark.asyncio
async def test_a_rule_that_fires_again_with_a_new_card_pushes_again(harness):
    harness["cards"] = [_card("rule_aaaa_1", "SOXL hit your stop", rule_id="aaaa")]
    assert len(await harness["run"]()) == 1
    harness["cards"] = [_card("rule_aaaa_2", "SOXL hit your stop again", rule_id="aaaa")]
    assert len(await harness["run"]()) == 1


@pytest.mark.asyncio
async def test_no_new_card_is_dropped_when_many_arrive_at_once(harness):
    harness["cards"] = [_card(f"rec_{i}", f"Card {i}") for i in range(8)]
    out = await harness["run"]()
    singles = [s for s in out if s["title"].startswith("\U0001f4a1 Card")]
    summary = [s for s in out if "more new card" in s["title"]]
    assert len(singles) == ps.MAX_CARD_PUSHES_PER_RUN, out
    assert len(summary) == 1, out
    for i in range(ps.MAX_CARD_PUSHES_PER_RUN, 8):
        assert f"Card {i}" in summary[0]["body"], summary
    # every one of the eight is marked, so none is pushed again next run
    assert await harness["run"]() == []


@pytest.mark.asyncio
async def test_a_card_seen_in_an_earlier_run_is_not_in_the_summary(harness):
    harness["cards"] = [_card("rec_old", "Old card")]
    await harness["run"]()
    harness["cards"] = [_card("rec_old", "Old card")] + [_card(f"rec_{i}", f"Card {i}") for i in range(7)]
    out = await harness["run"]()
    assert not any("Old card" in s["title"] or "Old card" in s["body"] for s in out), out
    assert sum(1 for s in out if s["title"].startswith("\U0001f4a1 Card")) == ps.MAX_CARD_PUSHES_PER_RUN


@pytest.mark.asyncio
async def test_the_first_run_after_the_switch_pushes_no_old_card(harness):
    """What is already on Today when this ships is not new. One line says the
    rule changed; the cards already there are marked, not pushed."""
    harness["kv"].clear()
    harness["cards"] = [_card(f"rec_{i}", f"Card {i}") for i in range(4)]
    out = await harness["run"]()
    assert len(out) == 1 and "every new card" in out[0]["title"], out
    assert "4 card(s)" in out[0]["body"]
    assert await harness["run"]() == []
    harness["cards"].append(_card("rec_new", "A new one"))
    out = await harness["run"]()
    assert [s["title"] for s in out] == ["\U0001f4a1 A new one"], out


@pytest.mark.asyncio
async def test_card_pushes_do_not_use_up_the_app_health_budget(harness, monkeypatch):
    """Eight new cards must not crowd out PR 144's broken-job push."""
    import app.services.recommendations as R
    harness["cards"] = [_card(f"rec_{i}", f"Card {i}") for i in range(8)]

    async def fake_build(session, user):
        return {"recommendations": list(harness["cards"]), "headsups": [
            {"card": {"id": "rec_jh", "title": "1 background job is not working",
                      "meta": {"headsup_kind": "job_health"}}}]}
    monkeypatch.setattr(R, "build_recommendations", fake_build)
    out = await harness["run"]()
    assert any("background job" in s["title"] for s in out), out


def test_a_rule_push_marks_its_card_so_the_card_is_not_pushed_twice():
    """rules_service pushes a firing the moment it is found; its card
    (`rule_<id[:8]>`) is marked with the same key the card loop checks."""
    import inspect

    from app.services import rules_service as rs
    src = inspect.getsource(rs.evaluate_user)
    push = src[src.index("if notify and newly:"):]
    assert 'card_signature({"id": f"rule_{n[\'id\'][:8]}"})' in push
    assert ps.card_signature({"id": "rule_abcd1234"}) == "rec:rule_abcd1234"


def test_the_check_runs_every_30_minutes():
    import inspect

    from app.worker import scheduler as sch
    src = inspect.getsource(sch)
    i = src.index('_guarded("push_evaluate"')
    assert "minutes=30" in src[i:i + 300], src[i:i + 300]

"""Agent War Room tests."""
from app.services.demo_data import DEFAULT_OBSERVATIONS
from app.services.war_room import build_war_room


def test_war_room_produces_agent_transcripts():
    wr = build_war_room(DEFAULT_OBSERVATIONS)
    assert wr["count"] >= 2
    assert "Research" in wr["agents"] and "Adversary" in wr["agents"]
    teva = [s for s in wr["sessions"] if s["ticker"] == "TEVA"][0]
    agents = [line["agent"] for line in teva["transcript"]]
    assert agents[:3] == ["Research", "Alpha", "Risk"]
    assert teva["outcome"] == "DISPLAYED"


def test_war_room_shows_veto_path():
    wr = build_war_room(DEFAULT_OBSERVATIONS)
    hype = [s for s in wr["sessions"] if s["ticker"] == "HYPE"][0]
    assert hype["outcome"] == "VETOED"
    assert any("veto" in line["says"].lower() for line in hype["transcript"])

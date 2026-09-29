"""PR 77: a sleeve holding's own strategy rule decides; the review only reports it."""
from app.services.recommendations import _holding_review_recs
from tests._review_fixtures import down_both, measurement, row


def test_a_sleeve_holding_gets_no_sell_card_even_with_an_edge():
    rows = [row("SOXL", 10.0, 150.0)]
    note = "Sleeve holding: its own rule decides. The btm_trend_soxl rule still holds SOXL."
    review, cards = _holding_review_recs(rows, {"nav": 10_000}, {"SOXL": down_both()},
                                         {"SOXL": measurement()}, {"SOXL": note},
                                         cash_ils=0.0, trigger_ils=None)
    assert cards == []
    assert review[0]["state"] == "Exit" and review[0]["note"] == note

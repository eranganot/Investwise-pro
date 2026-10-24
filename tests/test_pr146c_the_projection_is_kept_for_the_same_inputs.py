"""PR 146c. 146b's smoke FAILED on production (b951765): goal_projection still 250 ms. Root cause:
146b assumed the goal story owned the goal there; it did not -- the verdict was solved for the
TQQQ vol-target sleeve, which the owner swapped for DBMF later that day, so the verdict was not
current and the behind-your-goal card (and its projection) was on Today. The projection is the
cost; it is now kept for exactly the same inputs, so warm reads skip it.
"""
import time

from app.services import recommendations as rr

KW = dict(nav=25_000.0, rate=15.6, vol=43.0, years=4.7, target=60_000.0, standing_monthly=0.0,
          cons_rate=7.0, cons_vol=15.0)


def test_the_same_inputs_return_the_same_numbers_without_a_second_search():
    rr._LEVERS_CACHE.clear()
    t0 = time.perf_counter()
    a = rr._goal_levers(**KW)
    first = time.perf_counter() - t0
    t1 = time.perf_counter()
    b = rr._goal_levers(**KW)
    second = time.perf_counter() - t1
    assert a == b == rr._goal_levers_uncached(**KW), "no rounding: the card and /plan quote one median"
    assert second < first / 10, (first, second)


def test_any_input_change_is_a_fresh_search_and_callers_cannot_edit_the_kept_answer():
    rr._LEVERS_CACHE.clear()
    a = rr._goal_levers(**KW)
    a["projected"] = -1.0
    assert rr._goal_levers(**KW)["projected"] != -1.0
    moved = rr._goal_levers(**{**KW, "nav": 25_001.0})
    assert moved == rr._goal_levers_uncached(**{**KW, "nav": 25_001.0})
    for i in range(rr._LEVERS_CACHE_MAX + 5):
        rr._goal_levers(**{**KW, "nav": 30_000.0 + i})
    assert len(rr._LEVERS_CACHE) <= rr._LEVERS_CACHE_MAX

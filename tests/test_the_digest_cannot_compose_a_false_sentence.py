"""PR 35 -- the 07:00 push made four claims and three of them were false.

Sent to Eran's phone on 2026-09-01:

    "Hello! Your account is currently valued at 21,474.35 ILS, with a total
     return of 17.76%. You have an 89.2% probability of reaching your 102,973
     goal in 5 years. To diversify your holdings, consider buying 2,147 ILS of
     PDBC (Broad commodities) to reach your 10% target in this asset class."

Measured against the app the same morning:

  21,474.35   real -- the NAV snapshot for 2026-08-31
  17.76%      real, and NOT a return on that NAV. It is a BACKFILL of the
              holdings held today priced through 252 sessions of real closes.
              The account's return on the 20,000 contributed was +3.89%.
  89.2%       not in the context at all. /plan/projection says 22.7%.
  102,973     not in the context. The plan target is 100,288.77.
  5 years     not in the context. The horizon is 6.
  2,147 ILS   exactly 10% of NAV, computed by the model
  10%         from OBJ_TARGET["Grow"], which is not in the context either

The first two are the interesting failure. Both numbers were TRUE and the
sentence was false, because they measure different things and nothing in the
payload said so. The prompt's "never invent numbers" did not forbid it and was
not violated.

So the fix is not a better instruction, it is two mechanisms:

  * every quotable figure travels as {value, means}, so a BACKFILL says it is a
    backfill in the same object as the number;
  * every number in the generated text must appear in the context, or the whole
    digest is discarded and the deterministic one is sent instead.

The second is what catches an 89.2% from nowhere and a 10%-of-NAV worked out on
the spot -- by arithmetic, rather than by hoping the instruction held.

Not financial advice.
"""
import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

import pytest  # noqa: E402


def _unsupported(text, ctx):
    """Imported lazily so this file still COLLECTS against the previous commit."""
    from app.services.digest_service import unsupported_numbers
    return unsupported_numbers(text, ctx)


# The context as it is actually assembled, with the two figures that were
# welded together carrying their bases.
CTX = {
    "nav_ils": {"value": 21474.35, "means": "what your holdings are worth right now"},
    "performance": {
        "total_return_pct": {"value": 17.03, "means": "a BACKFILL of the holdings you hold TODAY"},
        "benchmark": "SPY",
        "excess_return_pct": {"value": -1.95, "means": "the backfill minus the benchmark"},
    },
    "risk": {"annualized_volatility_pct": 49.13, "beta": 0.94,
             "var_95_1d_pct": -5.02, "var_confidence_pct": 95},
    "goal": {"target_ils": {"value": 100288.77, "means": "the target you set in Plan"}},
    "projection": {"probability_meets_target": 0.227, "target_amount": 100288.77, "years": 6},
    "recommendations": [{"title": "Trim: SOXL", "action": "SOXL screens poorly (20/100)"}],
}

# Verbatim.
THE_REAL_PUSH = (
    "Hello! Your account is currently valued at 21,474.35 ILS, with a total return of 17.76%. "
    "You have an 89.2% probability of reaching your 102,973 goal in 5 years. "
    "To diversify your holdings, consider buying 2,147 ILS of PDBC (Broad commodities) "
    "to reach your 10% target in this asset class."
)


# --------------------------------------------------------------------------- #
# the message itself
# --------------------------------------------------------------------------- #
def test_the_notification_that_started_this_is_rejected():
    """Every figure that was not in the context, named."""
    bad = set(_unsupported(THE_REAL_PUSH, CTX))
    for n in (17.76, 89.2, 102973.0, 5.0, 2147.0, 10.0):
        assert n in bad, f"{n} was quoted and is not in the context, and the check missed it"


def test_the_true_half_of_that_sentence_is_still_accepted():
    """21,474.35 was real and must not be flagged. The check is not a blanket no."""
    assert 21474.35 not in _unsupported(THE_REAL_PUSH, CTX)


def test_a_correctly_grounded_digest_passes_clean():
    """The point is a digest that CAN be written, not one that always falls back."""
    good = (
        "Your holdings are worth about 21,474 ILS. A backfill of the holdings you hold today "
        "returned 17.03% over the window, -1.95% against SPY - that is what these holdings "
        "would have done, not what your account did. The chance of reaching 100,289 ILS in "
        "6 years is 23%. Volatility is running at 49.13%. Not financial advice."
    )
    assert _unsupported(good, CTX) == []


# --------------------------------------------------------------------------- #
# the matching rule
# --------------------------------------------------------------------------- #
def test_rounding_is_allowed_and_a_different_number_is_not():
    """"21,474" for 21474.35 is the same figure. 17.76 for 17.03 is not.

    This is the whole distinction. A tolerance wide enough to accept the first
    would accept the second -- they differ by 4% -- so the rule is rounding to
    the decimals the text used, never a percentage band.
    """
    assert _unsupported("21,474 ILS", CTX) == []
    assert _unsupported("21,474.35 ILS", CTX) == []
    assert _unsupported("17.03%", CTX) == []
    assert _unsupported("17.76%", CTX) == [17.76]
    assert _unsupported("17%", CTX) == []          # 17.03 rounds to 17
    assert _unsupported("18%", CTX) == [18.0]


def test_a_fraction_may_be_quoted_as_a_percentage():
    """The context stores 0.227; a reader is owed "23%", not "0.227"."""
    assert _unsupported("a 23% chance", CTX) == []
    assert _unsupported("a 22.7% chance", CTX) == []
    assert _unsupported("a 89.2% chance", CTX) == [89.2]


def test_numbers_inside_a_string_the_model_was_given_are_quotable():
    """Repeating a card's own wording back is honest, not an invention."""
    assert _unsupported("SOXL screens poorly (20/100)", CTX) == []


def test_smallness_is_not_a_licence():
    """"5 years" was one of the false claims, and 5 is a small number."""
    assert _unsupported("in 5 years", CTX) == [5.0]
    assert _unsupported("in 6 years", CTX) == []


def test_a_digest_with_no_numbers_at_all_is_fine():
    assert _unsupported("Nothing needs your attention. Not financial advice.", CTX) == []
    assert _unsupported("", CTX) == []


# --------------------------------------------------------------------------- #
# what build() does with a failure
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_an_unverifiable_digest_is_discarded_not_patched(monkeypatch):
    """The whole text goes, and the deterministic one is sent instead.

    Not patched, because the sentence around an untraceable number was built on
    that number -- there is nothing left to keep.
    """
    from app.services import digest_service as D

    monkeypatch.setattr(D, "gemini_enabled", lambda: True)
    monkeypatch.setattr(D, "gemini_generate", lambda *a, **k: THE_REAL_PUSH)

    async def _ctx(_s, _u):
        return dict(CTX)

    monkeypatch.setattr(D, "gather", _ctx)

    out = await D.build(None, None)
    assert out["verified"] is False
    assert out["llm"] is False, "an unverified text must not be shipped as the digest"
    assert THE_REAL_PUSH not in out["digest"]
    assert "89.2" not in out["digest"]
    assert set(out["unsupported_numbers"]) >= {17.76, 89.2, 102973.0}
    # the fallback that replaces it is itself grounded
    assert _unsupported(out["digest"], CTX) == []


@pytest.mark.asyncio
async def test_a_verified_digest_is_sent_as_written(monkeypatch):
    """A model that stays inside the context keeps its prose."""
    from app.services import digest_service as D

    good = ("Your holdings are worth about 21,474 ILS. Volatility is 49.13%. "
            "Not financial advice.")
    monkeypatch.setattr(D, "gemini_enabled", lambda: True)
    monkeypatch.setattr(D, "gemini_generate", lambda *a, **k: good)

    async def _ctx(_s, _u):
        return dict(CTX)

    monkeypatch.setattr(D, "gather", _ctx)

    out = await D.build(None, None)
    assert out["verified"] is True
    assert out["llm"] is True
    assert out["digest"] == good


# --------------------------------------------------------------------------- #
# the fallback names its bases
# --------------------------------------------------------------------------- #
def test_the_fallback_calls_the_backfill_a_backfill():
    """The fallback is what ships on every failure, so it carries the same duty.

    The old one read "Performance: 17.03% total", directly under the net-worth
    line -- the same two figures in the same order that produced the false
    sentence, only without a model in between.
    """
    from app.services.digest_service import _fallback

    text = _fallback(dict(CTX))
    assert "ackfill" in text, f"the fallback does not say what 17.03% is: {text}"
    assert "not what your account did" in text
    assert "Not financial advice." in text


def test_the_fallback_quotes_one_goal_probability_and_it_is_the_plan_one():
    from app.services.digest_service import _fallback

    text = _fallback(dict(CTX))
    assert "23%" in text
    assert "89.2" not in text, "the invented probability is back"


def test_the_risk_block_no_longer_carries_a_second_goal_probability():
    """Two engines answering one question is how a reader gets a third answer.

    `portfolio_risk` returns its own `goal` with its own probability. The
    context takes the projection's instead -- the same one the Plan screen
    shows -- and copies the risk block WITHOUT `goal`.
    """
    import inspect

    from app.services import context_service

    src = inspect.getsource(context_service.gather)
    risk_copy = src.split('ctx["risk"] = {k: r.get(k) for k in')[1].split(")}")[0]
    assert '"goal"' not in risk_copy, (
        "the risk block still carries a second goal probability: " + risk_copy)


# --------------------------------------------------------------------------- #
# the seam
# --------------------------------------------------------------------------- #
def test_labelling_is_a_copy_and_never_reaches_the_api():
    """`gather` feeds a PROMPT and a BROWSER, and only one of them wants labels.

    The first version of this change wrapped the values inside `gather` itself.
    `/api/v1/ask` returns that same object to the client, so `context.nav_ils`
    stopped being a number and became `{"value": ...}` -- caught by
    test_assistant.py, which had been asserting `context["nav_ils"] >= 0` since
    long before any of this. Labelling belongs at the seam where text is
    generated, not in the shared assembler.
    """
    from app.services.context_service import for_prompt

    plain = {"nav_ils": 21474.35,
             "performance": {"total_return_pct": 17.03},
             "projection": {"probability_meets_target": 0.227}}
    labelled = for_prompt(plain)

    assert labelled["nav_ils"]["value"] == 21474.35
    assert "BACKFILL" in labelled["performance"]["total_return_pct"]["means"]
    assert labelled["projection"]["probability_meets_target"]["value"] == 0.227

    # ...and the input is untouched, because THAT object goes to the browser
    assert plain["nav_ils"] == 21474.35
    assert plain["performance"]["total_return_pct"] == 17.03
    assert plain["projection"]["probability_meets_target"] == 0.227


def test_a_figure_with_no_basis_is_left_alone_rather_than_guessed_at():
    """An unknown figure passes through unwrapped. The table is the whole rule."""
    from app.services.context_service import for_prompt

    out = for_prompt({"something_new_pct": 4.2})
    assert out["something_new_pct"] == 4.2

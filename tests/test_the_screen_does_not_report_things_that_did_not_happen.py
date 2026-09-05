"""PR 58, the honesty track. Three claims were investigated; two held.

**1. "What's moving" reported four events that never happened.** HELD, and it
was the worst thing found. `BuiltinEconomicDataProvider` returned four hardcoded
sentences -- *"US CPI release above consensus; rate-path implications."*,
*"USD/ILS broke a multi-week range."* -- unchanged since the day they were typed,
carrying severity scores of 95 and 80.

What made them harmful is everything TRUE that was wrapped around them.
`market_impact.annotate` named the user's actual holdings and computed the
actual shekel exposure ("touches 36% of your portfolio (₪7,634)"), `_playbook`
turned each into advice (*"Consider hedging the currency or rebalancing toward
ILS assets"*), and the card printed `last_refreshed` above it -- a field that
comes from `market_status()`, the **price** refresh clock. A frozen list wore a
moving timestamp. Every layer was honest by itself; the composition is what
reached the user.

**2. `Settings.objective` was a wiring diagram for a wire nobody ran.** HELD.
Written by `plan_settings`, commented "the plan objective the agents optimize
toward", and read by no agent: every reader goes to `plan.objective` directly.
The only thing that touched it was a test asserting the round-trip, which is
what made a dead field look load-bearing.

**3. `war_room.AGENTS` lists seven agents that do not exist.** DID NOT HOLD.
Every one of the seven is emitted in real transcripts by the real pipeline, and
five of them unconditionally. The claim was mine and it was wrong; the test
below pins the truth so the question does not get re-opened from memory.

Not financial advice.
"""
import os
import re
from pathlib import Path

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")

REPO = Path(__file__).resolve().parent.parent


def test_the_builtin_provider_invents_no_events():
    """Zero is the honest count when there is no source.

    The frozen FX table survives being labelled because it has a job: some
    number must value a dollar holding, and a stale one with a loud warning
    beats a crash. Nothing needs an economic event to exist, so there is no
    equivalent argument -- and a labelled fiction is still a fiction the reader
    has to remember to discount.
    """
    from app.providers.builtin import BuiltinEconomicDataProvider

    assert BuiltinEconomicDataProvider().get_events() == []


def test_no_invented_event_text_survives_anywhere_in_the_app():
    """Not just the provider. The sentences themselves, anywhere.

    Deleting the return value would leave the strings a copy-paste away in a
    fixture, a seed script, or a demo mode. These four are searched for by their
    own words.
    """
    ghosts = ["CPI release above consensus",
              "broke a multi-week range",
              "surtax threshold guidance revised",
              "constituent earnings cluster"]
    offenders = []
    for path in (REPO / "app").rglob("*.py"):
        if "__pycache__" in str(path):
            continue
        body = path.read_text(encoding="utf-8", errors="ignore")
        for g in ghosts:
            # The provider docstring quotes two of them to explain the removal.
            if g in body and "STATUS.md PR 58" not in body:
                offenders.append(f"{path.relative_to(REPO)}: {g!r}")
    assert not offenders, (
        "invented market events are back in the app:\n  " + "\n  ".join(offenders))


def test_the_events_payload_does_not_borrow_the_price_clock():
    """`last_refreshed` was the lie, more than the events were.

    Four static sentences under "Updates hourly - last refreshed 4 minutes ago"
    do not read as a placeholder; they read as reporting. The timestamp came
    from `market_status()`, which is about PRICES. A timestamp borrowed from a
    different subject is not a small inaccuracy.
    """
    import inspect

    from app.api.routes import market

    src = inspect.getsource(market.research_events)
    code = "\n".join(ln for ln in src.splitlines() if not ln.strip().startswith("#"))
    assert "last_refreshed" not in code, (
        "the price-refresh clock is back on the events payload")
    assert "market_status" not in code, (
        "research_events reads market_status again, which is about prices")
    for key in ('"source"', '"live"', '"source_note"'):
        assert key in code, f"the payload no longer carries {key}"


def test_the_empty_panel_explains_itself_rather_than_going_blank():
    """A card that renders nothing is indistinguishable from one that failed.

    The old renderer's catch block wrote an em-dash on error. If the empty case
    fell through to the same silence, a reader could not tell "there is no
    source" from "the request died".
    """
    html = (REPO / "app" / "static_app" / "index.html").read_text(encoding="utf-8")
    assert "source_note" in html, (
        "index.html no longer reads source_note, so an empty panel says nothing")

    # Comment lines stripped: the replacement quotes the old caption to explain
    # why it went, and a guard that cannot tell a quotation from a recurrence
    # would forbid writing down what happened.
    code = "\n".join(ln for ln in html.splitlines()
                     if not ln.strip().startswith("//"))
    assert "Updates hourly" not in code, (
        "the borrowed 'Updates hourly' caption is back in the UI")


def test_the_empty_case_does_not_take_the_glossary_down_with_it():
    """A `return` here would have emptied the glossary too.

    The news block and the glossary live in the same function, so an early exit
    on the empty path would have removed a second, unrelated panel -- which is
    exactly the kind of quiet collateral this PR is about.
    """
    html = (REPO / "app" / "static_app" / "index.html").read_text(encoding="utf-8")
    news = html[html.index('fetch("/api/v1/research/events")'):]
    news = news[:news.index("// glossary")]
    assert "return;" not in news, (
        "the empty-events path returns early, which skips the glossary render "
        "that follows it in the same function")


def test_settings_no_longer_advertises_an_objective_nothing_reads():
    """The comment claimed the agents optimized toward it. They never saw it."""
    from app.core.config import Settings

    assert "objective" not in Settings.model_fields, (
        "Settings.objective is back. Every reader takes plan.objective off the "
        "plan itself; if something now reads the settings copy, wire it and say "
        "where.")

    body = (REPO / "app").rglob("*.py")
    readers = []
    for path in body:
        if "__pycache__" in str(path):
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for m in re.finditer(r"\b(?:settings|cfg|ps)\.objective\b", text):
            readers.append(f"{path.relative_to(REPO)}:{text[:m.start()].count(chr(10)) + 1}")
    assert not readers, f"something reads objective off a Settings object: {readers}"


def test_every_war_room_agent_is_one_that_actually_speaks():
    """THE CLAIM THAT DID NOT HOLD, pinned as the correction.

    `AGENTS` is returned by `/war-room` as the cast list. It was alleged to name
    seven agents where only a handful exist. It does not: all seven appear in
    transcripts built by the real pipeline, and Alpha, Tax, Decision, Adversary
    and UX are appended unconditionally.

    The confusion was between two different taxonomies -- `app/agents/` holds
    five modules (adversary, allocation, fee, research, screener), while the war
    room's names are pipeline ROLES, most of which are engines. That is worth
    arguing about on its own terms; it is not a fabricated list, and this test
    exists so the next reader checks rather than remembering.
    """
    from app.services.war_room import AGENTS

    src = (REPO / "app" / "services" / "war_room.py").read_text(encoding="utf-8")
    emitted = set(re.findall(r'"agent": "([A-Za-z ]+)"', src))

    missing = [a for a in AGENTS if a not in emitted]
    assert not missing, (
        f"AGENTS advertises {missing} to /war-room, but no transcript line ever "
        "carries that name. A cast list is a claim about what the reader will "
        "see.")

    unlisted = sorted(emitted - set(AGENTS))
    assert not unlisted, (
        f"{unlisted} speak in transcripts but are missing from AGENTS, so the "
        "cast list understates the transcript.")

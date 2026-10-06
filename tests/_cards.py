"""PR 102: Today's cards and the cards behind its heads-ups, in one list."""


def all_cards(body: dict) -> list[dict]:
    return ((body.get("recommendations") or []) + (body.get("more_recommendations") or [])
            + [h["card"] for h in (body.get("headsups") or []) if h.get("card")])

"""PR 104a: the Plan tab is Goal · My mix · Explore (Execution plan v2.2, D6 = A + C).

The screen is static HTML; this guards its structure -- the three sub-tabs, which
block lives in which, and D5 ("What would it take?" folded at the foot of
Explore) -- so a later edit cannot quietly put a block back in the wrong place.
"""
import re
from pathlib import Path

HTML = (Path(__file__).resolve().parents[1] / "app" / "static_app" / "index.html").read_text(encoding="utf-8")


def _section(id_: str) -> str:
    start = HTML.index(f'<div id="{id_}"')
    nxt = [HTML.find(f'<div id="{k}"', start + 1) for k in ("plan-goal", "plan-mix", "plan-explore")]
    ends = [i for i in nxt if i > start] + [HTML.index("</section>", start)]
    return HTML[start:min(ends)]


def test_three_sub_tabs():
    seg = HTML[HTML.index('id="planSeg"'):HTML.index('id="planSeg"') + 600]
    assert re.findall(r"planTab\('(\w+)'\)", seg) == ["goal", "mix", "explore"]


def test_each_block_is_in_its_sub_tab():
    goal, mix, explore = _section("plan-goal"), _section("plan-mix"), _section("plan-explore")
    assert 'id="goalSum"' in goal and 'id="goalWays"' in goal
    assert 'id="mixBox"' in mix and 'id="protBox"' in mix
    assert 'id="stratBox"' in explore


def test_what_would_it_take_is_folded_at_the_foot_of_explore():
    explore = _section("plan-explore")
    assert explore.index('id="stratBox"') < explore.index('id="wwitBox"')
    assert '<details class="card" id="wwitBox"' in explore and " open" not in explore[explore.index('id="wwitBox"') - 30:
                                                                                              explore.index('id="wwitBox"') + 60]


def test_the_rules_section_shows_only_stops_and_the_guard():
    m = re.search(r"const _STOP_TYPES=\{([^}]*)\}", HTML)
    assert m and set(re.findall(r"(\w+):", m.group(1))) == {"profit_protect", "trailing_stop", "stop_loss"}
    assert 'rule_type==="drawdown_guard"' in HTML

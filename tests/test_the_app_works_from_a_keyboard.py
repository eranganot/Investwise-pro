"""PR 117 (L12): keyboard and focus.

The shell had no focus styles, no Escape, and 17 clickable spans/divs that a
keyboard could not reach. Static checks over index.html, so a regression is a
red test, not a QA finding:
- every `var(--x)` is defined in `:root` or carries a fallback;
- every `<span|div ... onclick>` is a button-role element (overlay backdrops,
  which close on a click outside the modal, are the only exceptions);
- `.seg{` is defined once (D84: look A with a blue selection);
- a `:focus-visible` ring exists and one keydown handler serves Enter/Space/Escape.
"""
import re
from pathlib import Path

HTML = Path("app/static_app/index.html").read_text(encoding="utf-8")
BACKDROPS = {"sheetBg", "cashov", "histov", "editov", "ov"}


def _root_vars() -> set[str]:
    root = re.search(r":root\{([^}]*)\}", HTML).group(1)
    return set(re.findall(r"--([\w-]+)\s*:", root))


def test_every_css_variable_is_defined_or_has_a_fallback():
    defined = _root_vars()
    bad = sorted({m.group(1) for m in re.finditer(r"var\(--([\w-]+)\s*\)", HTML) if m.group(1) not in defined})
    assert not bad, f"var(--x) with no definition and no fallback: {bad}"
    assert "warn" in defined, "--warn is used across the app and must be a theme colour"


def test_every_clickable_span_or_div_is_a_button():
    bad = []
    for m in re.finditer(r"<(span|div)\b([^>]*)\bonclick=", HTML):
        attrs = m.group(2)
        ident = re.search(r'\bid="([^"]+)"', attrs)
        if ident and ident.group(1) in BACKDROPS:
            continue
        if 'role="button"' not in attrs or "tabindex=" not in attrs:
            line = HTML.count("\n", 0, m.start()) + 1
            bad.append(f"index.html:{line} <{m.group(1)}{attrs[:60]}")
    assert not bad, "clickable but not keyboard-reachable:\n" + "\n".join(bad)


def test_one_segmented_control_style():
    assert len(re.findall(r"^\s*\.seg\{", HTML, re.M)) == 1
    assert re.search(r"\.seg button\.on\{background:var\(--blue\)", HTML), "D84: look A with a blue selection"


def test_focus_is_visible_and_the_keys_work():
    assert ":focus-visible{" in HTML
    assert "outline:0" not in HTML, "an input with its outline removed and no replacement ring"
    assert re.search(r"document\.addEventListener\(\"keydown\"", HTML), "one delegated key handler"
    for key in ('"Escape"', '"Enter"', '" "'):
        assert key in HTML, key
    assert "_sheetReturn" in HTML, "the sheet returns focus to what opened it"

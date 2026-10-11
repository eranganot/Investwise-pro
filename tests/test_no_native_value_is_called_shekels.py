"""PR 105 guard: nothing multiplies a quantity by a native price and files the
result under a shekel name without converting it.

The class this closes: `quantity * current_price` is in the holding's TRADING
currency. Assigned to `value_ils`, or to any name or dict key containing `ils`,
it is a dollar figure wearing a shekel label. Five such sites reached the
assistant, the risk validation, the sector card, the fee scan and the backtest
route (C3), and the trim/replace sizing was the same mistake run backwards
(C2b, C3+). PR 102 fixed one sibling by hand and the next was found a month
later, so this walks the whole of `app/` instead of trusting the next author.

A site passes when the same expression goes through a conversion (`fx_rate`,
`rate`, `position_value_ils`, `holding_value_ils`, `quote_in_ils`, `to_base`).
"""
import ast
import pathlib
import re

APP = pathlib.Path(__file__).resolve().parents[1] / "app"
_NATIVE = re.compile(r"\b\w*(qty|quantity|shares)\w*\b[^\n]*\*[^\n]*\b\w*price\w*\b"
                     r"|\b\w*price\w*\b[^\n]*\*[^\n]*\b\w*(qty|quantity|shares)\w*\b", re.I)
_CONVERTED = re.compile(r"fx|rate|value_ils\(|_ils\b(?!\s*[:=])|to_base|price_ils", re.I)

# Sites that are correct and say why. Key: "relative/path.py:<name or key>".
ALLOWED: dict[str, str] = {}


def _ils_name(node) -> str | None:
    if isinstance(node, ast.Name) and "ils" in node.id.lower():
        return node.id
    if isinstance(node, ast.Attribute) and "ils" in node.attr.lower():
        return node.attr
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant) \
            and isinstance(node.slice.value, str) and "ils" in node.slice.value.lower():
        return node.slice.value
    return None


def _offenders():
    out = []
    for path in sorted(APP.rglob("*.py")):
        src = path.read_text(encoding="utf-8")
        tree = ast.parse(src)
        rel = path.relative_to(APP.parent).as_posix()
        pairs = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign)) and node.value is not None:
                for t in (node.targets if isinstance(node, ast.Assign) else [node.target]):
                    name = _ils_name(t)
                    if name:
                        pairs.append((name, node.value))
            elif isinstance(node, ast.Dict):
                for k, v in zip(node.keys, node.values):
                    if isinstance(k, ast.Constant) and isinstance(k.value, str) and "ils" in k.value.lower():
                        pairs.append((k.value, v))
        for name, value in pairs:
            text = ast.get_source_segment(src, value) or ""
            if _NATIVE.search(text) and not _CONVERTED.search(text):
                key = f"{rel}:{name}"
                if key not in ALLOWED:
                    out.append(f"{key} (line {value.lineno}): {text[:90]}")
    return out


def test_no_native_value_is_filed_as_shekels():
    bad = _offenders()
    assert not bad, ("quantity x native price stored under a shekel name, with no FX "
                     "conversion. Use fx.position_value_ils / holding_value_ils / "
                     "quote_in_ils:\n  " + "\n  ".join(bad))


def test_the_guard_catches_the_bug_it_was_written_for(tmp_path, monkeypatch):
    """The guard is only worth its run time if it fails on the original line."""
    bad = tmp_path / "app" / "x.py"
    bad.parent.mkdir()
    bad.write_text('def f(d):\n    return {"value_ils": d["quantity"] * d["current_price"]}\n')
    good = tmp_path / "app" / "y.py"
    good.write_text('def f(d, ccy):\n    return {"value_ils": d["quantity"] * d["current_price"] * fx_rate(ccy)}\n')
    monkeypatch.setattr(__import__(__name__), "APP", tmp_path / "app")
    found = _offenders()
    assert len(found) == 1 and "x.py:value_ils" in found[0], found

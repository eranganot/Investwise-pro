"""Every call into the funding layer must say which target it is measuring against.

PR 42 threaded the book's target (sleeves plus core) through
`rank_trim_candidates`, `plan_funding` and `propose_funded_buy`, and updated the
call sites it had found. It had not found them all: the list came from a grep
piped through `head`, and ten lines were treated as the whole answer. Two call
sites were missed -- `_buy_idea_recs` and `_commodity_recs` -- and the first of
them is the one that builds the screener cards on Today.

So PR 42 shipped, and the card still read:

    "cheapest way to raise it: Equities is the most overweight class
     (86% against a 80% target) and VTI is among its least-taxed positions"

on a book whose own target for Equities is 89.2% and whose `/mix` reported
`rebalance_required: false`. The fix was right and the coverage was not, which
is indistinguishable from not having fixed it.

This test does not care what the callers pass. It cares that they pass
something, so that a new call site cannot silently inherit the objective table
again. Walking the AST rather than grepping is the point: the failure being
pinned here was a search that stopped early.

Not financial advice.
"""
import ast
import pathlib

# The functions that choose a target mix. Every call to one of them has to be
# explicit about which target, because the two available answers differ by nine
# points on this book and can differ in sign.
FUNDING_CALLS = {"propose_funded_buy", "plan_funding", "rank_trim_candidates"}

MODULES = ("app/services/recommendations.py", "app/services/funding_service.py")

REPO = pathlib.Path(__file__).resolve().parent.parent


def _calls_missing_book_target(path: pathlib.Path):
    """Every call to a funding entry point that does not pass `book_target`."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    defined_here = {n.name for n in ast.walk(tree)
                    if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    missing = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name not in FUNDING_CALLS:
            continue
        # A recursive helper defined in the same module and called with the
        # parameter forwarded still counts; what matters is the keyword.
        if any(k.arg == "book_target" for k in node.keywords):
            continue
        missing.append((name, node.lineno, name in defined_here))
    return missing


def test_every_funding_call_names_its_target():
    for rel in MODULES:
        path = REPO / rel
        assert path.exists(), f"{rel} moved; this guard needs its new path"
        missing = _calls_missing_book_target(path)
        assert not missing, (
            f"{rel} calls the funding layer without saying which target to measure "
            f"against, so it silently inherits OBJ_TARGET[objective]:\n"
            + "\n".join(f"  line {ln}: {nm}(...)" for nm, ln, _ in missing))


def test_the_guard_would_have_caught_the_miss():
    """The guard has to fail on the shape it exists to prevent.

    A test that only ever passes is not evidence. This builds the exact call the
    two missed sites made and confirms the walker flags it -- otherwise a green
    run above proves nothing about the walker.
    """
    import tempfile

    src = (
        "def f(rows, snap, plan, objective, cap):\n"
        "    return propose_funded_buy(rows=rows, snap=snap, plan=plan,\n"
        "                              objective=objective, cap=cap,\n"
        "                              ticker='USO', buying_class='Commodities')\n"
    )
    with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False,
                                     encoding="utf-8") as fh:
        fh.write(src)
        tmp = pathlib.Path(fh.name)
    try:
        missing = _calls_missing_book_target(tmp)
        assert [m[0] for m in missing] == ["propose_funded_buy"], (
            "the walker did not flag a funding call with no book_target; the "
            "guard above is vacuous")
    finally:
        tmp.unlink()

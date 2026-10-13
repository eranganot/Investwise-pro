"""PR 118a/118b (L7): the layering points one way.

Routes call services. Services (and engines, agents, the worker) never import
`app.api.*`: three did, calling route handlers as functions, so a request-shaped
function became the business logic of plan health, the assistant context and Today.
"""
import ast
from pathlib import Path

LAYERS = ("app/services", "app/engines", "app/agents", "app/worker", "app/core")


def test_services_never_import_routes():
    bad = []
    for root in LAYERS:
        for p in Path(root).rglob("*.py"):
            for n in ast.walk(ast.parse(p.read_text(encoding="utf-8"))):
                mods = ([n.module or ""] if isinstance(n, ast.ImportFrom)
                        else [a.name for a in n.names] if isinstance(n, ast.Import) else [])
                bad += [f"{p}:{n.lineno} imports {m}" for m in mods if m == "app.api" or m.startswith("app.api.")]
    assert not bad, "\n".join(bad)

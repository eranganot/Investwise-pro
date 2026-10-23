"""PR 144 -- a build that cannot run its own jobs says so at boot.

`run_nav_snapshot_blocking` imported `async_session` from `app.core.database`,
where the name is `AsyncSessionLocal`. The import sat inside the function, so
nothing at import time, no linter and no test touched it, and the job failed 7
nights out of 7 (CLAUDE.md, "a job nothing calls"). Railway deploys every push
whether or not anything checked it.

This walks the module of every scheduled job and resolves every `from X import Y`
written INSIDE a function -- the imports that only run when the job does -- and
reports each one that would fail. Pure and cheap: it imports modules (already
imported by the app) and reads attributes; it runs nothing.
"""
from __future__ import annotations

import ast
import importlib
import inspect
import logging

logger = logging.getLogger("investwise.self_check")


def _resolve(base_pkg: str, mod: str | None, level: int) -> str | None:
    if level == 0:
        return mod
    parts = base_pkg.split(".")
    if level > len(parts):
        return None
    root = ".".join(parts[: len(parts) - level + 1])
    return f"{root}.{mod}" if mod else root


def lazy_import_errors(module) -> list[str]:
    """Every `from X import Y` inside a function of `module` that would fail."""
    try:
        src = inspect.getsource(module)
    except (OSError, TypeError):
        return []
    tree = ast.parse(src)
    pkg = module.__name__.rpartition(".")[0] if not hasattr(module, "__path__") else module.__name__
    out: list[str] = []
    seen: set[tuple[str, str]] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if not isinstance(node, ast.ImportFrom):
                continue
            target = _resolve(pkg, node.module, node.level or 0)
            if not target:
                continue
            try:
                mod = importlib.import_module(target)
            except Exception as exc:  # noqa: BLE001
                key = (target, "*")
                if key not in seen:
                    seen.add(key)
                    out.append(f"{module.__name__}.{fn.name}: cannot import {target} ({type(exc).__name__})")
                continue
            for a in node.names:
                if a.name == "*" or (target, a.name) in seen:
                    continue
                seen.add((target, a.name))
                if not hasattr(mod, a.name):
                    try:
                        importlib.import_module(f"{target}.{a.name}")    # a submodule is fine
                    except Exception:  # noqa: BLE001
                        out.append(f"{module.__name__}.{fn.name}: {target} has no {a.name!r}")
    return out


def check_jobs(entry_points: dict) -> dict:
    """{ok, errors, modules}: the lazy imports of every job's module resolve."""
    errors: list[str] = []
    mods = sorted({getattr(fn, "__module__", None) for fn in entry_points.values()} - {None})
    for name in mods:
        try:
            errors += lazy_import_errors(importlib.import_module(name))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{name}: cannot import ({type(exc).__name__}: {exc})")
    return {"ok": not errors, "errors": errors[:20], "modules": mods}

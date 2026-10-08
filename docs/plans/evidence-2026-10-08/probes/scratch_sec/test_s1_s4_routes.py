"""S1/S4: introspect every route's auth dependencies, then probe mutating routes with a READ_ONLY token
and every route with no credentials (REQUIRE_AUTH=true)."""
import json
import os
import re
import uuid

from fastapi.routing import APIRoute
from fastapi.testclient import TestClient

from conftest import tok

OUT = os.path.join(os.path.dirname(__file__), "routes.json")


def _roles(dependant, acc):
    from app.api.deps import acting_user
    from app.core.auth import Role
    call = dependant.call
    if call is acting_user:
        acc.add("acting_user")
    if call is not None and getattr(call, "__name__", "") == "_dep" and call.__closure__:
        for cell in call.__closure__:
            try:
                v = cell.cell_contents
            except ValueError:
                continue
            if isinstance(v, Role):
                acc.add(v.value)
    for d in dependant.dependencies:
        _roles(d, acc)
    return acc


def _level(acc):
    order = ["SUPERADMIN", "ADVISOR", "ANALYST", "READ_ONLY"]
    for r in order:
        if r in acc:
            return r
    return "NONE"


def _fill(path):
    return re.sub(r"\{[^}]+\}", lambda m: str(uuid.uuid4()) if "id" in m.group(0) else "TEVA", path)


def test_routes():
    from app.main import app
    rows = []
    for r in app.routes:
        if not isinstance(r, APIRoute):
            rows.append({"path": getattr(r, "path", "?"), "methods": sorted(getattr(r, "methods", None) or []),
                         "kind": type(r).__name__, "level": "NONE(non-API)"})
            continue
        acc = _roles(r.dependant, set())
        rows.append({"path": r.path, "methods": sorted(r.methods), "kind": "api",
                     "level": _level(acc), "acting_user": "acting_user" in acc, "endpoint": r.endpoint.__module__})
    ro = tok("READ_ONLY")
    with TestClient(app, raise_server_exceptions=False) as c:
        for row in rows:
            if row["kind"] != "api":
                continue
            p = _fill(row["path"])
            m = [x for x in row["methods"] if x not in ("HEAD", "OPTIONS")][0]
            row["probe_method"] = m
            # unauthenticated probe for routes with no auth dependency (GET only, safe)
            if row["level"] == "NONE" and m == "GET":
                try:
                    resp = c.get(p)
                    row["anon_status"] = resp.status_code
                    row["anon_body"] = resp.text[:160]
                except Exception as e:  # noqa: BLE001
                    row["anon_status"] = f"EXC {e!r}"[:80]
            if m in ("POST", "PUT", "PATCH", "DELETE"):
                try:
                    # route-level gate only: 401/403 = rejected, anything else = auth passed
                    resp = c.request(m, p, headers=ro, json={})
                    row["ro_status"] = resp.status_code
                    row["ro_body"] = resp.text[:120]
                except Exception as e:  # noqa: BLE001
                    row["ro_status"] = f"EXC {e!r}"[:80]
                try:
                    resp = c.request(m, p, json={})
                    row["anon_write_status"] = resp.status_code
                except Exception as e:  # noqa: BLE001
                    row["anon_write_status"] = f"EXC {e!r}"[:80]
    with open(OUT, "w") as f:
        json.dump(rows, f, indent=1)
    print("\nwrote", OUT, len(rows))

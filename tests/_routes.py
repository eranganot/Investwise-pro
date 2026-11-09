"""Every route path the app serves, in match order.

FastAPI <=0.115 flattened include_router() into app.routes (one APIRoute per
endpoint, full path). From 0.16x an included router is a single
_IncludedRouter entry carrying the router and its prefix, so
`[r.path for r in app.routes]` no longer sees the endpoints at all (IW-1b,
2026-11-05). This walks both shapes.
"""


def flat_paths(routes, prefix: str = "") -> list:
    out = []
    for r in routes:
        path = getattr(r, "path", None)
        if path is not None:
            out.append(prefix + path)
            continue
        ctx = getattr(r, "include_context", None)
        if ctx is not None:
            out.extend(flat_paths(ctx.included_router.routes, prefix + (ctx.prefix or "")))
    return out


def api_routes(app):
    """(methods, path, dependant) for every endpoint, with router-level
    dependencies included - what the security gates must inspect.

    Old FastAPI: each flattened APIRoute already carries the merged dependant.
    New FastAPI: an _IncludedRouter yields _EffectiveRouteContext objects whose
    `dependant` is the merged one. Iterating app.routes for APIRoute alone under
    the new version yields only the app's own few routes, so every "no route is
    public by accident" check passed on an empty list.
    """
    from fastapi.routing import APIRoute

    out = []
    for r in app.routes:
        if isinstance(r, APIRoute):
            out.append((r.methods, r.path, r.dependant))
        elif hasattr(r, "effective_route_contexts"):
            for c in r.effective_route_contexts():
                if getattr(c, "methods", None) and getattr(c, "dependant", None) is not None:
                    out.append((c.methods, c.path, c.dependant))
    return out

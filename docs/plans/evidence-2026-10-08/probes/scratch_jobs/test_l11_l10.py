"""L11: startup blocks on futures fetches.  L10: futures bypass failure mode."""
import os
import tempfile

_DB = os.path.join(tempfile.mkdtemp(prefix="iwl11_"), "l11.db")
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///" + _DB.replace("\\", "/")
os.environ["AUTO_CREATE_TABLES"] = "true"
os.environ["ENABLE_SCHEDULER"] = "true"
os.environ["MARKET_DATA_PROVIDER"] = "builtin"

import time  # noqa: E402

import pytest  # noqa: E402


def _boot_time(monkeypatch, delay):
    from fastapi.testclient import TestClient

    from app.services import markets_service, warmup

    calls = []

    def slow_http(url, *a, **k):
        calls.append(url)
        time.sleep(delay)
        raise TimeoutError("stub: provider slow")
    monkeypatch.setattr(markets_service, "_http_text", slow_http)

    async def _no_warm():
        return None
    monkeypatch.setattr(warmup, "warm_loop", _no_warm)

    import app.main as m
    t0 = time.perf_counter()
    with TestClient(m.app) as c:
        t_ready = time.perf_counter() - t0
        r = c.get("/health/ready")
    print(f"\n[delay={delay}s] lifespan->first request: {t_ready:.2f}s  "
          f"futures fetches during boot: {len(calls)}  /health/ready={r.status_code}")
    return t_ready, len(calls)


@pytest.mark.parametrize("delay", [0.0, 5.0, 12.0])
def test_boot_blocks_on_futures(monkeypatch, delay):
    t, n = _boot_time(monkeypatch, delay)
    assert n == 8
    assert t >= delay


def test_outage_overwrites_good_cache_with_empty(monkeypatch):
    from app.services import markets_service as ms
    good = {"chart": {"result": [{"meta": {"regularMarketPrice": 30.0,
                                           "chartPreviousClose": 20.0, "currency": "USD"}}]}}
    import json
    monkeypatch.setattr(ms, "_http_text", lambda url, *a, **k: json.dumps(good))
    snap1 = ms.futures_snapshot(force=True)
    print("\n[before outage] items:", len(snap1["futures"]), "regime:", snap1["market"])

    def down(url, *a, **k):
        raise OSError("HTTP Error 429: Too Many Requests")
    monkeypatch.setattr(ms, "_http_text", down)
    snap2 = ms.futures_snapshot(force=True)
    print("[after outage]  items:", len(snap2["futures"]), "regime:", snap2["market"])
    print("[cached_regime after outage]", ms.cached_regime())
    assert snap2["futures"] == [] and snap2["market"]["regime"] == "neutral"

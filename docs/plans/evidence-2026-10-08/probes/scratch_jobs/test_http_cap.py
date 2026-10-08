"""Does _http_text's `result(timeout=timeout+5)` actually cap a hung call?"""
import os
import time

os.environ.setdefault("ENABLE_SCHEDULER", "false")


def test_cap_is_not_a_cap(monkeypatch):
    import urllib.request

    from app.providers import live

    class _Hang:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def hung_urlopen(req, timeout=None):
        time.sleep(10)          # e.g. slow-drip body / DNS, not covered by socket timeout
        return _Hang()
    monkeypatch.setattr(urllib.request, "urlopen", hung_urlopen)
    t0 = time.perf_counter()
    err = None
    try:
        live._http_text("http://x", timeout=1.0)    # nominal cap: 1 + 5 = 6s
    except Exception as e:  # noqa: BLE001
        err = type(e).__name__
    dt = time.perf_counter() - t0
    print(f"\n_http_text(timeout=1) nominal cap 6s -> returned after {dt:.2f}s, error={err}")
    assert dt >= 9.5

#!/usr/bin/env python3
"""FULL SMOKE in Python - the daily production smoke without PowerShell (IW-6).

A line-for-line port of scripts/smoke/smoke-all.ps1 plus the D-1 smoke-key
checks (smoke-d1-read-only-smoke-key.ps1), so the scheduled run on a Mac or in
a Linux cloud box no longer downloads pwsh every morning. READ-ONLY: every
request is a GET, except the one /ask probe the PowerShell smoke also makes,
which the read-only smoke key is refused (SKIP, by design - D131).

    IW_SMOKE_KEY=... python3 scripts/smoke/smoke_all.py --sha <short sha>
    python3 scripts/smoke/smoke_all.py --base-url http://localhost:8000

Key resolution, most private first: IW_AGENT_KEY, IW_SMOKE_KEY,
scripts/.agent-key (git-ignored). Stdlib only. Exit 1 on any FAIL.
Known non-failures in the daily run (owner item A1): "Gemini failing" and an
AI summary that failed because the credits are exhausted.
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time
import urllib.error
import urllib.request

PROD = "https://investwise-pro-production.up.railway.app"
ROOT = pathlib.Path(__file__).resolve().parents[2]


class Smoke:
    def __init__(self, base: str, key: str | None):
        self.base = base.rstrip("/")
        self.key = key
        self.passed = self.failed = self.skipped = self.retried = 0
        self.call_no = 0

    # -- output -------------------------------------------------------------
    def ok(self, m):
        print(f"  PASS  {m}")
        self.passed += 1

    def bad(self, m):
        print(f"  FAIL  {m}")
        self.failed += 1

    def skip(self, m):
        print(f"  SKIP  {m}")
        self.skipped += 1

    @staticmethod
    def sec(m):
        print(f"\n{m}")

    @staticmethod
    def note(m):
        print(f"   {m}")

    # -- http ---------------------------------------------------------------
    def _request(self, method, path, timeout, body=None, headers=None, auth=True):
        h = {"User-Agent": "investwise-smoke/py", "Accept": "application/json"}
        if auth and self.key:
            h["x-agent-key"] = self.key
        h.update(headers or {})
        data = json.dumps(body).encode() if body is not None else None
        if data is not None:
            h["Content-Type"] = "application/json"
        req = urllib.request.Request(self.base + path, data=data, method=method, headers=h)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return json.loads(raw) if raw else None

    def api(self, method, path, timeout=60, body=None):
        """JSON or None. One retry on a transport error (a stale keep-alive),
        none on an HTTP status - same rule as the PowerShell Api()."""
        for attempt in (1, 2):
            self.call_no += 1
            t0 = time.monotonic()
            try:
                r = self._request(method, path, timeout, body)
                print(f"   [call {self.call_no:2}] {method} {path}  {time.monotonic() - t0:.2f}s")
                if attempt == 2:
                    print("  (recovered on retry)")
                    self.retried += 1
                return r
            except urllib.error.HTTPError as e:
                print(f"   [call {self.call_no:2}] {method} {path}  FAILED after {time.monotonic() - t0:.2f}s"
                      f"  HTTP {e.code}")
                return None
            except Exception as e:  # noqa: BLE001 - reported, then retried once
                print(f"   [call {self.call_no:2}] {method} {path}  FAILED after {time.monotonic() - t0:.2f}s"
                      f"  {type(e).__name__}: {e}")
                if attempt == 1:
                    time.sleep(0.8)
                    continue
                return None
        return None


def resolve_key() -> str | None:
    for var in ("IW_AGENT_KEY", "IW_SMOKE_KEY"):
        if os.environ.get(var):
            return os.environ[var].strip()
    f = ROOT / "scripts" / ".agent-key"
    if f.exists():
        return f.read_text().strip() or None
    return None


def num(v, default=0.0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def run(s: Smoke, sha: str, schema: str | None, wait_sec: int) -> None:
    print("\n===== INVESTWISE FULL SMOKE (python) =====")
    print(f"   {s.base}{'  (expected build ' + sha + ')' if sha else ''}")

    # 0a. Build - which commit is answering (from _smoke-common Assert-Build).
    s.sec("0. Build")
    deadline = time.monotonic() + wait_sec
    h = None
    while True:
        try:
            h = s._request("GET", "/health", 30, auth=False)
        except Exception:  # noqa: BLE001
            h = None
        if not sha or (h and str(h.get("commit", "")).startswith(sha.strip())) or time.monotonic() > deadline:
            break
        s.note(f"waiting for {sha} (serving {h.get('commit') if h else 'nothing'})")
        time.sleep(20)
    if h is None:
        s.bad("/health unreachable")
        return
    if sha:
        if str(h.get("commit", "")).startswith(sha.strip()):
            s.ok(f"serving {h.get('commit')}")
        else:
            s.bad(f"serving {h.get('commit')}, expected {sha} - nothing below would be about this build")
            return
    else:
        s.skip("no --sha: cannot prove which build answered")
    if schema:
        (s.ok if h.get("schema_revision") == schema else s.bad)(f"schema {h.get('schema_revision')} (expected {schema})")

    # 0b. Preflight - is the deploy settled?
    s.sec("0. Preflight - is the app settled?")
    stable = 0
    for i in range(1, 6):
        t0 = time.monotonic()
        try:
            s._request("GET", "/health", 10, auth=False)
            s._request("GET", "/health/ready", 10)
            stable += 1
            s.note(f"probe {i} OK ({time.monotonic() - t0:.2f}s)")
        except Exception:  # noqa: BLE001
            s.note(f"probe {i} FAILED")
        time.sleep(0.6)
    if stable < 5:
        s.bad(f"only {stable}/5 health probes succeeded - a deploy is most likely in flight; re-run when Active")
        return
    s.ok("5/5 health probes - deploy is settled, results below are meaningful")

    # D-1: the key is the read-only smoke key and a wrong key reads nothing.
    s.sec("0. Keys (D-1)")
    sk, ak = os.environ.get("IW_SMOKE_KEY"), os.environ.get("IW_AGENT_KEY")
    if sk and ak and sk == ak:
        s.bad("IW_SMOKE_KEY equals IW_AGENT_KEY - the scheduled smoke must not hold the write key")
    elif not s.key:
        s.bad("no key: set IW_SMOKE_KEY (or IW_AGENT_KEY / scripts/.agent-key)")
        return
    else:
        s.ok("a key is set (value never printed)")
    try:
        s._request("GET", "/api/v1/portfolio", 30, headers={"x-agent-key": "not-a-key"}, auth=False)
        s.bad("a wrong key read the portfolio")
    except urllib.error.HTTPError as e:
        (s.ok if e.code in (401, 403) else s.bad)(f"a wrong key is refused ({e.code})")
    except Exception as e:  # noqa: BLE001
        s.skip(f"wrong-key probe did not answer: {type(e).__name__}")

    # 1. Latency
    s.sec("1. Latency (phase 10 - provider cache TTLs)")
    t1 = time.monotonic()
    recs = s.api("GET", "/api/v1/recommendations")
    cold = time.monotonic() - t1
    t2 = time.monotonic()
    recs2 = s.api("GET", "/api/v1/recommendations")
    warm = round(time.monotonic() - t2, 1)
    s.note(f"cold {cold:.1f}s   warm {warm}s")
    (s.bad if recs is None else s.ok)("recommendations unreachable - most checks below cannot run" if recs is None
                                      else "recommendations responded")
    if recs2 is None:
        s.skip("second call failed")
    elif warm < 8:
        s.ok(f"warm call {warm}s (baseline was 24.2s every time)")
        if warm > 2:
            s.note(f"NOTE {warm}s is still slow for a warm call (open item)")
    else:
        s.bad(f"warm call {warm}s - provider cache is not surviving between requests")

    # 2. Cash
    s.sec("2. Cash is not priced as a stock (phase 1)")
    port = s.api("GET", "/api/v1/portfolio")
    if port is None:
        s.skip("portfolio unreachable (NOT a pass)")
    else:
        cash_api = (s.api("GET", "/api/v1/portfolio/cash") or {}).get("cash_ils")
        s.note(f"NAV {num(port.get('nav_ils')):.2f}  invested {num(port.get('invested_ils')):.2f}  gain {port.get('gain_pct')}%")
        s.note(f"card cash {port.get('cash_ils')}  |  modal cash {cash_api}")
        if cash_api is None:
            s.skip("cash endpoint empty")
        elif abs(num(port.get("cash_ils")) - num(cash_api)) < 1.0:
            s.ok("both cash figures agree")
        else:
            s.bad("card and modal cash disagree")
        g = port.get("gain_pct")
        if g is None:
            s.skip("no gain_pct")
        elif abs(num(g)) < 500:
            s.ok(f"gain {g}% is plausible")
        else:
            s.bad(f"gain {g}% - NAV still inflated")

    # 3. Health score
    s.sec("3. Health score is measured, not placeholder (phases 3 + 9)")
    health = s.api("GET", "/api/v1/health-check")
    if health is None:
        s.skip("health-check unreachable (NOT a pass)")
    else:
        s.note(f"score {health.get('wealth_health_score')}  risk {health.get('risk_score')}  tax {health.get('tax_efficiency_score')}"
               f"  spread {health.get('diversification_score')}  cash {health.get('liquidity_score')}")
        (s.ok if health.get("max_achievable") == 100 else s.bad)(
            "no hidden ceiling" if health.get("max_achievable") == 100 else f"max_achievable = {health.get('max_achievable')}")
        vol = health.get("avg_volatility_pct")
        if vol == 15:
            s.bad("volatility is exactly 15% - placeholder still in use")
        elif num(vol) > 0:
            s.ok(f"volatility {vol}% derived from real instruments")
        else:
            s.skip("no volatility reported")
        tax = num(health.get("tax_efficiency_score"))
        if tax > 85:
            s.ok(f"tax {tax:g} exceeds the old hard cap of 85")
        else:
            s.skip(f"tax {tax:g} - only meaningful if you have no unharvested losses")

    # 4. Expected return
    s.sec("4. Expected return is grounded (phase 9)")
    plan = s.api("GET", "/api/v1/plan")
    if plan is None:
        s.skip("plan unreachable (NOT a pass)")
    else:
        roi = plan.get("portfolio_expected_roi_pct")
        s.note(f"expected ROI {roi}%/yr  target {plan.get('roi_annual_target_pct')}%  on_track {plan.get('roi_on_track')}")
        if roi is None:
            s.bad("expected ROI is null")
        elif num(roi) > 0:
            s.ok(f"expected ROI {roi}%/yr")
        else:
            s.bad(f"still {roi}%/yr")

    # 5. Cash not tradeable (read-only since PR 145)
    s.sec("5. Cash is not a tradeable holding (phase 6)")
    st5 = s.api("GET", "/api/v1/push/status")
    pr5 = (((st5 or {}).get("scheduler") or {}).get("history") or {}).get("price_refresh")
    if pr5 is None:
        s.skip("no price_refresh history (NOT a pass)")
    elif pr5.get("last_ok") and (pr5.get("last_result") or {}).get("rules"):
        s.ok(f"rule check ran at {pr5.get('last_run')} (newly triggered: {pr5['last_result']['rules'].get('triggered')})")
    else:
        s.bad(f"the last rule check did not run cleanly at {pr5.get('last_run')}: {pr5.get('last_error')}")
    rules = (s.api("GET", "/api/v1/rules") or {}).get("rules")
    if rules is None:
        s.skip("rules unreachable (NOT a pass)")
    else:
        armed = [r for r in rules if r.get("ticker") == "CASH" and r.get("active")]
        (s.bad if armed else s.ok)("CASH rule still armed" if armed else "no active rule on CASH")
        sug = (s.api("GET", "/api/v1/rules/suggestions") or {}).get("suggestions")
        if sug is None:
            s.skip("suggestions unreachable")
        else:
            on_cash = [x for x in sug if x.get("ticker") == "CASH"]
            (s.bad if on_cash else s.ok)("CASH still suggested" if on_cash else "suggester never offers rules on CASH")

    # 6. Banner matches cards - re-read recommendations FIRST, then rules (see the .ps1).
    s.sec("6. Banner matches the cards (phases 6 + 8)")
    recs_now = s.api("GET", "/api/v1/recommendations", 90)
    rules_now = (s.api("GET", "/api/v1/rules") or {}).get("rules")
    if rules_now is None or recs_now is None:
        s.skip("need both rules and recommendations (NOT a pass)")
    else:
        recs = recs_now
        trig = [r for r in rules_now if r.get("triggered") and r.get("active")]
        pool = list(recs.get("recommendations") or []) + list(recs.get("more_recommendations") or []) + \
            [h.get("card") for h in (recs.get("headsups") or []) if h.get("card")]
        rule_cards = {c.get("id") for c in pool if str(c.get("id", "")).startswith("rule_")}
        s.note(f"triggered: {', '.join(r.get('ticker', '?') for r in trig) or 'none'}")
        if len(trig) == len(rule_cards):
            s.ok(f"banner ({len(trig)}) matches rule cards ({len(rule_cards)}) - no deadlock")
        else:
            s.bad(f"banner {len(trig)} vs {len(rule_cards)} cards")
            s.note(f"rule_banner: {json.dumps(recs.get('rule_banner'))[:300]}")
            s.note(f"rule cards : {', '.join(sorted(rule_cards)) or 'none'}")

    # 7. Audit trail
    s.sec("7. Rule audit trail (phases 2 + 5)")
    events = (s.api("GET", "/api/v1/rules/events?limit=50") or {}).get("events")
    if events is None:
        s.skip("events unreachable (NOT a pass)")
    elif not events:
        s.skip("no firings logged yet")
    else:
        s.ok(f"{len(events)} firing(s) logged")
        outcomes = sorted({e.get("outcome") for e in events if e.get("outcome") != "triggered"})
        (s.ok if outcomes else s.skip)(f"outcomes stamped: {', '.join(map(str, outcomes))}" if outcomes else "nothing acted on yet")
        fired = [e for e in events if e.get("outcome") not in ("paused", "resumed", "created", "edited", "deleted")]
        if not fired:
            s.skip("no firing in the last 50 events")
        elif fired[0].get("trigger_price"):
            s.ok(f"grounded in a real quote ({fired[0].get('ticker')} @ {fired[0].get('trigger_price')})")
        else:
            s.bad("no trigger_price")

    # 8. Redeploy card
    s.sec("8. Idle cash redeployment (phases 7 + 9)")
    if recs is None or port is None:
        s.skip("need recommendations + portfolio (NOT a pass)")
    else:
        cap = num(((plan or {}).get("caps") or {}).get("concentration_cap"))
        nav = num(port.get("nav_ils"))
        weights = {p.get("ticker"): num(p.get("value_ils")) / nav for p in (port.get("positions") or []) if nav}
        over = [t for t, w in weights.items() if cap > 0 and w >= cap and t != "CASH"]
        floor_pct = {"Preserve": 0.10, "Income": 0.07, "Grow": 0.03}.get((plan or {}).get("objective"), 0.05)
        cash = num(port.get("cash_ils"))
        spendable = cash - nav * floor_pct
        held = [c for c in list(recs.get("recommendations") or []) + list(recs.get("more_recommendations") or [])
                if (c.get("apply") or {}).get("kind") == "held_cash"]
        reserved = sum(num((c.get("apply") or {}).get("reserved_ils")) for c in held)
        spendable -= reserved
        s.note(f"cash {cash:,.2f} | floor {floor_pct:.0%} | held back {reserved:,.2f} | spendable {spendable:,.2f}")
        card = next((c for c in (recs.get("recommendations") or []) if (c.get("apply") or {}).get("kind") == "redeploy_cash"), None)
        if card is None:
            if spendable < 250:
                s.ok(f"no redeploy card, and none is due (spendable {spendable:.0f} is under the 250 minimum)")
            else:
                s.bad(f"no redeploy card despite {spendable:.0f} spendable above the floor")
        else:
            s.ok(f"card: {card.get('title')}")
            legs = (card.get("apply") or {}).get("legs") or []
            (s.bad if any(leg.get("ticker") == "CASH" for leg in legs) else s.ok)("never buys CASH with cash")
            (s.bad if any(num(leg.get("amount_ils")) < 250 for leg in legs) else s.ok)("no sub-minimum dust trades")
            (s.bad if any(leg.get("ticker") in over for leg in legs) else s.ok)("no leg tops up a holding at/over YOUR cap")
            total = sum(num(leg.get("amount_ils")) for leg in legs)
            (s.ok if total <= cash else s.bad)(f"total {total:,.2f} within available cash")
            meta = card.get("meta") or {}
            after, floor = num(meta.get("cash_after_ils")), num(meta.get("floor_pct")) * nav
            (s.ok if after >= floor - 1 else s.bad)(f"keeps {after:,.2f} buffer (floor {floor:,.2f})")
        cash_cards = [c for c in (recs.get("recommendations") or [])
                      if (c.get("apply") or {}).get("kind") in ("redeploy_cash", "rebalance_to_objective")
                      or "idle cash" in str(c.get("title", ""))]
        (s.ok if len(cash_cards) <= 1 else s.bad)(
            "one card per pot of cash" if len(cash_cards) <= 1 else f"{len(cash_cards)} cards spend the same cash")

    # 9. Notifications
    s.sec("9. Notifications (phase 4)")
    st = s.api("GET", "/api/v1/push/status")
    if st is None:
        s.skip("push/status unreachable (NOT a pass)")
    else:
        for b in st.get("blockers") or []:
            s.note(f"-> {b}")
        (s.ok if num(st.get("subscriptions")) > 0 else s.bad)(
            f"{st.get('subscriptions')} live subscription(s)" if num(st.get("subscriptions")) > 0
            else "ZERO subscriptions - enable notifications in the app")
        (s.ok if st.get("push_library_ok") else s.bad)(
            "push library OK" if st.get("push_library_ok") else f"push library: {st.get('push_library_error')}")
        sch = st.get("scheduler") or {}
        (s.ok if sch.get("scheduler_running") else s.bad)("scheduler running" if sch.get("scheduler_running") else "scheduler not running")
        jids = {j.get("id") for j in sch.get("jobs") or []}
        (s.ok if {"push_evaluate", "push_digest"} <= jids else s.bad)(
            "push jobs registered" if {"push_evaluate", "push_digest"} <= jids else "push jobs missing")

    # 10. AI
    s.sec("10. AI features (phase 10)")
    diag = s.api("GET", "/api/v1/adversary/diagnostics")
    if diag is None:
        s.skip("diagnostics unreachable (NOT a pass)")
    elif diag.get("ok"):
        s.ok(f"Gemini reachable (model {diag.get('adversary_llm_model')})")
    else:
        s.bad(f"Gemini failing: {diag.get('error')}")
    ai = s.api("GET", "/api/v1/ai/portfolio-summary")
    if ai is None:
        s.skip("ai summary unreachable")
    elif ai.get("llm"):
        s.ok("portfolio summary generated")
    else:
        s.bad(f"summary failed - reason shown to user: '{ai.get('error')}'")
    ask = s.api("POST", "/api/v1/ask", body={"question": "What is my biggest holding?"})
    if ask is None:
        s.skip("ask unreachable")
    elif ask.get("llm"):
        s.ok("Ask InvestWise answered")
    else:
        s.bad(f"ask failed - reason shown to user: '{ask.get('error')}'")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sha", default="", help="short SHA production should serve")
    ap.add_argument("--base-url", default=PROD)
    ap.add_argument("--schema", default=None, help="expected /health schema_revision")
    ap.add_argument("--wait-for-deploy-seconds", type=int, default=600)
    a = ap.parse_args(argv)
    s = Smoke(a.base_url, resolve_key())
    run(s, a.sha, a.schema, a.wait_for_deploy_seconds if a.sha else 0)
    print(f"\n===== {s.passed} passed, {s.failed} failed, {s.skipped} skipped =====")
    if s.retried:
        print(f"{s.retried} call(s) only succeeded on retry (stale connection, not an API fault).")
    return 1 if s.failed else 0


if __name__ == "__main__":
    sys.exit(main())

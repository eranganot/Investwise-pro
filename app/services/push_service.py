"""Web Push (PWA notifications).

Responsibilities:
  * Manage a VAPID keypair (env-pinned, else lazily generated + persisted in DB).
  * Store/prune browser push subscriptions, keyed by the user's email.
  * Send notifications (deduped) and evaluate triggers: new recommendations,
    risk alerts, and large price moves. Plus a scheduled digest.

pywebpush / py_vapid are imported lazily so a missing dependency never blocks
app boot — push simply stays disabled until the package is installed.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import get_settings
from app.models.tables import KVSetting, NotifiedEvent, PushSubscription, User

logger = logging.getLogger("investwise.push")

_KV_PUB = "vapid_public_key"
_KV_PRIV = "vapid_private_key"
KV_LAST_PUSH_RUN = "last_push_run"

# Statuses that mean "this subscription will never work again, drop it so the
# browser re-subscribes on the next visit".
#   404/410 - the endpoint is gone (classic expiry).
#   403     - VAPID signature rejected: the keypair no longer matches the one the
#             subscription was created with. This was NOT pruned before, so if the
#             DB-persisted keypair was ever regenerated, every push failed 403
#             forever, nothing was cleaned up, and no client ever re-subscribed --
#             a permanent, completely silent outage.
DEAD_CODES = (403, 404, 410)


# --------------------------------------------------------------------------- #
# Key/value helpers
# --------------------------------------------------------------------------- #
async def _kv_get(session: AsyncSession, key: str) -> str | None:
    row = await session.get(KVSetting, key)
    return row.value if row else None


async def _kv_set(session: AsyncSession, key: str, value: str) -> None:
    row = await session.get(KVSetting, key)
    if row:
        row.value = value
    else:
        session.add(KVSetting(key=key, value=value))
    await session.flush()


def _b64url(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


def _generate_vapid() -> tuple[str, str]:
    """Return (public_b64url_raw, private_b64url_raw)."""
    from cryptography.hazmat.primitives import serialization
    from py_vapid import Vapid01

    v = Vapid01()
    v.generate_keys()
    priv_raw = v.private_key.private_numbers().private_value.to_bytes(32, "big")
    pub_raw = v.public_key.public_bytes(
        serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint
    )
    return _b64url(pub_raw), _b64url(priv_raw)


async def get_vapid(session: AsyncSession) -> tuple[str, str, str]:
    """Return (public, private, subject). Env wins; else load/generate in DB."""
    s = get_settings()
    subject = s.vapid_subject or "mailto:admin@example.com"
    if s.vapid_public_key and s.vapid_private_key:
        return s.vapid_public_key, s.vapid_private_key, subject
    pub = await _kv_get(session, _KV_PUB)
    priv = await _kv_get(session, _KV_PRIV)
    if not (pub and priv):
        pub, priv = _generate_vapid()
        await _kv_set(session, _KV_PUB, pub)
        await _kv_set(session, _KV_PRIV, priv)
        await session.commit()
        logger.info("Generated and persisted a new VAPID keypair.")
    return pub, priv, subject


async def public_key(session: AsyncSession) -> str:
    pub, _, _ = await get_vapid(session)
    return pub


# --------------------------------------------------------------------------- #
# Subscriptions
# --------------------------------------------------------------------------- #
async def save_subscription(session: AsyncSession, subject: str, sub: dict, ua: str | None) -> None:
    endpoint = sub.get("endpoint")
    keys = sub.get("keys") or {}
    p256dh, auth = keys.get("p256dh"), keys.get("auth")
    if not (endpoint and p256dh and auth):
        raise ValueError("invalid subscription payload")
    existing = await session.scalar(select(PushSubscription).where(PushSubscription.endpoint == endpoint))
    if existing:
        existing.subject, existing.p256dh, existing.auth, existing.ua = subject, p256dh, auth, ua
    else:
        session.add(PushSubscription(subject=subject, endpoint=endpoint, p256dh=p256dh, auth=auth, ua=ua))
    await session.commit()


async def delete_subscription(session: AsyncSession, endpoint: str) -> None:
    await session.execute(delete(PushSubscription).where(PushSubscription.endpoint == endpoint))
    await session.commit()


# --------------------------------------------------------------------------- #
# Dedupe ledger
# --------------------------------------------------------------------------- #
async def _seen(session: AsyncSession, subject: str, signature: str) -> bool:
    cutoff = datetime.now(timezone.utc) - timedelta(days=get_settings().push_dedupe_days)
    row = await session.scalar(
        select(NotifiedEvent).where(
            NotifiedEvent.subject == subject,
            NotifiedEvent.signature == signature,
            NotifiedEvent.created_at >= cutoff,
        )
    )
    return row is not None


async def _mark(session: AsyncSession, subject: str, signature: str) -> None:
    session.add(NotifiedEvent(subject=subject, signature=signature))
    await session.flush()


# --------------------------------------------------------------------------- #
# Sending
# --------------------------------------------------------------------------- #
def _send_sync(sub_info: dict, payload: dict, private_key: str, subject: str) -> int:
    """Send one push. Returns HTTP-ish status: 201 ok, 404/410 = prune, else error."""
    from pywebpush import WebPushException, webpush

    try:
        webpush(
            subscription_info=sub_info,
            data=json.dumps(payload),
            vapid_private_key=private_key,
            vapid_claims={"sub": subject},
            ttl=86400,
        )
        return 201
    except WebPushException as exc:  # noqa: BLE001
        code = getattr(getattr(exc, "response", None), "status_code", 0) or 0
        if code not in DEAD_CODES:
            logger.warning("web push failed (%s): %s", code, exc)
        else:
            logger.info("pruning dead push subscription (%s)", code)
        return code or 500
    except Exception as exc:  # noqa: BLE001
        # pywebpush/py_vapid missing, or a malformed key: without this the
        # ImportError escaped into the caller's broad except and every push
        # disappeared with a single ambiguous warning.
        logger.warning("web push unavailable: %s: %s", type(exc).__name__, exc)
        return 500


async def send_to_subject(session: AsyncSession, subject: str, title: str, body: str,
                          url: str = "/app/", tag: str | None = None, data: dict | None = None,
                          category: str = "action") -> int:
    """Fan a notification out to all of a subject's devices. Prunes dead subs.

    category: "action" — maps 1:1 to a card in the Today view; "info" — purely
    informational (price moves, the weekly digest) and implies no to-do."""
    _, private, vsubject = await get_vapid(session)
    subs = (await session.scalars(
        select(PushSubscription).where(PushSubscription.subject == subject))).all()
    if not subs:
        return 0
    payload = {"title": title, "body": body, "url": url, "tag": tag or "investwise",
               "category": category, "data": {"category": category, **(data or {})}}
    sent, dead = 0, []
    for sub in subs:
        info = {"endpoint": sub.endpoint, "keys": {"p256dh": sub.p256dh, "auth": sub.auth}}
        code = await asyncio.to_thread(_send_sync, info, payload, private, vsubject)
        if code in DEAD_CODES:
            dead.append(sub.endpoint)
        elif code == 201:
            sent += 1
    for ep in dead:
        await session.execute(delete(PushSubscription).where(PushSubscription.endpoint == ep))
    if dead:
        await session.commit()
    return sent


async def send_test(session: AsyncSession, subject: str) -> int:
    return await send_to_subject(
        session, subject, "InvestWise", "🔔 Notifications are on. We'll alert you to what matters.",
        url="/app/", tag="iw-test")


# --------------------------------------------------------------------------- #
# Triggers
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# P4.2 - four triggers, four rate limits
# --------------------------------------------------------------------------- #
#
# The limits are per TRIGGER, not per message. That distinction is the whole
# design: recommendation ids are content hashes, so a drift card whose amount
# moves from 3,300 to 3,362 is a NEW id and would slip past an id-keyed dedupe
# and push again. Nothing about the situation changed; only the rounding did.
#
#   signal flip      immediate  - rare, and acting late is the main reason a
#                                 rule underperforms its own backtest
#   rule fired       immediate  - the user armed it; silence would be worse
#   sleeve drift     daily      - drifts a little every day and says the same
#                                 thing each time
#   rules available  NEVER live - suggestions regenerate as prices move, so a
#                                 live push here is the one that gets
#                                 notifications switched off entirely. Weekly,
#                                 inside the 07:00 digest.
#
# TRIGGER_NEVER_LIVE is deliberately not "a very long window": a card that must
# never fire a live push should be impossible to push, not merely unlikely.
TRIGGER_IMMEDIATE = 0
# A fired rule is urgent the first time and noise the fifth. TRIGGER_IMMEDIATE
# means "no rate limit at all", which is how one cap breach reached the user
# every few hours. Hysteresis stops the rule flapping; this floor means that
# even if some future condition flaps anyway, it can interrupt at most twice a
# day. A different rule firing is a different signature and is unaffected.
TRIGGER_RULE_REPEAT = 12
TRIGGER_DAILY = 24
TRIGGER_NEVER_LIVE = None

_TRIGGER_RULES: tuple[tuple[str, str, int | None], ...] = (
    # (trigger name, how to spot it, rate limit in hours)
    ("rules_available", "ready to arm", TRIGGER_NEVER_LIVE),
    ("sleeve_drift", "has drifted to", TRIGGER_DAILY),
    ("sleeve_coldstart", "hold none of it", TRIGGER_DAILY),
)


def classify_trigger(rec: dict) -> tuple[str, int | None]:
    """(trigger, rate_limit_hours) for one card. Falls back to the old behaviour.

    Keyed on stable properties -- the card's dimension and a phrase from its
    title -- rather than its id, which changes whenever a number in it does.
    """
    title = str(rec.get("title") or "")
    rid = str(rec.get("id") or "")
    if rid.startswith("rule_"):
        return ("rule_fired", TRIGGER_RULE_REPEAT)
    if rid.startswith("stratsig_"):
        return ("signal_flip", TRIGGER_IMMEDIATE)
    for name, needle, limit in _TRIGGER_RULES:
        if needle in title:
            return (name, limit)
    # Everything else keeps the pre-existing behaviour: the global dedupe window.
    return ("recommendation", get_settings().push_dedupe_days * 24)


async def _seen_within(session: AsyncSession, subject: str, signature: str,
                       hours: int) -> bool:
    """Has this TRIGGER fired for this subject inside the window?"""
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max(0, hours))
    row = await session.scalar(
        select(NotifiedEvent).where(
            NotifiedEvent.subject == subject,
            NotifiedEvent.signature == signature,
            NotifiedEvent.created_at >= cutoff,
        )
    )
    return row is not None


def trigger_signature(trigger: str, rec: dict) -> str:
    """Stable per-trigger key.

    For a rate-limited trigger this must NOT include anything that moves, or the
    limit never bites. `rule_fired` keeps the rule id, because two different
    rules firing are two different events the user needs to hear about.
    """
    if trigger == "rule_fired":
        return f"trigger:rule_fired:{rec.get('rule_id') or rec.get('id')}"
    if trigger == "recommendation":
        return f"rec:{rec.get('id')}"
    # Drift and cold-start are about ONE sleeve, so key on the ticker if the card
    # names one, else on the trigger alone.
    meta = rec.get("meta") or {}
    tk = meta.get("ticker") or ""
    return f"trigger:{trigger}:{tk}" if tk else f"trigger:{trigger}"


def _sev_set() -> set[str]:
    return {x.strip().upper() for x in get_settings().push_notify_severities.split(",") if x.strip()}


async def evaluate_and_notify(session: AsyncSession, user: User, max_sends: int = 5) -> dict:
    """Inspect the user's portfolio and push for important changes:
    new high-severity recommendations, risk alerts, and large price moves."""
    from app.services.plan_service import effective_caps, get_plan
    from app.services.portfolio_analytics import compute_snapshot, load_positions, risk_alerts
    from app.services.recommendations import build_recommendations

    subject = user.email
    # ensure keys exist / subscriptions present
    has_subs = await session.scalar(
        select(PushSubscription).where(PushSubscription.subject == subject))
    if not has_subs:
        return {"sent": 0, "reason": "no subscriptions"}

    sev = _sev_set()
    sent = 0

    # 1) Recommendations
    try:
        built = await build_recommendations(session, user)
        for r in built.get("recommendations", []):
            if sent >= max_sends:
                break
            trigger, limit = classify_trigger(r)
            # A card whose trigger is never-live is skipped regardless of
            # severity: the digest carries it instead.
            if limit is None:
                continue
            # The four P4.2 triggers carry their own urgency and bypass the
            # severity filter -- a fired rule the user armed themselves is worth
            # a push even at MEDIUM, and drift is MEDIUM by design.
            if trigger == "recommendation" and r.get("severity", "").upper() not in sev:
                continue
            sig = trigger_signature(trigger, r)
            if limit > 0 and await _seen_within(session, subject, sig, limit):
                continue
            if limit == 0 and await _seen(session, subject, sig):
                continue      # immediate still means once per event, not per run
            icon = {"rule_fired": "\u26a1", "signal_flip": "\U0001f504",
                    "sleeve_drift": "\u2696\ufe0f", "sleeve_coldstart": "\u2696\ufe0f"}.get(trigger, "\U0001f4a1")
            sent += await send_to_subject(
                session, subject, f"{icon} {r.get('title', 'New recommendation')}",
                r.get("action") or "Open InvestWise to review.", url="/app/", tag=sig)
            await _mark(session, subject, sig)
    except Exception:  # noqa: BLE001
        logger.warning("recommendation eval failed", exc_info=False)

    # 2) Risk alerts + 3) price moves both need positions
    try:
        positions = await load_positions(session, user)
    except Exception:  # noqa: BLE001
        positions = []

    if positions:
        try:
            snap = compute_snapshot(positions)
            cap = effective_caps(await get_plan(session, user)).get("concentration_cap")
            for a in risk_alerts(snap, cap).get("alerts", []):
                if sent >= max_sends:
                    break
                if a.get("severity", "").upper() not in sev:
                    continue
                sig = f"alert:{a.get('vector')}"
                if await _seen(session, subject, sig):
                    continue
                sent += await send_to_subject(
                    session, subject, "⚠️ Risk alert", a.get("detail", "Check your portfolio."),
                    url="/app/", tag=sig)
                await _mark(session, subject, sig)
        except Exception:  # noqa: BLE001
            logger.warning("risk alert eval failed", exc_info=False)

        # price moves vs last-notified baseline (stored per ticker in KV)
        thr = get_settings().push_price_move_pct
        for p in positions:
            if sent >= max_sends:
                break
            tk, cur = p.get("ticker"), float(p.get("current_price") or 0)
            if not tk or cur <= 0:
                continue
            kvk = f"pxbase:{subject}:{tk}"
            base_s = await _kv_get(session, kvk)
            if base_s is None:
                await _kv_set(session, kvk, str(cur))
                continue
            base = float(base_s)
            if base <= 0:
                await _kv_set(session, kvk, str(cur))
                continue
            chg = (cur - base) / base * 100.0
            if abs(chg) >= thr:
                arrow = "📈" if chg > 0 else "📉"
                sent += await send_to_subject(
                    session, subject, f"{arrow} FYI — {tk} {chg:+.1f}%",
                    f"{tk} is now {cur:,.2f} (was {base:,.2f}). No action needed — just keeping you posted.",
                    url="/app/", tag=f"px:{tk}", category="info")
                await _kv_set(session, kvk, str(cur))

    await session.commit()
    return {"sent": sent}


async def send_digest(session: AsyncSession, user: User) -> dict:
    from app.services.digest_service import build as build_digest

    subject = user.email
    has_subs = await session.scalar(
        select(PushSubscription).where(PushSubscription.subject == subject))
    if not has_subs:
        return {"sent": 0, "reason": "no subscriptions"}
    sig = f"digest:{datetime.now(timezone.utc):%Y-%m-%d}"
    if await _seen(session, subject, sig):
        return {"sent": 0, "reason": "already sent today"}
    try:
        d = await build_digest(session, user)
        text = (d.get("digest") or "Your weekly summary is ready.").strip()
    except Exception:  # noqa: BLE001
        text = "Your weekly summary is ready."
    # The never-live triggers ride here instead. Suggestions regenerate as prices
    # move, so pushing them the moment they appear is the notification that gets
    # notifications switched off; once a week, in something the user already
    # opens, is the honest cadence.
    try:
        from app.services.recommendations import build_recommendations
        _built = await build_recommendations(session, user)
        _waiting = [r for r in _built.get("recommendations", [])
                    if classify_trigger(r)[1] is None]
        if _waiting:
            text += ("\n\n" + "; ".join(str(r.get("title")) for r in _waiting[:3])
                     + " \u2014 waiting in the app.")
    except Exception:  # noqa: BLE001
        logger.warning("digest could not append pending suggestions", exc_info=False)
    n = await send_to_subject(session, subject, "📋 Your wealth digest", text[:300],
                              url="/app/", tag="digest", category="info")
    await _mark(session, subject, sig)
    await session.commit()
    return {"sent": n}


# --------------------------------------------------------------------------- #
# Background runners (own short-lived engine; safe from APScheduler threads)
# --------------------------------------------------------------------------- #
async def diagnostics(session: AsyncSession, user: User) -> dict:
    """Why am I not getting notifications? Answers it without guesswork.

    Reports the three independent things that must all be true -- the scheduler
    is running its jobs, this user has a live subscription, and the push library
    can actually sign and send -- plus when each last happened.
    """
    from app.worker.scheduler import job_state

    subject = user.email
    subs = (await session.scalars(
        select(PushSubscription).where(PushSubscription.subject == subject))).all()
    last_run = await _kv_get(session, KV_LAST_PUSH_RUN)
    recent = (await session.scalars(
        select(NotifiedEvent).where(NotifiedEvent.subject == subject)
        .order_by(NotifiedEvent.created_at.desc()).limit(10))).all()

    library_ok, library_error = True, None
    try:
        import pywebpush  # noqa: F401
        import py_vapid  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        library_ok, library_error = False, f"{type(exc).__name__}: {exc}"

    sched = job_state()
    blockers = []
    if not subs:
        blockers.append("No push subscription for this account — re-enable notifications in the app.")
    if not library_ok:
        blockers.append(f"Push library unavailable ({library_error}).")
    if not sched.get("scheduler_running"):
        blockers.append("Scheduler is not running in this process — no job can fire.")
    for jid in ("push_evaluate", "push_digest"):
        h = sched.get("history", {}).get(jid)
        if h and not h.get("last_ok"):
            blockers.append(f"Job {jid} last failed: {h.get('last_error')}")
        if h is None and sched.get("scheduler_running"):
            blockers.append(f"Job {jid} has not run yet since this process started.")

    return {
        "subscriptions": len(subs),
        "vapid_pinned_by_env": bool(get_settings().vapid_public_key),
        "push_library_ok": library_ok,
        "push_library_error": library_error,
        "last_fanout_run": last_run,
        "dedupe_days": get_settings().push_dedupe_days,
        "notify_severities": get_settings().push_notify_severities,
        "recent_notifications": [
            {"signature": e.signature,
             "at": e.created_at.isoformat() if e.created_at else None} for e in recent],
        "scheduler": sched,
        "blockers": blockers or ["Nothing obviously broken — send a test push to confirm delivery."],
    }


async def _for_each_subscriber(coro_name: str) -> dict:
    """Run evaluate_and_notify or send_digest for every distinct subscriber."""
    from app.services.feed_service import ensure_user

    engine = create_async_engine(get_settings().database_url, pool_pre_ping=True)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    total = 0
    subjects: list[str] = []
    try:
        async with Session() as session:
            subjects = list((await session.scalars(select(PushSubscription.subject).distinct())).all())
        for subj in subjects:
            async with Session() as session:
                user = await ensure_user(session, subj)
                await session.flush()
                fn = evaluate_and_notify if coro_name == "evaluate" else send_digest
                res = await fn(session, user)
                total += res.get("sent", 0)
        # Persist a heartbeat: a fan-out that ran and sent nothing is a very
        # different diagnosis from one that never ran at all.
        async with Session() as session:
            await _kv_set(session, KV_LAST_PUSH_RUN,
                          f"{coro_name}@{datetime.now(timezone.utc).isoformat()}"
                          f" subscribers={len(subjects)} sent={total}")
            await session.commit()
    finally:
        await engine.dispose()
    return {"subscribers": len(subjects), "sent": total}


def run_evaluations_blocking() -> dict:
    """Sync entrypoint for APScheduler (runs in its own thread)."""
    try:
        return asyncio.run(_for_each_subscriber("evaluate"))
    except Exception:  # noqa: BLE001
        logger.warning("scheduled push evaluation failed", exc_info=True)
        return {"sent": 0}


def run_digests_blocking() -> dict:
    """Sync entrypoint for APScheduler (runs in its own thread)."""
    try:
        return asyncio.run(_for_each_subscriber("digest"))
    except Exception:  # noqa: BLE001
        logger.warning("scheduled digest failed", exc_info=True)
        return {"sent": 0}

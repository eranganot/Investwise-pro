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
        if code not in (404, 410):
            logger.warning("web push failed (%s): %s", code, exc)
        return code or 500


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
        if code in (404, 410):
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
            if r.get("severity", "").upper() not in sev:
                continue
            sig = f"rec:{r.get('id')}"
            if await _seen(session, subject, sig):
                continue
            sent += await send_to_subject(
                session, subject, f"💡 {r.get('title', 'New recommendation')}",
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
    n = await send_to_subject(session, subject, "📋 Your wealth digest", text[:300],
                              url="/app/", tag="digest", category="info")
    await _mark(session, subject, sig)
    await session.commit()
    return {"sent": n}


# --------------------------------------------------------------------------- #
# Background runners (own short-lived engine; safe from APScheduler threads)
# --------------------------------------------------------------------------- #
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

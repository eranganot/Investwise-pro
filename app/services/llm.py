"""Shared Gemini text generation (REST, no SDK) - used by the Adversary narrative,
the Q&A assistant, and the digest. Off (returns None) unless GOOGLE_API_KEY is set.
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import urllib.error
import urllib.request

from app.core.config import get_settings


def gemini_key() -> str | None:
    return os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")


def gemini_enabled() -> bool:
    return bool(gemini_key())


def _classify(exc: Exception | None, key: str | None) -> str:
    """Turn a failure into something a user can act on.

    Every failure mode used to collapse into a bare None, so "top up your
    billing" (a two-minute fix) rendered identically to "the model is down"
    (nothing you can do). Live example: HTTP 429 "prepayment credits are
    depleted" surfaced only as "Summary unavailable".
    """
    if not key:
        return "AI is not configured — no GOOGLE_API_KEY set."
    text = f"{exc}" if exc else ""
    if "429" in text or "quota" in text.lower() or "credit" in text.lower():
        return "AI credits are exhausted — top up billing in Google AI Studio."
    if "401" in text or "403" in text or "API key" in text:
        return "The AI key was rejected — check GOOGLE_API_KEY."
    if "404" in text:
        return "The configured AI model no longer exists — update the model name."
    if "timed out" in text.lower() or isinstance(exc, TimeoutError):
        return "The AI request timed out — try again shortly."
    return "The AI service is unreachable just now — try again shortly."


def gemini_generate_ex(prompt: str, *, model: str | None = None,
                       timeout: float = 20.0) -> tuple[str | None, str | None]:
    """Like ``gemini_generate`` but returns ``(text, error_message)``.

    The error is a short, plain-language reason suitable for showing the user.
    Never raises.
    """
    key = gemini_key()
    if not key:
        return None, _classify(None, None)
    try:
        out = _gemini_call(prompt, key, model, timeout)
        return (out or None), (None if out else "The AI returned an empty response.")
    except Exception as exc:  # noqa: BLE001
        return None, _classify(exc, key)


def _gemini_call(prompt: str, key: str, model: str | None, timeout: float) -> str:
    """One Gemini REST call. RAISES on failure so the caller can classify it."""
    model = model or getattr(get_settings(), "adversary_llm_model", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    body = json.dumps({"contents": [{"parts": [{"text": prompt}]}]}).encode("utf-8")

    def _call() -> str:
        req = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8', 'ignore')[:200]}")
        cands = payload.get("candidates") or []
        if not cands:
            return ""
        parts = (cands[0].get("content") or {}).get("parts") or []
        return "".join(p.get("text", "") for p in parts).strip()

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_call).result(timeout=timeout + 5)


def gemini_generate(prompt: str, *, model: str | None = None, timeout: float = 20.0) -> str | None:
    """Generate text via the Gemini REST API in a worker thread. Returns None if
    no key is configured or the call fails (never raises).

    Kept for callers that only need the text; prefer ``gemini_generate_ex`` when
    the failure reason should reach the user."""
    text, _err = gemini_generate_ex(prompt, model=model, timeout=timeout)
    return text


def gemini_generate_grounded(prompt: str, *, model: str | None = None,
                             timeout: float = 40.0) -> dict | None:
    """Generate with Google Search grounding (live web). Returns
    {"text": str, "sources": [{"title","url"}]} or None on failure.

    Falls back to ungrounded generation if the grounding tool is rejected
    (older model / unsupported), so research never hard-fails when a key exists.
    """
    key = gemini_key()
    if not key:
        return None
    model = model or getattr(get_settings(), "adversary_llm_model", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def _post(body: dict) -> dict:
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": key})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _extract(payload: dict) -> dict:
        cands = payload.get("candidates") or []
        if not cands:
            return {"text": "", "sources": []}
        c0 = cands[0]
        parts = (c0.get("content") or {}).get("parts") or []
        text = "".join(p.get("text", "") for p in parts).strip()
        sources = []
        gm = c0.get("groundingMetadata") or {}
        for ch in (gm.get("groundingChunks") or []):
            web = ch.get("web") or {}
            if web.get("uri"):
                sources.append({"title": web.get("title") or web["uri"], "url": web["uri"]})
        # de-dupe by url, keep order
        seen, uniq = set(), []
        for s in sources:
            if s["url"] not in seen:
                seen.add(s["url"])
                uniq.append(s)
        return {"text": text, "sources": uniq[:8]}

    def _call() -> dict:
        base = {"contents": [{"parts": [{"text": prompt}]}]}
        try:
            return _extract(_post({**base, "tools": [{"google_search": {}}]}))
        except urllib.error.HTTPError:
            return _extract(_post(base))  # grounding unsupported -> plain generation

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            res = ex.submit(_call).result(timeout=timeout + 5)
        return res if (res and res.get("text")) else None
    except Exception:
        return None

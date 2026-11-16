"""Shared Gemini text generation (REST, no SDK) - used by the Adversary narrative,
the Q&A assistant, and the digest. Off (returns None) unless GOOGLE_API_KEY is set.
"""
from __future__ import annotations

import concurrent.futures
import contextlib
import contextvars
import json
import os
import time
import urllib.error
import urllib.request
import uuid
from typing import Any, Iterator

from app.core.config import get_settings
from app.core.request_context import get_request_id


_ai_client: Any | None = None
_ai_distinct_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ai_distinct_id", default=None
)
_ai_process_session_id = f"process-{uuid.uuid4()}"


def configure_ai_observability(client: Any) -> None:
    """Reuse the application PostHog client for manual Gemini generations."""
    global _ai_client
    _ai_client = client


def set_ai_distinct_id(distinct_id: str) -> None:
    """Attribute later model calls in this request to the resolved user."""
    _ai_distinct_id.set(distinct_id)


@contextlib.contextmanager
def ai_observability_context(distinct_id: str) -> Iterator[None]:
    """Supply identity for model calls made by a background job."""
    token = _ai_distinct_id.set(distinct_id)
    try:
        yield
    finally:
        _ai_distinct_id.reset(token)


def ai_trace_id() -> str:
    """Use one trace per request turn; background jobs receive a fresh turn."""
    return get_request_id() or uuid.uuid4().hex


def _error_status(error: Exception) -> int | None:
    if isinstance(error, urllib.error.HTTPError):
        return error.code
    words = str(error).split()
    if len(words) >= 2 and words[0] == "HTTP":
        try:
            return int(words[1].rstrip(":"))
        except ValueError:
            return None
    return None


def capture_gemini_generation(
    prompt: str,
    model: str,
    *,
    output: str | None = None,
    latency: float | None = None,
    error: Exception | None = None,
    trace_id: str | None = None,
    span_name: str = "gemini_generate",
    tools: list[dict] | None = None,
) -> None:
    """Queue a manual AI event without affecting the provider call's outcome."""
    if _ai_client is None or (distinct_id := _ai_distinct_id.get()) is None:
        return

    properties: dict[str, Any] = {
        "$ai_trace_id": trace_id or ai_trace_id(),
        "$ai_session_id": _ai_process_session_id,
        "$ai_span_id": uuid.uuid4().hex,
        "$ai_span_name": span_name,
        "$ai_model": model,
        "$ai_provider": "gemini",
        "$ai_input": [{"role": "user", "content": prompt}],
    }
    if output is not None:
        properties["$ai_output_choices"] = [{"role": "assistant", "content": output}]
    if latency is not None:
        properties["$ai_latency"] = latency
    if tools is not None:
        properties["$ai_tools"] = tools
    if error is not None:
        properties["$ai_is_error"] = True
        properties["$ai_error"] = type(error).__name__
        if (status := _error_status(error)) is not None:
            properties["$ai_http_status"] = status

    try:
        _ai_client.capture_ai("$ai_generation", distinct_id=distinct_id, properties=properties)
    except Exception:  # noqa: BLE001 -- observability must not affect the LLM result
        pass


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
    trace_id = ai_trace_id()
    started_at = time.perf_counter()

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

    try:
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            output = ex.submit(_call).result(timeout=timeout + 5)
    except Exception as exc:
        capture_gemini_generation(
            prompt, model, latency=time.perf_counter() - started_at, error=exc,
            trace_id=trace_id,
        )
        raise
    capture_gemini_generation(
        prompt, model, output=output, latency=time.perf_counter() - started_at,
        trace_id=trace_id,
    )
    return output


def gemini_generate(prompt: str, *, model: str | None = None, timeout: float = 20.0) -> str | None:
    """Generate text via the Gemini REST API in a worker thread. Returns None if
    no key is configured or the call fails (never raises).

    Kept for callers that only need the text; prefer ``gemini_generate_ex`` when
    the failure reason should reach the user."""
    text, _err = gemini_generate_ex(prompt, model=model, timeout=timeout)
    return text


def gemini_generate_grounded(prompt: str, *, model: str | None = None,
                             timeout: float = 40.0) -> dict | None:
    """`gemini_generate_grounded_ex` without the reason (kept for callers)."""
    res, _err = gemini_generate_grounded_ex(prompt, model=model, timeout=timeout)
    return res


def gemini_generate_grounded_ex(prompt: str, *, model: str | None = None,
                                timeout: float = 40.0) -> tuple[dict | None, str | None]:
    """Generate with Google Search grounding (live web). Returns
    {"text": str, "sources": [{"title","url"}]} or None on failure.

    Falls back to ungrounded generation if the grounding tool is rejected
    (older model / unsupported), so research never hard-fails when a key exists.
    """
    key = gemini_key()
    if not key:
        return None, _classify(None, None)
    model = model or getattr(get_settings(), "adversary_llm_model", "gemini-2.0-flash")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    trace_id = ai_trace_id()

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

    def _post(body: dict, *, tools: list[dict] | None = None) -> dict:
        started_at = time.perf_counter()
        req = urllib.request.Request(
            url, data=json.dumps(body).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/json", "x-goog-api-key": key})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except Exception as exc:
            capture_gemini_generation(
                prompt, model, latency=time.perf_counter() - started_at, error=exc,
                trace_id=trace_id, span_name="gemini_generate_grounded", tools=tools,
            )
            raise
        capture_gemini_generation(
            prompt, model, output=_extract(payload)["text"],
            latency=time.perf_counter() - started_at, trace_id=trace_id,
            span_name="gemini_generate_grounded", tools=tools,
        )
        return payload

    def _call() -> dict:
        base = {"contents": [{"parts": [{"text": prompt}]}]}
        google_search = [{"google_search": {}}]
        try:
            return _extract(_post({**base, "tools": google_search}, tools=google_search))
        except urllib.error.HTTPError as e:
            # PR 110 (L15): fall back ONLY when the grounding tool itself is
            # rejected (HTTP 400). A 429 used to trigger a second, ungrounded
            # call into the same exhausted quota, and both failures collapsed
            # into "temporarily unavailable".
            if getattr(e, "code", None) != 400:
                raise
            return _extract(_post(base))

    try:
        from app.providers.live import _HTTP_POOL
        res = _HTTP_POOL.submit(_call).result(timeout=timeout + 5)
        return (res, None) if (res and res.get("text")) else (None, "The AI returned an empty response.")
    except Exception as exc:  # noqa: BLE001
        return None, _classify(exc, key)

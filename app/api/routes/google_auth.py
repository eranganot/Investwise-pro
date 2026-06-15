"""Google sign-in (Phase A).

Manual OAuth 2.0 (no heavy SDK): redirect to Google, exchange the code for an
access token, read the verified email from Google's userinfo, enforce the email
allowlist, then mint the app's own RS256 JWT and set it as an HttpOnly session
cookie. Multi-user-ready: every signed-in identity maps to a User row, so
widening access later is just editing ALLOWED_EMAILS.
"""
from __future__ import annotations

import concurrent.futures
import json
import secrets
import urllib.error
import urllib.parse
import urllib.request

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.auth import Role, create_token
from app.core.config import get_settings
from app.core.database import get_session
from app.services.feed_service import ensure_user

router = APIRouter(tags=["google-auth"])

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_USERINFO_URL = "https://www.googleapis.com/oauth2/v3/userinfo"
_STATE_COOKIE = "iw_oauth_state"


def _http_json(req: urllib.request.Request, timeout: float = 15.0) -> dict:
    """Run a blocking HTTP call in a worker thread (off the event loop)."""
    def _call() -> dict:
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"HTTP {e.code}: {e.read().decode('utf-8','ignore')[:200]}")
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(_call).result(timeout=timeout + 5)


def _is_https(request: Request) -> bool:
    return (request.headers.get("x-forwarded-proto", "").lower().startswith("https")
            or request.url.scheme == "https")


def _login_page(message: str = "") -> str:
    note = f'<p class="err">{message}</p>' if message else ""
    return f"""<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>InvestWise · Sign in</title>
<style>
 body{{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
   background:#0b0f17;color:#e8eef6;font-family:ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}}
 .card{{background:#111824;border:1px solid #1f2a3a;border-radius:18px;padding:34px 30px;width:330px;text-align:center}}
 .logo{{font-size:20px;font-weight:800;margin-bottom:6px}} .logo span{{color:#3b82f6}}
 .sub{{color:#8b97a8;font-size:13px;margin-bottom:22px}}
 a.btn{{display:flex;align-items:center;justify-content:center;gap:10px;text-decoration:none;
   background:#fff;color:#1f2937;font-weight:600;font-size:14px;padding:11px 14px;border-radius:10px}}
 a.btn:hover{{opacity:.92}} .err{{color:#f87171;font-size:13px;margin-top:14px}}
 .g{{width:18px;height:18px}}
</style></head><body><div class="card">
 <div class="logo">Invest<span>Wise</span></div>
 <div class="sub">Your private wealth cockpit</div>
 <a class="btn" href="/auth/google/login">
  <img class="g" src="https://www.gstatic.com/firebasejs/ui/2.0.0/images/auth/google.svg" alt="">
  Sign in with Google</a>{note}
</div></body></html>"""


@router.get("/login", response_class=HTMLResponse)
async def login_page(error: str = "") -> HTMLResponse:
    return HTMLResponse(_login_page(error))


@router.get("/auth/google/login")
async def google_login(request: Request):
    s = get_settings()
    if not s.google_oauth_client_id:
        return RedirectResponse("/login?error=Google+sign-in+is+not+configured")
    state = secrets.token_urlsafe(24)
    params = urllib.parse.urlencode({
        "client_id": s.google_oauth_client_id,
        "redirect_uri": s.google_oauth_redirect_uri,
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
        "access_type": "online",
        "prompt": "select_account",
    })
    resp = RedirectResponse(f"{_AUTH_URL}?{params}")
    resp.set_cookie(_STATE_COOKIE, state, max_age=600, httponly=True,
                    secure=_is_https(request), samesite="lax")
    return resp


@router.get("/auth/google/callback")
async def google_callback(request: Request, code: str = "", state: str = "",
                          session: AsyncSession = Depends(get_session)):
    s = get_settings()
    if not code or state != request.cookies.get(_STATE_COOKIE):
        return RedirectResponse("/login?error=Sign-in+failed+(state+mismatch)")
    try:
        token_req = urllib.request.Request(
            _TOKEN_URL,
            data=urllib.parse.urlencode({
                "client_id": s.google_oauth_client_id,
                "client_secret": s.google_oauth_client_secret,
                "code": code, "grant_type": "authorization_code",
                "redirect_uri": s.google_oauth_redirect_uri,
            }).encode(),
            method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})
        tokens = _http_json(token_req)
        access = tokens.get("access_token")
        info_req = urllib.request.Request(_USERINFO_URL, headers={"Authorization": f"Bearer {access}"})
        info = _http_json(info_req)
    except Exception:
        return RedirectResponse("/login?error=Sign-in+failed+(could+not+reach+Google)")

    email = (info.get("email") or "").lower()
    if not email or not info.get("email_verified", False):
        return RedirectResponse("/login?error=Email+not+verified")
    if s.allowed_email_list and email not in s.allowed_email_list:
        return RedirectResponse("/login?error=This+account+is+not+allowed")

    await ensure_user(session, email, name=info.get("name") or email, role="SuperAdmin")
    await session.commit()

    jwt_token = create_token(email, Role.SUPERADMIN, "access", ttl=s.session_ttl_sec)
    resp = RedirectResponse(s.post_login_redirect)
    resp.set_cookie(s.session_cookie_name, jwt_token, max_age=s.session_ttl_sec,
                    httponly=True, secure=_is_https(request), samesite="lax")
    resp.delete_cookie(_STATE_COOKIE)
    return resp


@router.get("/auth/logout")
async def logout(request: Request):
    resp = RedirectResponse("/login")
    resp.delete_cookie(get_settings().session_cookie_name)
    return resp

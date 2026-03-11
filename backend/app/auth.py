"""
Microsoft Azure AD Single Sign-On via MSAL.

Set these environment variables to enable SSO:
  AZURE_TENANT_ID      - Azure AD tenant ID (GUID or domain)
  AZURE_CLIENT_ID      - App registration client ID
  AZURE_CLIENT_SECRET  - App registration client secret
  AUTH_REDIRECT_URI    - Full callback URL (default: http://localhost:8000/auth/callback)
  SESSION_SECRET       - Secret for signing session cookies (auto-generated if absent)

If AZURE_CLIENT_ID is not set, SSO is disabled and all routes are accessible as
"Local User" (development mode).
"""

from __future__ import annotations

import os
import secrets
from typing import Optional

from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

AZURE_TENANT_ID    = os.environ.get("AZURE_TENANT_ID", "")
AZURE_CLIENT_ID    = os.environ.get("AZURE_CLIENT_ID", "")
AZURE_CLIENT_SECRET = os.environ.get("AZURE_CLIENT_SECRET", "")
REDIRECT_URI       = os.environ.get("AUTH_REDIRECT_URI", "http://localhost:8000/auth/callback")
SESSION_SECRET     = os.environ.get("SESSION_SECRET") or secrets.token_hex(32)

SCOPES = ["User.Read"]

# SSO is active only when all three Azure config vars are present
SSO_ENABLED: bool = bool(AZURE_TENANT_ID and AZURE_CLIENT_ID and AZURE_CLIENT_SECRET)

_LOCAL_USER = {"name": "Local User", "upn": "local@localhost", "oid": "dev"}

# Routes that are always accessible (no login required)
_UNPROTECTED = ("/auth/", "/static/", "/favicon.ico")


def get_msal_app():
    """Create a fresh MSAL ConfidentialClientApplication."""
    try:
        import msal  # optional dependency — only needed when SSO_ENABLED
        return msal.ConfidentialClientApplication(
            AZURE_CLIENT_ID,
            authority=f"https://login.microsoftonline.com/{AZURE_TENANT_ID}",
            client_credential=AZURE_CLIENT_SECRET,
        )
    except ImportError:
        return None


def get_current_user(request: Request) -> Optional[dict]:
    """Return the signed-in user dict, or Local User if SSO is disabled."""
    if not SSO_ENABLED:
        return _LOCAL_USER
    return request.session.get("user")


# ---------------------------------------------------------------------------
# SSO route handlers (registered in main.py)
# ---------------------------------------------------------------------------

async def handle_login(request: Request) -> RedirectResponse:
    """Redirect the browser to the Microsoft login page."""
    next_url = request.query_params.get("next", "/")
    if not SSO_ENABLED:
        return RedirectResponse(next_url)

    msal_app = get_msal_app()
    if msal_app is None:
        return HTMLResponse("msal package not installed. Run: pip install msal", status_code=500)

    state = secrets.token_urlsafe(16)
    request.session["auth_state"] = state
    request.session["auth_next"]  = next_url

    auth_url = msal_app.get_authorization_request_url(
        SCOPES,
        state=state,
        redirect_uri=REDIRECT_URI,
    )
    return RedirectResponse(auth_url)


async def handle_callback(request: Request) -> RedirectResponse | HTMLResponse:
    """Exchange the authorisation code for a token and create a session."""
    code  = request.query_params.get("code")
    state = request.query_params.get("state")
    error = request.query_params.get("error")

    if error:
        desc = request.query_params.get("error_description", "")
        return HTMLResponse(
            f"<h2>Authentication error</h2><p>{error}: {desc}</p>",
            status_code=400,
        )

    if state != request.session.get("auth_state"):
        return HTMLResponse("<h2>Invalid state — possible CSRF.</h2>", status_code=400)

    msal_app = get_msal_app()
    if msal_app is None:
        return HTMLResponse("msal not installed", status_code=500)

    result = msal_app.acquire_token_by_authorization_code(
        code, scopes=SCOPES, redirect_uri=REDIRECT_URI
    )

    if "error" in result:
        return HTMLResponse(
            f"<h2>Token error</h2><p>{result.get('error_description')}</p>",
            status_code=400,
        )

    claims = result.get("id_token_claims", {})
    request.session["user"] = {
        "name": claims.get("name", "Unknown"),
        "upn":  claims.get("upn") or claims.get("preferred_username", ""),
        "oid":  claims.get("oid", ""),
    }

    next_url = request.session.pop("auth_next", "/")
    return RedirectResponse(next_url or "/")


async def handle_logout(request: Request) -> RedirectResponse:
    """Clear the session and redirect to Microsoft logout."""
    request.session.clear()
    if SSO_ENABLED:
        post_logout = REDIRECT_URI.rsplit("/auth/", 1)[0] + "/"
        ms_logout = (
            f"https://login.microsoftonline.com/{AZURE_TENANT_ID}"
            f"/oauth2/v2.0/logout?post_logout_redirect_uri={post_logout}"
        )
        return RedirectResponse(ms_logout)
    return RedirectResponse("/")

"""SOTI MobiControl FastAPI router."""

from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from ..services import soti as soti_svc

router = APIRouter(prefix="/soti", tags=["soti"])

BASE_DIR = Path(__file__).resolve().parent.parent
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# ---------------------------------------------------------------------------
# In-memory session store: { session_id: { token, tenant_url } }
# This is intentionally simple — suitable for a single-operator tool.
# ---------------------------------------------------------------------------
_sessions: dict[str, dict] = {}
_SESSION_COOKIE = "soti_session"


def _get_session(request: Request) -> dict | None:
    sid = request.cookies.get(_SESSION_COOKIE)
    return _sessions.get(sid) if sid else None


# ---------------------------------------------------------------------------
# HTML page
# ---------------------------------------------------------------------------

@router.get("/", response_class=HTMLResponse)
async def soti_page(request: Request):
    session = _get_session(request)
    return templates.TemplateResponse(
        "soti.html",
        {"request": request, "connected": session is not None},
    )


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

@router.post("/login")
async def soti_login(request: Request):
    form = await request.form()
    tenant_url    = str(form.get("tenant_url", "")).strip()
    username      = str(form.get("username", "")).strip()
    password      = str(form.get("password", ""))
    client_id     = str(form.get("client_id", "")).strip()
    client_secret = str(form.get("client_secret", ""))

    if not all([tenant_url, username, password, client_id, client_secret]):
        return JSONResponse({"error": "All fields are required."}, status_code=400)

    # Basic URL validation — must start with https://
    if not tenant_url.startswith("https://"):
        return JSONResponse(
            {"error": "Tenant URL must start with https://"}, status_code=400
        )

    try:
        token_data = await soti_svc.get_token(
            tenant_url, username, password, client_id, client_secret
        )
    except soti_svc.SotiClientError as exc:
        return JSONResponse({"error": str(exc)}, status_code=401)
    except Exception as exc:
        return JSONResponse(
            {"error": f"Connection error: {exc}"}, status_code=502
        )

    sid = uuid.uuid4().hex
    _sessions[sid] = {
        "token": token_data["access_token"],
        "tenant_url": tenant_url,
    }
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        _SESSION_COOKIE, sid, httponly=True, samesite="lax", secure=False
    )
    return resp


@router.post("/logout")
async def soti_logout(request: Request):
    sid = request.cookies.get(_SESSION_COOKIE)
    if sid:
        _sessions.pop(sid, None)
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(_SESSION_COOKIE)
    return resp


# ---------------------------------------------------------------------------
# Device groups (folder tree)
# ---------------------------------------------------------------------------

@router.get("/folders")
async def soti_folders(request: Request):
    session = _get_session(request)
    if not session:
        return JSONResponse({"error": "Not authenticated."}, status_code=401)
    try:
        groups = await soti_svc.get_device_groups(
            session["tenant_url"], session["token"]
        )
        return JSONResponse(groups)
    except soti_svc.SotiClientError as exc:
        return JSONResponse({"error": str(exc)}, status_code=401)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


# ---------------------------------------------------------------------------
# Devices in a group
# ---------------------------------------------------------------------------

@router.get("/devices")
async def soti_devices(request: Request, group_path: str = "/"):
    session = _get_session(request)
    if not session:
        return JSONResponse({"error": "Not authenticated."}, status_code=401)
    try:
        devices = await soti_svc.get_devices_in_group(
            session["tenant_url"], session["token"], group_path
        )
        return JSONResponse(devices)
    except soti_svc.SotiClientError as exc:
        return JSONResponse({"error": str(exc)}, status_code=401)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)


# ---------------------------------------------------------------------------
# Trigger remote log search on selected devices
# ---------------------------------------------------------------------------

@router.post("/search")
async def soti_search(request: Request):
    session = _get_session(request)
    if not session:
        return JSONResponse({"error": "Not authenticated."}, status_code=401)

    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "Invalid JSON body."}, status_code=400)

    raw_query  = str(body.get("query", "")).strip()
    device_ids = body.get("device_ids", [])

    if not raw_query:
        return JSONResponse({"error": "Search query is required."}, status_code=400)
    if not device_ids or not isinstance(device_ids, list):
        return JSONResponse({"error": "No devices selected."}, status_code=400)

    results: dict[str, dict] = {}
    for device_id in device_ids:
        if not isinstance(device_id, str) or not device_id.strip():
            continue
        try:
            job = await soti_svc.trigger_log_search(
                session["tenant_url"], session["token"], device_id.strip(), raw_query
            )
            results[device_id] = {"status": "triggered", "job": job}
        except soti_svc.SotiClientError as exc:
            results[device_id] = {"status": "error", "error": str(exc)}
        except Exception as exc:
            results[device_id] = {"status": "error", "error": str(exc)}

    return JSONResponse(results)


# ---------------------------------------------------------------------------
# Poll script job result
# ---------------------------------------------------------------------------

@router.get("/jobs/{device_id}/{job_id}")
async def soti_job_result(request: Request, device_id: str, job_id: str):
    session = _get_session(request)
    if not session:
        return JSONResponse({"error": "Not authenticated."}, status_code=401)
    try:
        result = await soti_svc.get_job_result(
            session["tenant_url"], session["token"], device_id, job_id
        )
        return JSONResponse(result)
    except soti_svc.SotiClientError as exc:
        return JSONResponse({"error": str(exc)}, status_code=401)
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=502)

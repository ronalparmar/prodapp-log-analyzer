"""
SOTI MobiControl Web API async client.

Handles OAuth2 ROPC authentication, device group discovery,
device listing, and remote log-search via script jobs.
"""

from __future__ import annotations

import urllib.parse
import re

import httpx

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SotiClientError(Exception):
    """Raised for known SOTI API errors (auth failure, bad request, etc.)."""


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

async def get_token(
    tenant_url: str,
    username: str,
    password: str,
    client_id: str,
    client_secret: str,
) -> dict:
    """Authenticate via OAuth2 ROPC and return the full token response dict."""
    token_url = tenant_url.rstrip("/") + "/MobiControl/api/token"
    data = {
        "grant_type": "password",
        "username": username,
        "password": password,
        "client_id": client_id,
        "client_secret": client_secret,
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(
            token_url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
    if resp.status_code == 401:
        raise SotiClientError("Authentication failed: invalid credentials or client ID/secret.")
    if not resp.is_success:
        raise SotiClientError(f"Authentication failed: HTTP {resp.status_code} — {resp.text[:200]}")
    return resp.json()


# ---------------------------------------------------------------------------
# Device groups (folder tree)
# ---------------------------------------------------------------------------

async def get_device_groups(tenant_url: str, token: str) -> list[dict]:
    """Fetch the device group/folder tree from SOTI MobiControl."""
    url = tenant_url.rstrip("/") + "/MobiControl/api/devicegroups"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code == 401:
        raise SotiClientError("Session expired — please reconnect.")
    if not resp.is_success:
        raise SotiClientError(f"Failed to fetch device groups: HTTP {resp.status_code}")
    data = resp.json()
    # Normalise: API may return a single root object or an array
    return data if isinstance(data, list) else [data]


# ---------------------------------------------------------------------------
# Devices in a group
# ---------------------------------------------------------------------------

async def get_devices_in_group(
    tenant_url: str,
    token: str,
    group_path: str,
) -> list[dict]:
    """List all devices under a device group path."""
    url = tenant_url.rstrip("/") + "/MobiControl/api/devices"
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    params = {"deviceGroupPath": group_path}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, params=params, headers=headers)
    if resp.status_code == 401:
        raise SotiClientError("Session expired — please reconnect.")
    if not resp.is_success:
        raise SotiClientError(f"Failed to fetch devices: HTTP {resp.status_code}")
    data = resp.json()
    # Normalise: could be list or wrapper object
    if isinstance(data, list):
        return data
    return data.get("Devices") or data.get("devices") or []


# ---------------------------------------------------------------------------
# Remote log search via RunScript job
# ---------------------------------------------------------------------------

_SAFE_QUERY_RE = re.compile(r"^[A-Za-z0-9._\-]{1,100}$")


def _validate_query(query: str) -> str:
    """Validate and return the query; raise SotiClientError if unsafe."""
    q = query.strip()
    if not _SAFE_QUERY_RE.match(q):
        raise SotiClientError(
            "Invalid search query. Only letters, digits, dots, hyphens and "
            "underscores are allowed (1–100 characters)."
        )
    return q


async def trigger_log_search(
    tenant_url: str,
    token: str,
    device_id: str,
    raw_query: str,
) -> dict:
    """
    Push a shell script to the device that greps
    /sdcard/com.postenbring.*/logs/*.txt for the supplied query.

    Returns the SOTI job object (contains JobId for polling).
    """
    query = _validate_query(raw_query)

    # The script:
    # 1. Finds all log files matching the Posten Bring package path
    # 2. Greps each file for the query (up to 20 matching lines per file)
    # 3. Prints FILE:<path> before each file's results
    # 4. Prints NO_MATCH if nothing is found
    script = (
        'FILES=$(find /sdcard -path "*/com.postenbring*/logs/*.txt" 2>/dev/null); '
        'FOUND=0; '
        'for f in $FILES; do '
        '  MATCHES=$(grep -n "' + query + '" "$f" 2>/dev/null | head -20); '
        '  if [ -n "$MATCHES" ]; then FOUND=1; echo "FILE:$f"; echo "$MATCHES"; fi; '
        'done; '
        'if [ "$FOUND" -eq 0 ]; then echo "NO_MATCH"; fi'
    )

    url = (
        tenant_url.rstrip("/")
        + "/MobiControl/api/devices/"
        + urllib.parse.quote(device_id, safe="")
        + "/actions"
    )
    payload = {"action": "RunScript", "script": script}
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.post(url, json=payload, headers=headers)
    if resp.status_code == 401:
        raise SotiClientError("Session expired — please reconnect.")
    if not resp.is_success:
        raise SotiClientError(
            f"Failed to send script to device {device_id}: HTTP {resp.status_code}"
        )
    return resp.json()


# ---------------------------------------------------------------------------
# Poll job result
# ---------------------------------------------------------------------------

async def get_job_result(
    tenant_url: str,
    token: str,
    device_id: str,
    job_id: str,
) -> dict:
    """Fetch the current status/output of a previously triggered script job."""
    url = (
        tenant_url.rstrip("/")
        + "/MobiControl/api/devices/"
        + urllib.parse.quote(device_id, safe="")
        + "/jobs/"
        + urllib.parse.quote(job_id, safe="")
    )
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    async with httpx.AsyncClient(timeout=30) as client:
        resp = await client.get(url, headers=headers)
    if resp.status_code == 401:
        raise SotiClientError("Session expired — please reconnect.")
    if not resp.is_success:
        raise SotiClientError(f"Failed to fetch job result: HTTP {resp.status_code}")
    return resp.json()

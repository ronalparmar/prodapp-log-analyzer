# SOTI MobiControl Cloud API – Integration Guide

This document describes how to obtain SOTI MobiControl Cloud API credentials and use them to retrieve device information relevant to log collection from Posten Bring TC57 devices.

---

## Overview

SOTI MobiControl is a Mobile Device Management (MDM) platform. The Cloud version provides a REST API (SOTI MobiControl Web API) to query device inventory, device properties, and trigger actions on managed devices.

> **Note:** Even with full API access, directly browsing `/sdcard` paths on devices via the MobiControl API is typically not supported. The recommended approach is to use a **device-side collector app** (separate solution) that packages logs and uploads them to this analyzer.

---

## What Information You Need

To use the SOTI MobiControl Cloud API you will need:

| Item | Description | Example |
|------|-------------|---------|
| **Tenant base URL** | Your SOTI Cloud tenant hostname | `https://your-tenant.soticloud.com` |
| **Client ID** | OAuth2 client identifier | `shopapp-log-collector` |
| **Client Secret** | OAuth2 client secret | (generated in SOTI console) |
| **Scope** | API scope | `api` |
| **Token endpoint** | OAuth2 token URL | `https://your-tenant.soticloud.com/MobiControl/api/token` |

---

## Step 1: Find Your Tenant URL

1. Log in to the SOTI MobiControl web console as an **administrator**.
2. Look at the browser address bar — your tenant URL is the base domain, e.g.:
   `https://acme.soticloud.com`
3. The API base URL is typically:
   `https://<tenant>.soticloud.com/MobiControl/api/`

---

## Step 2: Generate API Credentials

SOTI MobiControl uses **OAuth 2.0 (Resource Owner Password Credentials)** or **Client Credentials** flow depending on your version.

### Option A: API User credentials (most common)

1. In SOTI MobiControl console, go to **Administration** → **API Access** (or **Web API**).
2. Create a new **API user** (or use an existing admin account).
3. Note the **username** and **password**.
4. Use these with the token endpoint below.

### Option B: Client Credentials (newer versions)

1. Go to **Administration** → **API Clients** → **Add Client**.
2. Set a **Client ID** (e.g., `shopapp-log-collector`).
3. Generate a **Client Secret** (copy it — it won't be shown again).
4. Assign appropriate **permissions/scopes** (at minimum: `device.read`).

> Contact your SOTI tenant administrator if you do not see these options — some features require specific licensing tiers.

---

## Step 3: Obtain an Access Token

### Using Resource Owner Password Credentials (ROPC)

```bash
curl -X POST "https://<tenant>.soticloud.com/MobiControl/api/token" \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "grant_type=password" \
  -d "username=<api_username>" \
  -d "password=<api_password>" \
  -d "client_id=<client_id>" \
  -d "client_secret=<client_secret>"
```

**Response:**
```json
{
  "access_token": "eyJ...",
  "token_type": "Bearer",
  "expires_in": 3600
}
```

---

## Step 4: Common API Tasks

### List All Devices

```bash
curl -X GET "https://<tenant>.soticloud.com/MobiControl/api/devices" \
  -H "Authorization: Bearer <access_token>" \
  -H "Accept: application/json"
```

**Response** (excerpt):
```json
[
  {
    "DeviceId": "TC5705956",
    "DeviceName": "TC57-Norway-01",
    "OSType": "Android",
    "OSVersion": "10",
    "DeviceManufacturer": "Zebra",
    "DeviceModel": "TC57",
    "IsOnline": true,
    "LastConnectTime": "2026-02-18T09:30:00Z"
  }
]
```

### Get Device Properties

```bash
curl -X GET "https://<tenant>.soticloud.com/MobiControl/api/devices/<DeviceId>" \
  -H "Authorization: Bearer <access_token>"
```

### Search Devices by Group

```bash
curl -X GET "https://<tenant>.soticloud.com/MobiControl/api/devices?deviceGroupPath=/Norway/TC57" \
  -H "Authorization: Bearer <access_token>"
```

### Send a Script/Job to a Device

```bash
curl -X POST "https://<tenant>.soticloud.com/MobiControl/api/devices/<DeviceId>/actions" \
  -H "Authorization: Bearer <access_token>" \
  -H "Content-Type: application/json" \
  -d '{
    "action": "RunScript",
    "script": "am start -n com.postenbring.logcollector/.MainActivity"
  }'
```

---

## Browsing Device Storage (`/sdcard`) — Important Limitation

**SOTI MobiControl's REST API does not provide a file browser for device internal/external storage.** Common workarounds:

| Approach | Notes |
|----------|-------|
| **Device-side collector app** (recommended) | An Android app running on the TC57 discovers log files under `/sdcard/com.postenbring.*/logs/*.txt`, zips them, and POSTs to this analyzer's `/api/v1/uploads` endpoint. This is the standard, reliable approach. |
| **SOTI Script deployment** | Use MobiControl to push and execute a shell script on the device that zips and uploads logs. Requires shell access and appropriate device policy. |
| **MobiControl Content Library** | If the collector app saves files to a known content library sync folder, MobiControl can pull them — but this requires explicit configuration. |
| **Direct ADB (USB/TCP)** | For ad-hoc debugging only; not scalable for fleet use. |

---

## Required Permissions / Roles

For API access, the API user/client needs at minimum:

- `devices.read` — list and query devices
- `devices.manage` (optional) — send jobs/scripts
- `reports.read` (optional) — access built-in reports

Consult your SOTI tenant admin to grant appropriate roles.

---

## References

- [SOTI MobiControl REST API Documentation](https://www.soti.net/mc/help/v15.3/en/console/webservices/)
- [SOTI Developer Portal](https://developer.soti.net/)
- [OAuth 2.0 for SOTI MobiControl](https://www.soti.net/mc/help/v15.3/en/console/webservices/authentication/)

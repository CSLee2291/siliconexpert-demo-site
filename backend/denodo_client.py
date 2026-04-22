"""Denodo fallback for PNs not in the Excel.

Queries iv_plm_allparts_latest via Denodo's REST Web Service. The REST endpoint
format varies across Denodo deployments; we try the documented path and fall
back to the OData-style variant if that 404s. The user should verify
DENODO_REST_BASE_URL if both fail.
"""
from __future__ import annotations

import os
from typing import Any

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _base() -> str:
    use_dev = str(os.getenv("DENODO_REST_USE_DEV_API", "false")).lower() == "true"
    if use_dev:
        return os.getenv("DENODO_REST_DEV_BASE_URL", "").rstrip("/")
    return os.getenv("DENODO_REST_BASE_URL", "").rstrip("/")


def _auth() -> tuple[str, str]:
    use_dev = str(os.getenv("DENODO_REST_USE_DEV_API", "false")).lower() == "true"
    if use_dev:
        return (
            os.getenv("DENODO_REST_DEV_USERNAME", ""),
            os.getenv("DENODO_REST_DEV_PASSWORD", ""),
        )
    return (
        os.getenv("DENODO_REST_USERNAME", ""),
        os.getenv("DENODO_REST_PASSWORD", ""),
    )


def _verify_ssl() -> bool:
    return str(os.getenv("DENODO_REST_VERIFY_SSL", "false")).lower() == "true"


VIEW = "iv_plm_allparts_latest"


class DenodoError(Exception):
    """Raised when the Denodo REST service is unreachable or misbehaving.

    The caller uses this to distinguish genuine "not found" (no exception,
    returns None) from a broken connection (exception). SiliconExpert-only
    flows should still work when this exception is raised.
    """


def is_configured() -> bool:
    """True when enough env is set to attempt a Denodo call."""
    return bool(_base() and _auth()[0])


def find_item_ex(item_number: str) -> tuple[dict | None, str | None]:
    """Look up a single Item_Number.

    Returns `(row, error)`:
      * `(dict, None)` — matched row
      * `(None, None)` — Denodo queried successfully, no match
      * `(None, "…")`  — connection / auth / protocol failure
    """
    base = _base()
    if not base:
        # Not configured: treat as "feature not available", not an error.
        return None, None
    user, pw = _auth()
    params_common = {"$format": "JSON"}
    safe = item_number.replace("'", "''")

    candidates = [
        (f"{base}/views/{VIEW}", {"$filter": f"Item_Number = '{safe}'", **params_common}),
        (f"{base}/{VIEW}", {"$filter": f"Item_Number = '{safe}'", **params_common}),
    ]

    last_http_error: str | None = None
    connection_errors: list[str] = []

    for url, params in candidates:
        try:
            r = requests.get(
                url,
                params=params,
                auth=(user, pw),
                verify=_verify_ssl(),
                timeout=30,
            )
        except requests.RequestException as exc:
            # Network / DNS / TLS / timeout — try next candidate, collect.
            connection_errors.append(f"{type(exc).__name__}: {exc}")
            continue
        if r.status_code == 404:
            # Path doesn't exist at this URL shape — try next candidate.
            continue
        if r.status_code in (401, 403):
            return None, f"Denodo auth failed ({r.status_code})"
        if not r.ok:
            last_http_error = f"Denodo HTTP {r.status_code}"
            continue
        try:
            j = r.json()
        except ValueError:
            last_http_error = "Denodo returned non-JSON response"
            continue

        rows = _extract_rows(j)
        if rows:
            return rows[0], None
        # Query succeeded, simply no match — explicitly a non-error.
        return None, None

    # All candidates failed. Decide between "unreachable" and "no match".
    if connection_errors:
        return None, "Denodo unreachable · " + connection_errors[0]
    if last_http_error:
        return None, last_http_error
    # All paths returned 404 — the view simply doesn't serve the part.
    return None, None


def find_item(item_number: str) -> dict | None:
    """Back-compat wrapper: ignore connection errors, return None."""
    row, _err = find_item_ex(item_number)
    return row


def _extract_rows(j: Any) -> list[dict]:
    if isinstance(j, list):
        return [row for row in j if isinstance(row, dict)]
    if isinstance(j, dict):
        for key in ("elements", "value", "rows", "result"):
            v = j.get(key)
            if isinstance(v, list):
                return [row for row in v if isinstance(row, dict)]
    return []

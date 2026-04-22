"""SiliconExpert ProductAPI client.

Per-request sessions so concurrent users don't share cookies (see
SiliconExpertAPIImplementation.md).
"""
from __future__ import annotations

import os
from typing import Any

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


def _base() -> str:
    return os.getenv(
        "SILICONEXPERT_API_BASE", "https://api.siliconexpert.com/ProductAPI/search"
    ).rstrip("/")


def _creds() -> dict[str, str]:
    login = os.getenv("SILICONEXPERT_LOGIN", "")
    key = os.getenv("SILICONEXPERT_API_KEY", "")
    if not login or not key:
        raise RuntimeError("SILICONEXPERT_LOGIN / SILICONEXPERT_API_KEY not set")
    return {"login": login, "apiKey": key}


def _new_session() -> requests.Session:
    s = requests.Session()
    s.verify = False  # corp proxy tolerance
    return s


def _authenticate(session: requests.Session) -> tuple[bool, dict]:
    url = f"{_base()}/authenticateUser"
    r = session.post(url, params=_creds(), timeout=30)
    r.raise_for_status()
    j = r.json()
    ok = str(j.get("Status", {}).get("Success", "")).lower() == "true"
    return ok, j


def _form() -> dict[str, str]:
    c = _creds()
    return {"login": c["login"], "apiKey": c["apiKey"], "fmt": "json"}


def part_search(part_number: str, manufacturer: str | None = None) -> dict:
    """GET-equivalent wrapper for /partsearch."""
    s = _new_session()
    ok, auth = _authenticate(s)
    if not ok:
        return {"error": "auth_failed", "auth": auth}

    url = f"{_base()}/partsearch"
    data = {**_form(), "partNumber": part_number}
    if manufacturer:
        data["manufacturer"] = manufacturer
    r = s.post(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def list_part_search(parts: list[dict[str, str]]) -> dict:
    """POST /listPartSearch. parts = [{partNumber, manufacturer?}, ...]."""
    import json as _json

    s = _new_session()
    ok, auth = _authenticate(s)
    if not ok:
        return {"error": "auth_failed", "auth": auth}

    url = f"{_base()}/listPartSearch"
    data = {**_form(), "partNumber": _json.dumps(parts)}
    r = s.post(
        url,
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def part_detail(com_ids: list[str], *, lifecycle: bool = True) -> dict:
    """POST /partDetail. Batches of 50 ComIDs.

    The real response returns `Results.ResultDto` as a single object for a
    single-ComID request and a list for multi. Normalize both shapes to a list.
    """
    if not com_ids:
        return {"Results": {"ResultDto": []}}

    out: list[dict] = []
    for i in range(0, len(com_ids), 50):
        batch = [c for c in com_ids[i : i + 50] if c]
        if not batch:
            continue
        s = _new_session()
        ok, auth = _authenticate(s)
        if not ok:
            return {"error": "auth_failed", "auth": auth}

        url = f"{_base()}/partDetail"
        data = {
            **_form(),
            "comIds": ",".join(str(c) for c in batch),
        }
        if lifecycle:
            data["getLifeCycleData"] = "1"
        r = s.post(
            url,
            data=data,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            timeout=90,
        )
        r.raise_for_status()
        j = r.json()
        dto = j.get("Results", {}).get("ResultDto")
        if isinstance(dto, list):
            out.extend(x for x in dto if isinstance(x, dict))
        elif isinstance(dto, dict):
            out.append(dto)

    return {"Results": {"ResultDto": out}}


def get_all_taxonomy() -> dict:
    """POST /parametric/getAllTaxonomy — full category tree."""
    s = _new_session()
    ok, auth = _authenticate(s)
    if not ok:
        return {"error": "auth_failed", "auth": auth}
    r = s.post(
        f"{_base()}/parametric/getAllTaxonomy",
        data=_form(),
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=90,
    )
    r.raise_for_status()
    return r.json()


def get_pl_features(pl_name: str, page: int = 1) -> dict:
    """POST /parametric/getPlFeatures — feature filters for a product line."""
    s = _new_session()
    ok, auth = _authenticate(s)
    if not ok:
        return {"error": "auth_failed", "auth": auth}
    data = {**_form(), "plName": pl_name, "pageNumber": str(page)}
    r = s.post(
        f"{_base()}/parametric/getPlFeatures",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=90,
    )
    r.raise_for_status()
    return r.json()


def get_search_result(pl_name: str, page: int = 1) -> dict:
    """POST /parametric/getSearchResult — parts within a product line."""
    s = _new_session()
    ok, auth = _authenticate(s)
    if not ok:
        return {"error": "auth_failed", "auth": auth}
    data = {**_form(), "plName": pl_name, "pageNumber": str(page)}
    r = s.post(
        f"{_base()}/parametric/getSearchResult",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=90,
    )
    r.raise_for_status()
    return r.json()


def manufacturers(mfr: str) -> dict:
    """POST /manufacturers — fuzzy manufacturer search."""
    s = _new_session()
    ok, auth = _authenticate(s)
    if not ok:
        return {"error": "auth_failed", "auth": auth}
    r = s.post(
        f"{_base()}/manufacturers",
        data={**_form(), "mfr": mfr},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def pcn(com_id: str | None = None, part_number: str | None = None) -> dict:
    """POST /pcn — product change notifications.

    Accepts either a ComID (preferred) or a partNumber.
    """
    s = _new_session()
    ok, auth = _authenticate(s)
    if not ok:
        return {"error": "auth_failed", "auth": auth}
    data: dict[str, str] = {**_form()}
    if com_id:
        data["comIds"] = str(com_id)
    elif part_number:
        data["partNumber"] = part_number
    else:
        return {"error": "missing comId or partNumber"}
    r = s.post(
        f"{_base()}/pcn",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def xref(parts: list[dict]) -> dict:
    """POST /xref — cross-reference search.

    `parts` is a list of dicts with either `partNumber` or `comId` keys, e.g.
        [{"partNumber": "bav99"}]
        [{"comId": "32701591"}]
        [{"partNumber": "X", "manufacturer": "Y"}]
    """
    import json as _json

    s = _new_session()
    ok, auth = _authenticate(s)
    if not ok:
        return {"error": "auth_failed", "auth": auth}
    data = {**_form(), "parts": _json.dumps(parts)}
    r = s.post(
        f"{_base()}/xref",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=90,
    )
    r.raise_for_status()
    return r.json()


def supplier_profile(manufacturer_name: str) -> dict:
    """POST /supplierProfile — detailed manufacturer profile (URL, HQ, parts
    count, acquisitions, etc.)."""
    s = _new_session()
    ok, auth = _authenticate(s)
    if not ok:
        return {"error": "auth_failed", "auth": auth}
    r = s.post(
        f"{_base()}/supplierProfile",
        data={**_form(), "manufacturerName": manufacturer_name},
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=60,
    )
    r.raise_for_status()
    return r.json()


def user_status() -> dict:
    s = _new_session()
    ok, auth = _authenticate(s)
    if not ok:
        return {"error": "auth_failed", "auth": auth}
    r = s.post(f"{_base()}/userStatus", data=_form(), timeout=30)
    r.raise_for_status()
    return r.json()


def resolve_comid(mpn: str, manufacturer: str | None = None) -> str | None:
    """Try to resolve a ComID via /partsearch using MPN (+ manufacturer).

    /partsearch returns `Result` as a single object for one match or a list
    for multiple — normalize both shapes here.
    """
    try:
        j = part_search(mpn, manufacturer)
    except Exception:
        return None
    results = j.get("Result")
    if isinstance(results, dict):
        results = [results]
    elif not isinstance(results, list):
        results = []
    if not results:
        return None
    if manufacturer:
        mfr = manufacturer.strip().lower()
        # fuzzy compare: prefix match either way ("Infineon Technologies"
        # vs "Infineon Technologies AG")
        for row in results:
            row_mfr = str(row.get("Manufacturer", "")).strip().lower()
            if row_mfr == mfr or row_mfr.startswith(mfr) or mfr.startswith(row_mfr):
                cid = str(row.get("ComID", "")).strip()
                if cid:
                    return cid
    cid = str(results[0].get("ComID", "")).strip()
    return cid or None

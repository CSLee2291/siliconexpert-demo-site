"""Load V_SE_MPN_LIST*.xlsx and expose PN / MPN lookups.

Columns: PN, MPN, Manufacturer, Status, SE_ComID
PN == Advantech Item_Number. SE_ComID may be blank.
"""
from __future__ import annotations

import os
import threading
from typing import Any

import openpyxl


_lock = threading.Lock()
_state: dict[str, Any] = {"loaded": False, "by_pn": {}, "by_mpn": {}, "rows": []}


def _norm(v: Any) -> str:
    if v is None:
        return ""
    return str(v).strip()


def _norm_key(v: Any) -> str:
    return _norm(v).upper()


def _row_to_dict(row: tuple) -> dict:
    pn, mpn, mfr, status, comid = row
    return {
        "pn": _norm(pn),
        "mpn": _norm(mpn),
        "manufacturer": _norm(mfr),
        "status": _norm(status),
        "comId": _norm(comid),
    }


def load(path: str | None = None) -> dict:
    """Load the Excel into memory (idempotent, thread-safe)."""
    with _lock:
        if _state["loaded"]:
            return _state
        xlsx_path = path or os.getenv(
            "EXCEL_PATH", "./acl_pn_comID/V_SE_MPN_LIST20260128.xlsx"
        )
        if not os.path.exists(xlsx_path):
            raise FileNotFoundError(f"Excel file not found: {xlsx_path}")

        wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
        ws = wb.active
        rows: list[dict] = []
        by_pn: dict[str, list[dict]] = {}
        by_mpn: dict[str, list[dict]] = {}

        it = ws.iter_rows(values_only=True)
        header = next(it, None)
        expected = ("PN", "MPN", "Manufacturer", "Status", "SE_ComID")
        if tuple(_norm(h) for h in (header or ())) != expected:
            raise ValueError(f"Unexpected Excel header: {header!r}")

        for raw in it:
            if not raw:
                continue
            d = _row_to_dict(raw)
            if not d["pn"] and not d["mpn"]:
                continue
            rows.append(d)
            if d["pn"]:
                by_pn.setdefault(_norm_key(d["pn"]), []).append(d)
            if d["mpn"]:
                by_mpn.setdefault(_norm_key(d["mpn"]), []).append(d)

        wb.close()
        _state.update({"loaded": True, "by_pn": by_pn, "by_mpn": by_mpn, "rows": rows})
        return _state


def find_by_pn(pn: str) -> list[dict]:
    load()
    return list(_state["by_pn"].get(_norm_key(pn), []))


def find_by_mpn(mpn: str) -> list[dict]:
    load()
    return list(_state["by_mpn"].get(_norm_key(mpn), []))


def search(query: str) -> list[dict]:
    """Find rows where query matches PN or MPN exactly (case-insensitive)."""
    q = _norm_key(query)
    if not q:
        return []
    hits = find_by_pn(q)
    if not hits:
        hits = find_by_mpn(q)
    return hits


def stats() -> dict:
    load()
    return {
        "rows": len(_state["rows"]),
        "distinctPN": len(_state["by_pn"]),
        "distinctMPN": len(_state["by_mpn"]),
    }

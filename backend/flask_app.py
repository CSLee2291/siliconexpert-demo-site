"""Flask backend — serves public/index.html + /api/*."""
from __future__ import annotations

import os
import sys
from pathlib import Path

# Load environment before importing service layer (which reads env at import time).
# `.env` is the production file (gitignored, copied from .env.template).
# `.env.development` is kept only as a legacy fallback for existing checkouts.
ROOT = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv

    for candidate in (ROOT / ".env", ROOT / ".env.development"):
        if candidate.exists():
            load_dotenv(candidate)
            break
except ImportError:
    pass

# Ensure parent dir is importable as a package.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from flask import Flask, jsonify, request, send_from_directory  # noqa: E402

from backend import excel_lookup, recent_store, service, siliconexpert  # noqa: E402


PUBLIC_DIR = ROOT / "public"


def create_app() -> Flask:
    app = Flask(__name__, static_folder=None)

    # Warm the excel cache at startup when requested.
    if os.getenv("EXCEL_LOAD_STRATEGY", "startup").lower() == "startup":
        excel_lookup.load()
    recent_store.init()

    @app.get("/")
    def index():  # noqa: ANN202
        return send_from_directory(PUBLIC_DIR, "index.html")

    @app.get("/<path:filename>")
    def static_files(filename):  # noqa: ANN202, ANN001
        return send_from_directory(PUBLIC_DIR, filename)

    @app.get("/api/health")
    def health():  # noqa: ANN202
        return jsonify(
            {
                "ok": True,
                "excel": excel_lookup.stats(),
                "mode": "flask",
            }
        )

    @app.get("/api/search")
    def api_search():  # noqa: ANN202
        q = request.args.get("q", "").strip()
        return jsonify(service.search(q))

    @app.route("/api/bulk", methods=["GET", "POST"])
    def api_bulk():  # noqa: ANN202
        # Accept either a comma/newline-separated string in `pns` (query or
        # form) or a JSON body { "pns": "..." | [...] }.
        raw = ""
        if request.method == "POST":
            body = request.get_json(silent=True) or {}
            pns_field = body.get("pns")
            if isinstance(pns_field, list):
                raw = "\n".join(str(x) for x in pns_field)
            elif isinstance(pns_field, str):
                raw = pns_field
            else:
                raw = request.form.get("pns", "") or ""
        if not raw:
            raw = request.args.get("pns", "") or ""
        if not raw.strip():
            return jsonify({"error": "missing pns"}), 400
        return jsonify(service.bulk_search(raw))

    @app.get("/api/detail")
    def api_detail():  # noqa: ANN202
        com_id = request.args.get("comId") or request.args.get("comid") or None
        pn = request.args.get("pn") or None
        if not com_id and not pn:
            return jsonify({"error": "missing pn or comId"}), 400
        return jsonify(service.detail(pn=pn, com_id=com_id))

    @app.get("/api/recent")
    def api_recent():  # noqa: ANN202
        try:
            limit = max(1, min(int(request.args.get("limit", "10") or "10"), 50))
        except ValueError:
            limit = 10
        return jsonify({"items": recent_store.list_recent(limit)})

    @app.delete("/api/recent")
    def api_recent_clear():  # noqa: ANN202
        return jsonify({"cleared": recent_store.clear()})

    @app.get("/api/user-status")
    def api_user_status():  # noqa: ANN202
        try:
            return jsonify(siliconexpert.user_status())
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": repr(exc)}), 500

    _taxonomy_cache: dict[str, object] = {}

    @app.get("/api/taxonomy")
    def api_taxonomy():  # noqa: ANN202
        # Cached because it's a ~120KB full catalog tree that rarely changes.
        if "data" not in _taxonomy_cache:
            try:
                _taxonomy_cache["data"] = siliconexpert.get_all_taxonomy()
            except Exception as exc:  # noqa: BLE001
                return jsonify({"error": repr(exc)}), 500
        return jsonify(_taxonomy_cache["data"])

    @app.get("/api/features")
    def api_features():  # noqa: ANN202
        pl = request.args.get("plName", "").strip()
        page = int(request.args.get("page", "1") or "1")
        if not pl:
            return jsonify({"error": "missing plName"}), 400
        try:
            return jsonify(siliconexpert.get_pl_features(pl, page))
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": repr(exc)}), 500

    @app.get("/api/browse")
    def api_browse():  # noqa: ANN202
        pl = request.args.get("plName", "").strip()
        page = int(request.args.get("page", "1") or "1")
        if not pl:
            return jsonify({"error": "missing plName"}), 400
        try:
            return jsonify(siliconexpert.get_search_result(pl, page))
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": repr(exc)}), 500

    @app.get("/api/mfr")
    def api_mfr():  # noqa: ANN202
        q = request.args.get("q", "").strip()
        if not q:
            return jsonify({"resultSize": "0", "Result": {"MfrDto": []}})
        try:
            return jsonify(siliconexpert.manufacturers(q))
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": repr(exc)}), 500

    @app.get("/api/pcn")
    def api_pcn():  # noqa: ANN202
        com_id = (request.args.get("comId") or "").strip()
        pn = (request.args.get("pn") or "").strip()
        # If PN provided and maps to Excel with a ComID, prefer that.
        if pn and not com_id:
            rows = excel_lookup.find_by_pn(pn)
            if rows and rows[0].get("comId"):
                com_id = rows[0]["comId"]
        if not com_id and not pn:
            return jsonify({"error": "missing comId or pn"}), 400
        try:
            raw = siliconexpert.pcn(
                com_id=com_id or None,
                part_number=pn if not com_id else None,
            )
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": repr(exc)}), 500

        data = (raw.get("Result") or {}).get("PCNData") or {}
        dto = data.get("PCNDto") or []
        if isinstance(dto, dict):
            dto = [dto]
        rows_out: list[dict] = []
        for r in dto if isinstance(dto, list) else []:
            if not isinstance(r, dict):
                continue
            rows_out.append(
                {
                    "pcnNumber":       str(r.get("PCNNumber") or ""),
                    "manufacturer":    str(r.get("Manufacturer") or ""),
                    "typeOfChange":    str(r.get("TypeOfChange") or ""),
                    "description":     str(r.get("DescriptionOfChange") or ""),
                    "source":          str(r.get("Source") or r.get("PcnSource") or ""),
                    "notificationDate": str(r.get("NotificationDate") or ""),
                    "effectiveDate":   str(r.get("EffectiveDate") or ""),
                    "lastTimeBuyDate": str(r.get("LastTimeBuyDate") or ""),
                    "lastShipDate":    str(r.get("LastShipDate") or ""),
                    "affectedProduct": str(r.get("AffectedProductName") or ""),
                    "pcnId":           str(r.get("PCNId") or ""),
                }
            )
        return jsonify(
            {
                "status": "ok" if rows_out else "empty",
                "reason": ""
                if rows_out
                else "No PCNs returned by SE /pcn for this part",
                "reqComId": str(data.get("ReqComId") or com_id),
                "reqPartNumber": str(data.get("ReqPartNumber") or pn),
                "count": len(rows_out),
                "pcns": rows_out,
            }
        )

    @app.get("/api/xref")
    def api_xref():  # noqa: ANN202
        com_id = (request.args.get("comId") or "").strip()
        pn = (request.args.get("pn") or "").strip()
        mpn = (request.args.get("mpn") or "").strip()
        manufacturer = (request.args.get("manufacturer") or "").strip()
        if not com_id and not pn and not mpn:
            return jsonify({"error": "missing comId, pn, or mpn"}), 400

        # If a PN is provided and maps to the Excel with a ComID, prefer ComID.
        key: dict[str, str] = {}
        resolved_comid = com_id
        if pn and not com_id:
            rows = excel_lookup.find_by_pn(pn)
            if rows:
                if rows[0].get("comId"):
                    resolved_comid = rows[0]["comId"]
                elif not mpn:
                    mpn = rows[0].get("mpn") or ""
                    manufacturer = manufacturer or rows[0].get("manufacturer") or ""
        if resolved_comid:
            key = {"comId": resolved_comid}
        elif mpn:
            key = {"partNumber": mpn}
            if manufacturer:
                key["manufacturer"] = manufacturer
        elif pn:
            key = {"partNumber": pn}

        try:
            raw = siliconexpert.xref([key])
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": repr(exc)}), 500

        cross_data = (raw.get("Result") or {}).get("CrossData") or {}
        dto = cross_data.get("CrossDto") or []
        if isinstance(dto, dict):
            dto = [dto]

        def _f(v: object) -> float | None:
            try:
                return float(str(v or "").strip()) if str(v or "").strip() else None
            except ValueError:
                return None

        # Type codes from SE: A=exact, B=similar, C=functional, D=different
        # Compound codes carry a modifier: A/UPGRADE, A/DOWNGRADE, B/UPGRADE, …
        base_labels = {
            "A": "Exact",
            "B": "Similar",
            "C": "Functional",
            "D": "Different",
            "E": "Enhanced",
            "F": "Footprint",
            "G": "Direct",
        }

        def _label(code: str) -> str:
            if not code:
                return ""
            head, _, tail = code.partition("/")
            h = base_labels.get(head, head)
            return f"{h} · {tail.title()}" if tail else h

        rows_out: list[dict] = []
        seen_ids: set[str] = set()
        for r in dto:
            if not isinstance(r, dict):
                continue
            cross_id = str(r.get("CrossID") or "").strip()
            if cross_id and cross_id in seen_ids:
                continue
            if cross_id:
                seen_ids.add(cross_id)
            pricing = r.get("CrossPricingData") or {}
            t_code = str(r.get("Type") or "").strip().upper()
            rows_out.append(
                {
                    "crossId": cross_id,
                    "partNumber": str(r.get("CrossPartNumber") or ""),
                    "manufacturer": str(r.get("CrossManufacturer") or ""),
                    "lifecycle": str(r.get("CrossLifecycle") or ""),
                    "description": str(r.get("CrossDescription") or ""),
                    "datasheet": str(r.get("CrossDatasheet") or ""),
                    "rohs": str(r.get("CrossRoHSStatus") or ""),
                    "packaging": str(r.get("CrossPackaging") or ""),
                    "type": t_code,
                    "typeLabel": _label(t_code),
                    "comment": str(r.get("Comment") or ""),
                    "formFitFunction": str(r.get("FormFitFunction") or ""),
                    "replacementSource": str(r.get("ReplacementSource") or ""),
                    "pricing": {
                        "min": _f(pricing.get("MinimumPrice")),
                        "avg": _f(pricing.get("AveragePrice")),
                        "minLeadtime": str(pricing.get("MinLeadtime") or ""),
                        "maxLeadtime": str(pricing.get("Maxleadtime") or ""),
                    },
                }
            )

        return jsonify(
            {
                "status": "ok" if rows_out else "empty",
                "reason": "" if rows_out else (
                    "SiliconExpert /xref returned no cross references for this part"
                ),
                "reqPartNumber": str(cross_data.get("ReqPartNumber") or ""),
                "reqManufacturer": str(cross_data.get("ReqManufacturer") or ""),
                "reqComId": str(cross_data.get("ReqComId") or ""),
                "count": int(cross_data.get("CrossCount") or len(rows_out) or 0),
                "crosses": rows_out,
                "query": key,
            }
        )

    @app.get("/api/supplier")
    def api_supplier():  # noqa: ANN202
        name = request.args.get("name", "").strip()
        if not name:
            return jsonify({"error": "missing name"}), 400
        try:
            return jsonify(siliconexpert.supplier_profile(name))
        except Exception as exc:  # noqa: BLE001
            return jsonify({"error": repr(exc)}), 500

    return app


def main() -> None:
    port = int(os.getenv("BACKEND_PORT", "8000"))
    app = create_app()
    app.run(host="127.0.0.1", port=port, debug=True)


if __name__ == "__main__":
    main()

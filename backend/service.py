"""Compose Excel + SiliconExpert + Denodo into the UI contract.

Two endpoints:
  search(query) -> {query, hits: [Candidate], source}
  detail(com_id | pn) -> {part: Part, lifecycle: LifeCycle | None, raw: {...}}

Candidate and Part intentionally use shallow, UI-friendly keys.
"""
from __future__ import annotations

from typing import Any

from . import denodo_client, excel_lookup, recent_store, siliconexpert as se


def _record_hit(cand: dict, kind: str) -> None:
    """Best-effort telemetry write — the call is fail-soft in recent_store."""
    if not cand:
        return
    pn = (cand.get("pn") or "").strip()
    if not pn:
        return
    recent_store.record(
        pn=pn,
        mpn=cand.get("mpn") or "",
        manufacturer=cand.get("manufacturer") or "",
        com_id=cand.get("comId") or "",
        source=cand.get("source") or "",
        kind=kind,
    )


def _candidate_from_row(row: dict, *, source: str) -> dict:
    return {
        "pn": row.get("pn") or "",
        "mpn": row.get("mpn") or "",
        "manufacturer": row.get("manufacturer") or "",
        "comId": row.get("comId") or "",
        "status": row.get("status") or "",
        "source": source,
    }


BULK_LIMIT = 50  # Matches SE /partDetail 50-ComID batch cap.


def _parse_bulk_list(raw: str) -> list[str]:
    """Split by comma, whitespace, newline; trim, dedupe, preserve order."""
    if not raw:
        return []
    # Replace all common separators with newline, then split.
    for sep in (",", ";", "\t", "\r"):
        raw = raw.replace(sep, "\n")
    seen: set[str] = set()
    out: list[str] = []
    for line in raw.split("\n"):
        pn = line.strip()
        if not pn or pn.lower() in seen:
            continue
        seen.add(pn.lower())
        out.append(pn)
    return out


def bulk_search(raw: str) -> dict:
    """Look up many PNs in one request. Caps at BULK_LIMIT to respect SE's
    50-ComID batch rule on /partDetail.

    Returns:
      {
        "query": raw,
        "total": <requested count>,
        "limit": BULK_LIMIT,
        "truncated": bool,
        "missing": [...PNs not found anywhere],
        "hits": [ per-PN candidate with reason when no ComID resolved ],
        "source": "bulk",
      }
    """
    pns = _parse_bulk_list(raw)
    total = len(pns)
    truncated = total > BULK_LIMIT
    pns_capped = pns[:BULK_LIMIT]

    hits: list[dict] = []
    missing: list[str] = []
    denodo_configured = denodo_client.is_configured()
    denodo_errors: set[str] = set()

    for pn in pns_capped:
        rows = excel_lookup.search(pn)
        if rows:
            cand = _candidate_from_row(rows[0], source="excel")
            cand["requested"] = pn
            cand["reason"] = ""
            if not cand["comId"] and cand["mpn"]:
                resolved = se.resolve_comid(cand["mpn"], cand["manufacturer"] or None)
                if resolved:
                    cand["comId"] = resolved
                    cand["source"] = "excel+partsearch"
                else:
                    cand["reason"] = (
                        "Excel row had no SE_ComID and /partsearch found no match"
                    )
            elif not cand["comId"]:
                cand["reason"] = "Excel row has no SE_ComID and no MPN"
            _record_hit(cand, kind="bulk")
            hits.append(cand)
            continue

        # Try Denodo fallback (only if configured). Connection errors are
        # surfaced via the top-level `denodo` block; per-PN status uses
        # `requested` + `reason`.
        row, err = (None, None)
        if denodo_configured:
            try:
                row, err = denodo_client.find_item_ex(pn)
            except Exception as exc:  # noqa: BLE001
                err = f"Denodo lookup failed: {exc!r}"
        if err:
            denodo_errors.add(err)

        if row:
            hits.append(
                {
                    "pn": str(row.get("Item_Number") or pn),
                    "mpn": "",
                    "manufacturer": str(row.get("Manufacturer") or ""),
                    "comId": "",
                    "status": str(row.get("Status") or ""),
                    "source": "denodo",
                    "requested": pn,
                    "reason": "Found in Denodo but not mapped to a SE ComID",
                }
            )
            continue

        missing.append(pn)

    denodo_status = {
        "configured": denodo_configured,
        "online": denodo_configured and not denodo_errors,
        "error": "; ".join(sorted(denodo_errors)) if denodo_errors else None,
    }

    return {
        "query": raw,
        "total": total,
        "limit": BULK_LIMIT,
        "truncated": truncated,
        "missing": missing,
        "hits": hits,
        "source": "bulk",
        "denodo": denodo_status,
    }


def search(query: str) -> dict:
    q = (query or "").strip()
    if not q:
        return {"query": q, "hits": [], "source": "empty", "reason": ""}

    rows = excel_lookup.search(q)
    if rows:
        hits: list[dict] = []
        for r in rows:
            cand = _candidate_from_row(r, source="excel")
            cand["reason"] = ""  # will be filled if ComID resolution fails
            if not cand["comId"] and cand["mpn"]:
                # Fallback: resolve ComID via /partsearch using MPN (+ mfr).
                resolved = se.resolve_comid(cand["mpn"], cand["manufacturer"] or None)
                if resolved:
                    cand["comId"] = resolved
                    cand["source"] = "excel+partsearch"
                else:
                    cand["reason"] = (
                        "Excel has no SE_ComID and /partsearch found no match "
                        f"for MPN '{cand['mpn']}'"
                        + (f" · {cand['manufacturer']}" if cand["manufacturer"] else "")
                    )
            elif not cand["comId"]:
                cand["reason"] = "Excel has no SE_ComID and no MPN to resolve from"
            _record_hit(cand, kind="single")
            hits.append(cand)
        return {"query": q, "hits": hits, "source": "excel"}

    # Not in Excel — fall back to Denodo iv_plm_allparts_latest by Item_Number.
    denodo_configured = denodo_client.is_configured()
    row, denodo_error = (None, None)
    if denodo_configured:
        try:
            row, denodo_error = denodo_client.find_item_ex(q)
        except Exception as exc:  # noqa: BLE001
            denodo_error = f"Denodo lookup failed: {exc!r}"
    denodo_status = {
        "configured": denodo_configured,
        "online": denodo_configured and denodo_error is None,
        "error": denodo_error,
    }
    if row:
        cand = {
            "pn": str(row.get("Item_Number") or row.get("item_number") or q),
            "mpn": "",
            "manufacturer": str(row.get("Manufacturer") or row.get("manufacturer") or ""),
            "comId": "",
            "status": str(row.get("Status") or row.get("status") or ""),
            "source": "denodo",
            "denodo": row,
            "reason": "Part found in Denodo but not in local SE mapping · no ComID available",
        }
        return {
            "query": q,
            "hits": [cand],
            "source": "denodo",
            "denodo": denodo_status,
        }

    if denodo_error:
        reason = (
            f"'{q}' not found in Excel mapping · Denodo fallback unavailable ({denodo_error})"
            " · SiliconExpert API still reachable for known parts"
        )
    elif not denodo_configured:
        reason = (
            f"'{q}' not found in Excel mapping · Denodo fallback not configured"
        )
    else:
        reason = f"'{q}' not found in Excel mapping and not in Denodo iv_plm_allparts_latest"

    return {
        "query": q,
        "hits": [],
        "source": "none",
        "reason": reason,
        "denodo": denodo_status,
    }


def _find_candidate(pn: str | None, com_id: str | None) -> dict | None:
    if pn:
        rows = excel_lookup.find_by_pn(pn)
        if rows:
            return _candidate_from_row(rows[0], source="excel")
    if com_id:
        # Reverse scan — cheap enough for 10k rows, one-time.
        excel_lookup.load()
        for r in excel_lookup._state["rows"]:  # noqa: SLF001 — intentional
            if r.get("comId") == com_id:
                return _candidate_from_row(r, source="excel")
        return {
            "pn": "",
            "mpn": "",
            "manufacturer": "",
            "comId": com_id,
            "status": "",
            "source": "comid",
        }
    return None


def _to_float(v: object) -> float | None:
    s = str(v or "").strip().replace("%", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _int_from_count(v: object) -> int | None:
    # "0 Source", "0 Distributor(s)", "13 Year(s)" -> 0, 0, 13
    s = str(v or "").strip()
    if not s:
        return None
    num = ""
    for ch in s:
        if ch.isdigit():
            num += ch
        elif num:
            break
    return int(num) if num else None


def _weeks_from(v: object) -> float | None:
    # "8 Week(s)", "12.0 Week(s)", "8" -> 8.0 / 12.0 / 8.0
    s = str(v or "").strip()
    if not s:
        return None
    s = s.replace("Week(s)", "").replace("week(s)", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _normalize_commercial(dto: dict) -> dict:
    """Real commercial fields from /partDetail.

    Pricing / stock / distributors all live in different sub-sections of
    partDetail. We flatten the useful bits so the UI can render everything
    without understanding the SE response shape.
    """
    res_factors = dto.get("ResilienceRatingFactors") or {}
    res_details = dto.get("ResilienceRatingdetails") or {}
    counterfeit = dto.get("FullCounterfeitData") or {}
    multi = (res_details.get("AssuranceOfSupply") or {}).get("multiSourcingRiskDto") or {}
    inv_risk = (res_details.get("AssuranceOfSupply") or {}).get("inventoryRiskDto") or {}
    summary = dto.get("SummaryData") or {}

    # ---- Pricing + lead time ----
    pricing = dto.get("PricingData") or {}
    price_min = _to_float(pricing.get("MinimumPrice"))
    price_avg = _to_float(pricing.get("AveragePrice"))
    min_lead = _weeks_from(pricing.get("MinLeadtime"))
    max_lead = _weeks_from(pricing.get("Maxleadtime"))
    price_last_updated = str(pricing.get("LastUpdatedate") or "")

    # ---- Price breaks (volume tiers) ----
    breaks_raw = (dto.get("PriceBreaksData") or {}).get("PriceBreaksDto") or []
    if isinstance(breaks_raw, dict):
        breaks_raw = [breaks_raw]
    price_breaks: list[dict] = []
    for b in breaks_raw if isinstance(breaks_raw, list) else []:
        if not isinstance(b, dict):
            continue
        price_breaks.append(
            {
                "qty": _int_from_count(b.get("PriceBreaK")),
                "avg": _to_float(b.get("AveragePrice")),
                "min": _to_float(b.get("MinPrice")),
            }
        )
    # Sort ascending by qty for display
    price_breaks.sort(key=lambda x: (x.get("qty") is None, x.get("qty") or 0))

    # ---- Price & lead-time history ----
    history_raw = dto.get("PriceAndLeadTimeHistory") or []
    if isinstance(history_raw, dict):
        history_raw = [history_raw]
    price_history: list[dict] = []
    for h in history_raw if isinstance(history_raw, list) else []:
        if not isinstance(h, dict):
            continue
        price_history.append(
            {
                "date": str(h.get("LastUpdatedate") or ""),
                "min": _to_float(h.get("MinimumPrice")),
                "avg": _to_float(h.get("AveragePrice")),
                "minLead": _weeks_from(h.get("MinLeadtime")),
                "maxLead": _weeks_from(h.get("Maxleadtime")),
            }
        )
    # Keep chronological order (API returns newest first; reverse for chart).
    price_history.reverse()

    # ---- Inventory ----
    total_inventory = _int_from_count(dto.get("TotalInventory"))
    average_inventory = _int_from_count(dto.get("AverageInventory"))

    # ---- Franchised distributors ----
    fran_raw = (dto.get("FranchisedInventoryData") or {}).get("FranchisedInventoryDto") or []
    if isinstance(fran_raw, dict):
        fran_raw = [fran_raw]
    distributors: list[dict] = []
    for d in fran_raw if isinstance(fran_raw, list) else []:
        if not isinstance(d, dict):
            continue
        distributors.append(
            {
                "distributor": str(d.get("Distributor") or ""),
                "quantity": _int_from_count(d.get("Quantity")),
                "buyNowLink": str(d.get("BuyNowLink") or ""),
                "lastUpdated": str(d.get("LastUpdated") or ""),
            }
        )
    # Sort by quantity descending, zeros last
    distributors.sort(key=lambda r: (r.get("quantity") or 0) * -1)

    authorized_dist_count = (
        _int_from_count(summary.get("AuthorizedDistributors"))
        or _int_from_count(counterfeit.get("AuthorizedDistributorswithStockCount"))
    )
    distributors_with_stock = sum(
        1 for d in distributors if (d.get("quantity") or 0) > 0
    )

    return {
        "resilienceRating": _to_float(res_factors.get("ResilienceRating")),
        "resilienceGrade": str(res_factors.get("ResilienceRatingGrade") or ""),
        "otherSources": _int_from_count(multi.get("countOfOtherSources")),
        "crossesAvailable": str(multi.get("crosseAavailableWithinPartCategory") or ""),
        "authorizedDistributorsCount": authorized_dist_count,
        "distributorsWithStock": distributors_with_stock,
        "counterfeitRisk": str(counterfeit.get("CounterfeitOverallRisk") or ""),
        "counterfeitGrade": str(counterfeit.get("OverallCounterfeitRiskGrade") or ""),
        "yearsSinceIntro": _int_from_count(counterfeit.get("TimeSinceMarketIntroduction")),
        "inventoryRiskGrade": str(inv_risk.get("grade") or ""),
        "historicalShortages": str(counterfeit.get("HistoricalShortagesInventoryReported") or ""),
        # New pricing/stock block
        "priceMin": price_min,
        "priceAvg": price_avg,
        "minLeadWeeks": min_lead,
        "maxLeadWeeks": max_lead,
        "priceLastUpdated": price_last_updated,
        "priceBreaks": price_breaks,
        "priceHistory": price_history,
        "totalInventory": total_inventory,
        "averageInventory": average_inventory,
        "distributors": distributors,
    }


def _normalize_parametric(dto: dict) -> list[dict]:
    """Real parametric features from ParametricData.Features (per part)."""
    pd = dto.get("ParametricData") or {}
    feats = pd.get("Features") or []
    out: list[dict] = []
    for f in feats if isinstance(feats, list) else []:
        if not isinstance(f, dict):
            continue
        out.append(
            {
                "name": str(f.get("FeatureName") or ""),
                "value": str(f.get("FeatureValue") or ""),
                "unit": str(f.get("FeatureUnit") or ""),
            }
        )
    return out


def _normalize_packaging(dto: dict) -> dict:
    """Flatten PackagingData + PackageData into a combined dict. Each field
    is either a string or empty. ComID is included so it can be displayed.
    """
    pkg = dto.get("PackagingData") or {}
    case = dto.get("PackageData") or {}
    com_id = str(dto.get("RequestedComID") or "")

    def s(src: dict, k: str) -> str:
        return str(src.get(k) or "")

    return {
        "comId": com_id,
        # --- PackagingData (tape + reel)
        "packagingSuffix":          s(pkg, "PackagingSuffix"),
        "packaging":                s(pkg, "Packaging"),
        "quantityOfPackaging":      s(pkg, "QuantityOfPackaging"),
        "reelDiameter":             s(pkg, "ReelDiameter"),
        "reelWidth":                s(pkg, "ReelWidth"),
        "tapePitch":                s(pkg, "TapePitch"),
        "tapeWidth":                s(pkg, "TapeWidth"),
        "feedHolePitch":            s(pkg, "FeedHolePitch"),
        "holeCenterToComponentCenter": s(pkg, "HoleCenterToComponentCenter"),
        "leadClinchHeight":         s(pkg, "LeadClinchHeight"),
        "componentOrientation":     s(pkg, "ComponentOrientation"),
        "packagingDocument":        s(pkg, "PackagingDocument"),
        "tapeMaterial":             s(pkg, "TapeMaterial"),
        "tapeType":                 s(pkg, "TapeType"),
        # --- PackageData (outline / case)
        "supplierPackage":          s(case, "SupplierPackage"),
        "pinCount":                 s(case, "PinCount"),
        "pcb":                      s(case, "PCB"),
        "tab":                      s(case, "Tab"),
        "packageDiameter":          s(case, "PackageDiameter"),
        "mounting":                 s(case, "Mounting"),
        "packageLength":            s(case, "PackageLength"),
        "packageWidth":             s(case, "PackageWidth"),
        "packageHeight":            s(case, "PackageHeight"),
        "packageDescription":       s(case, "PackageDescription"),
        "packageMaterial":          s(case, "PackageMaterial"),
        "standardPackageName":      s(case, "StandardPackageName"),
        "seatedPlaneHeight":        s(case, "SeatedPlaneHeight"),
        "pinPitch":                 s(case, "PinPitch"),
        "jedec":                    s(case, "Jedec"),
        "packageOutline":           s(case, "PackageOutline"),
        "packageCase":              s(case, "PackageCase"),
        "leadShape":                s(case, "LeadShape"),
        "basicPackageType":         s(case, "BasicPackageType"),
        "packageWeight":            s(case, "PackageWeight"),
        "minimumSeatedPlaneHeight": s(case, "MinimumSeatedPlaneHeight"),
        "packageOrientation":       s(case, "PackageOrientation"),
    }


def _normalize_countries(dto: dict) -> list[dict]:
    """Flatten SummaryData.CountriesOfOrigin.CountryOfOrigin into rows."""
    summary = dto.get("SummaryData") or {}
    container = summary.get("CountriesOfOrigin") or {}
    raw = container.get("CountryOfOrigin") if isinstance(container, dict) else None
    if isinstance(raw, dict):
        raw = [raw]
    com_id = str(dto.get("RequestedComID") or "")
    out: list[dict] = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        out.append(
            {
                "comId":   com_id,
                "country": str(r.get("Country") or ""),
                "source":  str(r.get("Source") or ""),
            }
        )
    return out


def _normalize_documents(dto: dict) -> dict:
    """Structured document payload from /partDetail.

    Sections:
      - images: small + large product image URLs
      - datasheet: latest + full revision history
      - certifications: AEC / ISO 26262 / IATF 16949 / PPAP / Automotive / ESD
      - lifecycleHistory: list of lifecycle source documents
      - gidep: GIDEP advisory if present
      - counterfeitReports: list of counterfeit incident reports (truncated)
    """
    summary = dto.get("SummaryData") or {}
    history = dto.get("History") or {}
    img = dto.get("ProductImage") or {}
    qual = dto.get("Qualifications") or {}
    env = dto.get("EnvironmentalDto") or {}
    gidep = dto.get("GidepData") or {}
    counterfeit = dto.get("FullCounterfeitData") or {}

    # Datasheet revisions
    ds_hist_raw = history.get("Datasheet") or []
    if isinstance(ds_hist_raw, dict):
        ds_hist_raw = [ds_hist_raw]
    datasheet_history = [
        {
            "date": str(r.get("date") or r.get("Date") or ""),
            "url":  str(r.get("url")  or r.get("URL")  or r.get("Url")  or ""),
        }
        for r in (ds_hist_raw if isinstance(ds_hist_raw, list) else [])
        if isinstance(r, dict)
    ]
    datasheet_history = [d for d in datasheet_history if d["url"]]

    # Lifecycle source docs
    lc_hist_raw = history.get("Lifecycle") or []
    if isinstance(lc_hist_raw, dict):
        lc_hist_raw = [lc_hist_raw]
    lifecycle_history = [
        {
            "date":            str(r.get("Date") or r.get("date") or ""),
            "lifecycle":       str(r.get("Lifecycle") or ""),
            "manufacturerName": str(r.get("ManufacturerName") or ""),
            "partNumber":      str(r.get("PartNumber") or ""),
            "reasonOfChange":  str(r.get("ReasonOfChange") or ""),
            "sourceName":      str(r.get("SourceName") or ""),
            "sourceURL":       str(r.get("SourceURL") or ""),
        }
        for r in (lc_hist_raw if isinstance(lc_hist_raw, list) else [])
        if isinstance(r, dict)
    ]

    # Counterfeit reports (limit 20 to keep payload reasonable; full count below)
    cf_raw = counterfeit.get("ManCounterfeitReports") or []
    if isinstance(cf_raw, dict):
        cf_raw = [cf_raw]
    counterfeit_reports = [
        {
            "mpn":               str(r.get("MPN") or ""),
            "supplier":          str(r.get("Supplier") or ""),
            "notificationDate":  str(r.get("NotificationDate") or ""),
            "description":       str(r.get("Description") or ""),
            "counterfeitMethod": str(r.get("CounterfitMethods") or r.get("CounterfeitMethods") or ""),
            "source":            str(r.get("Source") or ""),
        }
        for r in (cf_raw if isinstance(cf_raw, list) else [])[:20]
        if isinstance(r, dict)
    ]
    cf_count = _int_from_count(counterfeit.get("ManCounterfeitReportsCount"))
    if cf_count is None and isinstance(cf_raw, list):
        cf_count = len(cf_raw)

    # Certifications (only include rows that have a URL)
    certifications: list[dict] = []
    cert_src = [
        ("AEC-Q100", summary.get("AECPDF"),
            f"AEC number: {summary.get('AECNumber') or '—'} · AEC qualified: "
            f"{summary.get('AECQualified') or '—'}"),
        ("ISO 26262 (ASIL)", summary.get("Iso26262Source"),
            lang_safe_str(summary.get("Iso26262Level"), "functional safety")),
        ("IATF 16949", summary.get("IsoTs16949Source"),
            "automotive QMS"),
        ("PPAP",  summary.get("PPAPSource"),
            f"PPAP status: {summary.get('PPAP') or '—'}"),
        ("Automotive qualification", summary.get("AutomotiveSource"),
            f"Automotive: {summary.get('Automotive') or '—'}"),
        ("ESD qualification",
            (qual.get("ESDQualification") or {}).get("SourceOfInformation")
            or summary.get("ESDSourceofInformation"),
            (qual.get("ESDQualification") or {}).get("ESDClass") or ""),
        ("Flammability",
            (qual.get("Flammability") or {}).get("PDFURL"),
            (qual.get("Flammability") or {}).get("FlammabilityRating") or ""),
        ("Reliability (FIT/MTBF)",
            (qual.get("Reliability") or {}).get("SourceOfInformation"),
            ""),
        ("Material declaration (RoHS)", env.get("Source"),
            f"{env.get('SourceType') or '—'}"),
        ("Conflict minerals policy",
            env.get("ConflictMineralsPolicy"), ""),
        ("Conflict minerals statement",
            env.get("ConflictMineralStatement"), ""),
        ("CMRT template", env.get("EICCTemplate"),
            str(env.get("EICCTemplateVersion") or "")),
        ("SEC form SD", env.get("SDForm"), ""),
    ]
    for name, url, subtitle in cert_src:
        if url:
            certifications.append({
                "name":     name,
                "url":      str(url),
                "subtitle": str(subtitle or "")
            })

    return {
        "images": {
            "small": str(img.get("ProductImageSmall") or summary.get("SmallImageURL") or ""),
            "large": str(img.get("ProductImageLarge") or ""),
        },
        "datasheet": {
            "latestUrl":     str(summary.get("Datasheet") or ""),
            "supplierUrl":   str(summary.get("OnlineSupplierDatasheetURL") or ""),
            "latestDate":    datasheet_history[0]["date"] if datasheet_history else "",
            "revisionCount": len(datasheet_history),
            "history":       datasheet_history,
        },
        "certifications":     certifications,
        "lifecycleHistory":   lifecycle_history,
        "gidep": {
            "typeOfChange":     str(gidep.get("TypeOfChange") or ""),
            "description":      str(gidep.get("GIDEPDescription") or ""),
            "notificationDate": str(gidep.get("NotificationDate") or ""),
            "documentNumber":   str(gidep.get("DocumentNumber") or ""),
        } if gidep else {},
        "counterfeit": {
            "overallRisk":   str(counterfeit.get("CounterfeitOverallRisk") or ""),
            "overallGrade":  str(counterfeit.get("OverallCounterfeitRiskGrade") or ""),
            "reportsCount":  cf_count or 0,
            "reports":       counterfeit_reports,
        },
    }


def lang_safe_str(v: object, default: str = "") -> str:
    s = str(v or "").strip()
    return s if s else default


def _normalize_regulatory(dto: dict) -> dict:
    """Full regulatory declaration block from /partDetail.

    Pulls from three sub-objects:
      - EnvironmentalDto: RoHS, China RoHS, Conflict Minerals, Halogen, REE
      - ReachData.ReachDto: REACH SVHC
      - Qualifications: ESD, Flammability, Reliability (FIT/MTBF)
    """
    env = dto.get("EnvironmentalDto") or {}
    china = env.get("ChinaRoHS") or {}
    reach = (dto.get("ReachData") or {}).get("ReachDto") or {}
    annex = reach.get("AnnexXIV") or {}
    qual = dto.get("Qualifications") or {}
    esd = qual.get("ESDQualification") or {}
    flam = qual.get("Flammability") or {}
    rel = qual.get("Reliability") or {}
    fit = rel.get("FitDetail") or {}
    mtbf = rel.get("MTBFDetail") or {}

    return {
        "rohs": {
            "status": str(env.get("RoHSStatus") or ""),
            "version": str(env.get("RoHSVersion") or ""),
            "source": str(env.get("Source") or ""),
            "sourceType": str(env.get("SourceType") or ""),
            "otherSource": (
                str((env.get("OtherSources") or {}).get("Source") or "")
                if isinstance(env.get("OtherSources"), dict)
                else ""
            ),
            "exemption": str(env.get("Exemption") or ""),
            "exemptionType": str(env.get("ExemptionType") or ""),
            "exemptionCodes": str(env.get("ExemptionCodes") or ""),
            "leadFree": str(env.get("LeadFree") or ""),
        },
        "chinaRoHS": {
            "status": str(china.get("ChinaRoHSStatus") or ""),
            "version": str(china.get("ChinaRoHSVersion") or ""),
            "concentrations": {
                "cadmium":  str(china.get("CadmiumConcentration") or ""),
                "chromium": str(china.get("ChromiumConcentration") or ""),
                "lead":     str(china.get("LeadConcentration") or ""),
                "mercury":  str(china.get("MercuryConcentration") or ""),
                "PBB":      str(china.get("PBBConcentration") or ""),
                "PBDE":     str(china.get("PBDEConcentration") or ""),
                "DEHP":     str(china.get("EthylhexylDehpConcentration") or ""),
                "BBP":      str(china.get("ButylBenzylBbpConcentration") or ""),
                "DBP":      str(china.get("DibutylDbpConcentration") or ""),
            },
            "flags": {
                "cadmium":  str(china.get("CadmiumFlag") or ""),
                "chromium": str(china.get("ChromiumFlag") or ""),
                "lead":     str(china.get("LeadFlag") or ""),
                "mercury":  str(china.get("MercuryFlag") or ""),
                "PBB":      str(china.get("PBBFlag") or ""),
                "PBDE":     str(china.get("PBDEFlag") or ""),
                "DEHP":     str(china.get("EthylhexylDehpFlag") or ""),
                "BBP":      str(china.get("ButylBenzylBbpFlag") or ""),
                "DBP":      str(china.get("DibutylDbpFlag") or ""),
            },
        },
        "reach": {
            "status":             str(reach.get("ReachStatus") or ""),
            "containsSVHC":       str(reach.get("ContainsSVHC") or ""),
            "exceedsThreshold":   str(reach.get("SVHCExceedThresholdLimit") or ""),
            "svhcListVersion":    str(reach.get("SVHCListVersion") or ""),
            "substance":          str(reach.get("SubstanceIdentification") or ""),
            "substanceLocation":  str(reach.get("SubstanceLocation") or ""),
            "concentration":      str(reach.get("SubstanceConcentration") or ""),
            "casNumber":          str(reach.get("CASNumber") or ""),
            "inclusionDate":      str(reach.get("SVHCDateOfInclusion") or ""),
            "sourceType":         str(reach.get("SourceType") or ""),
            "source":             str(reach.get("CachedSource") or ""),
            "annexXIV": {
                "sunsetDate":       str(annex.get("SunsetDate") or ""),
                "applicationDate":  str(annex.get("ApplicationDate") or ""),
                "authEntryNumber":  str(annex.get("AuthorizationEntryNumber") or ""),
                "exempted":         str(annex.get("ExemptedCategories") or ""),
            },
        },
        "conflictMinerals": {
            "status":          str(env.get("ConflictMineralStatus") or ""),
            "statement":       str(env.get("ConflictMineralStatement") or ""),
            "policy":          str(env.get("ConflictMineralsPolicy") or ""),
            "eiccMembership":  str(env.get("EICCMembership") or ""),
            "eiccTemplate":    str(env.get("EICCTemplate") or ""),
            "eiccVersion":     str(env.get("EICCTemplateVersion") or ""),
            "sdForm":          str(env.get("SDForm") or ""),
            "sustainability":  str(env.get("ConflictMineralsSustainabilityReport") or ""),
        },
        "halogen": str(env.get("HalgonFree") or env.get("HalogenFree") or ""),
        "rareEarth": str(env.get("RareEarthElementInformation") or ""),
        "esd": {
            "protection":    str(esd.get("ESDProtection") or ""),
            "maxVoltage":    str(esd.get("MaximumESDProtectionVoltage") or ""),
            "esdClass":      str(esd.get("ESDClass") or ""),
            "source":        str(esd.get("SourceOfInformation") or ""),
        },
        "flammability": {
            "status":     str(flam.get("Flammability") or ""),
            "rating":     str(flam.get("FlammabilityRating") or ""),
            "source":     str(flam.get("PDFURL") or ""),
        },
        "reliability": {
            "fit":            str(fit.get("FIT") or ""),
            "fitCondition":   str(fit.get("ConditionValue") or ""),
            "mtbf":           str(mtbf.get("MTBF") or ""),
            "mtbfCondition":  str(mtbf.get("ConditionValue") or ""),
            "source":         str(rel.get("SourceOfInformation") or ""),
            "flammabilityRating": str(rel.get("FlammabilityRating") or ""),
        },
    }


def _normalize_chemicals(dto: dict) -> list[dict]:
    """Flatten ChemicalData.ChemicalDto into the UI row shape."""
    raw = (dto.get("ChemicalData") or {}).get("ChemicalDto") or []
    if isinstance(raw, dict):
        raw = [raw]
    com_id = str(dto.get("RequestedComID") or "")
    out: list[dict] = []
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        out.append(
            {
                "comId":                      com_id,
                "totalMassInGram":            _to_float(r.get("TotalMassInGram")),
                "totalMassSummationInGram":   _to_float(r.get("TotalMassSummationInGram")),
                "locationName":               str(r.get("LocationName") or ""),
                "homogenousMaterial":         str(r.get("HomogenousMaterial") or ""),
                "homogenousMaterialMass":     _to_float(r.get("HomogenousMaterialMass")),
                "substanceIdentification":    str(r.get("SubstanceIdentification") or ""),
                "normalizedSubstance":        str(r.get("NormalizedSubstance") or ""),
                "substanceMass":              _to_float(r.get("SubstanceMass")),
                "ppm":                        _to_float(r.get("PPM")),
                "casNumber":                  str(r.get("CASNumber") or ""),
                "mdsUrl":                     str(r.get("MDSURL") or ""),
                "itemSubItem":                str(r.get("ItemSubItem") or ""),
            }
        )
    return out


def _normalize_lifecycle(dto: dict) -> dict | None:
    lc = dto.get("LifeCycleData") or {}
    risk = dto.get("RiskData") or {}
    if not lc and not risk:
        return None
    return {
        "partStatus": str(lc.get("PartStatus") or ""),
        "estimatedYearsToEOL": _to_float(lc.get("EstimatedYearsToEOL")),
        "minYearsToEOL": _to_float(lc.get("MinimumEstimatedYearsToEOL")),
        "maxYearsToEOL": _to_float(lc.get("MaximumEstimatedYearsToEOL")),
        "estimatedEOLDate": str(lc.get("EstimatedEOLDate") or ""),
        "partLifecycleStage": str(lc.get("PartLifecycleStage") or ""),
        "lifeCycleRiskGrade": str(lc.get("LifeCycleRiskGrade") or ""),
        "overallRiskPct": _to_float(lc.get("OverallRisk")),
        "lifeCycleComment": str(lc.get("LifeCycleComment") or ""),
        "riskGrades": {
            "rohs": str(risk.get("RohsRisk") or ""),
            "multiSourcing": str(risk.get("MultiSourcingRisk") or ""),
            "inventory": str(risk.get("InventoryRisk") or ""),
            "lifecycle": str(risk.get("LifecycleRisk") or ""),
        },
        "numberOfDistributors": _to_float(risk.get("NumberOfDistributors")),
        "crossesAvailable": str(risk.get("CrossesPartCategory") or ""),
    }


def _normalize_part(dto: dict, candidate: dict) -> dict:
    summary = dto.get("SummaryData") or {}
    env = dto.get("EnvironmentalDto") or {}
    return {
        "comId": str(
            dto.get("RequestedComID")
            or summary.get("DataProviderID")
            or candidate.get("comId")
            or ""
        ),
        "pn": candidate.get("pn") or "",
        "mpn": candidate.get("mpn") or str(summary.get("PartNumber") or ""),
        "manufacturer": candidate.get("manufacturer")
        or str(summary.get("Manufacturer") or ""),
        "description": str(summary.get("PartDescription") or ""),
        "plName": str(summary.get("PLName") or ""),
        "family": str(
            summary.get("FamilyName") or summary.get("GenericName") or ""
        ),
        "taxonomy": str(summary.get("TaxonomyPath") or ""),
        "datasheetUrl": str(summary.get("Datasheet") or ""),
        "supplierDatasheetUrl": str(summary.get("OnlineSupplierDatasheetURL") or ""),
        "imageUrl": str(summary.get("SmallImageURL") or ""),
        "introductionDate": str(summary.get("IntroductionDate") or ""),
        "lastCheckDate": str(summary.get("LastCheckDate") or ""),
        "eccn": str(summary.get("ECCN") or ""),
        "rohs": str(env.get("RoHSStatus") or summary.get("EURoHS") or ""),
        "rohsIdentifier": str(env.get("RohsIdentifier") or ""),
        "conflictMinerals": str(env.get("ConflictMineralStatus") or ""),
        "automotive": str(summary.get("Automotive") or ""),
        "aecQualified": str(summary.get("AECQualified") or ""),
    }


def detail(*, pn: str | None = None, com_id: str | None = None) -> dict:
    cand = _find_candidate(pn, com_id)
    if not cand:
        return {
            "status": "not_found",
            "reason": (
                f"'{pn or com_id}' not found in Excel mapping "
                "and not in Denodo iv_plm_allparts_latest"
            ),
            "part": {},
            "lifecycle": None,
            "commercial": {},
            "parametric": [],
            "regulatory": {},
            "chemicals": [],
            "documents": {},
            "packaging": {},
            "countries": [],
            "raw": {"query": {"pn": pn, "comId": com_id}},
        }

    com = cand.get("comId") or ""
    # If Excel had no ComID but has MPN, try to resolve on demand.
    if not com and cand.get("mpn"):
        resolved = se.resolve_comid(cand["mpn"], cand.get("manufacturer") or None)
        if resolved:
            com = resolved
            cand["comId"] = resolved
            cand["source"] = (cand.get("source") or "") + "+partsearch"

    part: dict = _normalize_part({}, cand)
    part["comId"] = com or part["comId"]
    commercial: dict = {}
    parametric: list[dict] = []
    regulatory: dict = {}
    chemicals: list[dict] = []
    documents: dict = {}
    packaging: dict = {}
    countries: list[dict] = []
    lifecycle: dict | None = None
    raw: dict = {"candidate": cand}
    status = "ok"
    reason = ""

    if not com:
        status = "no_comid"
        reason = cand.get("reason") or _reason_for_no_comid(cand)
    else:
        try:
            resp = se.part_detail([com], lifecycle=True)
        except Exception as exc:  # noqa: BLE001
            raw["error"] = f"partDetail: {exc!r}"
            status = "partdetail_error"
            reason = f"SiliconExpert /partDetail failed · {exc!r}"
            resp = {"Results": {"ResultDto": []}}
        dtos = resp.get("Results", {}).get("ResultDto") or []
        raw["partDetail"] = dtos
        if dtos:
            d0 = dtos[0] if isinstance(dtos[0], dict) else {}
            part = _normalize_part(d0, cand)
            lifecycle = _normalize_lifecycle(d0)
            commercial = _normalize_commercial(d0)
            parametric = _normalize_parametric(d0)
            regulatory = _normalize_regulatory(d0)
            chemicals = _normalize_chemicals(d0)
            documents = _normalize_documents(d0)
            packaging = _normalize_packaging(d0)
            countries = _normalize_countries(d0)
        elif status == "ok":
            status = "empty_partdetail"
            reason = (
                f"ComID {com} returned no data from /partDetail "
                "(check SE authorization / account quota)"
            )

    # Record a high-signal event: user confirmed interest in this part. Carries
    # lifecycle + EOL + risk so the Home screen's Recent Searches can render a
    # one-line summary without a second fetch.
    try:
        recent_store.record(
            pn=(part.get("pn") or cand.get("pn") or "").strip(),
            mpn=part.get("mpn") or cand.get("mpn") or "",
            manufacturer=part.get("manufacturer") or cand.get("manufacturer") or "",
            com_id=part.get("comId") or cand.get("comId") or "",
            lifecycle=(lifecycle or {}).get("partStatus") or "",
            yeol=(lifecycle or {}).get("estimatedYearsToEOL"),
            risk=(lifecycle or {}).get("overallRiskPct"),
            source=cand.get("source") or "",
            kind="detail",
        )
    except Exception:  # noqa: BLE001
        pass

    return {
        "status": status,
        "reason": reason,
        "part": part,
        "lifecycle": lifecycle,
        "commercial": commercial,
        "parametric": parametric,
        "regulatory": regulatory,
        "chemicals": chemicals,
        "documents": documents,
        "packaging": packaging,
        "countries": countries,
        "raw": raw,
    }


def _reason_for_no_comid(cand: dict) -> str:
    src = cand.get("source") or ""
    if "denodo" in src:
        return (
            "Part found in Advantech Denodo (iv_plm_allparts_latest) but is not "
            "mapped to a SiliconExpert ComID · no SE data available"
        )
    mpn = cand.get("mpn")
    mfr = cand.get("manufacturer") or ""
    if mpn:
        return (
            f"No SE_ComID in Excel and /partsearch returned no match for "
            f"MPN '{mpn}'" + (f" · {mfr}" if mfr else "")
        )
    return "No SE_ComID in Excel and no MPN to resolve · no SE data available"

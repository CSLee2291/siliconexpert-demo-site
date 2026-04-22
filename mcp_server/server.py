"""MCP server that exposes the SiliconExpert demo Azure Web App as LLM tools.

Backend: https://siliconexpert-demo-cslee.azurewebsites.net (set SE_API_BASE to override).

Each tool wraps one slice of the full `/api/detail` bundle so the LLM can pick
exactly the right sub-set for the user's question.  The first tool call for a
given part number fetches and caches the full bundle; subsequent tool calls for
the same PN are served from the session cache.
"""
import os
import sys
import logging
from typing import Any

import httpx
from mcp.server.fastmcp import FastMCP


DEFAULT_BASE = "https://siliconexpert-demo-cslee.azurewebsites.net"
BASE_URL = os.getenv("SE_API_BASE", DEFAULT_BASE).rstrip("/")
HTTP_TIMEOUT = float(os.getenv("SE_API_TIMEOUT", "60"))

logging.basicConfig(
    level=os.getenv("SE_MCP_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("se-mcp")

mcp = FastMCP("siliconexpert-demo")

# Per-process cache for /api/detail responses.  Keyed by upper-cased part number.
# Keeps the Azure API quiet when the LLM fires multiple slice tools at the same
# PN during a single conversation.
_detail_cache: dict[str, dict[str, Any]] = {}


# --------------------------------------------------------------------------- #
# HTTP helpers                                                                #
# --------------------------------------------------------------------------- #


async def _get(path: str, **params: Any) -> dict[str, Any]:
    """GET <BASE_URL><path>?<params>.  Returns parsed JSON or {"error": ...}."""
    url = f"{BASE_URL}{path}"
    try:
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
            r = await client.get(url, params=params)
            r.raise_for_status()
            return r.json()
    except httpx.HTTPStatusError as e:
        log.warning("HTTP %s on %s %s", e.response.status_code, path, params)
        return {"error": f"HTTP {e.response.status_code}", "url": str(e.request.url)}
    except httpx.HTTPError as e:
        log.warning("HTTP error on %s %s: %s", path, params, e)
        return {"error": str(e), "url": url}


async def _fetch_detail(part_number: str) -> dict[str, Any]:
    """Fetch /api/detail for a PN (or MPN) with per-session caching."""
    key = (part_number or "").strip().upper()
    if not key:
        return {"error": "empty part_number"}
    if key in _detail_cache:
        return _detail_cache[key]
    data = await _get("/api/detail", pn=key)
    if not data.get("error"):
        _detail_cache[key] = data
    return data


def _slice(detail: dict[str, Any], keys: list[str]) -> dict[str, Any]:
    """Extract a subset of the detail bundle, preserving status/reason."""
    if detail.get("error"):
        return detail
    out: dict[str, Any] = {
        "partNumber": detail.get("part", {}).get("comId") or detail.get("part", {}).get("pn"),
        "status": detail.get("status"),
    }
    if detail.get("reason"):
        out["reason"] = detail["reason"]
    for k in keys:
        if k in detail:
            out[k] = detail[k]
    return out


# --------------------------------------------------------------------------- #
# Tools                                                                       #
# --------------------------------------------------------------------------- #


@mcp.tool()
async def search_advantech_part(query: str) -> dict[str, Any]:
    """Search the Advantech PN / MPN mapping, returning SiliconExpert ComIDs.

    Use this first when the user gives a part number and you don't yet know
    whether it's an Advantech internal PN, a manufacturer MPN, or something
    unknown.  Returns up to a handful of `hits`, each with `pn`, `mpn`,
    `manufacturer`, `comId`, and `source` (`excel` | `partsearch` | `denodo`).

    Args:
        query: The part number string to look up.  Case-insensitive; leading
            and trailing whitespace is stripped.
    """
    return await _get("/api/search", q=query)


@mcp.tool()
async def get_overview(part_number: str) -> dict[str, Any]:
    """Get the Overview section for a part — manufacturer, description, family,
    AEC/automotive qualification, datasheet URL, image URL, and the parametric
    spec list (e.g. package, supply voltage, output type).

    Covers everything the UI "Overview" tab shows.  Call this when the user
    asks "what is part X?" or "who makes X?" or "what are the key specs?".

    Args:
        part_number: Advantech PN or manufacturer MPN.
    """
    detail = await _fetch_detail(part_number)
    return _slice(detail, ["part", "parametric"])


@mcp.tool()
async def get_lifecycle_and_risk(part_number: str) -> dict[str, Any]:
    """Get the lifecycle stage, estimated EOL, years-to-EOL, and risk grades.

    Fields include `partLifecycleStage` (e.g. Active, NRND, Obsolete),
    `estimatedEOLDate`, `estimatedYearsToEOL`, `lifeCycleRiskGrade`,
    `overallRiskPct`, and a `lifeCycleComment` written by SE analysts.

    Call this when the user asks "is X end-of-life?", "how long until EOL?",
    "is X a risky part to design in?".

    Args:
        part_number: Advantech PN or manufacturer MPN.
    """
    detail = await _fetch_detail(part_number)
    return _slice(detail, ["lifecycle"])


@mcp.tool()
async def get_pricing_and_stock(part_number: str) -> dict[str, Any]:
    """Get pricing tiers, distributor inventory, and supply-chain resilience.

    The `commercial` block includes `distributors` (per-distributor stock +
    price-break tiers), `averageInventory`, `maxLeadWeeks`, `minLeadWeeks`,
    `counterfeitGrade`, `counterfeitRisk`, `inventoryRiskGrade`, and
    `historicalShortages`.

    Call this when the user asks about price, stock, lead time, supply risk,
    or counterfeit exposure.

    Args:
        part_number: Advantech PN or manufacturer MPN.
    """
    detail = await _fetch_detail(part_number)
    return _slice(detail, ["commercial"])


@mcp.tool()
async def get_compliance(part_number: str) -> dict[str, Any]:
    """Get regulatory status — RoHS, REACH, China RoHS, halogen, conflict
    minerals, ELV, ESD, flammability, rare-earth — plus the full chemical
    data sheet (per-substance percentages and CAS numbers).

    Call this when the user asks "is X RoHS?", "is X REACH compliant?",
    "what substances does X contain?", or about conflict minerals disclosure.

    Args:
        part_number: Advantech PN or manufacturer MPN.
    """
    detail = await _fetch_detail(part_number)
    return _slice(detail, ["regulatory", "chemicals"])


@mcp.tool()
async def get_documents(part_number: str) -> dict[str, Any]:
    """Get document references — datasheet URL, large product images,
    certification list, counterfeit report, lifecycle-change history, and
    GIDEP (Government-Industry Data Exchange Program) alerts.

    Call this when the user asks "where's the datasheet?", "do you have a
    picture?", or "any GIDEP alerts for X?".

    Args:
        part_number: Advantech PN or manufacturer MPN.
    """
    detail = await _fetch_detail(part_number)
    return _slice(detail, ["documents"])


@mcp.tool()
async def get_packaging_data(part_number: str) -> dict[str, Any]:
    """Get packaging details — package type (e.g. SC-70), mounting (SMD/THT),
    moisture sensitivity level (MSL), reel size, tape pitch, JEDEC designator,
    component orientation, and seated-plane height.

    Call this when the user asks about package, MSL, reel, or tape-and-reel
    dimensions.

    Args:
        part_number: Advantech PN or manufacturer MPN.
    """
    detail = await _fetch_detail(part_number)
    return _slice(detail, ["packaging"])


@mcp.tool()
async def get_countries_of_origin(part_number: str) -> dict[str, Any]:
    """Get the list of Countries Of Origin (COO) declared by the manufacturer
    for this part.

    Call this when the user asks "where is X made?" or "what countries?".

    Args:
        part_number: Advantech PN or manufacturer MPN.
    """
    detail = await _fetch_detail(part_number)
    return _slice(detail, ["countries"])


@mcp.tool()
async def get_cross_reference(part_number: str) -> dict[str, Any]:
    """Get the cross-reference (alternate parts) table for a part.

    Returns up to a few hundred `crosses`, each a candidate replacement with
    its own MPN, manufacturer, ComID, and a match-strength indicator.  The
    xref endpoint is separate from `/api/detail` and can be large.

    Call this when the user asks "what are alternatives to X?", "what's the
    cross reference?", or "what can I replace X with?".

    Args:
        part_number: Advantech PN or manufacturer MPN.
    """
    return await _get("/api/xref", pn=part_number)


@mcp.tool()
async def get_pcn_history(part_number: str) -> dict[str, Any]:
    """Get Product Change Notifications (PCNs) issued against a part.

    PCNs are manufacturer-issued notices about changes to a part — new
    lifecycle stage, package revision, process node change, etc.  Each item
    has a date, description, and revision identifier.

    Call this when the user asks "any PCNs for X?", "has X changed recently?",
    or about end-of-life notices.

    Args:
        part_number: Advantech PN or manufacturer MPN.
    """
    return await _get("/api/pcn", pn=part_number)


@mcp.tool()
async def get_full_part_info(part_number: str) -> dict[str, Any]:
    """Get the entire SiliconExpert detail bundle in one call — overview,
    lifecycle, pricing, compliance, chemicals, documents, packaging, countries,
    and the parametric spec list.  Does NOT include cross-reference or PCNs
    (call `get_cross_reference` / `get_pcn_history` for those).

    Use this when the user asks an open-ended question like "tell me about X"
    or "summarise part X" — it's cheaper than calling every slice tool.  For
    focused questions, the per-section tools give tighter responses.

    Args:
        part_number: Advantech PN or manufacturer MPN.
    """
    detail = await _fetch_detail(part_number)
    if detail.get("error"):
        return detail
    # Drop the raw SE response blob — it's huge and duplicative.
    return {k: v for k, v in detail.items() if k != "raw"}


# --------------------------------------------------------------------------- #
# Entrypoint                                                                  #
# --------------------------------------------------------------------------- #


if __name__ == "__main__":
    log.info("SiliconExpert MCP server starting — backend=%s", BASE_URL)
    mcp.run()

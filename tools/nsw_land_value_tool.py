#!/usr/bin/env python3
"""
nsw_land_value_tool.py — NSW Valuer General land value for any NSW property.

Geocodes the address, looks up cadastral lot/plan via NSW Spatial API,
then queries the NSW Valuer General API for land value and valuation date.
Also calculates a simple AVM estimate if land area and suburb PSI data are available.

Usage: python3 nsw_land_value_tool.py "14 Addison Road Marrickville NSW 2204"
"""

import json
import logging
import re
import sys
import time
import urllib.parse
from typing import Optional

import requests

logger = logging.getLogger(__name__)

NOMINATIM_URL    = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HDRS   = {"User-Agent": "TriggerBOFF/1.0 (property buyer assistant)"}
NSW_SPATIAL      = "https://portal.spatial.nsw.gov.au/server/rest/services"
NSW_VG_API       = "https://api.valuergeneral.nsw.gov.au/landvalue/v2/properties"
NSW_VG_SCRAPE    = "https://valuation.property.nsw.gov.au/embed/propertySalesInformation"


# ── Geocode ────────────────────────────────────────────────────────────────────

def _geocode(address: str) -> Optional[tuple]:
    """Return (lat, lon) for an address via Nominatim, or None on failure."""
    query = address
    if "nsw" not in query.lower():
        query = f"{query}, NSW, Australia"
    try:
        r = requests.get(
            NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "au"},
            headers=NOMINATIM_HDRS,
            timeout=15,
        )
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        logger.warning("Geocode failed: %s", e)
    return None


# ── NSW Spatial (ArcGIS) helper ────────────────────────────────────────────────

def _arcgis_query(endpoint: str, lat: float, lon: float, fields: str = "*") -> list:
    """Query an ArcGIS FeatureServer by point intersection."""
    try:
        r = requests.get(
            endpoint,
            params={
                "where": "1=1",
                "geometry": f"{lon},{lat}",
                "geometryType": "esriGeometryPoint",
                "inSR": "4326",
                "spatialRel": "esriSpatialRelIntersects",
                "outFields": fields,
                "returnGeometry": "false",
                "f": "json",
            },
            timeout=15,
        )
        data = r.json()
        if "error" in data:
            logger.debug("ArcGIS error: %s", data["error"])
            return []
        return [f.get("attributes", {}) for f in data.get("features", [])]
    except Exception as e:
        logger.debug("ArcGIS query failed: %s", e)
        return []


def _get_lot_plan(lat: float, lon: float) -> dict:
    """
    Fetch cadastral lot/plan details from NSW Spatial API.
    Returns dict with lot_plan, lot_number, plan_label, area_sqm (if available).
    """
    result: dict = {"lot_plan": None, "lot_number": None, "plan_label": None, "land_area_sqm": None}

    lot_data = _arcgis_query(
        f"{NSW_SPATIAL}/NSW_Land_Parcel_Property_Theme/FeatureServer/8/query",
        lat, lon,
        fields="lotnumber,planlabel,lotidstring,shape_Area",
    )
    if lot_data:
        attrs = lot_data[0]
        result["lot_number"] = attrs.get("lotnumber")
        result["plan_label"] = attrs.get("planlabel")
        result["lot_plan"] = attrs.get("lotidstring") or attrs.get("planlabel")
        area = attrs.get("shape_Area")
        if area:
            area_f = float(area)
            if area_f > 0:
                # shape_Area is in square metres (GDA94 projected)
                result["land_area_sqm"] = round(area_f, 1)

    return result


# ── NSW VG API ─────────────────────────────────────────────────────────────────

def _vg_api_by_address(address: str) -> Optional[dict]:
    """
    Try NSW VG land value API with address query param.
    Returns parsed response dict, or None if unavailable.
    """
    try:
        encoded = urllib.parse.quote(address)
        r = requests.get(
            NSW_VG_API,
            params={"address": address},
            headers={"Accept": "application/json"},
            timeout=20,
        )
        if r.status_code == 200:
            data = r.json()
            if data:
                return data
        elif r.status_code in (401, 403):
            logger.info("NSW VG API requires auth (status %s) — will use fallback", r.status_code)
        elif r.status_code == 404:
            logger.info("NSW VG API: address not found")
        else:
            logger.debug("NSW VG API returned %s: %s", r.status_code, r.text[:200])
    except Exception as e:
        logger.warning("NSW VG API error: %s", e)
    return None


def _vg_api_by_lot_plan(lot: str, plan: str) -> Optional[dict]:
    """
    Try NSW VG land value API with lot/plan query params.
    """
    try:
        r = requests.get(
            NSW_VG_API,
            params={"lot": lot, "plan": plan},
            headers={"Accept": "application/json"},
            timeout=20,
        )
        if r.status_code == 200:
            data = r.json()
            if data:
                return data
    except Exception as e:
        logger.debug("NSW VG lot/plan query failed: %s", e)
    return None


def _parse_vg_response(data) -> dict:
    """
    Normalise the VG API response into a flat dict with land_value, valuation_date, land_area_sqm.
    The API may return a list or a dict with a 'properties' key.
    """
    if isinstance(data, list):
        items = data
    elif isinstance(data, dict):
        items = (
            data.get("properties")
            or data.get("results")
            or data.get("data")
            or [data]
        )
    else:
        return {}

    if not items:
        return {}

    item = items[0] if isinstance(items, list) else items

    # Normalise common field names across API versions
    land_value = (
        item.get("landValue")
        or item.get("land_value")
        or item.get("value")
        or item.get("landValueAmount")
    )
    val_date = (
        item.get("valuationDate")
        or item.get("valuation_date")
        or item.get("baseDate")
        or item.get("date")
    )
    area = (
        item.get("landArea")
        or item.get("land_area")
        or item.get("area")
        or item.get("siteArea")
    )

    return {
        "land_value": land_value,
        "valuation_date": str(val_date)[:10] if val_date else None,
        "land_area_sqm": float(area) if area else None,
        "raw": item,
    }


# ── Scrape fallback ────────────────────────────────────────────────────────────

def _vg_scrape_fallback(address: str) -> dict:
    """
    Scrape valuation.property.nsw.gov.au as a last-resort fallback.
    Returns partial data — typically just a confirmation the address exists.
    Note: This is a JavaScript-heavy SPA; we can only fetch the embed frame and
    look for any JSON blobs in the initial HTML payload.
    """
    result = {"fallback_used": True, "source": NSW_VG_SCRAPE}
    try:
        params = {"address": address}
        r = requests.get(
            NSW_VG_SCRAPE,
            params=params,
            headers={
                "User-Agent": "Mozilla/5.0 (compatible; TriggerBOFF/1.0; property buyer assistant)",
                "Accept": "text/html,application/xhtml+xml",
            },
            timeout=20,
        )
        if r.status_code != 200:
            result["error"] = f"Scrape returned HTTP {r.status_code}"
            return result

        html = r.text
        # Hunt for any JSON that looks like land value data
        land_value_match = re.search(
            r'"landValue"\s*:\s*(\d+(?:\.\d+)?)', html
        )
        date_match = re.search(
            r'"(?:valuationDate|baseDate|date)"\s*:\s*"([0-9\-T:Z+]+)"', html
        )
        area_match = re.search(
            r'"(?:landArea|area|siteArea)"\s*:\s*(\d+(?:\.\d+)?)', html
        )

        if land_value_match:
            result["land_value"] = float(land_value_match.group(1))
        if date_match:
            result["valuation_date"] = date_match.group(1)[:10]
        if area_match:
            result["land_area_sqm"] = float(area_match.group(1))

        if "land_value" not in result:
            result["note"] = (
                "NSW VG portal requires JavaScript for full data. "
                "Visit https://valuation.property.nsw.gov.au to look up manually."
            )
            result["manual_url"] = f"{NSW_VG_SCRAPE}?address={urllib.parse.quote(address)}"

    except Exception as e:
        result["error"] = str(e)

    return result


# ── Simple AVM estimate ────────────────────────────────────────────────────────

# Rough median land $/sqm by inner/middle Sydney suburb (sourced from public VG data).
# These are indicative figures for fallback AVM only — not valuations.
_SUBURB_PSI = {
    "marrickville": 3200, "newtown": 4100, "surry hills": 6500, "glebe": 5200,
    "leichhardt": 3800, "balmain": 5800, "rozelle": 5500, "annandale": 4500,
    "stanmore": 4200, "petersham": 3900, "enmore": 4000, "dulwich hill": 3100,
    "tempe": 2800, "sydenham": 2900, "st peters": 3000, "erskineville": 4300,
    "redfern": 5000, "waterloo": 4800, "zetland": 5600, "beaconsfield": 4600,
    "alexandria": 4700, "rosebery": 4200, "mascot": 3800, "botany": 2900,
    "randwick": 4500, "kingsford": 3600, "kensington": 4400, "coogee": 5500,
    "maroubra": 3700, "pagewood": 3200, "eastlakes": 3000,
    "strathfield": 3500, "burwood": 3800, "ashfield": 3600, "summer hill": 4000,
    "haberfield": 4200, "five dock": 3700, "drummoyne": 4800,
    "chatswood": 4200, "lane cove": 4000, "north sydney": 5200,
    "neutral bay": 5800, "cremorne": 5500, "mosman": 6800, "manly": 5900,
    "dee why": 4200, "brookvale": 3500, "frenchs forest": 2800,
    "hornsby": 2200, "wahroonga": 2800, "turramurra": 2600,
    "parramatta": 2800, "penrith": 1500, "blacktown": 1600,
    "liverpool": 1800, "campbelltown": 1200, "bankstown": 2400,
    "hurstville": 3000, "kogarah": 3200, "rockdale": 2800,
}


def _simple_avm(address: str, land_area_sqm: Optional[float], land_value: Optional[float]) -> Optional[dict]:
    """
    Calculate a rough AVM from median $/sqm PSI for the suburb × land area.
    Returns None if insufficient data.
    """
    if not land_area_sqm or land_area_sqm <= 0:
        return None

    # Extract suburb from address
    suburb_key = None
    lower = address.lower()
    for sub in _SUBURB_PSI:
        if sub in lower:
            suburb_key = sub
            break

    if not suburb_key:
        # If we have a land_value and land_area, still calculate $/sqm
        if land_value and land_area_sqm:
            actual_rate = land_value / land_area_sqm
            return {
                "estimate": round(land_value),  # Use the actual VG value as AVM
                "method": "VG land value used directly (no PSI suburb match)",
                "rate_per_sqm": round(actual_rate),
                "land_area_sqm": land_area_sqm,
            }
        return None

    rate = _SUBURB_PSI[suburb_key]
    estimate = round(rate * land_area_sqm, -3)  # Round to nearest $1000
    return {
        "estimate": estimate,
        "method": f"median $/sqm PSI ({suburb_key}: ${rate:,}/sqm) × {land_area_sqm:.0f} sqm",
        "rate_per_sqm": rate,
        "land_area_sqm": land_area_sqm,
        "note": "Indicative estimate only — not a formal valuation",
    }


# ── Public entry point ─────────────────────────────────────────────────────────

def run(address: str) -> dict:
    """
    Get NSW Valuer General land value and valuation date for a NSW property address.

    Args:
        address: Full or partial NSW address, e.g. "14 Addison Road Marrickville NSW 2204"

    Returns:
        dict with keys: address, land_value, valuation_date, lot_plan, land_area_sqm,
                        simple_avm_estimate, data_source, coordinates
    """
    result = {
        "address": address,
        "land_value": None,
        "valuation_date": None,
        "lot_plan": None,
        "land_area_sqm": None,
        "simple_avm_estimate": None,
        "data_source": None,
        "coordinates": None,
    }

    # ── Step 1: Geocode ────────────────────────────────────────────────────────
    coords = _geocode(address)
    if not coords:
        result["error"] = f"Could not geocode address: {address}"
        return result

    lat, lon = coords
    result["coordinates"] = {"lat": round(lat, 6), "lon": round(lon, 6)}
    time.sleep(1)  # Respect Nominatim rate limit

    # ── Step 2: Cadastral lot/plan from NSW Spatial ────────────────────────────
    cadastral = _get_lot_plan(lat, lon)
    result["lot_plan"] = cadastral.get("lot_plan")
    if cadastral.get("land_area_sqm"):
        result["land_area_sqm"] = cadastral["land_area_sqm"]

    # ── Step 3: NSW VG API — try address first ─────────────────────────────────
    vg_data = _vg_api_by_address(address)
    data_source = "NSW VG API (address)"

    # ── Step 4: If address lookup failed, try lot/plan ─────────────────────────
    if not vg_data and cadastral.get("lot_number") and cadastral.get("plan_label"):
        vg_data = _vg_api_by_lot_plan(
            str(cadastral["lot_number"]), str(cadastral["plan_label"])
        )
        if vg_data:
            data_source = "NSW VG API (lot/plan)"

    # ── Step 5: Parse VG response ──────────────────────────────────────────────
    if vg_data:
        parsed = _parse_vg_response(vg_data)
        result["land_value"] = parsed.get("land_value")
        result["valuation_date"] = parsed.get("valuation_date")
        result["data_source"] = data_source
        # Override land area from VG if available
        if parsed.get("land_area_sqm"):
            result["land_area_sqm"] = parsed["land_area_sqm"]

    # ── Step 6: Scrape fallback if API unavailable ─────────────────────────────
    if not vg_data or not result.get("land_value"):
        logger.info("NSW VG API did not return data — trying scrape fallback")
        scraped = _vg_scrape_fallback(address)
        if scraped.get("land_value"):
            result["land_value"] = scraped["land_value"]
            result["valuation_date"] = scraped.get("valuation_date")
            result["data_source"] = "NSW VG portal (scraped)"
            if scraped.get("land_area_sqm") and not result["land_area_sqm"]:
                result["land_area_sqm"] = scraped["land_area_sqm"]
        else:
            if not result["data_source"]:
                result["data_source"] = "NSW Spatial (cadastral only)"
            result["vg_note"] = (
                scraped.get("note")
                or "NSW VG API unavailable or address not matched. "
                   "Visit https://valuation.property.nsw.gov.au to look up manually."
            )
            result["manual_lookup_url"] = (
                f"https://valuation.property.nsw.gov.au/embed/propertySalesInformation"
                f"?address={urllib.parse.quote(address)}"
            )

    # ── Step 7: Simple AVM estimate ────────────────────────────────────────────
    avm = _simple_avm(address, result.get("land_area_sqm"), result.get("land_value"))
    if avm:
        result["simple_avm_estimate"] = avm

    return result


# ── CLI ────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    address = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "14 Addison Road Marrickville NSW 2204"
    print(json.dumps(run(address), indent=2, default=str))


# ── Tool Registry ──────────────────────────────────────────────────────────────

try:
    from tools.registry import registry

    registry.register(
        name="nsw_land_value",
        toolset="property_data",
        schema={
            "name": "nsw_land_value",
            "description": (
                "Get NSW Valuer General land value and valuation date for any NSW "
                "property address. Also returns cadastral lot/plan details and a simple "
                "AVM estimate (land $/sqm × area). Useful for assessing whether a "
                "property is priced fairly relative to its land value, especially for "
                "houses and development sites."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": (
                            "Full NSW property address e.g. "
                            "'14 Addison Road Marrickville NSW 2204'"
                        ),
                    },
                },
                "required": ["address"],
            },
        },
        handler=lambda args, **kw: json.dumps(
            run(args.get("address", "")), indent=2, default=str
        ),
        check_fn=lambda: True,
        requires_env=[],
    )

except ImportError:
    # Running outside Hermes context (e.g. direct testing)
    pass

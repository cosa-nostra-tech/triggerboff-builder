#!/usr/bin/env python3
"""
strata_tool.py — NSW strata scheme information for any address.

Looks up the strata plan number, scheme name, lot count, managing agent,
registration date and scheme type for a NSW strata property. Provides
direct links for levy history and manual strata scheme searches.

Usage: python3 strata_tool.py "4/14 Addison Road Marrickville NSW 2204"
"""

import json, logging, re, sys, time
import requests

logger = logging.getLogger(__name__)

NOMINATIM_URL     = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "TriggerBOFF/1.0 (property buyer assistant)"}
NSW_SPATIAL       = "https://portal.spatial.nsw.gov.au/server/rest/services"
STRATA_HUB_API    = "https://strataservices.fairtrading.nsw.gov.au/api/v2/schemeInfo"
FAIRTRADING_SEARCH = (
    "https://www.fairtrading.nsw.gov.au/housing-and-property/strata-and-community-living"
    "/about-strata-schemes/searching-for-strata-information"
)
PROPERTY_REGISTRY = "https://www.propertyregistry.com.au"

# NSW Spatial address geocoder — returns parcel-centred coordinates
NSW_ADDR_LAYER = f"{NSW_SPATIAL}/NSW_Geocoded_Addressing_Theme/FeatureServer/1/query"


def _geocode(address):
    """
    Geocode via Nominatim, falling back to a plain NSW address append.
    Returns (lat, lon) or None.
    """
    query = address if "nsw" in address.lower() else f"{address}, NSW, Australia"
    try:
        r = requests.get(NOMINATIM_URL,
            params={"q": query, "format": "json", "limit": 1, "countrycodes": "au"},
            headers=NOMINATIM_HEADERS, timeout=15)
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        logger.warning("Geocode failed: %s", e)
    return None


def _arcgis_query(endpoint, lat, lon, fields="*"):
    """Spatial point-in-polygon query against an ArcGIS FeatureServer layer."""
    try:
        r = requests.get(endpoint, params={
            "where": "1=1", "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint", "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": fields, "returnGeometry": "false", "f": "json",
        }, timeout=15)
        data = r.json()
        if "error" in data:
            return []
        return [f.get("attributes", {}) for f in data.get("features", [])]
    except Exception as e:
        logger.debug("ArcGIS query failed: %s", e)
        return []


def _geocode_nsw_spatial(street_address):
    """
    Look up an exact street address in the NSW Spatial address layer.
    Returns (lat, lon) using the parcel-centred geometry, or None.
    This gives more accurate coordinates for strata buildings than Nominatim.
    """
    # Strip unit prefix e.g. "4/14" → "14", "Unit 4, 14" → "14"
    clean = re.sub(r'^\d+\s*/\s*', '', street_address.strip())
    clean = re.sub(r'^(?:Unit|Apt|Flat|Suite)\s*\d+[,\s]+', '', clean, flags=re.IGNORECASE)

    # Keep only street number + name + suburb for the where clause
    # e.g. "14 Addison Road Marrickville NSW 2204" → "14 ADDISON ROAD MARRICKVILLE"
    clean_upper = re.sub(r'\s+NSW.*$', '', clean, flags=re.IGNORECASE).upper().strip()

    try:
        r = requests.get(NSW_ADDR_LAYER, params={
            "where": f"address = '{clean_upper}'",
            "outFields": "address",
            "returnGeometry": "true", "outSR": "4326",
            "resultRecordCount": 1, "f": "json",
        }, timeout=15)
        data = r.json()
        features = data.get("features", [])
        if features:
            geom = features[0].get("geometry", {})
            x, y = geom.get("x"), geom.get("y")
            if x and y:
                return float(y), float(x)
    except Exception as e:
        logger.debug("NSW Spatial address geocode failed: %s", e)
    return None


def _extract_plan_number(lot_plan_str):
    """Extract strata plan number from lot/plan string e.g. 'SP12345' or '4//SP12345'."""
    if not lot_plan_str:
        return None
    m = re.search(r'\bSP\s*(\d+)', lot_plan_str, re.IGNORECASE)
    if m:
        return f"SP{m.group(1)}"
    return None


def _query_strata_hub(address=None, lot_plan=None, plan_number=None):
    """
    Try the NSW Strata Hub API with various search strategies.
    Returns a parsed dict or None if the API is unavailable / property not found.
    """
    params_list = []

    if plan_number:
        num = re.sub(r'\D', '', plan_number)
        if num:
            params_list.append({"planNumber": num})
            params_list.append({"strataplanNumber": num})
            params_list.append({"schemePlanNumber": num})

    if address:
        params_list.append({"address": address})
        params_list.append({"searchAddress": address})

    headers = {
        "Accept": "application/json",
        "User-Agent": NOMINATIM_HEADERS["User-Agent"],
    }

    for params in params_list:
        try:
            r = requests.get(STRATA_HUB_API, params=params, headers=headers, timeout=15)
            logger.debug("Strata Hub %s → HTTP %s", params, r.status_code)

            if r.status_code in (401, 403):
                logger.info("Strata Hub API requires authentication (HTTP %s)", r.status_code)
                return None

            if r.status_code == 404:
                continue

            if r.status_code != 200:
                continue

            data = r.json()

            items = []
            if isinstance(data, list):
                items = data
            elif isinstance(data, dict):
                for key in ("results", "schemes", "data", "schemeInfo", "items"):
                    if key in data and isinstance(data[key], list):
                        items = data[key]
                        break
                if not items and any(k in data for k in ("schemeName", "planNumber", "lotCount")):
                    items = [data]

            if items:
                s = items[0]
                return {
                    "strata_plan_number": (
                        s.get("planNumber") or s.get("schemePlanNumber") or
                        s.get("strataplanNumber") or s.get("strataSchemeId") or plan_number
                    ),
                    "scheme_name":       s.get("schemeName") or s.get("name") or s.get("schemeTitle"),
                    "lot_count":         s.get("lotCount") or s.get("numberOfLots") or s.get("totalLots"),
                    "registration_date": (
                        s.get("registrationDate") or s.get("dateRegistered") or
                        s.get("created") or s.get("registeredDate")
                    ),
                    "managing_agent": (
                        s.get("managingAgent") or s.get("agentName") or
                        s.get("strataManager") or s.get("managementCompany")
                    ),
                    "scheme_type": (
                        s.get("schemeType") or s.get("type") or
                        s.get("landUse") or s.get("category")
                    ),
                }

        except requests.exceptions.ConnectionError as e:
            logger.info("Strata Hub API connection error (DNS/network): %s", e)
            return None  # Whole API unreachable — skip remaining param attempts
        except requests.exceptions.JSONDecodeError:
            logger.debug("Strata Hub returned non-JSON for params %s", params)
            continue
        except Exception as e:
            logger.debug("Strata Hub query error: %s", e)
            continue

    return None


def _scrape_fairtrading(address):
    """
    Attempt to extract strata info from Fair Trading's public search page.
    The page is largely client-side rendered, so we can only pick up any
    server-rendered metadata. Returns a partial dict or None.
    """
    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml",
        }
        r = requests.get(FAIRTRADING_SEARCH, headers=headers, timeout=15)
        if r.status_code != 200:
            return None

        plan_matches = re.findall(r'\bSP\s*(\d{4,6})\b', r.text, re.IGNORECASE)
        if plan_matches:
            return {
                "strata_plan_number": f"SP{plan_matches[0]}",
                "notes_from_scrape": "Partial data extracted from Fair Trading search page.",
            }
    except Exception as e:
        logger.debug("Fair Trading scrape failed: %s", e)
    return None


def run(address):
    result = {
        "address":          address,
        "lot_plan":         None,
        "strata_plan_number": None,
        "scheme_name":      None,
        "lot_count":        None,
        "registration_date": None,
        "managing_agent":   None,
        "scheme_type":      None,
        "levies_check_url": PROPERTY_REGISTRY,
        "manual_lookup_url": FAIRTRADING_SEARCH,
        "notes": [],
    }

    # ── 1. Geocode — try NSW Spatial first for parcel-accurate coords ────────
    coords = _geocode_nsw_spatial(address)
    if coords:
        result["notes"].append("Geocoded via NSW Spatial address layer (parcel-accurate).")
    else:
        coords = _geocode(address)
        if coords:
            result["notes"].append("Geocoded via Nominatim.")

    if not coords:
        result["error"] = f"Could not geocode address: {address}"
        result["notes"].append(f"Manual lookup: {FAIRTRADING_SEARCH}")
        return result

    lat, lon = coords
    time.sleep(1)  # Nominatim rate-limit courtesy delay

    # ── 2. Cadastral lot / plan from NSW Spatial Lot layer ───────────────────
    lot_data = _arcgis_query(
        f"{NSW_SPATIAL}/NSW_Land_Parcel_Property_Theme/FeatureServer/8/query",
        lat, lon,
        fields="lotnumber,planlabel,lotidstring,hasstratum",
    )
    if lot_data:
        attrs = lot_data[0]
        result["lot_plan"] = attrs.get("lotidstring") or attrs.get("planlabel")
        # hasstratum=1 means this parcel has strata lots above/below it
        if attrs.get("hasstratum") == 1 and not _extract_plan_number(result["lot_plan"]):
            result["notes"].append(
                "Parcel has stratum flag set — strata lots may exist on this land parcel."
            )

    prop_data = _arcgis_query(
        f"{NSW_SPATIAL}/NSW_Land_Parcel_Property_Theme/FeatureServer/12/query",
        lat, lon, fields="address,propid",
    )
    if prop_data:
        registered_addr = prop_data[0].get("address")
        if registered_addr:
            result["notes"].append(f"Registered address (NSW Spatial): {registered_addr}")

    plan_number = _extract_plan_number(result["lot_plan"])
    if plan_number:
        result["strata_plan_number"] = plan_number

    # ── 3. NSW Strata Hub API ────────────────────────────────────────────────
    strata = _query_strata_hub(
        address=address,
        lot_plan=result["lot_plan"],
        plan_number=plan_number,
    )

    if strata:
        result["strata_plan_number"] = strata.get("strata_plan_number") or result["strata_plan_number"]
        result["scheme_name"]        = strata.get("scheme_name")
        result["lot_count"]          = strata.get("lot_count")
        result["registration_date"]  = strata.get("registration_date")
        result["managing_agent"]     = strata.get("managing_agent")
        result["scheme_type"]        = strata.get("scheme_type")
        result["notes"].append("Strata data retrieved from NSW Strata Hub API.")
    else:
        # ── 4. Fair Trading page scrape fallback ─────────────────────────────
        logger.info("Strata Hub API unavailable; attempting Fair Trading page scrape.")
        scraped = _scrape_fairtrading(address)
        if scraped:
            result["strata_plan_number"] = (
                scraped.get("strata_plan_number") or result["strata_plan_number"]
            )
            result["notes"].append(
                scraped.get("notes_from_scrape", "Partial data from Fair Trading page.")
            )
        else:
            result["notes"].append(
                "Strata Hub API unavailable and automated scrape returned no data. "
                "Use the manual lookup URLs below."
            )

    # ── 5. Always include lookup URLs ────────────────────────────────────────
    sp = result["strata_plan_number"]
    if sp:
        sp_num = re.sub(r'\D', '', sp)
        result["levies_check_url"] = (
            f"{PROPERTY_REGISTRY}/strata/{sp_num}" if sp_num else PROPERTY_REGISTRY
        )

    result["notes"] += [
        f"Levy history and owners corporation details: {PROPERTY_REGISTRY}",
        f"Official strata scheme search (Fair Trading): {FAIRTRADING_SEARCH}",
        "Strata Hub portal: https://www.stratahub.com.au",
    ]

    # ── 6. Flag likely non-strata title ─────────────────────────────────────
    if not result["strata_plan_number"] and result["lot_plan"]:
        lp = (result["lot_plan"] or "").upper()
        if not any(pfx in lp for pfx in ("SP", "STRATA")):
            result["notes"].append(
                "Lot/plan does not appear to be strata title (no SP prefix found). "
                "This property may be Torrens title or community title — "
                "confirm at https://online.land.nsw.gov.au/lrs/app/titles"
            )

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    address = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "4/14 Addison Road Marrickville NSW 2204"
    print(json.dumps(run(address), indent=2))


try:
    from tools.registry import registry
    registry.register(
        name="strata_info",
        toolset="property_data",
        schema={
            "name": "strata_info",
            "description": (
                "Get NSW strata scheme information for any address including plan number, "
                "lot count, managing agent and links to levy history checks."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "Full NSW address e.g. '4/14 Addison Road Marrickville NSW 2204'",
                    }
                },
                "required": ["address"],
            },
        },
        handler=lambda args, **kw: json.dumps(run(args.get("address", "")), indent=2),
        check_fn=lambda: True,
        requires_env=[],
    )
except ImportError:
    pass

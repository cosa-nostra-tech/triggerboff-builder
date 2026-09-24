#!/usr/bin/env python3
"""
nsw_overlays_tool.py — Environmental overlays for any NSW property address.

Checks bushfire prone land (live NSW Spatial data), and provides direct links
for flood risk and heritage checks (those layers require auth tokens).

Usage: python3 nsw_overlays_tool.py "42 Smith Street Surry Hills NSW 2010"
"""

import json, logging, sys, time
import requests

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "TriggerBOFF/1.0 (property buyer assistant)"}
NSW_SPATIAL = "https://portal.spatial.nsw.gov.au/server/rest/services"


def _geocode(address):
    if "nsw" not in address.lower():
        address = f"{address}, NSW, Australia"
    try:
        r = requests.get(NOMINATIM_URL,
            params={"q": address, "format": "json", "limit": 1, "countrycodes": "au"},
            headers=NOMINATIM_HEADERS, timeout=15)
        data = r.json()
        if data:
            return float(data[0]["lat"]), float(data[0]["lon"])
    except Exception as e:
        logger.warning("Geocode failed: %s", e)
    return None


def _arcgis_query(endpoint, lat, lon, fields="*"):
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


def run(address):
    result = {
        "address": address, "coordinates": None,
        "flood_risk": "check required", "flood_check_url": "https://www.floodcheck.com.au",
        "bushfire_prone": "unknown", "bushfire_category": None,
        "heritage_listed": "check required",
        "heritage_check_url": "https://www.heritage.nsw.gov.au/discover-heritage/heritage-register",
        "lot_plan": None, "registered_address": None, "notes": [],
    }

    coords = _geocode(address)
    if not coords:
        result["error"] = f"Could not geocode: {address}"
        return result

    lat, lon = coords
    result["coordinates"] = {"lat": round(lat, 6), "lon": round(lon, 6)}
    time.sleep(1)

    # Cadastral lot info
    lot_data = _arcgis_query(f"{NSW_SPATIAL}/NSW_Land_Parcel_Property_Theme/FeatureServer/8/query",
                              lat, lon, fields="lotnumber,planlabel,lotidstring")
    if lot_data:
        result["lot_plan"] = lot_data[0].get("lotidstring") or lot_data[0].get("planlabel")

    prop_data = _arcgis_query(f"{NSW_SPATIAL}/NSW_Land_Parcel_Property_Theme/FeatureServer/12/query",
                               lat, lon, fields="address,propid")
    if prop_data:
        result["registered_address"] = prop_data[0].get("address")

    # Bushfire prone land
    for ep in [
        f"{NSW_SPATIAL}/Hosted/NSW_BushFire_Prone_Land/FeatureServer/0/query",
        "https://geo.seed.nsw.gov.au/arcgis/rest/services/Hosted/BushFireProneLand/FeatureServer/0/query",
    ]:
        bf = _arcgis_query(ep, lat, lon, fields="category,d_category")
        if bf:
            cat = bf[0].get("d_category") or bf[0].get("category") or "yes"
            result["bushfire_prone"] = "yes"
            result["bushfire_category"] = str(cat)
            break
    else:
        result["bushfire_prone"] = "no"

    result["notes"] = [
        "Flood overlay: NSW flood layer data requires authentication. Check https://www.floodcheck.com.au or your local council's interactive flood map.",
        "Heritage: Check NSW Heritage Register at https://www.heritage.nsw.gov.au/discover-heritage/heritage-register",
        "Bushfire: Verify at https://www.rfs.nsw.gov.au/plan-and-prepare/building-in-a-bush-fire-area",
    ]
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    address = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "14 Addison Road Marrickville NSW 2204"
    print(json.dumps(run(address), indent=2))


try:
    from tools.registry import registry
    registry.register(
        name="nsw_property_overlays",
        toolset="property_data",
        schema={
            "name": "nsw_property_overlays",
            "description": "Check environmental overlays for any NSW property: flood risk (check link), bushfire prone land (live data), heritage listing (check link), and cadastral lot/plan details.",
            "parameters": {"type": "object",
                "properties": {"address": {"type": "string", "description": "Full NSW address e.g. '14 Addison Road Marrickville NSW 2204'"}},
                "required": ["address"]}
        },
        handler=lambda args, **kw: json.dumps(run(args.get("address", "")), indent=2),
        check_fn=lambda: True, requires_env=[],
    )
except ImportError:
    pass

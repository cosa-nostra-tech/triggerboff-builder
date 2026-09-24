#!/usr/bin/env python3
"""
school_catchment_tool.py — NSW public school catchment zones for any address.

Downloads the NSW DoE school intake zones shapefile (public, free) and does
point-in-polygon lookup for primary and secondary schools.

Usage: python3 school_catchment_tool.py "14 Addison Road Marrickville NSW"
"""

import json, logging, os, sys, zipfile
from pathlib import Path
import requests

logger = logging.getLogger(__name__)

CATCHMENT_ZIP_URL = (
    "https://data.nsw.gov.au/data/dataset/8b1e8161-7252-43d9-81ed-6311569cb1d7"
    "/resource/32d6f502-ddb1-45d9-b114-5e34ddfd33ac/download/catchments.zip"
)
CACHE_DIR = Path(os.getenv("NSW_CATCHMENT_CACHE", "/tmp/nsw_catchment_cache"))
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "TriggerBOFF/1.0 (property buyer assistant)"}


def _ensure_shapefile():
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    primary_shp = CACHE_DIR / "catchments_primary.shp"
    secondary_shp = CACHE_DIR / "catchments_secondary.shp"
    if primary_shp.exists() and secondary_shp.exists():
        return CACHE_DIR
    logger.info("Downloading NSW school catchment shapefile (~8MB)...")
    r = requests.get(CATCHMENT_ZIP_URL, timeout=120)
    r.raise_for_status()
    zip_path = CACHE_DIR / "catchments.zip"
    zip_path.write_bytes(r.content)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(CACHE_DIR)
    logger.info("Extracted to %s", CACHE_DIR)
    return CACHE_DIR


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
        logger.warning("Geocoding failed: %s", e)
    return None


def get_school_catchments(address):
    try:
        import geopandas as gpd
        from shapely.geometry import Point
    except ImportError:
        return {
            "error": "geopandas/shapely not installed. Run: pip install geopandas shapely",
            "fallback": f"Check manually at https://www.schoolfinder.education.nsw.gov.au",
        }

    coords = _geocode(address)
    if not coords:
        return {"error": f"Could not geocode: {address}"}

    lat, lon = coords

    try:
        shp_dir = _ensure_shapefile()
    except Exception as e:
        return {"error": f"Could not download school catchment data: {e}",
                "fallback": "https://www.schoolfinder.education.nsw.gov.au"}

    point = Point(lon, lat)
    schools = []

    for school_type, shp_name in [("Primary", "catchments_primary.shp"), ("Secondary", "catchments_secondary.shp")]:
        shp_path = shp_dir / shp_name
        if not shp_path.exists():
            continue
        try:
            gdf = gpd.read_file(shp_path)
            if gdf.crs and gdf.crs.to_epsg() != 4326:
                gdf = gdf.to_crs(epsg=4326)
            for _, row in gdf[gdf.geometry.contains(point)].iterrows():
                years = []
                if row.get("KINDERGART") == "Y": years.append("K")
                for y in range(1, 13):
                    if row.get(f"YEAR{y}") == "Y": years.append(str(y))
                schools.append({
                    "type": school_type,
                    "name": row.get("USE_DESC", "Unknown"),
                    "school_id": str(row.get("USE_ID", "")),
                    "years": ", ".join(years) if years else "unknown",
                })
        except Exception as e:
            logger.warning("Error reading %s: %s", shp_name, e)

    result = {"address": address, "coordinates": {"lat": lat, "lon": lon}, "schools": schools}
    if not schools:
        result["note"] = (
            "No school catchment found. Property may be in a gap zone. "
            "Verify at: https://www.schoolfinder.education.nsw.gov.au"
        )
    else:
        result["note"] = "Source: NSW Department of Education school intake zones."
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    address = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "14 Addison Road Marrickville NSW"
    print(json.dumps(get_school_catchments(address), indent=2))


try:
    from tools.registry import registry
    registry.register(
        name="get_school_catchments",
        toolset="property_data",
        schema={
            "name": "get_school_catchments",
            "description": "Find which NSW public school catchment zones (primary and secondary) a property falls within. Uses official DoE intake zone boundaries.",
            "parameters": {"type": "object",
                "properties": {"address": {"type": "string", "description": "Full property address e.g. '14 Addison Road Marrickville NSW 2204'"}},
                "required": ["address"]}
        },
        handler=lambda args, **kw: json.dumps(get_school_catchments(args.get("address", "")), indent=2),
        check_fn=lambda: True, requires_env=[],
    )
except ImportError:
    pass

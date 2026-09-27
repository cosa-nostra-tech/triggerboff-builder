#!/usr/bin/env python3
"""
nsw_planning_tool.py — NSW planning controls and development applications for any NSW address.

Fetches land zoning, height of buildings limit, floor space ratio, minimum lot size,
and LEP name from the NSW ePlanning ArcGIS MapServer. Also retrieves recent
development applications from the NSW Planning Portal DA Tracker.

Usage: python3 nsw_planning_tool.py "14 Addison Road Marrickville NSW"
"""

import json, logging, sys, time
from html.parser import HTMLParser
from urllib.parse import quote_plus
import requests

logger = logging.getLogger(__name__)

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
NOMINATIM_HEADERS = {"User-Agent": "TriggerBOFF/1.0 (property buyer assistant)"}

# NSW ePlanning Primary Planning Layers (ArcGIS MapServer)
# Layer IDs: 2=Land Zoning, 5=Height of Building, 1=Floor Space Ratio, 4=Lot Size
EPI_MAPSERVER = (
    "https://mapprod3.environment.nsw.gov.au/arcgis/rest/services"
    "/Planning/EPI_Primary_Planning_Layers/MapServer"
)
EPI_LAYER_ZONING = 2
EPI_LAYER_HEIGHT = 5
EPI_LAYER_FSR = 1
EPI_LAYER_LOT_SIZE = 4

DA_TRACKER_URL = "https://datracker.planning.nsw.gov.au/Home/Index"


# ---------------------------------------------------------------------------
# HTML parser for DA tracker table
# ---------------------------------------------------------------------------

class _DATableParser(HTMLParser):
    """Extract rows from the first HTML table found on the DA tracker page."""

    def __init__(self):
        super().__init__()
        self._in_table = False
        self._in_row = False
        self._in_cell = False
        self._current_row = []
        self._current_cell = []
        self.headers = []
        self.rows = []
        self._header_done = False

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._in_table = True
        elif tag == "tr" and self._in_table:
            self._in_row = True
            self._current_row = []
        elif tag in ("td", "th") and self._in_row:
            self._in_cell = True
            self._current_cell = []

    def handle_endtag(self, tag):
        if tag == "table":
            self._in_table = False
        elif tag == "tr" and self._in_row:
            self._in_row = False
            if self._current_row:
                if not self._header_done:
                    self.headers = self._current_row
                    self._header_done = True
                else:
                    self.rows.append(self._current_row)
        elif tag in ("td", "th") and self._in_cell:
            self._in_cell = False
            cell_text = " ".join("".join(self._current_cell).split())
            self._current_row.append(cell_text)

    def handle_data(self, data):
        if self._in_cell:
            self._current_cell.append(data)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def _arcgis_point_query(layer_id, lat, lon):
    """Query a single NSW EPI MapServer layer at the given point."""
    url = f"{EPI_MAPSERVER}/{layer_id}/query"
    try:
        r = requests.get(url, params={
            "geometry": f"{lon},{lat}",
            "geometryType": "esriGeometryPoint",
            "inSR": "4326",
            "spatialRel": "esriSpatialRelIntersects",
            "outFields": "*",
            "returnGeometry": "false",
            "f": "json",
        }, headers=NOMINATIM_HEADERS, timeout=20)
        data = r.json()
        if "error" in data:
            logger.debug("ArcGIS layer %s error: %s", layer_id, data["error"])
            return []
        return [f.get("attributes", {}) for f in data.get("features", [])]
    except Exception as e:
        logger.debug("ArcGIS layer %s query failed: %s", layer_id, e)
        return []


def _get_planning_controls(lat, lon):
    """Query the NSW EPI MapServer for zoning, height, FSR, and lot size."""
    controls = {
        "zone_code": None,
        "zone_label": None,
        "lep_name": None,
        "height_of_buildings_m": None,
        "floor_space_ratio": None,
        "min_lot_size_sqm": None,
    }

    # Land Zoning (layer 2): SYM_CODE = zone code (e.g. "R2"), LAY_CLASS = label
    zoning = _arcgis_point_query(EPI_LAYER_ZONING, lat, lon)
    if zoning:
        attrs = zoning[0]
        controls["zone_code"] = attrs.get("SYM_CODE") or None
        controls["zone_label"] = attrs.get("LAY_CLASS") or None
        controls["lep_name"] = attrs.get("EPI_NAME") or None
        logger.debug("Zoning attrs: %s", attrs)

    # Height of Buildings (layer 5): MAX_B_H = height in metres
    height = _arcgis_point_query(EPI_LAYER_HEIGHT, lat, lon)
    if height:
        attrs = height[0]
        raw = attrs.get("MAX_B_H") or attrs.get("MAX_B_H_M")
        if raw is not None:
            try:
                controls["height_of_buildings_m"] = float(raw)
            except (ValueError, TypeError):
                controls["height_of_buildings_m"] = raw
        logger.debug("Height attrs: %s", attrs)

    # Floor Space Ratio (layer 1): FSR field
    fsr = _arcgis_point_query(EPI_LAYER_FSR, lat, lon)
    if fsr:
        attrs = fsr[0]
        raw = attrs.get("FSR")
        if raw is not None:
            try:
                controls["floor_space_ratio"] = float(raw)
            except (ValueError, TypeError):
                controls["floor_space_ratio"] = raw
        logger.debug("FSR attrs: %s", attrs)

    # Minimum Lot Size (layer 4): LOT_SIZE field
    lot = _arcgis_point_query(EPI_LAYER_LOT_SIZE, lat, lon)
    if lot:
        attrs = lot[0]
        raw = attrs.get("LOT_SIZE")
        if raw is not None:
            try:
                controls["min_lot_size_sqm"] = float(raw)
            except (ValueError, TypeError):
                controls["min_lot_size_sqm"] = raw
        logger.debug("Lot size attrs: %s", attrs)

    return controls


def _get_recent_das(address, max_results=5):
    """Recent DAs for the address, from the open NSW Planning Portal archive.

    This previously scraped `datracker.planning.nsw.gov.au`, a host that does not
    resolve at all, so it could only ever return the failure fallback. It now
    delegates to nsw_da_tracker_tool, which queries the open ArcGIS DA archive
    behind the Planning Portal.

    That archive is frozen at April 2023, so the result carries `data_through`
    and `data_freshness` and must be presented as historical context.
    """
    try:
        from nsw_da_tracker_tool import run as da_run
    except ImportError as e:  # pragma: no cover
        logger.warning("DA archive tool unavailable: %s", e)
        return {"note": f"DA history unavailable: {e}"}

    suburb = postcode = None
    try:
        from geocode_tool import geocode_address
        g = geocode_address(address)
        if isinstance(g, dict):
            suburb = (g.get("suburb") or "").upper() or None
            postcode = g.get("postcode") or None
    except Exception as e:  # noqa: BLE001
        logger.debug("Could not geocode %r for DA lookup: %s", address, e)

    # The archive indexes on suburb/postcode, not street address, so prefer the
    # geocoded suburb and fall back to a street match only when geocoding failed.
    result = da_run(
        suburb=suburb, postcode=postcode,
        address=None if suburb else address,
        limit=max_results,
    )
    if result.get("error"):
        return {"note": result["error"], "data_through": result.get("data_through", "")}

    return {
        "count": result.get("count", 0),
        "development_applications": result.get("development_applications", []),
        "demolition_signals": result.get("demolition_signals", []),
        "subdivision_signals": result.get("subdivision_signals", []),
        "data_source": result.get("data_source", ""),
        "data_through": result.get("data_through", ""),
        "data_freshness": result.get("data_freshness", ""),
    }


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def run(address):
    result = {
        "address": address,
        "coordinates": None,
        "zone": None,
        "zone_code": None,
        "zone_label": None,
        "lep_name": None,
        "height_of_buildings_m": None,
        "floor_space_ratio": None,
        "min_lot_size_sqm": None,
        "recent_development_applications": [],
        "da_tracker_url": None,
        "notes": [],
    }

    # 1. Geocode
    coords = _geocode(address)
    if not coords:
        result["error"] = f"Could not geocode: {address}"
        return result

    lat, lon = coords
    result["coordinates"] = {"lat": round(lat, 6), "lon": round(lon, 6)}
    time.sleep(1)  # be polite to Nominatim

    # 2. NSW ePlanning controls (ArcGIS MapServer)
    controls = _get_planning_controls(lat, lon)
    result.update(controls)

    # Build human-readable zone string
    if controls.get("zone_code") and controls.get("zone_label"):
        result["zone"] = f"{controls['zone_code']} {controls['zone_label']}"
    elif controls.get("zone_code"):
        result["zone"] = str(controls["zone_code"])
    elif controls.get("zone_label"):
        result["zone"] = controls["zone_label"]

    # 3. Development applications (open NSW Planning Portal archive)
    da_search_address = address if "nsw" in address.lower() else f"{address} NSW"
    da_result = _get_recent_das(da_search_address)
    result["recent_development_applications"] = da_result.get("development_applications", [])
    if da_result.get("demolition_signals"):
        result["demolition_signals"] = da_result["demolition_signals"]
    if da_result.get("subdivision_signals"):
        result["subdivision_signals"] = da_result["subdivision_signals"]
    result["da_count"] = da_result.get("count", 0)
    result["da_data_through"] = da_result.get("data_through", "")
    result["da_source"] = da_result.get("data_source", "")
    # Say plainly how current the DA data is, and where to get live activity.
    if da_result.get("data_freshness"):
        result["notes"].append(da_result["data_freshness"])
    if da_result.get("note"):
        result["notes"].append(da_result["note"])

    result["notes"].append(
        "Planning controls sourced from NSW ePlanning Spatial Viewer (EPI Primary Planning Layers). "
        "Always verify with your local council LEP before making decisions."
    )
    result["notes"].append(
        "For live DA activity, use your council's DA register or "
        "https://www.planningportal.nsw.gov.au/"
    )
    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    address = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "14 Addison Road Marrickville NSW"
    print(json.dumps(run(address), indent=2))


try:
    from tools.registry import registry
    registry.register(
        name="nsw_planning_overlays",
        toolset="property_data",
        schema={
            "name": "nsw_planning_overlays",
            "description": (
                "Get zoning, height limits, floor space ratio, minimum lot size, LEP name, "
                "and recent development applications for any NSW address."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "Full NSW address e.g. '14 Addison Road Marrickville NSW 2204'",
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

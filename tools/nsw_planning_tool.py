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
    """Scrape the NSW DA Tracker for recent DAs near the address.

    Returns a list of DA dicts, or a fallback dict with a direct URL if scraping fails.
    """
    encoded = quote_plus(address)
    tracker_url = f"{DA_TRACKER_URL}?pageIndex=1&pageSize={max_results}&address={encoded}"
    das = []

    try:
        r = requests.get(
            tracker_url,
            headers={"User-Agent": "TriggerBOFF/1.0 (property buyer assistant)"},
            timeout=20,
        )
        r.raise_for_status()
        html = r.text

        parser = _DATableParser()
        parser.feed(html)

        if parser.rows:
            for row in parser.rows[:max_results]:
                # Pad row to header length
                padded = row + [""] * max(0, len(parser.headers) - len(row))
                da_dict = dict(zip(parser.headers, padded))

                # Normalise common field names across DA Tracker layout variants
                da = {
                    "da_number": (
                        da_dict.get("DA Number") or da_dict.get("Application Number")
                        or da_dict.get("DA No") or ""
                    ),
                    "description": (
                        da_dict.get("Description") or da_dict.get("Proposal")
                        or da_dict.get("Development Description") or ""
                    ),
                    "address": (
                        da_dict.get("Address") or da_dict.get("Property Address") or ""
                    ),
                    "lodgement_date": (
                        da_dict.get("Lodgement Date") or da_dict.get("Date Lodged")
                        or da_dict.get("Lodged") or ""
                    ),
                    "status": (
                        da_dict.get("Status") or da_dict.get("DA Status") or ""
                    ),
                }
                # Preserve any extra fields with values
                known_keys = {
                    "DA Number", "Application Number", "DA No",
                    "Description", "Proposal", "Development Description",
                    "Address", "Property Address",
                    "Lodgement Date", "Date Lodged", "Lodged",
                    "Status", "DA Status",
                }
                extra = {k: v for k, v in da_dict.items() if k not in known_keys and v}
                if extra:
                    da["extra"] = extra
                das.append(da)
        else:
            logger.info("DA Tracker: no table rows found in response")

    except Exception as e:
        logger.warning("DA Tracker scrape failed: %s", e)

    if not das:
        return {
            "note": "Could not retrieve DAs automatically. Check directly at:",
            "da_tracker_url": tracker_url,
        }

    return das


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

    # 3. DA tracker
    da_search_address = address if "nsw" in address.lower() else f"{address} NSW"
    das = _get_recent_das(da_search_address)
    if isinstance(das, list):
        result["recent_development_applications"] = das
        result["da_tracker_url"] = (
            f"{DA_TRACKER_URL}?pageIndex=1&pageSize=5&address={quote_plus(da_search_address)}"
        )
    else:
        result["recent_development_applications"] = []
        result["da_tracker_url"] = das.get("da_tracker_url")
        result["notes"].append(das.get("note", ""))

    result["notes"].append(
        "Planning controls sourced from NSW ePlanning Spatial Viewer (EPI Primary Planning Layers). "
        "Always verify with your local council LEP before making decisions."
    )
    result["notes"].append(
        f"DA Tracker: {result['da_tracker_url']}"
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

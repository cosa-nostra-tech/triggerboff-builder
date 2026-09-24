"""
Geocoding tool using Nominatim OpenStreetMap API.
Converts addresses to lat/lon and reverse geocodes coordinates.
"""
import json
import sys
import requests

NOMINATIM_SEARCH = "https://nominatim.openstreetmap.org/search"
NOMINATIM_REVERSE = "https://nominatim.openstreetmap.org/reverse"
HEADERS = {"User-Agent": "TriggerBOFF/1.0"}


def geocode_address(address: str) -> dict:
    """
    Geocode an Australian address to lat/lon.
    Returns lat, lon, display_name, suburb, postcode.
    """
    params = {
        "q": address,
        "format": "json",
        "countrycodes": "au",
        "limit": 1,
        "addressdetails": 1,
    }
    resp = requests.get(NOMINATIM_SEARCH, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    results = resp.json()

    if not results:
        return {"error": f"No results found for address: {address}"}

    r = results[0]
    addr = r.get("address", {})
    suburb = (
        addr.get("suburb")
        or addr.get("city_district")
        or addr.get("town")
        or addr.get("village")
        or addr.get("municipality")
        or ""
    )
    postcode = addr.get("postcode", "")

    return {
        "lat": float(r["lat"]),
        "lon": float(r["lon"]),
        "display_name": r.get("display_name", ""),
        "suburb": suburb,
        "postcode": postcode,
    }


def reverse_geocode(lat: float, lon: float) -> dict:
    """
    Reverse geocode lat/lon to address.
    Returns lat, lon, display_name, suburb, postcode.
    """
    params = {
        "lat": lat,
        "lon": lon,
        "format": "json",
        "addressdetails": 1,
    }
    resp = requests.get(NOMINATIM_REVERSE, params=params, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    r = resp.json()

    if "error" in r:
        return {"error": r["error"]}

    addr = r.get("address", {})
    suburb = (
        addr.get("suburb")
        or addr.get("city_district")
        or addr.get("town")
        or addr.get("village")
        or addr.get("municipality")
        or ""
    )
    postcode = addr.get("postcode", "")

    return {
        "lat": lat,
        "lon": lon,
        "display_name": r.get("display_name", ""),
        "suburb": suburb,
        "postcode": postcode,
    }


# Hermes registry
try:
    from tools.registry import registry

    registry.register(
        name="geocode_address",
        toolset="property_data",
        schema={
            "name": "geocode_address",
            "description": "Geocode an Australian address to latitude/longitude using OpenStreetMap Nominatim.",
            "parameters": {
                "type": "object",
                "properties": {
                    "address": {
                        "type": "string",
                        "description": "Full address string (e.g. '14 Addison Road Marrickville NSW')",
                    }
                },
                "required": ["address"],
            },
        },
        handler=lambda args, **kw: json.dumps(
            geocode_address(args.get("address", "")), indent=2
        ),
        check_fn=lambda: True,
        requires_env=[],
    )

    registry.register(
        name="reverse_geocode",
        toolset="property_data",
        schema={
            "name": "reverse_geocode",
            "description": "Reverse geocode latitude/longitude to suburb and postcode using OpenStreetMap Nominatim.",
            "parameters": {
                "type": "object",
                "properties": {
                    "lat": {"type": "number", "description": "Latitude"},
                    "lon": {"type": "number", "description": "Longitude"},
                },
                "required": ["lat", "lon"],
            },
        },
        handler=lambda args, **kw: json.dumps(
            reverse_geocode(args.get("lat", 0), args.get("lon", 0)), indent=2
        ),
        check_fn=lambda: True,
        requires_env=[],
    )
except ImportError:
    pass


if __name__ == "__main__":
    address = sys.argv[1] if len(sys.argv) > 1 else "14 Addison Road Marrickville NSW"
    print(f"Geocoding: {address}")
    result = geocode_address(address)
    print(json.dumps(result, indent=2))

    if "lat" in result and "lon" in result:
        print(f"\nReverse geocoding: {result['lat']}, {result['lon']}")
        rev = reverse_geocode(result["lat"], result["lon"])
        print(json.dumps(rev, indent=2))

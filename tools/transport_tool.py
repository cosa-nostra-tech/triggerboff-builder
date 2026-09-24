"""
Transport for NSW commute time tool.
Calculates transit time from an address to Sydney CBD using TfNSW Trip Planner API.
"""
import json
import os
import sys
import requests

TFNSW_TRIP_URL = "https://api.transport.nsw.gov.au/v1/tp/trip"
TFNSW_REGISTER_URL = "https://opendata.transport.nsw.gov.au"


def _geocode_address(address: str) -> dict:
    """Internal geocode helper (avoids circular import)."""
    params = {
        "q": address,
        "format": "json",
        "countrycodes": "au",
        "limit": 1,
        "addressdetails": 1,
    }
    headers = {"User-Agent": "TriggerBOFF/1.0"}
    resp = requests.get(
        "https://nominatim.openstreetmap.org/search",
        params=params,
        headers=headers,
        timeout=15,
    )
    resp.raise_for_status()
    results = resp.json()
    if not results:
        return {}
    r = results[0]
    return {"lat": float(r["lat"]), "lon": float(r["lon"])}


def get_transit_time(
    origin_address: str, destination: str = "Sydney CBD, NSW"
) -> dict:
    """
    Get estimated transit time from an address to Sydney CBD (or custom destination).

    Requires TFNSW_API_KEY environment variable.
    Register at: https://opendata.transport.nsw.gov.au

    Returns:
        dict with duration_minutes, transfers, modes, legs, and journey_summary
    """
    api_key = os.environ.get("TFNSW_API_KEY")
    if not api_key:
        return {
            "error": "TFNSW_API_KEY environment variable not set.",
            "help": (
                "Register for a free API key at: "
                + TFNSW_REGISTER_URL
                + "\nThen set: export TFNSW_API_KEY=your_key_here"
            ),
        }

    # Step 1: Geocode origin
    coords = _geocode_address(origin_address)
    if not coords:
        return {"error": f"Could not geocode address: {origin_address}"}

    lat = coords["lat"]
    lon = coords["lon"]

    # Step 2: Call TfNSW Trip Planner
    params = {
        "outputFormat": "rapidJSON",
        "coordOutputFormat": "EPSG:4326",
        "type_origin": "coord",
        "name_origin": f"{lon}:{lat}:EPSG:4326",
        "type_destination": "any",
        "name_destination": destination,
        "depArrMacro": "dep",
        "calcNumberOfTrips": 1,
    }
    headers = {"Authorization": f"apikey {api_key}"}

    try:
        resp = requests.get(TFNSW_TRIP_URL, params=params, headers=headers, timeout=30)
        resp.raise_for_status()
        data = resp.json()
    except requests.HTTPError as e:
        status = getattr(e.response, "status_code", None)
        return {"error": f"TfNSW API error: {e}", "status_code": status}
    except Exception as e:
        return {"error": f"Request failed: {e}"}

    # Step 3: Parse response
    journeys = data.get("journeys", [])
    if not journeys:
        return {
            "error": "No journeys found in TfNSW response.",
            "raw_keys": list(data.keys()),
        }

    journey = journeys[0]
    legs = journey.get("legs", [])

    # Calculate total duration
    duration_minutes = None
    if "duration" in journey:
        duration_minutes = round(journey["duration"] / 60, 1)
    elif legs:
        # Sum leg durations
        total_secs = sum(leg.get("duration", 0) for leg in legs)
        duration_minutes = round(total_secs / 60, 1)

    # Count transfers (legs minus 1, excluding walk legs)
    transit_legs = [l for l in legs if l.get("transportation", {}).get("product", {}).get("class", 0) != 100]
    transfers = max(0, len(transit_legs) - 1)

    # List transport modes
    modes = []
    leg_summaries = []
    for leg in legs:
        transport = leg.get("transportation", {})
        product = transport.get("product", {})
        mode_name = product.get("name") or product.get("class") or "unknown"
        origin_name = leg.get("origin", {}).get("name", "")
        dest_name = leg.get("destination", {}).get("name", "")
        leg_duration = round(leg.get("duration", 0) / 60, 1)

        if mode_name and mode_name not in modes:
            modes.append(str(mode_name))

        leg_summaries.append({
            "mode": str(mode_name),
            "from": origin_name,
            "to": dest_name,
            "duration_minutes": leg_duration,
        })

    return {
        "origin_address": origin_address,
        "destination": destination,
        "origin_coords": {"lat": lat, "lon": lon},
        "duration_minutes": duration_minutes,
        "transfers": transfers,
        "modes": modes,
        "legs": leg_summaries,
        "journey_summary": (
            f"{duration_minutes} min via {', '.join(modes) if modes else 'unknown'} "
            f"with {transfers} transfer(s)"
            if duration_minutes
            else "Duration unknown"
        ),
    }


# Hermes registry
try:
    from tools.registry import registry

    registry.register(
        name="get_transit_time",
        toolset="property_data",
        schema={
            "name": "get_transit_time",
            "description": (
                "Get estimated public transit commute time from a Sydney address to the CBD "
                "using Transport for NSW Trip Planner API."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "origin_address": {
                        "type": "string",
                        "description": "Origin address (e.g. '14 Addison Road Marrickville NSW')",
                    },
                    "destination": {
                        "type": "string",
                        "description": "Destination (default: 'Sydney CBD, NSW')",
                    },
                },
                "required": ["origin_address"],
            },
        },
        handler=lambda args, **kw: json.dumps(
            get_transit_time(
                args.get("origin_address", ""),
                args.get("destination", "Sydney CBD, NSW"),
            ),
            indent=2,
        ),
        check_fn=lambda: bool(os.environ.get("TFNSW_API_KEY")),
        requires_env=["TFNSW_API_KEY"],
    )
except ImportError:
    pass


if __name__ == "__main__":
    address = sys.argv[1] if len(sys.argv) > 1 else "14 Addison Road Marrickville NSW"
    destination = sys.argv[2] if len(sys.argv) > 2 else "Sydney CBD, NSW"
    print(f"Getting transit time from: {address}")
    print(f"To: {destination}")
    result = get_transit_time(address, destination)
    print(json.dumps(result, indent=2))

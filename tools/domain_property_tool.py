"""
Domain API tool for Sydney Property Buyer Harness.
Searches property listings and suburb statistics via the Domain API.
Requires DOMAIN_API_KEY environment variable.
"""

import json
import os
import sys
import urllib.parse
import urllib.request
from typing import Optional

# domain_fallback sits beside this file in $HERMES_HOME/tools/. The plugin puts
# that directory on sys.path, but resolve it explicitly so the module works when
# imported directly during testing too.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from domain_fallback import get_api_key as _fallback_get_api_key  # noqa: E402
from domain_fallback import is_failure  # noqa: E402
from domain_fallback import request as _domain_request_impl  # noqa: E402

DOMAIN_API_BASE = "https://api.domain.com.au/v1"
DOMAIN_API_V2 = "https://api.domain.com.au/v2"


def _get_api_key() -> str:
    return _fallback_get_api_key()


def _domain_request(endpoint: str, method: str = "GET", body: Optional[dict] = None,
                    base: str = DOMAIN_API_BASE, tool: str = "domain_property"):
    """Call the Domain API through domain_fallback.

    Returns parsed data on success, or a structured ``unavailable`` payload on
    failure — check with ``is_failure()`` before treating the value as data.
    """
    return _domain_request_impl(
        tool=tool, endpoint=endpoint, method=method, body=body, base=base, timeout=15
    )


def search_properties(
    suburbs: list,
    min_price: Optional[int] = None,
    max_price: Optional[int] = None,
    bedrooms_min: Optional[int] = None,
    bedrooms_max: Optional[int] = None,
    property_types: Optional[list] = None,
    listing_type: str = "Sale",
    page_size: int = 10,
    task_id: Optional[str] = None,
) -> str:
    """
    Search for property listings on Domain.

    Args:
        suburbs: List of suburb names e.g. ["Marrickville", "Newtown"]
        min_price: Minimum price in AUD
        max_price: Maximum price in AUD
        bedrooms_min: Minimum number of bedrooms
        bedrooms_max: Maximum number of bedrooms
        property_types: List of types e.g. ["House", "Apartment", "Unit", "Townhouse"]
        listing_type: "Sale" or "Rent"
        page_size: Number of results (max 20)
    """
    body = {
        "listingType": listing_type,
        "locations": [
            {"state": "NSW", "suburb": s, "postCode": "", "region": "", "area": ""}
            for s in suburbs
        ],
        "pageSize": min(page_size, 20),
    }

    if min_price or max_price:
        body["price"] = {}
        if min_price:
            body["price"]["minimum"] = min_price
        if max_price:
            body["price"]["maximum"] = max_price

    if bedrooms_min or bedrooms_max:
        body["bedrooms"] = {}
        if bedrooms_min:
            body["bedrooms"]["minimum"] = bedrooms_min
        if bedrooms_max:
            body["bedrooms"]["maximum"] = bedrooms_max

    if property_types:
        body["propertyTypes"] = property_types

    result = _domain_request("/listings/residential/_search", method="POST", body=body)

    if is_failure(result):
        return json.dumps(result)

    # Format results for readability
    listings = []
    for item in result:
        listing = item.get("listing", {})
        price_details = listing.get("priceDetails", {})
        property_details = listing.get("propertyDetails", {})
        advertiser = listing.get("advertiser", {})

        formatted = {
            "id": listing.get("id"),
            "address": property_details.get("displayableAddress", ""),
            "suburb": property_details.get("suburb", ""),
            "price_display": price_details.get("displayPrice", ""),
            "bedrooms": property_details.get("bedrooms"),
            "bathrooms": property_details.get("bathrooms"),
            "car_spaces": property_details.get("carspaces"),
            "land_area": property_details.get("landArea"),
            "property_type": property_details.get("propertyType", ""),
            "days_listed": listing.get("daysListed"),
            "inspection_date": listing.get("inspectionDetails", {}).get(
                "inspectionTime", ""
            ) if listing.get("inspectionDetails") else "",
            "agent": advertiser.get("name", ""),
            "agency": advertiser.get("preferredColorHex", ""),  # placeholder
            "url": f"https://www.domain.com.au/property/{listing.get('id', '')}",
            "headline": listing.get("headline", ""),
        }
        listings.append(formatted)

    return json.dumps(
        {
            "total_results": len(listings),
            "listings": listings,
            "note": "Days listed is key — 60+ days = motivated seller territory",
        },
        indent=2,
    )


def get_suburb_stats(suburb: str, state: str = "NSW", postcode: str = "",
                     task_id: Optional[str] = None) -> str:
    """
    Get suburb performance statistics — median sold price, number sold, highest
    and lowest sold price, auction counts and median rent listing price.

    Args:
        suburb: Suburb name e.g. "Marrickville"
        state: State code, default "NSW"
        postcode: Optional postcode; improves matching on the v2 route
    """
    # URL encode the suburb name
    suburb_encoded = urllib.parse.quote(suburb)
    postcode_encoded = urllib.parse.quote(postcode or "")
    # Domain deprecated /v1/suburbPerformanceStatistics in favour of
    # GET /v2/suburbPerformanceStatistics/{state}/{suburb}/{postcode}.
    # The path this used to call — /suburbPerformance/statistics — was never a
    # real Domain route (verified: 404 "No Matching Route", while the v2 route
    # returns 403 "not permitted on project"), so this tool could never have
    # returned data even with entitlements enabled.
    result = _domain_request(
        f"/suburbPerformanceStatistics/{state}/{suburb_encoded}/{postcode_encoded}"
        f"?propertyCategory=house&chronologicalSpan=12&tPlusFrom=1&tPlusTo=3",
        base=DOMAIN_API_V2,
        tool="get_suburb_stats",
    )

    if is_failure(result):
        return json.dumps(result)

    return json.dumps(result, indent=2)


def get_property_details(domain_listing_id: str, task_id: Optional[str] = None) -> str:
    """
    Get full details for a specific Domain listing by its ID.

    Args:
        domain_listing_id: The Domain listing ID (number)
    """
    result = _domain_request(f"/listings/{domain_listing_id}")

    if is_failure(result):
        return json.dumps(result)

    return json.dumps(result, indent=2)


# ── Tool Registry ──────────────────────────────────────────────────────────────
# This file is auto-discovered by Hermes' tool loader when placed in a
# tools/ directory that's on the search path. Each registry.register() call
# below exposes one tool to the LLM.

try:
    from tools.registry import registry

    registry.register(
        name="search_properties",
        toolset="property",
        schema={
            "name": "search_properties",
            "description": (
                "Search Domain.com.au for property listings in Sydney. "
                "Returns listings with price, bedrooms, days listed (critical for negotiation), "
                "and direct URLs. Use this when the user wants to find properties matching "
                "specific criteria. Days listed over 60 indicates a motivated seller."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "suburbs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "List of NSW suburb names to search e.g. ['Marrickville', 'Newtown']",
                    },
                    "min_price": {
                        "type": "integer",
                        "description": "Minimum price in AUD e.g. 800000",
                    },
                    "max_price": {
                        "type": "integer",
                        "description": "Maximum price in AUD e.g. 1200000",
                    },
                    "bedrooms_min": {
                        "type": "integer",
                        "description": "Minimum number of bedrooms",
                    },
                    "bedrooms_max": {
                        "type": "integer",
                        "description": "Maximum number of bedrooms",
                    },
                    "property_types": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Property types: House, Apartment, Unit, Townhouse, Studio, DuplexSemiDetached",
                    },
                    "page_size": {
                        "type": "integer",
                        "description": "Number of results to return (max 20, default 10)",
                    },
                },
                "required": ["suburbs"],
            },
        },
        handler=lambda args, **kw: search_properties(
            suburbs=args.get("suburbs", []),
            min_price=args.get("min_price"),
            max_price=args.get("max_price"),
            bedrooms_min=args.get("bedrooms_min"),
            bedrooms_max=args.get("bedrooms_max"),
            property_types=args.get("property_types"),
            page_size=args.get("page_size", 10),
            task_id=kw.get("task_id"),
        ),
        check_fn=lambda: bool(_get_api_key()),
        requires_env=["DOMAIN_API_KEY"],
    )

    registry.register(
        name="get_suburb_stats",
        toolset="property",
        schema={
            "name": "get_suburb_stats",
            "description": (
                "Get suburb performance statistics from Domain including median prices, "
                "auction clearance rates, and days on market. Use this to give buyers "
                "context on a suburb's current market conditions."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "suburb": {
                        "type": "string",
                        "description": "NSW suburb name e.g. 'Marrickville'",
                    },
                    "postcode": {
                        "type": "string",
                        "description": "Optional postcode e.g. '2204' — improves matching",
                    },
                },
                "required": ["suburb"],
            },
        },
        handler=lambda args, **kw: get_suburb_stats(
            suburb=args.get("suburb", ""),
            postcode=args.get("postcode", ""),
            task_id=kw.get("task_id"),
        ),
        check_fn=lambda: bool(_get_api_key()),
        requires_env=["DOMAIN_API_KEY"],
    )

    registry.register(
        name="get_property_details",
        toolset="property",
        schema={
            "name": "get_property_details",
            "description": (
                "Get full details for a specific Domain listing including price history, "
                "inspection times, and complete property description."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "domain_listing_id": {
                        "type": "string",
                        "description": "The Domain listing ID (number from the listing URL)",
                    },
                },
                "required": ["domain_listing_id"],
            },
        },
        handler=lambda args, **kw: get_property_details(
            domain_listing_id=args.get("domain_listing_id", ""),
            task_id=kw.get("task_id"),
        ),
        check_fn=lambda: bool(_get_api_key()),
        requires_env=["DOMAIN_API_KEY"],
    )

except ImportError:
    # Running outside Hermes context (e.g. direct testing)
    pass

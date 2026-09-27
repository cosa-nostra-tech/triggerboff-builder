#!/usr/bin/env python3
"""
nsw_da_tracker_tool.py — NSW development application history from the open
NSW Planning Portal ArcGIS service.

WHY THIS REPLACED A SCRAPER
---------------------------
nsw_planning_tool.py used to scrape `datracker.planning.nsw.gov.au`. That host
does not resolve at all (verified: no DNS, HTTP 000), so the DA half of that
tool could never return anything. The NSW Planning Portal also exposes no live
DA search API (every candidate path 404s).

What DOES work is the open ArcGIS MapServer behind the Planning Portal:

    Planning/Planning_Portal_Application_Tracking/MapServer/0

Verified reachable, keyless and queryable. Fields include DEVELOPMENT_SITE_OWNER,
DWELLINGS_TO_BE_DEMOLISHED, PROPOSED_SUBDIVISION, UNITS_OR_DWELLINGS_PROPOSED
and COST_OF_DEVELOPMENT — genuinely useful due-diligence signal.

IMPORTANT — THIS IS AN ARCHIVE, NOT A LIVE FEED
-----------------------------------------------
The published service is frozen: the newest LODGEMENT_DATE available is April
2023. Every response therefore carries `data_through` and a `data_freshness`
note, and the agent is instructed to present this as historical DA context —
never as "what's about to be listed". Treating it as live would be exactly the
kind of invented-signal claim this product must not make.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from typing import Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

logger = logging.getLogger(__name__)

DA_LAYER = (
    "https://mapprod3.environment.nsw.gov.au/arcgis/rest/services/Planning/"
    "Planning_Portal_Application_Tracking/MapServer/0"
)

OUT_FIELDS = ",".join([
    "PLANNING_PORTAL_APP_NUMBER", "PRIMARY_ADDRESS", "SUBURBNAME", "POSTCODE",
    "STATUS", "APPLICATION_TYPE", "LODGEMENT_DATE", "DETERMINED_DATE",
    "COST_OF_DEVELOPMENT", "COST_OF_DEVELOPMENT_RANGE", "TYPE_OF_DEVELOPMENT",
    "DWELLINGS_TO_BE_DEMOLISHED", "DWELLINGS_TO_BE_CONSTRUCTED",
    "UNITS_OR_DWELLINGS_PROPOSED", "STOREYS_PROPOSED", "PROPOSED_SUBDIVISION",
    "DEVELOPMENT_SITE_OWNER", "LGA_NAME", "DEVELOPMENT_DETAILED_DESC",
])

FRESHNESS_NOTE = (
    "This is the NSW Planning Portal's published DA archive, not a live feed. "
    "The service's newest lodgement is April 2023, so treat these as HISTORICAL "
    "development context for the address/suburb. Do NOT describe them as current "
    "or upcoming, and never present them as evidence of a property coming to "
    "market. For current DA activity, direct the user to their council's DA "
    "register or the NSW Planning Portal."
)


def _arcgis_query(where: str, limit: int = 10, order_desc: bool = True) -> list[dict]:
    params = {
        "where": where,
        "outFields": OUT_FIELDS,
        "returnGeometry": "false",
        "resultRecordCount": str(max(1, min(limit, 50))),
        "f": "json",
    }
    if order_desc:
        params["orderByFields"] = "LODGEMENT_DATE DESC"
    url = f"{DA_LAYER}/query?" + urllib.parse.urlencode(params)
    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=25) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logger.warning("DA Tracker ArcGIS HTTP %s", e.code)
        return []
    except Exception as e:  # noqa: BLE001
        logger.warning("DA Tracker ArcGIS failed: %s", e)
        return []

    if "error" in data:
        logger.warning("DA Tracker ArcGIS error payload: %s", data["error"])
        return []
    return [f.get("attributes", {}) for f in data.get("features", [])]


def _fmt_date(raw: Optional[str]) -> str:
    """ArcGIS returns dates as 'YYYYMMDDHHMMSS.ss' strings."""
    if not raw:
        return ""
    s = str(raw).split(".")[0]
    if len(s) >= 8 and s[:8].isdigit():
        return f"{s[0:4]}-{s[4:6]}-{s[6:8]}"
    return str(raw)


def _latest_lodgement() -> str:
    rows = _arcgis_query("1=1", limit=1, order_desc=True)
    if rows:
        return _fmt_date(rows[0].get("LODGEMENT_DATE"))
    return ""


def _clean(attrs: dict) -> dict:
    return {
        "da_number": attrs.get("PLANNING_PORTAL_APP_NUMBER") or "",
        "address": (attrs.get("PRIMARY_ADDRESS") or "").strip(),
        "suburb": attrs.get("SUBURBNAME") or "",
        "postcode": attrs.get("POSTCODE") or "",
        "lga": attrs.get("LGA_NAME") or "",
        "status": attrs.get("STATUS") or "",
        "application_type": attrs.get("APPLICATION_TYPE") or "",
        "lodged": _fmt_date(attrs.get("LODGEMENT_DATE")),
        "determined": _fmt_date(attrs.get("DETERMINED_DATE")),
        "cost_of_development": attrs.get("COST_OF_DEVELOPMENT") or "",
        "type_of_development": attrs.get("TYPE_OF_DEVELOPMENT") or "",
        "dwellings_to_demolish": attrs.get("DWELLINGS_TO_BE_DEMOLISHED"),
        "dwellings_to_construct": attrs.get("DWELLINGS_TO_BE_CONSTRUCTED"),
        "units_proposed": attrs.get("UNITS_OR_DWELLINGS_PROPOSED"),
        "storeys_proposed": attrs.get("STOREYS_PROPOSED"),
        "subdivision": attrs.get("PROPOSED_SUBDIVISION") or "",
        "site_owner": attrs.get("DEVELOPMENT_SITE_OWNER") or "",
        "description": attrs.get("DEVELOPMENT_DETAILED_DESC") or "",
    }


def _build_where(suburb=None, postcode=None, lga=None, address=None) -> Optional[str]:
    clauses = []
    if suburb:
        clauses.append(f"UPPER(SUBURBNAME)='{suburb.strip().upper().replace(chr(39), chr(39)*2)}'")
    if postcode:
        clauses.append(f"POSTCODE='{postcode.strip()}'")
    if lga:
        clauses.append(f"UPPER(LGA_NAME) LIKE '%{lga.strip().upper()}%'")
    if address:
        token = address.strip().upper().split(",")[0]
        token = token.replace("'", "''")
        clauses.append(f"UPPER(PRIMARY_ADDRESS) LIKE '%{token}%'")
    return " AND ".join(clauses) if clauses else None


def run(suburb: Optional[str] = None, postcode: Optional[str] = None,
        lga: Optional[str] = None, address: Optional[str] = None,
        demolitions_only: bool = False, limit: int = 10) -> dict:
    """Look up historical NSW development applications.

    Returns DAs plus an explicit statement of how current the underlying data is.
    """
    if not any([suburb, postcode, lga, address]):
        return {
            "error": "Provide at least one of: suburb, postcode, lga, address.",
            "example": "suburb='MARRICKVILLE'",
        }

    where = _build_where(suburb=suburb, postcode=postcode, lga=lga, address=address)
    if not where:
        return {"error": "Provide at least one of: suburb, postcode, lga, address."}
    if demolitions_only:
        where = f"({where}) AND DWELLINGS_TO_BE_DEMOLISHED > 0"

    rows = _arcgis_query(where, limit=limit)
    das = [_clean(r) for r in rows]

    result = {
        "query": {k: v for k, v in {
            "suburb": suburb, "postcode": postcode, "lga": lga,
            "address": address, "demolitions_only": demolitions_only or None,
        }.items() if v},
        "count": len(das),
        "development_applications": das,
        "data_source": "NSW Planning Portal DA archive (open ArcGIS MapServer)",
        "data_through": _latest_lodgement(),
        "data_freshness": FRESHNESS_NOTE,
    }

    if not das:
        result["note"] = (
            "No development applications matched. This may mean none were lodged "
            "in the covered period, or the archive simply ends in April 2023."
        )

    # Surface the strongest due-diligence signals first-class, not buried.
    demo = [d for d in das if d.get("dwellings_to_demolish")]
    subdiv = [d for d in das if d.get("subdivision")]
    if demo:
        result["demolition_signals"] = [
            {"address": d["address"], "lodged": d["lodged"],
             "demolishing": d["dwellings_to_demolish"],
             "replacing_with": d["dwellings_to_construct"]}
            for d in demo
        ]
    if subdiv:
        result["subdivision_signals"] = [
            {"address": d["address"], "lodged": d["lodged"], "subdivision": d["subdivision"]}
            for d in subdiv
        ]
    return result


# ── Tool Registry ──────────────────────────────────────────────────────────────

try:
    from tools.registry import registry

    registry.register(
        name="nsw_da_history",
        toolset="property_data",
        schema={
            "name": "nsw_da_history",
            "description": (
                "Historical NSW development application (DA) record for a suburb, "
                "postcode, LGA or street address, from the NSW Planning Portal's open "
                "archive. Use for due diligence: nearby redevelopment that could shadow "
                "or devalue a property, proposed subdivisions, and demolish-and-rebuild "
                "activity. Note the archive's newest lodgement is April 2023, so treat "
                "results as historical context, NOT as current or upcoming activity."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "suburb": {"type": "string", "description": "Suburb name e.g. 'MARRICKVILLE'"},
                    "postcode": {"type": "string", "description": "Postcode e.g. '2204'"},
                    "lga": {"type": "string", "description": "Council name e.g. 'Inner West'"},
                    "address": {"type": "string", "description": "Street address or street name to match"},
                    "demolitions_only": {"type": "boolean", "description": "Only DAs proposing demolition"},
                    "limit": {"type": "integer", "description": "Max results (default 10, max 50)"},
                },
            },
        },
        handler=lambda args, **kw: json.dumps(run(
            suburb=args.get("suburb"), postcode=args.get("postcode"),
            lga=args.get("lga"), address=args.get("address"),
            demolitions_only=bool(args.get("demolitions_only")),
            limit=int(args.get("limit", 10)),
        ), indent=2),
        check_fn=lambda: True,
        requires_env=[],
    )
except ImportError:
    pass


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    kwargs = {}
    for a in sys.argv[1:]:
        if "=" in a:
            k, v = a.split("=", 1)
            if v.lower() in ("true", "false"):
                kwargs[k] = v.lower() == "true"
            elif v.isdigit():
                kwargs[k] = int(v)
            else:
                kwargs[k] = v
    print(json.dumps(run(**kwargs), indent=2))

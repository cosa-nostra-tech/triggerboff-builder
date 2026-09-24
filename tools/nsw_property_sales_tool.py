#!/usr/bin/env python3
"""
nsw_property_sales_tool.py — Real settled property sale prices from NSW Valuer General PSI.

Fetches ACTUAL sold prices (not Domain listings). Data is from the NSW Valuer General
Property Sales Information (PSI) weekly dataset, updated every Monday.

Usage: python3 nsw_property_sales_tool.py SURRY_HILLS [postcode]
"""

import csv, io, json, logging, os, sys, zipfile
from datetime import datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from statistics import median

logger = logging.getLogger(__name__)

EMBED_URL = "https://valuation.property.nsw.gov.au/embed/propertySalesInformation"
WEEKLY_BASE = "https://www.valuergeneral.nsw.gov.au/__psi/weekly"
CACHE_DIR = Path(os.getenv("NSW_PSI_CACHE", "/tmp/nsw_psi_cache"))

B_FIELDS = [
    "record_type","district_code","property_id","sale_counter","download_date",
    "property_name","unit_number","house_number","street_name","locality","postcode",
    "area","area_type","contract_date","settlement_date","purchase_price","zone_code",
    "nature_of_property","primary_purpose","strata_lot_number","component_code",
    "sale_code","interest_of_sale","dealing_number",
]


class _LinkCollector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.hrefs = []
    def handle_starttag(self, tag, attrs):
        if tag == "a":
            for name, value in attrs:
                if name == "href" and value:
                    self.hrefs.append(value)


def _fetch_weekly_zip_urls():
    try:
        import requests
        r = requests.get(EMBED_URL, timeout=30, headers={"User-Agent": "TriggerBOFF/1.0"})
        r.raise_for_status()
        parser = _LinkCollector()
        parser.feed(r.text)
        return sorted(h for h in parser.hrefs if "weekly" in h and h.endswith(".zip"))
    except Exception as e:
        logger.warning("Could not scrape embed page: %s", e)
        return []


def _guess_weekly_urls(n=4):
    today = datetime.today()
    monday = today - timedelta(days=today.weekday())
    return [f"{WEEKLY_BASE}/{(monday - timedelta(weeks=i)).strftime('%Y%m%d')}.zip" for i in range(n)]


def _download_zip(url):
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    filename = url.rsplit("/", 1)[-1]
    cache_path = CACHE_DIR / filename
    if cache_path.exists():
        return cache_path.read_bytes()
    try:
        import requests
        r = requests.get(url, timeout=120, headers={"User-Agent": "TriggerBOFF/1.0"})
        if r.status_code == 200:
            cache_path.write_bytes(r.content)
            return r.content
    except Exception as e:
        logger.warning("Download failed %s: %s", url, e)
    return None


def _parse_b_record(fields):
    if len(fields) < 16:
        return None
    rec = {B_FIELDS[i]: fields[i].strip() for i in range(min(len(B_FIELDS), len(fields)))}
    try:
        rec["purchase_price_int"] = int(rec.get("purchase_price", 0) or 0)
    except ValueError:
        rec["purchase_price_int"] = 0
    area, area_type = rec.get("area", ""), rec.get("area_type", "")
    try:
        val = float(area)
        rec["area_sqm"] = val * 10000 if area_type == "H" else val
    except (ValueError, TypeError):
        rec["area_sqm"] = None
    for df in ["contract_date", "settlement_date"]:
        d = rec.get(df, "").strip()
        rec[f"{df}_iso"] = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 and d.isdigit() else None
    return rec


def _extract_sales(zip_bytes, suburb, postcode, prop_type):
    suburb_u = suburb.upper().strip()
    pt_u = prop_type.upper().strip() if prop_type else None
    matches = []
    try:
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            for dat_name in [n for n in zf.namelist() if n.upper().endswith(".DAT")]:
                raw = zf.read(dat_name).decode("utf-8", errors="replace")
                for row in csv.reader(raw.splitlines(), delimiter=";", quoting=csv.QUOTE_NONE):
                    if not row or row[0] != "B":
                        continue
                    rec = _parse_b_record(row)
                    if not rec:
                        continue
                    if rec.get("locality", "").upper() != suburb_u:
                        continue
                    if postcode and rec.get("postcode", "").strip() != postcode.strip():
                        continue
                    if pt_u and pt_u not in rec.get("primary_purpose", "").upper():
                        continue
                    if rec.get("purchase_price_int", 0) <= 0:
                        continue
                    matches.append(rec)
    except zipfile.BadZipFile:
        pass
    return matches


def run(suburb, postcode=None, property_type=None, weeks_back=4):
    weeks_back = min(max(1, int(weeks_back)), 12)
    all_weekly = _fetch_weekly_zip_urls() or _guess_weekly_urls(n=weeks_back)
    all_weekly = all_weekly[-weeks_back:]

    all_sales, source_zips, errors = [], [], []
    for url in reversed(all_weekly):
        zip_bytes = _download_zip(url)
        if zip_bytes is None:
            errors.append(f"Could not download {url}")
            continue
        sales = _extract_sales(zip_bytes, suburb, postcode, property_type)
        if sales:
            all_sales.extend(sales)
            source_zips.append(url.rsplit("/", 1)[-1])

    if not all_sales:
        return {
            "sales": [], "stats": {"suburb": suburb, "count": 0, "median_price": None},
            "note": f"No sales found for {suburb.upper()} in last {weeks_back} weeks. Try suburb in ALL CAPS or increase weeks_back.",
            "errors": errors,
        }

    all_sales.sort(key=lambda r: r.get("settlement_date", ""), reverse=True)

    def fmt_addr(r):
        parts = []
        if r.get("unit_number"): parts.append(f"Unit {r['unit_number']}")
        if r.get("house_number"): parts.append(r["house_number"])
        if r.get("street_name"): parts.append(r["street_name"].title())
        parts.append(r.get("locality", "").title())
        if r.get("postcode"): parts.append(r["postcode"])
        return " ".join(parts)

    output = [{"address": fmt_addr(r), "sale_date": r.get("settlement_date_iso"),
               "contract_date": r.get("contract_date_iso"), "price": r.get("purchase_price_int"),
               "price_formatted": f"${r.get('purchase_price_int', 0):,}",
               "area_sqm": round(r["area_sqm"], 1) if r.get("area_sqm") else None,
               "property_type": r.get("primary_purpose") or r.get("nature_of_property"),
               "zone": r.get("zone_code")} for r in all_sales[:10]]

    prices = [r["purchase_price_int"] for r in all_sales if r.get("purchase_price_int", 0) > 0]
    return {
        "sales": output,
        "stats": {"suburb": suburb.upper(), "postcode": postcode, "count": len(prices),
                  "median_price": int(median(prices)) if prices else None,
                  "median_price_formatted": f"${int(median(prices)):,}" if prices else None,
                  "min_price": min(prices) if prices else None, "max_price": max(prices) if prices else None},
        "source_zips": source_zips, "weeks_searched": weeks_back,
        "note": "NSW Valuer General PSI — registered settlement prices, not listing prices.",
    }


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    suburb = sys.argv[1] if len(sys.argv) > 1 else "SURRY HILLS"
    postcode = sys.argv[2] if len(sys.argv) > 2 else None
    print(json.dumps(run(suburb=suburb, postcode=postcode), indent=2))


try:
    from tools.registry import registry
    registry.register(
        name="nsw_property_sales",
        toolset="property_data",
        schema={
            "name": "nsw_property_sales",
            "description": "Fetch real settled property sale prices from NSW Valuer General PSI. Actual registered sold prices, NOT listing prices. Use for comparable sales analysis before any offer or auction.",
            "parameters": {"type": "object",
                "properties": {
                    "suburb": {"type": "string", "description": "Suburb in CAPS e.g. MARRICKVILLE"},
                    "postcode": {"type": "string", "description": "Optional postcode to filter"},
                    "property_type": {"type": "string", "description": "Optional: RESIDENCE, UNIT, HOUSE, VACANT LAND"},
                    "weeks_back": {"type": "integer", "description": "Recent weekly files to search (default 4, max 12)"},
                }, "required": ["suburb"]}
        },
        handler=lambda args, **kw: json.dumps(run(
            suburb=args.get("suburb", ""), postcode=args.get("postcode"),
            property_type=args.get("property_type"), weeks_back=int(args.get("weeks_back", 4))), indent=2),
        check_fn=lambda: True, requires_env=[],
    )
except ImportError:
    pass

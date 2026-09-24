"""
ABS 2021 Census suburb demographics tool.
Fetches median income, age, tenure, and dwelling structure data via SDMX-JSON API.
"""
import json
import sys
import requests
import xml.etree.ElementTree as ET

CODELIST_URL = "https://data.api.abs.gov.au/rest/codelist/ABS/CL_SAL_2021"
G02_URL = "https://data.api.abs.gov.au/rest/data/C21_G02_SAL/.{sal_code}..?format=jsondata"
G37_URL = "https://data.api.abs.gov.au/rest/data/C21_G37_SAL/..{sal_code}..?format=jsondata"
G36_URL = "https://data.api.abs.gov.au/rest/data/C21_G36_SAL/..{sal_code}..?format=jsondata"


def find_sal_code(suburb_name: str) -> str | None:
    """Fetch ABS codelist XML and find SAL code for suburb name."""
    resp = requests.get(CODELIST_URL, timeout=30)
    resp.raise_for_status()
    root = ET.fromstring(resp.content)

    suburb_upper = suburb_name.strip().upper()
    for elem in root.iter():
        tag = elem.tag.split('}')[-1]
        if tag == 'Code':
            code_id = elem.get('id', '')
            # Name child element contains suburb name (common:Name)
            for child in elem:
                child_tag = child.tag.split('}')[-1]
                if child_tag == 'Name' and child.text:
                    if child.text.strip().upper() == suburb_upper:
                        return code_id
    return None


def fetch_sdmx_json(url: str) -> dict:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


def build_series_index(struct: dict) -> dict:
    """
    Build {dim_id: {value_id: index}} from SDMX series dimensions.
    Returns dict keyed by dim_id, each containing value_id -> positional index.
    """
    dims = struct['dimensions']['series']
    index = {}
    for dim in dims:
        dim_id = dim['id']
        key_pos = dim['keyPosition']
        index[dim_id] = {
            'key_position': key_pos,
            'values': {v['id']: i for i, v in enumerate(dim.get('values', []))}
        }
    return index


def get_series_value(series: dict, dim_index: dict, filters: dict) -> float | None:
    """
    Get value from series dict matching filter criteria.
    filters: {dim_id: value_id}
    Returns the numeric value or None.
    """
    # Map filters to expected index positions
    expected = {}
    for dim_id, value_id in filters.items():
        if dim_id not in dim_index:
            return None
        val_map = dim_index[dim_id]['values']
        if value_id not in val_map:
            return None
        key_pos = dim_index[dim_id]['key_position']
        expected[key_pos] = val_map[value_id]

    for key, sdata in series.items():
        parts = key.split(':')
        match = True
        for pos, idx in expected.items():
            if int(pos) >= len(parts) or int(parts[int(pos)]) != idx:
                match = False
                break
        if match:
            obs = sdata.get('observations', {})
            # observations keyed by time index, get first value
            if obs:
                val = list(obs.values())[0]
                return val[0] if val else None
    return None


def get_suburb_demographics(suburb: str) -> dict:
    """
    Fetch ABS 2021 Census demographics for a Sydney suburb.
    Returns median age, income, mortgage, rent, household size,
    owner-occupied %, renting %, houses %, apartments %.
    """
    # 1. Find SAL code
    sal_code = find_sal_code(suburb)
    if not sal_code:
        return {'error': f'Could not find SAL code for suburb: {suburb}'}

    result = {'suburb': suburb, 'sal_code': sal_code}

    # 2. G02 - Medians and Averages
    # Series dims: MEDAVG (0), REGION (1), REGION_TYPE (2), STATE (3)
    # MEDAVG IDs: 1=Median age, 4=Median household income (weekly), 5=Median mortgage (monthly),
    #             6=Median rent (weekly), 8=Average household size
    try:
        g02 = fetch_sdmx_json(G02_URL.format(sal_code=sal_code))
        struct = g02['data']['structures'][0]
        dim_index = build_series_index(struct)
        series = g02['data']['dataSets'][0]['series']

        medavg_dim = dim_index.get('MEDAVG', {}).get('values', {})
        # Find the right MEDAVG IDs by checking dim values list
        medavg_all = struct['dimensions']['series'][0]['values']
        medavg_map = {v['id']: v.get('name', '') for v in medavg_all}

        def get_medavg(medavg_id):
            return get_series_value(series, dim_index, {
                'MEDAVG': medavg_id,
                'REGION': sal_code,
                'REGION_TYPE': 'SAL'
            })

        result['median_age'] = get_medavg('1')
        result['median_weekly_household_income'] = get_medavg('4')
        result['median_monthly_mortgage'] = get_medavg('5')
        result['median_weekly_rent'] = get_medavg('6')
        result['average_household_size'] = get_medavg('8')
    except Exception as e:
        result['g02_error'] = str(e)

    # 3. G37 - Tenure type by dwelling structure
    # Series dims: TENLLD (0), STRD (1), REGION (2), REGION_TYPE (3), STATE (4)
    # TENLLD: 1=Owned outright, 2=Owned with mortgage, R_T=Rented total, _T=Total
    # STRD: _T=Total
    try:
        g37 = fetch_sdmx_json(G37_URL.format(sal_code=sal_code))
        struct37 = g37['data']['structures'][0]
        dim_index37 = build_series_index(struct37)
        series37 = g37['data']['dataSets'][0]['series']

        def get_tenure(tenlld_id):
            return get_series_value(series37, dim_index37, {
                'TENLLD': tenlld_id,
                'STRD': '_T',
                'REGION': sal_code,
                'REGION_TYPE': 'SAL'
            })

        owned_outright = get_tenure('1') or 0
        owned_mortgage = get_tenure('2') or 0
        rented = get_tenure('R_T') or 0
        total = get_tenure('_T') or 0

        if total and total > 0:
            result['owner_occupied_pct'] = round((owned_outright + owned_mortgage) / total * 100, 1)
            result['renting_pct'] = round(rented / total * 100, 1)
        else:
            result['owner_occupied_pct'] = None
            result['renting_pct'] = None
    except Exception as e:
        result['g37_error'] = str(e)

    # 4. G36 - Dwelling structure
    # Series dims: DWTSTRD (0), SUM (1), REGION (2), REGION_TYPE (3), STATE (4)
    # DWTSTRD: 11=Separate house, 3=Flat or apartment total, OCC=Total occupied private dwellings
    # SUM: D=Dwellings
    try:
        g36 = fetch_sdmx_json(G36_URL.format(sal_code=sal_code))
        struct36 = g36['data']['structures'][0]
        dim_index36 = build_series_index(struct36)
        series36 = g36['data']['dataSets'][0]['series']

        def get_dwelling(dwtstrd_id):
            return get_series_value(series36, dim_index36, {
                'DWTSTRD': dwtstrd_id,
                'SUM': 'D',
                'REGION': sal_code,
                'REGION_TYPE': 'SAL'
            })

        houses = get_dwelling('11') or 0
        apartments = get_dwelling('3') or 0
        total_dwell = get_dwelling('OCC') or 0

        if total_dwell and total_dwell > 0:
            result['houses_pct'] = round(houses / total_dwell * 100, 1)
            result['apartments_pct'] = round(apartments / total_dwell * 100, 1)
        else:
            result['houses_pct'] = None
            result['apartments_pct'] = None
    except Exception as e:
        result['g36_error'] = str(e)

    return result


# Hermes registry
try:
    from tools.registry import registry
    registry.register(
        name='get_suburb_demographics',
        toolset='property_data',
        schema={
            'name': 'get_suburb_demographics',
            'description': 'Fetch ABS 2021 Census demographics for a Sydney suburb including median income, age, tenure, and dwelling structure.',
            'parameters': {
                'type': 'object',
                'properties': {
                    'suburb': {'type': 'string', 'description': 'Suburb name (e.g. "Marrickville")'}
                },
                'required': ['suburb']
            }
        },
        handler=lambda args, **kw: json.dumps(get_suburb_demographics(args.get('suburb', '')), indent=2),
        check_fn=lambda: True,
        requires_env=[]
    )
except ImportError:
    pass


if __name__ == '__main__':
    suburb = sys.argv[1] if len(sys.argv) > 1 else 'Marrickville'
    print(f"Fetching ABS demographics for: {suburb}")
    data = get_suburb_demographics(suburb)
    print(json.dumps(data, indent=2))

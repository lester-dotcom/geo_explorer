"""
=============================================================================
generate_dwelling_data.py — Fetch Census 2021 TS044 data and write dwelling_data.js
=============================================================================
Data source  : ONS Census 2021 via Nomis API
Table        : TS044 — Accommodation type
Geography    : MSOA level (Type 297) → aggregated to postcode district
Output       : dwelling_data.js (same directory as this script)

Requirements:
    pip install requests

Usage:
    python3 generate_dwelling_data.py            # full run (~5–10 min)
    python3 generate_dwelling_data.py --dry-run  # fetch 100 MSOAs only (test)

Approximate runtime:
    Full run: 5–10 minutes (Nomis data fetch ~1–2 min, MSOA lookup pagination ~3–5 min)
    Dry run:  ~30 seconds
=============================================================================
"""

import sys
import json
import time
import re
import os
import argparse
import requests

# ── Configuration ─────────────────────────────────────────────────────────────

NOMIS_DISCOVERY_URL = "https://www.nomisweb.co.uk/api/v01/dataset/def.sdmx.json?search=TS044"
NOMIS_FALLBACK_IDS  = ["NM_2072_1", "NM_2073_1", "NM_2063_1"]
MSOA_LOOKUP_BASE    = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
    "MSOA21_PCD_LAD_EW_LU/FeatureServer/0/query"
    "?where=1%3D1&outFields=MSOA21CD,pcds&returnGeometry=false&f=json"
)
MSOA_PAGE_SIZE = 2000

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
OUTPUT_FILE = os.path.join(SCRIPT_DIR, "dwelling_data.js")

# Accommodation type category label → bucket mapping (matched by substring)
CATEGORY_MAP = {
    "semi": ["semi-detached"],
    "terraced": ["terraced"],
    "flat": ["purpose-built", "converted", "in a commercial building",
             "in commercial building", "flat, maisonette", "flat or maisonette"],
    "detached": ["detached"],  # checked AFTER semi to avoid false match
}

# ── Step 1: Discover Nomis dataset ID ─────────────────────────────────────────

def discover_dataset_id():
    print("Discovering Nomis dataset ID for TS044…")
    try:
        r = requests.get(NOMIS_DISCOVERY_URL, timeout=30)
        r.raise_for_status()
        data = r.json()
        # Navigate SDMX structure: Structure > KeyFamilies > KeyFamily
        kf_list = (
            data.get("structure", {})
                .get("keyfamilies", {})
                .get("keyfamily", [])
        )
        if isinstance(kf_list, dict):
            kf_list = [kf_list]
        for kf in kf_list:
            agency_id = kf.get("agencyid", "")
            kf_id = kf.get("id", "")
            if kf_id:
                full_id = kf_id if kf_id.startswith("NM_") else f"NM_{kf_id}"
                print(f"  Found dataset: {full_id}")
                return full_id
    except Exception as e:
        print(f"  Discovery request failed: {e}")
    return None


def get_dataset_id():
    discovered = discover_dataset_id()
    if discovered:
        return discovered
    print("  Falling back to known IDs…")
    for fid in NOMIS_FALLBACK_IDS:
        url = f"https://www.nomisweb.co.uk/api/v01/dataset/{fid}/def.sdmx.json"
        try:
            r = requests.get(url, timeout=15)
            if r.status_code == 200:
                print(f"  Using fallback ID: {fid}")
                return fid
        except Exception:
            pass
    raise RuntimeError("Could not determine Nomis dataset ID for TS044.")

# ── Step 2: Fetch variable names from dataset metadata ────────────────────────

def get_variable_name(dataset_id):
    """Return the accommodation-type dimension variable name (e.g. C2021_ACCOMODATION_TYPE_8_CODE)."""
    print(f"Fetching metadata for {dataset_id}…")
    url = f"https://www.nomisweb.co.uk/api/v01/dataset/{dataset_id}/def.sdmx.json"
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        dims = (
            data.get("structure", {})
                .get("keyfamilies", {})
                .get("keyfamily", {})
                .get("components", {})
                .get("dimension", [])
        )
        if isinstance(dims, dict):
            dims = [dims]
        for dim in dims:
            concept_ref = dim.get("conceptref", "")
            if "ACCOM" in concept_ref.upper() or "TYPE" in concept_ref.upper():
                print(f"  Accommodation variable: {concept_ref}")
                return concept_ref
    except Exception as e:
        print(f"  Metadata fetch failed: {e}")
    # Best-guess fallback
    guess = "C2021_ACCOMODATION_TYPE_8_CODE"
    print(f"  Using guessed variable name: {guess}")
    return guess


def get_category_labels(dataset_id, variable_name):
    """Return dict of {code_value: label} for the accommodation type variable."""
    print("Fetching category labels…")
    url = (
        f"https://www.nomisweb.co.uk/api/v01/dataset/{dataset_id}"
        f"/{variable_name.lower()}.def.sdmx.json"
    )
    labels = {}
    try:
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        data = r.json()
        items = (
            data.get("structure", {})
                .get("codelists", {})
                .get("codelist", {})
                .get("code", [])
        )
        if isinstance(items, dict):
            items = [items]
        for item in items:
            code = str(item.get("value", ""))
            desc = item.get("description", {})
            label = desc.get("en", desc) if isinstance(desc, dict) else str(desc)
            if isinstance(label, dict):
                label = next(iter(label.values()), "")
            labels[code] = label.lower()
    except Exception as e:
        print(f"  Labels fetch failed ({e}), will use numeric code guessing.")
    print(f"  {len(labels)} labels loaded.")
    return labels


def classify_label(label_lower):
    """Return bucket name for a category label, or None to skip."""
    # Semi-detached must come before detached check
    if "semi-detached" in label_lower or "semi detached" in label_lower:
        return "semi"
    if "terraced" in label_lower:
        return "terraced"
    if any(kw in label_lower for kw in ["purpose-built flat", "converted flat",
                                         "in a commercial", "in commercial",
                                         "flat, maisonette", "flat or maisonette",
                                         "flat/maisonette", "apartment"]):
        return "flat"
    if "detached" in label_lower:
        return "detached"
    return None  # caravans, mobile, other → ignored


# ── Step 3: Download MSOA data from Nomis ─────────────────────────────────────

def fetch_nomis_data(dataset_id, variable_name, dry_run=False):
    """Download CSV data, return list of dicts {geo_code, cat_code, value}."""
    print(f"Fetching Nomis MSOA data (dataset={dataset_id}, variable={variable_name})…")
    geo_param = "TYPE297"
    limit_param = "&recordlimit=100" if dry_run else ""
    url = (
        f"https://www.nomisweb.co.uk/api/v01/dataset/{dataset_id}/data.csv"
        f"?geography={geo_param}&measures=20100"
        f"&select=geography_code,{variable_name},OBS_VALUE"
        f"{limit_param}"
    )
    print(f"  URL: {url}")
    rows = []
    try:
        r = requests.get(url, timeout=300, stream=True)
        r.raise_for_status()
        lines = r.text.splitlines()
        if not lines:
            raise ValueError("Empty response from Nomis")
        header = [h.strip().upper() for h in lines[0].split(",")]
        print(f"  Header columns: {header}")
        # Find column indices
        geo_idx = next((i for i, h in enumerate(header) if "GEOGRAPHY_CODE" in h or h == "GEOGRAPHY"), None)
        cat_idx = next((i for i, h in enumerate(header) if "TYPE" in h or "ACCOM" in h or "C2021" in h), None)
        val_idx = next((i for i, h in enumerate(header) if "OBS_VALUE" in h or "VALUE" in h), None)
        if geo_idx is None or cat_idx is None or val_idx is None:
            # Try positional fallback: assume geography_code, cat, obs_value
            print(f"  WARNING: Could not identify columns by name, attempting positional (0,1,2)")
            geo_idx, cat_idx, val_idx = 0, 1, 2
        for line in lines[1:]:
            if not line.strip():
                continue
            parts = line.split(",")
            if len(parts) <= max(geo_idx, cat_idx, val_idx):
                continue
            try:
                rows.append({
                    "geo_code": parts[geo_idx].strip(),
                    "cat_code": parts[cat_idx].strip(),
                    "value": int(float(parts[val_idx].strip()))
                })
            except (ValueError, IndexError):
                pass
        print(f"  {len(rows)} data rows fetched.")
    except Exception as e:
        print(f"  ERROR fetching Nomis data: {e}")
        raise
    return rows, header, cat_idx


# ── Step 4: Download MSOA → Postcode lookup ───────────────────────────────────

def fetch_msoa_postcode_lookup():
    """Return dict {msoa_code: postcode_district}."""
    print("Fetching MSOA → postcode lookup (paginated)…")
    lookup = {}  # msoa_code → postcode_district (most common)
    # We'll collect all pcd values per MSOA then pick the district
    msoa_pcds = {}  # msoa_code → list of postcode strings

    offset = 0
    page = 1
    while True:
        url = f"{MSOA_LOOKUP_BASE}&resultOffset={offset}&resultRecordCount={MSOA_PAGE_SIZE}"
        try:
            r = requests.get(url, timeout=60)
            r.raise_for_status()
            data = r.json()
        except Exception as e:
            print(f"  Page {page} fetch error: {e}. Retrying in 5s…")
            time.sleep(5)
            try:
                r = requests.get(url, timeout=60)
                r.raise_for_status()
                data = r.json()
            except Exception as e2:
                print(f"  Page {page} retry failed: {e2}. Stopping pagination.")
                break

        features = data.get("features", [])
        if not features:
            break
        for feat in features:
            attrs = feat.get("attributes", {})
            msoa = attrs.get("MSOA21CD", "")
            pcd  = attrs.get("pcds", "") or ""
            if not msoa or not pcd:
                continue
            district = extract_district(pcd.strip())
            if district:
                if msoa not in msoa_pcds:
                    msoa_pcds[msoa] = {}
                msoa_pcds[msoa][district] = msoa_pcds[msoa].get(district, 0) + 1

        print(f"  Page {page}: {len(features)} records (total MSOAs so far: {len(msoa_pcds)})")
        offset += MSOA_PAGE_SIZE
        page   += 1
        if len(features) < MSOA_PAGE_SIZE:
            break  # last page
        time.sleep(0.2)

    # Pick most-common district per MSOA
    for msoa, dist_counts in msoa_pcds.items():
        lookup[msoa] = max(dist_counts, key=dist_counts.get)

    print(f"  MSOA lookup complete: {len(lookup)} MSOAs mapped to districts.")
    return lookup


def extract_district(postcode):
    """Extract postcode district from a full postcode, e.g. 'CM13 3LW' → 'CM13'."""
    postcode = postcode.strip().upper()
    # Standard UK postcode: split on space, take first part
    parts = postcode.split()
    if parts:
        return parts[0]
    # Fallback: match letters then numbers
    m = re.match(r'^([A-Z]{1,2}\d{1,2}[A-Z]?)', postcode)
    if m:
        return m.group(1)
    return None


# ── Step 5: Load HNW centroids ────────────────────────────────────────────────

def load_hnw_centroids():
    """Parse hnw_data.js and return dict {district: {lat, lng}}."""
    hnw_path = os.path.join(SCRIPT_DIR, "hnw_data.js")
    centroids = {}
    try:
        with open(hnw_path, "r") as f:
            content = f.read()
        # Extract entries: "DISTRICT":{"hnw":..,"lat":..,"lng":..}
        pattern = re.compile(
            r'"([A-Z0-9]+)"\s*:\s*\{[^}]*"lat"\s*:\s*([-\d.]+)[^}]*"lng"\s*:\s*([-\d.]+)',
            re.DOTALL
        )
        for m in pattern.finditer(content):
            dist, lat, lng = m.group(1), float(m.group(2)), float(m.group(3))
            centroids[dist] = {"lat": lat, "lng": lng}
        print(f"  Loaded {len(centroids)} HNW centroids from hnw_data.js.")
    except Exception as e:
        print(f"  Could not load hnw_data.js: {e}")
    return centroids


# ── Step 6: Aggregate + compute percentages ───────────────────────────────────

def aggregate(nomis_rows, cat_labels, msoa_lookup):
    """
    Return dict {district: {detached, semi, terraced, flat, _total}} with raw counts.
    """
    buckets = {}  # district → {detached, semi, terraced, flat, other, total}

    for row in nomis_rows:
        msoa     = row["geo_code"]
        cat_code = row["cat_code"]
        value    = row["value"]

        district = msoa_lookup.get(msoa)
        if not district:
            continue

        # Classify category
        label = cat_labels.get(cat_code, "")
        if label:
            bucket = classify_label(label)
        else:
            # No label available — try to infer from numeric code order heuristic
            # (Nomis TS044 typical order: 1=detached, 2=semi, 3=terraced, 4=flat PB,
            #  5=flat conv, 6=flat comm, 7=caravan, 8=other)
            try:
                code_int = int(cat_code)
                bucket_by_code = {1: "detached", 2: "semi", 3: "terraced",
                                   4: "flat", 5: "flat", 6: "flat"}
                bucket = bucket_by_code.get(code_int)
            except ValueError:
                bucket = None

        if district not in buckets:
            buckets[district] = {"detached": 0, "semi": 0, "terraced": 0,
                                  "flat": 0, "other": 0, "total": 0}
        buckets[district]["total"] += value
        if bucket:
            buckets[district][bucket] += value
        else:
            buckets[district]["other"] += value

    return buckets


def compute_percentages(raw_buckets):
    """Convert raw counts to percentages (excluding other/caravan from denominator)."""
    result = {}
    for district, counts in raw_buckets.items():
        denom = counts["detached"] + counts["semi"] + counts["terraced"] + counts["flat"]
        if denom == 0:
            continue
        result[district] = {
            "detached": round(counts["detached"] / denom * 100),
            "semi":     round(counts["semi"]     / denom * 100),
            "terraced": round(counts["terraced"] / denom * 100),
            "flat":     round(counts["flat"]     / denom * 100),
        }
    return result


# ── Step 7: Compute centroids for districts not in HNW data ──────────────────

def compute_missing_centroids(pct_districts, hnw_centroids, msoa_lookup,
                               nomis_rows):
    """
    For districts not in hnw_centroids, attempt to derive a centroid.
    We do this by averaging known MSOA centroids — but we don't have
    MSOA coordinates here. Instead, fall back to the postcodes.io bulk API.
    Returns dict {district: {lat, lng}}.
    """
    missing = [d for d in pct_districts if d not in hnw_centroids]
    if not missing:
        return {}

    print(f"  Computing centroids for {len(missing)} districts not in hnw_data.js…")
    # Build a sample postcode per district from the MSOA lookup
    district_to_sample = {}
    for msoa, dist in msoa_lookup.items():
        if dist in missing and dist not in district_to_sample:
            # We only have the district code, so append a common suffix for lookup
            district_to_sample[dist] = dist  # will query as outward code

    computed = {}
    # Use postcodes.io outward code lookup
    batch_size = 100
    dist_list = list(district_to_sample.keys())
    for i in range(0, len(dist_list), batch_size):
        batch = dist_list[i:i + batch_size]
        try:
            r = requests.post(
                "https://api.postcodes.io/outcodes",
                json={"outcodes": batch},
                timeout=30
            )
            if r.status_code == 200:
                data = r.json()
                for item in data.get("result", []):
                    if item and item.get("outcode") and item.get("latitude"):
                        computed[item["outcode"].upper()] = {
                            "lat": round(item["latitude"], 4),
                            "lng": round(item["longitude"], 4),
                        }
        except Exception as e:
            print(f"    postcodes.io batch failed: {e}")
        time.sleep(0.1)

    print(f"  Got {len(computed)} centroids from postcodes.io.")
    return computed


# ── Step 8: Write output ───────────────────────────────────────────────────────

def write_js(pct_data, centroids, output_path):
    lines = ["const DWELLING_DATA = {"]
    for district in sorted(pct_data.keys()):
        pct = pct_data[district]
        c   = centroids.get(district)
        if not c:
            continue  # skip if no coordinates
        lat = c["lat"]
        lng = c["lng"]
        lines.append(
            f"  '{district}': {{ lat: {lat}, lng: {lng}, "
            f"detached: {pct['detached']}, semi: {pct['semi']}, "
            f"terraced: {pct['terraced']}, flat: {pct['flat']} }},"
        )
    lines.append("};")
    content = "\n".join(lines) + "\n"
    with open(output_path, "w") as f:
        f.write(content)
    print(f"\nWrote {output_path} with {len([l for l in lines if l.startswith('  ')])} districts.")


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generate dwelling_data.js from Census 2021 TS044")
    parser.add_argument("--dry-run", action="store_true",
                        help="Fetch only 100 MSOAs for a quick test")
    args = parser.parse_args()

    if args.dry_run:
        print("=== DRY RUN MODE (100 MSOAs only) ===\n")

    # 1. Get dataset ID
    dataset_id = get_dataset_id()

    # 2. Get variable name + category labels
    variable_name = get_variable_name(dataset_id)
    cat_labels    = get_category_labels(dataset_id, variable_name)

    # 3. Fetch Nomis MSOA data
    nomis_rows, header, cat_idx = fetch_nomis_data(dataset_id, variable_name,
                                                    dry_run=args.dry_run)
    if not nomis_rows:
        print("ERROR: No data returned from Nomis. Aborting.")
        sys.exit(1)

    # 4. Fetch MSOA → postcode district lookup
    msoa_lookup = fetch_msoa_postcode_lookup()

    # 5. Load HNW centroids
    print("\nLoading HNW centroids…")
    hnw_centroids = load_hnw_centroids()

    # 6. Aggregate
    print("\nAggregating MSOA data to postcode districts…")
    raw_buckets = aggregate(nomis_rows, cat_labels, msoa_lookup)
    print(f"  {len(raw_buckets)} districts aggregated.")

    # 7. Percentages
    pct_data = compute_percentages(raw_buckets)
    print(f"  {len(pct_data)} districts with valid percentages.")

    # 8. Centroids for missing districts
    extra_centroids = compute_missing_centroids(pct_data, hnw_centroids,
                                                msoa_lookup, nomis_rows)
    all_centroids = {**hnw_centroids, **extra_centroids}

    # 9. Write output
    write_js(pct_data, all_centroids, OUTPUT_FILE)
    print("Done.")


if __name__ == "__main__":
    main()

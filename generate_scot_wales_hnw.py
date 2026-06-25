"""
generate_scot_wales_hnw.py — Add Scotland (SIMD) and Wales (WIMD) to hnw_data.js
using real deprivation index data from official government sources.

Scotland: SIMD 2020v2 postcode lookup
  Source: Scottish Government
  URL: https://www.gov.scot/binaries/content/documents/govscot/publications/statistics/2020/09/
       scottish-index-of-multiple-deprivation-2020v2-postcode-lookup/documents/
       simd2020v2_postcode_lookup.csv/simd2020v2_postcode_lookup.csv/
       govscot%3Adocument/simd2020v2_postcode_lookup.csv
  Columns used: Postcode, SIMD2020V2_Income_Domain_Rank (1=most deprived, 6976=least deprived)

Wales: WIMD 2019
  The Welsh Government publishes WIMD 2019 at LSOA level. The simplest machine-readable source is:
    https://statswales.gov.wales/Catalogue/Community-Safety-and-Social-Inclusion/Welsh-Index-of-Multiple-Deprivation/WIMD-2019
  However, the StatsWales API requires dataset-specific query parameters that change over time.

  Recommended manual download approach:
    1. Visit https://gov.wales/welsh-index-multiple-deprivation-2019-results-interactive-map
    2. Download "WIMD 2019 results" Excel/CSV (file: wimd2019_results_by_indicator_la.xlsx)
       or the LSOA-level file from:
       https://gov.wales/sites/default/files/statistics-and-research/2019-11/
       welsh-index-multiple-deprivation-2019-index-and-domain-scores-ranks.ods
    3. Save the income domain sheet as wimd_2019_income.csv with columns:
         LSOA_Code, LSOA_Name, Income_Domain_Rank
       (rank out of 1909 LSOAs, 1=most deprived)

  Alternatively, ONS Nomis provides a Wales income deprivation indicator:
    https://www.nomisweb.co.uk/census/2011/qs119ew
  But this is Census-based, not WIMD-specific.

  To link LSOAs to postcode districts you also need the ONS LSOA to postcode lookup:
    https://geoportal.statistics.gov.uk/datasets/ons-uprn-directory-2022/about
  or the simpler:
    https://geoportal.statistics.gov.uk/datasets/postcode-to-output-area-to-lower-layer-super-output-area-to-middle-layer-super-output-area-to-local-authority-district-february-2021-lookup-in-the-uk/about

Usage:
  pip install requests
  python3 generate_scot_wales_hnw.py

  For Wales with manual data:
  python3 generate_scot_wales_hnw.py --wimd-csv /path/to/wimd_2019_income.csv \
                                      --postcode-csv /path/to/postcode_lsoa_lookup.csv

Outputs:
  Updates /tmp/geo_explorer/hnw_data.js in place.
"""

import re
import sys
import json
import csv
import io
import argparse
import statistics
from pathlib import Path

try:
    import requests
except ImportError:
    print("ERROR: requests not installed. Run: pip install requests")
    sys.exit(1)

# ─── Paths ────────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).parent
HNW_DATA_JS = SCRIPT_DIR / "hnw_data.js"
DWELLING_DATA_JS = SCRIPT_DIR / "dwelling_data.js"

# ─── SIMD URL ─────────────────────────────────────────────────────────────────
SIMD_URL = (
    "https://www.gov.scot/binaries/content/documents/govscot/publications/statistics/"
    "2020/09/scottish-index-of-multiple-deprivation-2020v2-postcode-lookup/documents/"
    "simd2020v2_postcode_lookup.csv/simd2020v2_postcode_lookup.csv/"
    "govscot%3Adocument/simd2020v2_postcode_lookup.csv"
)

SIMD_TOTAL_ZONES = 6976  # total data zones in SIMD 2020


def load_js_data(js_file: Path, var_name: str) -> dict:
    """Parse a JS file of the form: const VAR = { ... }; and return the dict."""
    text = js_file.read_text(encoding="utf-8")
    # Strip JS variable declaration
    match = re.search(r'(?:const|var)\s+' + var_name + r'\s*=\s*(\{[\s\S]*?\});?\s*$', text)
    if not match:
        raise ValueError(f"Could not find {var_name} in {js_file}")
    return json.loads(match.group(1))


def save_hnw_data(data: dict, js_file: Path):
    """Write HNW_DATA back to JS file."""
    sorted_data = dict(sorted(data.items()))
    lines = json.dumps(sorted_data, separators=(', ', ': '))
    # Pretty-print one entry per line
    lines = lines.replace('{', '{\n  ', 1)
    lines = lines.replace('}', '\n}', -1 if lines.count('}') == 1 else 0)
    # Actually use proper formatting
    inner = json.dumps(sorted_data, indent=None)
    # Emit as compact JSON but one key per line
    entries = []
    for k, v in sorted_data.items():
        entries.append(f'  {json.dumps(k)}: {json.dumps(v)}')
    output = "const HNW_DATA = {\n" + ",\n".join(entries) + "\n};\n"
    js_file.write_text(output, encoding="utf-8")


def rank_to_hnw(rank: float, total: int) -> int:
    """
    Convert a deprivation rank (1=most deprived, total=least deprived)
    to an HNW score (1=most deprived, 10=most affluent).
    """
    # Normalise to 0.0 (most deprived) → 1.0 (least deprived)
    normalised = (rank - 1) / (total - 1)
    # Map to 1–10
    hnw = int(normalised * 9) + 1
    return max(1, min(10, hnw))


def extract_district(postcode: str) -> str | None:
    """Extract postcode district from a full postcode, e.g. 'G12 8AA' → 'G12'."""
    postcode = postcode.strip().upper()
    parts = postcode.split()
    if parts:
        return parts[0]
    # Try regex for postcodes without space
    m = re.match(r'^([A-Z]{1,2}\d{1,2}[A-Z]?)\d[A-Z]{2}$', postcode)
    if m:
        return m.group(1)
    return None


# ─── Scotland: SIMD 2020 ──────────────────────────────────────────────────────

def fetch_simd_data() -> dict[str, int]:
    """
    Download SIMD 2020v2 postcode lookup CSV and return
    {district: median_income_rank} for all Scottish districts.
    """
    print(f"Downloading SIMD 2020v2 CSV...")
    try:
        resp = requests.get(SIMD_URL, timeout=60)
        resp.raise_for_status()
    except requests.RequestException as e:
        print(f"ERROR downloading SIMD data: {e}")
        print("Check URL or internet connection. Skipping Scotland.")
        return {}

    print(f"Downloaded {len(resp.content):,} bytes")

    # Parse CSV
    text = resp.content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))

    # Find relevant columns
    fieldnames = reader.fieldnames or []
    print(f"CSV columns: {fieldnames[:10]}...")

    # Find the income rank column (may vary slightly by version)
    income_col = None
    for col in fieldnames:
        if "income" in col.lower() and "rank" in col.lower():
            income_col = col
            break
    if not income_col:
        # Fallback: look for SIMD rank column
        for col in fieldnames:
            if "rank" in col.lower() and "simd" in col.lower():
                income_col = col
                break

    if not income_col:
        print(f"ERROR: Could not find income rank column. Available: {fieldnames}")
        return {}

    print(f"Using income column: {income_col}")

    # Aggregate: district → list of income ranks
    district_ranks: dict[str, list[int]] = {}
    rows_processed = 0
    rows_skipped = 0

    for row in reader:
        postcode = row.get("Postcode", "").strip()
        rank_str = row.get(income_col, "").strip()

        if not postcode or not rank_str:
            rows_skipped += 1
            continue

        district = extract_district(postcode)
        if not district:
            rows_skipped += 1
            continue

        try:
            rank = float(rank_str)
        except ValueError:
            rows_skipped += 1
            continue

        district_ranks.setdefault(district, []).append(rank)
        rows_processed += 1

    print(f"Processed {rows_processed:,} postcode rows ({rows_skipped:,} skipped)")
    print(f"Found {len(district_ranks):,} unique districts")

    # Compute median rank per district → HNW score
    district_hnw: dict[str, int] = {}
    for district, ranks in district_ranks.items():
        median_rank = statistics.median(ranks)
        hnw = rank_to_hnw(median_rank, SIMD_TOTAL_ZONES)
        district_hnw[district] = hnw

    return district_hnw


# ─── Wales: WIMD 2019 ─────────────────────────────────────────────────────────

WIMD_TOTAL_LSOAS = 1909  # total LSOAs in Wales WIMD 2019


def load_wimd_from_csv(wimd_csv: Path, postcode_csv: Path) -> dict[str, int]:
    """
    Load WIMD 2019 income ranks from manually downloaded CSVs.

    wimd_csv: CSV with columns LSOA_Code, Income_Domain_Rank
    postcode_csv: ONS postcode-to-LSOA lookup with columns pcds (postcode), lsoa11cd (LSOA code)

    Returns {district: hnw_score}
    """
    # Load WIMD ranks
    lsoa_ranks: dict[str, float] = {}
    with open(wimd_csv, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            lsoa = row.get("LSOA_Code", "").strip()
            rank_str = row.get("Income_Domain_Rank", "").strip()
            if lsoa and rank_str:
                try:
                    lsoa_ranks[lsoa] = float(rank_str)
                except ValueError:
                    pass
    print(f"Loaded {len(lsoa_ranks):,} LSOA income ranks from WIMD CSV")

    # Load postcode→LSOA lookup (filter Wales only: LSOA starts with W)
    postcode_lsoa: dict[str, str] = {}
    with open(postcode_csv, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for row in reader:
            pc = row.get("pcds", row.get("postcode", "")).strip()
            lsoa = row.get("lsoa11cd", row.get("lsoa", "")).strip()
            if pc and lsoa and lsoa.startswith("W"):
                postcode_lsoa[pc.upper()] = lsoa
    print(f"Loaded {len(postcode_lsoa):,} Welsh postcode→LSOA mappings")

    # Aggregate: district → income ranks
    district_ranks: dict[str, list[float]] = {}
    for postcode, lsoa in postcode_lsoa.items():
        rank = lsoa_ranks.get(lsoa)
        if rank is None:
            continue
        district = extract_district(postcode)
        if district:
            district_ranks.setdefault(district, []).append(rank)

    district_hnw: dict[str, int] = {}
    for district, ranks in district_ranks.items():
        median_rank = statistics.median(ranks)
        hnw = rank_to_hnw(median_rank, WIMD_TOTAL_LSOAS)
        district_hnw[district] = hnw

    print(f"Computed HNW scores for {len(district_hnw):,} Welsh districts")
    return district_hnw


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--wimd-csv", help="Path to manually downloaded WIMD 2019 income CSV")
    parser.add_argument("--postcode-csv", help="Path to ONS postcode-to-LSOA lookup CSV")
    parser.add_argument("--skip-scotland", action="store_true", help="Skip Scotland (SIMD) processing")
    parser.add_argument("--skip-wales", action="store_true", help="Skip Wales (WIMD) processing")
    parser.add_argument("--dry-run", action="store_true", help="Preview changes without writing")
    args = parser.parse_args()

    # Load existing data
    print(f"Loading {HNW_DATA_JS}...")
    hnw_data = load_js_data(HNW_DATA_JS, "HNW_DATA")
    print(f"  {len(hnw_data):,} existing districts")

    # Load dwelling centroids for lat/lng
    print(f"Loading {DWELLING_DATA_JS}...")
    dwelling_data = load_js_data(DWELLING_DATA_JS, "DWELLING_DATA")
    print(f"  {len(dwelling_data):,} districts with centroids")

    new_data: dict[str, int] = {}

    # ── Scotland ──────────────────────────────────────────────────────────────
    if not args.skip_scotland:
        print("\n── Scotland (SIMD 2020) ──────────────────────────────────────")
        simd_hnw = fetch_simd_data()
        if simd_hnw:
            scot_added = 0
            scot_updated = 0
            for district, hnw in simd_hnw.items():
                centroid = dwelling_data.get(district)
                if centroid is None:
                    continue  # No centroid available, skip
                entry = {"hnw": hnw, "lat": centroid["lat"], "lng": centroid["lng"]}
                if district not in hnw_data:
                    scot_added += 1
                else:
                    scot_updated += 1
                new_data[district] = entry
            print(f"Scotland: {scot_added} new districts, {scot_updated} updated")
        else:
            print("Scotland: no data fetched (download failed)")
    else:
        print("\nSkipping Scotland.")

    # ── Wales ─────────────────────────────────────────────────────────────────
    if not args.skip_wales:
        print("\n── Wales (WIMD 2019) ─────────────────────────────────────────")
        if args.wimd_csv and args.postcode_csv:
            wimd_hnw = load_wimd_from_csv(Path(args.wimd_csv), Path(args.postcode_csv))
            if wimd_hnw:
                wales_added = 0
                wales_updated = 0
                for district, hnw in wimd_hnw.items():
                    centroid = dwelling_data.get(district)
                    if centroid is None:
                        continue
                    entry = {"hnw": hnw, "lat": centroid["lat"], "lng": centroid["lng"]}
                    if district not in hnw_data:
                        wales_added += 1
                    else:
                        wales_updated += 1
                    new_data[district] = entry
                print(f"Wales: {wales_added} new districts, {wales_updated} updated")
        else:
            print("Wales: --wimd-csv and --postcode-csv not provided.")
            print()
            print("To obtain WIMD 2019 data:")
            print("  1. Download the WIMD 2019 results file from:")
            print("     https://gov.wales/welsh-index-multiple-deprivation-2019-results-interactive-map")
            print("     File: 'Welsh Index of Multiple Deprivation (WIMD) 2019 results' (ODS/Excel)")
            print("     Extract income domain ranks to a CSV with columns: LSOA_Code, Income_Domain_Rank")
            print()
            print("  2. Download the ONS postcode-to-LSOA lookup from:")
            print("     https://geoportal.statistics.gov.uk/datasets/ons-postcode-directory-latest-centroids/")
            print("     or Nomis: https://www.nomisweb.co.uk/default.aspx")
            print("     Relevant columns: pcds (postcode), lsoa11cd (LSOA code)")
            print()
            print("  3. Run:")
            print("     python3 generate_scot_wales_hnw.py \\")
            print("       --wimd-csv wimd_2019_income.csv \\")
            print("       --postcode-csv postcode_to_lsoa.csv")
            print()
            print("Skipping Wales for now.")
    else:
        print("\nSkipping Wales.")

    # ── Write results ─────────────────────────────────────────────────────────
    if not new_data:
        print("\nNo new data to write.")
        return

    if args.dry_run:
        print(f"\nDRY RUN: would update {len(new_data):,} districts")
        sample = list(new_data.items())[:10]
        for k, v in sample:
            print(f"  {k}: {v}")
        return

    # Merge
    merged = dict(hnw_data)
    merged.update(new_data)
    save_hnw_data(merged, HNW_DATA_JS)

    print(f"\nDone. hnw_data.js updated:")
    print(f"  Before: {len(hnw_data):,} districts")
    print(f"  After:  {len(merged):,} districts")
    print(f"  Net new: {len(merged) - len(hnw_data):,}")


if __name__ == "__main__":
    main()

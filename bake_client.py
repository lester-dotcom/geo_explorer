#!/usr/bin/env python3
"""
bake_client.py — Generate a pre-loaded client version of the Geo Intelligence Suite.

Usage:
  python3 bake_client.py --client "Acme Corp" --customers customers.xlsx

Options:
  --client      Client name (used for folder slug and page titles)
  --customers   Excel (.xlsx) or CSV file with customer postcodes
  --locations   Excel (.xlsx) or CSV file with competitor/location postcodes (overlap-map only)
  --showroom    Showroom postcode for local-coverage tool (e.g. SW4 2JQ)
  --postcode-col  Column name containing postcodes (auto-detected if omitted)
  --name-col      Column name for customer/location names (auto-detected if omitted)

Output:
  Creates  clients/<slug>/  inside the repo, containing all tools with data pre-loaded.
  Shared JS files (hnw_data.js etc.) are referenced via ../../ relative paths.
"""

import argparse
import json
import os
import re
import shutil
import sys
import unicodedata

# ── Optional Excel support ─────────────────────────────────────────────────────
try:
    import openpyxl
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

# ── Helpers ────────────────────────────────────────────────────────────────────

def slugify(text):
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    text = re.sub(r'[^\w\s-]', '', text.lower())
    return re.sub(r'[\s-]+', '-', text).strip('-')


def looks_like_postcode(value):
    """Return True if value resembles a UK postcode (loose check)."""
    v = str(value).strip().upper().replace(' ', '')
    return bool(re.match(r'^[A-Z]{1,2}\d{1,2}[A-Z]?\d[A-Z]{2}$', v))


def normalise_postcode(value):
    """Return uppercase postcode with canonical spacing (e.g. SW42JQ → SW4 2JQ)."""
    v = str(value).strip().upper().replace(' ', '')
    if len(v) >= 5:
        return v[:-3] + ' ' + v[-3:]
    return v


def detect_postcode_col(headers):
    """Pick the most likely postcode column from header names."""
    candidates = ['postcode', 'post code', 'postal code', 'post_code', 'zip', 'pc']
    for h in headers:
        if h.strip().lower() in candidates:
            return h
    # Fallback: first header whose values look like postcodes (checked by caller)
    return None


def detect_name_col(headers):
    """Pick the most likely name/label column."""
    candidates = ['name', 'company', 'customer', 'client', 'account', 'organisation',
                  'organization', 'business', 'site', 'location', 'label']
    for h in headers:
        if h.strip().lower() in candidates:
            return h
    return None


def read_file(path, postcode_col_hint=None, name_col_hint=None):
    """
    Read an Excel or CSV file and return a list of dicts with keys:
      postcode, name (may be empty string)
    Only rows with valid-looking postcodes are included.
    """
    ext = os.path.splitext(path)[1].lower()

    rows = []
    headers = []

    if ext in ('.xlsx', '.xls', '.xlsm'):
        if not HAS_OPENPYXL:
            sys.exit("openpyxl is required to read Excel files.  Install with:  pip3 install openpyxl")
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        ws = wb.active
        raw = list(ws.iter_rows(values_only=True))
        if not raw:
            return []
        headers = [str(h).strip() if h is not None else '' for h in raw[0]]
        for r in raw[1:]:
            rows.append(dict(zip(headers, [str(c).strip() if c is not None else '' for c in r])))

    elif ext == '.csv' or ext == '.txt':
        import csv
        with open(path, newline='', encoding='utf-8-sig') as f:
            reader = csv.DictReader(f)
            headers = reader.fieldnames or []
            for r in reader:
                rows.append({k: (v or '').strip() for k, v in r.items()})
    else:
        sys.exit(f"Unsupported file type: {ext}  (use .xlsx, .csv, or .txt)")

    # Detect columns
    pc_col = postcode_col_hint or detect_postcode_col(headers)
    nm_col = name_col_hint or detect_name_col(headers)

    # If no postcode column found by name, sniff by value
    if not pc_col:
        for h in headers:
            sample = [r[h] for r in rows[:20] if r.get(h)]
            if sample and sum(looks_like_postcode(v) for v in sample) / len(sample) > 0.6:
                pc_col = h
                break

    if not pc_col:
        sys.exit(
            f"Could not detect a postcode column in {path}.\n"
            f"Headers found: {headers}\n"
            f"Use --postcode-col to specify it explicitly."
        )

    print(f"  Postcode column : {pc_col!r}")
    if nm_col:
        print(f"  Name column     : {nm_col!r}")

    result = []
    for r in rows:
        pc_raw = r.get(pc_col, '')
        if not pc_raw:
            continue
        pc = normalise_postcode(pc_raw)
        if not looks_like_postcode(pc_raw):
            continue
        name = r.get(nm_col, '') if nm_col else ''
        result.append({'postcode': pc, 'name': name})

    return result


def to_js_array(records):
    """Convert list of {postcode, name} to a compact JS array of strings (postcodes only)."""
    postcodes = [r['postcode'] for r in records]
    return json.dumps(postcodes, indent=2)


def to_js_array_with_names(records):
    """Convert list of {postcode, name} to JS array of objects for tools that use names."""
    return json.dumps([{'postcode': r['postcode'], 'name': r['name']} for r in records], indent=2)


# ── HTML patching ──────────────────────────────────────────────────────────────

BAKED_COMMENT = "/* ── BAKED CLIENT DATA (generated by bake_client.py) ── */"

def inject_js_constant(html, name, value_js):
    """Insert a const declaration right before the first <script> that contains 'let customerData'."""
    injection = f"\n{BAKED_COMMENT}\nconst {name} = {value_js};\n"
    # Insert before the script block that declares customerData
    pattern = r'(<script[^>]*>)'
    matches = list(re.finditer(r'let customerData\s*=', html))
    if matches:
        # Find the opening <script> tag before the first customerData declaration
        pos = matches[0].start()
        # Walk back to find the nearest <script> open tag
        script_open = html.rfind('<script', 0, pos)
        if script_open != -1:
            tag_end = html.index('>', script_open) + 1
            return html[:tag_end] + injection + html[tag_end:]
    # Fallback: inject before </head>
    return html.replace('</head>', injection + '</head>', 1)


def patch_customer_data_init(html, baked_var='BAKED_CUSTOMERS'):
    """Replace  let customerData = [];  with  let customerData = BAKED_CUSTOMERS.slice();"""
    return re.sub(
        r'let customerData\s*=\s*\[\s*\];',
        f'let customerData = {baked_var}.slice();',
        html
    )


def patch_upload_label(html, label_id, status_html):
    """
    Replace the upload <label> element (identified by id) with a pre-loaded status badge.
    Handles both single-line and multi-line label tags.
    """
    # Match <label ... id="labelId" ...>...</label>
    pattern = re.compile(
        r'<label[^>]+id=["\']' + re.escape(label_id) + r'["\'][^>]*>.*?</label>',
        re.DOTALL | re.IGNORECASE
    )
    replacement = status_html
    result, count = pattern.subn(replacement, html)
    if count == 0:
        # Try the reverse attribute order
        pattern2 = re.compile(
            r'<label[^>]*>.*?id=["\']' + re.escape(label_id) + r'["\'].*?</label>',
            re.DOTALL | re.IGNORECASE
        )
        result, count = pattern2.subn(replacement, html)
    if count == 0:
        print(f"  Warning: could not find upload label #{label_id} — upload UI left unchanged")
    return result


def baked_badge(count, label="customers", color="#16a34a"):
    return (
        f'<div style="border:1.5px solid {color};border-radius:8px;padding:10px;'
        f'text-align:center;background:rgba(22,163,74,0.07);font-size:12px;font-weight:600;'
        f'color:{color}">✓ {count:,} {label} pre-loaded</div>'
    )


def fix_asset_paths(html, depth=2):
    """
    Rewrite src/href paths to shared JS/data files so they resolve from clients/<slug>/.
    Paths that already start with http / data: / # / ../../ are left alone.
    """
    prefix = '../../'
    shared_files = [
        'hnw_data.js', 'dwelling_data.js', 'valid_sectors.js', 'valid_districts.js',
        'brand-map.html', 'customer-map.html', 'dwelling-explorer.html',
        'hnw-finder.html', 'local-coverage.html', 'overlap-map.html',
        'watchdog-report.html', 'profile-tool', 'index.html',
    ]

    def rewrite(m):
        attr, quote, path = m.group(1), m.group(2), m.group(3)
        if path.startswith(('http', 'data:', '#', '../../', '/')):
            return m.group(0)
        # Check if the base name matches a shared file
        base = path.split('/')[-1].split('?')[0]
        if any(base == sf or path == sf for sf in shared_files):
            return f'{attr}={quote}{prefix}{path}{quote}'
        return m.group(0)

    html = re.sub(r'(src|href)=(["\'])([^"\'#>]+)\2', rewrite, html)
    return html


# ── Per-tool patchers ──────────────────────────────────────────────────────────

def patch_local_coverage(html, customers, showroom_pc=None):
    count = len(customers)
    js_arr = to_js_array(customers)

    # Inject constant
    html = inject_js_constant(html, 'BAKED_CUSTOMERS', js_arr)
    # Swap init
    html = patch_customer_data_init(html)
    # Replace upload label
    html = patch_upload_label(html, 'uploadLabel', baked_badge(count))

    # Remove the file input listener block (it would override our data)
    html = re.sub(
        r"document\.getElementById\('fileInput'\)\.addEventListener\('change'.*?\}\);",
        "// fileInput listener removed (data pre-loaded)",
        html, count=1, flags=re.DOTALL
    )

    # Pre-fill showroom postcode if provided
    if showroom_pc:
        # Replace the default value="" on the showroom input, or inject via JS
        html = re.sub(
            r"(id=['\"]postcode['\"][^>]*value=['\"])['\"]",
            rf'\g<1>{showroom_pc}\'',
            html
        )
        # Also inject a JS line to set it after DOM load
        html = html.replace(
            '</body>',
            f'<script>document.addEventListener("DOMContentLoaded",()=>'
            f'{{const el=document.getElementById("postcode");if(el)el.value="{showroom_pc}";}});</script>\n</body>'
        )

    return html


def patch_dwelling_explorer(html, customers):
    count = len(customers)
    js_arr = to_js_array(customers)

    html = inject_js_constant(html, 'BAKED_CUSTOMERS', js_arr)
    html = patch_customer_data_init(html)
    html = patch_upload_label(html, 'uploadLabel', baked_badge(count))
    html = re.sub(
        r"fileInput\.addEventListener\('change'.*?\}\);",
        "// fileInput listener removed (data pre-loaded)",
        html, count=1, flags=re.DOTALL
    )
    return html


def patch_customer_map(html, customers):
    count = len(customers)
    # customer-map uses objects with name
    js_arr = to_js_array_with_names(customers)

    # Inject as BAKED_CUSTOMERS (array of objects)
    html = inject_js_constant(html, 'BAKED_CUSTOMERS', js_arr)

    # customer-map parseCSV populates customerData — we need to call it differently.
    # Inject a post-load that populates customerData from BAKED_CUSTOMERS
    init_js = (
        '\ndocument.addEventListener("DOMContentLoaded", function() {\n'
        '  if (typeof BAKED_CUSTOMERS !== "undefined" && BAKED_CUSTOMERS.length) {\n'
        '    customerData = BAKED_CUSTOMERS.map(function(r) {\n'
        '      return { postcode: r.postcode || r, name: r.name || "" };\n'
        '    });\n'
        '    if (typeof checkReady === "function") checkReady();\n'
        '    const fn = document.getElementById("fileName");\n'
        '    if (fn) fn.textContent = customerData.length + " customers pre-loaded";\n'
        '    const lbl = document.getElementById("uploadLabel");\n'
        '    if (lbl) lbl.classList.add("has-file");\n'
        '  }\n'
        '});\n'
    )
    html = html.replace('</body>', init_js + '</body>', 1)
    html = patch_upload_label(html, 'uploadLabel', baked_badge(count))
    return html


def patch_hnw_finder(html, customers):
    count = len(customers)
    js_arr = to_js_array(customers)

    html = inject_js_constant(html, 'BAKED_CUSTOMERS', js_arr)
    html = patch_customer_data_init(html)
    html = patch_upload_label(html, 'uploadLabel', baked_badge(count))
    html = re.sub(
        r"document\.getElementById\('fileInput'\)\.addEventListener\('change'.*?\}\);",
        "// fileInput listener removed (data pre-loaded)",
        html, count=1, flags=re.DOTALL
    )
    return html


def patch_overlap_map(html, customers, locations=None):
    c_count = len(customers)
    js_cust = to_js_array(customers)
    html = inject_js_constant(html, 'BAKED_CUSTOMERS', js_cust)

    # Patch customer array init (overlap-map may use a different var name)
    # It uses two separate arrays — patch whichever is customerData
    html = patch_customer_data_init(html, 'BAKED_CUSTOMERS')
    html = patch_upload_label(html, 'ul-c', baked_badge(c_count, 'customers', '#4f46e5'))

    if locations:
        l_count = len(locations)
        js_locs = to_js_array(locations)
        html = inject_js_constant(html, 'BAKED_LOCATIONS', js_locs)
        # overlap-map likely has a second array for locations
        html = re.sub(
            r'let locationData\s*=\s*\[\s*\];',
            'let locationData = BAKED_LOCATIONS.slice();',
            html
        )
        html = patch_upload_label(html, 'ul-l', baked_badge(l_count, 'locations', '#d97706'))

    return html


# ── Tool registry ──────────────────────────────────────────────────────────────

def get_patcher(filename, customers, locations, showroom_pc):
    name = os.path.basename(filename).lower()
    if name == 'local-coverage.html':
        return lambda h: patch_local_coverage(h, customers, showroom_pc)
    if name == 'dwelling-explorer.html':
        return lambda h: patch_dwelling_explorer(h, customers)
    if name == 'customer-map.html':
        return lambda h: patch_customer_map(h, customers)
    if name == 'hnw-finder.html':
        return lambda h: patch_hnw_finder(h, customers)
    if name == 'overlap-map.html':
        return lambda h: patch_overlap_map(h, customers, locations)
    # No customer data for these tools — just copy
    return None


TOOL_FILES = [
    'index.html',
    'local-coverage.html',
    'dwelling-explorer.html',
    'customer-map.html',
    'hnw-finder.html',
    'overlap-map.html',
    'brand-map.html',
    'watchdog-report.html',
    'profile-tool (1).html',  # included if present
]


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Bake a client-specific Geo Explorer')
    parser.add_argument('--client', required=True, help='Client name (e.g. "Acme Corp")')
    parser.add_argument('--customers', required=True, help='Excel or CSV file with customer postcodes')
    parser.add_argument('--locations', help='Excel or CSV for competitor/location postcodes (overlap-map)')
    parser.add_argument('--showroom', help='Showroom postcode for local-coverage (e.g. SW4 2JQ)')
    parser.add_argument('--postcode-col', dest='postcode_col', help='Column name for postcodes')
    parser.add_argument('--name-col', dest='name_col', help='Column name for labels/names')
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    client_slug = slugify(args.client)
    out_dir = os.path.join(script_dir, 'clients', client_slug)

    print(f"\n── Geo Explorer Client Baker ──────────────────────────────")
    print(f"  Client  : {args.client}")
    print(f"  Slug    : {client_slug}")
    print(f"  Output  : clients/{client_slug}/")
    print()

    # Read customer data
    print(f"Reading customers from: {args.customers}")
    customers = read_file(args.customers, args.postcode_col, args.name_col)
    print(f"  → {len(customers)} valid postcodes loaded\n")

    locations = []
    if args.locations:
        print(f"Reading locations from: {args.locations}")
        locations = read_file(args.locations, args.postcode_col, args.name_col)
        print(f"  → {len(locations)} valid postcodes loaded\n")

    if not customers:
        sys.exit("No valid UK postcodes found in the customers file. Check the postcode column.")

    # Create output directory
    os.makedirs(out_dir, exist_ok=True)

    # Process each tool file
    for fname in TOOL_FILES:
        src = os.path.join(script_dir, fname)
        if not os.path.exists(src):
            # Try without the parenthetical
            alt = os.path.join(script_dir, os.path.basename(fname))
            if not os.path.exists(alt):
                print(f"  Skipping (not found): {fname}")
                continue
            src = alt

        dst = os.path.join(out_dir, os.path.basename(fname))
        # Normalise output filename — remove " (1)" suffixes
        dst_name = re.sub(r'\s*\(\d+\)', '', os.path.basename(fname))
        dst = os.path.join(out_dir, dst_name)

        with open(src, 'r', encoding='utf-8') as f:
            html = f.read()

        # Fix asset paths first
        html = fix_asset_paths(html)

        # Patch title to include client name
        html = re.sub(
            r'(<title>[^<]*)(</title>)',
            rf'\g<1> — {args.client}\g<2>',
            html, count=1
        )

        # Apply tool-specific patches
        patcher = get_patcher(dst_name, customers, locations, args.showroom)
        if patcher:
            html = patcher(html)
            print(f"  ✓ Patched  : {dst_name}")
        else:
            print(f"  ○ Copied   : {dst_name}")

        with open(dst, 'w', encoding='utf-8') as f:
            f.write(html)

    # Copy profile-tool directory if present
    profile_src = os.path.join(script_dir, 'profile-tool')
    if os.path.isdir(profile_src):
        profile_dst = os.path.join(out_dir, 'profile-tool')
        if os.path.exists(profile_dst):
            shutil.rmtree(profile_dst)
        shutil.copytree(profile_src, profile_dst)
        print(f"  ○ Copied   : profile-tool/")

    print(f"\n── Done ────────────────────────────────────────────────────")
    print(f"  Client files ready in:  clients/{client_slug}/")
    print(f"  Commit and push to deploy:")
    print(f"    git add clients/{client_slug}")
    print(f'    git commit -m "Add {args.client} client build"')
    print(f"    git push origin main")
    print()
    print(f"  Live URL (after push):")
    print(f"    https://lester-dotcom.github.io/geo_explorer/clients/{client_slug}/")
    print()


if __name__ == '__main__':
    main()

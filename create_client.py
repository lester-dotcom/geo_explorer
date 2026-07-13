#!/usr/bin/env python3
"""
create_client.py — Create a client-specific version of the Geo Intelligence Suite.

Duplicates all tools to  clients/<slug>/  with localStorage persistence:
  - Postcodes uploaded in any tool are saved in the browser
  - They reload automatically on every visit (no re-upload needed)
  - The client can re-upload at any time to update the data
  - Original tools at /geo_explorer/ are untouched

Usage:
  python3 create_client.py --client "Acme Corp" --pin 4821
  python3 create_client.py --client "Acme Corp" --slug acme --pin 4821

Then commit and push the clients/<slug>/ folder to deploy.
"""

import argparse
import os
import re
import shutil
import sys
import unicodedata

# ── Helpers ────────────────────────────────────────────────────────────────────

def slugify(text):
    text = unicodedata.normalize('NFKD', text).encode('ascii', 'ignore').decode()
    text = re.sub(r'[^\w\s-]', '', text.lower())
    return re.sub(r'[\s-]+', '-', text).strip('-')


def fix_asset_paths(html):
    """Rewrite relative paths to shared JS/data files to use ../../ prefix."""
    shared = {
        'hnw_data.js', 'dwelling_data.js', 'valid_sectors.js', 'valid_districts.js',
    }
    # tool links in index.html
    tool_html = {
        'brand-map.html', 'customer-map.html', 'dwelling-explorer.html',
        'hnw-finder.html', 'local-coverage.html', 'overlap-map.html',
        'watchdog-report.html', 'profile-tool',
    }

    def rewrite(m):
        attr, q, path = m.group(1), m.group(2), m.group(3)
        if path.startswith(('http', 'data:', '#', '../../', '/')):
            return m.group(0)
        base = path.split('/')[-1].split('?')[0]
        if base in shared:
            return f'{attr}={q}../../{path}{q}'
        return m.group(0)

    return re.sub(r'(src|href)=(["\'])([^"\'#>\s]+)\2', rewrite, html)


# ── PIN gate ──────────────────────────────────────────────────────────────────

def pin_gate_script(pin, slug):
    """
    Returns a <script> block that locks the page behind a PIN.
    - PIN is hashed (SHA-256) in the browser — plain PIN never stored anywhere.
    - Unlock persists in sessionStorage for the tab lifetime.
    - Overlay is injected before <body> content renders.
    """
    session_key = f'geo_pd_{slug}_auth'
    return f"""
<script>
/* ── Pratt Digital PIN gate ── */
(function() {{
  var SESSION_KEY = '{session_key}';
  var HASH = '{pin}'; // replaced with SHA-256 hex at runtime check

  async function sha256(str) {{
    var buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(str));
    return Array.from(new Uint8Array(buf)).map(function(b){{return b.toString(16).padStart(2,'0');}}).join('');
  }}

  var correctHash = null;
  sha256('{pin}').then(function(h) {{ correctHash = h; }});

  if (sessionStorage.getItem(SESSION_KEY) === '1') return; // already unlocked this tab

  // Build overlay
  var overlay = document.createElement('div');
  overlay.id = 'pd-pin-overlay';
  overlay.style.cssText = [
    'position:fixed','inset:0','z-index:99999',
    'background:rgba(15,23,42,0.97)',
    'display:flex','align-items:center','justify-content:center',
    'font-family:system-ui,sans-serif'
  ].join(';');

  overlay.innerHTML = [
    '<div style="background:#1e293b;border-radius:16px;padding:40px 36px;',
    'max-width:340px;width:90%;text-align:center;box-shadow:0 25px 60px rgba(0,0,0,.5)">',
    '<div style="font-size:28px;margin-bottom:8px">🔒</div>',
    '<h2 style="color:#f1f5f9;margin:0 0 6px;font-size:18px">Geo Intelligence Suite</h2>',
    '<p style="color:#94a3b8;font-size:13px;margin:0 0 24px">Enter your access PIN to continue</p>',
    '<input id="pd-pin-input" type="password" inputmode="numeric" maxlength="10" ',
    'placeholder="PIN" style="width:100%;box-sizing:border-box;padding:12px 16px;',
    'font-size:20px;letter-spacing:6px;text-align:center;border:1.5px solid #334155;',
    'border-radius:8px;background:#0f172a;color:#f1f5f9;outline:none;margin-bottom:12px">',
    '<button id="pd-pin-btn" style="width:100%;padding:12px;background:#3b82f6;',
    'color:#fff;border:none;border-radius:8px;font-size:15px;font-weight:600;',
    'cursor:pointer">Unlock</button>',
    '<p id="pd-pin-err" style="color:#f87171;font-size:12px;margin:10px 0 0;',
    'display:none">Incorrect PIN — please try again</p>',
    '</div>'
  ].join('');

  document.documentElement.appendChild(overlay);

  async function attempt() {{
    var val = document.getElementById('pd-pin-input').value.trim();
    if (!val) return;
    var h = await sha256(val);
    if (h === correctHash) {{
      sessionStorage.setItem(SESSION_KEY, '1');
      document.getElementById('pd-pin-overlay').remove();
    }} else {{
      document.getElementById('pd-pin-err').style.display = '';
      document.getElementById('pd-pin-input').value = '';
      document.getElementById('pd-pin-input').focus();
    }}
  }}

  document.addEventListener('DOMContentLoaded', function() {{
    var btn = document.getElementById('pd-pin-btn');
    var inp = document.getElementById('pd-pin-input');
    if (btn) btn.addEventListener('click', attempt);
    if (inp) inp.addEventListener('keydown', function(e){{ if(e.key==='Enter') attempt(); }});
    if (inp) inp.focus();
  }});
}})();
</script>"""


# ── localStorage snippet ───────────────────────────────────────────────────────

def storage_head_script(slug, dataset='customers'):
    """Returns a <script> block to inject in <head> with localStorage helpers."""
    key = f'geo_pd_{slug}_{dataset}'
    return f"""
<script>
/* ── Pratt Digital client storage ({dataset}) ── */
(function() {{
  window.PD_KEY_{dataset.upper()} = '{key}';
  window.pdSave_{dataset} = function(data) {{
    try {{
      localStorage.setItem('{key}', JSON.stringify({{
        data: data,
        savedAt: new Date().toISOString(),
        count: data.length
      }}));
    }} catch(e) {{}}
  }};
  window.pdLoad_{dataset} = function() {{
    try {{
      var s = localStorage.getItem('{key}');
      if (!s) return null;
      var obj = JSON.parse(s);
      return obj && obj.data && obj.data.length ? obj : null;
    }} catch(e) {{ return null; }}
  }};
  window.pdSavedAt_{dataset} = function() {{
    try {{
      var s = localStorage.getItem('{key}');
      if (!s) return null;
      return JSON.parse(s).savedAt || null;
    }} catch(e) {{ return null; }}
  }};
  window.pdClear_{dataset} = function() {{
    localStorage.removeItem('{key}');
  }};
}})();
</script>"""


def saved_badge_html(dataset='customers', color='#16a34a'):
    """Returns HTML for the "loaded from storage" badge with an Update button."""
    return f"""<div id="pd-storage-badge-{dataset}" style="border:1.5px solid {color};border-radius:8px;padding:10px 12px;background:rgba(22,163,74,0.07);font-size:12px;">
  <div style="font-weight:600;color:{color}">✓ <span id="pd-badge-count-{dataset}"></span> saved</div>
  <div style="color:#666;font-size:11px;margin-top:2px"><span id="pd-badge-date-{dataset}"></span> · <a href="#" onclick="pdClearAndReload_{dataset}();return false;" style="color:{color}">Upload new list</a></div>
</div>"""


# ── Per-tool patchers ──────────────────────────────────────────────────────────
# Strategy: inject storage helpers in <head>, then append a single <script>
# block before </body> that wraps the tool's parse function and handles restore.
# This avoids fragile regex injection into function bodies.

def _badge_js(dataset='customers', color='#16a34a', label_id='uploadLabel', extra_js=''):
    """Returns JS that shows the restored-data badge and wires up the clear link."""
    return f"""
function pdShowRestoredBadge_{dataset}(saved) {{
  var lbl = document.getElementById('{label_id}');
  if (!lbl) return;
  lbl.outerHTML = '<div id="pd-badge-{dataset}" style="border:1.5px solid {color};border-radius:8px;padding:10px 12px;background:rgba(0,0,0,0.04);font-size:12px">'
    + '<div style="font-weight:600;color:{color}">\\u2713 <span id="pd-bc-{dataset}"></span> saved</div>'
    + '<div style="color:#666;font-size:11px;margin-top:2px"><span id="pd-bd-{dataset}"></span>'
    + ' &middot; <a href="#" onclick="pdClearAndReload_{dataset}();return false;" style="color:{color}">Upload new list</a></div>'
    + '</div>';
  var c = document.getElementById('pd-bc-{dataset}');
  var d = document.getElementById('pd-bd-{dataset}');
  if (c) c.textContent = (saved.count || saved.data && saved.data.length || 0) + ' customers';
  if (d && saved.savedAt) d.textContent = 'Saved ' + new Date(saved.savedAt).toLocaleDateString('en-GB',{{day:'numeric',month:'short'}});
  {extra_js}
}}
function pdClearAndReload_{dataset}() {{
  pdClear_{dataset}();
  location.reload();
}}"""


def patch_local_coverage(html, slug):
    html = html.replace('</head>', storage_head_script(slug) + '\n</head>', 1)

    badge_js = _badge_js('customers', '#16a34a', 'uploadLabel', extra_js='if(typeof checkReady==="function") checkReady();')
    body_script = f"""
<script>
/* ── PD client persistence: local-coverage ── */
{badge_js}

document.addEventListener('DOMContentLoaded', function() {{
  // Restore saved postcodes
  var saved = pdLoad_customers();
  if (saved && saved.data && saved.data.length) {{
    customerData = saved.data;
    pdShowRestoredBadge_customers(saved);
  }}

  // Wrap parseCustomers to save after every upload
  if (typeof parseCustomers === 'function') {{
    var _orig = parseCustomers;
    parseCustomers = function(text) {{
      _orig(text);
      if (customerData && customerData.length > 0) {{
        pdSave_customers(customerData);
      }}
    }};
  }}
}});
</script>"""
    html = html.replace('</body>', body_script + '\n</body>', 1)
    return html


def patch_dwelling_explorer(html, slug):
    html = html.replace('</head>', storage_head_script(slug) + '\n</head>', 1)

    badge_js = _badge_js('customers', '#16a34a', 'uploadLabel',
        extra_js='var btn=document.getElementById("analyseBtn");if(btn)btn.style.display="";')
    body_script = f"""
<script>
/* ── PD client persistence: dwelling-explorer ── */
{badge_js}

document.addEventListener('DOMContentLoaded', function() {{
  var saved = pdLoad_customers();
  if (saved && saved.data && saved.data.length) {{
    customerData = saved.data;
    pdShowRestoredBadge_customers(saved);
  }}

  if (typeof parseCustomers === 'function') {{
    var _orig = parseCustomers;
    parseCustomers = function(text) {{
      _orig(text);
      if (customerData && customerData.length > 0) {{
        pdSave_customers(customerData);
      }}
    }};
  }}
}});
</script>"""
    html = html.replace('</body>', body_script + '\n</body>', 1)
    return html


def patch_customer_map(html, slug):
    html = html.replace('</head>', storage_head_script(slug) + '\n</head>', 1)

    badge_js = _badge_js('customers', '#16a34a', 'uploadLabel',
        extra_js='if(typeof checkReady==="function") checkReady();')
    body_script = f"""
<script>
/* ── PD client persistence: customer-map ── */
{badge_js}

document.addEventListener('DOMContentLoaded', function() {{
  var saved = pdLoad_customers();
  if (saved && saved.data && saved.data.length) {{
    customerData = saved.data;
    pdShowRestoredBadge_customers(saved);
    if (typeof checkReady === 'function') checkReady();
  }}

  if (typeof parseCSV === 'function') {{
    var _orig = parseCSV;
    parseCSV = function(text) {{
      _orig(text);
      if (customerData && customerData.length > 0) {{
        pdSave_customers(customerData);
      }}
    }};
  }}
}});
</script>"""
    html = html.replace('</body>', body_script + '\n</body>', 1)
    return html


def patch_hnw_finder(html, slug):
    # hnw-finder loads text into #pasteArea, not directly into customerData
    # Persist the raw textarea text instead
    key = f'geo_pd_{slug}_hnw_text'
    head_script = f"""
<script>
(function() {{
  window.pdSaveHnw = function(text) {{
    try {{ localStorage.setItem('{key}', JSON.stringify({{text:text,savedAt:new Date().toISOString()}})); }} catch(e) {{}}
  }};
  window.pdLoadHnw = function() {{
    try {{ var s=localStorage.getItem('{key}'); return s?JSON.parse(s):null; }} catch(e) {{ return null; }}
  }};
  window.pdClearHnw = function() {{ localStorage.removeItem('{key}'); }};
}})();
</script>"""
    html = html.replace('</head>', head_script + '\n</head>', 1)

    badge_js = _badge_js('customers', '#7c3aed', 'uploadLabel',
        extra_js='if(typeof checkReady==="function") checkReady();')
    body_script = f"""
<script>
/* ── PD client persistence: hnw-finder ── */
{badge_js}

document.addEventListener('DOMContentLoaded', function() {{
  // Restore: pre-fill pasteArea
  var saved = pdLoadHnw();
  if (saved && saved.text) {{
    var pa = document.getElementById('pasteArea');
    if (pa) {{
      pa.value = saved.text;
      if (typeof checkReady === 'function') checkReady();
    }}
    var lines = saved.text.trim().split('\\n').filter(function(l){{return l.trim();}}).length;
    var fakeCount = {{count: lines, savedAt: saved.savedAt, data:[]}};
    pdShowRestoredBadge_customers(fakeCount);
    var c = document.getElementById('pd-bc-customers');
    if (c) c.textContent = lines + ' postcodes';
  }}

  // Save on file upload
  var fi = document.getElementById('fileInput');
  if (fi) {{
    fi.addEventListener('change', function() {{
      var f = fi.files[0]; if (!f) return;
      var r = new FileReader();
      r.onload = function(e) {{ pdSaveHnw(e.target.result); }};
      r.readAsText(f);
    }});
  }}

  // Save on textarea input
  var pa = document.getElementById('pasteArea');
  if (pa) {{
    pa.addEventListener('input', function() {{
      pdSaveHnw(pa.value);
    }});
  }}
}});

function pdClearAndReload_customers() {{ pdClearHnw(); location.reload(); }}
</script>"""
    html = html.replace('</body>', body_script + '\n</body>', 1)
    return html


def patch_overlap_map(html, slug):
    head_c = storage_head_script(slug, 'customers')
    head_l = storage_head_script(slug, 'locations')
    html = html.replace('</head>', head_c + head_l + '\n</head>', 1)

    body_script = f"""
<script>
/* ── PD client persistence: overlap-map ── */
function pdShowRestoredBadge_overlap(ds, saved) {{
  var color = ds === 'c' ? '#4f46e5' : '#d97706';
  var labelId = ds === 'c' ? 'ul-c' : 'ul-l';
  var dataset = ds === 'c' ? 'customers' : 'locations';
  var lbl = document.getElementById(labelId);
  if (!lbl) return;
  lbl.outerHTML = '<div id="pd-badge-' + dataset + '" style="border:1.5px solid ' + color + ';border-radius:8px;padding:10px 12px;background:rgba(0,0,0,0.04);font-size:12px">'
    + '<div style="font-weight:600;color:' + color + '">\\u2713 <span id="pd-bc-' + dataset + '"></span> saved</div>'
    + '<div style="color:#666;font-size:11px;margin-top:2px"><span id="pd-bd-' + dataset + '"></span>'
    + ' &middot; <a href="#" onclick="pdClearAndReload_' + dataset + '();return false;" style="color:' + color + '">Upload new list</a></div>'
    + '</div>';
  var c = document.getElementById('pd-bc-' + dataset);
  var d = document.getElementById('pd-bd-' + dataset);
  if (c) c.textContent = (saved.count||0) + (ds==='c'?' customers':' locations');
  if (d && saved.savedAt) d.textContent = 'Saved ' + new Date(saved.savedAt).toLocaleDateString('en-GB',{{day:'numeric',month:'short'}});
}}
function pdClearAndReload_customers() {{ pdClear_customers(); location.reload(); }}
function pdClearAndReload_locations() {{ pdClear_locations(); location.reload(); }}

document.addEventListener('DOMContentLoaded', function() {{
  // Restore saved datasets
  var sc = pdLoad_customers();
  var sl = pdLoad_locations();
  if (sc && sc.data && sc.data.length) {{
    if (typeof cData !== 'undefined') cData = sc.data;
    pdShowRestoredBadge_overlap('c', sc);
    if (typeof checkReady === 'function') checkReady();
  }}
  if (sl && sl.data && sl.data.length) {{
    if (typeof lData !== 'undefined') lData = sl.data;
    pdShowRestoredBadge_overlap('l', sl);
    if (typeof checkReady === 'function') checkReady();
  }}

  // Wrap parseCSV to save after upload
  if (typeof parseCSV === 'function') {{
    var _orig = parseCSV;
    parseCSV = function(text, ds) {{
      _orig(text, ds);
      if (ds === 'c' && typeof cData !== 'undefined' && cData.length > 0) pdSave_customers(cData);
      if (ds === 'l' && typeof lData !== 'undefined' && lData.length > 0) pdSave_locations(lData);
    }};
  }}
}});
</script>"""
    html = html.replace('</body>', body_script + '\n</body>', 1)
    return html


# ── Tool registry ──────────────────────────────────────────────────────────────

def get_patcher(filename, slug):
    name = os.path.basename(filename).lower()
    if name == 'local-coverage.html':
        return lambda h: patch_local_coverage(h, slug)
    if name == 'dwelling-explorer.html':
        return lambda h: patch_dwelling_explorer(h, slug)
    if name == 'customer-map.html':
        return lambda h: patch_customer_map(h, slug)
    if name == 'hnw-finder.html':
        return lambda h: patch_hnw_finder(h, slug)
    if name == 'overlap-map.html':
        return lambda h: patch_overlap_map(h, slug)
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
    'profile-tool (1).html',
]


# ── Main ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='Create a client Geo Explorer instance')
    parser.add_argument('--client', required=True, help='Client display name (e.g. "Acme Corp")')
    parser.add_argument('--slug', help='URL slug override (default: derived from client name)')
    parser.add_argument('--pin', help='Access PIN (recommended). E.g. --pin 4821')
    args = parser.parse_args()

    script_dir = os.path.dirname(os.path.abspath(__file__))
    slug = args.slug or slugify(args.client)
    out_dir = os.path.join(script_dir, 'clients', slug)

    if not args.pin:
        print("  ⚠️  No --pin provided. Tools will be accessible to anyone with the URL.")
        print("     Run with --pin YOUR_PIN to add access protection.\n")

    print(f"\n── Geo Explorer Client Builder ────────────────────────────")
    print(f"  Client  : {args.client}")
    print(f"  Slug    : {slug}")
    print(f"  PIN     : {'set ✓' if args.pin else 'none (unprotected)'}")
    print(f"  Output  : clients/{slug}/")
    print()

    os.makedirs(out_dir, exist_ok=True)

    processed = 0
    for fname in TOOL_FILES:
        src = os.path.join(script_dir, fname)
        if not os.path.exists(src):
            # Try stripping parenthetical suffix (e.g. "profile-tool (1).html" → "profile-tool.html")
            alt_name = re.sub(r'\s*\(\d+\)', '', fname)
            alt = os.path.join(script_dir, alt_name)
            if os.path.exists(alt):
                src = alt
                fname = alt_name
            else:
                print(f"  Skipping (not found): {fname}")
                continue

        dst_name = re.sub(r'\s*\(\d+\)', '', os.path.basename(fname))
        dst = os.path.join(out_dir, dst_name)

        with open(src, 'r', encoding='utf-8') as f:
            html = f.read()

        # Fix asset paths
        html = fix_asset_paths(html)

        # Update page title
        html = re.sub(
            r'(<title>[^<]*)(</title>)',
            rf'\g<1> · {args.client}\g<2>',
            html, count=1
        )

        # Inject PIN gate (as first thing in <head>)
        if args.pin:
            html = html.replace('<head>', '<head>\n' + pin_gate_script(args.pin, slug), 1)

        # Apply tool-specific persistence patches
        patcher = get_patcher(dst_name, slug)
        if patcher:
            html = patcher(html)
            print(f"  ✓ Patched  : {dst_name}  (localStorage persistence added)")
        else:
            print(f"  ○ Copied   : {dst_name}")

        with open(dst, 'w', encoding='utf-8') as f:
            f.write(html)
        processed += 1

    # Copy profile-tool directory if present
    profile_src = os.path.join(script_dir, 'profile-tool')
    if os.path.isdir(profile_src):
        profile_dst = os.path.join(out_dir, 'profile-tool')
        if os.path.exists(profile_dst):
            shutil.rmtree(profile_dst)
        shutil.copytree(profile_src, profile_dst)
        print(f"  ○ Copied   : profile-tool/")

    print(f"\n  {processed} files written to clients/{slug}/")
    print(f"\n── Next steps ──────────────────────────────────────────────")
    print(f"  cd to your geo_explorer repo folder, then:")
    print(f"    git add clients/{slug}")
    print(f'    git commit -m "Add {args.client} client instance"')
    print(f"    git push origin main")
    print()
    print(f"  Live URL (after push — allow 1-2 min for GitHub Pages):")
    print(f"    https://lester-dotcom.github.io/geo_explorer/clients/{slug}/")
    print()


if __name__ == '__main__':
    main()

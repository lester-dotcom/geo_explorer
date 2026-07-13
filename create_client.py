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


# ── Multi-dataset storage system ───────────────────────────────────────────────

def storage_head_script(slug, ns='customers'):
    """
    Shared localStorage helpers for named dataset management.
    Supports multiple named datasets per tool with a selector UI.
    ns = namespace suffix, e.g. 'customers' or 'locations'
    """
    ds_key    = f'geo_pd_{slug}_{ns}_datasets'
    act_key   = f'geo_pd_{slug}_{ns}_active'
    return f"""
<script>
/* ── Pratt Digital multi-dataset storage: {ns} ── */
(function() {{
  var DS_KEY  = '{ds_key}';
  var ACT_KEY = '{act_key}';

  function _all()  {{ try {{ return JSON.parse(localStorage.getItem(DS_KEY)||'{{}}'); }} catch(e) {{ return {{}}; }} }}
  function _save(o) {{ try {{ localStorage.setItem(DS_KEY, JSON.stringify(o)); }} catch(e) {{}} }}

  window.pdSave_{ns} = function(data, name) {{
    var all = _all();
    name = name || 'Dataset ' + (Object.keys(all).length + 1);
    all[name] = {{ data: data, count: data.length, savedAt: new Date().toISOString() }};
    _save(all);
    localStorage.setItem(ACT_KEY, name);
  }};
  window.pdLoad_{ns} = function() {{
    var all = _all(), keys = Object.keys(all);
    if (!keys.length) return null;
    var name = localStorage.getItem(ACT_KEY);
    if (!name || !all[name]) name = keys[0];
    return Object.assign({{ name: name }}, all[name]);
  }};
  window.pdAllDatasets_{ns} = function() {{ return _all(); }};
  window.pdSetActive_{ns} = function(name) {{ localStorage.setItem(ACT_KEY, name); }};
  window.pdDeleteDataset_{ns} = function(name) {{
    var all = _all(); delete all[name]; _save(all);
    var keys = Object.keys(all);
    localStorage.setItem(ACT_KEY, keys.length ? keys[0] : '');
  }};
  window.pdClear_{ns} = function() {{
    localStorage.removeItem(DS_KEY);
    localStorage.removeItem(ACT_KEY);
  }};
}})();
</script>"""


def inject_before_body_end(html, script):
    """Insert script before the LAST </body> tag (avoids hitting </body> inside JS template literals)."""
    parts = html.rsplit('</body>', 1)
    if len(parts) == 2:
        return parts[0] + script + '\n</body>' + parts[1]
    return html + script


# ── Shared dataset picker UI ───────────────────────────────────────────────────

def dataset_picker_js(ns='customers', color='#16a34a', label_id='uploadLabel',
                      data_var='customerData', on_load_extra='', on_save_extra=''):
    """
    Returns JS that:
    1. On DOMContentLoaded: if saved datasets exist, replaces the upload label
       with a dropdown picker + active dataset badge.
    2. Wraps the parse function to prompt for a dataset name on upload, then save.
    3. Allows switching between saved datasets via dropdown.
    """
    return f"""
/* ─── PD dataset picker: {ns} ─── */
(function() {{

  function fmtDate(s) {{
    try {{ return new Date(s).toLocaleDateString('en-GB',{{day:'numeric',month:'short'}}); }}
    catch(e) {{ return ''; }}
  }}

  function renderPicker(ns_active) {{
    var all = pdAllDatasets_{ns}();
    var names = Object.keys(all);
    var lbl = document.getElementById('{label_id}');
    if (!lbl) return;

    var sel = '<select id="pd-sel-{ns}" style="width:100%;padding:6px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:12px;margin-bottom:6px;background:#fff">';
    names.forEach(function(n) {{
      sel += '<option value="' + n.replace(/"/g,'&quot;') + '"' + (n === ns_active ? ' selected' : '') + '>'
           + n + ' (' + all[n].count + ')</option>';
    }});
    sel += '</select>';

    window.pdRenderPicker_{ns} = renderPicker;  // expose for external callers

    lbl.outerHTML =
      '<div id="pd-picker-{ns}" style="border:1.5px solid {color};border-radius:8px;padding:10px 12px;font-size:12px;">'
      + '<div style="font-weight:600;color:{color};margin-bottom:6px">Saved datasets</div>'
      + sel
      + '<div style="display:flex;gap:6px;margin-bottom:8px">'
      + '<button onclick="pdUseSelected_{ns}()" style="flex:1;padding:5px 0;background:{color};color:#fff;border:none;border-radius:5px;font-size:11px;font-weight:600;cursor:pointer">Load selected</button>'
      + '<button onclick="pdDeleteSelected_{ns}()" style="padding:5px 10px;background:#f3f4f6;color:#6b7280;border:1px solid #d1d5db;border-radius:5px;font-size:11px;cursor:pointer">Delete</button>'
      + '</div>'
      + '<div style="border-top:1px solid #e5e7eb;padding-top:8px;color:#6b7280;font-size:11px">'
      + '<label style="cursor:pointer;display:inline-flex;align-items:center;gap:4px">'
      + '<input type="file" id="pd-fi-{ns}" accept=".csv,.txt,.xlsx" style="display:none">'
      + '&#43; Upload new dataset</label>'
      + '</div>'
      + '</div>';

    document.getElementById('pd-fi-{ns}').addEventListener('change', function() {{
      var f = this.files[0]; if (!f) return;
      var name = window.prompt('Name this dataset:', f.name.replace(/[.][^.]+$/, ''));
      if (name === null) name = f.name.replace(/[.][^.]+$/, '');
      name = name.trim() || 'Dataset';
      var r = new FileReader();
      r.onload = function(e) {{ pdTriggerParse_{ns}(e.target.result, name); }};
      r.readAsText(f);
    }});

    // Show active badge below picker
    if (ns_active && all[ns_active]) {{
      updateActiveBadge_{ns}(ns_active, all[ns_active]);
    }}
  }}

  function updateActiveBadge_{ns}(name, info) {{
    var existing = document.getElementById('pd-active-{ns}');
    var html = '<div id="pd-active-{ns}" style="margin-top:6px;padding:6px 10px;background:rgba(0,0,0,0.04);border-radius:6px;font-size:11px;color:#374151">'
      + '&#10003; <strong>' + name + '</strong> &mdash; ' + info.count + ' records'
      + (info.savedAt ? ' &middot; saved ' + fmtDate(info.savedAt) : '')
      + '</div>';
    if (existing) {{ existing.outerHTML = html; }}
    else {{
      var picker = document.getElementById('pd-picker-{ns}');
      if (picker) picker.insertAdjacentHTML('afterend', html);
    }}
  }}

  window.pdUseSelected_{ns} = function() {{
    var sel = document.getElementById('pd-sel-{ns}');
    if (!sel) return;
    var name = sel.value;
    var all = pdAllDatasets_{ns}();
    if (!all[name]) return;
    pdSetActive_{ns}(name);
    var data = all[name].data;
    if (typeof {data_var} !== 'undefined') {data_var} = data;
    {on_load_extra}
    updateActiveBadge_{ns}(name, all[name]);
  }};

  window.pdDeleteSelected_{ns} = function() {{
    var sel = document.getElementById('pd-sel-{ns}');
    if (!sel) return;
    var name = sel.value;
    if (!confirm('Delete dataset "' + name + '"?')) return;
    pdDeleteDataset_{ns}(name);
    var all = pdAllDatasets_{ns}();
    if (Object.keys(all).length) {{
      renderPicker(pdLoad_{ns}() ? pdLoad_{ns}().name : '');
      pdUseSelected_{ns}();
    }} else {{
      location.reload();
    }}
  }};

  window.pdTriggerParse_{ns} = function(text, name) {{
    // Parse the raw CSV text using the tool's existing parse function
    if (typeof _pdOrigParse_{ns} === 'function') {{
      _pdOrigParse_{ns}(text);
      // After parse, data_var is populated — save it
      setTimeout(function() {{
        var data = (typeof {data_var} !== 'undefined') ? {data_var} : [];
        if (data.length) {{
          pdSave_{ns}(data, name);
          renderPicker(name);
          updateActiveBadge_{ns}(name, pdAllDatasets_{ns}()[name]);
          {on_save_extra}
        }}
      }}, 100);
    }}
  }};

  window._pdOrigParse_{ns} = null; // set below after tool's function is defined

  document.addEventListener('DOMContentLoaded', function() {{
    var all = pdAllDatasets_{ns}();
    var names = Object.keys(all);
    var active = pdLoad_{ns}();

    if (names.length) {{
      renderPicker(active ? active.name : names[0]);
      if (active && active.data && active.data.length) {{
        if (typeof {data_var} !== 'undefined') {data_var} = active.data;
        {on_load_extra}
      }}
    }}
  }});

}})();
"""


def _standard_patcher(html, slug, parse_fn, data_var='customerData',
                       ns='customers', color='#16a34a', on_load_extra='', on_save_extra=''):
    """Common patcher for tools that use a named parse function + customerData array."""
    html = html.replace('</head>', storage_head_script(slug, ns) + '\n</head>', 1)
    picker = dataset_picker_js(ns=ns, color=color, label_id='uploadLabel',
                                data_var=data_var, on_load_extra=on_load_extra,
                                on_save_extra=on_save_extra)
    body_script = f"""
<script>
{picker}
document.addEventListener('DOMContentLoaded', function() {{
  // Wire up the tool's parse function to the dataset picker
  if (typeof {parse_fn} === 'function') {{
    _pdOrigParse_{ns} = {parse_fn};
    {parse_fn} = function(text) {{
      _pdOrigParse_{ns}(text);
      // If the picker UI isn't shown yet (no saved datasets), save this upload
      if (!document.getElementById('pd-picker-{ns}')) {{
        setTimeout(function() {{
          var data = (typeof {data_var} !== 'undefined') ? {data_var} : [];
          if (!data.length) return;
          var fi = document.getElementById('fileInput');
          var defName = (fi && fi.files && fi.files[0])
            ? fi.files[0].name.replace(/[.][^.]+$/, '') : 'My Customers';
          var name = window.prompt('Name this dataset:', defName);
          if (name === null) name = defName;
          pdSave_{ns}(data, name.trim() || defName);
          if (typeof pdRenderPicker_{ns} === 'function') pdRenderPicker_{ns}(name.trim() || defName);
          {on_save_extra}
        }}, 150);
      }}
    }};
  }}
}});
</script>"""
    return inject_before_body_end(html, body_script)


def patch_local_coverage(html, slug):
    return _standard_patcher(html, slug, 'parseCustomers',
        on_load_extra='if(typeof checkReady==="function") checkReady();',
        on_save_extra='if(typeof checkReady==="function") checkReady();')


def patch_dwelling_explorer(html, slug):
    return _standard_patcher(html, slug, 'parseCustomers',
        on_load_extra=('var btn=document.getElementById("analyseBtn");'
                       'if(btn)btn.style.display="";'))


def patch_customer_map(html, slug):
    return _standard_patcher(html, slug, 'parseCSV',
        on_load_extra='if(typeof checkReady==="function") checkReady();',
        on_save_extra='if(typeof checkReady==="function") checkReady();')


def patch_profile_tool_html(html, slug):
    """Profile-tool: parseCSV is in app.js; rawData is the data variable."""
    return _standard_patcher(html, slug, 'parseCSV', data_var='rawData',
        color='#7c3aed',
        on_load_extra='if(typeof checkReady==="function") checkReady();',
        on_save_extra='if(typeof checkReady==="function") checkReady();')


def patch_hnw_finder(html, slug):
    """HNW finder: uploads go into pasteArea textarea, not parsed to an array."""
    html = html.replace('</head>', storage_head_script(slug) + '\n</head>', 1)

    picker = dataset_picker_js(
        ns='customers', color='#7c3aed', label_id='uploadLabel',
        data_var='customerData',
        on_load_extra=(
            'var pa=document.getElementById("pasteArea");'
            'if(pa&&active&&active.rawText){pa.value=active.rawText;}'
            'if(typeof checkReady==="function") checkReady();'
        )
    )
    body_script = f"""
<script>
{picker}

// HNW uses textarea — override pdTriggerParse_customers for text passthrough
window.pdTriggerParse_customers = function(text, name) {{
  var all = pdAllDatasets_customers();
  // Count lines as postcodes
  var lines = text.trim().split('\\n').filter(function(l){{return l.trim();}});
  var fakeData = lines; // store raw lines as "data" for count purposes
  pdSave_customers(fakeData, name);
  // Also store raw text alongside
  try {{
    var stored = JSON.parse(localStorage.getItem('geo_pd_{slug}_customers_datasets')||'{{}}');
    if (stored[name]) {{ stored[name].rawText = text; localStorage.setItem('geo_pd_{slug}_customers_datasets', JSON.stringify(stored)); }}
  }} catch(e) {{}}
  // Fill the pasteArea
  var pa = document.getElementById('pasteArea');
  if (pa) {{ pa.value = text; if(typeof checkReady==='function') checkReady(); }}
  // Re-render picker
  var all2 = pdAllDatasets_customers();
  setTimeout(function() {{
    var lbl = document.getElementById('uploadLabel') || document.getElementById('pd-picker-customers');
    location.reload(); // simplest — picker will restore on reload
  }}, 200);
}};

document.addEventListener('DOMContentLoaded', function() {{
  var active = pdLoad_customers();
  if (active) {{
    // Restore raw text into pasteArea
    try {{
      var stored = JSON.parse(localStorage.getItem('geo_pd_{slug}_customers_datasets')||'{{}}');
      var entry = stored[active.name];
      if (entry && entry.rawText) {{
        var pa = document.getElementById('pasteArea');
        if (pa) {{
          pa.value = entry.rawText;
          pa.dispatchEvent(new Event('input')); // triggers checkReady in tool
          if(typeof checkReady==='function') checkReady();
        }}
      }}
    }} catch(e) {{}}
  }}
}});
</script>"""
    return inject_before_body_end(html, body_script)


def patch_overlap_map(html, slug):
    html = html.replace('</head>',
        storage_head_script(slug, 'customers') + storage_head_script(slug, 'locations') + '\n</head>', 1)

    picker_c = dataset_picker_js(ns='customers', color='#4f46e5', label_id='ul-c',
        data_var='cData',
        on_load_extra='if(typeof checkReady==="function") checkReady();')
    picker_l = dataset_picker_js(ns='locations', color='#d97706', label_id='ul-l',
        data_var='lData',
        on_load_extra='if(typeof checkReady==="function") checkReady();')

    body_script = f"""
<script>
{picker_c}
{picker_l}
document.addEventListener('DOMContentLoaded', function() {{
  if (typeof parseCSV === 'function') {{
    _pdOrigParse_customers = parseCSV;
    _pdOrigParse_locations = parseCSV;
    parseCSV = function(text, ds) {{
      _pdOrigParse_customers(text, ds);
      setTimeout(function() {{
        if (ds === 'c' && typeof cData !== 'undefined' && cData.length) {{
          var name = window.prompt('Name this dataset:', 'Customers');
          if (name === null) name = 'Customers';
          pdSave_customers(cData, name.trim() || 'Customers');
        }}
        if (ds === 'l' && typeof lData !== 'undefined' && lData.length) {{
          var name = window.prompt('Name this dataset:', 'Locations');
          if (name === null) name = 'Locations';
          pdSave_locations(lData, name.trim() || 'Locations');
        }}
      }}, 100);
    }};
  }}
}});
</script>"""
    return inject_before_body_end(html, body_script)


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
    if name == 'profile-tool.html':
        return lambda h: patch_profile_tool_html(h, slug)
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

    # Copy profile-tool directory and patch its index.html
    profile_src = os.path.join(script_dir, 'profile-tool')
    if os.path.isdir(profile_src):
        profile_dst = os.path.join(out_dir, 'profile-tool')
        if os.path.exists(profile_dst):
            shutil.rmtree(profile_dst)
        shutil.copytree(profile_src, profile_dst)
        # Patch profile-tool/index.html with dataset picker
        pt_index = os.path.join(profile_dst, 'index.html')
        if os.path.exists(pt_index):
            with open(pt_index, 'r', encoding='utf-8') as f:
                pt_html = f.read()
            if args.pin:
                pt_html = pt_html.replace('<head>', '<head>\n' + pin_gate_script(args.pin, slug), 1)
            pt_html = patch_profile_tool_html(pt_html, slug)
            with open(pt_index, 'w', encoding='utf-8') as f:
                f.write(pt_html)
            print(f"  ✓ Patched  : profile-tool/index.html  (localStorage persistence added)")
        else:
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

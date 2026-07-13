#!/usr/bin/env python3
"""
create_client.py — Create a client-specific version of the Geo Intelligence Suite.

Duplicates all tools to  clients/<slug>/  with localStorage persistence:
  - Postcodes uploaded in any tool (or the launcher) are saved in the browser
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
    """
    session_key = f'geo_pd_{slug}_auth'
    return f"""
<script>
/* ── Pratt Digital PIN gate ── */
(function() {{
  var SESSION_KEY = '{session_key}';

  async function sha256(str) {{
    var buf = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(str));
    return Array.from(new Uint8Array(buf)).map(function(b){{return b.toString(16).padStart(2,'0');}}).join('');
  }}

  var correctHash = null;
  sha256('{pin}').then(function(h) {{ correctHash = h; }});

  if (sessionStorage.getItem(SESSION_KEY) === '1') return;

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
    Supports multiple named datasets per namespace with a selector UI.
    """
    ds_key  = f'geo_pd_{slug}_{ns}_datasets'
    act_key = f'geo_pd_{slug}_{ns}_active'
    return f"""
<script>
/* ── Pratt Digital multi-dataset storage: {ns} ── */
(function() {{
  var DS_KEY  = '{ds_key}';
  var ACT_KEY = '{act_key}';

  function _all()   {{ try {{ return JSON.parse(localStorage.getItem(DS_KEY)||'{{}}'); }} catch(e) {{ return {{}}; }} }}
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
  window.pdSetActive_{ns}   = function(name) {{ localStorage.setItem(ACT_KEY, name); }};
  window.pdDeleteDataset_{ns} = function(name) {{
    var all = _all(); delete all[name]; _save(all);
    var keys = Object.keys(all);
    localStorage.setItem(ACT_KEY, keys.length ? keys[0] : '');
  }};
  window.pdClear_{ns} = function() {{
    localStorage.removeItem(DS_KEY);
    localStorage.removeItem(ACT_KEY);
  }};
  window.pdRawGet_{ns} = function(name) {{
    var all = _all();
    return all[name] || null;
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
       with a dropdown picker.
    2. pdTriggerParse_{ns}(text, name) calls the original parse fn then saves.
    3. pdUseSelected_{ns}() loads the selected dataset into the tool.

    BUG FIXES vs prior version:
    - renderPicker now finds the element by label_id OR by the picker div itself,
      so re-renders after the first upload work correctly.
    - window.pdRenderPicker_{ns} is set at IIFE scope so it's always available,
      even on first upload when renderPicker hasn't been called yet at load time.
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
    // Find the element to replace: either the original label or the existing picker div
    var lbl = document.getElementById('{label_id}') || document.getElementById('pd-picker-{ns}');
    if (!lbl) return;

    var sel = '<select id="pd-sel-{ns}" style="width:100%;padding:6px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:12px;margin-bottom:6px;background:#fff">';
    names.forEach(function(n) {{
      sel += '<option value="' + n.replace(/"/g,'&quot;') + '"' + (n === ns_active ? ' selected' : '') + '>'
           + n + ' (' + all[n].count + ')</option>';
    }});
    sel += '</select>';

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
      var name = f.name.replace(/[.][^.]+$/, '') || 'Dataset';
      var r = new FileReader();
      r.onload = function(e) {{ pdTriggerParse_{ns}(e.target.result, name); }};
      r.readAsText(f);
    }});

    if (ns_active && all[ns_active]) {{
      updateActiveBadge_{ns}(ns_active, all[ns_active]);
    }}
  }}

  // Expose globally at IIFE scope so it's always set, even before first call
  window.pdRenderPicker_{ns} = renderPicker;

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
    if (typeof _pdOrigParse_{ns} === 'function') {{
      _pdOrigParse_{ns}(text);
      setTimeout(function() {{
        var data = (typeof {data_var} !== 'undefined') ? {data_var} : [];
        if (data.length) {{
          pdSave_{ns}(data, name);
          renderPicker(name);
          updateActiveBadge_{ns}(name, pdAllDatasets_{ns}()[name]);
          {on_save_extra}
        }}
      }}, 150);
    }}
  }};

  window._pdOrigParse_{ns} = null;

  document.addEventListener('DOMContentLoaded', function() {{
    var all = pdAllDatasets_{ns}();
    var names = Object.keys(all);
    var active = pdLoad_{ns}();

    if (names.length) {{
      renderPicker(active ? active.name : names[0]);
      if (active && active.data && active.data.length) {{
        if (typeof {data_var} !== 'undefined') {data_var} = active.data;
        var name = active.name; // make `name` available to on_load_extra (same scope as pdUseSelected)
        {on_load_extra}
      }}
    }}
  }});

}})();
"""


def _standard_patcher(html, slug, parse_fn, data_var='customerData',
                       ns='customers', color='#16a34a', on_load_extra='', on_save_extra=''):
    """Common patcher for tools that use a named parse function + data array."""
    html = html.replace('</head>', storage_head_script(slug, ns) + '\n</head>', 1)
    picker = dataset_picker_js(ns=ns, color=color, label_id='uploadLabel',
                                data_var=data_var, on_load_extra=on_load_extra,
                                on_save_extra=on_save_extra)
    body_script = f"""
<script>
{picker}
document.addEventListener('DOMContentLoaded', function() {{
  if (typeof {parse_fn} === 'function') {{
    _pdOrigParse_{ns} = {parse_fn};
    {parse_fn} = function(text) {{
      _pdOrigParse_{ns}(text);
      // If picker isn't shown yet (first upload), save and render
      if (!document.getElementById('pd-picker-{ns}')) {{
        setTimeout(function() {{
          var data = (typeof {data_var} !== 'undefined') ? {data_var} : [];
          if (!data.length) return;
          var fi = document.getElementById('fileInput');
          var name = (fi && fi.files && fi.files[0])
            ? fi.files[0].name.replace(/[.][^.]+$/, '') : 'Customers';
          pdSave_{ns}(data, name);
          if (typeof pdRenderPicker_{ns} === 'function') pdRenderPicker_{ns}(name);
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
    # customer-map stores {postcode: "XX1 1AA"} objects, but the launcher stores plain strings.
    # Normalize whatever is in storage to {postcode:...} objects before assigning, so
    # runAnalysis() can safely call c.postcode on every entry.
    on_load_extra = (
        'if(all&&all[name]&&all[name].data&&all[name].data.length){'
        '  customerData=all[name].data.map(function(c){'
        '    return(c&&typeof c==="object"&&c.postcode)?c:{postcode:String(c)};'
        '  });'
        '}'
        'if(typeof checkReady==="function") checkReady();'
    )
    return _standard_patcher(html, slug, 'parseCSV',
        on_load_extra=on_load_extra,
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

    # on_load_extra runs in both pdUseSelected_customers and DOMContentLoaded.
    # Both have `name` and `all` in scope (DOMContentLoaded now defines `var name = active.name`).
    # If rawText is absent (e.g. uploaded via the launcher data manager), fall back to
    # reconstructing the text from the stored postcodes array (one per line) — HNW's
    # parseInput() handles that format just fine.
    on_load_extra = (
        'var _pa=document.getElementById("pasteArea");'
        'var _entry=all[name];'
        'if(_pa&&_entry){'
        '  var _raw=_entry.rawText||(_entry.data&&_entry.data.length?_entry.data.join("\\n"):"");'
        '  if(_raw){_pa.value=_raw;_pa.dispatchEvent(new Event("input"));}'
        '}'
        'if(typeof checkReady==="function") checkReady();'
    )

    picker = dataset_picker_js(
        ns='customers', color='#7c3aed', label_id='uploadLabel',
        data_var='customerData',
        on_load_extra=on_load_extra
    )

    ds_key = f'geo_pd_{slug}_customers_datasets'

    body_script = f"""
<script>
{picker}

// HNW uses textarea — override pdTriggerParse_customers for text passthrough
window.pdTriggerParse_customers = function(text, name) {{
  // Count lines as proxy for record count
  var lines = text.trim().split('\\n').filter(function(l){{return l.trim();}});
  pdSave_customers(lines, name);
  // Persist raw text alongside the record array
  try {{
    var stored = JSON.parse(localStorage.getItem('{ds_key}')||'{{}}');
    if (stored[name]) {{
      stored[name].rawText = text;
      localStorage.setItem('{ds_key}', JSON.stringify(stored));
    }}
  }} catch(e) {{}}
  // Fill pasteArea so the tool can plot immediately
  var pa = document.getElementById('pasteArea');
  if (pa) {{
    pa.value = text;
    pa.dispatchEvent(new Event('input'));
    if (typeof checkReady === 'function') checkReady();
  }}
  // Re-render picker with the new dataset
  if (typeof pdRenderPicker_customers === 'function') pdRenderPicker_customers(name);
  var all2 = pdAllDatasets_customers();
  if (all2[name]) {{
    var existing = document.getElementById('pd-active-customers');
    var info = all2[name];
    var badge = '<div id="pd-active-customers" style="margin-top:6px;padding:6px 10px;background:rgba(0,0,0,0.04);border-radius:6px;font-size:11px;color:#374151">'
      + '&#10003; <strong>' + name + '</strong> &mdash; ' + info.count + ' records</div>';
    if (existing) existing.outerHTML = badge;
    else {{
      var pickerEl = document.getElementById('pd-picker-customers');
      if (pickerEl) pickerEl.insertAdjacentHTML('afterend', badge);
    }}
  }}
}};

document.addEventListener('DOMContentLoaded', function() {{
  // Intercept original fileInput (shown before picker exists) so first upload is persisted
  var origFi = document.getElementById('fileInput');
  if (origFi) {{
    origFi.addEventListener('change', function() {{
      if (!this.files[0]) return;
      var fname = this.files[0].name.replace(/[.][^.]+$/, '') || 'Customers';
      // Wait for original handler to fill pasteArea, then save
      setTimeout(function() {{
        var pa = document.getElementById('pasteArea');
        var text = pa ? pa.value : '';
        if (text) pdTriggerParse_customers(text, fname);
      }}, 200);
    }});
  }}

  // Restore content into pasteArea on page load
  var active = pdLoad_customers();
  if (active) {{
    try {{
      var stored = JSON.parse(localStorage.getItem('{ds_key}')||'{{}}');
      var entry = stored[active.name];
      if (entry) {{
        var rawText = entry.rawText || (entry.data && entry.data.length ? entry.data.join('\\n') : '');
        var pa = document.getElementById('pasteArea');
        if (pa && rawText) {{
          pa.value = rawText;
          pa.dispatchEvent(new Event('input'));
          if (typeof checkReady === 'function') checkReady();
        }}
      }}
    }} catch(e) {{}}
  }}
}});
</script>"""
    return inject_before_body_end(html, body_script)


def patch_overlap_map(html, slug):
    """Overlap map: two namespaces (customers = cData, locations = lData)."""
    html = html.replace('</head>',
        storage_head_script(slug, 'customers') + storage_head_script(slug, 'locations') + '\n</head>', 1)

    # on_load_extra runs in both DOMContentLoaded (name=active.name) and pdUseSelected.
    # Normalize to {postcode,name} objects in case data was saved as plain strings by the launcher.
    _norm_c = (
        'if(all&&all[name]&&all[name].data&&all[name].data.length){'
        '  cData=all[name].data.map(function(c){'
        '    return(c&&typeof c==="object"&&c.postcode)?c:{postcode:String(c),name:null};'
        '  });'
        '}'
        'if(typeof checkReady==="function") checkReady();'
    )
    _norm_l = (
        'if(all&&all[name]&&all[name].data&&all[name].data.length){'
        '  lData=all[name].data.map(function(c){'
        '    return(c&&typeof c==="object"&&c.postcode)?c:{postcode:String(c),name:null};'
        '  });'
        '}'
        'if(typeof checkReady==="function") checkReady();'
    )
    picker_c = dataset_picker_js(
        ns='customers', color='#4f46e5', label_id='ul-c',
        data_var='cData',
        on_load_extra=_norm_c
    )
    picker_l = dataset_picker_js(
        ns='locations', color='#d97706', label_id='ul-l',
        data_var='lData',
        on_load_extra=_norm_l
    )

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
          // Use the actual filename from the fi-c file input
          var fi = document.getElementById('fi-c');
          var name = (fi && fi.files && fi.files[0])
            ? fi.files[0].name.replace(/[.][^.]+$/, '') : 'Customers';
          pdSave_customers(cData, name);
          if (typeof pdRenderPicker_customers === 'function') pdRenderPicker_customers(name);
          if (typeof checkReady === 'function') checkReady();
        }}
        if (ds === 'l' && typeof lData !== 'undefined' && lData.length) {{
          var fi2 = document.getElementById('fi-l');
          var name2 = (fi2 && fi2.files && fi2.files[0])
            ? fi2.files[0].name.replace(/[.][^.]+$/, '') : 'Locations';
          pdSave_locations(lData, name2);
          if (typeof pdRenderPicker_locations === 'function') pdRenderPicker_locations(name2);
          if (typeof checkReady === 'function') checkReady();
        }}
      }}, 150);
    }};
  }}
}});
</script>"""
    return inject_before_body_end(html, body_script)


# ── Launcher data manager ──────────────────────────────────────────────────────

def patch_launcher(html, slug):
    """
    Injects a 'Your Data' management card into the launcher index.html.
    Users upload CSV files here; datasets are saved to localStorage and
    immediately available in every tool without re-uploading.
    """
    ds_key_c  = f'geo_pd_{slug}_customers_datasets'
    act_key_c = f'geo_pd_{slug}_customers_active'

    launcher_script = f"""
<script>
/* ── Pratt Digital Launcher Data Manager ── */
(function() {{
  var DS_KEY  = '{ds_key_c}';
  var ACT_KEY = '{act_key_c}';

  function _all()   {{ try {{ return JSON.parse(localStorage.getItem(DS_KEY)||'{{}}'); }} catch(e) {{ return {{}}; }} }}
  function _save(o) {{ try {{ localStorage.setItem(DS_KEY, JSON.stringify(o)); }} catch(e) {{}} }}

  function fmtDate(s) {{
    try {{ return new Date(s).toLocaleDateString('en-GB',{{day:'numeric',month:'short',year:'numeric'}}); }}
    catch(e) {{ return ''; }}
  }}

  // Normalise a potential postcode string (same logic as the tools)
  function normalisePC(s) {{
    if (!s) return null;
    var c = s.replace(/\\s+/g,'').toUpperCase();
    if (c.length >= 5 && c.length <= 7) return c.slice(0,-3) + ' ' + c.slice(-3);
    return null;
  }}

  // Extract postcodes from raw CSV/text, matching the tools' parseCustomers behaviour:
  // one postcode per row, duplicates kept (multiple customers at same postcode = real data),
  // header row skipped, normalisePC fallback on last/first cell.
  function extractPostcodes(text) {{
    var lines = text.replace(/\\r/g,'').trim().split('\\n').filter(function(l){{return l.trim();}});
    var results = [];
    var isHdr = function(l) {{ return /postcode|address|name|customer|email/i.test(l); }};
    var start = lines.length && isHdr(lines[0]) ? 1 : 0;
    for (var i = start; i < lines.length; i++) {{
      var parts = lines[i].split(',').map(function(s){{return s.trim().replace(/^["']|["']$/g,'');}});
      var pc = null;
      for (var j = 0; j < parts.length; j++) {{
        var m = parts[j].match(/([A-Z]{{1,2}}\\d{{1,2}}[A-Z]?\\s*\\d[A-Z]{{2}})/i);
        if (m) {{ pc = normalisePC(m[1]); if (pc) break; }}
      }}
      if (!pc) pc = normalisePC(parts[parts.length-1]) || normalisePC(parts[0]);
      if (pc) results.push(pc);
    }}
    return results;
  }}

  function renderManager() {{
    var all = _all();
    var names = Object.keys(all);
    var active = localStorage.getItem(ACT_KEY) || (names[0] || '');
    var mgr = document.getElementById('pd-data-manager');
    if (!mgr) return;

    var listHtml = '';
    if (names.length === 0) {{
      listHtml = '<p style="color:#6b7280;font-size:13px;margin:0 0 12px">No datasets saved yet. Upload a file below.</p>';
    }} else {{
      listHtml = '<table style="width:100%;border-collapse:collapse;font-size:13px;margin-bottom:12px">'
        + '<thead><tr style="border-bottom:1px solid #e5e7eb">'
        + '<th style="text-align:left;padding:4px 8px;color:#6b7280;font-weight:500">Name</th>'
        + '<th style="text-align:right;padding:4px 8px;color:#6b7280;font-weight:500">Records</th>'
        + '<th style="text-align:right;padding:4px 8px;color:#6b7280;font-weight:500">Saved</th>'
        + '<th style="padding:4px 8px"></th>'
        + '</tr></thead><tbody>';
      names.forEach(function(n) {{
        var info = all[n];
        var isActive = (n === active);
        listHtml += '<tr style="border-bottom:1px solid #f3f4f6' + (isActive?' background:#f0fdf4':'') + '">'
          + '<td style="padding:6px 8px;font-weight:' + (isActive?'600':'400') + ';color:#1f2937">'
          + (isActive ? '&#10003; ' : '') + n.replace(/</g,'&lt;') + '</td>'
          + '<td style="padding:6px 8px;text-align:right;color:#374151">' + (info.count||0).toLocaleString() + '</td>'
          + '<td style="padding:6px 8px;text-align:right;color:#6b7280">' + fmtDate(info.savedAt) + '</td>'
          + '<td style="padding:6px 8px;text-align:right">'
          + '<button onclick="pdLauncherDelete(' + JSON.stringify(n) + ')" style="font-size:11px;color:#ef4444;background:none;border:none;cursor:pointer;padding:2px 6px">Delete</button>'
          + '</td></tr>';
      }});
      listHtml += '</tbody></table>';
    }}

    mgr.innerHTML = listHtml
      + '<div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap">'
      + '<div style="flex:1;min-width:160px">'
      + '<label style="display:block;font-size:12px;color:#6b7280;margin-bottom:4px">Dataset name</label>'
      + '<input id="pd-new-name" type="text" placeholder="e.g. Online Customers" '
      + 'style="width:100%;box-sizing:border-box;padding:7px 10px;border:1px solid #d1d5db;border-radius:6px;font-size:13px">'
      + '</div>'
      + '<div style="flex:1;min-width:160px">'
      + '<label style="display:block;font-size:12px;color:#6b7280;margin-bottom:4px">CSV / TXT file</label>'
      + '<input id="pd-new-file" type="file" accept=".csv,.txt" '
      + 'style="width:100%;box-sizing:border-box;padding:6px 8px;border:1px solid #d1d5db;border-radius:6px;font-size:12px;background:#fff">'
      + '</div>'
      + '<button onclick="pdLauncherUpload()" style="padding:8px 18px;background:#4f46e5;color:#fff;border:none;border-radius:6px;font-size:13px;font-weight:600;cursor:pointer;white-space:nowrap">Save Dataset</button>'
      + '</div>'
      + '<p id="pd-upload-status" style="font-size:12px;color:#6b7280;margin:8px 0 0"></p>';
  }}

  window.pdLauncherUpload = function() {{
    var fi = document.getElementById('pd-new-file');
    var ni = document.getElementById('pd-new-name');
    var st = document.getElementById('pd-upload-status');
    if (!fi || !fi.files[0]) {{ if (st) st.textContent = 'Please choose a file.'; return; }}
    var rawName = fi.files[0].name.replace(/[.][^.]+$/, '');
    var name = (ni && ni.value.trim()) ? ni.value.trim() : rawName || 'Dataset';
    if (st) st.textContent = 'Reading file...';
    var r = new FileReader();
    r.onload = function(e) {{
      var postcodes = extractPostcodes(e.target.result);
      if (!postcodes.length) {{
        if (st) st.textContent = 'No UK postcodes found in file. Please check the format.';
        return;
      }}
      var all = _all();
      all[name] = {{ data: postcodes, count: postcodes.length, savedAt: new Date().toISOString() }};
      _save(all);
      localStorage.setItem(ACT_KEY, name);
      if (st) st.textContent = 'Saved "' + name + '" (' + postcodes.length.toLocaleString() + ' postcodes).';
      if (ni) ni.value = '';
      if (fi) fi.value = '';
      renderManager();
    }};
    r.readAsText(fi.files[0]);
  }};

  window.pdLauncherDelete = function(name) {{
    if (!confirm('Delete dataset "' + name + '"?')) return;
    var all = _all();
    delete all[name];
    _save(all);
    var keys = Object.keys(all);
    localStorage.setItem(ACT_KEY, keys.length ? keys[0] : '');
    renderManager();
  }};

  document.addEventListener('DOMContentLoaded', renderManager);
}})();
</script>"""

    # Inject the data manager card HTML.
    # We look for the tools grid and insert the data manager card before it.
    # Fallback: insert just before </main> or </body>.
    data_card = """
<div style="background:#fff;border-radius:16px;padding:28px 32px;box-shadow:0 1px 4px rgba(0,0,0,0.08);border:1.5px solid #e0e7ff;margin-bottom:28px">
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
    <div style="width:40px;height:40px;background:#eef2ff;border-radius:10px;display:flex;align-items:center;justify-content:center;font-size:20px">📁</div>
    <div>
      <h2 style="margin:0;font-size:16px;font-weight:700;color:#1e1b4b">Your Data</h2>
      <p style="margin:0;font-size:13px;color:#6b7280">Upload customer files here — they load automatically in every tool</p>
    </div>
  </div>
  <div id="pd-data-manager">Loading...</div>
</div>"""

    # Try to inject before the first tool card / grid
    # Look for the main tools section landmark
    for marker in ['<div class="tools-grid"', '<div class="grid"', '<section', '<main']:
        idx = html.find(marker)
        if idx != -1:
            html = html[:idx] + data_card + '\n' + html[idx:]
            break
    else:
        # Fallback: before </main> or </body>
        html = inject_before_body_end(html, data_card)

    return inject_before_body_end(html, launcher_script)


# ── Tool registry ──────────────────────────────────────────────────────────────

def get_patcher(filename, slug):
    name = os.path.basename(filename).lower()
    if name == 'index.html':
        return lambda h: patch_launcher(h, slug)
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

        html = fix_asset_paths(html)

        html = re.sub(
            r'(<title>[^<]*)(</title>)',
            rf'\g<1> · {args.client}\g<2>',
            html, count=1
        )

        if args.pin:
            html = html.replace('<head>', '<head>\n' + pin_gate_script(args.pin, slug), 1)

        patcher = get_patcher(dst_name, slug)
        if patcher:
            html = patcher(html)
            print(f"  ✓ Patched  : {dst_name}")
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
        pt_index = os.path.join(profile_dst, 'index.html')
        if os.path.exists(pt_index):
            with open(pt_index, 'r', encoding='utf-8') as f:
                pt_html = f.read()
            if args.pin:
                pt_html = pt_html.replace('<head>', '<head>\n' + pin_gate_script(args.pin, slug), 1)
            pt_html = patch_profile_tool_html(pt_html, slug)
            with open(pt_index, 'w', encoding='utf-8') as f:
                f.write(pt_html)
            print(f"  ✓ Patched  : profile-tool/index.html")
        else:
            print(f"  ○ Copied   : profile-tool/")

    print(f"\n  {processed} files written to clients/{slug}/")
    print(f"\n── Next steps ──────────────────────────────────────────────")
    print(f"  cd to your geo_explorer repo folder, then:")
    print(f"    git add clients/{slug}")
    print(f'    git commit -m "Update {args.client} client"')
    print(f"    git push origin main")
    print()
    print(f"  Live URL (after push):")
    print(f"    https://lester-dotcom.github.io/geo_explorer/clients/{slug}/")
    print()


if __name__ == '__main__':
    main()

// ── Branding (reads from launcher geoSuiteSettings) ──────────────────────
(function applyBranding() {
  try {
    const s = JSON.parse(localStorage.getItem('geoSuiteSettings') || '{}');
    if (s.primary) {
      document.documentElement.style.setProperty('--pd-green', s.primary);
      document.documentElement.style.setProperty('--pd-green-dark', s.primary);
      document.documentElement.style.setProperty('--pd-green-light', s.primary + '26');
    }
    if (s.secondary) {
      document.documentElement.style.setProperty('--pd-slate', s.secondary);
      document.documentElement.style.setProperty('--pd-slate-dark', s.secondary);
    }
    if (s.name) {
      const h1 = document.querySelector('header h1');
      if (h1) h1.textContent = s.name + ' · Profile & Lookalike Finder';
      document.title = document.title.replace('Pratt Digital', s.name);
    }
    if (s.logo) {
      const img = document.querySelector('header .header-logo');
      if (img) img.src = s.logo;
    }
  } catch(e) {}
})();
// ── District data (IMD decile, house price band 1-10, rural 0/1, region) ──

// House price band descriptions
const HP_LABELS = {1:'Under £150k',2:'£150–200k',3:'£200–250k',4:'£250–300k',5:'£300–350k',6:'£350–450k',7:'£450–550k',8:'£550–700k',9:'£700k–1m',10:'Over £1m'};
const IMD_LABELS = {1:'Most deprived 10%',2:'Decile 2',3:'Decile 3',4:'Decile 4',5:'Decile 5',6:'Decile 6',7:'Decile 7',8:'Decile 8',9:'Decile 9',10:'Least deprived 10%'};

// Colour palette for regions
const REGION_COLOURS = ['#2d5be3','#e35b2d','#18a058','#9333ea','#f59e0b','#06b6d4','#ec4899','#64748b','#84cc16'];

// Prune fake districts (data.js was generated with WD0-WD99 style entries for every area)
(function() {
  for (const key of Object.keys(DISTRICT_DATA)) {
    if (!VALID_DISTRICTS.has(key)) delete DISTRICT_DATA[key];
  }
})();

let rawData = [];
let profileData = null;

// ── File input ────────────────────────────────────────────────────────────
document.getElementById('fileInput').addEventListener('change', function() {
  const f = this.files[0]; if (!f) return;
  document.getElementById('fileName').textContent = f.name;
  document.getElementById('uploadLabel').classList.add('loaded');
  const r = new FileReader();
  r.onload = e => { parseCSV(e.target.result); document.getElementById('pasteArea').value=''; };
  r.readAsText(f);
});
const lbl = document.getElementById('uploadLabel');
lbl.addEventListener('dragover', e=>{e.preventDefault(); lbl.style.borderColor='var(--pd-green)';});
lbl.addEventListener('dragleave', ()=>lbl.style.borderColor='');
lbl.addEventListener('drop', e=>{
  e.preventDefault(); lbl.style.borderColor='';
  const f=e.dataTransfer.files[0]; if(!f) return;
  document.getElementById('fileName').textContent=f.name;
  lbl.classList.add('loaded');
  const r=new FileReader(); r.onload=ev=>parseCSV(ev.target.result); r.readAsText(f);
});
document.getElementById('pasteArea').addEventListener('input', function(){
  parseCSV(this.value);
  document.getElementById('fileName').textContent='';
  document.getElementById('uploadLabel').classList.remove('loaded');
});

// ── Parse ─────────────────────────────────────────────────────────────────
function normalisePC(s) {
  if (!s) return null;
  const c = s.replace(/\s+/g,'').toUpperCase();
  if (c.length>=5&&c.length<=7) return c.slice(0,-3)+' '+c.slice(-3);
  return null;
}
function extractPC(s) {
  const m = s.match(/([A-Z]{1,2}\d{1,2}[A-Z]?\s*\d[A-Z]{2})/i);
  return m ? normalisePC(m[1]) : null;
}
function districtOf(pc) { return pc.replace(/\s.*/,''); }
function parseCSV(text) {
  const lines = text.trim().split('\n').filter(l=>l.trim());
  if (!lines.length) { rawData=[]; checkReady(); return; }
  const isHdr = l => /postcode|address|name|customer|email|order/i.test(l);
  const start = isHdr(lines[0]) ? 1 : 0;
  rawData = [];
  for (const line of lines.slice(start)) {
    const parts = line.split(',').map(s=>s.trim().replace(/^["']|["']$/g,''));
    let pc = null;
    for (const p of parts) { if(!pc) pc=extractPC(p); }
    if(!pc) pc=normalisePC(parts[parts.length-1])||normalisePC(parts[0]);
    if(pc) rawData.push(pc);
  }
  setStatus(`${rawData.length} postcodes loaded.`);
  checkReady();
}
function checkReady() {
  document.getElementById('analyseBtn').disabled = rawData.length===0;
}
function setStatus(msg,type='') {
  const el=document.getElementById('statusMsg');
  el.textContent=msg; el.className='status-msg'+(type?' '+type:'');
}
function setProgress(pct,text) {
  document.getElementById('progressWrap').classList.toggle('active',pct<100);
  document.getElementById('progressFill').style.width=pct+'%';
  document.getElementById('progressText').textContent=text;
}

// ── Geocode ───────────────────────────────────────────────────────────────
async function bulkGeocode(postcodes) {
  const BATCH=100, results={};
  for (let i=0;i<postcodes.length;i+=BATCH) {
    const batch=postcodes.slice(i,i+BATCH);
    setProgress(Math.round(i/postcodes.length*70), `Geocoding… ${i}/${postcodes.length}`);
    try {
      const resp=await fetch('https://api.postcodes.io/postcodes',{
        method:'POST',headers:{'Content-Type':'application/json'},
        body:JSON.stringify({postcodes:batch})
      });
      const data=await resp.json();
      if(data.status===200) for(const item of data.result) {
        const key=item.query.replace(/\s+/g,'').toUpperCase();
        results[key]=item.result
          ? {lat:item.result.latitude,lng:item.result.longitude,
             region:item.result.region||'Unknown',
             adminDistrict:item.result.admin_district||'Unknown',
             valid:true}
          : {valid:false};
      }
    } catch(e) { console.error(e); }
  }
  return results;
}

// ── Main analysis ─────────────────────────────────────────────────────────
async function runAnalysis() {
  document.getElementById('analyseBtn').disabled=true;
  const unique=[...new Set(rawData.map(pc=>pc.replace(/\s+/g,'').toUpperCase()))];
  const geo = await bulkGeocode(unique);
  setProgress(75,'Building profile…');

  // Enrich each postcode with district data
  const enriched=[];
  let noGeo=0,noDistrict=0,missingNI=0,missingCI=0;
  const missingDistricts={},missingScotland={};
  const SCOTTISH_AREAS=new Set(['AB','DD','DG','EH','FK','G','HS','IV','KA','KW','KY','ML','PA','PH','TD','ZE']);
  const NI_AREAS=new Set(['BT']);
  const CI_AREAS=new Set(['JE','GY','IM']);
  for(const pc of rawData) {
    const key=pc.replace(/\s+/g,'').toUpperCase();
    const g=geo[key];
    if(!g||!g.valid){noGeo++;continue;}
    const d=districtOf(pc);
    const stats=DISTRICT_DATA[d];
    if(!stats){
      const area=d.match(/^([A-Z]+)/)?.[1]||'';
      if(SCOTTISH_AREAS.has(area)) missingScotland[d]=(missingScotland[d]||0)+1;
      else if(NI_AREAS.has(area)) missingNI++;
      else if(CI_AREAS.has(area)) missingCI++;
      else { noDistrict++; missingDistricts[d]=(missingDistricts[d]||0)+1; }
      continue;
    }
    enriched.push({pc,district:d,region:stats.region,imd:stats.imd,hp:stats.hp,rural:stats.rural,
      age_young:stats.age_young||25,age_mid:stats.age_mid||40,age_older:stats.age_older||35,
      pct_prof:stats.pct_prof||30,pct_no_car:stats.pct_no_car||20,pct_degree:stats.pct_degree||25,
      lat:g.lat,lng:g.lng,adminDistrict:g.adminDistrict});
  }
  const noScotland=Object.values(missingScotland).reduce((s,v)=>s+v,0);
  const noData=noGeo+noDistrict+noScotland+missingNI+missingCI;

  // Compute all averages including new Census-derived fields
  const calcAvg = (fn) => enriched.reduce((s,e)=>s+fn(e),0)/Math.max(1,enriched.length);
  const avgImd2    = calcAvg(e=>e.imd);
  const avgHp2     = calcAvg(e=>e.hp);
  const pctRural2  = enriched.filter(e=>e.rural).length/Math.max(1,enriched.length)*100;
  const avgYoung   = calcAvg(e=>e.age_young);
  const avgMid     = calcAvg(e=>e.age_mid);
  const avgOlder   = calcAvg(e=>e.age_older);
  const avgProf    = calcAvg(e=>e.pct_prof);
  const avgNoCar   = calcAvg(e=>e.pct_no_car);
  const avgDegree  = calcAvg(e=>e.pct_degree);
  setProgress(90,'Calculating…');
  if(enriched.length<5) {
    setStatus(`Only ${enriched.length} postcodes could be profiled (need data for districts). Try more postcodes.`,'error');
    document.getElementById('analyseBtn').disabled=false;
    setProgress(100,'');
    return;
  }

  profileData={enriched,noData,noGeo,noDistrict,noScotland,missingNI,missingCI,missingDistricts,missingScotland,total:rawData.length,avgYoung,avgMid,avgOlder,avgProf,avgNoCar,avgDegree};
  try {
    renderResults();
  } catch(e) {
    console.error('renderResults failed:', e);
    setStatus('Error rendering results: ' + e.message, 'error');
    document.getElementById('analyseBtn').disabled=false;
    setProgress(100,'');
    return;
  }
  setProgress(100,'');
  const matchPct_=Math.round(enriched.length/rawData.length*100);
  setStatus(`Matched ${enriched.length.toLocaleString()} of ${rawData.length.toLocaleString()} postcodes (${matchPct_}%) across ${new Set(enriched.map(e=>e.district)).size} districts.`+(noGeo>0?' '+noGeo+' invalid postcodes.':'')+(noDistrict>0?' '+noDistrict+' districts not in lookup.':''),'success');
  document.getElementById('analyseBtn').disabled=false;
}

// ── Render ────────────────────────────────────────────────────────────────
function renderResults() {
  const pd=profileData;
  const {enriched,noData,noGeo,noDistrict,noScotland,missingNI,missingCI,missingDistricts,total,avgYoung,avgMid,avgOlder,avgProf,avgNoCar,avgDegree}=pd;
  const matchPct=Math.round(enriched.length/(total||enriched.length)*100);
  const n=enriched.length;

  // Compute averages
  const avgImd = pd.avgYoung!==undefined ? enriched.reduce((s,e)=>s+e.imd,0)/n : enriched.reduce((s,e)=>s+e.imd,0)/n;
  const avgHp  = enriched.reduce((s,e)=>s+e.hp,0)/n;
  const pctRural = enriched.filter(e=>e.rural).length/n*100;
  const pctEngland = enriched.filter(e=>!['Scotland','Wales'].includes(e.region)).length/n*100;

  // IMD distribution
  const imdDist={}; for(let i=1;i<=10;i++) imdDist[i]=0;
  enriched.forEach(e=>imdDist[e.imd]=(imdDist[e.imd]||0)+1);

  // HP distribution
  const hpDist={}; for(let i=1;i<=10;i++) hpDist[i]=0;
  enriched.forEach(e=>hpDist[e.hp]=(hpDist[e.hp]||0)+1);

  // Region distribution (England only)
  const regionDist={};
  enriched.filter(e=>!['Scotland','Wales'].includes(e.region)).forEach(e=>{
    regionDist[e.region]=(regionDist[e.region]||0)+1;
  });
  const regionSorted=Object.entries(regionDist).sort((a,b)=>b[1]-a[1]);
  const maxRegion=regionSorted[0]?.[1]||1;

  // Top districts
  const distCount={};
  enriched.forEach(e=>distCount[e.district]=(distCount[e.district]||0)+1);
  const topDists=Object.entries(distCount).sort((a,b)=>b[1]-a[1]).slice(0,8);

  // Dominant characteristics for pen portrait
  const dominantImd=Math.round(avgImd);
  const dominantHp=Math.round(avgHp);
  const topRegion=regionSorted[0]?.[0]||'England';

  // Generate pen portrait text
  const portrait=generatePortrait(enriched,avgImd,avgHp,pctRural,topRegion,n,avgYoung||0,avgMid||0,avgOlder||0,avgProf||0,avgNoCar||0,avgDegree||0);

  // Lookalike districts
  const lookalikes=findLookalikes(enriched,avgImd,avgHp,pctRural);

  const content=document.getElementById('content');
  content.innerHTML=`
<div class="tabs">
  <button class="tab active" onclick="switchTab(this,'tab-portrait')">👤 Customer Profile</button>
  <button class="tab" onclick="switchTab(this,'tab-lookalike')">🎯 Lookalike Areas</button>
  <button class="tab" onclick="switchTab(this,'tab-recs')">📣 Channel Recommendations</button>
</div>

<!-- PORTRAIT TAB -->
<div id="tab-portrait">

  <div class="card">
    <div class="card-title">✍️ Pen Portrait</div>
    <div class="pen-portrait">${portrait}</div>
  </div>

  <div class="card">
    <div class="card-title">📊 At a glance</div>
    <div class="portrait-grid">
      <div class="portrait-card" style="border-left-color:${matchPct>=90?'var(--pd-green)':matchPct>=70?'#f59e0b':'#e24b4a'}">
        <h3>Match rate</h3>
        <div class="value" style="color:${matchPct>=90?'var(--pd-green-dark)':matchPct>=70?'#b45309':'#b91c1c'}">${matchPct}%</div>
        <div class="sub">${n.toLocaleString()} of ${(total||n).toLocaleString()} postcodes</div>
      </div>
      <div class="portrait-card">
        <h3>Postcodes profiled</h3>
        <div class="value">${n.toLocaleString()}</div>
        <div class="sub">${new Set(enriched.map(e=>e.district)).size} districts</div>
      </div>
      <div class="portrait-card">
        <h3>Deprivation (avg)</h3>
        <div class="value">Decile ${dominantImd}</div>
        <div class="sub">${IMD_LABELS[dominantImd]||''}</div>
      </div>
      <div class="portrait-card">
        <h3>House prices (avg)</h3>
        <div class="value">Band ${dominantHp}/10</div>
        <div class="sub">${HP_LABELS[dominantHp]||''}</div>
      </div>
      <div class="portrait-card">
        <h3>Age — dominant band</h3>
        <div class="value">${(avgMid||40)>(avgOlder||35)&&(avgMid||40)>(avgYoung||25)?'35–54':(avgOlder||35)>(avgYoung||25)?'55+':'16–34'}</div>
        <div class="sub">${Math.round(avgYoung||0)}% young · ${Math.round(avgMid||0)}% mid · ${Math.round(avgOlder||0)}% older</div>
      </div>
      <div class="portrait-card">
        <h3>Professional occupations</h3>
        <div class="value">${Math.round(avgProf||0)}%</div>
        <div class="sub">Degree-level: ${Math.round(avgDegree||0)}% · No car: ${Math.round(avgNoCar||0)}%</div>
      </div>
      <div class="portrait-card">
        <h3>Rural/urban split</h3>
        <div class="value">${Math.round(100-pctRural)}% urban</div>
        <div class="sub">${Math.round(pctRural)}% rural</div>
      </div>
      ${noData>0?`<div class="portrait-card wide" style="border-left-color:#f59e0b;background:#fffbeb">
        <h3>Unmatched breakdown</h3>
        <div style="font-size:12px;color:var(--muted);line-height:1.8;margin-top:4px">
          ${noGeo>0?`<span style="color:#b45309">⚠ ${noGeo.toLocaleString()} invalid or unresolved postcodes</span> — terminated, malformed, or not yet in database<br>`:''}
          ${noScotland>0?`<span style="color:#6b7280">ℹ ${noScotland.toLocaleString()} Scottish postcodes</span> — excluded from England profile (different deprivation index)<br>`:''}
          ${missingNI>0?`<span style="color:#6b7280">ℹ ${missingNI.toLocaleString()} Northern Ireland postcodes (BT)</span> — excluded from profile<br>`:''}
          ${missingCI>0?`<span style="color:#6b7280">ℹ ${missingCI.toLocaleString()} Channel Islands/IoM postcodes (JE/GY/IM)</span> — excluded from profile<br>`:''}
          ${noDistrict>0?`<span style="color:#b45309">⚠ ${noDistrict.toLocaleString()} postcodes in districts not in lookup</span> — top missing: ${Object.entries(missingDistricts).sort((a,b)=>b[1]-a[1]).slice(0,8).map(([d,c])=>d+' ('+c+')').join(', ')}`:''}
        </div>
      </div>`:''}
    </div>
  </div>

  <div class="card">
    <div class="card-title">🏚️ Deprivation distribution <span style="font-weight:400;font-size:11px;color:var(--muted)">(IMD 2019 — 1=most deprived)</span></div>
    ${[1,2,3,4,5,6,7,8,9,10].map(d=>{
      const pct=Math.round((imdDist[d]||0)/n*100);
      const isMode=d===dominantImd;
      return `<div class="trait-bar">
        <span class="trait-label" style="${isMode?'font-weight:600;color:var(--text)':''}">Decile ${d}</span>
        <div class="trait-track"><div class="trait-fill" style="width:${pct}%;background:${isMode?'var(--pd-green)':'var(--border)'}"></div></div>
        <span class="trait-val">${pct}%</span>
      </div>`;}).join('')}
  </div>

  <div class="card">
    <div class="card-title">👥 Age &amp; lifestyle profile</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
      <div>
        <div class="section-label" style="margin-bottom:8px">Age distribution</div>
        ${[['16–34 (young)',Math.round(avgYoung||0),'#2d5be3'],['35–54 (mid)',Math.round(avgMid||0),'#18a058'],['55+ (older)',Math.round(avgOlder||0),'#f59e0b']].map(([l,v,c])=>`
          <div class="trait-bar"><span class="trait-label">${l}</span>
          <div class="trait-track"><div class="trait-fill" style="width:${Math.min(100,v*2)}%;background:${c}"></div></div>
          <span class="trait-val">${v}%</span></div>`).join('')}
      </div>
      <div>
        <div class="section-label" style="margin-bottom:8px">Socioeconomic indicators</div>
        ${[['Professional/managerial',Math.round(avgProf||0),'#9333ea'],['Degree-level qualified',Math.round(avgDegree||0),'#06b6d4'],['No car households',Math.round(avgNoCar||0),'#94a3b8']].map(([l,v,c])=>`
          <div class="trait-bar"><span class="trait-label" style="min-width:130px;font-size:10px">${l}</span>
          <div class="trait-track"><div class="trait-fill" style="width:${Math.min(100,v*1.5)}%;background:${c}"></div></div>
          <span class="trait-val">${v}%</span></div>`).join('')}
      </div>
    </div>
  </div>

  <div class="card">
    <div class="card-title">🏠 House price distribution</div>
    ${[1,2,3,4,5,6,7,8,9,10].map(d=>{
      const pct=Math.round((hpDist[d]||0)/n*100);
      const isMode=d===dominantHp;
      return `<div class="trait-bar">
        <span class="trait-label" style="font-size:10px;${isMode?'font-weight:600;color:var(--text)':''}">Band ${d}: ${HP_LABELS[d]}</span>
        <div class="trait-track"><div class="trait-fill" style="width:${pct}%;background:${isMode?'var(--c1)':'var(--border)'}"></div></div>
        <span class="trait-val">${pct}%</span>
      </div>`;}).join('')}
  </div>

  <div class="card">
    <div class="card-title">📍 Regional breakdown</div>
    <div class="region-list">
      ${regionSorted.map(([r,c],i)=>`
        <div class="region-item">
          <span class="region-name">${r}</span>
          <div class="region-track"><div class="region-fill" style="width:${Math.round(c/maxRegion*100)}%;background:${REGION_COLOURS[i%REGION_COLOURS.length]}"></div></div>
          <span class="region-pct">${Math.round(c/n*100)}%</span>
        </div>`).join('')}
    </div>
  </div>

  <div class="card">
    <div class="card-title">🔝 Top postcode districts</div>
    <div class="region-list">
      ${topDists.map(([d,c])=>{
        const stats=DISTRICT_DATA[d];
        return `<div class="region-item">
          <span class="region-name" style="font-family:'DM Mono',monospace;font-size:12px">${d} <span style="font-family:'DM Sans',sans-serif;font-size:10px;color:var(--muted)">${stats?stats.region:''}</span></span>
          <div class="region-track"><div class="region-fill" style="width:${Math.round(c/topDists[0][1]*100)}%;background:var(--c1)"></div></div>
          <span class="region-pct">${c}</span>
        </div>`;}).join('')}
    </div>
  </div>

  <div class="export-row">
    <button class="export-btn green" onclick="exportProfile()">↓ Export profile CSV</button>
    <button class="export-btn" onclick="generateBrief()" style="background:#4c565c;color:#fff;border-color:#4c565c">📄 Generate client brief</button>
  </div>

</div>

<!-- LOOKALIKE TAB -->
<div id="tab-lookalike" style="display:none">

  <div class="card">
    <div style="display:flex;gap:6px;margin-bottom:12px">
      <button class="mode-btn active" id="mode-gap" onclick="setMode('gap')">🔍 Gap districts</button>
      <button class="mode-btn" id="mode-under" onclick="setMode('under')">📉 Underindexing districts</button>
    </div>
    <p id="mode-desc" style="font-size:12px;color:var(--muted);line-height:1.7">
      Based on your customer profile (avg IMD decile <strong>${avgImd.toFixed(1)}</strong>, avg house price band <strong>${avgHp.toFixed(1)}</strong>, <strong>${Math.round(pctRural)}% rural</strong>),
      the tool scores every postcode district in England by how closely it matches.
      Adjust the threshold to tighten or loosen the match criteria.
    </p>
  </div>

  <div class="card">
    <div class="card-title" id="results-card-title">⚙️ Match threshold</div>
    <div class="lookalike-controls">
      <label>IMD tolerance ±</label>
      <input type="range" id="imdTol" min="1" max="4" step="1" value="2" oninput="updateLookalikes()">
      <span id="imdTolVal">2 deciles</span>
      <label style="margin-left:12px">HP tolerance ±</label>
      <input type="range" id="hpTol" min="1" max="4" step="1" value="2" oninput="updateLookalikes()">
      <span id="hpTolVal">2 bands</span>
      <label style="margin-left:12px">Rural match</label>
      <select id="ruralMatch" onchange="updateLookalikes()" style="font-size:12px;padding:4px 8px;border:1px solid var(--border);border-radius:6px;background:var(--surface)">
        <option value="any">Any</option>
        <option value="same">Same as customers</option>
        <option value="urban">Urban only</option>
        <option value="rural">Rural only</option>
      </select>
    </div>
    <div id="lookalike-results"></div>
    <div class="export-row" style="margin-top:12px">
      <button class="export-btn green" id="export-lookalike-btn" onclick="exportLookalikes()">↓ Export CSV</button>
      <button class="export-btn" id="map-toggle-btn" onclick="toggleDistrictMap()" style="display:none">🗺 Show on map</button>
    </div>

    <div class="map-panel" id="district-map-panel">
      <div class="map-panel-header">
        <span id="map-panel-title">District map</span>
        <div style="display:flex;align-items:center;gap:12px">
          <span id="map-legend" style="font-size:11px;color:var(--muted)"></span>
          <button onclick="toggleDistrictMap()" title="Close map">✕</button>
        </div>
      </div>
      <div id="map-progress-wrap" style="display:none;padding:6px 14px;background:var(--surface2);border-bottom:1px solid var(--border)">
        <div style="height:3px;background:var(--border);border-radius:2px;overflow:hidden;margin-bottom:3px">
          <div id="map-progress-fill" style="height:100%;background:var(--pd-green);border-radius:2px;width:0%;transition:width .15s"></div>
        </div>
        <div id="map-progress-text" style="font-size:10px;color:var(--muted)">Loading boundaries…</div>
      </div>
      <div id="district-map"></div>
    </div>
  </div>

</div>

<!-- RECOMMENDATIONS TAB -->
<div id="tab-recs" style="display:none">
  <div id="recs-content"></div>
</div>
`;

  profileData.avgImd=avgImd; profileData.avgHp=avgHp; profileData.pctRural=pctRural;

  // Defer lookalike/underindex calc so UI renders first
  setTimeout(() => {
    profileData.lookalikes=findLookalikes(enriched,avgImd,avgHp,pctRural);
    profileData.underindex=findUnderindex(enriched,avgImd,avgHp,pctRural);
    lookalikMode='gap';
    document.getElementById('mode-gap')?.classList.add('active');
    document.getElementById('mode-under')?.classList.remove('active');
    renderLookalikes(profileData.lookalikes);
    renderRecommendations();
  }, 50);
}

function switchTab(btn, tabId) {
  document.querySelectorAll('.tab').forEach(t=>t.classList.remove('active'));
  btn.classList.add('active');
  document.querySelectorAll('[id^="tab-"]').forEach(t=>t.style.display='none');
  document.getElementById(tabId).style.display='block';
}

// ── Pen portrait generator ────────────────────────────────────────────────
function generatePortrait(enriched, avgImd, avgHp, pctRural, topRegion, n, avgYoung, avgMid, avgOlder, avgProf, avgNoCar, avgDegree) {
  const deprivDesc = avgImd<=2?'highly deprived areas':avgImd<=4?'below-average affluence areas':avgImd<=6?'mid-market areas':avgImd<=8?'above-average affluence areas':'affluent, low-deprivation areas';
  const hpDesc = avgHp<=3?'lower-priced property markets (typically under £250k)':avgHp<=5?'mid-range property markets (£250–350k)':avgHp<=7?'upper-mid property markets (£350–550k)':avgHp<=9?'premium property markets (£550k–£1m)':'the most expensive property markets (over £1m)';
  const ruralDesc = pctRural>60?'predominantly rural':pctRural>40?'a mix of rural and urban':pctRural>20?'mainly urban with some rural pockets':'predominantly urban';
  
  // Regional concentration
  const regionDist={};
  enriched.filter(e=>!['Scotland','Wales'].includes(e.region)).forEach(e=>{
    regionDist[e.region]=(regionDist[e.region]||0)+1;
  });
  const topRegions=Object.entries(regionDist).sort((a,b)=>b[1]-a[1]).slice(0,2).map(r=>r[0]);
  const regionStr=topRegions.length>1?`${topRegions[0]} and ${topRegions[1]}`:topRegions[0]||'England';

  const imdMode=parseInt(Object.entries(
    enriched.reduce((acc,e)=>{acc[e.imd]=(acc[e.imd]||0)+1;return acc;},{})
  ).sort((a,b)=>b[1]-a[1])[0][0]);

  const segments=[];
  if(avgImd>=7&&avgHp>=7) segments.push('likely to be early adopters of premium products and services');
  if(avgImd<=4&&avgHp<=4) segments.push('likely to be value-conscious, deal-driven shoppers');
  if(pctRural>40) segments.push('likely to travel further to shop, with strong online purchasing habits');
  if(avgImd>=6&&avgHp>=5&&pctRural<30) segments.push('typical aspirational suburban professionals');
  if(topRegions[0]==='London') segments.push('heavily London/South East weighted, suggesting a trend-forward customer');
  
  const aY=Math.round(avgYoung||0),aM=Math.round(avgMid||0),aO=Math.round(avgOlder||0);
  const ageDesc=aO>aM&&aO>aY?'an older skew ('+aO+'% aged 55+)':aM>aY?'a mid-age skew ('+aM+'% aged 35–54)':'a younger skew ('+aY+'% aged 16–34)';
  const profDesc=(avgProf||30)>=55?'highly professional':(avgProf||30)>=40?'above-average professional':(avgProf||30)>=25?'mixed occupational':'below-average professional';
  const degreeDesc=(avgDegree||25)>=45?'highly educated':(avgDegree||25)>=30?'above-average qualifications':'average qualification levels';
  return `Your customer base of <strong>${n.toLocaleString()}</strong> postcodes is concentrated in <strong>${regionStr}</strong>, representing <strong>${deprivDesc}</strong> with <strong>${hpDesc}</strong>, in communities that are <strong>${ruralDesc}</strong>.

Age profile shows <strong>${ageDesc}</strong> — ${aY}% young, ${aM}% mid, ${aO}% older. Socioeconomically, these areas have a <strong>${profDesc} workforce</strong> with <strong>${degreeDesc}</strong> (${Math.round(avgDegree||0)}% degree-level). ${Math.round(avgNoCar||0)}% of households have no car, indicating ${(avgNoCar||20)>30?'strong urban dependency':'predominantly car-owning households'}.

The most common IMD decile is <strong>Decile ${imdMode}</strong> (${IMD_LABELS[imdMode]}), with average house price band <strong>${avgHp.toFixed(1)}/10</strong> (${HP_LABELS[Math.round(avgHp)]}).

${segments.length?`This suggests customers are typically <strong>${segments.slice(0,2).join(', ')}</strong>. `:''}Lookalike opportunity exists in areas matching this demographic fingerprint — particularly where deprivation decile, house prices, age profile and professional occupation rates align with your existing base.`;
}

// ── Lookalike finder ──────────────────────────────────────────────────────
let lookalikMode = 'gap'; // 'gap' | 'under'

function setMode(mode) {
  lookalikMode = mode;
  document.getElementById('mode-gap').classList.toggle('active', mode==='gap');
  document.getElementById('mode-under').classList.toggle('active', mode==='under');

  const desc = document.getElementById('mode-desc');
  if (mode === 'gap') {
    desc.innerHTML = `Based on your customer profile (avg IMD decile <strong>${profileData?.avgImd?.toFixed(1)||'—'}</strong>, avg house price band <strong>${profileData?.avgHp?.toFixed(1)||'—'}</strong>, <strong>${Math.round(profileData?.pctRural||0)}% rural</strong>),
      the tool scores every postcode district in England by how closely it matches.
      <strong>Gap districts</strong> are places with matching demographics where you have <em>no customers yet</em>.`;
  } else {
    desc.innerHTML = `<strong>Underindexing districts</strong> are places that already appear in your customer data and closely match your profile demographics — but have <em>fewer customers than you'd expect</em> given that profile fit.
      These are your leaky buckets: the conditions are right, the product works there, but penetration is weaker than it should be.`;
  }

  document.getElementById('results-card-title').textContent = mode==='gap' ? '⚙️ Match threshold' : '⚙️ Match threshold & underperformance';
  document.getElementById('export-lookalike-btn').textContent = mode==='gap' ? '↓ Export gap districts CSV' : '↓ Export underindexing districts CSV';
  if (districtMapOpen) clearDistrictMap();
  updateLookalikes();
}

function findLookalikes(enriched, avgImd, avgHp, pctRural) {
  const imdTol=parseInt(document.getElementById('imdTol')?.value||2);
  const hpTol=parseInt(document.getElementById('hpTol')?.value||2);
  const ruralMatch=document.getElementById('ruralMatch')?.value||'any';
  const customerRural=pctRural>50;

  // Get districts customers ARE in
  const customerDistricts=new Set(enriched.map(e=>e.district));

  const matches=[];
  for(const [district,stats] of Object.entries(DISTRICT_DATA)) {
    if(customerDistricts.has(district)) continue;
    if(['Scotland','Wales'].includes(stats.region)) continue;

    if(ruralMatch==='same'&&stats.rural!==(customerRural?1:0)) continue;
    if(ruralMatch==='urban'&&stats.rural!==0) continue;
    if(ruralMatch==='rural'&&stats.rural!==1) continue;

    const imdDiff=Math.abs(stats.imd-avgImd);
    const hpDiff=Math.abs(stats.hp-avgHp);
    if(imdDiff>imdTol||hpDiff>hpTol) continue;

    const imdScore=(1-imdDiff/(imdTol+1))*50;
    const hpScore=(1-hpDiff/(hpTol+1))*40;
    const ruralScore=stats.rural===(customerRural?1:0)?10:5;
    const score=Math.round(imdScore+hpScore+ruralScore);
    matches.push({district,stats,score,imdDiff,hpDiff});
  }
  return matches.sort((a,b)=>b.score-a.score);
}

function findUnderindex(enriched, avgImd, avgHp, pctRural) {
  const imdTol=parseInt(document.getElementById('imdTol')?.value||2);
  const hpTol=parseInt(document.getElementById('hpTol')?.value||2);
  const ruralMatch=document.getElementById('ruralMatch')?.value||'any';
  const customerRural=pctRural>50;

  // Count customers per district
  const distCount={};
  for (const e of enriched) {
    distCount[e.district] = (distCount[e.district]||0) + 1;
  }

  // Step 1: score ALL districts (customer + non-customer) against the profile
  // We need this to establish what a "normal" count looks like for a given score
  const allScored = [];
  for (const [district, stats] of Object.entries(DISTRICT_DATA)) {
    if (!stats) continue;
    if (['Scotland','Wales','Northern Ireland'].includes(stats.region)) continue;
    if(ruralMatch==='same'&&stats.rural!==(customerRural?1:0)) continue;
    if(ruralMatch==='urban'&&stats.rural!==0) continue;
    if(ruralMatch==='rural'&&stats.rural!==1) continue;
    const imdDiff = Math.abs(stats.imd - avgImd);
    const hpDiff  = Math.abs(stats.hp  - avgHp);
    if (imdDiff > imdTol || hpDiff > hpTol) continue;
    const imdScore   = (1 - imdDiff/(imdTol+1)) * 50;
    const hpScore    = (1 - hpDiff/(hpTol+1))   * 40;
    const ruralScore = stats.rural===(customerRural?1:0) ? 10 : 5;
    const profileScore = Math.round(imdScore + hpScore + ruralScore);
    const count = distCount[district] || 0;
    allScored.push({ district, stats, profileScore, count });
  }

  if (!allScored.length) return [];

  // Step 2: among districts that DO have customers, compute expected count
  // Expected = (district profileScore / avg profileScore) * avg customer count
  // This normalises for profile quality — a 90% match district should have
  // proportionally more customers than a 65% match district
  const withCustomers = allScored.filter(d => d.count > 0);
  if (!withCustomers.length) return [];

  const avgScore = withCustomers.reduce((s,d)=>s+d.profileScore,0) / withCustomers.length;
  const avgCount = withCustomers.reduce((s,d)=>s+d.count,0) / withCustomers.length;

  // Only flag districts that actually have customers but underperform
  const results = [];
  for (const d of withCustomers) {
    // Expected count scaled by how well this district matches the profile
    const scoreRatio = avgScore > 0 ? d.profileScore / avgScore : 1;
    const expectedCount = Math.max(2, Math.round(avgCount * scoreRatio));
    if (expectedCount < 2) continue;
    const index = Math.round(d.count / expectedCount * 100);
    const gap = expectedCount - d.count;
    // Only show genuine underperformers with meaningful gap
    if (index < 80 && gap >= 2) {
      results.push({ ...d, expectedCount, index, gap });
    }
  }

  return results.sort((a,b) => b.gap - a.gap);
}

function updateLookalikes() {
  document.getElementById('imdTolVal').textContent=document.getElementById('imdTol').value+' deciles';
  document.getElementById('hpTolVal').textContent=document.getElementById('hpTol').value+' bands';
  if (profileData) {
    if (lookalikMode === 'gap') {
      profileData.lookalikes = findLookalikes(profileData.enriched, profileData.avgImd, profileData.avgHp, profileData.pctRural);
      renderLookalikes(profileData.lookalikes);
    } else {
      profileData.underindex = findUnderindex(profileData.enriched, profileData.avgImd, profileData.avgHp, profileData.pctRural);
      renderUnderindex(profileData.underindex);
    }
    renderRecommendations();
  }
}

function renderLookalikes(matches) {
  const el=document.getElementById('lookalike-results');
  if(!el) return;
  if(!matches.length){
    el.innerHTML='<p style="font-size:12px;color:var(--muted);padding:12px 0">No districts match at this threshold — try widening the tolerance sliders.</p>';
    return;
  }
  el.innerHTML=`<p style="font-size:11px;color:var(--muted);margin-bottom:10px">${matches.length} matching district${matches.length!==1?'s':''} found — not already in your customer data</p>
  <div class="match-grid">
    ${matches.map(m=>{
      const scoreClass=m.score>=75?'score-high':m.score>=55?'score-mid':'score-low';
      return `<div class="match-card">
        <div class="match-pc">${m.district}</div>
        <div class="match-score ${scoreClass}">${m.score}%</div>
        <div class="match-details">
          ${m.stats.region}<br>
          IMD ${m.stats.imd} · HP ${m.stats.hp} · ${m.stats.rural?'Rural':'Urban'}<br>
          ${m.stats.age_young||'?'}/${m.stats.age_mid||'?'}/${m.stats.age_older||'?'}% age<br>
          ${m.stats.pct_prof||'?'}% prof · ${m.stats.pct_degree||'?'}% degree
        </div>
        <div class="match-bar"><div class="match-bar-fill" style="width:${m.score}%"></div></div>
      </div>`;}).join('')}
  </div>`;
}

function renderUnderindex(results) {
  const el = document.getElementById('lookalike-results');
  if (!el) return;
  if (!results.length) {
    el.innerHTML = '<p style="font-size:12px;color:var(--muted);padding:12px 0">No underindexing districts found at this threshold — try widening the tolerance sliders, or your existing customers are well-distributed across matched districts.</p>';
    return;
  }
  const maxGap = results[0].gap;
  el.innerHTML = `
    <p style="font-size:11px;color:var(--muted);margin-bottom:10px">
      ${results.length} district${results.length!==1?'s':''} underindexing — demographically matched but underperforming. Sorted by size of opportunity (expected − actual customers).
    </p>
    <div class="match-grid">
      ${results.map(r => {
        const gapPct = Math.round(r.gap / maxGap * 100);
        return `<div class="match-card underindex-card">
          <div class="match-pc">${r.district}</div>
          <div class="match-score underindex-score">Index ${r.index}</div>
          <div class="match-details">
            ${r.stats.region}<br>
            IMD ${r.stats.imd} · HP ${r.stats.hp} · Profile ${r.profileScore}%<br>
            <span style="color:#b33030;font-weight:500">${r.count} actual vs ${r.expectedCount} expected</span>
          </div>
          <div class="match-bar"><div class="match-bar-fill" style="width:${gapPct}%;background:#e24b4a"></div></div>
        </div>`;
      }).join('')}
    </div>`;
}

// ── Recommendation engine ─────────────────────────────────────────────────
function generateRecommendations(enriched, avgImd, avgHp, pctRural, lookalikes, topRegion) {
  const recs = [];
  const n = enriched.length;
  const isPremium   = avgHp >= 7;
  const isMidMarket = avgHp >= 4 && avgHp < 7;
  const isValueLed  = avgHp < 4;
  const isUrban     = pctRural < 30;
  const isMixed     = pctRural >= 30 && pctRural <= 60;
  const isRural     = pctRural > 60;
  const isAffluent  = avgImd >= 7;
  const isDeprived  = avgImd <= 4;

  // Count how many high-score lookalikes exist
  const highMatches  = lookalikes.filter(m => m.score >= 75);
  const urbanMatches = lookalikes.filter(m => m.score >= 60 && m.stats.rural === 0);
  const ruralMatches = lookalikes.filter(m => m.score >= 60 && m.stats.rural === 1);

  // Regional concentration of lookalikes
  const lookalikRegions = {};
  lookalikes.forEach(m => {
    lookalikRegions[m.stats.region] = (lookalikRegions[m.stats.region]||0)+1;
  });
  const topLookalikRegion = Object.entries(lookalikRegions).sort((a,b)=>b[1]-a[1])[0];

  // ── 1. Meta / Social ──────────────────────────────────────────────────
  if (highMatches.length >= 3) {
    const districtList = highMatches.slice(0,5).map(m=>m.district).join(', ');
    recs.push({
      priority: 'high',
      channels: isPremium ? ['ch-meta','ch-display'] : ['ch-meta'],
      title: 'Meta Ads — Lookalike & Geo-targeted Campaigns',
      rationale: `You have ${highMatches.length} high-confidence lookalike districts (score 75%+) that share your customers' demographic fingerprint but have no customer penetration yet. Meta's geo-targeting and lookalike audience tools are well-suited to reaching these areas efficiently — especially given the profile match on deprivation and house price band.`,
      actions: [
        `<strong>Lookalike audience:</strong> Upload your customer postcode list as a Custom Audience in Meta, then build a 1–2% Lookalike targeting the lookalike districts: ${districtList}${highMatches.length > 5 ? ` (+${highMatches.length-5} more)` : ''}.`,
        `<strong>Geo radius targeting:</strong> Set campaign geo to the top lookalike districts with a 5–10 mile radius per district. Keep separate ad sets per region so you can read performance by geography.`,
        isPremium
          ? `<strong>Creative angle:</strong> Premium profile — lead with quality, provenance, and aspiration. Avoid discount-led creative.`
          : isValueLed
          ? `<strong>Creative angle:</strong> Value-conscious profile — lead with price transparency, bundles, or introductory offers.`
          : `<strong>Creative angle:</strong> Mid-market profile — balance aspiration with accessibility. Social proof (reviews, UGC) tends to over-index here.`,
      ]
    });
  }

  // ── 2. Google Search / PMax ───────────────────────────────────────────
  recs.push({
    priority: highMatches.length >= 5 ? 'high' : 'mid',
    channels: ['ch-google', 'ch-search'],
    title: 'Google Ads — Geo-targeted Search & Performance Max',
    rationale: `Search intent in lookalike districts can be captured before competitors establish presence. ${isAffluent ? 'Your affluent customer profile suggests above-average search CPCs are justified — these users convert.' : 'Mid-funnel search campaigns work well for this profile, particularly for considered purchases.'}`,
    actions: [
      `<strong>Search campaigns:</strong> Add the top ${Math.min(highMatches.length, 10)} lookalike postcode districts as location targets in existing or new campaigns. Use bid adjustments (+15–25%) for districts scoring 80%+.`,
      `<strong>Performance Max:</strong> Include lookalike district coordinates in the asset group location signal. Feed the customer postcode list as a Customer Match signal to anchor PMax audience targeting.`,
      topLookalikRegion
        ? `<strong>Regional push:</strong> ${topLookalikRegion[0]} has the highest concentration of lookalike districts (${topLookalikRegion[1]} districts). Consider a dedicated campaign for this region with region-specific ad copy.`
        : `<strong>Ad copy:</strong> Where possible, reference local area in headlines — improves CTR in geo-targeted campaigns.`,
    ]
  });

  // ── 3. Out of Home ───────────────────────────────────────────────────
  if (isRural || isMixed || ruralMatches.length >= 3) {
    recs.push({
      priority: isRural ? 'high' : 'mid',
      channels: ['ch-ooh'],
      title: 'Out of Home — Rural & Commuter OOH',
      rationale: `${ruralMatches.length} of your lookalike districts are rural or semi-rural. Rural audiences are harder to reach digitally (lower social media density, ad fatigue lower) but respond well to OOH — particularly high-dwell formats like petrol forecourts, farm shops, garden centres, and village notice boards. ${isRural ? 'Your existing customers are predominantly rural, confirming this channel is already part of their media environment.' : ''}`,
      actions: [
        `<strong>Forecourt & convenience:</strong> Clear Channel and Global both offer rural forecourt networks — good reach in market towns and commuter villages that match your lookalike profile.`,
        `<strong>Garden centre / lifestyle retail:</strong> If the product has a lifestyle angle, garden centre OOH (noticeboards, car parks, café areas) reaches exactly the rural-affluent demographic.`,
        isPremium
          ? `<strong>Commuter rail:</strong> For premium profiles, train station 6-sheets in commuter towns within lookalike districts — high dwell, high-income audience. Focus on ${topLookalikRegion ? topLookalikRegion[0] : 'regions with highest lookalike concentration'}.`
          : `<strong>Roadside 48-sheets:</strong> Cost-effective for mid-market rural reach. Prioritise A-road sites serving market towns in the top lookalike districts.`,
      ]
    });
  } else if (isUrban && isPremium) {
    recs.push({
      priority: 'mid',
      channels: ['ch-ooh'],
      title: 'Out of Home — Premium Urban OOH',
      rationale: `Your customer profile is urban and premium (avg house price band ${avgHp.toFixed(1)}/10, IMD decile ${avgImd.toFixed(1)}). Premium urban OOH formats reach this demographic in their daily environment — commute, leisure, and high street.`,
      actions: [
        `<strong>London / major city Underground:</strong> Cross-track and escalator panels in stations serving lookalike districts. TfL route planner data can identify which lines serve your target postcodes.`,
        `<strong>High street 6-sheets:</strong> Digital 6-sheet networks in affluent high streets within lookalike districts — allows dayparting and creative versioning.`,
        `<strong>Gym & leisure:</strong> Gym washroom and leisure centre advertising skews urban-professional and matches a premium profile well.`,
      ]
    });
  } else {
    recs.push({
      priority: 'mid',
      channels: ['ch-ooh'],
      title: 'Out of Home — Targeted Urban OOH',
      rationale: `Urban lookalike districts suit digital OOH with geo and time targeting. Programmatic DOOH platforms (eg. Broadsign, Vistar) allow postcode-level targeting aligned directly to your lookalike district list.`,
      actions: [
        `<strong>Programmatic DOOH:</strong> Use your lookalike district list as a location file in a programmatic OOH buy. Broadsign and Vistar both support postcode polygon targeting.`,
        `<strong>Retail proximity:</strong> If any lookalike districts have relevant retail (supermarkets, shopping centres), on-site digital screens reach in-market shoppers.`,
        `<strong>Bus shelters & street furniture:</strong> Cost-effective reach for mid-market urban audiences. JCDecaux and Clear Channel offer postcode-level planning.`,
      ]
    });
  }

  // ── 4. Direct Mail ───────────────────────────────────────────────────
  if (isRural || isAffluent || highMatches.length >= 4) {
    recs.push({
      priority: (isRural && isAffluent) ? 'high' : 'mid',
      channels: ['ch-dm'],
      title: 'Direct Mail — Postcode-targeted Door Drops & Addressed Mail',
      rationale: `${isRural ? 'Rural areas have strong DM response rates — less letterbox competition, higher dwell time, and a demographic that still responds to physical mail. ' : ''}${isAffluent ? 'Affluent households (HP band 7–10) over-index significantly on DM response — physical mail signals quality and cuts through digital noise at this end of the market. ' : ''}The lookalike district list gives you a ready-made targeting brief for a Royal Mail door drop or addressed mailing.`,
      actions: [
        `<strong>Royal Mail Door to Door:</strong> Use the lookalike district list to plan a door drop within target postcode sectors. Royal Mail's targeting tool lets you select by postcode sector and demographic overlay — filter by Acorn category to refine further.`,
        isPremium
          ? `<strong>Addressed mail (solus):</strong> For premium profiles, addressed mail to selected lookalike postcodes via a data broker (e.g. Experian, CACI) consistently outperforms door drops on response rate. Worth the additional cost for high-value products.`
          : `<strong>Shared door drop:</strong> A shared mail pack (e.g. Royal Mail's Partially Addressed Mail) reduces CPT significantly versus solus while still offering postcode-level targeting.`,
        `<strong>Creative:</strong> Include a trackable mechanism — QR code, unique URL, or promo code per district — so you can directly attribute response back to the mailing areas and validate the lookalike model.`,
      ]
    });
  }

  // ── 5. Display / Programmatic ────────────────────────────────────────
  if (urbanMatches.length >= 4 || isPremium) {
    recs.push({
      priority: 'low',
      channels: ['ch-display', 'ch-google'],
      title: 'Programmatic Display — Postcode Audience Targeting',
      rationale: `Programmatic display allows you to target users by postcode segment, effectively reaching people who live in your lookalike districts as they browse across the web. Lower CPM than social but useful for awareness building ahead of a search or social push.`,
      actions: [
        `<strong>DV360 / The Trade Desk:</strong> Build a postcode audience segment from the lookalike district list. Layer with demographic and purchase-intent signals matching your customer profile (${isPremium ? 'premium lifestyle, home improvement, high-income' : 'mid-market lifestyle, value-conscious'}).`,
        `<strong>Retargeting:</strong> Pixel your existing customers by district, then use lookalike modelling within the DSP to expand reach into similar postcode clusters.`,
        `<strong>Sequencing:</strong> Run awareness display first in new lookalike districts, then retarget engagers with a direct-response social or search campaign 2–4 weeks later.`,
      ]
    });
  }

  // ── 6. Regional / local press ────────────────────────────────────────
  if (isRural || topLookalikRegion) {
    recs.push({
      priority: 'low',
      channels: ['ch-display'],
      title: 'Regional Press & Local Digital — Area-specific Push',
      rationale: `${topLookalikRegion ? `${topLookalikRegion[0]} contains your highest concentration of lookalike districts. ` : ''}Regional newspapers, local news sites, and hyperlocal platforms (Nextdoor, local Facebook groups) can build brand presence in specific areas before broader digital spend.`,
      actions: [
        topLookalikRegion
          ? `<strong>Regional press:</strong> Identify the dominant regional title in ${topLookalikRegion[0]} and explore print or digital display. Local press still reaches older, higher-property-value demographics disproportionately well.`
          : `<strong>Local press:</strong> Identify dominant regional titles in your top lookalike district areas for awareness advertising.`,
        `<strong>Nextdoor Ads:</strong> Nextdoor allows postcode neighbourhood targeting — good for reaching homeowners and established residents, which aligns with ${isAffluent ? 'your affluent profile' : 'your target profile'}.`,
        `<strong>Local search (Google Business):</strong> If there's any physical presence in or near lookalike districts, ensure Google Business profiles are optimised for those areas and consider local service ads.`,
      ]
    });
  }

  // Sort: high > mid > low
  const order = {high:0, mid:1, low:2};
  recs.sort((a,b) => order[a.priority]-order[b.priority]);

  return recs;
}

function renderRecommendations() {
  const el = document.getElementById('recs-content');
  if (!el || !profileData) return;

  const {enriched, avgImd, avgHp, pctRural, lookalikes} = profileData;
  const regionDist = {};
  enriched.filter(e=>!['Scotland','Wales'].includes(e.region)).forEach(e=>{
    regionDist[e.region]=(regionDist[e.region]||0)+1;
  });
  const topRegion = Object.entries(regionDist).sort((a,b)=>b[1]-a[1])[0]?.[0]||'England';

  const recs = generateRecommendations(enriched, avgImd, avgHp, pctRural, lookalikes, topRegion);

  const highCount = recs.filter(r=>r.priority==='high').length;
  const summary = `Based on your customer profile — <strong>IMD decile ${avgImd.toFixed(1)}</strong>, house price band <strong>${avgHp.toFixed(1)}/10</strong>, <strong>${Math.round(pctRural)}% rural</strong> — and <strong>${lookalikes.length} lookalike districts</strong> identified, the following channel mix is recommended for expansion targeting. ${highCount} channel${highCount!==1?'s are':' is'} flagged as high priority based on profile fit.`;

  const channelLegend = {
    'ch-meta':'Meta Ads','ch-google':'Google Ads','ch-ooh':'Out of Home',
    'ch-dm':'Direct Mail','ch-search':'Paid Search','ch-display':'Programmatic'
  };

  el.innerHTML = `
    <div class="rec-summary">${summary}</div>

    <div class="rec-section-title">High priority</div>
    ${recs.filter(r=>r.priority==='high').map(r=>recCard(r,channelLegend)).join('')||'<p style="font-size:12px;color:var(--muted);margin-bottom:16px">No high priority channels — widen the lookalike threshold to generate more matches.</p>'}

    <div class="rec-section-title">Worth considering</div>
    ${recs.filter(r=>r.priority==='mid').map(r=>recCard(r,channelLegend)).join('')}

    <div class="rec-section-title">Supporting / longer term</div>
    ${recs.filter(r=>r.priority==='low').map(r=>recCard(r,channelLegend)).join('')}

    <div class="export-row" style="margin-top:8px">
      <button class="export-btn green" onclick="exportRecs()">↓ Export recommendations</button>
    </div>
  `;
}

function recCard(r, channelLegend) {
  const priLabel = {high:'High priority', mid:'Worth considering', low:'Supporting'};
  const priClass = {high:'pri-high', mid:'pri-mid', low:'pri-low'};
  return `<div class="rec-card priority-${r.priority}">
    <div class="rec-header">
      <div>
        <div class="rec-channels">
          ${r.channels.map(c=>`<span class="channel-badge ${c}">${channelLegend[c]||c}</span>`).join('')}
        </div>
      </div>
      <span class="rec-priority ${priClass[r.priority]}">${priLabel[r.priority]}</span>
    </div>
    <div class="rec-title">${r.title}</div>
    <div class="rec-rationale">${r.rationale}</div>
    <div class="rec-actions">
      ${r.actions.map(a=>`<div class="rec-action">${a}</div>`).join('')}
    </div>
  </div>`;
}

function exportRecs() {
  if (!profileData?.lookalikes) return;
  const {avgImd, avgHp, pctRural, lookalikes} = profileData;
  const regionDist={};
  profileData.enriched.filter(e=>!['Scotland','Wales'].includes(e.region)).forEach(e=>{
    regionDist[e.region]=(regionDist[e.region]||0)+1;
  });
  const topRegion=Object.entries(regionDist).sort((a,b)=>b[1]-a[1])[0]?.[0]||'England';
  const recs = generateRecommendations(profileData.enriched, avgImd, avgHp, pctRural, lookalikes, topRegion);
  const rows = [['priority','channel','recommendation','rationale','actions']];
  for (const r of recs) {
    rows.push([
      r.priority,
      r.channels.join(' + '),
      r.title,
      r.rationale.replace(/,/g,';'),
      r.actions.map(a=>a.replace(/<[^>]+>/g,'')).join(' | ').replace(/,/g,';')
    ]);
  }
  dlCSV(rows, 'channel_recommendations.csv');
}
function dlCSV(rows, name) {
  const a=document.createElement('a');
  a.href='data:text/csv;charset=utf-8,'+encodeURIComponent(rows.map(r=>r.join(',')).join('\n'));
  a.download=name; a.click();
}
function exportProfile() {
  if(!profileData) return;
  const rows=[['postcode','district','region','admin_district','imd_decile','hp_band','rural','lat','lng']];
  for(const e of profileData.enriched)
    rows.push([e.pc,e.district,e.region,e.adminDistrict,e.imd,e.hp,e.rural,e.lat,e.lng]);
  dlCSV(rows,'customer_profile.csv');
}
function exportLookalikes() {
  const wb = XLSX.utils.book_new();

  if (lookalikMode === 'gap') {
    if (!profileData?.lookalikes?.length) return;

    // Sheet 1: Districts
    const distRows = [['District','Match Score','Region','IMD Decile','House Price Band','Rural']];
    for (const m of profileData.lookalikes)
      distRows.push([m.district, m.score, m.stats.region, m.stats.imd, m.stats.hp, m.stats.rural]);
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(distRows), 'Districts');

    // Sheet 2: Sectors (expand each district to 10 possible sectors 0-9)
    const secRows = [['Sector','Match Score','District','Region']];
    for (const m of profileData.lookalikes)
      for (let i = 0; i <= 9; i++)
        secRows.push([`${m.district} ${i}`, m.score, m.district, m.stats.region]);
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(secRows), 'Sectors');

    XLSX.writeFile(wb, 'gap_districts.xlsx');

  } else {
    if (!profileData?.underindex?.length) return;

    // Sheet 1: Districts
    const distRows = [['District','Index Score','Profile Match','Actual Customers','Expected Customers','Customer Gap','Region','IMD Decile','House Price Band','Rural']];
    for (const r of profileData.underindex)
      distRows.push([r.district, r.index, r.profileScore, r.count, r.expectedCount, r.gap,
        r.stats.region, r.stats.imd, r.stats.hp, r.stats.rural]);
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(distRows), 'Districts');

    // Sheet 2: Sectors (expand each district to 10 possible sectors 0-9)
    const secRows = [['Sector','Index Score','Profile Match','District','Region']];
    for (const r of profileData.underindex)
      for (let i = 0; i <= 9; i++)
        secRows.push([`${r.district} ${i}`, r.index, r.profileScore, r.district, r.stats.region]);
    XLSX.utils.book_append_sheet(wb, XLSX.utils.aoa_to_sheet(secRows), 'Sectors');

    XLSX.writeFile(wb, 'underindexing_districts.xlsx');
  }
}

// ── Clear ─────────────────────────────────────────────────────────────────
function clearAll() {
  rawData=[]; profileData=null;
  document.getElementById('fileInput').value='';
  document.getElementById('pasteArea').value='';
  document.getElementById('fileName').textContent='';
  document.getElementById('uploadLabel').classList.remove('loaded');
  document.getElementById('progressWrap').classList.remove('active');
  setStatus('Load customer postcodes to build a demographic profile and find lookalike areas.');
  checkReady();
  document.getElementById('content').innerHTML=`<div class="placeholder"><div class="icon">📊</div><p><strong>Paste your customer postcodes on the left</strong><br>The tool will profile your existing customer base by deprivation, house prices, urban/rural split and region — then find other areas of England with matching characteristics where you have no customers yet.</p></div>`;
}

// ── Sample ────────────────────────────────────────────────────────────────
function loadSample() {
  // Realistic sample: London-concentrated business with sparser Home Counties coverage.
  // Heavy London districts (avg 8-10 per district) vs sparse outliers (1-2) produces
  // meaningful underindexing in matched suburban/commuter districts.
  document.getElementById('pasteArea').value=`postcode
SW2 3PP
SW2 5TU
SW2 5NB
SW2 2TP
SW2 3QG
SW2 3QR
SW2 4TW
SW2 3DN
SW3 6LB
SW3 9AY
SW3 9HH
SW3 3UD
SW3 1QP
SW3 3DJ
SW3 5UE
SW3 6NJ
SW4 9NF
SW4 9EJ
SW4 8PL
SW4 7QN
SW4 7PE
SW4 7SE
SW4 9PJ
SW4 8EG
SW5 0BP
SW5 0TR
SW5 9UG
SW5 9AH
SW5 9JX
SW5 9DH
SW5 9HX
SW5 9TQ
SW6 2NX
SW6 5TJ
SW6 1PA
SW6 5QD
SW6 1NS
SW6 1GA
SW6 2AJ
SW6 9GF
SW7 3QA
SW7 2RL
SW7 5PX
SW7 4SD
SW7 1TW
SW7 4DA
SW7 4AA
SW7 4DD
SW8 4JE
SW8 1QU
SW8 9DR
SW8 5JS
SW8 1HE
SW8 1LN
SW8 2BX
SW8 4DL
SW9 9QU
SW9 0UU
SW9 0PS
SW9 8TD
SW9 9HB
SW9 8QG
SW9 7PY
SW9 9DA
SW10 9UP
SW10 9HG
SW10 9UW
SW10 0RG
SW10 9HQ
SW10 0TY
SW10 9JH
SW10 9PF
SW11 3BT
SW11 6QE
SW11 6DS
SW11 6DF
SW11 5DS
SW11 5XG
SW11 5EF
SW11 5AN
SW12 0ES
SW12 8BX
SW12 0AH
SW12 8AD
SW12 8NJ
SW12 8NU
SW12 8HB
SW12 9JR
SW13 9HT
SW13 9LY
SW13 9HP
SW13 8RA
SW13 0HA
SW13 8QY
SW13 0LF
SW13 0EQ
SW14 9ES
SW14 7RJ
SW14 8NL
SW14 8LB
SW14 7JX
SW14 8NH
SW14 8DT
SW14 8HL
SW15 2QN
SW15 1QQ
SW15 1PQ
SW15 5HH
SW15 4AN
SW15 1SJ
SW15 5FB
SW15 5DY
SW16 6ET
SW16 5EF
SW16 1TE
SW16 5UX
SW16 3QE
SW16 1JS
SW16 6QE
SW16 5UF
SW17 0UL
SW17 9NH
SW17 7UD
SW17 9BB
SW17 8HN
SW17 0GD
SW17 0DB
SW17 9PD
SW18 1HH
SW18 4GQ
SW18 5BA
SW18 4JG
SW18 3JZ
SW18 4EF
SW18 5LY
SW18 1SW
SW19 2BH
SW19 9LS
SW19 8TZ
SW19 8RR
SW19 4RY
SW19 7PJ
SW19 7PA
SW19 6TQ
SW20 0UN
SW20 8JF
SW20 9BH
SW20 0QP
SW20 8PP
SW20 8NP
SW20 9AJ
SW20 9HH
SE1 7AD
SE1 5NR
SE1 4AY
SE1 4YL
SE1 0BH
SE1 8JB
SE1 7FX
SE1 7GG
SE2 9DD
SE2 9QG
SE2 9SW
SE2 0LE
SE2 0ND
SE2 9EQ
SE2 9FD
SE2 0HG
SE3 9FZ
SE3 7JQ
SE3 0XF
SE3 7AN
SE3 7HF
SE3 0EE
SE3 9RT
SE3 8DS
SE4 2AT
SE4 2PS
SE4 2QD
SE4 1HG
SE4 2AW
SE4 2BL
SE4 1AL
SE4 2FR
SE5 5SW
SE5 9QD
SE5 7RT
SE5 9RA
SE5 7JE
SE5 7BY
SE5 0SQ
SE5 9RP
SE6 9SD
SE6 1AY
SE6 4RH
SE6 1LA
SE6 4JN
SE6 4NJ
SE6 1LJ
SE6 4NN
SE7 8BL
SE7 8HB
SE7 8FD
SE7 8BL
SE7 8PQ
SE7 7AE
SE7 7DR
SE7 8DA
SE8 3DX
SE8 4BG
SE8 4RJ
SE8 4DY
SE8 4BG
SE8 3BT
SE8 5ES
SE8 3AZ
SE9 2LR
SE9 9FW
SE9 6DS
SE9 2EY
SE9 2ES
SE9 1RL
SE9 5PR
SE9 1NN
SE10 8EW
SE10 8HT
SE10 9GB
SE10 9UB
SE10 9PH
SE10 0UX
SE10 8FT
SE10 0TP
SE11 4DJ
SE11 5TW
SE11 4TJ
SE11 5AE
SE11 5QY
SE11 6TX
SE11 5LH
SE11 4AZ
SE12 0QA
SE12 8AT
SE12 8JH
SE12 8EZ
SE12 8UU
SE12 0JS
SE12 0HT
SE12 0BT
SE13 7NY
SE13 9DY
SE13 6DF
SE13 7RN
SE13 7FE
SE13 7SB
SE13 7PA
SE13 6EW
SE14 5HS
SE14 5NQ
SE14 6TJ
SE14 5RD
SE14 6EP
SE14 6PA
SE14 5XA
SE14 6AZ
SE15 6HT
SE15 4EL
SE15 5UN
SE15 3UH
SE15 6LH
SE15 5LX
SE15 1QP
SE15 4PQ
SE16 7RP
SE16 3AF
SE16 7PN
SE16 4DH
SE16 3UQ
SE16 4AF
SE16 5EF
SE16 2FR
SE17 2EG
SE17 3AA
SE17 3DN
SE17 2HZ
SE17 1PD
SE17 1EL
SE17 3SE
SE17 3TA
SE18 2QN
SE18 2PD
SE18 1ED
SE18 4JS
SE18 3TY
SE18 4AT
SE18 7RA
SE18 6SW
SE19 3UH
SE19 3HZ
SE19 2QU
SE19 2QP
SE19 3QJ
SE19 3SB
SE19 2JG
SE19 3NS
SE20 7JP
SE20 7RF
SE20 8QR
SE20 9FA
SE20 7UR
SE20 8SE
SE20 8HX
SE20 8SN
SE21 8SG
SE21 8EZ
SE21 7DE
SE21 8EG
SE21 7AN
SE21 7NA
SE21 8PZ
SE21 7AG
SE22 8GA
SE22 0JR
SE22 8NX
SE22 0SF
SE22 9LE
SE22 0DP
SE22 8EQ
SE22 0QN
SE23 3XJ
SE23 3TS
SE23 2DY
SE23 3YN
SE23 9GN
SE23 3BP
SE23 2JS
SE23 2JA
SE24 0EX
SE24 0PS
SE24 0NW
SE24 9LH
SE24 0JT
SE24 9QG
SE24 9NU
SE24 0BZ
SE25 6NQ
SE25 6RU
SE25 5PQ
SE25 5FR
SE25 6LZ
SE25 4NB
SE25 5DP
SE25 4SY
SE26 5RZ
SE26 4AZ
SE26 6PJ
SE26 5NR
SE26 5RY
SE26 6JU
SE26 5AY
SE26 5TP
N1 6LE
N1 6BJ
N1 1UQ
N1 7BG
N1 2SB
N1 1NL
N1 4EH
N1 2EN
N2 9RB
N2 9LQ
N2 9QB
N2 0BB
N2 0AB
N2 9JD
N2 0SW
N2 8JT
N4 4NP
N4 9NA
N4 3SG
N4 4EE
N4 2PZ
N4 4AT
N4 1ES
N4 3LL
N5 1EY
N5 2PJ
N5 1NE
N5 1AS
N5 1BF
N5 1HN
N5 1TU
N5 2JA
N6 5RG
N6 6AF
N6 4AP
N6 6BA
N6 4DS
N6 4NU
N6 5RL
N6 5HG
N7 7RL
N7 6EP
N7 0SA
N7 8JD
N7 0SD
N7 7FH
N7 7JU
N7 8AJ
N8 0NA
N8 9FD
N8 9DB
N8 9BN
N8 8QS
N8 9NS
N8 9BD
N8 9AR
N10 1LT
N10 3BU
N10 2QP
N10 1HS
N10 1NB
N10 1QN
N10 3YE
N10 1ER
N11 3AY
N11 1LH
N11 1AS
N11 1NU
N11 2EF
N11 3LP
N11 3PS
N11 2DQ
N12 8HS
N12 0AW
N12 2DP
N12 9RY
N12 0ES
N12 0LU
N12 0EB
N12 8QN
N13 4HS
N13 5UJ
N13 6EE
N13 5HR
N13 4RR
N13 5EX
N13 5DA
N13 5SS
N15 4GL
N15 4GA
N15 3JR
N15 4AR
N15 3AF
N15 6UY
N15 6AE
N15 3SS
N16 1NA
N16 8NH
N16 6LD
N16 0QU
N16 8AT
N16 8AY
N16 5QR
N16 5BF
N17 6NY
N17 8BJ
N17 9HY
N17 0SG
N17 0FL
N17 1EF
N17 0XP
N17 0HE
N19 4PP
N19 5JT
N19 4DX
N19 3UZ
N19 5NW
N19 4AL
N19 3QH
N19 3SF
N20 9AW
N20 0YN
N20 0DT
N20 8HS
N20 0XN
N20 0BJ
N20 8AJ
N20 8HJ
N21 1DU
N21 3BQ
N21 1NR
N21 2BT
N21 3AR
N21 2DT
N21 3JD
N21 1NP
N22 5SD
N22 5LP
N22 7SE
N22 7TJ
N22 6JQ
N22 8HR
N22 6AD
N22 5RY
NW1 8YD
NW1 0SE
NW1 7QX
NW1 7HY
NW1 6DB
NW1 9RZ
NW1 1BG
NW1 9GU
NW2 1JH
NW2 3RJ
NW2 7QN
NW2 2DL
NW2 3DD
NW2 3PE
NW2 2DB
NW2 5JP
NW3 6EH
NW3 5QA
NW3 4BY
NW3 6EF
NW3 7JN
NW3 7NU
NW3 4HB
NW3 5QJ
NW4 3JS
NW4 1DE
NW4 2NL
NW4 4EF
NW4 4XR
NW4 9DG
NW4 3XZ
NW4 1AQ
NW5 9TH
NW5 3DB
NW5 2AP
NW5 9RB
NW5 3DB
NW5 1ST
NW5 9LL
NW5 1JY
NW6 1RY
NW6 5SB
NW6 4JL
NW6 3DJ
NW6 3BB
NW6 3DN
NW6 4EN
NW6 5QW
NW7 4DP
NW7 1UE
NW7 1JS
NW7 4SH
NW7 1FT
NW7 1AU
NW7 1NH
NW7 2EG
NW8 0QU
NW8 7PP
NW8 9JH
NW8 8DT
NW8 7LH
NW8 8QA
NW8 8EB
NW8 0JS
NW9 5JQ
NW9 4AA
NW9 0LS
NW9 9HH
NW9 0NW
NW9 8HT
NW9 8JN
NW9 9AN
NW10 6UB
NW10 0BW
NW10 3JT
NW10 0PE
NW10 9AF
NW10 2UW
NW10 7JH
NW10 2SR
NW11 0RL
NW11 8DG
NW11 9RT
NW11 8TL
NW11 6AL
NW11 1BJ
NW11 9HJ
NW11 0SB
W2 3BE
W2 2LE
W2 2AB
W2 1SP
W2 4LL
W2 5XQ
W2 7HG
W2 1XA
W3 6GN
W3 6NZ
W3 7BU
W3 6FT
W3 6DF
W3 7FN
W3 0AB
W3 7SE
W4 5AL
W4 1PF
W4 3JS
W4 5RP
W4 4JP
W4 1TY
W4 2HE
W4 2JJ
W5 2EL
W5 1RH
W5 4AA
W5 4EP
W5 3LL
W5 2TB
W5 2TQ
W5 3PQ
W6 0LG
W6 9LE
W6 0LB
W6 7EW
W6 8QF
W6 0FY
W6 7HQ
W6 8ER
W7 3QE
W7 2JY
W7 3FH
W7 2NJ
W7 1PB
W7 3PE
W7 2EN
W7 3JZ
W8 7DZ
W8 4HL
W8 7RB
W8 5DG
W8 5BU
W8 5BS
W8 7JH
W8 6EB
W9 3JW
W9 4JZ
W9 3ED
W9 2AU
W9 1NX
W9 3RS
W9 1TJ
W9 2AW
W10 4LZ
W10 4AR
W10 5PZ
W10 6JN
W10 6QL
W10 5ER
W10 5XU
W10 6NZ
W11 1NL
W11 3HD
W11 3LX
W11 2PW
W11 4NG
W11 4QQ
W11 4QF
W11 2AH
W12 8BS
W12 0UJ
W12 0HX
W12 2EY
W12 9BL
W12 8DD
W12 8EN
W12 2HW
W13 3EN
W13 0FR
W13 9LF
W13 9EH
W13 9PL
W13 8ED
W13 0LG
W13 9NA
W14 9JP
W14 8LP
W14 8NB
W14 0JY
W14 8HL
W14 0BD
W14 0SR
W14 9XU
E1 4GS
E1 5HZ
E1 3NR
E1 1LT
E1 0PS
E1 2FY
E1 6JB
E1 1RH
E2 8LN
E2 9PN
E2 0AX
E2 7BD
E2 9BD
E2 8JP
E2 6GU
E2 6LY
E3 4LS
E3 5HQ
E3 3US
E3 5GH
E3 3UQ
E3 5QS
E3 5PR
E3 3SU
E4 0LF
E4 7PD
E4 7SR
E4 8JA
E4 7DQ
E4 8SB
E4 6PH
E4 8AQ
E5 8TS
E5 0HR
E5 8NZ
E5 0NP
E5 0TA
E5 8AN
E5 9JF
E5 0AZ
E6 3RD
E6 7AB
E6 6BA
E6 1AP
E6 5RY
E6 1DA
E6 3LB
E6 2DU
E7 7JA
E7 9AB
E7 0QT
E7 9AU
E7 8ER
E7 8LG
E7 0LR
E7 9LS
E8 3SH
E8 4EN
E8 1AW
E8 4QD
E8 4EG
E8 3QN
E8 3DU
E8 9GT
E9 7HG
E9 6RL
E9 6LG
E9 7LJ
E9 5YR
E9 6HP
E9 5DU
E9 6PX
E10 5TG
E10 7NN
E10 7BZ
E10 5GZ
E10 5BB
E10 6DB
E10 7JD
E10 6LX
E11 2BT
E11 9FG
E11 1PU
E11 1SF
E11 1AA
E11 1EN
E11 2DT
E11 3DD
E12 5BD
E12 5NA
E12 6SW
E12 5BF
E12 5NE
E12 6EN
E12 6EL
E12 5DP
E13 9GP
E13 0FD
E13 0RF
E13 0JN
E13 0DW
E13 9DX
E13 0AZ
E13 8PS
E14 4AY
E14 9RJ
E14 7LE
E14 9AG
E14 0NB
E14 2DR
E14 0GY
E14 3WD
E15 1AQ
E15 3EW
E15 2HY
E15 4QS
E15 4PY
E15 2ZD
E15 4QB
E15 1UD
E16 3NR
E16 1BL
E16 3HR
E16 2EY
E16 1YS
E16 2ZT
E16 1UP
E16 1QW
E17 4PJ
E17 3QY
E17 5DY
E17 7JT
E17 5DB
E17 6JJ
E17 9NX
E17 4HG
KT1 3RG
KT1 9GY
KT2 5PL
KT2 7FJ
KT3 4JY
KT3 3DJ
KT4 7SW
KT4 8LX
KT5 9RG
KT5 8ST
KT6 5RJ
KT6 4HA
KT7 0JZ
KT7 0YW
KT8 0DX
KT8 9BJ
KT9 2DT
KT9 1SN
KT10 0ET
KT10 9EW
KT12 5BU
KT12 4RP
KT13 0EF
KT13 9JJ
KT17 1JE
KT17 1TZ
KT20 7DN
KT20 5AR
KT22 8AT
KT22 0EL
TW1 1HT
TW1 4HY
TW2 7AG
TW2 7QZ
TW3 1SU
TW3 1AS
TW4 5PJ
TW4 7LE
TW7 6RD
TW7 7NE
TW9 4EU
TW9 2TL
TW10 7TT
TW10 6BS
TW11 9QH
TW11 9EQ
TW12 2ND
TW12 3RU
TW13 5QY
TW13 5HA
TW15 2ED
TW15 1UU
TW18 2DY
TW18 1PX
TW20 8LT
TW20 8DA
RH1 1DR
RH1 1BG
RH2 9LF
RH2 2HU
RH4 9TY
RH4 2DL
RH6 7AR
RH6 9JA
RH10 1WH
RH10 1WL
RH12 1JF
RH12 3HH
RH13 6EH
RH13 8LE
RH16 9TB
RH16 2JD
RH19 4LZ
RH19 2PD
GU1 4LZ
GU1 3ND
GU2 9UW
GU2 8AD
GU7 9SH
GU7 9AE
GU9 1PX
GU9 8AJ
GU14 7EW
GU14 4GT
GU21 2PB
GU21 6JJ
GU22 7JR
GU22 0DG
GU34 3SE
GU34 5JY
GU51 5SJ
GU51 1AW
SL1 5JN
SL1 6HB
SL3 0JT
SL3 3NG
SL4 2NZ
SL4 6BA
SL6 1EW
SL6 3PB
SL9 9AR
SL9 8LJ
HP1 9ST
HP1 2DE
HP2 5DD
HP2 6LF
HP5 2TA
HP5 3PE
HP9 2BA
HP9 1LZ
HP13 5HJ
HP13 6UJ
HP22 7DF
HP22 5AY
AL1 3PR
AL1 9NB
AL3 7JW
AL3 5PB
AL5 9EU
AL5 5UN
AL7 3TB
AL7 3HQ
AL9 6AF
AL9 5RF
SG1 1TX
SG1 6AS
SG4 8XG
SG4 7SB
SG7 6BP
SG7 6DT
SG12 8FR
SG12 0AG
SG14 2FP
SG14 3AQ
SG17 5DA
SG17 5JZ
EN1 1ET
EN1 3XG
EN3 6HT
EN3 6TN
EN5 2FL
EN5 4PR
EN7 5FF
EN7 6LP
WD3 3BZ
WD3 3EE
WD5 0LA
WD5 0GP
WD6 5NL
WD6 3JD
WD17 1NJ
WD17 4LP
WD23 2TX
WD23 2JL
WD25 0HX
WD25 7EA
OX1 1EW
OX1 1UH
OX2 9EY
OX2 9QX
OX3 9QB
OX3 8HW
OX4 1DX
OX4 1DQ
OX7 3NJ
OX7 3LX
OX14 5TH
OX14 9SJ
OX16 9PL
OX16 0QS
RG1 7DD
RG1 1PR
RG4 8LD
RG4 7TU
RG9 4LG
RG9 1EF
RG12 1FB
RG12 8GJ
RG21 5SS
RG21 3FB
RG27 9JH
RG27 8ED
B1 1NN
B5 7FN
B11 3RZ
B15 1BD
B17 9PU
B29 6EX
B38 0EB
B44 0JW
B63 3QS
B90 1ER
B91 2PJ
CV1 5PP
CV3 1GL
CV4 8DD
CV7 8LT
CV21 3EL
LE1 4FP
LE3 3GD
LE5 0SZ
LE11 1HA
NN1 5EZ
NN4 8TN
NN8 3TS
MK1 9HG
MK6 5BP
MK14 5EH
MK18 1WD
M1 5QD
M4 5FX
M14 7RD
M20 2PQ
M21 8HA
LS1 5JA
LS6 1NH
LS11 9BW
LS17 8BR
BD1 4PD
BD5 9RB
BD16 3JN
S1 4AW
S6 2TW
S10 1DB
S11 9LD
S17 3LH
S35 7DH
S60 4AW
S65 3HR`;
  parseCSV(document.getElementById('pasteArea').value);
}

// ── Brief generator ──────────────────────────────────────────────────────
function generateBrief() {
  if (!profileData) { alert('Run analysis first.'); return; }

  // Read branding from localStorage (set by launcher customise panel)
  let branding = {};
  try { branding = JSON.parse(localStorage.getItem('geoSuiteSettings') || '{}'); } catch(e) {}
  const agencyName  = branding.name      || 'Pratt Digital';
  const primary     = branding.primary   || '#aec90b';
  const secondary   = branding.secondary || '#f0a500';
  const logoSrc     = branding.logo      || '';

  const pd = profileData;
  const {enriched, avgImd, avgHp, pctRural, avgYoung, avgMid, avgOlder,
         avgProf, avgNoCar, avgDegree, lookalikes, underindex, total} = pd;
  const n = enriched.length;
  const matchPct = Math.round(n / (total||n) * 100);

  // Colour helpers
  function darken(hex, pct) {
    const num = parseInt(hex.slice(1),16), f = 1-pct/100;
    const r=Math.max(0,Math.round(((num>>16)&0xff)*f));
    const g=Math.max(0,Math.round(((num>>8)&0xff)*f));
    const b=Math.max(0,Math.round((num&0xff)*f));
    return '#'+[r,g,b].map(v=>v.toString(16).padStart(2,'0')).join('');
  }
  function lighten(hex, pct) {
    const num = parseInt(hex.slice(1),16), f = pct/100;
    const r=Math.min(255,Math.round(((num>>16)&0xff)+(255-((num>>16)&0xff))*f));
    const g=Math.min(255,Math.round(((num>>8)&0xff)+(255-((num>>8)&0xff))*f));
    const b=Math.min(255,Math.round((num&0xff)+(255-(num&0xff))*f));
    return '#'+[r,g,b].map(v=>v.toString(16).padStart(2,'0')).join('');
  }

  const primaryDark  = darken(primary, 15);
  const primaryLight = lighten(primary, 88);

  // Region distribution
  const regionDist = {};
  enriched.filter(e=>!['Scotland','Wales','Northern Ireland'].includes(e.region))
    .forEach(e=>{ regionDist[e.region]=(regionDist[e.region]||0)+1; });
  const regionSorted = Object.entries(regionDist).sort((a,b)=>b[1]-a[1]);
  const maxRegion = regionSorted[0]?.[1]||1;

  // Top districts
  const distCount = {};
  enriched.forEach(e=>{ distCount[e.district]=(distCount[e.district]||0)+1; });
  const topDists = Object.entries(distCount).sort((a,b)=>b[1]-a[1]).slice(0,10);
  const maxDist = topDists[0]?.[1]||1;

  // IMD distribution
  const imdDist = {};
  for(let i=1;i<=10;i++) imdDist[i]=0;
  enriched.forEach(e=>{ imdDist[e.imd]=(imdDist[e.imd]||0)+1; });

  // Top lookalikes and underindex
  const topLookalikes = (lookalikes||[]).slice(0,12);
  const topUnderindex = (underindex||[]).slice(0,10);

  // High priority recommendations
  const highRecs = [];
  try {
    const topRegion = regionSorted[0]?.[0]||'England';
    const recs = generateRecommendations(enriched, avgImd||5, avgHp||5, pctRural||20, lookalikes||[], topRegion);
    highRecs.push(...recs.filter(r=>r.priority==='high').slice(0,3));
    highRecs.push(...recs.filter(r=>r.priority==='mid').slice(0,2));
  } catch(e) {}

  const channelLabels = {
    'ch-meta':'Meta Ads','ch-google':'Google Ads','ch-ooh':'Out of Home',
    'ch-dm':'Direct Mail','ch-search':'Paid Search','ch-display':'Programmatic'
  };

  const today = new Date().toLocaleDateString('en-GB',{day:'numeric',month:'long',year:'numeric'});
  const dominantImd = Math.round(avgImd||5);
  const dominantHp  = Math.round(avgHp||5);
  const dominantAge = (avgOlder||35)>(avgMid||40)&&(avgOlder||35)>(avgYoung||25)?'55+'
    :(avgMid||40)>(avgYoung||25)?'35–54':'16–34';

  const briefHTML = `<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Customer Intelligence Brief — ${agencyName}</title>
<link href="https://fonts.googleapis.com/css2?family=DM+Sans:wght@300;400;500;600;700&display=swap" rel="stylesheet">
<style>
  :root { --primary:${primary}; --primary-dark:${primaryDark}; --primary-light:${primaryLight}; --secondary:${secondary}; --slate:#4c565c; --muted:#6b6960; --border:#e2e0d8; --bg:#f5f4f0; }
  * { box-sizing:border-box; margin:0; padding:0; }
  body { font-family:'DM Sans',sans-serif; color:#1a1a18; background:#fff; font-size:13px; line-height:1.6; }

  /* Print */
  @media print {
    body { font-size:11px; }
    .no-print { display:none !important; }
    .page-break { page-break-before:always; }
    .card { break-inside:avoid; }
  }
  @page { margin:15mm 15mm 15mm 15mm; size:A4; }

  /* Layout */
  .page { max-width:860px; margin:0 auto; padding:30px; }

  /* Header */
  .brief-header { display:flex; align-items:center; justify-content:space-between; padding-bottom:16px; border-bottom:4px solid var(--primary); margin-bottom:24px; }
  .header-logo { max-height:44px; max-width:180px; object-fit:contain; }
  .header-logo.placeholder { background:var(--primary); color:var(--slate); font-weight:700; font-size:14px; padding:8px 14px; border-radius:6px; display:flex; align-items:center; }
  .header-right { text-align:right; }
  .brief-title { font-size:20px; font-weight:700; color:var(--slate); }
  .brief-meta { font-size:11px; color:var(--muted); margin-top:3px; }

  /* Section headers */
  .section-title { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.08em; color:var(--primary-dark); margin:20px 0 10px; padding-bottom:5px; border-bottom:1px solid var(--border); }

  /* Stat grid */
  .stat-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:10px; margin-bottom:16px; }
  .stat-card { background:var(--bg); border-radius:8px; padding:10px 12px; border-left:3px solid var(--primary); }
  .stat-card h4 { font-size:9px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:var(--muted); margin-bottom:4px; }
  .stat-card .val { font-size:20px; font-weight:700; color:var(--slate); line-height:1; }
  .stat-card .sub { font-size:10px; color:var(--muted); margin-top:3px; }

  /* Portrait */
  .portrait-box { background:linear-gradient(135deg,${primaryLight},#eef1fc); border:1px solid var(--primary); border-radius:10px; padding:14px 18px; font-size:12px; line-height:1.9; margin-bottom:16px; }
  .portrait-box strong { color:var(--slate); }

  /* Bar charts */
  .bar-row { display:flex; align-items:center; gap:8px; margin-bottom:5px; font-size:11px; }
  .bar-label { min-width:120px; color:var(--muted); font-size:10px; }
  .bar-label.bold { font-weight:600; color:#1a1a18; }
  .bar-track { flex:1; height:5px; background:var(--border); border-radius:3px; overflow:hidden; }
  .bar-fill { height:100%; border-radius:3px; }
  .bar-val { min-width:32px; text-align:right; font-size:10px; font-weight:600; }

  /* Two col */
  .two-col { display:grid; grid-template-columns:1fr 1fr; gap:20px; }

  /* Lookalike grid */
  .match-grid { display:grid; grid-template-columns:repeat(4,1fr); gap:6px; }
  .match-item { background:var(--bg); border-radius:6px; padding:7px 9px; border-left:2px solid var(--primary); }
  .match-item .dist { font-family:monospace; font-size:12px; font-weight:700; }
  .match-item .score { font-size:10px; font-weight:700; color:var(--primary-dark); }
  .match-item .detail { font-size:9px; color:var(--muted); margin-top:2px; line-height:1.4; }

  /* Underindex grid */
  .under-grid { display:grid; grid-template-columns:repeat(3,1fr); gap:6px; }
  .under-item { background:#fdf0f0; border-radius:6px; padding:7px 9px; border-left:2px solid #e24b4a; }
  .under-item .dist { font-family:monospace; font-size:12px; font-weight:700; }
  .under-item .idx { font-size:10px; font-weight:700; color:#b91c1c; }
  .under-item .detail { font-size:9px; color:var(--muted); margin-top:2px; line-height:1.4; }

  /* Recs */
  .rec-item { border:1px solid var(--border); border-radius:8px; padding:10px 12px; margin-bottom:8px; border-left:3px solid var(--primary); }
  .rec-item.mid { border-left-color:var(--secondary); }
  .rec-channels { display:flex; gap:4px; flex-wrap:wrap; margin-bottom:5px; }
  .ch-badge { font-size:9px; font-weight:700; padding:2px 6px; border-radius:3px; }
  .ch-meta { background:#eef1fc; color:#2d5be3; }
  .ch-google { background:#fef9c3; color:#854d0e; }
  .ch-ooh { background:#f0fdf4; color:#166534; }
  .ch-dm { background:#fdf4ff; color:#7e22ce; }
  .ch-search { background:#fff7ed; color:#9a3412; }
  .ch-display { background:#f0f9ff; color:#0369a1; }
  .rec-title { font-size:12px; font-weight:600; margin-bottom:4px; }
  .rec-rationale { font-size:11px; color:var(--muted); line-height:1.6; }

  /* Print button */
  .print-bar { position:fixed; bottom:20px; right:20px; display:flex; gap:8px; z-index:100; }
  .print-btn { padding:10px 18px; border-radius:8px; border:none; cursor:pointer; font-family:'DM Sans',sans-serif; font-size:13px; font-weight:600; }
  .print-btn.primary { background:var(--primary); color:var(--slate); }
  .print-btn.secondary { background:#fff; color:var(--slate); border:1px solid var(--border); }

  /* Footer */
  .brief-footer { margin-top:24px; padding-top:12px; border-top:1px solid var(--border); display:flex; justify-content:space-between; font-size:10px; color:var(--muted); }
</style>
</head>
<body>
<div class="page">

  <!-- Header -->
  <div class="brief-header">
    ${logoSrc ? `<img class="header-logo" src="${logoSrc}" alt="${agencyName}">` : `<div class="header-logo placeholder">${agencyName}</div>`}
    <div class="header-right">
      <div class="brief-title">Customer Intelligence Brief</div>
      <div class="brief-meta">Generated ${today} · ${agencyName} · Geo Intelligence Suite</div>
    </div>
  </div>

  <!-- Pen portrait -->
  <div class="section-title">Customer pen portrait</div>
  <div class="portrait-box">${document.querySelector('.pen-portrait')?.innerHTML || 'Profile data not available.'}</div>

  <!-- At a glance -->
  <div class="section-title">At a glance</div>
  <div class="stat-grid">
    <div class="stat-card">
      <h4>Match rate</h4>
      <div class="val">${matchPct}%</div>
      <div class="sub">${n.toLocaleString()} of ${(total||n).toLocaleString()} postcodes</div>
    </div>
    <div class="stat-card">
      <h4>Deprivation avg</h4>
      <div class="val">Decile ${dominantImd}</div>
      <div class="sub">${IMD_LABELS[dominantImd]||''}</div>
    </div>
    <div class="stat-card">
      <h4>House prices avg</h4>
      <div class="val">Band ${dominantHp}/10</div>
      <div class="sub">${HP_LABELS[dominantHp]||''}</div>
    </div>
    <div class="stat-card">
      <h4>Dominant age</h4>
      <div class="val">${dominantAge}</div>
      <div class="sub">${Math.round(avgYoung||0)}% / ${Math.round(avgMid||0)}% / ${Math.round(avgOlder||0)}%</div>
    </div>
    <div class="stat-card">
      <h4>Professional occ.</h4>
      <div class="val">${Math.round(avgProf||0)}%</div>
      <div class="sub">Managers &amp; professionals</div>
    </div>
    <div class="stat-card">
      <h4>Degree-level</h4>
      <div class="val">${Math.round(avgDegree||0)}%</div>
      <div class="sub">Adult qualifications</div>
    </div>
    <div class="stat-card">
      <h4>No car households</h4>
      <div class="val">${Math.round(avgNoCar||0)}%</div>
      <div class="sub">${Math.round(avgNoCar||0)>30?'Urban-dependent':'Car-owning'}</div>
    </div>
    <div class="stat-card">
      <h4>Urban / rural</h4>
      <div class="val">${Math.round(100-(pctRural||0))}% urban</div>
      <div class="sub">${Math.round(pctRural||0)}% rural</div>
    </div>
  </div>

  <!-- Age & socioeconomic -->
  <div class="two-col">
    <div>
      <div class="section-title">Age distribution</div>
      ${[['16–34 (young)',Math.round(avgYoung||0),'#2d5be3'],['35–54 (mid)',Math.round(avgMid||0),'#18a058'],['55+ (older)',Math.round(avgOlder||0),'#f59e0b']].map(([l,v,c])=>`
        <div class="bar-row">
          <span class="bar-label ${v===Math.max(Math.round(avgYoung||0),Math.round(avgMid||0),Math.round(avgOlder||0))?'bold':''}">${l}</span>
          <div class="bar-track"><div class="bar-fill" style="width:${Math.min(100,v*2)}%;background:${c}"></div></div>
          <span class="bar-val">${v}%</span>
        </div>`).join('')}
    </div>
    <div>
      <div class="section-title">Socioeconomic indicators</div>
      ${[['Professional/managerial',Math.round(avgProf||0),'#9333ea'],['Degree-level qualified',Math.round(avgDegree||0),'#06b6d4'],['No car households',Math.round(avgNoCar||0),'#94a3b8']].map(([l,v,c])=>`
        <div class="bar-row">
          <span class="bar-label">${l}</span>
          <div class="bar-track"><div class="bar-fill" style="width:${Math.min(100,v*1.5)}%;background:${c}"></div></div>
          <span class="bar-val">${v}%</span>
        </div>`).join('')}
    </div>
  </div>

  <!-- Regional & districts -->
  <div class="two-col" style="margin-top:16px">
    <div>
      <div class="section-title">Regional breakdown</div>
      ${regionSorted.slice(0,6).map(([r,c],i)=>`
        <div class="bar-row">
          <span class="bar-label">${r}</span>
          <div class="bar-track"><div class="bar-fill" style="width:${Math.round(c/maxRegion*100)}%;background:${['#2d5be3','#e35b2d','#18a058','#9333ea','#f59e0b','#06b6d4'][i%6]}"></div></div>
          <span class="bar-val">${Math.round(c/n*100)}%</span>
        </div>`).join('')}
    </div>
    <div>
      <div class="section-title">Top postcode districts</div>
      ${topDists.map(([d,c])=>`
        <div class="bar-row">
          <span class="bar-label" style="font-family:monospace;font-size:11px">${d}</span>
          <div class="bar-track"><div class="bar-fill" style="width:${Math.round(c/maxDist*100)}%;background:var(--primary)"></div></div>
          <span class="bar-val">${c}</span>
        </div>`).join('')}
    </div>
  </div>

  ${topLookalikes.length ? `
  <!-- Lookalike gap districts -->
  <div class="section-title page-break">Gap districts — matching profile, no current presence</div>
  <div class="match-grid">
    ${topLookalikes.map(m=>`
      <div class="match-item">
        <div class="dist">${m.district}</div>
        <div class="score">Match ${m.score}%</div>
        <div class="detail">${m.stats.region}<br>IMD ${m.stats.imd} · HP ${m.stats.hp} · ${m.stats.rural?'Rural':'Urban'}<br>${m.stats.age_young||'?'}/${m.stats.age_mid||'?'}/${m.stats.age_older||'?'}% age</div>
      </div>`).join('')}
  </div>` : ''}

  ${topUnderindex.length ? `
  <!-- Underindex districts -->
  <div class="section-title">Underindexing districts — present but underperforming potential</div>
  <div class="under-grid">
    ${topUnderindex.map(r=>`
      <div class="under-item">
        <div class="dist">${r.district}</div>
        <div class="idx">Index ${r.index} · Gap ${r.gap}</div>
        <div class="detail">${r.stats.region}<br>Actual ${r.count} vs expected ${r.expectedCount}<br>Profile match ${r.profileScore}%</div>
      </div>`).join('')}
  </div>` : ''}

  ${highRecs.length ? `
  <!-- Channel recommendations -->
  <div class="section-title">Recommended channel mix</div>
  ${highRecs.map(r=>`
    <div class="rec-item ${r.priority}">
      <div class="rec-channels">
        ${r.channels.map(c=>`<span class="ch-badge ${c}">${channelLabels[c]||c}</span>`).join('')}
        <span style="margin-left:auto;font-size:9px;font-weight:600;color:${r.priority==='high'?'#145c38':'#b45309'};background:${r.priority==='high'?'#e8f7f0':'#fff7ed'};padding:2px 6px;border-radius:3px">${r.priority==='high'?'High priority':'Worth considering'}</span>
      </div>
      <div class="rec-title">${r.title}</div>
      <div class="rec-rationale">${r.rationale.replace(/<[^>]+>/g,'').slice(0,200)}${r.rationale.length>200?'…':''}</div>
    </div>`).join('')}` : ''}

  <!-- Footer -->
  <div class="brief-footer">
    <span>${agencyName} · Geo Intelligence Suite</span>
    <span>Generated ${today} · Confidential</span>
  </div>

</div>

<!-- Print controls -->
<div class="print-bar no-print">
  <button class="print-btn secondary" onclick="window.close()">✕ Close</button>
  <button class="print-btn primary" onclick="window.print()">🖨 Save as PDF</button>
</div>

</body>
</html>`;

  const w = window.open('', '_blank');
  w.document.write(briefHTML);
  w.document.close();
}

// ── District map ──────────────────────────────────────────────────────────
let districtMap     = null;
let districtMapOpen = false;
let districtMarkers = null;

function clearDistrictMap() {
  if (districtMarkers && districtMap) {
    districtMap.removeLayer(districtMarkers);
    districtMarkers = null;
  }
}

function toggleDistrictMap() {
  const panel = document.getElementById('district-map-panel');
  const btn   = document.getElementById('map-toggle-btn');
  districtMapOpen = !districtMapOpen;
  panel.classList.toggle('open', districtMapOpen);
  btn.textContent = districtMapOpen ? '🗺 Hide map' : '🗺 Show on map';
  if (districtMapOpen) renderDistrictMap();
}

function renderDistrictMap() {
  if (!districtMap) {
    districtMap = L.map('district-map').setView([52.5, -1.5], 6);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '© <a href="https://openstreetmap.org">OpenStreetMap</a>', maxZoom: 18
    }).addTo(districtMap);
  } else {
    if (districtMarkers) { districtMap.removeLayer(districtMarkers); districtMarkers = null; }
  }
  setTimeout(() => districtMap.invalidateSize(), 80);
  const isUnder = lookalikMode === 'under';
  const items   = isUnder ? (profileData?.underindex||[]) : (profileData?.lookalikes||[]);
  if (!items.length) return;

  const lg = L.layerGroup();
  const bounds = [];
  let plotted = 0;

  for (const item of items) {
    const d = DISTRICT_DATA[item.district];
    if (!d || d.lat == null) continue;
    bounds.push([d.lat, d.lng]);
    plotted++;
    let colour, popupBody;
    if (isUnder) {
      colour = item.index < 40 ? '#e24b4a' : item.index < 70 ? '#f59e0b' : '#18a058';
      popupBody = `<strong>${item.district}</strong> — ${item.stats.region}<br>
        <span style="color:${colour};font-weight:600">Index ${item.index}</span> · Profile ${item.profileScore}%<br>
        Actual: <strong>${item.count}</strong> · Expected: <strong>${item.expectedCount}</strong> · Gap: <strong>${item.gap}</strong><br>
        <small style="color:#888">IMD ${item.stats.imd} · HP ${item.stats.hp} · ${item.stats.rural?'Rural':'Urban'}</small>`;
    } else {
      colour = item.score >= 75 ? '#18a058' : item.score >= 55 ? '#f59e0b' : '#94a3b8';
      popupBody = `<strong>${item.district}</strong> — ${item.stats.region}<br>
        <span style="color:${colour};font-weight:600">Match ${item.score}%</span><br>
        Age: ${item.stats.age_young||'?'}% / ${item.stats.age_mid||'?'}% / ${item.stats.age_older||'?'}% · Prof ${item.stats.pct_prof||'?'}%<br>
        <small style="color:#888">IMD ${item.stats.imd} · HP ${item.stats.hp} · ${item.stats.rural?'Rural':'Urban'}</small>`;
    }
    const size = isUnder
      ? Math.max(10, Math.min(22, Math.round(item.gap/(items[0].gap||1)*16)+8))
      : Math.max(10, Math.min(20, Math.round(item.score/100*12)+8));
    L.circleMarker([d.lat, d.lng], {
      radius: size/2, fillColor: colour, color: '#fff', fillOpacity: 0.85, weight: 1.5
    }).bindPopup(popupBody).addTo(lg);
  }

  districtMarkers = lg.addTo(districtMap);
  document.getElementById('map-panel-title').textContent =
    isUnder ? `Underindexing districts — ${plotted} plotted` : `Gap districts — ${plotted} plotted`;
  document.getElementById('map-legend').textContent =
    isUnder ? 'Red = biggest gap · Amber = moderate · Green = mild'
            : 'Green = strong match · Amber = good · Grey = moderate';
  if (bounds.length) districtMap.fitBounds(bounds, {padding:[40,40]});
}

const _rl = renderLookalikes;
renderLookalikes = function(m) {
  _rl(m);
  const btn = document.getElementById('map-toggle-btn');
  if (btn) btn.style.display = m.length ? 'block' : 'none';
  if (districtMapOpen) renderDistrictMap();
};
const _ru = renderUnderindex;
renderUnderindex = function(r) {
  _ru(r);
  const btn = document.getElementById('map-toggle-btn');
  if (btn) btn.style.display = r.length ? 'block' : 'none';
  if (districtMapOpen) renderDistrictMap();
};

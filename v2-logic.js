// ════════════════════════════════════════════════════
// FoodShield AI v2 — Logic & Rendering
// ════════════════════════════════════════════════════

// ── State ─────────────────────────────────────────
let map, geoLayer;
let activeFilters = { region:'', risk:'', commodity:'' };
let activeScenarios = new Set();
let chartInstances = {};

function destroyChart(id) {
  if (chartInstances[id]) { chartInstances[id].destroy(); delete chartInstances[id]; }
}

// ── Tab routing ───────────────────────────────────
function showTab(tab, btn) {
  document.querySelectorAll('.tab-page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.nav-btn').forEach(b => b.classList.remove('active'));
  document.getElementById('tab-'+tab).classList.add('active');
  if (btn) btn.classList.add('active');
  if (tab==='country')  renderCountryTab();
  if (tab==='trade')    renderTradeTab();
  if (tab==='score')    renderScoreTab();
  if (tab==='forecast') renderForecastTab();
  if (tab==='scenario') renderScenarioTab();
}

// ── Search ────────────────────────────────────────
function handleSearch() {
  const q = document.getElementById('search-box').value.toLowerCase().trim();
  const ac = document.getElementById('autocomplete');
  if (!q) { ac.style.display='none'; return; }
  const hits = COUNTRIES.filter(c=>c.name.toLowerCase().includes(q)).slice(0,7);
  if (!hits.length) { ac.style.display='none'; return; }
  ac.style.display='block';
  ac.innerHTML = hits.map(c=>`
    <div class="ac-item" onclick="selectCountry('${c.iso}')">
      <span>${c.name}</span>
      <span class="ac-score" style="background:${riskColor(c.fdrs)}1a;color:${riskColor(c.fdrs)}">${c.fdrs}</span>
    </div>`).join('');
}
document.addEventListener('click', e=>{
  if (!e.target.closest('.search-wrap'))
    document.getElementById('autocomplete').style.display='none';
});
function selectCountry(iso) {
  const c = byISO(iso); if (!c) return;
  document.getElementById('autocomplete').style.display='none';
  document.getElementById('search-box').value = c.name;
  showTab('global', document.querySelector('.nav-btn'));
  openPanel(c);
  if (geoLayer) geoLayer.eachLayer(l=>{
    if (lookupFeature(l.feature)?.iso===iso) {
      try { map.fitBounds(l.getBounds(),{maxZoom:5,padding:[60,60]}); } catch(e){}
    }
  });
}

// ── Filters ───────────────────────────────────────
function applyFilters() {
  activeFilters.region    = document.getElementById('filter-region').value;
  activeFilters.risk      = document.getElementById('filter-risk').value;
  activeFilters.commodity = document.getElementById('filter-commodity').value;
  if (geoLayer) geoLayer.setStyle(styleFeature);
}

// ════════════════════════════════════════════════════
// MAP
// ════════════════════════════════════════════════════
function styleFeature(f) {
  const c = lookupFeature(f);
  if (!c) return { fillColor:'#1a2e4a', color:'#04080f', weight:.5, fillOpacity:.8 };
  let visible = true;
  if (activeFilters.region    && c.region!==activeFilters.region) visible=false;
  if (activeFilters.risk      && riskLabel(c.fdrs)!==activeFilters.risk) visible=false;
  if (activeFilters.commodity==='wheat'     && c.w<30)  visible=false;
  if (activeFilters.commodity==='rice'      && c.r<30)  visible=false;
  if (activeFilters.commodity==='maize'     && c.m<30)  visible=false;
  if (activeFilters.commodity==='fertilizer'&& c.c[2]<40) visible=false;
  if (!visible) return { fillColor:'#0f1d30', color:'#04080f', weight:.4, fillOpacity:.6 };
  return { fillColor:riskColor(c.fdrs), color:'#04080f', weight:.5, fillOpacity:.85 };
}

function initMap() {
  map = L.map('map',{center:[20,10],zoom:2.2,minZoom:1.5,maxZoom:8,zoomControl:true,worldCopyJump:false});
  L.tileLayer('https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png',{
    subdomains:'abcd',maxZoom:20
  }).addTo(map);

  fetch('https://raw.githubusercontent.com/holtzy/D3-graph-gallery/master/DATA/world.geojson')
    .then(r=>r.json()).then(data=>{
      geoLayer = L.geoJSON(data,{style:styleFeature,onEachFeature}).addTo(map);
      updateOverlayStats();
      updateRail();
    }).catch(()=>{
      fetch('https://raw.githubusercontent.com/datasets/geo-countries/master/data/countries.geojson')
        .then(r=>r.json()).then(data=>{
          geoLayer = L.geoJSON(data,{style:styleFeature,onEachFeature}).addTo(map);
          updateOverlayStats();
          updateRail();
        });
    });
}

function onEachFeature(f,layer) {
  const c = lookupFeature(f);
  const name = f.properties.name||f.properties.NAME||f.properties.ADMIN||'Unknown';
  layer.on({
    mouseover(e) {
      const l=e.target;
      l.setStyle({weight:2,color:'#3b82f6',fillOpacity:1});
      l.bringToFront();
      const tip = c
        ? `<b style="font-size:13px">${c.name}</b><br>
           FDRS: <b style="color:${riskColor(c.fdrs)}">${c.fdrs}</b> — ${riskLabel(c.fdrs)}<br>
           <span style="color:#64748b;font-size:10px">${c.region} · Food inflation: ${c.fi}%</span>`
        : `<b>${name}</b><br><span style="color:#475569;font-size:10px">No data available</span>`;
      layer.bindTooltip(tip,{sticky:true,opacity:1}).openTooltip();
    },
    mouseout(e) { geoLayer.resetStyle(e.target); layer.closeTooltip(); },
    click()     { if(c) openPanel(c); }
  });
}

function updateOverlayStats() {
  const n = COUNTRIES.length;
  const vuln = COUNTRIES.filter(c=>c.fdrs>75).length;
  const avg  = Math.round(COUNTRIES.reduce((s,c)=>s+c.fdrs,0)/n);
  document.getElementById('msp-countries').textContent = n;
  document.getElementById('msp-vulnerable').textContent = vuln;
  document.getElementById('msp-avg').textContent = avg;
}

// ════════════════════════════════════════════════════
// SIDE PANEL
// ════════════════════════════════════════════════════
function openPanel(c) {
  const sp = document.getElementById('sidepanel');
  sp.classList.remove('hidden');

  // Header
  document.getElementById('sp-name').textContent   = c.name;
  document.getElementById('sp-region').textContent = c.region;
  const col = riskColor(c.fdrs);
  document.getElementById('sp-header-bg').style.background =
    `linear-gradient(135deg, ${col}22, ${col}08)`;

  // Score
  document.getElementById('sp-score').textContent    = c.fdrs;
  document.getElementById('sp-score').style.color    = col;
  const badge = document.getElementById('sp-badge');
  badge.style.cssText = riskBadgeStyle(c.fdrs);
  badge.textContent   = riskLabel(c.fdrs);

  // Forecast arrow
  const trend = c.f2030 > c.fdrs ? '▲' : '▼';
  const tCol  = c.f2030 > c.fdrs ? '#ef4444' : '#22c55e';
  document.getElementById('sp-forecast').innerHTML =
    `<span class="trend-arrow" style="color:${tCol}">${trend}</span>
     <span style="color:#64748b">2030 forecast: </span>
     <b style="color:${riskColor(c.f2030)}">${c.f2030}</b>`;

  // Score bar
  const fill = document.getElementById('sp-score-fill');
  fill.style.width      = c.fdrs+'%';
  fill.style.background = col;
  fill.style.boxShadow  = `0 0 8px ${col}`;

  // Radar
  destroyChart('sp-radar');
  const ctx = document.getElementById('sp-radar');
  chartInstances['sp-radar'] = new Chart(ctx,{
    type:'radar',
    data:{
      labels:['Import Dep.','Supplier Conc.','Prod. Trend','Inflation','Climate','Conflict'],
      datasets:[{
        data:c.c,
        backgroundColor:col+'22',borderColor:col,
        borderWidth:2,pointBackgroundColor:col,pointRadius:3,pointHoverRadius:5
      }]
    },
    options:{
      responsive:true,maintainAspectRatio:true,
      plugins:{legend:{display:false}},
      scales:{r:{
        min:0,max:100,
        ticks:{stepSize:25,color:'#334155',font:{size:8},backdropColor:'transparent'},
        grid:{color:'#1a2e4a'},angleLines:{color:'#1a2e4a'},
        pointLabels:{color:'#64748b',font:{size:8.5}}
      }}
    }
  });

  // Components
  const compLabels = ['Staple Import Dep.','Supplier Concentration','Production Trend','Food Inflation','Climate Volatility','Conflict / Logistics'];
  const compWeights= ['30%','20%','15%','15%','10%','10%'];
  document.getElementById('sp-comp-list').innerHTML = c.c.map((v,i)=>`
    <div class="comp-row">
      <div class="comp-label">${compLabels[i]}</div>
      <div class="comp-weight">${compWeights[i]}</div>
      <div class="comp-track"><div class="comp-fill" style="width:${v}%;background:${riskColor(v)}"></div></div>
      <div class="comp-val" style="color:${riskColor(v)}">${v}</div>
    </div>`).join('');

  // Metrics
  document.getElementById('sp-metrics').innerHTML = `
    <div class="sp-row"><span class="k">Wheat import dep.</span><span class="v">${c.w}%</span></div>
    <div class="sp-row"><span class="k">Rice import dep.</span><span class="v">${c.r}%</span></div>
    <div class="sp-row"><span class="k">Food inflation</span><span class="v" style="color:${c.fi>20?'#ef4444':'#94a3b8'}">${c.fi}% / yr</span></div>
    <div class="sp-row"><span class="k">Net food trade</span><span class="v" style="color:${c.net>=0?'#22c55e':'#ef4444'}">${c.net>=0?'+':''}${(c.net/1000).toFixed(0)}B USD</span></div>`;

  // Imports
  document.getElementById('sp-imports').innerHTML =
    c.imports.map(i=>`<span class="import-tag">${i}</span>`).join('');

  // Suppliers
  document.getElementById('sp-suppliers').innerHTML = c.suppliers.map((s,i)=>`
    <div class="supplier-row">
      <div class="supplier-name">${s}</div>
      <div class="supplier-bar-wrap"><div class="supplier-bar-fill" style="width:${(c.supPct||[])[i]||20}%"></div></div>
      <div class="supplier-pct">${(c.supPct||[])[i]||'—'}%</div>
    </div>`).join('');

  document.getElementById('sp-ai').textContent = c.ai;

  document.getElementById('sp-outlook').innerHTML =
    `Projected FDRS by 2030: <b style="color:${riskColor(c.f2030)}">${c.f2030}/100</b>
     (${c.f2030>c.fdrs?'<span style="color:#ef4444">▲ deteriorating</span>':'<span style="color:#22c55e">▼ improving / stable</span>'}).`;

  document.getElementById('sp-actions').innerHTML =
    c.resp.split('·').map(a=>`<div style="padding:2px 0;">→ ${a.trim()}</div>`).join('');
}

function closePanel() {
  document.getElementById('sidepanel').classList.add('hidden');
}

// ════════════════════════════════════════════════════
// RIGHT RAIL
// ════════════════════════════════════════════════════
function updateRail() {
  // Distribution
  const cats = [0,0,0,0];
  COUNTRIES.forEach(c=>{
    if(c.fdrs<=25) cats[0]++;
    else if(c.fdrs<=50) cats[1]++;
    else if(c.fdrs<=75) cats[2]++;
    else cats[3]++;
  });
  const labels=['Resilient','Exposed','Dependent','Vulnerable'];
  const colors=['#22c55e','#eab308','#f97316','#ef4444'];
  document.getElementById('dist-list').innerHTML = cats.map((n,i)=>`
    <div class="dist-row">
      <div class="dist-label" style="color:${colors[i]}">● ${labels[i]}</div>
      <div class="dist-track"><div class="dist-fill" style="background:${colors[i]};width:${n/COUNTRIES.length*100}%"></div></div>
      <div class="dist-count">${n}</div>
    </div>`).join('');

  // Vulnerable
  const sorted=[...COUNTRIES].sort((a,b)=>b.fdrs-a.fdrs);
  document.getElementById('rail-vulnerable').innerHTML = sorted.slice(0,7).map((c,i)=>`
    <div class="rank-row" onclick="openPanel(COUNTRIES.find(x=>x.iso==='${c.iso}'))">
      <div class="rank-num">${i+1}</div>
      <div class="rank-name">${c.name}</div>
      <div class="rank-score" style="background:${riskColor(c.fdrs)}1a;color:${riskColor(c.fdrs)}">${c.fdrs}</div>
    </div>`).join('');

  // Resilient
  const res=[...COUNTRIES].sort((a,b)=>a.fdrs-b.fdrs);
  document.getElementById('rail-resilient').innerHTML = res.slice(0,7).map((c,i)=>`
    <div class="rank-row" onclick="openPanel(COUNTRIES.find(x=>x.iso==='${c.iso}'))">
      <div class="rank-num">${i+1}</div>
      <div class="rank-name">${c.name}</div>
      <div class="rank-score" style="background:${riskColor(c.fdrs)}1a;color:${riskColor(c.fdrs)}">${c.fdrs}</div>
    </div>`).join('');

  // Insights
  const avgWheat = Math.round(COUNTRIES.reduce((s,c)=>s+c.w,0)/COUNTRIES.length);
  const highFI   = COUNTRIES.filter(c=>c.fi>30).length;
  const risingDep= COUNTRIES.filter(c=>c.f2030>c.fdrs+5).length;
  document.getElementById('rail-insights').innerHTML = [
    {icon:'🌾',text:`<b>${avgWheat}%</b> average wheat import dependency globally`},
    {icon:'📈',text:`<b>${highFI} countries</b> with food inflation above 30%`},
    {icon:'⚠️',text:`<b>${risingDep} countries</b> projected to deteriorate by 2030`},
    {icon:'🌍',text:`<b>${COUNTRIES.filter(c=>c.fdrs>75).length} countries</b> currently structurally vulnerable`},
  ].map(i=>`<div class="insight-card"><div class="insight-icon">${i.icon}</div><div class="insight-text">${i.text}</div></div>`).join('');
}

// ════════════════════════════════════════════════════
// COUNTRY TAB
// ════════════════════════════════════════════════════
function populateSelects() {
  const sorted = [...COUNTRIES].sort((a,b)=>a.name.localeCompare(b.name));
  const opts = sorted.map(c=>`<option value="${c.iso}">${c.name}</option>`).join('');
  ['country-sel','trade-sel','forecast-sel','cmp-a','cmp-b'].forEach(id=>{
    const el = document.getElementById(id); if(el) el.innerHTML=opts;
  });
  document.getElementById('country-sel').value  = 'EGY';
  document.getElementById('trade-sel').value    = 'EGY';
  document.getElementById('forecast-sel').value = 'EGY';
  if(document.getElementById('cmp-a')) document.getElementById('cmp-a').value='EGY';
  if(document.getElementById('cmp-b')) document.getElementById('cmp-b').value='NLD';
}

function renderCountryTab() {
  const c = byISO(document.getElementById('country-sel').value); if(!c) return;
  const col = riskColor(c.fdrs);
  const compLabels=['Staple Import Dep.','Supplier Concentration','Production Trend','Food Inflation','Climate Volatility','Conflict / Logistics'];
  const compWeights=['30%','20%','15%','15%','10%','10%'];

  document.getElementById('country-body').innerHTML = `
    <div class="grid-4" style="margin-bottom:4px;">
      ${c.c.map((v,i)=>`
        <div class="metric-card" style="--accent:${riskColor(v)}">
          <div class="metric-label">${compLabels[i]}</div>
          <div class="metric-value" style="color:${riskColor(v)}">${v}</div>
          <div class="metric-sub">${compWeights[i]} weight</div>
        </div>`).join('')}
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-title">Risk Radar</div>
        <div style="max-height:240px;display:flex;justify-content:center;">
          <canvas id="radar-ct" style="max-height:240px;max-width:320px;"></canvas>
        </div>
      </div>
      <div class="card">
        <div class="card-title">FDRS Score Breakdown</div>
        ${c.c.map((v,i)=>`
          <div class="comp-row">
            <div class="comp-label">${compLabels[i]}</div>
            <div class="comp-weight">${compWeights[i]}</div>
            <div class="comp-track" style="width:140px"><div class="comp-fill" style="width:${v}%;background:${riskColor(v)}"></div></div>
            <div class="comp-val" style="color:${riskColor(v)}">${v}</div>
          </div>`).join('')}
        <div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--b1);display:flex;align-items:center;gap:12px;">
          <span style="font-size:13px;color:var(--t3)">Total FDRS</span>
          <span style="font-size:36px;font-weight:900;font-family:'JetBrains Mono';color:${col};letter-spacing:-2px">${c.fdrs}</span>
          <span class="risk-badge" style="${riskBadgeStyle(c.fdrs)}">${riskLabel(c.fdrs)}</span>
        </div>
      </div>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-title">Top Food Imports</div>
        ${c.imports.map((im,i)=>`
          <div style="display:flex;align-items:center;gap:10px;padding:6px 0;border-bottom:1px solid var(--b1);">
            <span style="font-size:10px;color:var(--t3);font-family:'JetBrains Mono';width:14px">${i+1}</span>
            <span style="font-size:12px;color:var(--t1)">${im}</span>
          </div>`).join('')}
      </div>
      <div class="card">
        <div class="card-title">Top Supplier Countries</div>
        ${c.suppliers.map((s,i)=>`
          <div class="supplier-row" style="padding:5px 0;border-bottom:1px solid var(--b1);">
            <div class="supplier-name">${s}</div>
            <div class="supplier-bar-wrap" style="width:100px"><div class="supplier-bar-fill" style="width:${(c.supPct||[])[i]||20}%"></div></div>
            <div class="supplier-pct">${(c.supPct||[])[i]||'—'}%</div>
          </div>`).join('')}
        <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--b1);">
          <div class="sp-row"><span class="k">Wheat import dep.</span><span class="v">${c.w}%</span></div>
          <div class="sp-row"><span class="k">Rice import dep.</span><span class="v">${c.r}%</span></div>
          <div class="sp-row"><span class="k">Food inflation</span><span class="v" style="color:${c.fi>20?'#ef4444':'#94a3b8'}">${c.fi}%/yr</span></div>
          <div class="sp-row"><span class="k">Net food trade</span><span class="v" style="color:${c.net>=0?'#22c55e':'#ef4444'}">${c.net>=0?'+':''}${(c.net/1000).toFixed(0)}B USD</span></div>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Production vs Consumption Trend</div>
      <div style="height:220px;"><canvas id="prodchart-ct"></canvas></div>
    </div>

    <div class="card" style="border-left:3px solid var(--blue);">
      <div class="card-title">✦ AI Analysis</div>
      <p style="font-size:13px;color:var(--t2);line-height:1.7;">${c.ai}</p>
      <div style="margin-top:14px;padding-top:12px;border-top:1px solid var(--b1);display:grid;grid-template-columns:1fr 1fr;gap:16px;">
        <div>
          <div style="font-size:9px;font-weight:700;color:var(--t3);text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px;">Main Risk</div>
          <div style="font-size:12px;color:var(--t2)">${c.risk}</div>
        </div>
        <div>
          <div style="font-size:9px;font-weight:700;color:var(--t3);text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px;">2030 Outlook</div>
          <div style="font-size:12px;color:${riskColor(c.f2030)}">FDRS → ${c.f2030} / 100 ${c.f2030>c.fdrs?'▲':'▼'}</div>
        </div>
      </div>
      <div style="margin-top:12px;padding-top:10px;border-top:1px solid var(--b1);">
        <div style="font-size:9px;font-weight:700;color:var(--t3);text-transform:uppercase;letter-spacing:.8px;margin-bottom:6px;">Recommended Actions</div>
        <div style="font-size:12px;color:var(--t2);line-height:1.7;">${c.resp.split('·').map(a=>`→ ${a.trim()}`).join('<br>')}</div>
      </div>
    </div>`;

  setTimeout(()=>{
    destroyChart('radar-ct');
    chartInstances['radar-ct'] = mkRadar('radar-ct', c, c.name);
    destroyChart('prodchart-ct');
    chartInstances['prodchart-ct'] = mkProdChart('prodchart-ct', c);
  },50);
}

// ════════════════════════════════════════════════════
// TRADE TAB
// ════════════════════════════════════════════════════
function renderTradeTab() {
  const c = byISO(document.getElementById('trade-sel').value); if(!c) return;

  // Mock trade data derived from country data
  const importBars = c.imports.map((name,i)=>{
    const val = Math.round((c.net<0 ? Math.abs(c.net)*0.8 : 500) * [0.35,0.25,0.18,0.12,0.10][i] / 100);
    return {name, val: Math.max(val,10)};
  });
  const exportItems = c.net>0
    ? ['Soybeans','Beef','Wheat','Sugar','Poultry'].slice(0,5)
    : ['Petroleum products','Minerals','Textiles','Coffee','Vegetables'].slice(0,5);

  document.getElementById('trade-body').innerHTML = `
    <div class="grid-2">
      <div class="card">
        <div class="card-title">Top 5 Food Imports</div>
        ${c.imports.map((name,i)=>{
          const pct = [35,25,18,12,10][i];
          return `<div style="padding:7px 0;border-bottom:1px solid var(--b1);">
            <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
              <span style="font-size:12px">${name}</span>
              <span style="font-size:11px;color:var(--t3);font-family:'JetBrains Mono'">${pct}%</span>
            </div>
            <div style="height:4px;background:var(--bg4);border-radius:2px;overflow:hidden;">
              <div style="height:100%;width:${pct}%;background:#ef4444;border-radius:2px;"></div>
            </div>
          </div>`;
        }).join('')}
        <div style="margin-top:10px;padding-top:8px;border-top:1px solid var(--b1);display:flex;justify-content:space-between;">
          <span style="font-size:11px;color:var(--t3)">Total food imports</span>
          <span style="font-size:12px;font-weight:700;color:#ef4444;font-family:'JetBrains Mono'">${Math.abs(Math.min(c.net,0)/1000).toFixed(1)}B USD</span>
        </div>
      </div>

      <div class="card">
        <div class="card-title">Top 5 Food Exports</div>
        ${exportItems.map((name,i)=>{
          const pct = [40,28,16,10,6][i];
          return `<div style="padding:7px 0;border-bottom:1px solid var(--b1);">
            <div style="display:flex;justify-content:space-between;margin-bottom:4px;">
              <span style="font-size:12px">${name}</span>
              <span style="font-size:11px;color:var(--t3);font-family:'JetBrains Mono'">${pct}%</span>
            </div>
            <div style="height:4px;background:var(--bg4);border-radius:2px;overflow:hidden;">
              <div style="height:100%;width:${pct}%;background:#22c55e;border-radius:2px;"></div>
            </div>
          </div>`;
        }).join('')}
        <div style="margin-top:10px;padding-top:8px;border-top:1px solid var(--b1);display:flex;justify-content:space-between;">
          <span style="font-size:11px;color:var(--t3)">Total food exports</span>
          <span style="font-size:12px;font-weight:700;color:#22c55e;font-family:'JetBrains Mono'">${Math.max(c.net,0)/1000 > 0 ? (c.net/1000).toFixed(1) : '< 1'}B USD</span>
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Supplier Concentration Analysis</div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:24px;align-items:center;">
        <div>
          <canvas id="supplier-pie" style="max-height:200px;"></canvas>
        </div>
        <div>
          <div style="margin-bottom:12px;">
            <div style="font-size:10px;color:var(--t3);margin-bottom:4px;text-transform:uppercase;letter-spacing:.6px;">HHI Concentration Index</div>
            <div style="font-size:32px;font-weight:800;font-family:'JetBrains Mono';color:${riskColor(c.c[1])}">${c.c[1]}</div>
            <div style="font-size:11px;color:var(--t3);margin-top:2px;">${c.c[1]>60?'High concentration risk':c.c[1]>30?'Moderate concentration':'Well diversified'}</div>
          </div>
          ${c.suppliers.map((s,i)=>`
            <div class="supplier-row" style="padding:4px 0;">
              <div class="supplier-name" style="font-size:11px">${s}</div>
              <div class="supplier-bar-wrap" style="width:100px"><div class="supplier-bar-fill" style="width:${(c.supPct||[])[i]||20}%"></div></div>
              <div class="supplier-pct">${(c.supPct||[])[i]||'—'}%</div>
            </div>`).join('')}
        </div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Import / Export Trend Over Time</div>
      <div style="height:220px;"><canvas id="trade-trend-chart"></canvas></div>
    </div>

    <div class="card">
      <div class="card-title">Net Trade Balance</div>
      <div style="display:flex;align-items:center;gap:24px;padding:8px 0;">
        <div>
          <div style="font-size:9px;color:var(--t3);text-transform:uppercase;letter-spacing:.7px;margin-bottom:4px;">Net Position</div>
          <div style="font-size:40px;font-weight:900;font-family:'JetBrains Mono';color:${c.net>=0?'#22c55e':'#ef4444'};letter-spacing:-2px">
            ${c.net>=0?'+':''}${(c.net/1000).toFixed(1)}B
          </div>
          <div style="font-size:11px;color:var(--t3)">USD annually</div>
        </div>
        <div style="flex:1;height:8px;background:var(--bg4);border-radius:4px;overflow:hidden;position:relative;">
          <div style="position:absolute;left:50%;top:0;bottom:0;width:2px;background:var(--b3);z-index:1;"></div>
          ${c.net>=0
            ? `<div style="position:absolute;left:50%;top:0;bottom:0;width:${Math.min(Math.abs(c.net)/2000,45)}%;background:#22c55e;border-radius:4px;"></div>`
            : `<div style="position:absolute;right:50%;top:0;bottom:0;width:${Math.min(Math.abs(c.net)/2000,45)}%;background:#ef4444;border-radius:4px;"></div>`}
        </div>
        <div style="text-align:right">
          <div style="font-size:11px;color:var(--t3)">Status: ${c.net>=0?'<span style="color:#22c55e">Net Exporter</span>':'<span style="color:#ef4444">Net Importer</span>'}</div>
        </div>
      </div>
    </div>`;

  setTimeout(()=>{
    destroyChart('supplier-pie');
    chartInstances['supplier-pie'] = new Chart(document.getElementById('supplier-pie'),{
      type:'doughnut',
      data:{
        labels:c.suppliers,
        datasets:[{data:c.supPct||[20,20,20,20,20],
          backgroundColor:['#3b82f6','#14b8a6','#f97316','#eab308','#8b5cf6'],
          borderWidth:0,hoverOffset:4}]
      },
      options:{responsive:true,maintainAspectRatio:true,
        plugins:{legend:{position:'right',labels:{color:'#64748b',font:{size:10},padding:8}},
        tooltip:{callbacks:{label:ctx=>`${ctx.label}: ${ctx.raw}%`}}}}
    });
    destroyChart('trade-trend-chart');
    chartInstances['trade-trend-chart'] = mkTradeTrendChart('trade-trend-chart',c);
  },50);
}

// ════════════════════════════════════════════════════
// SCORE TAB
// ════════════════════════════════════════════════════
function renderScoreTab() {
  const sorted = [...COUNTRIES].sort((a,b)=>b.fdrs-a.fdrs);
  document.getElementById('score-body').innerHTML = `
    <div class="grid-2">
      <div class="card">
        <div class="card-title">FDRS Formula</div>
        <div class="formula-box">
FDRS = (Import Dependency  × <b>30%</b>)
     + (Supplier Concentration × <b>20%</b>)
     + (Production Trend       × <b>15%</b>)
     + (Food Inflation          × <b>15%</b>)
     + (Climate Volatility      × <b>10%</b>)
     + (Conflict / Logistics    × <b>10%</b>)
        </div>
      </div>
      <div class="card">
        <div class="card-title">Risk Categories</div>
        ${[['0–25','Resilient','Diversified, stable, low import reliance','#22c55e'],
           ['26–50','Exposed','Moderate reliance, manageable risk','#eab308'],
           ['51–75','Dependent','High import reliance, supply gaps growing','#f97316'],
           ['76–100','Structurally Vulnerable','Extreme exposure, compounded shocks','#ef4444']]
          .map(([r,l,d,col])=>`
          <div style="display:flex;align-items:flex-start;gap:10px;padding:8px 0;border-bottom:1px solid var(--b1);">
            <div style="width:28px;padding:2px 6px;border-radius:3px;background:${col}1a;color:${col};font-size:9px;font-weight:700;text-align:center;flex-shrink:0;margin-top:1px;">${r}</div>
            <div><div style="font-size:12px;font-weight:600;color:${col}">${l}</div><div style="font-size:10px;color:var(--t3);margin-top:1px">${d}</div></div>
          </div>`).join('')}
      </div>
    </div>

    <div class="card">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;">
        <div class="card-title" style="margin-bottom:0">Global Ranking Table</div>
        <input class="fs-search" type="text" id="table-search" placeholder="Filter…" oninput="filterTable()" style="width:160px;margin-left:auto;">
      </div>
      <div style="overflow-x:auto;max-height:340px;overflow-y:auto;">
        <table class="fs-table">
          <thead><tr>
            <th>#</th><th>Country</th><th>Region</th><th>FDRS</th>
            <th>Imp Dep</th><th>Sup Conc</th><th>Prod</th><th>Inflat</th><th>Climate</th><th>Conflict</th>
            <th>2030 →</th>
          </tr></thead>
          <tbody id="ranking-tbody">
            ${sorted.map((c,i)=>`
              <tr onclick="openPanel(byISO('${c.iso}'));showTab('global',document.querySelector('.nav-btn'))">
                <td style="color:var(--t3);font-family:'JetBrains Mono'">${i+1}</td>
                <td><b>${c.name}</b></td>
                <td style="color:var(--t3);font-size:11px">${c.region}</td>
                <td><span class="score-pill" style="background:${riskColor(c.fdrs)}1a;color:${riskColor(c.fdrs)}">${c.fdrs}</span></td>
                ${c.c.map(v=>`<td style="color:${riskColor(v)};font-family:'JetBrains Mono';font-size:11px">${v}</td>`).join('')}
                <td><span style="color:${riskColor(c.f2030)};font-weight:700;font-size:11px;font-family:'JetBrains Mono'">${c.f2030}${c.f2030>c.fdrs?'▲':'▼'}</span></td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Country Comparison</div>
      <div style="display:flex;gap:12px;align-items:center;margin-bottom:16px;">
        <select class="fs-select" id="cmp-a" onchange="renderComparison()" style="font-size:12px;padding:7px 28px 7px 12px;"></select>
        <span style="color:var(--t3);font-weight:700">vs</span>
        <select class="fs-select" id="cmp-b" onchange="renderComparison()" style="font-size:12px;padding:7px 28px 7px 12px;"></select>
      </div>
      <div class="grid-2">
        <div style="text-align:center;"><canvas id="radar-a" style="max-height:220px;"></canvas></div>
        <div style="text-align:center;"><canvas id="radar-b" style="max-height:220px;"></canvas></div>
      </div>
      <div id="cmp-table" style="margin-top:16px;"></div>
    </div>

    <div class="card">
      <div class="card-title">2×2 Dependency Matrix</div>
      <div style="font-size:10px;color:var(--t3);margin-bottom:12px">
        X axis: Domestic system resilience (higher = more resilient) &nbsp;|&nbsp; Y axis: Food import dependency
      </div>
      <div style="height:360px;position:relative;">
        <canvas id="matrix-chart"></canvas>
      </div>
    </div>`;

  // Populate comparison dropdowns then render
  const sorted2 = [...COUNTRIES].sort((a,b)=>a.name.localeCompare(b.name));
  const opts = sorted2.map(c=>`<option value="${c.iso}">${c.name}</option>`).join('');
  document.getElementById('cmp-a').innerHTML = opts;
  document.getElementById('cmp-b').innerHTML = opts;
  document.getElementById('cmp-a').value = 'EGY';
  document.getElementById('cmp-b').value = 'NLD';

  setTimeout(()=>{ renderComparison(); renderMatrix(); },60);
}

function filterTable() {
  const q = document.getElementById('table-search').value.toLowerCase();
  document.querySelectorAll('#ranking-tbody tr').forEach(tr=>{
    tr.style.display = tr.textContent.toLowerCase().includes(q)?'':'none';
  });
}

function renderComparison() {
  const a = byISO(document.getElementById('cmp-a')?.value); if(!a) return;
  const b = byISO(document.getElementById('cmp-b')?.value); if(!b) return;
  destroyChart('radar-a'); chartInstances['radar-a'] = mkRadar('radar-a',a,a.name);
  destroyChart('radar-b'); chartInstances['radar-b'] = mkRadar('radar-b',b,b.name);

  const labels=['Import Dep.','Supplier Conc.','Prod. Trend','Inflation','Climate','Conflict','FDRS Total'];
  const av=[...a.c,a.fdrs], bv=[...b.c,b.fdrs];
  document.getElementById('cmp-table').innerHTML = `
    <table class="fs-table">
      <thead><tr><th>Component</th><th>${a.name}</th><th>${b.name}</th><th>Δ</th></tr></thead>
      <tbody>${labels.map((l,i)=>{
        const d=av[i]-bv[i];
        return `<tr>
          <td>${l}</td>
          <td><span style="color:${riskColor(av[i])};font-weight:700;font-family:'JetBrains Mono'">${av[i]}</span></td>
          <td><span style="color:${riskColor(bv[i])};font-weight:700;font-family:'JetBrains Mono'">${bv[i]}</span></td>
          <td style="color:${d>0?'#ef4444':d<0?'#22c55e':'#475569'};font-weight:700;font-family:'JetBrains Mono'">${d>0?'+':''}${d}</td>
        </tr>`;}).join('')}</tbody>
    </table>`;
}

function renderMatrix() {
  const canvas = document.getElementById('matrix-chart'); if(!canvas) return;
  destroyChart('matrix-chart');
  const data = COUNTRIES.map(c=>({
    x: 100-Math.round((c.c[2]+c.c[5])/2),
    y: c.c[0],
    r: 7,
    label:c.name, col:riskColor(c.fdrs), iso:c.iso
  }));
  chartInstances['matrix-chart'] = new Chart(canvas,{
    type:'bubble',
    data:{datasets:[{data,
      backgroundColor:data.map(d=>d.col+'99'),
      borderColor:data.map(d=>d.col),
      borderWidth:1
    }]},
    options:{
      responsive:true,maintainAspectRatio:false,
      plugins:{legend:{display:false},
        tooltip:{callbacks:{label:ctx=>{const d=ctx.raw;return `${d.label} — Resilience:${d.x} / Import dep:${d.y}`;}}},
      },
      scales:{
        x:{min:0,max:100,grid:{color:'#1a2e4a'},ticks:{color:'#334155'},
           title:{display:true,text:'Domestic System Resilience →',color:'#475569',font:{size:10}}},
        y:{min:0,max:100,grid:{color:'#1a2e4a'},ticks:{color:'#334155'},
           title:{display:true,text:'Food Import Dependency ↑',color:'#475569',font:{size:10}}}
      }
    }
  });
}

// ════════════════════════════════════════════════════
// FORECAST TAB
// ════════════════════════════════════════════════════
function renderForecastTab() {
  const c = byISO(document.getElementById('forecast-sel').value); if(!c) return;
  const t = c.trend;
  const lastProd = t.production[t.production.length-2];
  const lastCons = t.consumption[t.consumption.length-2];
  const gap = lastCons - lastProd;
  const proj2030imp = c.f2030;
  const trendDir = c.f2030>c.fdrs?'deteriorating':'improving';
  const col = riskColor(c.f2030);

  document.getElementById('forecast-body').innerHTML = `
    <div class="grid-4">
      <div class="metric-card" style="--accent:${col}">
        <div class="metric-label">2030 FDRS Forecast</div>
        <div class="metric-value" style="color:${col}">${c.f2030}</div>
        <div class="metric-sub">${trendDir} from ${c.fdrs} today</div>
      </div>
      <div class="metric-card" style="--accent:#ef4444">
        <div class="metric-label">2030 Import Dep. (est.)</div>
        <div class="metric-value" style="color:${riskColor(c.c[0]+5)}">${Math.min(100,c.c[0]+Math.round((c.f2030-c.fdrs)*0.4))}%</div>
        <div class="metric-sub">vs ${c.c[0]}% today</div>
      </div>
      <div class="metric-card" style="--accent:#f97316">
        <div class="metric-label">Production-Consumption Gap</div>
        <div class="metric-value" style="color:${gap>0?'#ef4444':'#22c55e'}">${gap>0?'+':''}{${Math.round(gap/1000)}K t</div>
        <div class="metric-sub">${gap>0?'Deficit growing':'Surplus maintained'}</div>
      </div>
      <div class="metric-card" style="--accent:#eab308">
        <div class="metric-label">Food Inflation Trend</div>
        <div class="metric-value" style="color:${c.fi>15?'#ef4444':'#22c55e'}">${c.fi}%</div>
        <div class="metric-sub">Current annual rate</div>
      </div>
    </div>

    <div class="card">
      <div class="card-title">Production vs Consumption — Historical & 2030 Projection</div>
      <div style="height:240px;"><canvas id="forecast-main"></canvas></div>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-title">Import / Export Trend</div>
        <div style="height:200px;"><canvas id="forecast-trade"></canvas></div>
      </div>
      <div class="card">
        <div class="card-title">Population Growth vs Food Supply</div>
        <div style="height:200px;"><canvas id="forecast-pop"></canvas></div>
      </div>
    </div>

    <div class="card" style="border-left:3px solid ${col};">
      <div class="card-title">✦ AI Trend Interpretation</div>
      <div style="font-size:13px;color:var(--t2);line-height:1.7;">
        ${c.f2030>c.fdrs
          ? `If current trends continue, <b style="color:var(--t1)">${c.name}</b>'s Food Dependency Risk Score is projected to rise from <b style="color:${riskColor(c.fdrs)}">${c.fdrs}</b> today to <b style="color:${riskColor(c.f2030)}">${c.f2030}</b> by 2030.
             The primary driver is ${c.risk.split('+')[0].trim().toLowerCase()}.
             ${c.f2030>75?'This trajectory risks crossing into Structurally Vulnerable territory before 2030.':'This represents a meaningful deterioration in food system resilience.'}
             Key interventions needed: ${c.resp.split('·')[0].trim()}.`
          : `${c.name} is on a stable or improving trajectory. Current FDRS of <b style="color:${riskColor(c.fdrs)}">${c.fdrs}</b> is projected to remain at <b style="color:${riskColor(c.f2030)}">${c.f2030}</b> by 2030.
             The country's main food system strengths are holding. The primary watch point remains: ${c.risk}.`}
      </div>
    </div>`;

  setTimeout(()=>{
    destroyChart('forecast-main');
    chartInstances['forecast-main'] = mkProdChart('forecast-main',c,true);
    destroyChart('forecast-trade');
    chartInstances['forecast-trade'] = mkTradeTrendChart('forecast-trade',c);
    destroyChart('forecast-pop');
    chartInstances['forecast-pop'] = mkPopChart('forecast-pop',c);
  },60);
}

// ════════════════════════════════════════════════════
// SCENARIO TAB
// ════════════════════════════════════════════════════
function renderScenarioTab() {
  document.getElementById('scenario-body').innerHTML = `
    <div class="grid-2">
      <div class="card">
        <div class="card-title">Select Shock Scenarios</div>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;margin-bottom:14px;" id="scenario-chips">
          ${SCENARIOS.map(s=>`
            <div class="scenario-chip" id="sc-${s.id}" onclick="toggleScenario('${s.id}',this)">
              <span style="font-size:16px">${s.icon}</span>
              <span style="font-size:11px;color:var(--t2)">${s.label}</span>
            </div>`).join('')}
        </div>
        <button onclick="runScenarios()" style="
          width:100%;padding:10px;border:none;border-radius:8px;cursor:pointer;font-size:12px;
          font-weight:700;font-family:'Inter';letter-spacing:.3px;
          background:linear-gradient(135deg,var(--blue2),var(--teal));color:#fff;
          transition:opacity .15s;
        " onmouseover="this.style.opacity='.85'" onmouseout="this.style.opacity='1'">
          ⚡ Run Scenario Analysis
        </button>
      </div>
      <div class="card" id="scenario-summary">
        <div class="card-title">Impact Summary</div>
        <div style="color:var(--t3);font-size:12px;text-align:center;padding:40px 0;">
          Select one or more scenarios and click Run Analysis
        </div>
      </div>
    </div>
    <div class="card" id="impact-card" style="display:none;">
      <div class="card-title">Country Impact Ranking</div>
      <div style="overflow-x:auto;max-height:440px;overflow-y:auto;">
        <table class="fs-table" id="impact-table">
          <thead><tr>
            <th>#</th><th>Country</th><th>Region</th>
            <th>Base FDRS</th><th>Scenario FDRS</th><th>Δ Change</th>
            <th>Main Exposure</th><th>Recommended Response</th>
          </tr></thead>
          <tbody id="impact-tbody"></tbody>
        </table>
      </div>
    </div>`;
}

function toggleScenario(id, el) {
  if (activeScenarios.has(id)) {
    activeScenarios.delete(id); el.classList.remove('active');
  } else {
    activeScenarios.add(id); el.classList.add('active');
  }
}

const SCENARIO_RESPONSES = {
  wheat20:'Diversify wheat suppliers · Build 6-month strategic reserve',
  rice20: 'Diversify rice suppliers · Domestic production boost',
  maize20:'Feed grain supplier diversification · Domestic maize expansion',
  fert30: 'Domestic fertilizer production · Organic alternatives · Subsidy programs',
  drought:'Irrigation investment · Drought-resilient crop varieties',
  oil25:  'Local logistics · Rail over road · Fuel reserves',
  ban:    'Supplier diversification · Regional trade corridors',
  fx:     'FX hedging · Local currency food pricing · Import financing',
  ship:   'Regional sourcing · Port diversification · Stockpiling',
  conflict:'Alternative trade routes · Regional supply chain redundancy',
};

function runScenarios() {
  if (!activeScenarios.size) { alert('Please select at least one scenario.'); return; }
  const selected = SCENARIOS.filter(s=>activeScenarios.has(s.id));

  const results = COUNTRIES.map(c=>{
    const delta = Math.round(selected.reduce((s,sc)=>s+sc.impact(c),0));
    return { ...c, newScore:Math.min(100,c.fdrs+delta), delta };
  }).sort((a,b)=>b.delta-a.delta);

  const top3 = results.slice(0,3).map(c=>c.name).join(', ');
  const crossing = results.filter(c=>riskLabel(c.fdrs)!==riskLabel(c.newScore)).length;
  const avgDelta = (results.reduce((s,c)=>s+c.delta,0)/results.length).toFixed(1);

  const sumDiv = document.getElementById('scenario-summary');
  sumDiv.innerHTML = `
    <div class="card-title">Impact Summary</div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-bottom:14px;">
      <div style="background:var(--bg3);border-radius:8px;padding:12px;text-align:center;">
        <div style="font-size:28px;font-weight:800;color:#ef4444;font-family:'JetBrains Mono'">${results[0].delta}pts</div>
        <div style="font-size:10px;color:var(--t3);margin-top:2px">Highest impact on ${results[0].name}</div>
      </div>
      <div style="background:var(--bg3);border-radius:8px;padding:12px;text-align:center;">
        <div style="font-size:28px;font-weight:800;color:#f97316;font-family:'JetBrains Mono'">${crossing}</div>
        <div style="font-size:10px;color:var(--t3);margin-top:2px">Countries cross risk threshold</div>
      </div>
    </div>
    <div style="font-size:11px;color:var(--t2);line-height:1.7;">
      <b style="color:var(--t1)">Scenarios:</b> ${selected.map(s=>s.icon+' '+s.label).join(', ')}<br>
      <b style="color:var(--t1)">Most exposed:</b> ${top3}<br>
      <b style="color:var(--t1)">Average score increase:</b> +${avgDelta} points
    </div>`;

  const resp = selected.map(s=>SCENARIO_RESPONSES[s.id]).join(' · ');
  document.getElementById('impact-tbody').innerHTML = results.slice(0,40).map((c,i)=>`
    <tr>
      <td style="color:var(--t3);font-family:'JetBrains Mono'">${i+1}</td>
      <td><b>${c.name}</b><div style="font-size:9px;color:var(--t3)">${c.region}</div></td>
      <td style="color:var(--t3);font-size:11px">${c.region}</td>
      <td><span class="score-pill" style="background:${riskColor(c.fdrs)}1a;color:${riskColor(c.fdrs)}">${c.fdrs}</span></td>
      <td><span class="score-pill" style="background:${riskColor(c.newScore)}1a;color:${riskColor(c.newScore)}">${c.newScore}</span></td>
      <td class="${c.delta>0?'delta-up':'delta-down'}" style="font-family:'JetBrains Mono'">${c.delta>0?'+':''}${c.delta}</td>
      <td style="font-size:10px;color:var(--t2);max-width:160px">${c.risk}</td>
      <td style="font-size:10px;color:var(--t2);max-width:200px">${resp.slice(0,100)}…</td>
    </tr>`).join('');
  document.getElementById('impact-card').style.display='block';
}

// ════════════════════════════════════════════════════
// CHART HELPERS
// ════════════════════════════════════════════════════
const CHART_DEFAULTS = {
  responsive:true, maintainAspectRatio:false,
  plugins:{legend:{labels:{color:'#64748b',font:{size:10},usePointStyle:true,pointStyleWidth:8}}},
  scales:{
    x:{grid:{color:'#1a2e4a'},ticks:{color:'#334155',font:{size:10}}},
    y:{grid:{color:'#1a2e4a'},ticks:{color:'#334155',font:{size:10}}}
  }
};

function mkRadar(id, c, title) {
  const ctx = document.getElementById(id); if(!ctx) return;
  const col = riskColor(c.fdrs);
  return new Chart(ctx,{
    type:'radar',
    data:{
      labels:['Import Dep.','Supplier Conc.','Prod. Trend','Inflation','Climate','Conflict'],
      datasets:[{data:c.c,backgroundColor:col+'22',borderColor:col,borderWidth:2,
        pointBackgroundColor:col,pointRadius:3,label:title}]
    },
    options:{responsive:true,maintainAspectRatio:true,
      plugins:{legend:{display:!!title,labels:{color:'#64748b',font:{size:10}}}},
      scales:{r:{min:0,max:100,
        ticks:{stepSize:25,color:'#1e3a5f',font:{size:8},backdropColor:'transparent'},
        grid:{color:'#1a2e4a'},angleLines:{color:'#1a2e4a'},
        pointLabels:{color:'#475569',font:{size:9}}
      }}
    }
  });
}

function mkProdChart(id, c, showForecast=false) {
  const ctx=document.getElementById(id); if(!ctx) return;
  const t=c.trend;
  const yrs=t.years.map(String);
  return new Chart(ctx,{
    type:'line',
    data:{labels:yrs,datasets:[
      {label:'Production',data:t.production,borderColor:'#22c55e',backgroundColor:'#22c55e15',fill:true,tension:.4,borderWidth:2,pointRadius:0},
      {label:'Consumption',data:t.consumption,borderColor:'#ef4444',backgroundColor:'#ef444415',fill:true,tension:.4,borderWidth:2,pointRadius:0},
    ]},
    options:{...CHART_DEFAULTS,
      plugins:{...CHART_DEFAULTS.plugins,
        annotation:{annotations:{
          gap:{type:'line',xMin:yrs.indexOf('2024'),xMax:yrs.indexOf('2024'),borderColor:'#3b82f660',borderWidth:1,borderDash:[4,4],
               label:{content:'Forecast →',display:true,color:'#475569',font:{size:9},position:'start'}}
        }}
      }
    }
  });
}

function mkTradeTrendChart(id,c) {
  const ctx=document.getElementById(id); if(!ctx) return;
  const t=c.trend;
  return new Chart(ctx,{
    type:'line',
    data:{labels:t.years.map(String),datasets:[
      {label:'Imports',data:t.imports,borderColor:'#ef4444',tension:.4,borderWidth:2,pointRadius:0},
      {label:'Exports',data:t.exports,borderColor:'#22c55e',tension:.4,borderWidth:2,pointRadius:0},
    ]},
    options:CHART_DEFAULTS
  });
}

function mkPopChart(id,c) {
  const ctx=document.getElementById(id); if(!ctx) return;
  const t=c.trend;
  // Food supply index: production / population
  const supply = t.production.map((p,i)=>Math.round(p/t.pop[i]*10)/10);
  const popNorm = t.pop.map(p=>Math.round(p/t.pop[0]*100));
  return new Chart(ctx,{
    type:'line',
    data:{labels:t.years.map(String),datasets:[
      {label:'Population (index)',data:popNorm,borderColor:'#3b82f6',tension:.4,borderWidth:2,pointRadius:0},
      {label:'Food supply / capita (index)',data:supply.map(s=>Math.round(s/supply[0]*100)),borderColor:'#14b8a6',tension:.4,borderWidth:2,pointRadius:0},
    ]},
    options:CHART_DEFAULTS
  });
}

// ════════════════════════════════════════════════════
// INIT
// ════════════════════════════════════════════════════
window.addEventListener('load',()=>{
  populateSelects();
  initMap();
  updateRail();
});

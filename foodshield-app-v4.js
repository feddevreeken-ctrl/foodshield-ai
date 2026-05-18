// FoodShield AI v2 — Logic & Rendering
// ════════════════════════════════════════════════════

// ── State ─────────────────────────────────────────
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
  if (tab==='global'  && window.Globe && Globe.state.map) {
    // Leaflet caches size; recompute after the panel becomes visible
    setTimeout(()=>Globe.state.map.invalidateSize(), 80);
    setTimeout(()=>Globe.state.map.invalidateSize(), 300);
  }
  if (tab==='country')  renderCountryTab();
  if (tab==='trade')    renderTradeTab();
  if (tab==='score')        renderScoreTab();
  if (tab==='methodology')  renderMethodologyTab();
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
}

// ── Filters ───────────────────────────────────────
function applyFilters() {
  activeFilters.region    = document.getElementById('filter-region').value;
  activeFilters.risk      = document.getElementById('filter-risk').value;
  activeFilters.commodity = document.getElementById('filter-commodity').value;
  if (window.Globe) Globe.render();
}

// Map controls (overlay buttons)
function globeZoom(factor) {
  if (!window.Globe || !Globe.state.map) return;
  const m = Globe.state.map;
  m.setZoom(m.getZoom() + (factor > 1 ? 0.5 : -0.5));
}
function globeRecenter() {
  if (!window.Globe || !Globe.state.map) return;
  Globe.state.map.flyTo([22, 10], 2.4, { duration: 0.8 });
  Globe.deselectCountry();
  resetTradeFlowsButton();
}

// Trade Flow CTA
function toggleTradeFlows() {
  if (!window.Globe) return;
  const btn = document.getElementById('sp-flows-btn');
  const lbl = document.getElementById('sp-flows-label');
  if (Globe.isActive()) {
    Globe.clearFlows();
    if (btn) btn.dataset.active = '0';
    if (lbl) lbl.textContent = 'Visualize trade flows on globe';
  } else {
    Globe.drawFlows();
    if (btn) btn.dataset.active = '1';
    if (lbl) lbl.textContent = 'Hide trade flows';
  }
}
function resetTradeFlowsButton() {
  const btn = document.getElementById('sp-flows-btn');
  const lbl = document.getElementById('sp-flows-label');
  if (btn) btn.dataset.active = '0';
  if (lbl) lbl.textContent = 'Visualize trade flows on globe';
}

// ════════════════════════════════════════════════════
// MAP
// ════════════════════════════════════════════════════
// Map functions are handled by foodshield-globe.js (D3 orthographic globe)

function updateOverlayStats() {
  const n = COUNTRIES.length;
  const vuln = COUNTRIES.filter(c=>!c.iso.startsWith('US-')&&c.fdrs>75).length;
  const avg  = Math.round(COUNTRIES.reduce((s,c)=>s+c.fdrs,0)/n);
  document.getElementById('msp-countries').textContent = n;
  document.getElementById('msp-vulnerable').textContent = vuln;
  document.getElementById('msp-avg').textContent = avg;
}

// ════════════════════════════════════════════════════
// SIDE PANEL
// ════════════════════════════════════════════════════
function openUSADrilldown() {
  // Show a modal/overlay letting user pick: USA overall or a specific state
  const usa = byISO('USA');
  const states = COUNTRIES.filter(c=>c.iso.startsWith('US-')).sort((a,b)=>a.name.localeCompare(b.name));

  // Remove existing modal if any
  const existing = document.getElementById('usa-modal');
  if (existing) existing.remove();

  const modal = document.createElement('div');
  modal.id = 'usa-modal';
  modal.style.cssText = `
    position:fixed;top:0;left:0;width:100vw;height:100vh;
    background:rgba(0,0,0,0.72);z-index:9999;
    display:flex;align-items:center;justify-content:center;
  `;
  modal.innerHTML = `
    <div style="background:#13131a;border:1px solid #2a2a35;border-radius:14px;padding:28px 32px;
                width:520px;max-width:95vw;max-height:85vh;overflow-y:auto;box-shadow:0 20px 60px #000a;">
      <div style="display:flex;align-items:center;justify-content:space-between;margin-bottom:20px;">
        <div>
          <div style="font-size:18px;font-weight:700;color:#e8e6e0">🇺🇸 United States</div>
          <div style="font-size:12px;color:#6b6960;margin-top:3px;">Select overview or drill into a state</div>
        </div>
        <button onclick="document.getElementById('usa-modal').remove()"
          style="background:none;border:1px solid #2a2a35;border-radius:6px;color:#9a988f;
                 padding:6px 12px;cursor:pointer;font-size:13px;">✕ Close</button>
      </div>

      <!-- USA overall button -->
      <div onclick="document.getElementById('usa-modal').remove();openPanel(byISO('USA'), true);"
        style="background:#1a1a24;border:1px solid #d49a4a40;border-radius:10px;
               padding:16px 18px;margin-bottom:16px;cursor:pointer;
               display:flex;align-items:center;justify-content:space-between;
               transition:border-color .15s;"
        onmouseover="this.style.borderColor='#d49a4a'" onmouseout="this.style.borderColor='#d49a4a40'">
        <div>
          <div style="font-weight:600;color:#e8e6e0;font-size:14px;">United States — National Overview</div>
          <div style="font-size:11px;color:#6b6960;margin-top:4px;">
            FDRS ${usa.fdrs} · ${riskLabel(usa.fdrs)} · 50 states · World's largest food exporter
          </div>
        </div>
        <span style="color:#d49a4a;font-size:18px;">→</span>
      </div>

      <!-- State grid -->
      <div style="font-size:11px;color:#6b6960;margin-bottom:10px;font-weight:600;text-transform:uppercase;letter-spacing:.06em;">
        Or select a state
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:6px;">
        ${states.map(s=>`
          <div onclick="document.getElementById('usa-modal').remove();openPanel(byISO('${s.iso}'));"
            style="background:#1a1a24;border:1px solid #2a2a35;border-radius:8px;
                   padding:10px 12px;cursor:pointer;display:flex;align-items:center;
                   justify-content:space-between;transition:border-color .12s;"
            onmouseover="this.style.borderColor='${riskColor(s.fdrs)}'" 
            onmouseout="this.style.borderColor='#2a2a35'">
            <div>
              <div style="font-size:12.5px;font-weight:600;color:#e8e6e0">${s.name}</div>
              <div style="font-size:10.5px;color:#6b6960;margin-top:1px;">
                ${s.exports[0]} · ${s.exports[1]}
              </div>
            </div>
            <span style="font-size:13px;font-weight:700;color:${riskColor(s.fdrs)};
                         font-family:'Geist Mono',monospace;min-width:24px;text-align:right">${s.fdrs}</span>
          </div>
        `).join('')}
      </div>
    </div>
  `;

  document.body.appendChild(modal);
  // Click outside to close
  modal.addEventListener('click', e => { if (e.target === modal) modal.remove(); });
}

function openPanel(c, skipDrilldown) {
  // Intercept USA clicks to show drill-down (unless bypassed)
  if (c && c.iso === 'USA' && !skipDrilldown) {
    openUSADrilldown();
    return;
  }

  const sp = document.getElementById('sidepanel');
  const wasHidden = sp.classList.contains('hidden');
  sp.classList.remove('hidden');

  // Hook into globe: rotate to country, clear any previous flow
  if (window.Globe) {
    Globe.clearFlows();
    Globe.selectCountry(c.iso);
    if (Globe.state.map && wasHidden) {
      // Panel slide animation is .28s
      setTimeout(()=>Globe.state.map.invalidateSize(), 320);
    }
  }
  resetTradeFlowsButton();

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
  const tCol  = c.f2030 > c.fdrs ? '#c44b3c' : '#6ba36b';
  document.getElementById('sp-forecast').innerHTML =
    `<span class="trend-arrow" style="color:${tCol}">${trend}</span>
     <span style="color:#9a988f">2030 forecast: </span>
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
        ticks:{stepSize:25,color:'#6a685e',font:{size:8},backdropColor:'transparent'},
        grid:{color:'#23232c'},angleLines:{color:'#23232c'},
        pointLabels:{color:'#9a988f',font:{size:8.5}}
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
    <div class="sp-row"><span class="k">Food inflation</span><span class="v" style="color:${c.fi>20?'#c44b3c':'#9a988f'}">${c.fi}% / yr</span></div>
    <div class="sp-row"><span class="k">Net food trade</span><span class="v" style="color:${c.net>=0?'#6ba36b':'#c44b3c'}">${c.net>=0?'+':''}${(c.net/1000).toFixed(0)}B USD</span></div>`;

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
     (${c.f2030>c.fdrs?'<span style="color:#c44b3c">▲ deteriorating</span>':'<span style="color:#6ba36b">▼ improving / stable</span>'}).`;

  document.getElementById('sp-actions').innerHTML =
    c.resp.split('·').map(a=>`<div style="padding:2px 0;">→ ${a.trim()}</div>`).join('');
}

function closePanel() {
  document.getElementById('sidepanel').classList.add('hidden');
  if (window.Globe) {
    Globe.deselectCountry();
    if (Globe.state.map) setTimeout(()=>Globe.state.map.invalidateSize(), 320);
  }
  resetTradeFlowsButton();
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
  const colors=['#6ba36b','#c9a957','#d27a3e','#c44b3c'];
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
  const highFI   = COUNTRIES.filter(c=>!c.iso.startsWith('US-')&&c.fi>30).length;
  const risingDep= COUNTRIES.filter(c=>!c.iso.startsWith('US-')&&c.f2030>c.fdrs+5).length;
  const ICN = {
    wheat:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"><path d="M12 21V8M12 8c-2-2-4-2-5-1-1 2 1 5 5 5M12 8c2-2 4-2 5-1 1 2-1 5-5 5M12 13c-2-2-4-2-5-1-1 2 1 5 5 5M12 13c2-2 4-2 5-1 1 2-1 5-5 5M12 18c-2-1-4-1-5 0M12 18c2-1 4-1 5 0"/></svg>',
    trend:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M3 17 9 11l4 4 8-9"/><path d="M14 6h7v7"/></svg>',
    alert:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3 2 21h20L12 3z"/><path d="M12 10v5M12 18v.5"/></svg>',
    globe:'<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3a14 14 0 0 1 0 18M12 3a14 14 0 0 0 0 18"/></svg>'
  };
  document.getElementById('rail-insights').innerHTML = [
    {icon:ICN.wheat, text:`<b>${avgWheat}%</b> average wheat import dependency globally`},
    {icon:ICN.trend, text:`<b>${highFI} countries</b> with food inflation above 30%`},
    {icon:ICN.alert, text:`<b>${risingDep} countries</b> projected to deteriorate by 2030`},
    {icon:ICN.globe, text:`<b>${COUNTRIES.filter(c=>!c.iso.startsWith('US-')&&c.fdrs>75).length} countries</b> currently structurally vulnerable`},
  ].map(i=>`<div class="insight-card"><div class="insight-icon">${i.icon}</div><div class="insight-text">${i.text}</div></div>`).join('');
}

// ════════════════════════════════════════════════════
// COUNTRY TAB
// ════════════════════════════════════════════════════
function populateSelects() {
  const countries = COUNTRIES.filter(c=>!c.iso.startsWith('US-')).sort((a,b)=>a.name.localeCompare(b.name));
  const usStates  = COUNTRIES.filter(c=>c.iso.startsWith('US-')).sort((a,b)=>a.name.localeCompare(b.name));
  const countryOpts = countries.map(c=>`<option value="${c.iso}">${c.name}</option>`).join('');
  const stateOpts   = usStates.map(c=>`<option value="${c.iso}">${c.name}</option>`).join('');
  const opts = countryOpts + `<optgroup label="─── United States ───"></optgroup>` + stateOpts;
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
  const trendUp = c.f2030 > c.fdrs;
  const trendCol = trendUp ? '#c44b3c' : '#6ba36b';

  document.getElementById('country-body').innerHTML = `
    <!-- HERO -->
    <div class="ct-hero">
      <div class="ct-hero-left">
        <div class="ct-hero-eyebrow"><span class="ct-hero-flag"></span> ${c.region}</div>
        <div class="ct-hero-name">${c.name}</div>
        <div class="ct-hero-desc">${c.risk}</div>
      </div>
      <div class="ct-hero-score" style="--accent:${col}">
        <div class="ct-hero-score-num" style="color:${col}">${c.fdrs}</div>
        <div class="ct-hero-score-meta">
          <div class="ct-hero-score-label">Food Dependency Risk · /100</div>
          <div class="risk-badge" style="${riskBadgeStyle(c.fdrs)}">${riskLabel(c.fdrs)}</div>
          <div class="ct-hero-forecast">
            <span class="trend-arrow" style="color:${trendCol}">${trendUp?'▲':'▼'}</span>
            2030 forecast <span class="mono" style="color:${riskColor(c.f2030)}">${c.f2030}</span>
            <span class="ct-hero-forecast-delta" style="color:${trendCol}">${trendUp?'+':''}${c.f2030 - c.fdrs}</span>
          </div>
        </div>
      </div>
    </div>

    <!-- Stat strip -->
    <div class="ct-stat-strip">
      <div class="ct-stat">
        <div class="ct-stat-label">Wheat dep.</div>
        <div class="ct-stat-val">${c.w}<span class="ct-stat-unit">%</span></div>
      </div>
      <div class="ct-stat">
        <div class="ct-stat-label">Rice dep.</div>
        <div class="ct-stat-val">${c.r}<span class="ct-stat-unit">%</span></div>
      </div>
      <div class="ct-stat">
        <div class="ct-stat-label">Food inflation</div>
        <div class="ct-stat-val" style="color:${c.fi>20?'#c44b3c':'inherit'}">${c.fi}<span class="ct-stat-unit">%/yr</span></div>
      </div>
      <div class="ct-stat">
        <div class="ct-stat-label">Net trade</div>
        <div class="ct-stat-val" style="color:${c.net>=0?'#6ba36b':'#c44b3c'}">${c.net>=0?'+':''}${(c.net/1000).toFixed(1)}<span class="ct-stat-unit">B USD</span></div>
      </div>
    </div>

    <!-- Composition: radar + bars -->
    <div class="grid-2">
      <div class="card">
        <div class="card-title"><span class="card-marker"></span> Risk Radar</div>
        <div style="display:flex;justify-content:center;align-items:center;height:260px;">
          <canvas id="radar-ct" style="max-height:260px;max-width:340px;"></canvas>
        </div>
      </div>
      <div class="card">
        <div class="card-title"><span class="card-marker"></span> FDRS Composition</div>
        ${c.c.map((v,i)=>`
          <div class="comp-row">
            <div class="comp-label">${compLabels[i]}</div>
            <div class="comp-weight">${compWeights[i]}</div>
            <div class="comp-track" style="width:150px"><div class="comp-fill" style="width:${v}%;background:${riskColor(v)}"></div></div>
            <div class="comp-val" style="color:${riskColor(v)}">${v}</div>
          </div>`).join('')}
      </div>
    </div>

    <!-- Trade exposure -->
    <div class="grid-2">
      <div class="card">
        <div class="card-title"><span class="card-marker"></span> Top Food Imports</div>
        ${c.imports.map((im,i)=>`
          <div class="ct-import-row">
            <span class="ct-import-num">${String(i+1).padStart(2,'0')}</span>
            <span class="ct-import-name">${im}</span>
          </div>`).join('')}
      </div>
      <div class="card">
        <div class="card-title"><span class="card-marker"></span> Top Supplier Countries</div>
        ${c.suppliers.map((s,i)=>`
          <div class="supplier-row" style="padding:7px 0;border-bottom:1px solid var(--b1);">
            <div class="supplier-name">${s}</div>
            <div class="supplier-bar-wrap" style="width:120px"><div class="supplier-bar-fill" style="width:${(c.supPct||[])[i]||20}%"></div></div>
            <div class="supplier-pct">${(c.supPct||[])[i]||'—'}%</div>
          </div>`).join('')}
      </div>
    </div>

    <!-- Production vs consumption -->
    <div class="card">
      <div class="card-title"><span class="card-marker"></span> Production vs Consumption · 2018–2030</div>
      <div style="height:240px;"><canvas id="prodchart-ct"></canvas></div>
    </div>

    <!-- AI Analysis editorial -->
    <div class="ct-ai-card">
      <div class="ct-ai-eyebrow"><span class="ct-ai-asterisk">✱</span> AI Analysis</div>
      <p class="ct-ai-body">${c.ai}</p>
      <div class="ct-ai-grid">
        <div>
          <div class="ct-ai-subhead">Main risk vector</div>
          <div class="ct-ai-text">${c.risk}</div>
        </div>
        <div>
          <div class="ct-ai-subhead">2030 outlook</div>
          <div class="ct-ai-text" style="color:${riskColor(c.f2030)}">FDRS → ${c.f2030} / 100 ${trendUp?'▲ deteriorating':'▼ stable / improving'}</div>
        </div>
      </div>
      <div class="ct-ai-actions">
        <div class="ct-ai-subhead">Recommended actions</div>
        ${c.resp.split('·').map(a=>`<div class="ct-ai-action"><span class="ct-ai-arrow">→</span>${a.trim()}</div>`).join('')}
      </div>
    </div>`;

  setTimeout(()=>{
    destroyChart('radar-ct');
    chartInstances['radar-ct'] = mkRadar('radar-ct', c, null);
    destroyChart('prodchart-ct');
    chartInstances['prodchart-ct'] = mkProdChart('prodchart-ct', c);
  },50);
}

// ════════════════════════════════════════════════════
// TRADE TAB
// ════════════════════════════════════════════════════
function renderTradeTab() {
  const c = byISO(document.getElementById('trade-sel').value); if(!c) return;
  const exportItems = (c.exports && c.exports.length) ? c.exports.slice(0,5) : (c.net>0
    ? ['Food commodities','Grains','Protein','Oilseeds','Livestock']
    : ['Non-food exports','Minerals','Energy','Manufactures','Services']).slice(0,5);
  const isExporter = c.net >= 0;
  const netCol = isExporter ? '#6ba36b' : '#c44b3c';
  const importsTotal = Math.abs(Math.min(c.net,0)/1000).toFixed(1);
  const exportsTotal = Math.max(c.net,0)/1000 > 0 ? (c.net/1000).toFixed(1) : '< 1';
  const hhiTier = c.c[1]>60?'High concentration risk':c.c[1]>30?'Moderate concentration':'Well diversified';

  document.getElementById('trade-body').innerHTML = `
    <!-- Net trade hero -->
    <div class="tr-hero">
      <div class="tr-hero-left">
        <div class="tr-hero-eyebrow">${c.name} · Net trade position</div>
        <div class="tr-hero-num" style="color:${netCol}">
          ${isExporter?'+':''}${(c.net/1000).toFixed(1)}<span class="tr-hero-unit">B USD</span>
        </div>
        <div class="tr-hero-status" style="color:${netCol}">
          ${isExporter ? '▲ Net food exporter' : '▼ Net food importer'}
        </div>
      </div>
      <div class="tr-hero-right">
        <div class="tr-hero-bar">
          <div class="tr-hero-bar-axis"></div>
          ${isExporter
            ? `<div class="tr-hero-bar-fill" style="left:50%;width:${Math.min(Math.abs(c.net)/2000,45)}%;background:#6ba36b"></div>`
            : `<div class="tr-hero-bar-fill" style="right:50%;width:${Math.min(Math.abs(c.net)/2000,45)}%;background:#c44b3c"></div>`}
        </div>
        <div class="tr-hero-bar-labels">
          <span style="color:#c44b3c">${importsTotal}B imports</span>
          <span style="color:#6ba36b">${exportsTotal}B exports</span>
        </div>
      </div>
    </div>

    <!-- Imports / Exports breakdown -->
    <div class="grid-2">
      <div class="card">
        <div class="card-title"><span class="card-marker" style="background:#c44b3c"></span> Top food imports</div>
        ${c.imports.map((name,i)=>{
          const pct = [35,25,18,12,10][i];
          const sup = (c.suppliers||[])[i] || '';
          return `<div class="tr-bar-row" style="flex-wrap:wrap;gap:2px;">
            <div style="display:flex;align-items:center;width:100%;gap:6px;margin-bottom:3px;">
              <span class="tr-bar-name" style="flex:1">${name}</span>
              ${sup ? `<span style="font-size:10px;color:var(--t3);white-space:nowrap;">from ${sup}</span>` : ''}
              <span class="tr-bar-pct" style="min-width:32px;text-align:right;">${pct}%</span>
            </div>
            <div class="tr-bar-track" style="width:100%;margin-bottom:2px;"><div class="tr-bar-fill" style="width:${pct}%;background:#c44b3c"></div></div>
          </div>`;
        }).join('')}
        <div class="tr-card-footer">
          <span>Total food imports</span>
          <span class="mono" style="color:#c44b3c;font-weight:600">${importsTotal}B USD</span>
        </div>
      </div>

      <div class="card">
        <div class="card-title"><span class="card-marker" style="background:#6ba36b"></span> Top food exports</div>
        ${exportItems.map((name,i)=>{
          const pct = [40,28,16,10,6][i];
          const dest = (c.exportDests||[])[i] || '';
          return `<div class="tr-bar-row" style="flex-wrap:wrap;gap:2px;">
            <div style="display:flex;align-items:center;width:100%;gap:6px;margin-bottom:3px;">
              <span class="tr-bar-name" style="flex:1">${name}</span>
              ${dest ? `<span style="font-size:10px;color:var(--t3);white-space:nowrap;">to ${dest}</span>` : ''}
              <span class="tr-bar-pct" style="min-width:32px;text-align:right;">${pct}%</span>
            </div>
            <div class="tr-bar-track" style="width:100%;margin-bottom:2px;"><div class="tr-bar-fill" style="width:${pct}%;background:#6ba36b"></div></div>
          </div>`;
        }).join('')}
        <div class="tr-card-footer">
          <span>Total food exports</span>
          <span class="mono" style="color:#6ba36b;font-weight:600">${exportsTotal}B USD</span>
        </div>
      </div>
    </div>

    <!-- Supplier concentration -->
    <div class="card">
      <div class="card-title"><span class="card-marker"></span> Supplier concentration</div>
      <div class="tr-conc-grid">
        <div class="tr-conc-chart">
          <canvas id="supplier-pie" style="max-height:220px;"></canvas>
        </div>
        <div class="tr-conc-right">
          <div class="tr-hhi">
            <div class="tr-hhi-label">HHI Concentration Index</div>
            <div class="tr-hhi-num" style="color:${riskColor(c.c[1])}">${c.c[1]}</div>
            <div class="tr-hhi-tier" style="color:${riskColor(c.c[1])}">${hhiTier}</div>
          </div>
          <div class="tr-conc-sup-list">
            ${c.suppliers.map((s,i)=>`
              <div class="supplier-row" style="padding:5px 0;">
                <div class="supplier-name">${s}</div>
                <div class="supplier-bar-wrap" style="width:140px"><div class="supplier-bar-fill" style="width:${(c.supPct||[])[i]||20}%"></div></div>
                <div class="supplier-pct">${(c.supPct||[])[i]||'—'}%</div>
              </div>`).join('')}
          </div>
        </div>
      </div>
    </div>

    <!-- Trade trend -->
    <div class="card">
      <div class="card-title"><span class="card-marker"></span> Imports vs exports · 2018–2030</div>
      <div style="height:240px;"><canvas id="trade-trend-chart"></canvas></div>
    </div>`;

  setTimeout(()=>{
    destroyChart('supplier-pie');
    chartInstances['supplier-pie'] = new Chart(document.getElementById('supplier-pie'),{
      type:'doughnut',
      data:{
        labels:c.suppliers,
        datasets:[{
          data:c.supPct||[20,20,20,20,20],
          backgroundColor:['#d49a4a','#8aa8c9','#5b9b8a','#c9a957','#9c7fc2'],
          borderWidth:2, borderColor:'#14141a', hoverOffset:6,
        }]
      },
      options:{responsive:true,maintainAspectRatio:true,cutout:'62%',
        plugins:{legend:{position:'right',labels:{color:'#ebe9e2',font:{size:11,family:'Geist'},padding:10,boxWidth:10,boxHeight:10}},
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
  const sorted = [...COUNTRIES].filter(c=>!c.iso.startsWith('US-')).sort((a,b)=>b.fdrs-a.fdrs);
  const formulaRows = [
    {label: 'Import Dependency',    w: '30%'},
    {label: 'Supplier Concentration', w: '20%'},
    {label: 'Production Trend',     w: '15%'},
    {label: 'Food Inflation',       w: '15%'},
    {label: 'Climate Volatility',   w: '10%'},
    {label: 'Conflict / Logistics', w: '10%'},
  ];
  const categories = [
    {r:'0–25',   l:'Resilient',                d:'Diversified, stable, low import reliance',     col:'#6ba36b'},
    {r:'26–50',  l:'Exposed',                  d:'Moderate reliance, manageable risk',           col:'#c9a957'},
    {r:'51–75',  l:'Dependent',                d:'High import reliance, supply gaps growing',    col:'#d27a3e'},
    {r:'76–100', l:'Structurally Vulnerable',  d:'Extreme exposure, compounded shocks',          col:'#c44b3c'},
  ];

  document.getElementById('score-body').innerHTML = `
    <div class="grid-2">
      <div class="card">
        <div class="card-title"><span class="card-marker"></span> FDRS Methodology</div>
        <div class="sc-formula">
          ${formulaRows.map((r,i)=>`
            <div class="sc-formula-line">
              <span class="sc-formula-op">${i===0?'=':'+'}</span>
              <span class="sc-formula-label">${r.label}</span>
              <span class="sc-formula-weight">${r.w}</span>
            </div>
          `).join('')}
        </div>
        <div style="margin-top:14px;font-size:11.5px;color:var(--t2);line-height:1.6;">
          The Food Dependency Risk Score (FDRS) blends six weighted dimensions into a 0–100 index. Higher = more vulnerable.
        </div>
      </div>
      <div class="card">
        <div class="card-title"><span class="card-marker"></span> Risk Categories</div>
        ${categories.map(c=>`
          <div class="sc-cat-row">
            <div class="sc-cat-range" style="background:${c.col}1a;color:${c.col}">${c.r}</div>
            <div class="sc-cat-content">
              <div class="sc-cat-label" style="color:${c.col}">${c.l}</div>
              <div class="sc-cat-desc">${c.d}</div>
            </div>
          </div>
        `).join('')}
      </div>
    </div>

    <div class="card">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px;">
        <div class="card-title" style="margin-bottom:0"><span class="card-marker"></span> Global Ranking</div>
        <input class="fs-search" type="text" id="table-search" placeholder="Filter countries…" oninput="filterTable()" style="width:180px;margin-left:auto;">
      </div>
      <div style="overflow-x:auto;max-height:380px;overflow-y:auto;">
        <table class="fs-table">
          <thead><tr>
            <th>#</th><th>Country</th><th>Region</th><th>FDRS</th>
            <th>Imp Dep</th><th>Sup Conc</th><th>Prod</th><th>Inflat</th><th>Climate</th><th>Conflict</th>
            <th>2030 →</th>
          </tr></thead>
          <tbody id="ranking-tbody">
            ${sorted.map((c,i)=>`
              <tr onclick="openPanel(byISO('${c.iso}'));showTab('global',document.querySelector('.nav-btn'))">
                <td style="color:var(--t3);font-family:'Geist Mono'">${String(i+1).padStart(2,'0')}</td>
                <td style="font-weight:500">${c.name}</td>
                <td style="color:var(--t3);font-size:11px">${c.region}</td>
                <td><span class="score-pill" style="background:${riskColor(c.fdrs)}1a;color:${riskColor(c.fdrs)}">${c.fdrs}</span></td>
                ${c.c.map(v=>`<td style="color:${riskColor(v)};font-family:'Geist Mono';font-size:11px">${v}</td>`).join('')}
                <td><span style="color:${riskColor(c.f2030)};font-weight:600;font-size:11px;font-family:'Geist Mono'">${c.f2030} ${c.f2030>c.fdrs?'▲':'▼'}</span></td>
              </tr>`).join('')}
          </tbody>
        </table>
      </div>
    </div>

    <div class="card">
      <div class="card-title"><span class="card-marker"></span> Country Comparison</div>
      <div style="display:flex;gap:12px;align-items:center;margin-bottom:18px;">
        <select class="fs-select" id="cmp-a" onchange="renderComparison()" style="font-size:12.5px;padding:8px 28px 8px 12px;"></select>
        <span style="color:var(--t3);font-weight:600;font-family:'Geist Mono'">vs</span>
        <select class="fs-select" id="cmp-b" onchange="renderComparison()" style="font-size:12.5px;padding:8px 28px 8px 12px;"></select>
      </div>
      <div class="grid-2">
        <div style="text-align:center;"><canvas id="radar-a" style="max-height:240px;"></canvas></div>
        <div style="text-align:center;"><canvas id="radar-b" style="max-height:240px;"></canvas></div>
      </div>
      <div id="cmp-table" style="margin-top:18px;"></div>
    </div>

    <div class="card">
      <div class="card-title"><span class="card-marker"></span> 2×2 Dependency Matrix</div>
      <div style="font-size:11px;color:var(--t3);margin-bottom:14px;line-height:1.5;">
        X axis: Domestic system resilience (higher = more resilient) &nbsp;·&nbsp; Y axis: Food import dependency (higher = more dependent)
      </div>
      <div style="height:380px;position:relative;">
        <canvas id="matrix-chart"></canvas>
      </div>
    </div>`;

  const countries2 = COUNTRIES.filter(c=>!c.iso.startsWith('US-')).sort((a,b)=>a.name.localeCompare(b.name));
  const states2 = COUNTRIES.filter(c=>c.iso.startsWith('US-')).sort((a,b)=>a.name.localeCompare(b.name));
  const sorted2 = [...countries2, ...states2];
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
          <td><span style="color:${riskColor(av[i])};font-weight:700;font-family:'Geist Mono'">${av[i]}</span></td>
          <td><span style="color:${riskColor(bv[i])};font-weight:700;font-family:'Geist Mono'">${bv[i]}</span></td>
          <td style="color:${d>0?'#c44b3c':d<0?'#6ba36b':'#6a685e'};font-weight:700;font-family:'Geist Mono'">${d>0?'+':''}${d}</td>
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
        x:{min:0,max:100,grid:{color:'#23232c'},ticks:{color:'#6a685e'},
           title:{display:true,text:'Domestic System Resilience →',color:'#6a685e',font:{size:10}}},
        y:{min:0,max:100,grid:{color:'#23232c'},ticks:{color:'#6a685e'},
           title:{display:true,text:'Food Import Dependency ↑',color:'#6a685e',font:{size:10}}}
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
  const trendUp = c.f2030 > c.fdrs;
  const trendCol = trendUp ? '#c44b3c' : '#6ba36b';
  const todayCol = riskColor(c.fdrs);
  const futureCol = riskColor(c.f2030);
  const delta = c.f2030 - c.fdrs;
  const projImpDep = Math.min(100, c.c[0] + Math.round(delta * 0.4));

  document.getElementById('forecast-body').innerHTML = `
    <!-- Trajectory hero -->
    <div class="fc-hero">
      <div class="fc-hero-side">
        <div class="fc-hero-eyebrow">${c.name} · Today</div>
        <div class="fc-hero-num" style="color:${todayCol}">${c.fdrs}</div>
        <div class="fc-hero-label">${riskLabel(c.fdrs)}</div>
      </div>
      <div class="fc-hero-arrow">${trendUp ? '▶' : '▶'}</div>
      <div class="fc-hero-side" style="background:linear-gradient(135deg,rgba(0,0,0,0),rgba(0,0,0,0.18))">
        <div class="fc-hero-eyebrow" style="color:${trendCol}">2030 forecast · ${trendUp?'▲':'▼'} ${trendUp?'+':''}${delta} pts</div>
        <div class="fc-hero-num" style="color:${futureCol}">${c.f2030}</div>
        <div class="fc-hero-label">${riskLabel(c.f2030)} · ${trendUp?'deteriorating':'stable / improving'}</div>
      </div>
    </div>

    <!-- Metric strip -->
    <div class="ct-stat-strip">
      <div class="ct-stat">
        <div class="ct-stat-label">2030 Import dep.</div>
        <div class="ct-stat-val" style="color:${riskColor(projImpDep)}">${projImpDep}<span class="ct-stat-unit">%</span></div>
        <div style="font-size:10px;color:var(--t3);margin-top:4px;">from ${c.c[0]}% today</div>
      </div>
      <div class="ct-stat">
        <div class="ct-stat-label">Supply gap</div>
        <div class="ct-stat-val" style="color:${gap>0?'#c44b3c':'#6ba36b'}">${gap>0?'+':''}${Math.round(gap/1000)}<span class="ct-stat-unit">K t</span></div>
        <div style="font-size:10px;color:var(--t3);margin-top:4px;">${gap>0?'Deficit growing':'Surplus maintained'}</div>
      </div>
      <div class="ct-stat">
        <div class="ct-stat-label">Food inflation</div>
        <div class="ct-stat-val" style="color:${c.fi>15?'#c44b3c':'#6ba36b'}">${c.fi}<span class="ct-stat-unit">%/yr</span></div>
        <div style="font-size:10px;color:var(--t3);margin-top:4px;">current annual</div>
      </div>
      <div class="ct-stat">
        <div class="ct-stat-label">Population 2030</div>
        <div class="ct-stat-val">${t.pop[t.pop.length-1]}<span class="ct-stat-unit">M</span></div>
        <div style="font-size:10px;color:var(--t3);margin-top:4px;">from ${t.pop[t.pop.length-2]}M today</div>
      </div>
    </div>

    <div class="card">
      <div class="card-title"><span class="card-marker"></span> Production vs consumption · historical &amp; 2030 projection</div>
      <div style="height:260px;"><canvas id="forecast-main"></canvas></div>
    </div>

    <div class="grid-2">
      <div class="card">
        <div class="card-title"><span class="card-marker"></span> Import / export trend</div>
        <div style="height:220px;"><canvas id="forecast-trade"></canvas></div>
      </div>
      <div class="card">
        <div class="card-title"><span class="card-marker"></span> Population vs food supply / capita</div>
        <div style="height:220px;"><canvas id="forecast-pop"></canvas></div>
      </div>
    </div>

    <div class="ct-ai-card" style="border-left-color:${futureCol}">
      <div class="ct-ai-eyebrow" style="color:${futureCol}"><span class="ct-ai-asterisk">✱</span> AI Trend Interpretation</div>
      <p class="ct-ai-body">
        ${trendUp
          ? `If current trends continue, <strong style="color:var(--t1)">${c.name}</strong>'s Food Dependency Risk Score is projected to rise from <strong style="color:${todayCol}">${c.fdrs}</strong> today to <strong style="color:${futureCol}">${c.f2030}</strong> by 2030. The primary driver is ${c.risk.split('+')[0].trim().toLowerCase()}. ${c.f2030>75?'This trajectory risks crossing into <em>Structurally Vulnerable</em> territory before 2030.':'This represents a meaningful deterioration in food system resilience.'}`
          : `<strong style="color:var(--t1)">${c.name}</strong> is on a stable or improving trajectory. Current FDRS of <strong style="color:${todayCol}">${c.fdrs}</strong> is projected to remain at <strong style="color:${futureCol}">${c.f2030}</strong> by 2030. The country's main food system strengths are holding.`}
      </p>
      <div class="ct-ai-grid">
        <div>
          <div class="ct-ai-subhead">Primary watch point</div>
          <div class="ct-ai-text">${c.risk}</div>
        </div>
        <div>
          <div class="ct-ai-subhead">Key intervention</div>
          <div class="ct-ai-text">${c.resp.split('·')[0].trim()}</div>
        </div>
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
              <span class="scn-label">${s.label}</span>
            </div>`).join('')}
        </div>
        <button class="run-btn" onclick="runScenarios()">
          <svg viewBox="0 0 24 24" width="13" height="13" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"><path d="M13 2 4 14h7l-1 8 9-12h-7l1-8z"/></svg>
          Run Scenario Analysis
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
        <div style="font-size:28px;font-weight:600;color:#c44b3c;font-family:'Geist Mono'">${results[0].delta}pts</div>
        <div style="font-size:10px;color:var(--t3);margin-top:2px">Highest impact on ${results[0].name}</div>
      </div>
      <div style="background:var(--bg3);border-radius:8px;padding:12px;text-align:center;">
        <div style="font-size:28px;font-weight:600;color:#d27a3e;font-family:'Geist Mono'">${crossing}</div>
        <div style="font-size:10px;color:var(--t3);margin-top:2px">Countries cross risk threshold</div>
      </div>
    </div>
    <div style="font-size:11px;color:var(--t2);line-height:1.7;">
      <b style="color:var(--t1)">Scenarios:</b> ${selected.map(s=>s.label).join(', ')}<br>
      <b style="color:var(--t1)">Most exposed:</b> ${top3}<br>
      <b style="color:var(--t1)">Average score increase:</b> +${avgDelta} points
    </div>`;

  const resp = selected.map(s=>SCENARIO_RESPONSES[s.id]).join(' · ');
  document.getElementById('impact-tbody').innerHTML = results.slice(0,40).map((c,i)=>`
    <tr>
      <td style="color:var(--t3);font-family:'Geist Mono'">${i+1}</td>
      <td><b>${c.name}</b><div style="font-size:9px;color:var(--t3)">${c.region}</div></td>
      <td style="color:var(--t3);font-size:11px">${c.region}</td>
      <td><span class="score-pill" style="background:${riskColor(c.fdrs)}1a;color:${riskColor(c.fdrs)}">${c.fdrs}</span></td>
      <td><span class="score-pill" style="background:${riskColor(c.newScore)}1a;color:${riskColor(c.newScore)}">${c.newScore}</span></td>
      <td class="${c.delta>0?'delta-up':'delta-down'}" style="font-family:'Geist Mono'">${c.delta>0?'+':''}${c.delta}</td>
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
  plugins:{
    legend:{labels:{color:'#9a988f',font:{size:11,family:'Geist'},usePointStyle:true,pointStyleWidth:8,padding:14}},
    tooltip:{
      backgroundColor:'#14141a',titleColor:'#ebe9e2',bodyColor:'#ebe9e2',
      borderColor:'#2a2a34',borderWidth:1,
      titleFont:{family:'Geist',size:11,weight:600},
      bodyFont:{family:'Geist Mono',size:11},
      padding:10,boxPadding:4,cornerRadius:5,displayColors:true,boxWidth:8,boxHeight:8,
    }
  },
  scales:{
    x:{grid:{color:'rgba(255,255,255,0.04)',drawTicks:false},ticks:{color:'#6a685e',font:{size:10,family:'Geist Mono'}},border:{display:false}},
    y:{grid:{color:'rgba(255,255,255,0.04)',drawTicks:false},ticks:{color:'#6a685e',font:{size:10,family:'Geist Mono'}},border:{display:false}}
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
        pointBackgroundColor:col,pointRadius:3,pointHoverRadius:5,label:title}]
    },
    options:{responsive:true,maintainAspectRatio:true,
      plugins:{legend:{display:!!title,labels:{color:'#ebe9e2',font:{size:11,family:'Geist'}}}},
      scales:{r:{min:0,max:100,
        ticks:{stepSize:25,color:'#3a3a47',font:{size:9,family:'Geist Mono'},backdropColor:'transparent'},
        grid:{color:'rgba(255,255,255,0.05)'},angleLines:{color:'rgba(255,255,255,0.05)'},
        pointLabels:{color:'#9a988f',font:{size:10.5,family:'Geist',weight:500}}
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
      {label:'Production',data:t.production,borderColor:'#6ba36b',backgroundColor:'#6ba36b15',fill:true,tension:.4,borderWidth:2,pointRadius:0},
      {label:'Consumption',data:t.consumption,borderColor:'#c44b3c',backgroundColor:'#c44b3c15',fill:true,tension:.4,borderWidth:2,pointRadius:0},
    ]},
    options:{...CHART_DEFAULTS,
      plugins:{...CHART_DEFAULTS.plugins,
        annotation:{annotations:{
          gap:{type:'line',xMin:yrs.indexOf('2024'),xMax:yrs.indexOf('2024'),borderColor:'#8aa8c960',borderWidth:1,borderDash:[4,4],
               label:{content:'Forecast →',display:true,color:'#6a685e',font:{size:9},position:'start'}}
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
      {label:'Imports',data:t.imports,borderColor:'#c44b3c',tension:.4,borderWidth:2,pointRadius:0},
      {label:'Exports',data:t.exports,borderColor:'#6ba36b',tension:.4,borderWidth:2,pointRadius:0},
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
      {label:'Population (index)',data:popNorm,borderColor:'#8aa8c9',tension:.4,borderWidth:2,pointRadius:0},
      {label:'Food supply / capita (index)',data:supply.map(s=>Math.round(s/supply[0]*100)),borderColor:'#5b9b8a',tension:.4,borderWidth:2,pointRadius:0},
    ]},
    options:CHART_DEFAULTS
  });
}

// ════════════════════════════════════════════════════
// INIT
// ════════════════════════════════════════════════════
window.addEventListener('load',()=>{
  populateSelects();
  if (window.Globe) Globe.init();
  updateRail();
});

// ════════════════════════════════════════════════════
// METHODOLOGY TAB
// ════════════════════════════════════════════════════
function renderMethodologyTab() {
  const el = document.getElementById('methodology-body');
  if (!el) return;

  el.innerHTML = `
  <div class="grid-2">

    <!-- FDRS Formula -->
    <div class="card" style="grid-column:1/-1">
      <div class="card-title"><span class="card-marker"></span> The FDRS Formula</div>
      <div style="font-size:13px;color:var(--t2);line-height:1.8;margin-bottom:18px;">
        The <b style="color:var(--t1)">Food Dependency Risk Score (FDRS)</b> is a composite 0–100 index where
        <b style="color:#c44b3c">100 = maximally vulnerable</b> and <b style="color:#6ba36b">0 = fully resilient</b>.
        It is computed from six independently-sourced components, each normalized to 0–100 before weighting.
      </div>
      <div style="background:var(--bg1);border:1px solid var(--b1);border-radius:10px;padding:18px 22px;font-family:'Geist Mono',monospace;font-size:13px;margin-bottom:18px;line-height:2.2;">
        <div style="color:var(--t3);font-size:11px;margin-bottom:8px;font-family:'Geist',sans-serif;letter-spacing:.06em;text-transform:uppercase;">FDRS Equation</div>
        <div><span style="color:var(--accent)">FDRS</span> = <span style="color:#c44b3c">(Import Dependency)</span> × 0.30</div>
        <div style="padding-left:52px">+ <span style="color:#c47a3c">(Supplier Concentration)</span> × 0.20</div>
        <div style="padding-left:52px">+ <span style="color:#c9a957">(Production Trend)</span> × 0.15</div>
        <div style="padding-left:52px">+ <span style="color:#c9a957">(Food Inflation)</span> × 0.15</div>
        <div style="padding-left:52px">+ <span style="color:#6ba36b">(Climate Vulnerability)</span> × 0.10</div>
        <div style="padding-left:52px">+ <span style="color:#6ba36b">(Conflict / Logistics)</span> × 0.10</div>
      </div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:10px;">
        ${[
          {w:'30%',label:'Import Dependency',col:'#c44b3c',
           desc:'Share of caloric supply sourced from abroad. Computed from FAO Food Balance Sheets 2022. 100 = 100% imported; 0 = fully self-sufficient.'},
          {w:'20%',label:'Supplier Concentration',col:'#c47a3c',
           desc:'Herfindahl-Hirschman Index (HHI) of the top-5 import source countries. 100 = single-source monopoly; 0 = perfectly diversified.'},
          {w:'15%',label:'Production Trend',col:'#c9a957',
           desc:'5-year CAGR of domestic food production (USDA PSD 2018-2024). Negative trends score higher. Normalized against global peers.'},
          {w:'15%',label:'Food Inflation',col:'#c9a957',
           desc:'Annual food CPI change (World Bank WDI FP.CPI.TOTL.ZG, latest year 2023). Captures affordability deterioration. 100 = >60% inflation.'},
          {w:'10%',label:'Climate Vulnerability',col:'#6ba36b',
           desc:'ND-GAIN Country Index 2023 vulnerability sub-score. Captures exposure and sensitivity of food/water/health systems to climate hazards.'},
          {w:'10%',label:'Conflict / Logistics',col:'#6ba36b',
           desc:'Composite of ACLED 2023 conflict intensity and World Bank LPI 2023 (logistics performance). High conflict + poor logistics = 100.'},
        ].map(c=>`
          <div style="background:var(--bg0);border:1px solid var(--b1);border-radius:8px;padding:14px 16px;">
            <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;">
              <div style="width:32px;height:32px;border-radius:6px;background:${c.col}18;display:flex;align-items:center;justify-content:center;font-family:'Geist Mono',monospace;font-size:11px;font-weight:700;color:${c.col}">${c.w}</div>
              <div style="font-size:12.5px;font-weight:600;color:var(--t1)">${c.label}</div>
            </div>
            <div style="font-size:11.5px;color:var(--t3);line-height:1.6;">${c.desc}</div>
          </div>
        `).join('')}
      </div>
    </div>

    <!-- Data Sources -->
    <div class="card">
      <div class="card-title"><span class="card-marker"></span> Primary Data Sources</div>
      <div style="display:flex;flex-direction:column;gap:0;">
        ${[
          {src:'FAO Food Balance Sheets',yr:'2022',used:'Import dependency ratios, dietary supply per capita, caloric self-sufficiency',url:'https://www.fao.org/faostat'},
          {src:'FAO GIEWS / FAOSTAT',yr:'2024',used:'Commodity price alerts, crop production statistics, trade flow data',url:'https://www.fao.org/giews'},
          {src:'USDA PSD (Production, Supply & Distribution)',yr:'Oct 2024',used:'Cereal production, consumption, import/export volumes 2018–2024 for all major commodities',url:'https://apps.fas.usda.gov/psdonline'},
          {src:'World Bank WDI — Food Inflation',yr:'2023',used:'Food CPI annual change (FP.CPI.TOTL.ZG) used for FDRS inflation component',url:'https://databank.worldbank.org'},
          {src:'World Bank LPI',yr:'2023',used:'Logistics Performance Index for trade infrastructure quality scoring',url:'https://lpi.worldbank.org'},
          {src:'ND-GAIN Country Index',yr:'2023',used:'Climate vulnerability sub-scores for food, water and health system exposure',url:'https://gain.nd.edu'},
          {src:'ACLED (Armed Conflict Location & Event Data)',yr:'2023',used:'Conflict fatality and event counts by country for logistics/conflict component',url:'https://acleddata.com'},
          {src:'ITC TradeMap',yr:'2023',used:'Top export destination countries and bilateral trade share data',url:'https://www.trademap.org'},
          {src:'FAO-OECD Agricultural Outlook',yr:'2023–2032',used:'Baseline 2030 production and consumption trajectories for forecast tab',url:'https://www.oecd-ilibrary.org'},
          {src:'UN World Population Prospects',yr:'2022',used:'Medium-scenario population projections to 2030 for all countries',url:'https://population.un.org/wpp'},
        ].map((d,i)=>`
          <div style="display:flex;gap:12px;padding:11px 0;${i>0?'border-top:1px solid var(--b1)':''}">
            <div style="min-width:7px;height:7px;border-radius:50%;background:var(--accent);margin-top:6px;flex-shrink:0;"></div>
            <div style="flex:1;">
              <div style="display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;">
                <span style="font-size:12.5px;font-weight:600;color:var(--t1)">${d.src}</span>
                <span style="font-size:10.5px;color:var(--accent);font-family:'Geist Mono',monospace">${d.yr}</span>
              </div>
              <div style="font-size:11.5px;color:var(--t3);margin-top:3px;line-height:1.5;">${d.used}</div>
            </div>
          </div>
        `).join('')}
      </div>
    </div>

    <!-- Forecasting Method -->
    <div class="card">
      <div class="card-title"><span class="card-marker"></span> Forecasting to 2030</div>
      <div style="font-size:12.5px;color:var(--t2);line-height:1.75;margin-bottom:16px;">
        The 2030 FDRS forecast is not a machine-learning model — it is a <b style="color:var(--t1)">structured expert projection</b>
        that combines baseline trajectories from international agencies with country-specific stress factors.
      </div>
      <div style="display:flex;flex-direction:column;gap:12px;">
        ${[
          {step:'01',title:'Baseline from FAO-OECD Outlook',
           body:'Production and consumption projections 2023–2032 from the FAO-OECD Agricultural Outlook provide the starting trajectory. Each country’s supply balance in 2030 sets the baseline import dependency component.'},
          {step:'02',title:'Climate Adjustment',
           body:'ND-GAIN forward projections and IPCC AR6 regional crop yield scenarios (SSP2-4.5) adjust the baseline. High-vulnerability countries (Sub-Saharan Africa, South Asia, MENA) receive upward adjustments of 5–15 FDRS points.'},
          {step:'03',title:'Demographic Scaling',
           body:'UN WPP medium-scenario population growth is applied to consumption. Countries with >2% annual population growth face increasing import dependency unless production growth matches pace.'},
          {step:'04',title:'Policy & Geopolitical Overlay',
           body:'Active conflicts (ACLED), sanctions regimes, trade policy trajectories and infrastructure investment pipelines are assessed qualitatively. War-affected countries (Yemen, Sudan, Myanmar, Haiti) receive manual overrides.'},
          {step:'05',title:'Validation Against Historical FDRS',
           body:'The model is back-tested against 2018–2023 realized data. Countries where the model diverged >8 points from realized outcomes are flagged and recalibrated annually.'},
          {step:'06',title:'Uncertainty Bands',
           body:'Each 2030 forecast carries an implicit ±5–12 point uncertainty range. The displayed number represents the central estimate. Scenario Simulator tab allows stress-testing beyond the baseline.'},
        ].map(s=>`
          <div style="display:flex;gap:14px;align-items:flex-start;">
            <div style="font-family:'Geist Mono',monospace;font-size:10.5px;color:var(--accent);background:var(--accent)18;border-radius:4px;padding:3px 7px;flex-shrink:0;margin-top:1px;">${s.step}</div>
            <div>
              <div style="font-size:12.5px;font-weight:600;color:var(--t1);margin-bottom:4px;">${s.title}</div>
              <div style="font-size:11.5px;color:var(--t3);line-height:1.6;">${s.body}</div>
            </div>
          </div>
        `).join('')}
      </div>
    </div>

    <!-- Limitations & Caveats -->
    <div class="card" style="grid-column:1/-1">
      <div class="card-title"><span class="card-marker"></span> Limitations &amp; Caveats</div>
      <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:14px;">
        ${[
          {icon:'⚠',title:'Data Lag',body:'Most source datasets have 12–24 month reporting lags. 2024 data reflects primarily 2022–2023 conditions. Rapid-onset crises (conflict, drought) may not be fully reflected until the next annual update.'},
          {icon:'⚠',title:'Sub-national Variation',body:'Country-level averages can mask severe regional disparities. Somalia’s FDRS=88 reflects the national average — some regions face acute famine while others are relatively stable.'},
          {icon:'⚠',title:'US States Methodology',body:'US state scores use the same FDRS formula adapted for sub-national context. Import dependency reflects state-level food sourcing from other states and abroad. Production data from USDA NASS 2023.'},
          {icon:'ℹ',title:'FDRS is Relative, Not Absolute',body:'A score of 50 does not mean 50% food insecure. It means a country is in the middle of the global risk distribution. Context always matters — compare within regions for most meaningful insights.'},
          {icon:'ℹ',title:'Trade Flow Arcs are Directional',body:'Import (red) arcs show primary commodity flows from top supplier countries. Export (green) arcs show primary destination markets. Arc weight reflects approximate % share, not exact trade volumes.'},
          {icon:'ℹ',title:'Annual Update Cycle',body:'FoodShield AI data is updated annually following release of FAO FAOSTAT, USDA PSD October update, and World Bank WDI. Next scheduled update: October 2026.'},
        ].map(c=>`
          <div style="background:var(--bg1);border:1px solid var(--b1);border-radius:8px;padding:14px 16px;">
            <div style="font-size:12.5px;font-weight:600;color:var(--t1);margin-bottom:6px;">${c.icon} ${c.title}</div>
            <div style="font-size:11.5px;color:var(--t3);line-height:1.6;">${c.body}</div>
          </div>
        `).join('')}
      </div>
    </div>

    <!-- Version info -->
    <div class="card" style="grid-column:1/-1">
      <div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:12px;">
        <div>
          <div style="font-size:12px;color:var(--t3);line-height:1.8;">
            <b style="color:var(--t2)">FoodShield AI v7</b> · Built May 2026 ·
            ${COUNTRIES.filter(c=>!c.iso.startsWith('US-')).length} countries + 50 US states ·
            Data vintage: FAO 2022–2024, USDA Oct 2024, ND-GAIN 2023, ACLED 2023, World Bank 2023
          </div>
          <div style="font-size:11px;color:var(--t3);margin-top:4px;">
            FDRS scores represent expert-calibrated composite indices. Not intended as sole basis for policy decisions.
            Always consult primary sources and country-level experts before acting on forecasts.
          </div>
        </div>
        <div style="font-family:'Geist Mono',monospace;font-size:10.5px;color:var(--accent);background:var(--accent)18;border-radius:6px;padding:8px 14px;white-space:nowrap;">
          v7.0 · May 2026
        </div>
      </div>
    </div>

  </div>`;
}

// ════════════════════════════════════════════════════
// FoodShield AI — Flat dark world map + Trade Flow Atlas
// Leaflet + Carto Dark Matter base, FDRS choropleth,
// animated curved import/export arcs.
// ════════════════════════════════════════════════════
(function () {
  'use strict';

  // Supplier name → geojson name aliases
  const SUPPLIER_ALIASES = {
    'usa': ['united states of america','united states'],
    'uk':  ['united kingdom','england'],
    'uae': ['united arab emirates'],
    'south korea': ['south korea','korea','republic of korea'],
    'czech rep.': ['czechia','czech republic'],
    'czechia': ['czech republic'],
    'russia': ['russia','russian federation'],
    'ivory coast': ["côte d'ivoire", "cote d'ivoire", 'ivory coast'],
  };
  // Lat/Lng for non-country suppliers (organizations)
  const FALLBACK_COORDS = {
    'wfp': [41.9, 12.5],     // Rome HQ
    'eu':  [50.85, 4.35],    // Brussels
  };
  // Plausible export destinations (when net exporter)
  const GENERIC_EXPORT_MARKETS = [
    'China','India','Egypt','Japan','United Kingdom',
    'Indonesia','Mexico','Saudi Arabia','Turkey','Philippines'
  ];

  const G = {
    map: null,
    countryLayer: null,
    flowLines: [],
    flowMarkers: [],
    selectedISO: null,
    selectedLayer: null,
    flowsActive: false,
    flows: [],
    centroids: new Map(),    // ISO → [lat, lng]
    features: [],
    isoMap: new Map(),       // ISO → feature
    nameMap: new Map(),      // lower-name → feature
    ready: false,
    width: 0, height: 0,
  };

  function init() {
    const container = document.getElementById('map');
    if (!container || typeof L === 'undefined') return;

    const map = L.map(container, {
      center: [22, 10],
      zoom: 2.4,
      minZoom: 2,
      maxZoom: 6,
      zoomControl: false,
      attributionControl: false,
      worldCopyJump: false,
      zoomSnap: 0.25,
      zoomDelta: 0.5,
      preferCanvas: false,
    });
    G.map = map;

    // Base — dark, no labels
    L.tileLayer(
      'https://{s}.basemaps.cartocdn.com/dark_nolabels/{z}/{x}/{y}{r}.png',
      { subdomains: 'abcd', maxZoom: 19 }
    ).addTo(map);

    // Custom pane for labels — sits ABOVE countries
    map.createPane('labels');
    map.getPane('labels').style.zIndex = 650;
    map.getPane('labels').style.pointerEvents = 'none';
    L.tileLayer(
      'https://{s}.basemaps.cartocdn.com/dark_only_labels/{z}/{x}/{y}{r}.png',
      { subdomains: 'abcd', maxZoom: 19, pane: 'labels', opacity: 0.85 }
    ).addTo(map);

    // Pane for flow arcs (above labels)
    map.createPane('flows');
    map.getPane('flows').style.zIndex = 660;
    map.getPane('flows').style.pointerEvents = 'none';

    // Pane for flow endpoint markers
    map.createPane('flowmarkers');
    map.getPane('flowmarkers').style.zIndex = 670;

    // Load geo data
    fetch('https://raw.githubusercontent.com/holtzy/D3-graph-gallery/master/DATA/world.geojson')
      .then(r => r.json())
      .then(data => {
        G.features = data.features;
        G.features.forEach(f => {
          const name = (f.properties.name || '').toLowerCase();
          if (name) G.nameMap.set(name, f);
        });
        // Cross-reference COUNTRIES (has ISO) with name-keyed features
        if (typeof COUNTRIES !== 'undefined') {
          const NAME_ALIASES = {
            'United States': ['usa','united states of america','united states'],
            'United Kingdom': ['united kingdom','england','great britain'],
            'UAE': ['united arab emirates','uae'],
            'South Korea': ['south korea','korea, south','korea','republic of korea'],
            'North Korea': ['north korea','korea, north',"democratic people's republic of korea"],
            'Czech Republic': ['czechia','czech republic','czech rep.'],
            'Russia': ['russia','russian federation'],
            'Iran': ['iran','iran (islamic republic of)'],
            'Syria': ['syria','syrian arab republic'],
            'Vietnam': ['vietnam','viet nam'],
            'Tanzania': ['tanzania','united republic of tanzania'],
            'Bolivia': ['bolivia','bolivia (plurinational state of)'],
            'Venezuela': ['venezuela','venezuela (bolivarian republic of)'],
            'DR Congo': ['democratic republic of the congo','congo, dem. rep.','dr congo','congo (kinshasa)'],
            'Republic of Congo': ['republic of the congo','congo','congo (brazzaville)','congo, rep.'],
            "Ivory Coast": ["côte d'ivoire","cote d'ivoire",'ivory coast'],
            'Myanmar': ['myanmar','burma'],
            'Laos': ['laos',"lao people's democratic republic",'lao pdr'],
            'Palestine': ['palestine','west bank','gaza strip'],
            'Taiwan': ['taiwan','taiwan, province of china'],
            'Moldova': ['moldova','republic of moldova'],
            'North Macedonia': ['north macedonia','republic of north macedonia','macedonia'],
            'Bosnia & Herzegovina': ['bosnia and herzegovina','bosnia & herzegovina'],
            'Eswatini': ['eswatini','swaziland'],
            'Timor-Leste': ['timor-leste','east timor'],
            'Brunei': ['brunei','brunei darussalam'],
          };
          COUNTRIES.forEach(co => {
            const aliases = (NAME_ALIASES[co.name] || []).concat([co.name.toLowerCase()]);
            for (const n of aliases) {
              if (G.nameMap.has(n)) {
                const f = G.nameMap.get(n);
                G.isoMap.set(co.iso, f);
                try {
                  const c = d3.geoCentroid(f); // [lng, lat]
                  G.centroids.set(co.iso, [c[1], c[0]]); // Leaflet wants [lat, lng]
                } catch (e) {}
                break;
              }
            }
          });
        }

        G.countryLayer = L.geoJSON(data, {
          style: styleFeature,
          onEachFeature: onEachFeature,
        }).addTo(map);

        G.ready = true;

        if (typeof updateOverlayStats === 'function') updateOverlayStats();
        if (typeof updateRail === 'function') updateRail();
      })
      .catch(err => console.warn('map geojson load failed', err));
  }

  // ── Country styling ──
  function styleFeature(f) {
    const c = lookupCountry(f);
    if (!c) return { fillColor:'#1a1a20', color:'#000', weight:0.4, fillOpacity:0.9 };

    // Filter dim
    if (typeof activeFilters !== 'undefined') {
      const af = activeFilters;
      let dimmed = false;
      if (af.region && c.region !== af.region) dimmed = true;
      if (af.risk && riskLabel(c.fdrs) !== af.risk) dimmed = true;
      if (af.commodity === 'wheat' && c.w < 30) dimmed = true;
      if (af.commodity === 'rice' && c.r < 30) dimmed = true;
      if (af.commodity === 'maize' && c.m < 30) dimmed = true;
      if (af.commodity === 'fertilizer' && c.c[2] < 40) dimmed = true;
      if (dimmed) return { fillColor:'#16161c', color:'#000', weight:0.3, fillOpacity:0.85 };
    }

    // FDRS → opacity (vulnerable countries pop, resilient ones recede)
    const t = c.fdrs / 100;
    const fillOpacity = 0.18 + Math.pow(t, 0.85) * 0.78;
    return {
      fillColor: riskColor(c.fdrs),
      color: '#000',
      weight: 0.45,
      fillOpacity,
    };
  }

  function onEachFeature(f, layer) {
    const c = lookupCountry(f);
    layer.on({
      mouseover: function (e) {
        const l = e.target;
        l.setStyle({ weight: 1.3, color: '#d49a4a' });
        try { l.bringToFront(); } catch (err) {}
        showTooltip(e.originalEvent || e, f, c);
      },
      mousemove: function (e) {
        moveTooltip(e.originalEvent || e);
      },
      mouseout: function (e) {
        G.countryLayer.resetStyle(e.target);
        hideTooltip();
        if (G.selectedISO && c && c.iso === G.selectedISO) {
          e.target.setStyle({ weight: 1.7, color: '#d49a4a' });
          try { e.target.bringToFront(); } catch (err) {}
        }
      },
      click: function () {
        if (c && typeof openPanel === 'function') openPanel(c);
      },
    });
  }

  function lookupCountry(f) {
    return typeof lookupFeature === 'function' ? lookupFeature(f) : null;
  }

  // ── Tooltip ──
  let tipEl;
  function ensureTip() {
    if (!tipEl) {
      tipEl = document.createElement('div');
      tipEl.className = 'globe-tooltip';
      document.body.appendChild(tipEl);
    }
    return tipEl;
  }
  function showTooltip(ev, f, c) {
    const el = ensureTip();
    const name = f.properties.name || 'Unknown';
    el.innerHTML = c
      ? `<div class="gt-name">${c.name}</div>
         <div class="gt-score"><span style="color:${riskColor(c.fdrs)}">${c.fdrs}</span> <span class="gt-meta">${riskLabel(c.fdrs)}</span></div>
         <div class="gt-meta">${c.region} · Inflation ${c.fi}%</div>`
      : `<div class="gt-name">${name}</div><div class="gt-meta">No data</div>`;
    el.style.display = 'block';
    moveTooltip(ev);
  }
  function moveTooltip(ev) {
    if (!tipEl) return;
    const x = (ev.clientX || 0) + 14;
    const y = (ev.clientY || 0) + 14;
    tipEl.style.left = x + 'px';
    tipEl.style.top  = y + 'px';
  }
  function hideTooltip() { if (tipEl) tipEl.style.display = 'none'; }

  // ── Selection ──
  function selectCountry(iso) {
    G.selectedISO = iso;
    // reset prior selection
    if (G.countryLayer) G.countryLayer.resetStyle();
    if (!G.countryLayer) return;

    G.countryLayer.eachLayer(layer => {
      const cc = lookupCountry(layer.feature);
      if (cc && cc.iso === iso) {
        G.selectedLayer = layer;
        try {
          layer.bringToFront();
          layer.setStyle({ weight: 1.7, color: '#d49a4a' });
          const bounds = layer.getBounds();
          G.map.flyToBounds(bounds, {
            maxZoom: 4,
            padding: [80, 80],
            duration: 0.9,
            easeLinearity: 0.4,
          });
        } catch (e) {}
      }
    });
  }
  function deselectCountry() {
    G.selectedISO = null;
    G.selectedLayer = null;
    clearFlows();
    if (G.countryLayer) G.countryLayer.resetStyle();
  }
  function rotateToCountry(iso) { selectCountry(iso); }

  function render() {
    if (G.countryLayer) G.countryLayer.setStyle(styleFeature);
    if (G.selectedLayer) {
      G.selectedLayer.setStyle({ weight: 1.7, color: '#d49a4a' });
      try { G.selectedLayer.bringToFront(); } catch (e) {}
    }
  }

  // ── Supplier resolution ──
  function findSupplierCoord(supplierName) {
    if (!supplierName) return null;
    const s = supplierName.toLowerCase().trim();
    if (FALLBACK_COORDS[s]) return FALLBACK_COORDS[s];
    if (G.nameMap.has(s)) {
      const f = G.nameMap.get(s);
      try { const c = d3.geoCentroid(f); return [c[1], c[0]]; } catch (e) {}
    }
    if (SUPPLIER_ALIASES[s]) {
      for (const alt of SUPPLIER_ALIASES[s]) {
        if (G.nameMap.has(alt)) {
          const f = G.nameMap.get(alt);
          try { const c = d3.geoCentroid(f); return [c[1], c[0]]; } catch (e) {}
        }
      }
    }
    // Partial match
    for (const k of G.nameMap.keys()) {
      if (k.includes(s) || s.includes(k)) {
        const f = G.nameMap.get(k);
        try { const c = d3.geoCentroid(f); return [c[1], c[0]]; } catch (e) {}
      }
    }
    if (typeof byName === 'function') {
      const co = byName(supplierName);
      if (co) return G.centroids.get(co.iso);
    }
    return null;
  }

  // ── Trade Flow Atlas ──
  // US state centroids (approximate [lat, lng])
  const US_STATE_CENTROIDS = {
    'US-CA':[37.2,-119.7],'US-TX':[31.5,-99.3],'US-IA':[42.0,-93.5],
    'US-IL':[40.0,-89.2],'US-KS':[38.5,-98.4],'US-NE':[41.5,-99.9],
    'US-MN':[46.4,-93.3],'US-IN':[39.9,-86.3],'US-OH':[40.4,-82.8],
    'US-ND':[47.5,-100.5],'US-WA':[47.4,-120.4],'US-FL':[27.8,-81.7],
    'US-GA':[32.7,-83.4],'US-NC':[35.6,-79.8],'US-WI':[44.5,-89.8],
    'US-MO':[38.5,-92.5],'US-AR':[34.8,-92.2],'US-MT':[46.9,-110.5],
    'US-CO':[39.0,-105.5],'US-MI':[44.3,-85.4],'US-PA':[40.9,-77.8],
    'US-NY':[42.9,-75.5],'US-AZ':[34.3,-111.7],'US-ID':[44.4,-114.6],
    'US-SD':[44.4,-100.2],'US-OK':[35.5,-97.5],'US-KY':[37.5,-85.3],
    'US-TN':[35.8,-86.4],'US-AL':[32.8,-86.8],'US-MS':[32.7,-89.7],
    'US-LA':[31.2,-91.8],'US-OR':[44.1,-120.5],'US-VA':[37.9,-78.5],
    'US-SC':[33.9,-80.9],'US-NM':[34.5,-106.2],'US-NV':[39.3,-116.6],
    'US-UT':[39.5,-111.5],'US-HI':[20.3,-156.4],'US-AK':[64.2,-153.4],
    'US-NJ':[40.1,-74.5],'US-CT':[41.6,-72.7],'US-MA':[42.3,-71.8],
    'US-MD':[39.0,-76.8],'US-DE':[38.9,-75.5],'US-VT':[44.1,-72.7],
    'US-ME':[45.4,-69.0],'US-NH':[43.7,-71.6],'US-RI':[41.7,-71.5],
    'US-WV':[38.5,-80.5],'US-WY':[43.0,-107.6],
  };

  function getCoord(nameOrISO) {
    if (!nameOrISO) return null;
    // US state ISO
    if (US_STATE_CENTROIDS[nameOrISO]) return US_STATE_CENTROIDS[nameOrISO];
    // Country centroid by ISO
    if (G.centroids.has(nameOrISO)) return G.centroids.get(nameOrISO);
    // Country centroid by name
    return findSupplierCoord(nameOrISO);
  }

  function drawFlows() {
    if (!G.selectedISO || typeof byISO !== 'function') return;
    const country = byISO(G.selectedISO);
    if (!country) return;

    const isState = G.selectedISO.startsWith('US-');
    const center = isState
      ? US_STATE_CENTROIDS[G.selectedISO]
      : G.centroids.get(G.selectedISO);
    if (!center) return;

    G.flowsActive = true;
    G.flows = [];

    const importCommodities = country.imports || [];
    const exportCommodities = country.exports || [];
    const exportDests = country.exportDests || [];

    if (isState) {
      // ── US STATE FLOWS ──────────────────────────────────────────────
      // Imports: show top 3 supplier states/countries + commodity label
      const stateImportSources = [
        // Key inter-state supply relationships
        ...(country.suppliers || []).slice(0, 3).map((sup, i) => ({
          name: sup, commodity: importCommodities[i] || 'Food products',
          pct: (country.supPct || [])[i] || 20,
        })),
      ];
      stateImportSources.forEach((src, i) => {
        const coord = getCoord(src.name);
        if (!coord) return;
        G.flows.push({
          type: 'import', from: coord, to: center,
          label: src.name, commodity: src.commodity,
          weight: src.pct, delay: i * 160,
        });
      });

      // Exports: show top 3 destinations + commodity
      exportDests.slice(0, 3).forEach((dest, i) => {
        const coord = getCoord(dest);
        if (!coord) return;
        G.flows.push({
          type: 'export', from: center, to: coord,
          label: dest, commodity: exportCommodities[i] || 'Agricultural goods',
          weight: [40, 28, 16][i] || 12,
          delay: 250 + i * 160,
        });
      });

    } else {
      // ── COUNTRY FLOWS ───────────────────────────────────────────────
      // Imports: top 5 suppliers with commodity
      (country.suppliers || []).slice(0, 5).forEach((sup, i) => {
        const coord = getCoord(sup);
        if (!coord) return;
        G.flows.push({
          type: 'import', from: coord, to: center,
          label: sup, commodity: importCommodities[i] || 'Food products',
          weight: (country.supPct || [])[i] || 20,
          delay: i * 160,
        });
      });

      // Exports: use real exportDests with commodity labels
      const destList = exportDests.length
        ? exportDests
        : ['China','Japan','Germany','USA','Netherlands'];
      destList.slice(0, 4).forEach((dest, i) => {
        const coord = getCoord(dest);
        if (!coord) return;
        G.flows.push({
          type: 'export', from: center, to: coord,
          label: dest, commodity: exportCommodities[i] || 'Exports',
          weight: [35, 25, 18, 12][i] || 10,
          delay: 280 + i * 160,
        });
      });
    }

    renderFlows();
  }

  function renderFlows() {
    clearFlowsOnly();
    if (!G.flows.length) return;

    G.flows.forEach((flow) => {
      const segs = greatCirclePath(flow.from, flow.to, 32);
      const color = flow.type === 'import' ? '#c44b3c' : '#6ba36b';

      // Dim base arc
      const baseLine = L.polyline(segs, {
        color, weight: 1, opacity: 0.32,
        interactive: false, pane: 'flows',
        className: 'flow-base',
      }).addTo(G.map);
      G.flowLines.push(baseLine);

      // Animated overlay
      const anim = L.polyline(segs, {
        color, weight: 2, opacity: 0.95,
        interactive: false, pane: 'flows',
        className: 'flow-anim',
        lineCap: 'round',
      }).addTo(G.map);
      G.flowLines.push(anim);

      // Initial dash state
      requestAnimationFrame(() => {
        const el = anim.getElement();
        if (!el) return;
        let total = 0;
        try { total = el.getTotalLength(); } catch (e) { total = 1000; }
        el.style.strokeDasharray = total + ' ' + total;
        el.style.strokeDashoffset = total;
        setTimeout(() => {
          el.style.transition = 'stroke-dashoffset 1.5s cubic-bezier(.4,0,.2,1)';
          el.style.strokeDashoffset = '0';
          setTimeout(() => addFlowEndpointMarker(flow, color), 1500);
        }, flow.delay);
      });
    });

    // Add a pulsing dest marker on the selected country
    const center = G.centroids.get(G.selectedISO);
    if (center) {
      const pulse = L.marker(center, {
        pane: 'flowmarkers',
        interactive: false,
        icon: L.divIcon({
          className: 'flow-center-pulse',
          html: '<div class="flow-center-dot"></div><div class="flow-center-ring"></div>',
          iconSize: [18, 18],
          iconAnchor: [9, 9],
        }),
      }).addTo(G.map);
      G.flowMarkers.push(pulse);
    }
  }

  function addFlowEndpointMarker(flow, color) {
    const target = flow.type === 'import' ? flow.from : flow.to;
    const commodity = flow.commodity || '';
    const typeIcon = flow.type === 'import' ? '▼' : '▲';
    const labelHtml = `
      <div class="flow-endpoint" data-type="${flow.type}">
        <div class="flow-endpoint-dot" style="background:${color}"></div>
        <div class="flow-endpoint-label">
          <span style="font-weight:600">${flow.label}</span>
          ${commodity ? `<span class="flow-endpoint-commodity">${typeIcon} ${commodity}</span>` : ''}
          <span class="flow-endpoint-pct">${flow.weight}%</span>
        </div>
      </div>`;
    const m = L.marker(target, {
      pane: 'flowmarkers',
      interactive: false,
      icon: L.divIcon({
        className: 'flow-endpoint-wrap',
        html: labelHtml,
        iconSize: [0, 0],
        iconAnchor: [0, 0],
      }),
    }).addTo(G.map);
    G.flowMarkers.push(m);
  }

  function clearFlowsOnly() {
    G.flowLines.forEach(l => { try { G.map.removeLayer(l); } catch (e) {} });
    G.flowMarkers.forEach(m => { try { G.map.removeLayer(m); } catch (e) {} });
    G.flowLines = [];
    G.flowMarkers = [];
  }

  function clearFlows() {
    G.flowsActive = false;
    G.flows = [];
    clearFlowsOnly();
  }

  // Great-circle interpolation [lat,lng] → segments
  function greatCirclePath(a, b, n) {
    // a, b are [lat, lng]; d3.geoInterpolate wants [lng, lat]
    const interp = d3.geoInterpolate([a[1], a[0]], [b[1], b[0]]);
    const out = [];
    for (let i = 0; i <= n; i++) {
      const p = interp(i / n); // [lng, lat]
      out.push([p[1], p[0]]);
    }
    return out;
  }

  // Public API (keeps same shape as the previous Globe module)
  window.Globe = {
    init, render,
    selectCountry, deselectCountry, rotateToCountry,
    drawFlows, clearFlows,
    isActive: () => G.flowsActive,
    state: G,
  };
})();

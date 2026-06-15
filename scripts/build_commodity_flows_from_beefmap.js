/* ============================================================
   build_commodity_flows_from_beefmap.js
   ------------------------------------------------------------
   SINGLE SOURCE OF TRUTH for FoodShield's beef trade data.

   Reads the World Beef Map dataset (beef-data.js → window.BEEF) and
   regenerates data/commodity_flows.json so FoodShield renders EXACTLY
   what the Beefmap shows — no hand-edited drift, no fabricated flows.

   Run from the FoodShield project root:
     node scripts/build_commodity_flows_from_beefmap.js \
          "/Users/fedde/Documents/Claude/Projects/Beefmap preview/beef-data.js" \
          "data/commodity_flows.beef.json"

   (If no path is given it tries the default Beefmap location. If no output path
   is given it overwrites data/commodity_flows.json.)

   FAITHFUL IMPORT — what carries over verbatim from the Beefmap:
     • FLOWS      → flows[]            (from,to,value(kt),kind,src,note)
     • BALANCE_2025.rows → balances{}  (prod/cons/net per country, latest yr)
     • RANKINGS   → rankings{exporters,importers}
     • SOURCES    → _sources{}
     • COMPANIES, COMMODITIES, GLOBAL, FORECAST → carried for reference

   Re-runnable: overwrites commodity_flows.json each time, so the two
   never diverge again. This is the fix for "beef data all messed up".
   ============================================================ */
const fs = require('fs');
const path = require('path');

const DEFAULT_BEEFMAP = '/Users/fedde/Documents/Claude/Projects/Beefmap preview/beef-data.js';
const beefmapPath = process.argv[2] || DEFAULT_BEEFMAP;
const OUT = process.argv[3]
  ? path.resolve(process.argv[3])
  : path.join(__dirname, '..', 'data', 'commodity_flows.json');

function loadBeef(p) {
  const src = fs.readFileSync(p, 'utf8');
  const sandbox = { window: {} };
  // beef-data.js is an IIFE that ends with `window.BEEF = {...}`
  new Function('window', src)(sandbox.window);
  if (!sandbox.window.BEEF) throw new Error('beef-data.js did not set window.BEEF');
  return sandbox.window.BEEF;
}

function build(B) {
  // --- balances: from BALANCE_2025.rows (authoritative latest-year prod/cons/exp/imp) ---
  const balRows = (B.BALANCE_2025 && B.BALANCE_2025.rows) || [];
  const balSrc = (B.BALANCE_2025 && B.BALANCE_2025.src) || 'usda';
  const balYear = (B.BALANCE_2025 && B.BALANCE_2025.year) || 2024;
  const balances = {};
  balRows.forEach(r => {
    const net = (r.exp || 0) - (r.imp || 0);
    balances[r.iso] = {
      iso3: r.iso,
      prod: r.prod, cons: r.cons, exp: r.exp, imp: r.imp,
      net,                                  // + = net exporter, − = net importer
      net_series: [net],                    // single authoritative point
      net: [net],                           // FoodShield reads net[last]
      year: balYear,
      src: balSrc,
      flag: 'sourced',
      note: `USDA/Rabobank ${balYear} balance: prod ${r.prod} / cons ${r.cons} / exp ${r.exp} / imp ${r.imp} kt CWE.`,
    };
  });

  // --- flows: VERBATIM from the Beefmap (the lines on the map) ---
  const flows = (B.FLOWS || []).map(f => ({
    from: f.from, to: f.to, value: f.value,
    kind: f.kind, src: f.src, note: f.note,
  }));

  // --- rankings: verbatim ---
  const rankings = {
    exporters: (B.RANKINGS && B.RANKINGS.exporters) || [],
    importers: (B.RANKINGS && B.RANKINGS.importers) || [],
    exportSrc: (B.RANKINGS && B.RANKINGS.exportSrc) || null,
    importSrc: (B.RANKINGS && B.RANKINGS.importSrc) || null,
    note: (B.RANKINGS && B.RANKINGS.note) || null,
  };

  const out = {
    _meta: {
      generated_at: new Date().toISOString(),
      version: 'v23',
      method: 'Faithful import from World Beef Map (beef-data.js). Re-run scripts/build_commodity_flows_from_beefmap.js to refresh.',
      source: 'RaboResearch World Beef Map 2025 + USDA FAS PSD + ABIEC + ITC + UN Comtrade (Beefmap dataset)',
    },
    _sources: B.SOURCES || {},
    commodities: {
      beef: {
        hs: '0201+0202',
        unit: 'kt CWE',
        balances,
        flows,
        rankings,
        companies: B.COMPANIES || [],
        commodities_split: B.COMMODITIES || {},   // fresh/frozen/live/offal/hides
        global: B.GLOBAL || {},
        forecast: B.FORECAST || {},
        scenarios: B.SCENARIOS || {},
      },
    },
  };
  return out;
}

function main() {
  console.log(`[beefmap→foodshield] reading ${beefmapPath}`);
  const B = loadBeef(beefmapPath);
  const out = build(B);
  fs.writeFileSync(OUT, JSON.stringify(out, null, 2));
  const beef = out.commodities.beef;
  console.log(`[beefmap→foodshield] wrote ${OUT}`);
  console.log(`  flows:    ${beef.flows.length} (verbatim from Beefmap FLOWS)`);
  console.log(`  balances: ${Object.keys(beef.balances).length} countries`);
  console.log(`  sources:  ${Object.keys(out._sources).length}`);
  console.log(`  rankings: ${beef.rankings.exporters.length} exporters / ${beef.rankings.importers.length} importers`);
  // sanity prints
  const bra = beef.flows.filter(f => f.from === 'BRA').length;
  const chn = beef.flows.filter(f => f.to === 'CHN').length;
  console.log(`  BRA exports: ${bra} · CHN imports: ${chn} (should match Beefmap)`);
}

main();

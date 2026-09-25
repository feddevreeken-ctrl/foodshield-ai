/* FoodShield displayed score. Shared by the browser and publication builder. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory();
  else root.FoodShieldScore = factory();
}(typeof window !== 'undefined' ? window : this, function () {
  'use strict';
  var weights = [0.23,0.16,0.11,0.09,0.09,0.08,0.06,0.12,0.06];
  function finite(v) { return typeof v === 'number' && isFinite(v); }
  function clip(v) { return Math.max(0, Math.min(100, v)); }
  function decomposition(cv, sce) {
    cv = Array.isArray(cv) ? cv : [];
    var components = [], contributions = [], observedWeight = 0, base = 0;
    for (var i = 0; i < weights.length; i++) {
      var v = finite(cv[i]) ? cv[i] : i === 6 && finite(sce) ? sce : null;
      components.push(v);
      if (v !== null) { observedWeight += weights[i]; base += weights[i] * v; }
    }
    if (observedWeight > 0 && observedWeight < 1) base /= observedWeight;
    var divisor = observedWeight > 0 && observedWeight < 1 ? observedWeight : 1;
    components.forEach(function (v, i) { contributions.push(v === null ? null : weights[i] * v / divisor); });
    var amp = components[0] !== null && components[7] !== null ? Math.min(6 * (components[0] / 100) * (components[7] / 100), 6) : 0;
    return {components:components, weights:weights, contributions:contributions, observedWeight:observedWeight,
      base:base, amp:amp, score:observedWeight > 0 ? clip(Math.round(base + amp)) : 0};
  }
  function score(cv, sce) { return decomposition(cv, sce).score; }
var COMMODITY_PRODUCERS = {
  'Wheat': ['CHN','IND','RUS','USA','FRA','CAN','UKR','PAK','AUS','DEU','TUR','ARG','KAZ','POL','GBR','ROU','BRA','IRN','UZB','HUN','ITA','ESP','EGY','MAR','BGR'],
  'Rice':  ['CHN','IND','IDN','BGD','VNM','THA','MMR','PHL','PAK','KHM','JPN','BRA','USA','EGY','MYS','LAO','NPL','MDG','ITA','ESP','TUR'],
  'Maize': ['USA','CHN','BRA','ARG','UKR','IND','MEX','RUS','FRA','ROU','HUN','RSA','ZAF','CAN','IDN','NGA','ETH','TZA','PHL','VNM','SRB','EGY','ITA'],
};

var US_STATE_PRODUCERS = {
  'Wheat': ['US-KS','US-ND','US-MT','US-WA','US-OK','US-ID','US-SD','US-NE','US-CO','US-TX','US-OR','US-MN'],
  'Rice':  ['US-AR','US-CA','US-LA','US-MS','US-TX','US-MO'],
  'Maize': ['US-IA','US-IL','US-NE','US-MN','US-IN','US-OH','US-WI','US-SD','US-MO','US-KS','US-MI','US-KY','US-PA','US-ND'],
};

/* USDA PSD rows older than this are ignored (the bulk still carries 1998 pre-accession rows for some
   EU members); EU members without a current national row use the EU27 bloc balance. */
var PSD_MIN_YEAR = 2022;
var EU27 = ['AUT','BEL','BGR','HRV','CYP','CZE','DNK','EST','FIN','FRA','DEU','GRC','HUN','IRL','ITA','LVA','LTU','LUX','MLT','NLD','POL','PRT','ROU','SVK','SVN','ESP','SWE'];

function commodityTradeDependency(c, commodity, live, menus) {
  if (!c || !c.iso) return null;

  var psdKey = ({ Wheat: 'wheat', Rice: 'rice', Maize: 'corn', Corn: 'corn', Soybeans: 'soybeans' })[commodity];
  var psd = live.psd || {};
  var current = function (r) { return r && finite(r.year) && r.year >= PSD_MIN_YEAR ? r : null; };
  var psdRow = psdKey ? current((psd[c.iso] || {})[psdKey]) : null;
  if (!psdRow && psdKey && EU27.indexOf(c.iso) >= 0) psdRow = current((psd.EU27 || {})[psdKey]);
  if (psdRow && psdRow.consumption_kt > 0 && psdRow.imports_kt != null) {
    var imports = Math.max(0, psdRow.imports_kt);
    var exports = Math.max(0, psdRow.exports_kt || 0);
    var cons    = psdRow.consumption_kt;

    if (commodityTradeDependency.basisOut) commodityTradeDependency.basisOut.basis = 'psd';
    var netImports = imports - exports;
    var ratio = (netImports > 0) ? Math.round((netImports / cons) * 100) : Math.max(0, Math.round((imports / (cons + exports)) * 100));
    return Math.max(0, Math.min(100, ratio));
  }

  if (commodityTradeDependency.basisOut) commodityTradeDependency.basisOut.basis = 'heuristic';
  var inImports = menus.imports.some(function (x) { return (x || '').toLowerCase().indexOf(commodity.toLowerCase()) >= 0; });
  var inExports = menus.exports.some(function (x) { return (x || '').toLowerCase().indexOf(commodity.toLowerCase()) >= 0; });
  var isProducer = (COMMODITY_PRODUCERS[commodity] || []).indexOf(c.iso) >= 0
                  || (US_STATE_PRODUCERS[commodity] || []).indexOf(c.iso) >= 0;

  var isCoreStaple = ['Wheat','Rice','Maize'].indexOf(commodity) >= 0;

  var dep;
  if (isProducer) {

    if (inExports && !inImports) {

      dep = 5;
    } else if (inExports && inImports) {

      var trend = finite((c.c || [])[2]) ? c.c[2] : 50;
      dep = Math.round(15 + trend * 0.35);
    } else if (inImports) {

      var trend = finite((c.c || [])[2]) ? c.c[2] : 50;
      dep = Math.round(25 + trend * 0.5);
    } else {

      dep = 10;
    }
  } else if (isCoreStaple) {

    /* A listed exporter of a core staple is not import-dependent for it. */
    dep = inExports ? 10 : 95;
  } else {

    if (inImports) dep = 90;
    else if (inExports) dep = 75;
    else return null;
  }
  return Math.max(0, Math.min(100, dep));
}

  function supplyChainExposure(c, live, menus) {
    if (!c) return 0;
    var parts = [], ws = [0.45,0.30,0.25];
    ['Wheat','Rice','Maize'].forEach(function (crop, i) {
      var v = commodityTradeDependency(c, crop, live, menus);
      if (v !== null) parts.push({val:v, weight:ws[i]});
    });
    if (!parts.length) return 0;
    var w = parts.reduce(function (s,p) { return s+p.weight; },0);
    var v = parts.reduce(function (s,p) { return s+p.val*p.weight; },0)/w;
    var hhi = (c.c || [])[1];
    v += ((hhi == null ? 50 : hhi)-50)*0.08;
    return clip(Math.round(v));
  }
  function displayed(c, live, menus) {
    var iso = c.iso, cv = (c.c || []).slice(), original = cv.slice();
    var sce = supplyChainExposure(c, live, menus);
    function row(key) { return (live[key] || {})[iso] || {}; }
    function value(v) { return v && v.value; }
    function composite(parts) {
      return parts.reduce(function (s,p) { return s+p.v*p.w; },0) / parts.reduce(function (s,p) { return s+p.w; },0);
    }
    var ndg=row('ndgain'), aqu=row('aqueduct'), ccp=row('cckp'), asap=row('asap'), parts=[];
    if (ndg.food_vulnerability != null) parts.push({v:ndg.food_vulnerability*100,w:0.45});
    var scarcity = aqu.water_stress && aqu.water_stress.score != null ? aqu.water_stress.score : (aqu.drought_risk || {}).score;
    if (scarcity != null) parts.push({v:Math.min(100,scarcity*20),w:0.20});
    if (aqu.flood_risk && aqu.flood_risk.score != null) parts.push({v:Math.min(100,aqu.flood_risk.score*20),w:0.15});
    if (asap.stress_score != null) parts.push({v:clip(asap.stress_score),w:0.20});
    if (ccp.warming_c != null) parts.push({v:clip(ccp.warming_c*50),w:0.20});
    if (ndg.food_vulnerability != null && parts.length >= 2) cv[4]=Math.round(original[4]*0.4+composite(parts)*0.6);
    /* Structural governance and risk only. Live conflict intensity (HAPI) is counted once, in the
       nowcast's conflict_kick, not here as well. */
    var inf=row('inform'), wgi=row('wgi'), lpi=row('lpi'); parts=[];
    if (inf.inform_risk != null) parts.push({v:inf.inform_risk*10,w:0.30});
    if (value(wgi.rule_of_law) != null) parts.push({v:clip((2.5-value(wgi.rule_of_law))*20),w:0.20});
    if (value(lpi.overall) != null) parts.push({v:clip((5-value(lpi.overall))*25),w:0.15});
    if (parts.length >= 2) cv[5]=Math.round(original[5]*0.4+composite(parts)*0.6);
    var d=decomposition(cv,sce), structural=d.score, base=structural;
    var fa=row('feeding_america');
    if (iso.indexOf('US-') === 0 && fa.food_insecurity_pct != null) {
      var mmg=clip(Math.round((fa.food_insecurity_pct-5)/15*75+10));
      base=Math.round(mmg*0.7+structural*0.3);
    }
    var adjustment=row('nowcast').adjustment || 0;
    var displayed=clip(Math.round(base+adjustment));
    return {displayed:displayed, base:base, delta:displayed-base, structural:structural,
      adjustment:adjustment, components:cv, sce:sce, decomposition:d};
  }
  /* Where a trade-dependency figure came from: 'psd' (a current USDA balance) or 'heuristic' (menu fallback). */
  function commodityTradeBasis(c, commodity, live, menus) {
    var out = { basis: null }; commodityTradeDependency.basisOut = out;
    try { commodityTradeDependency(c, commodity, live, menus); } finally { commodityTradeDependency.basisOut = null; }
    return out.basis;
  }
  return {weights:weights, score:score, decomposition:decomposition, displayed:displayed,
    supplyChainExposure:supplyChainExposure, commodityTradeDependency:commodityTradeDependency, commodityTradeBasis:commodityTradeBasis};
}));

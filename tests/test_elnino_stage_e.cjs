// Actual panel functions, with observed interpolation and explicit-rung fixtures.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('index.html','utf8');
const start=html.indexOf('(function () {',html.indexOf('   THE MAP USES A DIVERGING')),end=html.indexOf('\n})();',start);
const ctx=vm.createContext({window:{location:{href:'http://localhost',search:''}},document:{addEventListener(){}},URL,URLSearchParams,console});
vm.runInContext(html.slice(start,end)+'\nglobalThis.api={S,rowFor,levelValues,modelInterval,modelStateSentence,scenarioSnap,intervalBand,cropEffect,impactColor,fillFor,publishedLevels,interpolateNumeric};\n})();',ctx);
const {api}=ctx,S=api.S;
const exposure=JSON.parse(fs.readFileSync('data/enso_exposure.json'));
S.exp=exposure.data;S.meta.exp=exposure._meta;S.oniLive=1.8;S.oni=2;S.explicitScenario=false;
const close=(a,b)=>assert(Math.abs(a-b)<1e-9,`${a} != ${b}`);
for(const [iso,e] of Object.entries(S.exp)) {
 const r=api.rowFor(iso).lv;
 for(const k of Object.keys(e.levels.el_nino_strong))close(r[k],0.4*e.levels.el_nino_strong[k]+0.6*e.levels.el_nino_very_strong[k]);
}
assert(api.modelStateSentence().includes('interpolated between the +1.5 and +2.0 rungs at the observed +1.80'));
assert.equal(api.scenarioSnap(),null);assert.equal(api.intervalBand(1.8),'Strong El Niño');
close(api.cropEffect({yield_pct_per_oni_nino:10}),18);
assert.equal(api.impactColor(NaN,1),null);assert.equal(api.impactColor('2',1),null);
assert.equal(api.interpolateNumeric(null,5,.5),null);assert.equal(api.interpolateNumeric('low','high',.5),null);
assert.equal(api.interpolateNumeric({x:1},{},.5).x,null);
S.mode='impact';const z=api.rowFor('ZWE');assert.equal(api.fillFor('ZWE'),api.impactColor(Math.round(z.lv.production_shock_pct),z.cov));
S.oniLive=-1.2;S.oni=-1;close(api.rowFor('ZWE').lv.production_shock_pct,.4*S.exp.ZWE.levels.la_nina_strong.production_shock_pct+.6*S.exp.ZWE.levels.la_nina_moderate.production_shock_pct);
close(api.cropEffect({yield_pct_per_oni_nina:10}),-12);
S.explicitScenario=true;S.oni=1.5;assert.equal(api.rowFor('ZWE').lv,S.exp.ZWE.levels.el_nino_strong);assert(api.scenarioSnap());assert(api.modelStateSentence().startsWith('Explicit scenario:'));
S.explicitScenario=false;
for(const oni of [3,-2,.2,-.2]){S.oniLive=oni;assert.equal(api.rowFor('ZWE'),null);assert.equal(api.cropEffect({yield_pct_per_oni_nino:1}),null);}
S.oniLive=0;assert.equal(api.rowFor('ZWE').lv,S.exp.ZWE.levels.neutral);
const pins={la_nina_strong:-1.5,la_nina_moderate:-1,la_nina_weak:-.5,neutral:0,el_nino_weak:.5,el_nino_moderate:1,el_nino_strong:1.5,el_nino_very_strong:2,el_nino_extreme:2.5};
assert.deepEqual(exposure._meta.scenario_levels,pins);
for(const e of Object.values(S.exp))for(const [key,oni] of Object.entries(pins))assert.equal(e.levels[key].oni,oni);
assert.deepEqual(Object.fromEntries(api.publishedLevels().map(r=>[r[0],r[1]])),pins);
console.log('PASS Stage E: all 35 countries interpolate, explicit rungs, negative/neutral/bounds, missing values, colours and ladder metadata');

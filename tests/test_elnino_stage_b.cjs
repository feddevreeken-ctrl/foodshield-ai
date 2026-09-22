// Non-browser checks using the actual panel closure and repository feeds.
// Run from the repo root: node tests/test_elnino_stage_b.cjs
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync('index.html','utf8');
const start = html.indexOf('(function () {', html.indexOf('   THE MAP USES A DIVERGING'));
const end = html.indexOf('\n})();',start);
const nodes = {};
function node(id) { return nodes[id] || (nodes[id] = { innerHTML:'', value:'', style:{}, setAttribute(k,v){this[k]=v;}, querySelectorAll(){return [];}, classList:{toggle(){}, contains(){return false;}} }); }
const ctx = vm.createContext({window:{location:{href:'http://localhost/index.html',search:''}}, document:{getElementById:node,querySelectorAll(){return [];},addEventListener(){}}, URL,console,Date,setTimeout,clearTimeout,Event, URLSearchParams, charts:{}});
vm.runInContext(html.slice(start,end)+`
  mk = function(id,cfg) { if (!S._chartFilter || S._chartFilter.indexOf(id)>=0) globalThis.charts[id]=cfg; };
  globalThis.api={S,calendarSeason,calendarBasis,wireRuler,renderLandHead,renderDetail,renderCoeffs,renderCalendar,renderWater,renderMoney,renderPeople,renderLimits,renderControls,syncInstruments,drawCharts,selectCountry};
})();`, ctx);
const api = ctx.api, S=api.S;
for (const [key,file] of Object.entries({model:'enso_model',calendars:'crop_calendars',enso:'enso',lanes:'enso_lanes',econ:'enso_econ',exp:'enso_exposure',portwatch:'portwatch',pwhist:'portwatch_history',rtfp:'rtfp',ffpi:'fao_ffpi'})) {
 const data=JSON.parse(fs.readFileSync('data/'+file+'.json','utf8'));S[key]=data.data;S.meta[key]=data._meta;
}
S.oniLive=S.enso.latest.anom;
let passed=0;
function test(name,fn){fn();passed++;console.log('ok',name);}
test('longitude ruler supports arrow wrap, Home/End, panels and state switching',()=>{
 const saved={...nodes};let focused=null;
 const stops=['trades','soi','convection','warm_water','upwelling'];
 S.rulerStops=stops.map((id,i)=>[id,'','',[i,10,40+i,60]]);
 const ticks=stops.map(id=>({attrs:{'aria-controls':'enso-step-'+id},setAttribute(k,v){this.attrs[k]=v;},getAttribute(k){return this.attrs[k];},focus(){focused=id;}}));
 const states=['normal','elnino','lanina'].map(state=>({attrs:{'data-ruler-state':state},setAttribute(k,v){this.attrs[k]=v;},getAttribute(k){return this.attrs[k];}}));
 const spot={style:{}};const raster={querySelector(){return spot;}};
 nodes['enso-ruler-figure']={innerHTML:'',setAttribute(k,v){this[k]=v;},querySelector(){return raster;}};
 nodes['enso-mech']={querySelectorAll(q){return q==='[data-ruler]'?ticks:q==='[data-ruler-state]'?states:[];}};
 api.wireRuler();assert.equal(ticks[0].attrs['aria-selected'],'true');
 let prevented=false;ticks[0].onkeydown({key:'ArrowLeft',preventDefault(){prevented=true;}});
 assert(prevented);assert.equal(focused,'upwelling');assert.equal(ticks[4].attrs['aria-selected'],'true');assert.equal(nodes['enso-step-upwelling'].hidden,false);assert.equal(nodes['enso-step-trades'].hidden,true);
 ticks[4].onkeydown({key:'Home',preventDefault(){}});assert.equal(focused,'trades');
 ticks[0].onkeydown({key:'End',preventDefault(){}});assert.equal(focused,'upwelling');
 ticks[2].onclick();assert.equal(ticks[2].attrs['aria-selected'],'true');assert.equal(spot.style.left,'2%');
 states[2].onclick();assert.equal(states[2].attrs['aria-pressed'],'true');assert.equal(states[0].attrs['aria-pressed'],'false');assert(nodes['enso-ruler-figure'].innerHTML.includes('La Niña Pacific state'));
 for(const k of Object.keys(nodes))delete nodes[k];Object.assign(nodes,saved);
});
test('winter-crossing crop has a bounded growing season',()=>{
 const c={plant:[10,11],harvest:[3,4]};
 assert.equal(api.calendarSeason(c,1).stage,'in the ground');assert.equal(api.calendarSeason(c,9).stage,'between seasons');
 assert.equal(api.calendarSeason(c,3).stage,'harvesting now');assert.equal(api.calendarSeason(c,10).stage,'planting');assert(api.calendarSeason(c,9).djf);
});
test('summer crop does not inherit DJF exposure',()=>{
 const c={plant:[4,5],harvest:[9,10]};assert.equal(api.calendarSeason(c,7).stage,'in the ground');assert.equal(api.calendarSeason(c,1).stage,'between seasons');assert(!api.calendarSeason(c,1).djf);
});
test('planting across New Year and second seasons remain bounded',()=>{
 const c={plant:[11,12,1],harvest:[3]};assert.equal(api.calendarSeason(c,2).stage,'in the ground');assert(api.calendarSeason(c,2).djf);
 const multi={plant:[2,3,8],harvest:[6,11]};assert.equal(api.calendarSeason(multi,7).stage,'between seasons');assert.equal(api.calendarSeason(multi,9).stage,'in the ground');assert(!api.calendarSeason(multi,9).djf);
});
test('La Nina slopes are printed once with phase direction recorded',()=>{
 S.sel='ZWE';S.explicitScenario=true;S.oni=-1.5;api.renderDetail();const out=nodes['enso-detail'].innerHTML;
 assert(out.includes('data-direction="fall">+8.'));assert(out.includes('La Niña reverses'));
 assert(!out.includes('hs-bar'));assert(out.includes('Fitted production change'));assert(!out.includes('q='));
 assert.equal(api.calendarBasis('harvest months [5, 6]'),'Harvest: May, Jun');
});
test('country and scenario changes update the single fitted table',()=>{
 S.sel='USA';api.renderCoeffs();assert.equal(nodes['enso-harvest-fig']['data-iso'],'USA');assert(nodes['enso-detail'].innerHTML.includes('La Niña %/ONI <span'));
 S.sel='ZWE';S.oni=1.5;api.renderDetail();assert.equal(nodes['enso-harvest-fig']['data-iso'],'ZWE');assert(nodes['enso-detail'].innerHTML.includes('El Niño %/ONI <span'));
});
test('calendar retains eligible crop rows, month names, and stage groups',()=>{
 api.renderCalendar();const out=nodes['enso-calendar'].innerHTML;
 const expected=Object.entries(S.model).flatMap(([iso,cs])=>Object.entries(cs).filter(([crop,c])=>c.signal&&S.calendars[iso]?.[crop]?.harvest?.length)).length;
 assert.equal((out.match(/data-st=/g)||[]).length,Math.min(30,expected));assert.equal((out.match(/class="cal-group"/g)||[]).length,4);
 for(const m of ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'])assert(out.includes('>'+m+'</i>'));
});
test('shipping leads with published ordinal limits and separate dated AIS',()=>{
 api.renderWater();const out=nodes['enso-water'].innerHTML;assert(out.indexOf('id="enso-c-panama"')<out.indexOf('id="enso-c-panama-daily"'));
 assert(out.includes('Ordinal steps'));assert(out.includes('not to scale in time'));assert(out.includes('2025-09'));assert(out.includes('2026-09'));
 assert.equal((out.match(/data-lane=/g)||[]).length,S.lanes.lanes.length);
 for(const l of S.lanes.lanes){if(l.counter_evidence) assert(out.includes(l.counter_evidence.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;')));}
});
test('Panama chart retains every slot advisory with plain dates',()=>{
 api.drawCharts('ensowater');const c=ctx.charts['enso-c-panama'],p=S.lanes.lanes.find(l=>l.id==='panama');
 assert.equal(c.data.labels.length,p.precedent_2023.steps.length+p.live_2026.steps.length);assert(!c.data.labels.some(l=>l.includes('normal')));assert(c.data.labels.every(l=>/\d/.test(l)&&!l.includes('+')&&!l.includes('yr')));assert(c.data.labels.at(-1).includes('26'));
 assert.deepEqual(Array.from(c.data.datasets[1].data.slice(-p.live_2026.steps.length)),p.live_2026.steps.map(x=>x.total));
 assert(ctx.charts['enso-c-panama-daily'].plugins[0].id==='ensoRules');
});
test('prices have one seven-event surface and one FFPI canvas',()=>{
 api.renderMoney();const out=nodes['enso-money'].innerHTML;assert.equal((out.match(/class="enso-event"/g)||[]).length,7);
 assert.equal((out.match(/id="enso-c-ffpi"/g)||[]).length,1);assert(!out.includes('enso-money-story'));assert(!out.includes('id="enso-c-ffpilive"'));
 assert(out.includes('Disagreements and published critiques'));assert(out.includes(S.ffpi.latest.month));assert(out.includes(S.rtfp.AFG.as_of));
});
test('FFPI distinguishes latest month from annual averages without connecting gaps',()=>{
 api.drawCharts('ensomoney');const c=ctx.charts['enso-c-ffpi'];assert.equal(c.type,'scatter');assert.equal(c.data.datasets.length,3);assert(c.data.datasets.every(d=>!d.showLine));
 assert.equal(c.data.datasets[2].data[0].y,S.ffpi.latest.fpi);assert(c.data.datasets[2].label.includes(S.ffpi.latest.month));assert.notEqual(c.data.datasets[2].pointStyle,c.data.datasets[1].pointStyle);
});
test('all eight limits and the entire rejected-claims register remain',()=>{
 api.renderLimits();const out=nodes['enso-limits'].innerHTML;assert.equal((out.match(/class="enso-lim"/g)||[]).length,8);
 assert.equal((out.match(/class="enso-reg-row"/g)||[]).length,S.econ.do_not_publish.rows.length);assert(out.includes('How to read'));assert(out.includes('id="enso-gate"'));
});
test('native controls coexist with observed mode, nine rungs and labelled instruments',()=>{
 api.renderControls();const out=nodes['enso-controls'].innerHTML;assert.equal((out.match(/data-native="enso-level"/g)||[]).length,10);
 assert.equal((out.match(/data-native="enso-level" data-value="observed"/g)||[]).length,1);
 assert.equal((out.match(/class="is-observed-rung"/g)||[]).length,1);
 for(const id of ['enso-level','enso-mode','enso-country','enso-tog-regions','enso-tog-lanes','enso-tog-alerts','enso-tog-sst'])assert(out.includes('id="'+id+'"'));
 assert.equal((out.match(/class="enso-instrument-row/g)||[]).length,2);assert(out.includes('type="search"'));assert(out.includes('Observed / reported'));
});
test('Stage H scenario expands on request and stays fully visible for modelled paint',()=>{
 const mode=S.mode,expanded=S.scenarioExpanded;S.mode='rtfp';S.scenarioExpanded=false;
 api.renderControls();api.syncInstruments();
 assert(nodes['enso-controls'].innerHTML.includes('id="enso-scenario-rungs" hidden'));
 assert(nodes['enso-scenario-rungs'].hidden);assert(!nodes['enso-scenario-toggle'].hidden);
 nodes['enso-scenario-toggle'].onclick();assert(!nodes['enso-scenario-rungs'].hidden);assert.equal(nodes['enso-scenario-toggle']['aria-expanded'],'true');
 nodes['enso-scenario-toggle'].onclick();assert(nodes['enso-scenario-rungs'].hidden);
 for(const m of ['impact','crop','coverage']){S.mode=m;api.syncInstruments();assert(!nodes['enso-scenario-rungs'].hidden);assert(nodes['enso-scenario-toggle'].hidden);}
 S.mode=mode;S.scenarioExpanded=expanded;
});
test('humanitarian need is labelled reported',()=>{
 api.renderPeople();const out=nodes['enso-people-evidence'].innerHTML;assert(out.includes('data-kind="reported"'));assert(!out.includes('<h2'));assert.equal((out.match(/<tbody>/g)||[]).length,1);
});
console.log(passed+'/'+passed+' non-browser runtime checks passed');

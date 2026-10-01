// Non-browser checks using the actual panel closure and repository feeds.
// Run from the repo root: node tests/test_elnino_stage_b.cjs
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync('index.html','utf8');
const start = html.indexOf('(function () {', html.indexOf('   THE MAP USES A DIVERGING'));
const end = html.indexOf('\n})();',start);
const nodes = {};
function node(id) { return nodes[id] || (nodes[id] = { innerHTML:'', value:'', style:{}, setAttribute(k,v){this[k]=v;}, querySelector(){return null;}, querySelectorAll(){return [];}, classList:{toggle(){}, contains(){return false;}} }); }
const ctx = vm.createContext({window:{location:{href:'http://localhost/index.html',search:''}}, matchMedia(){return {matches:false,addEventListener(){},removeEventListener(){}};},IntersectionObserver:class {observe(){} disconnect(){}}, document:{getElementById:node,querySelector(){return node('scroller');},querySelectorAll(){return [];},addEventListener(){}}, URL,console,Date,setTimeout,clearTimeout,Event, URLSearchParams, charts:{}});
vm.runInContext(html.slice(start,end)+`
  mk = function(id,cfg) { if (!S._chartFilter || S._chartFilter.indexOf(id)>=0) globalThis.charts[id]=cfg; };
  globalThis.api={S,renderMechanism,feedIssue,renderFailures,calendarSeason,calendarBasis,crossSection,pacWeights,renderLandHead,renderDetail,renderCoeffs,renderCalendar,renderWater,renderMoney,renderPeople,renderLimits,renderControls,syncInstruments,drawCharts,selectCountry,isoOf};
})();`, ctx);
const api = ctx.api, S=api.S;
for (const [key,file] of Object.entries({model:'enso_model',calendars:'crop_calendars',enso:'enso',lanes:'enso_lanes',econ:'enso_econ',exp:'enso_exposure',portwatch:'portwatch',pwhist:'portwatch_history',rtfp:'rtfp',ffpi:'fao_ffpi',mech:'enso_mechanism',gauges:'enso_gauges'})) {
 const data=JSON.parse(fs.readFileSync('data/'+file+'.json','utf8'));S[key]=data.data;S.meta[key]=data._meta;
}
S.oniLive=S.enso.latest.anom;
let passed=0;
function test(name,fn){fn();passed++;console.log('ok',name);}
test('mechanism parts feed the Pacific explainer: live Niño 1+2, five tabs and panels, three registered illustrations, blend weights',()=>{
 api.renderMechanism();
 const V=S.mechView, nino12=(S.enso.weekly_nino34.nino12_anom>0?'+':'')+S.enso.weekly_nino34.nino12_anom.toFixed(1);
 assert(V.evidence.includes('Niño 1+2 at '+nino12+' °C (week of'));
 assert(!V.evidence.includes('Niño 1+2 at +4.5 °C (week of 9 Sep 2026)'));
 assert.equal((V.tabs.match(/data-ruler="/g)||[]).length,5);
 assert.equal((V.panels.match(/role="tabpanel"/g)||[]).length,5);
 assert(V.axis.includes('120°E')&&V.axis.includes('80°W'));
 assert.equal(nodes['enso-mech'].innerHTML,'');
 const layers=['normal','elnino','lanina'].map(st=>api.crossSection(st,true));
 ['walker4','elnino4','lanina3'].forEach((name,i)=>{
  assert(layers[i].includes('src="img/enso/'+name+'.webp"'));
  assert(layers[i].includes(name+'-768.webp 768w'));
  assert(layers[i].includes('width="1536" height="1024"'));
  assert(layers[i].includes('loading="eager"'));
 });
 const all=layers.join('');
 for(const label of ['Indonesia','Date line','Peru','Thermocline','Walker circulation','Rain over the warm pool','Cold water in reach','Rain follows the warm water','Upwelling capped'])assert(all.includes(label));
 const w=api.pacWeights;
 assert.deepEqual([w(0).elnino,w(0).lanina],[0,0]);
 assert.equal(w(.5).elnino,0);assert.equal(w(1.5).elnino,1);assert.equal(w(1.8).elnino,1);assert.equal(w(-1.87).lanina,1);assert.equal(w(1.8).lanina,0);
 assert(w(.8).elnino>0&&w(.8).elnino<w(1.1).elnino&&w(1.1).elnino<1);
 assert(!html.includes('function pacificSVG('));
 assert(!html.includes('@keyframes enso-'));
 assert(html.includes('transition:transform 250ms cubic-bezier(0.23,1,0.32,1)'));
});
test('partial agency feeds stay usable; stale and failed collectors still warn',()=>{
 const saved={data:S.bulletins,meta:S.meta.bulletins,names:S.feedNames,failed:S.failed};
 S.bulletins={bulletins:[{agency:'NOAA CPC'}]};
 S.meta.bulletins={status:'partial',generated_at:new Date().toISOString()};
 S.feedNames={bulletins:'enso_bulletins'};S.failed=[];
 assert.equal(api.feedIssue('bulletins'),'');api.renderFailures();assert.equal(nodes['enso-failures'].innerHTML,'');
 S.meta.bulletins.stale=true;assert(api.feedIssue('bulletins').includes('stale'));
 S.meta.bulletins.stale=false;S.meta.bulletins.status='error';assert(api.feedIssue('bulletins').includes('error'));
 S.meta.bulletins.status='partial';S.meta.bulletins.generated_at='2000-01-01';assert(api.feedIssue('bulletins').includes('collector has not run'));
 S.bulletins=saved.data;S.meta.bulletins=saved.meta;S.feedNames=saved.names;S.failed=saved.failed;
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
/* 2026-09-29 audit: El Niño-linked lanes as rows, the lanes with no ENSO link as one folded list; slot limits as a dated ladder. */
test('shipping opens with the lane board (linked rows, the rest folded), then Panama month by month with its slot ladder, then day by day',()=>{
 api.renderWater();const out=nodes['enso-water'].innerHTML;assert(out.includes('enso-pan-since'));assert(out.indexOf('enso-pan-since')<out.indexOf('id="enso-c-panama-daily"'));
 /* 2026-09-27 (Codex order review): the map leads, then the lane board as the answer, then Panama. */
 assert(out.indexOf('enso-lane-board')<out.indexOf('enso-pan-since'));assert(!out.includes('id="enso-c-panama"'));
 assert.equal((out.match(/data-board-lane=/g)||[]).length,9);
 const pan=S.lanes.lanes.find(l=>l.id==='panama');const todayIso=new Date().toISOString().slice(0,10);assert(out.includes(pan.live_2026.steps.filter(x=>(x.booking_from||x.effective)<=todayIso).at(-1).total+' slots/day for transits from'));
 for (const id of ['amazon','rhine','mississippi']) { const row=out.match(new RegExp('data-board-lane="'+id+'"[\\s\\S]*?(?=data-board-lane=|</figure>)'))[0]; assert(row.includes('2026'),id+' has a dated September observation'); }
 assert(out.includes('Booking slots a day, from the Canal’s advisories'));assert(out.includes('Advisories and operating context'));assert(out.includes('Sep 2025'));assert(out.includes('Sep 2026'));
 assert.equal((out.match(/data-lane=/g)||[]).length,S.lanes.lanes.length);
 for(const l of S.lanes.lanes){if(l.counter_evidence) assert(out.includes(l.counter_evidence.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;')));}
});
test('Panama slot ladder lists every dated slot advisory',()=>{
 api.renderWater();const out=nodes['enso-water'].innerHTML,p=S.lanes.lanes.find(l=>l.id==='panama');
 assert.equal((out.match(/class="enso-pan-step"/g)||[]).length,p.precedent_2023.steps.length+p.live_2026.steps.length);
 const low=Math.min(...p.precedent_2023.steps.map(x=>x.total));assert(out.includes('<span class="enso-pan-step">'+low+' <i>'));assert(out.includes('<span class="enso-pan-step">'+p.live_2026.steps.at(-1).total+' <i>'));
 api.drawCharts('ensowater');assert(!ctx.charts['enso-c-panama']);
 assert(ctx.charts['enso-c-panama-daily'].plugins[0].id==='ensoRules');
});
test('prices show past El Niños as one dot plot and no duplicate food-inflation chart',()=>{
 api.renderMoney();const out=nodes['enso-money'].innerHTML;assert.equal((out.match(/class="enso-event"/g)||[]).length,7);
 assert(!out.includes('id="enso-c-ffpi"'));assert(!out.includes('id="enso-c-rtfp"'));assert(!out.includes('enso-money-story'));assert(!out.includes('id="enso-c-ffpilive"'));
 assert(out.includes('They do not agree on the sign'));assert(out.includes('enso-estimates-fold'));assert(out.includes('World agricultural prices before '+api.S.econ.record.peaks.length+' past El Niño peaks: up in both windows'));
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
 assert.equal((out.match(/class="enso-instrument-row/g)||[]).length,1);assert(!out.includes('type="search"'));assert(out.includes('<summary>All layers</summary>'));
});
test('Natural Earth fallback keeps France as a named country option',()=>{
 const saved=S.names,feature={properties:{ISO_A3:'-99',ADM0_A3:'FRA',name:'France'}};
 const iso=api.isoOf(feature);assert.equal(iso,'FRA');S.names={[iso]:feature.properties.name};api.renderControls();
 const option=nodes['enso-controls'].innerHTML.match(/<option value="FRA"[^>]*>([^<]+)<\/option>/);
 assert(option);assert.equal(option[1],'France');assert.notEqual(option[1],'FRA');S.names=saved;
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
 const out=api.renderPeople()||nodes['enso-people'].innerHTML;assert(out.includes('data-kind="reported"'));assert(!out.includes('<h2'));assert.equal((out.match(/<tbody>/g)||[]).length,1);
});
console.log(passed+'/'+passed+' non-browser runtime checks passed');

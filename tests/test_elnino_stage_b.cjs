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
  globalThis.api={S,renderMechanism,feedIssue,renderFailures,calendarSeason,calendarBasis,wireRuler,renderLandHead,renderDetail,renderCoeffs,renderCalendar,renderWater,renderMoney,renderPeople,renderLimits,renderControls,syncInstruments,drawCharts,selectCountry,isoOf};
})();`, ctx);
const api = ctx.api, S=api.S;
for (const [key,file] of Object.entries({model:'enso_model',calendars:'crop_calendars',enso:'enso',lanes:'enso_lanes',econ:'enso_econ',exp:'enso_exposure',portwatch:'portwatch',pwhist:'portwatch_history',rtfp:'rtfp',ffpi:'fao_ffpi',mech:'enso_mechanism',gauges:'enso_gauges'})) {
 const data=JSON.parse(fs.readFileSync('data/'+file+'.json','utf8'));S[key]=data.data;S.meta[key]=data._meta;
}
S.oniLive=S.enso.latest.anom;
let passed=0;
function test(name,fn){fn();passed++;console.log('ok',name);}
test('paired longitude ruler supports arrow wrap, Home/End, per-state highlighting and persistent comparison state',()=>{
 const saved={...nodes};let focused=null, observe;
 const realTimer=ctx.setTimeout,realClear=ctx.clearTimeout;const timers=new Map();let timerId=0;
 ctx.setTimeout=(fn,ms)=>{assert.equal(ms,4000);timers.set(++timerId,fn);return timerId;};ctx.clearTimeout=id=>timers.delete(id);
 ctx.IntersectionObserver=class {constructor(fn){observe=fn;}observe(){}disconnect(){}};
 const advance=()=>{const [id,fn]=timers.entries().next().value;timers.delete(id);fn();};
 const stops=['trades','soi','warm_water','convection','upwelling'];
 const ticks=stops.map(id=>({attrs:{'aria-controls':'enso-step-'+id},setAttribute(k,v){this.attrs[k]=v;},getAttribute(k){return this.attrs[k];},focus(){focused=id;}}));
 const states=['elnino','lanina'].map(state=>({dataset:{rulerState:state},attrs:{},setAttribute(k,v){this.attrs[k]=v;}}));
 const highlights=[0,1].map(()=>({dataset:{},style:{}}));
 const rasters=['elnino','lanina'].map(state=>({dataset:{pacificState:state},setAttribute(k,v){this[k]=v;}}));
 const handlers={};
 const plate={querySelectorAll(q){assert.equal(q,'.enso-ruler-highlight');return highlights;},addEventListener(k,fn){handlers[k]=fn;}};
 nodes['enso-ruler-figure']={dataset:{},querySelectorAll(q){assert.equal(q,'[data-pacific-state]');return rasters;}};
 nodes['enso-mech']={querySelector(){return plate;},querySelectorAll(q){return q==='[data-ruler]'?ticks:q==='[data-ruler-state]'?states:[];}};
 api.renderMechanism();
 const markup=nodes['enso-mech'].innerHTML;
 const nino12=(S.enso.weekly_nino34.nino12_anom>0?'+':'')+S.enso.weekly_nino34.nino12_anom.toFixed(1);
 assert(markup.includes('Niño 1+2 at '+nino12+' °C (week of'));
 assert(!markup.includes('Niño 1+2 at +4.5 °C (week of 9 Sep 2026)'));
 const pair=markup.slice(markup.indexOf('<div class="enso-pacific-pair">'),markup.indexOf('<div class="enso-mechanism-reading">'));
 const imgs=[...pair.matchAll(/<img [^>]+>/g)].map(m=>m[0]);
 assert.equal(imgs.length,3);
 ['walker2','elnino2','lanina2'].forEach((name,i)=>{
  assert(imgs[i].includes('src="img/enso/'+name+'.webp"'));
  assert(imgs[i].includes(name+'-768.webp 768w'));
  assert(imgs[i].includes('width="1536" height="1024"'));
  assert(imgs[i].includes('loading="eager"'));
 });
 assert.equal((pair.match(/aria-hidden="false"/g)||[]).length,2);
 for(const label of ['Indonesia','Date line','Peru','Thermocline','Walker circulation','Rain over the warm pool','Cold water in reach','Rain follows the warm water','Upwelling capped'])assert(pair.includes(label));
 assert(!html.includes('function pacificSVG('));
 assert(!html.includes('@keyframes enso-'));
 assert(html.includes('transition:opacity 600ms cubic-bezier(0.77,0,0.175,1)'));
 assert(html.includes('transition:opacity 200ms cubic-bezier(0.77,0,0.175,1)'));
 assert(html.includes('transition:transform 250ms cubic-bezier(0.23,1,0.32,1)'));
 assert.equal(ticks[0].attrs['aria-selected'],'true');
 assert.equal(nodes['enso-ruler-figure'].dataset.state,'elnino');
 assert(nodes['enso-ruler-caption'].textContent.startsWith('Normal:'));
 assert.equal(rasters[0]['aria-hidden'],'false');assert.equal(rasters[1]['aria-hidden'],'true');
 ticks[1].onclick();assert.equal(nodes['enso-ruler-figure'].dataset.state,'elnino');
 let prevented=false;ticks[0].onkeydown({key:'ArrowLeft',preventDefault(){prevented=true;}});
 assert(prevented);assert.equal(focused,'upwelling');assert.equal(ticks[4].attrs['aria-selected'],'true');assert.equal(nodes['enso-step-upwelling'].hidden,false);assert.equal(nodes['enso-step-trades'].hidden,true);
 ticks[4].onkeydown({key:'Home',preventDefault(){}});assert.equal(focused,'trades');
 ticks[0].onkeydown({key:'End',preventDefault(){}});assert.equal(focused,'upwelling');
 ticks[2].onclick();assert.equal(ticks[2].attrs['aria-selected'],'true');
 assert.equal(highlights[0].style.transform,'translate(10px,38px) scale(0.52,0.12)');
 assert.equal(highlights[1].style.transform,'translate(10px,38px) scale(0.8,0.13)');
 ticks.forEach((tick,i)=>{tick.onclick();assert(highlights.every(r=>r.dataset.step===String(i)));});
 states[1].onclick();assert.equal(states[1].attrs['aria-pressed'],'true');assert.equal(states[0].attrs['aria-pressed'],'false');assert.equal(rasters[0]['aria-hidden'],'true');assert.equal(rasters[1]['aria-hidden'],'false');
 assert.equal(highlights[1].style.transform,'translate(78px,38px) scale(0.14,0.24)');
 ticks[0].onclick();assert.equal(nodes['enso-ruler-figure'].dataset.state,'lanina');
 assert(nodes['enso-ruler-caption'].textContent.includes('La Niña:'));
 observe([{isIntersecting:true}]);
 const play=nodes['enso-ruler-play'];play.onclick();assert.equal(ticks[0].attrs['aria-selected'],'true');
 for(let i=1;i<5;i++){advance();assert.equal(ticks[i].attrs['aria-selected'],'true');assert(highlights.every(r=>r.dataset.step===String(i)));}
 assert.equal(timers.size,0);assert.equal(play['aria-pressed'],'false');
 for(const event of ['pointerenter','focusin','keydown']){play.onclick();assert.equal(timers.size,1);handlers[event]();assert.equal(timers.size,0);}
 play.onclick();observe([{isIntersecting:false}]);assert.equal(timers.size,0);
 S.rulerCleanup();S.rulerCleanup=null;ctx.setTimeout=realTimer;ctx.clearTimeout=realClear;
 for(const k of Object.keys(nodes))delete nodes[k];Object.assign(nodes,saved);
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
test('shipping leads with nine lane answers and paired published limits and dated AIS',()=>{
 api.renderWater();const out=nodes['enso-water'].innerHTML;assert(out.indexOf('id="enso-c-panama"')<out.indexOf('id="enso-c-panama-daily"'));
 assert(out.indexOf('enso-lane-board')<out.indexOf('enso-panama-pair'));
 assert.equal((out.match(/data-board-lane=/g)||[]).length,9);
 const pan=S.lanes.lanes.find(l=>l.id==='panama');assert(out.includes(pan.live_2026.steps.at(-1).total+' slots/day from'));
 for (const id of ['amazon','rhine','mississippi']) { const row=out.match(new RegExp('data-board-lane="'+id+'"[\\s\\S]*?</tr>'))[0]; assert(row.includes('2026'),id+' has a dated September observation'); }
 api.drawCharts('ensowater');const key=ctx.charts['enso-c-panama'].keyNotes.join(' ');
 assert(key.includes('Ordinal steps'));assert(key.includes('not to scale in time'));assert(out.includes('Sep 2025'));assert(out.includes('Sep 2026'));
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
 assert.equal((out.match(/class="enso-instrument-row/g)||[]).length,1);assert(out.includes('type="search"'));assert(out.includes('<summary>All layers</summary>'));
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
 api.renderPeople();const out=nodes['enso-people-evidence'].innerHTML;assert(out.includes('data-kind="reported"'));assert(!out.includes('<h2'));assert.equal((out.match(/<tbody>/g)||[]).length,1);
});
console.log(passed+'/'+passed+' non-browser runtime checks passed');

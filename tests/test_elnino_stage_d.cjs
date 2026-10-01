// Non-browser Stage D gate. Runs the actual panel closure with a small DOM/Leaflet double.
// Browser layout and screenshots remain the owner's gate.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync('index.html', 'utf8');
class Element {
  constructor(tag = 'div') { this.tagName = tag; this.attrs = {}; this.childNodes = []; this.style = {}; this.dataset = {}; }
  get children() { return this.childNodes.filter(n => n instanceof Element); }
  set className(v) { this.attrs.class = v; } get className() { return this.attrs.class || ''; }
  get classList() { return { contains: c => this.className.split(/\s+/).includes(c), toggle: (c,on) => { const a=this.className.split(/\s+/).filter(x=>x&&x!==c); if(on)a.push(c); this.className=a.join(' '); } }; }
  setAttribute(k,v) { this.attrs[k] = String(v); if(k.startsWith('data-'))this.dataset[k.slice(5).replace(/-([a-z])/g,(_,c)=>c.toUpperCase())]=String(v); } getAttribute(k) { return this.attrs[k]; } removeAttribute(k) { delete this.attrs[k]; }
  appendChild(n) { if (n.parent) n.parent.childNodes=n.parent.childNodes.filter(x=>x!==n); n.parent=this; this.childNodes.push(n); return n; }
  insertBefore(n,b) { this.appendChild(n);this.childNodes.splice(this.childNodes.indexOf(n),1);this.childNodes.splice(Math.max(0,this.childNodes.indexOf(b)),0,n); }
  get textContent() { return this.childNodes.map(n=>n.textContent).join(''); }
  set textContent(v) { this.childNodes=[{textContent:String(v)}]; }
  set innerHTML(value) {
    this.childNodes=[]; const stack=[this];
    for(const token of value.match(/<[^>]*>|[^<]+/g)||[]) {
      if(token.startsWith('</')) { stack.pop(); continue; }
      if(token.startsWith('<')) {
        const tag=token.match(/^<([\w-]+)/); if(!tag)continue;
        const n=new Element(tag[1]);
        for(const m of token.matchAll(/([\w-]+)="([^"]*)"/g))n.setAttribute(m[1],m[2]);
        stack[stack.length-1].appendChild(n);
        if(!['input','br','img','hr','wbr'].includes(tag[1]))stack.push(n);
      } else stack[stack.length-1].appendChild({textContent:token});
    }
  }
  get innerHTML() { return this.childNodes.map(n=>n instanceof Element ? '<'+n.tagName+Object.entries(n.attrs).map(([k,v])=>' '+k+'="'+v+'"').join('')+'>'+n.innerHTML+'</'+n.tagName+'>' : n.textContent).join(''); }
  querySelectorAll(selector) { const out=[]; const match=n=>selector[0]==='[' ? (()=>{const m=selector.match(/^\[([^=]+)="([^"]*)"\]$/);return m&&n.getAttribute(m[1])===m[2];})() : selector[0]==='.'?n.classList.contains(selector.slice(1)):selector[0]==='#'?n.attrs.id===selector.slice(1):n.tagName===selector; const visit=n=>n.children.forEach(c=>{if(match(c))out.push(c);visit(c);});visit(this);return out; }
  querySelector(s) { return this.querySelectorAll(s)[0]||null; }
  insertAdjacentHTML(_,value) { const n=new Element();n.innerHTML=value;n.childNodes.slice().forEach(c=>this.appendChild(c)); }
}
const nodes = {};
function node(id) { return nodes[id] || (nodes[id]=new Element()); }
class Layer {
  constructor(coords,opts={}) { this.coords=coords;this.options=opts;this.element=new Element('path'); }
  addTo(map) { map.layers.add(this);return this; }
  on(){return this;} bindTooltip(html){this.tooltip=html;return this;} bringToFront(){return this;}
  getElement(){return this.element;} setStyle(s){Object.assign(this.options,s);}
  getBounds(){return {getCenter(){return {lat:0,lng:0};}};}
}
const L={polyline:(c,o)=>new Layer(c,o),marker:(c,o)=>new Layer(c,o),circleMarker:(c,o)=>new Layer(c,o),divIcon:o=>o};
const pending=[];
const ctx=vm.createContext({console,Date,URL,URLSearchParams,Event,L,charts:{},RAMP:['#1','#2','#3','#4','#5'],setTimeout:fn=>pending.push(fn),clearTimeout(){},window:{location:{href:'http://localhost/index.html',search:''},matchMedia(){return {matches:true};}},document:{getElementById:node,querySelector:s=>s==='#tab-elnino .content-page'?node('scroller'):null,querySelectorAll(){return [];},createElement:t=>new Element(t),createElementNS:(_,t)=>new Element(t),addEventListener(){}}});
const start=html.indexOf('(function () {',html.indexOf('   THE MAP USES A DIVERGING')),end=html.indexOf('\n})();',start);
vm.runInContext(html.slice(start,end)+`
  globalThis.api={S,laneMeasurement,transitKey,renderMapRanking,rankedPrices,rankedHotspots,priceMapSentence,mapState,renderControls,syncInstruments,selectCountry,flyTo,drawAlerts,alertLegend,placeChokepointLabels,fitMapView,drawGraticule,renderLegend,drawLanes,laneGeometry,corridorGeometry,fillFor,rtfpColor,renderWater,renderMoney,renderCalendar,analogPlate,drawCharts,paint,toggleSST,buildDefs};
  mk=function(id,cfg){ if(!S._chartFilter || S._chartFilter.indexOf(id)>=0) globalThis.charts[id]=cfg; };
  syncInstruments=renderMapTag=renderControls=renderDetail=renderFailures=wireTabKeys=syncTabRoving=wireRasterPlates=finishPlates=renderSubviewMeta=function(){};
})();`,ctx);
const api=ctx.api,S=api.S;
// The chart now shares the rail's published-country filter; load its real index.
S.isoIndex={};
JSON.parse(fs.readFileSync('data/enso_regions.json','utf8')).data.regions.forEach(r=>r.iso3.forEach(iso=>(S.isoIndex[iso] ||= []).push(r)));
for(const [key,file] of Object.entries({model:'enso_model',calendars:'crop_calendars',enso:'enso',lanes:'enso_lanes',corridors:'enso_corridors',econ:'enso_econ',exp:'enso_exposure',portwatch:'portwatch',pwhist:'portwatch_history',rtfp:'rtfp',fpma:'fpma_prices',pink:'worldbank_pink_sheet',ffpi:'fao_ffpi',asap:'asap',gdacs:'gdacs',relief:'reliefweb_alerts',sst:'sst_anomaly'})){
 const data=JSON.parse(fs.readFileSync('data/'+file+'.json','utf8'));S[key]=data.data;S.meta[key]=data._meta;
}
let passed=0;
function test(name,fn){fn();passed++;console.log('ok',name);}
S.oniLive=S.enso.latest.anom;
S.map={layers:new Set(),hasLayer(l){return this.layers.has(l);},removeLayer(l){this.layers.delete(l);},invalidateSize(){},fitBounds(bounds,options){this.bounds=bounds;this.fitOptions=options;},setView(center,z){this.center=center;this.zoom=z;}};
S.nino34=new Layer();S.ninoLabel=new Layer();S.sstLayer=new Layer();S.layerRegions=new Layer();S.layerRegions.eachLayer=()=>{};
const country=new Layer();country.feature={properties:{ISO_A3:'ZWE'}};
S.layerBase={eachLayer(fn){fn(country);}};
S.ready=true;S.names={};S.drawn={live:true,frame:true,limits:true,mech:true,map:true,water:true,money:true};
S.sel='ZWE';
test('lane geometry is data-only, validated and phase coloured',()=>{
 assert(S.lanes.lanes.every(l=>api.laneGeometry(l).length===0));
 /* 2026-09-29 audit: the map draws only lanes with a published ENSO link; 2026-09-30 court: the El Niño side by default, the La Niña rivers only when their layer is on. */
 const LINKED=S.lanes.lanes.filter(l=>l.phase==='el_nino');
 api.drawLanes();assert.equal(S.laneLines.length,0);assert.equal(S.lanePins.length,LINKED.length);
 S.showLaNina=true;api.drawLanes();assert.equal(S.lanePins.length,S.lanes.lanes.filter(l=>l.phase&&l.phase!=='none').length);S.showLaNina=false;api.drawLanes();
 S.lanePins.forEach(p=>assert(p.options.icon.html.includes('enso-choke-label')));
 const original=S.lanes;
 S.lanes={lanes:['el_nino','la_nina','none'].map((phase,i)=>({id:'test'+i,name:'test',phase,lat:0,lng:0,geometry:{type:'LineString',coordinates:[[10,20],[11,21]]}}))};
 S.showLaNina=true;api.drawLanes();assert.deepEqual(S.laneLines.map(l=>l.options.color),['#e0673c','#5b9bd0']);S.showLaNina=false;
 assert(S.laneLines.every(l=>l.options.weight>=3&&!l.options.dashArray));assert.equal(S.laneLines[0].coords[0][0],20);
 assert.equal(api.laneGeometry({geometry:{type:'LineString',coordinates:[[999,20],[0,0]]}}).length,0);
 S.lanes=original;S.lanePins=[];S.laneLines=[];api.drawLanes();
});
test('Shipping draws the El Niño-side corridors at one width, the weak link muted, with names in tooltips',()=>{
 const LINKED=S.lanes.lanes.filter(l=>l.phase==='el_nino'), LINKEDC=S.corridors.corridors.filter(c=>LINKED.some(l=>l.id===c.lane&&l.phase===c.phase));
 assert.equal(S.corridorLines.length,LINKEDC.length);assert.equal(S.corridorLabels.length,0);assert.equal(S.corridorArrows.length,0);
 LINKEDC.forEach((c,i)=>{
  const lane=S.lanes.lanes.find(l=>l.id===c.lane),line=S.corridorLines.find(l=>l.options.ensoCorridorId===c.id);
  /* 2026-09-30 court: constant width (nothing scaled); tier 1 = moderate/strong link in the phase ink, tier 2 = weak in a muted ink. */
  const tier=lane.phase==='none'?3:lane.attribution==='weak'?2:1, ink={el_nino:'#e0673c',la_nina:'#5b9bd0',none:'#7b8491'}[lane.phase];
  assert.equal(c.phase,lane.phase);assert.equal(line.options.color,tier===2?'#b39d8a':ink);
  assert.equal(line.options.weight,1.7);assert.equal(line.options.opacity,[0,.95,.8][tier]);assert.equal(line.options.dashArray,'6 4');
  for(const text of [c.name,c.basis,'schematic corridor through named waypoints, not vessel tracks',...c.commodities,...c.sources])assert(line.tooltip.includes(text.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;')));
 });
 const old=S.corridorLines.concat(S.corridorEdges);old.forEach(l=>l.addTo(S.map));api.drawLanes();
 assert(old.every(l=>!S.map.hasLayer(l)));assert.equal(S.corridorLines.length,LINKEDC.length);
});
test('Stage I transit rings join actual lane values and distinguish zero, missing and increases',()=>{
 S.lanes.lanes.filter(l=>l.phase==='el_nino').forEach((ln,i)=>{
  const m=api.laneMeasurement(ln),html=S.lanePins[i].options.icon.html,pw=S.portwatch[ln.portwatch_key];
  if(!pw){assert.equal(m,null);assert(!html.includes('no transit data'));assert(S.lanePins[i].options.icon.className.includes('no-transit'));assert(!html.includes('data-yoy'));}
  else {const dry=Number.isFinite(pw.yoy.dry_bulk_pct)&&Number.isFinite(pw.transits_per_day.dry_bulk),pct=dry?pw.yoy.dry_bulk_pct:pw.yoy.total_pct;assert.equal(m.pct,pct);assert.equal(m.total,pw.transits_per_day.total);assert(html.includes('data-yoy="'+pct+'"'));assert(!html.includes('<circle'));assert(!html.includes('enso-transit-ring'));assert(html.includes('--ink-phase:'+(ln.phase==='none'?'#7b8491':{el_nino:'#e0673c',la_nina:'#5b9bd0'}[ln.phase])));}
 });
 const original=S.portwatch,ln={portwatch_key:'fixture'};
 try {
  for(const pct of [-100,-25,0,25,100,150]){
   S.portwatch={fixture:{yoy:{total_pct:pct},transits_per_day:{total:0}}};
   const m=api.laneMeasurement(ln);assert.equal(m.pct,pct);assert.equal(m.radius,undefined);  /* 2026-09-29: no size-scaled marks; the diamond is one size and its colour carries the change */
  }
  for(const pw of [{},{yoy:{total_pct:null},transits_per_day:{total:1}},{yoy:{total_pct:'2'},transits_per_day:{total:1}},
    {yoy:{total_pct:Infinity},transits_per_day:{total:1}},{yoy:{total_pct:2},transits_per_day:{total:null}}]){
   S.portwatch={fixture:pw};assert.equal(api.laneMeasurement(ln),null);
  }
 } finally {S.portwatch=original;}
 assert(api.transitKey().includes('IMF PortWatch'));assert(api.transitKey().includes('28-day mean'));
});
test('Panama ends in a labelled Pacific stub, one line, nothing at the dateline',()=>{
 /* 2026-09-30 court: the stub label names where the cargo goes; Amazon and Malacca name their exits from the file's own waypoints. */
 assert.equal(S.corridorEdges.length,3);
 assert(S.corridorEdges.some(l=>l.options.icon.html.includes('to Asia and the Pacific coast of South America')&&l.options.icon.className.includes('is-pacific')));
 assert(S.corridorEdges.some(l=>l.options.icon.html.includes('Atlantic exit')));assert(S.corridorEdges.some(l=>l.options.icon.html.includes('to South China Sea')));
 const pan=S.corridorLines.find(l=>l.options.ensoCorridorId==='us_gulf_panama_east_asia');
 assert.equal(pan.coords.length,1);assert(pan.coords[0].every((p,i)=>!i||Math.abs(p[1]-pan.coords[0][i-1][1])<=180));assert(pan.coords[0].every(p=>p[1]>-180&&p[1]<180));
});
test('long ocean legs curve and split at the dateline without a world-spanning chord',()=>{
 const path=api.corridorGeometry(S.corridors.corridors[0].waypoints);
 assert.equal(path.length,2);assert(path.flat().length>50);
 for(const segment of path)for(let i=1;i<segment.length;i++)assert(Math.abs(segment[i][1]-segment[i-1][1])<=180);
 assert.equal(path[0].at(-1)[1],-180);assert.equal(path[1][0][1],180);
 assert(Math.max(...path.flat().map(p=>p[0]))>35.44);
 assert.equal(api.corridorGeometry([[0,0],[100,0]]).length,0);
});
/* Rewritten for the owner's "prices one dont show all countries of the el nino".
   The rail used to rank the twelve highest inflations in the whole RTFP feed,
   so it could run without naming one teleconnection country, while 22 of the 37
   it does name carry no monitored market and appeared nowhere. It now lists
   every teleconnection country, valued first and descending, then the ones with
   no value, then the monitored countries outside the layer. */
test('Stage I price rail lists every teleconnection country, valued first, and names the rest',()=>{
 const saved=S.rtfp, oldMode=S.mode, oldIdx=S.isoIndex;S.mode='rtfp';
 api.renderMapRanking();const list=node('enso-map-ranking');
 const tele=Object.keys(oldIdx||{});
 assert(tele.length>0,'the teleconnection fixture must not be empty');
 const shown=list.querySelectorAll('button').map(b=>b.getAttribute('data-map-country'));
 tele.forEach(k=>assert(shown.includes(k),k+' must be listed'));
 const valued=k=>Number.isFinite((saved[k]||{}).food_inflation_pct);
 Object.keys(saved).filter(valued).forEach(k=>assert(shown.includes(k),k+' is painted, so it must be listed'));
 const teleShown=shown.filter(k=>tele.includes(k)), teleValued=teleShown.filter(valued);
 assert.deepEqual(teleShown.slice(0,teleValued.length),teleValued,'valued teleconnection rows lead');
 teleValued.forEach((k,i)=>{if(i)assert(saved[teleValued[i-1]].food_inflation_pct>=saved[k].food_inflation_pct);});
 teleValued.forEach(k=>{assert(list.textContent.includes(((d)=>{const m=String(d).match(/^(\d{4})-(\d{2})-(\d{2})/);return m?['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][m[2]-1]+' '+m[1]:d;})(saved[k].as_of)));assert(list.textContent.includes(saved[k].markets+' markets'));});
 // 2026-09-24: unmonitored countries rank by official food CPI or are named as having no value.
 if(teleShown.length>teleValued.length)assert(list.textContent.includes('official food CPI')||list.textContent.includes('No value in either source'));
 assert.equal(api.mapState().title,'Food inflation, year on year');assert(api.priceMapSentence().includes('August 2026'));
 S.isoIndex={ZWE:[{}],KEN:[{}]};
 S.rtfp={ZWE:{food_inflation_pct:0,markets:2,as_of:'2026-07-01'},USA:{food_inflation_pct:-2,markets:3,as_of:'2026-08-01'},BAD:{food_inflation_pct:null},NAN:{food_inflation_pct:NaN}};
 api.renderMapRanking();
 assert.deepEqual(list.querySelectorAll('button').map(b=>b.getAttribute('data-map-country')),['ZWE','KEN','USA']);
 assert(list.textContent.includes('No value in either source')||list.textContent.includes('official food CPI'));
 assert(list.textContent.includes('1 monitored countries outside the layer'));
 assert(api.priceMapSentence().includes('July 2026 to August 2026'));
 S.rtfp={};api.renderMapRanking();
 assert.deepEqual(list.querySelectorAll('button').map(b=>b.getAttribute('data-map-country')),['KEN','ZWE'],'with no values at all both fall back to name order');
 assert(api.priceMapSentence().includes('reporting date unavailable'));
 S.rtfp=saved;S.mode=oldMode;S.isoIndex=oldIdx;
});
test('Stage H ASAP ranks major before hotspot and exposes assessment months',()=>{
 const saved=S.asap,oldMode=S.mode;S.mode='asap';
 S.asap={ZWE:{hotspot_code:2,assessment_date:'2026-08-11'},USA:{hotspot_code:1,assessment_date:'2026-07-11'},NO:{hotspot_code:0},NA:{hotspot_code:null}};
 api.renderMapRanking();const list=node('enso-map-ranking');
 assert.deepEqual(list.querySelectorAll('button').map(b=>b.getAttribute('data-map-country')),['ZWE','USA']);
 assert(list.textContent.includes('Major hotspot · August 2026'));assert(list.textContent.includes('Hotspot · July 2026'));
 assert.equal(api.mapState().title,'Where crops are under stress this season');
 S.asap=saved;S.mode=oldMode;
});
test('Stage H ranked-country taps stay in their lens and pan without zoom',()=>{
 const pan=[];S.map.panTo=(center,options)=>pan.push({center,options});
 const oldRep=S.reported;S.reported={ZWE:{events:[{iso:'ZWE',type:'drought',date:'2026-09-01'}],fits:1,against:0,none:0}};
 /* 2026-10-01: the Reported rail is the thread status queue (built from S.evthreads, not stubbed here); its taps are checked in the browser gate. */
 for(const [sub,mode] of [['ensomoney','rtfp']]) {
  S.sub=sub;S.mode=mode;api.renderMapRanking();const button=node('enso-map-ranking').querySelectorAll('button')[0];
  button.onclick();assert.equal(S.sel,button.getAttribute('data-map-country'));assert.equal(S.sub,sub);assert.equal(S.mode,mode);
  api.selectCountry('ZWE',{fly:true});assert.equal(country.options.color,'#ebe9e2');
 }
 assert(pan.length>=1);assert(pan.every(p=>p.options.animate===false));S.sub='elnino';S.reported=oldRep;
});
test('Reported draws every hazard in El Niño countries, rings the verdict, and counts the rest',()=>{
 const oldPins=S.alertPins,oldEv=ctx.window.disturbanceEvents,oldNews=S.news,oldHl=S._hl;
 const zweRain=(S.isoIndex.ZWE||[{}]).map(r=>r.rain).filter(Boolean)[0], today=new Date().toISOString().slice(0,10);
 const fitType=zweRain==='drier'?'drought':'flood', oppType=zweRain==='drier'?'flood':'drought';
 const oldG=S.gdacs;S.gdacs=null;S.alertPins=[];S._hl=null;S.news={items:[{title:'El Niño headline',source:'x',countries_mentioned:['ZWE'],published_at:today},{title:'too broad',countries_mentioned:['ZWE','ZAF','MOZ','MWI'],published_at:today}]};
 ctx.window.disturbanceEvents=[
  {iso:'ZWE',type:fitType,date:today,title:'fits',severity:'high',source:'GDACS'},
  {iso:'ZWE',type:oppType,date:today,title:'opposite',severity:'high',source:'GDACS'},
  {iso:'ZWE',type:'cyclone',date:today,title:'no rain link',severity:'medium',source:'GDACS'},
  {iso:'FRA',type:'drought',date:today,title:'outside',severity:'high',source:'GDACS'},
  {iso:'ZWE',type:'conflict',date:today,title:'not a hazard',severity:'high',source:'HAPI'},
  {iso:'ZWE',type:fitType,date:'2020-01-01',title:'too old',severity:'high',source:'GDACS'}];
 api.drawAlerts();
 /* Nothing is filtered by the verdict: fit, opposite and no-link hazards are all drawn, each with its ring. */
 assert.equal(S.alertPins.length,3);
 const c=S.alertCounts;assert.equal(c.n,3);assert.equal(c.fits,1);assert.equal(c.against,1);assert.equal(c.none,1);assert.equal(c.elsewhere,1);assert.equal(c.other,1);
 assert.deepEqual(S.alertPins.map(m=>m.options.ensoVerdict).sort(),['against','fits','none']);
 const fit=S.alertPins.find(m=>m.options.ensoVerdict==='fits');
 assert(fit.tooltip.includes('Fits the usual pattern'));assert(fit.tooltip.includes('do not attribute causes'));
 assert(S.alertPins.find(m=>m.options.ensoVerdict==='against').tooltip.includes('Runs against the usual pattern'));
 assert.equal(c.headlines,2,'both El Niño-country headlines are counted; only the one naming one to three countries gets a map tab');
 const oldShow=S.showAlerts;S.showAlerts=true;const leg=api.alertLegend();S.showAlerts=oldShow;assert(leg.includes('<b>3</b> reports'));assert(leg.includes('Fitting is not attribution'));
 S.alertPins=oldPins;ctx.window.disturbanceEvents=oldEv;S.news=oldNews;S._hl=oldHl;S.gdacs=oldG;
});
test('Shipping keeps one callout per chokepoint pairing Outlook with Now, with no corridor chips',()=>{
 assert.equal(S.corridorLabels.length,0);
 S.lanePins.forEach(pin=>{
  const html=pin.options.icon.html;
  assert.equal(html.split('enso-choke-label').length-1,1);
  /* 2026-09-30 court: a header, an Outlook line and a Now line; Panama adds the freight spread. */
  assert(html.includes('enso-lc-h')&&html.includes('Outlook'));if(!pin.options.icon.className.includes('no-transit'))assert(html.includes('Now'));  /* a lane with neither a PortWatch count nor a gauge loaded has no Now line */
  if(html.includes('Freight'))assert(html.includes('data-lane="panama"'));  /* only Panama carries the freight spread (the harness loads no freight feed, so it may be absent) */
 });
 const pac=S.corridorLines.find(l=>l.options.ensoCorridorId==='us_gulf_panama_east_asia')||S.corridorLines[0];assert.equal(pac.coords.length,1);assert(pac.coords.every(arc=>arc.length>1));
});
test('lens defaults fit the lane belt and tropical price countries without animation',()=>{
 const sub=S.sub;
 for(const [view,bounds] of [['ensowater',[[-45,-135],[62,150]]],['ensomoney',[[-40,-100],[40,155]]]]){
  S.sub=view;api.fitMapView();assert.deepEqual(S.map.bounds,bounds);assert.equal(S.map.fitOptions.animate,false);
 }
 S.sub=sub;
});
test('Explore instrument preserves controls and dates modelled paint from displayed metadata',()=>{
 const elements={};const get=id=>elements[id]||(elements[id]=new Element());
 const live={countries_overlay:JSON.parse(fs.readFileSync('data/countries.json')).data.countries};
 const main=vm.createContext({document:{getElementById:get},LIVE:live,window:{_mlState:{distOn:false,sstOn:false,flowsOn:false,expanded:false,hidden:new Set()},matchMedia(){return {matches:true};}},ML_TYPES:[{t:'drought',l:'Drought',c:'#c47a3c'}],_mlCounts(){return {drought:3};},distIconSVG:t=>'<svg data-t="'+t+'"></svg>'});
 const begin=html.indexOf('function exploreScoreDate()'),finish=html.indexOf('// Backward-compatible wrapper',begin);
 vm.runInContext(html.slice(begin,finish),main);vm.runInContext('renderMapLayers()',main);
 // 2026-09-25: the map-state strip carries the one-line lede and the computed date; the legend date line is gone.
 assert(get('map-state').textContent.includes('Food disruption risk, 0–100'));
 assert(get('map-state').textContent.includes(live.countries_overlay.AFG.fdrs_displayed_at.slice(0,10)));
 for(const label of ['Live disturbances','Sea temperature','Trade flows'])assert(get('map-layers').textContent.includes(label));
 for(const handler of ['mlToggleDist','mlToggleSST','mlToggleFlows','mlToggleExpand'])assert(get('map-layers').innerHTML.includes(handler+'(event)'));
 assert(get('map-layers').querySelector('#map-commodity-flows'));
 assert.equal(get('map-layers').querySelector('details').getAttribute('open'),undefined);
 live.countries_overlay={A:{fdrs_displayed_at:'2026-01-01T00:00:00Z'},B:{fdrs_displayed_at:'2026-01-02T00:00:00Z'}};
 assert.equal(vm.runInContext('exploreScoreDate()',main),'2026-01-01 to 2026-01-02');
 live.countries_overlay={};assert.equal(vm.runInContext('exploreScoreDate()',main),'unavailable');
 main._scnPaintOn=true;vm.runInContext('renderExploreMapState()',main);assert(get('map-state').textContent.includes('scenario FDRS change'));
});
test('Explore land blocks SST below unchanged bands and has a distinct opaque unscored fill',()=>{
 const pane={style:{}},drawn=[];
 const relief={style:{},classList:{add(){}}},tiles=[];
 const main=vm.createContext({map:{getPane(){return null;},createPane(name){assert(['exploreLand','exploreRelief'].includes(name));return name==='exploreLand'?pane:relief;}},L:{geoJSON(data,options){drawn.push(options);return {addTo(){}};},tileLayer(url,options){tiles.push(options);return {addTo(){}};}},data:{},LAND_TONE:'#0c0c0e',BORDER_LINE:'#aaa',lookupCountry:f=>f.country,mapRiskColor:()=> '#band',_scnPaintOn:false});
 const begin=html.indexOf("        var landPane = map.getPane('exploreLand')"),finish=html.indexOf('        G.countryLayer =',begin);
 vm.runInContext(html.slice(begin,finish),main);
 assert.equal(pane.style.zIndex,390);assert.equal(drawn[0].style.fillOpacity,1);assert.equal(drawn[0].pane,'exploreLand');
 assert.equal(relief.style.zIndex,405);assert.equal(tiles[0].pane,'exploreRelief');
 const styleBegin=html.indexOf('  function styleFeature(f)'),styleEnd=html.indexOf('  function onEachFeature',styleBegin);
 vm.runInContext(html.slice(styleBegin,styleEnd),main);
 const unscored=vm.runInContext('styleFeature({})',main);assert.equal(unscored.fillOpacity,1);assert.equal(unscored.fillColor,'url(#fs-unscored-hatch) #343b46');
 assert.equal(vm.runInContext('styleFeature({country:{fdrs:null}}).fillColor',main),unscored.fillColor);
 for(const score of [12,38,63,82,95]){const paint=vm.runInContext('styleFeature({country:{fdrs:'+score+'}})',main);assert.equal(paint.fillColor,'#band');assert(Math.abs(paint.fillOpacity-(0.40+Math.pow(score/100,0.85)*0.56))<1e-12);} // 2026-09-26: owner restored the score-graded opacity (the map's texture)
 const legend=html.slice(html.indexOf('<div id="map-legend"'),html.indexOf('</div><!-- /#map-canvas -->'));
 for(const label of ['0–25','26–50','51–75','76–88','89–100','Unscored','map-legend-date'])assert(legend.includes(label));
});
test('hatch SVG strokes match visible ochre and green samples',()=>{
 const svg=new Element('svg'), query=ctx.document.querySelectorAll;
 ctx.document.querySelectorAll=s=>s==='#enso-map svg'?[svg]:[];
 api.buildDefs();ctx.document.querySelectorAll=query;
 /* Harvest hatches first; then the Reported rain hatches, in the drought and flood hues (dry, wet, and both crossed). */
 assert.deepEqual(svg.querySelectorAll('line').map(n=>n.getAttribute('stroke')),['#c9773a','#4f9fa8','#c47a3c','#4a7ab3','#c47a3c','#4a7ab3','#4f9fa8','#c9773a','#4f9fa8','#c9773a','#4f9fa8','#c9773a','#4f9fa8']);  /* + the Harvests published-rises hatch, green like the fitted rises (2026-09-27); + the six lines of the 2027-outlook hatches, strong (fall, rise) and light (fall, rise, mixed as two) (2026-09-30) */
 S.mode='impact';S.showRegions=true;S.showSST=false;S.showLanes=false;api.renderLegend();
 const key=node('enso-legend').querySelector('.enso-legend').innerHTML;
 for(const c of ['#c9773a','#4f9fa8'])assert(key.includes('repeating-linear-gradient(45deg,'+c));
});
(async()=>{
 // Exercise buildMap and the real plate handlers with a bounded Leaflet double.
 const zoomNodes={},zoomNode=id=>zoomNodes[id]||(zoomNodes[id]=new Element());
 const zoomL=Object.assign({},L,{
  map(host,options){
   const m={options,layers:new Set(),panes:{},events:{},zoom:2,
    setView(center,z){this.center=center;return this.setZoom(z);},setZoom(z){this.zoom=z;if(this.events.zoomend)this.events.zoomend();return this;},
    getZoom(){return this.zoom;},getMinZoom(){return options.minZoom;},getMaxZoom(){return options.maxZoom;},
    on(name,fn){this.events[name]=fn;},createPane(n){return this.panes[n]={style:{}};},getPane(n){return this.panes[n];}};
   for(const k of ['scrollWheelZoom','doubleClickZoom','touchZoom','boxZoom','keyboard','dragging'])m[k]={enabled:()=>options[k]!==false};
   return m;
  },geoJSON:()=>new Layer(),rectangle:(c,o)=>new Layer(c,o)
 });
 const zoomCtx=vm.createContext({L:zoomL,console,Date,window:{location:{search:''}},document:{getElementById:zoomNode,createElement:t=>new Element(t),addEventListener(){}}});
 vm.runInContext(html.slice(start,end)+`
 globalThis.api={S,buildMap};
 drawWorldPlate=drawGraticule=addSSTLayer=drawLanes=drawAnnotations=buildDefs=paint=function(){};
 })();`,zoomCtx);
 zoomCtx.api.S.features=[];zoomCtx.api.S.showRegions=false;zoomCtx.api.S.showLanes=false;
 await zoomCtx.api.buildMap();
 test('Stage H map disables gesture and keyboard zoom while its plate buttons change zoom',()=>{
  const m=zoomCtx.api.S.map;
  for(const k of ['scrollWheelZoom','doubleClickZoom','touchZoom','boxZoom','keyboard']){assert.equal(m.options[k],false);assert(!m[k].enabled());}
  assert(m.dragging.enabled());
  const buttons=zoomNode('enso-mapwrap').querySelectorAll('button'),[plus,minus,reset]=buttons;
  assert.equal(buttons.length,3);assert(!minus.disabled);plus.onclick();assert.equal(m.getZoom(),3);assert(!minus.disabled);
  minus.onclick();assert.equal(m.getZoom(),2);plus.onclick();reset.onclick();assert.equal(m.getZoom(),2);
  for(let i=0;i<4;i++)plus.onclick();assert(plus.disabled);reset.onclick();assert(!plus.disabled);
 });
 const expected={elnino:'sst',ensoharvest:'harvest27',ensowater:'none',ensomoney:'staple',ensolive:'rain'};
 for(let cycle=0;cycle<2;cycle++) for(const [view,mode] of Object.entries(expected)) {
  node('scroller').scrollTop=1400;
  await ctx.window.ensoInit(view);pending.splice(0).forEach(fn=>fn());
  test('cycle '+(cycle+1)+' '+view+' clears inactive overlays and keeps its visible key',()=>{
   assert.equal(S.mode,mode);
   for(const l of [S.nino34,S.ninoLabel,S.sstLayer])assert.equal(S.map.hasLayer(l),view==='elnino');
   assert.equal(S.map.hasLayer(S.layerRegions),false);  /* 2026-09-27: published regions are an opt-in toggle on every lens */
   for(const l of S.lanePins.concat(S.laneLines,S.corridorLines,S.corridorLabels,S.corridorArrows,S.corridorEdges))assert.equal(S.map.hasLayer(l),view==='ensowater');
   for(const l of S.alertPins)assert.equal(S.map.hasLayer(l),view==='ensolive');
   /* Shipping has no callout any more: the Gatun card was the largest object on a map whose subject is the marks under it, and its numbers moved into the fold. Harvests keeps its two (three layers each). */
   assert.equal(S.annoLayers.length,0);  /* 2026-09-29 audit: the harvest callouts gave way to labelled 2027 circles (S.shiftLayers) */
   assert.equal(country.options.color,['ensoharvest','ensomoney','ensolive'].includes(view)?'#ebe9e2':'#e6e3da');
   if(view!=='ensolive')assert(!country.element.classList.contains('enso-hotspot')&&!country.element.classList.contains('enso-major-hotspot'));
   const legend=node('enso-legend'),visible=legend.querySelector('.enso-legend'),key=visible.textContent;
   assert.equal(legend.querySelectorAll('details').length,1);assert.equal(visible.querySelectorAll('details').length,0);
   assert.equal(legend.textContent.includes('Niño 3.4 box'),view==='elnino');  /* 2026-09-28: reference swatches sit in the key's fold */
   /* 2026-09-27: the published regions are an opt-in toggle, so their key rows appear only when it is on. */
   assert.equal(key.includes('El Niño reduces output here'),S.showRegions&&view==='ensoharvest');
   assert.equal(key.includes('El Niño raises output here'),S.showRegions&&view==='ensoharvest');
   assert.equal(key.includes('a published link, moderate or strong'),view==='ensowater');
   /* The solid-line swatch keyed a mark the map never draws: no lane in enso_lanes.json carries a geometry, so S.laneLines is always empty. The observed mark is the diamond and its ring. */
   assert.equal(key.includes('diamond: observed, measured at the chokepoint'),view==='ensowater');assert(!key.includes('Ring size'));
   assert.equal(key.includes('dashed: published schematic corridor through named ports'),view==='ensowater');
   if(view==='ensowater'){const LC=S.corridors.corridors.filter(c=>S.lanes.lanes.some(l=>l.id===c.lane&&l.phase===c.phase&&l.phase==='el_nino'));assert.equal(S.corridorLines.filter(l=>S.map.hasLayer(l)).length,LC.length);assert.equal(S.corridorLabels.length,0);for(const c of LC){assert(legend.querySelector('details').textContent.includes(c.basis.replace(/'/g,'&#39;')));}}
   assert.equal(key.includes('Does it fit the usual pattern?'),view==='ensolive');
   if(view==='ensolive')for(const label of ['usually drier in El Niño years','usually wetter','a possible early sign','runs against it','no rainfall expectation','Fitting is not attribution'])assert(key.includes(label),label+' | '+key.slice(0,600));
   if(view==='ensowater')for(const l of S.lanes.lanes)assert(legend.querySelector('details').textContent.includes(l.name));
   /* 2026-09-26: prices are circles (area = size of the change, solid RTFP, hollow CPI) over El Niño countries only. */
   /* 2026-09-26: staple prices (FAO GIEWS FPMA) over El Niño's published harvest effect, with the tab's verdict rings. */
   /* 2026-09-27 (map research): prices are a signed seven-class choropleth; the verdict and the harvest hatching are the second view. */
   if(view==='ensomoney')for(const label of ['Staple price, year on year','Blue falling','ochre to red rising','Dashed outline','No staple series'])assert(key.includes(label),label);
   if(cycle||view!=='elnino')assert.equal(node('scroller').scrollTop,0);
  });
 }
 test('hotspot classes and price no-data preserve distinct palettes',()=>{
  S.mode='asap';const saved=S.asap;S.asap={ZWE:{hotspot_code:1}};api.paint();assert.equal(api.fillFor('ZWE'),'#7d6a3e');assert(country.element.classList.contains('enso-hotspot'));
  S.asap.ZWE.hotspot_code=2;api.paint();assert.equal(api.fillFor('ZWE'),'#b4602c');assert(country.element.classList.contains('enso-major-hotspot'));assert(!country.element.classList.contains('enso-hotspot'));
  S.mode='rtfp';api.paint();assert(!country.element.classList.contains('enso-major-hotspot'));assert.equal(api.rtfpColor(-10),'#8fb1cf');assert.equal(api.rtfpColor(0),'#606268');assert.equal(api.rtfpColor(15),'#c9773a');assert.equal(api.rtfpColor(30),'#dd5a3a');assert.equal(api.rtfpColor(60),api.rtfpColor(30));assert.equal(api.rtfpColor(null),null);S.asap=saved;
 });
 test('Prices outlines all teleconnection members and leaves other land unfilled',()=>{
  const oldBase=S.layerBase, oldMode=S.mode, oldSel=S.sel;
  const countries=Object.keys(S.isoIndex).map(iso=>{const l=new Layer();l.feature={properties:{ISO_A3:iso}};return l;});
  const outside=new Layer();outside.feature={properties:{ISO_A3:'NOT_IN_LAYER'}};
  S.layerBase={eachLayer(fn){countries.concat(outside).forEach(fn);}};S.mode='rtfp';S.sel=null;api.paint();
  countries.forEach(l=>{
   /* 2026-09-26: teleconnection land is one flat tone with a visible outline; the values are circles. */
   assert.equal(l.options.color,'#8a8578');assert.equal(l.options.opacity,.8);assert.equal(l.options.weight,.8);
   assert.equal(l.options.fillColor,'#2b2e34');assert.equal(l.options.fillOpacity,1);
  });
  assert.equal(outside.options.opacity,.18);assert.equal(outside.options.fillOpacity,.06);
  assert.equal(api.rtfpColor(0),'#606268');assert.equal(api.rtfpColor(null),null);
  S.layerBase=oldBase;S.mode=oldMode;S.sel=oldSel;
 });
 test('graticule lines and labels share the pane beneath the country fills',()=>{
  const before=new Set(S.map.layers);api.drawGraticule();
  const marks=[...S.map.layers].filter(l=>!before.has(l));assert.equal(marks.length,6);assert(marks.every(l=>l.options.pane==='ensoGraticule'));
  marks.forEach(l=>S.map.removeLayer(l));
 });
 test('diagram keys describe actual marks, windows and dated series',()=>{
  const analog=api.analogPlate();for(const label of ['five strongest past events','last published season','±0.5','five marked analog winters'])assert(analog.includes(label));
  api.renderCalendar();for(const label of ['Outlined: planting','Filled: harvest','El Niño slope falls','El Niño slope rises','Tinted band: DJF','Vertical rule: this month','grows through DJF'])assert(node('enso-calendar').textContent.includes(label));
  assert(node('enso-calendar').innerHTML.indexOf('cal-key')<node('enso-calendar').innerHTML.indexOf('class="enso-cal"'));
  api.renderMoney();assert(node('enso-money').querySelector('#enso-c-record'));for(const label of ['vs the previous six months','vs the same months a year earlier'])assert(node('enso-money').textContent.includes(label));
  api.drawCharts('ensowater');assert(!ctx.charts['enso-c-panama']);
  const ais=ctx.charts['enso-c-panama-daily'].keyNotes[0],dates=S.pwhist.chokepoints.panama.dates;const isoT=(d)=>{const m=String(d).match(/^(\d{4})-(\d{2})-(\d{2})/);return m?(+m[3])+' '+['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][m[2]-1]+' '+m[1]:d;};for(const t of ['Points: observed daily','7-day means','slot limit from each advisory',isoT(dates[0]),isoT(dates[dates.length-1])])assert(ais.includes(t));
 });
 console.log(passed+'/'+passed+' Stage D non-browser checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});

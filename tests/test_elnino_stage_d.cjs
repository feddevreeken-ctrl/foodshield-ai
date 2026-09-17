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
  globalThis.api={S,renderMapRanking,rankedPrices,rankedHotspots,priceMapSentence,mapState,renderControls,syncInstruments,selectCountry,flyTo,drawAlerts,alertLegend,corridorChipCandidates,placeCorridorChips,renderLegend,drawLanes,laneGeometry,corridorGeometry,fillFor,rtfpColor,renderWater,renderMoney,renderCoeffs,renderCalendar,analogPlate,drawCharts,paint,toggleSST,buildDefs};
  mk=function(id,cfg){ if(!S._chartFilter || S._chartFilter.indexOf(id)>=0) globalThis.charts[id]=cfg; };
  syncInstruments=renderMapTag=renderControls=renderDetail=renderFailures=wireTabKeys=syncTabRoving=wireRasterPlates=finishPlates=renderSubviewMeta=function(){};
})();`,ctx);
const api=ctx.api,S=api.S;
for(const [key,file] of Object.entries({model:'enso_model',calendars:'crop_calendars',enso:'enso',lanes:'enso_lanes',corridors:'enso_corridors',econ:'enso_econ',exp:'enso_exposure',portwatch:'portwatch',pwhist:'portwatch_history',rtfp:'rtfp',ffpi:'fao_ffpi',asap:'asap',gdacs:'gdacs',relief:'reliefweb_alerts',sst:'sst_anomaly'})){
 const data=JSON.parse(fs.readFileSync('data/'+file+'.json','utf8'));S[key]=data.data;S.meta[key]=data._meta;
}
let passed=0;
function test(name,fn){fn();passed++;console.log('ok',name);}
S.oniLive=S.enso.latest.anom;
S.map={layers:new Set(),hasLayer(l){return this.layers.has(l);},removeLayer(l){this.layers.delete(l);},invalidateSize(){}};
S.nino34=new Layer();S.ninoLabel=new Layer();S.sstLayer=new Layer();S.layerRegions=new Layer();S.layerRegions.eachLayer=()=>{};
const country=new Layer();country.feature={properties:{ISO_A3:'ZWE'}};
S.layerBase={eachLayer(fn){fn(country);}};
S.ready=true;S.names={};S.drawn={live:true,frame:true,limits:true,mech:true,map:true,water:true,money:true};
S.sel='ZWE';
test('lane geometry is data-only, validated and phase coloured',()=>{
 assert(S.lanes.lanes.every(l=>api.laneGeometry(l).length===0));
 api.drawLanes();assert.equal(S.laneLines.length,0);assert.equal(S.lanePins.length,S.lanes.lanes.length);
 S.lanePins.forEach(p=>assert(p.options.icon.html.includes('enso-choke-label')));
 const original=S.lanes;
 S.lanes={lanes:['el_nino','la_nina','none'].map((phase,i)=>({id:'test'+i,name:'test',phase,lat:0,lng:0,geometry:{type:'LineString',coordinates:[[10,20],[11,21]]}}))};
 api.drawLanes();assert.deepEqual(S.laneLines.map(l=>l.options.color),['#d2693a','#4a86b3','#6a685e']);
 assert(S.laneLines.every(l=>l.options.weight>=3&&!l.options.dashArray));assert.equal(S.laneLines[0].coords[0][0],20);
 assert.equal(api.laneGeometry({geometry:{type:'LineString',coordinates:[[999,20],[0,0]]}}).length,0);
 S.lanes=original;S.lanePins=[];S.laneLines=[];api.drawLanes();
});
test('Shipping draws nine sourced schematic corridors, chips and destination chevrons',()=>{
 assert.equal(S.corridorLines.length,9);assert.equal(S.corridorLabels.length,9);assert.equal(S.corridorArrows.length,9);
 S.corridors.corridors.forEach((c,i)=>{
  const lane=S.lanes.lanes.find(l=>l.id===c.lane),line=S.corridorLines[i];
  assert.equal(c.phase,lane.phase);assert.equal(line.options.color,{el_nino:'#d2693a',la_nina:'#4a86b3',none:'#6a685e'}[c.phase]);
  assert.equal(line.options.weight,2);assert.equal(line.options.opacity,.75);
  assert.equal(S.corridorLabels[i].options.icon.className,'enso-corridor-chip'+(c.phase==='el_nino'?'':' enso-corridor-secondary'));
  for(const text of [c.name,c.basis,'schematic corridor through named waypoints, not vessel tracks',...c.commodities,...c.sources])assert(line.tooltip.includes(text.replace(/&/g,'&amp;').replace(/"/g,'&quot;').replace(/'/g,'&#39;')));
 });
 assert(S.lanePins.every(p=>p.options.zIndexOffset>S.corridorLabels[0].options.zIndexOffset));
 const old=S.corridorLines.concat(S.corridorLabels,S.corridorArrows);old.forEach(l=>l.addTo(S.map));api.drawLanes();
 assert(old.every(l=>!S.map.hasLayer(l)));assert.equal(S.corridorLines.length,9);
});
test('long ocean legs curve and split at the dateline without a world-spanning chord',()=>{
 const path=api.corridorGeometry(S.corridors.corridors[0].waypoints);
 assert.equal(path.length,2);assert(path.flat().length>50);
 for(const segment of path)for(let i=1;i<segment.length;i++)assert(Math.abs(segment[i][1]-segment[i-1][1])<=180);
 assert.equal(path[0].at(-1)[1],-180);assert.equal(path[1][0][1],180);
 assert(Math.max(...path.flat().map(p=>p[0]))>35.44);
 assert.equal(api.corridorGeometry([[0,0],[100,0]]).length,0);
});
test('Stage H price ranking is finite, descending, dated and honest about sparse coverage',()=>{
 const saved=S.rtfp, oldMode=S.mode;S.mode='rtfp';
 api.renderMapRanking();const list=node('enso-map-ranking');
 const expected=Object.keys(saved).filter(k=>Number.isFinite(saved[k].food_inflation_pct)).sort((a,b)=>saved[b].food_inflation_pct-saved[a].food_inflation_pct).slice(0,12);
 assert.deepEqual(list.querySelectorAll('button').map(b=>b.getAttribute('data-map-country')),expected);
 expected.forEach(k=>{assert(list.textContent.includes(saved[k].as_of));assert(list.textContent.includes(saved[k].markets+' markets'));});
 assert.equal(api.mapState().title,'Where food prices are rising fastest');assert(api.priceMapSentence().includes('as of August 2026'));
 S.rtfp={ZWE:{food_inflation_pct:0,markets:2,as_of:'2026-07-01'},USA:{food_inflation_pct:-2,markets:3,as_of:'2026-08-01'},BAD:{food_inflation_pct:null},NAN:{food_inflation_pct:NaN}};
 api.renderMapRanking();assert.equal(list.querySelectorAll('button').length,2);assert(list.textContent.includes('Only 2 countries carry a value'));
 assert(api.priceMapSentence().includes('July 2026 to August 2026'));
 S.rtfp={};api.renderMapRanking();assert(list.textContent.includes('Only 0 countries'));assert(api.priceMapSentence().includes('reporting date unavailable'));
 S.rtfp=saved;S.mode=oldMode;
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
 for(const [sub,mode] of [['ensomoney','rtfp'],['ensolive','asap']]) {
  S.sub=sub;S.mode=mode;api.renderMapRanking();const button=node('enso-map-ranking').querySelectorAll('button')[0];
  button.onclick();assert.equal(S.sel,button.getAttribute('data-map-country'));assert.equal(S.sub,sub);assert.equal(S.mode,mode);
  api.selectCountry('ZWE',{fly:true});assert.equal(country.options.color,'#ebe9e2');
 }
 assert.equal(pan.length,2);assert(pan.every(p=>p.options.animate===false));S.sub='elnino';
});
test('Stage H alert keys count only mapped reports and rings contrast with both purples',()=>{
 const oldG=S.gdacs,oldR=S.relief,oldPins=S.alertPins;
 S.alertPins=[];S.gdacs={yes:{is_current:true,lat:1,lng:2},no:{is_current:false,lat:1,lng:2},bad:{is_current:true,lat:NaN,lng:2}};
 S.relief={events:[{iso3:'ZWE'},{iso3:'WLD'}]};api.drawAlerts();
 assert.equal(S.alertPins.length,2);assert(api.alertLegend().includes('GDACS 1'));assert(api.alertLegend().includes('ReliefWeb 1'));
 function luminance(hex){const rgb=hex.match(/[0-9a-f]{2}/gi).map(h=>parseInt(h,16)/255).map(v=>v<=.04045?v/12.92:Math.pow((v+.055)/1.055,2.4));return rgb[0]*.2126+rgb[1]*.7152+rgb[2]*.0722;}
 for(const p of S.alertPins)for(const purple of ['#8866ad','#51316f'])assert((luminance(p.options.color)+.05)/(luminance(purple)+.05)>3);
 assert(S.alertPins.every(p=>p.options.fillColor==='#11161e'&&p.options.fillOpacity===1));
 S.gdacs=oldG;S.relief=oldR;S.alertPins=oldPins;
});
test('Stage H corridor labels clear Panama and Amazon and only three chips remain on phones',()=>{
 assert.equal(S.corridorLabels.filter(l=>!l.options.icon.className.includes('secondary')).length,3);
 for(const id of ['panama','amazon']) {
  const i=S.corridors.corridors.findIndex(c=>c.lane===id),at=S.corridorLabels[i].coords;
  const ln=S.lanes.lanes.find(l=>l.id===id);
  assert(Math.hypot(at[0]-ln.lat,at[1]-ln.lng)>8, id+' label must move off the diamond');
 }
 assert.equal(S.corridorLines[0].coords.length,2);assert(S.corridorLines[0].coords.every(arc=>arc.length>1));
});
test('Explore instrument preserves controls and dates modelled paint from displayed metadata',()=>{
 const elements={};const get=id=>elements[id]||(elements[id]=new Element());
 const live={countries_overlay:JSON.parse(fs.readFileSync('data/countries.json')).data.countries};
 const main=vm.createContext({document:{getElementById:get},LIVE:live,window:{_mlState:{distOn:false,sstOn:false,flowsOn:false,expanded:false,hidden:new Set()},matchMedia(){return {matches:true};}},ML_TYPES:[{t:'drought',l:'Drought',c:'#c47a3c'}],_mlCounts(){return {drought:3};}});
 const begin=html.indexOf('function exploreScoreDate()'),finish=html.indexOf('// Backward-compatible wrapper',begin);
 vm.runInContext(html.slice(begin,finish),main);vm.runInContext('renderMapLayers()',main);
 assert(get('map-state').textContent.includes('Modelled: displayed FDRS'));
 assert(get('map-legend-date').textContent.includes(live.countries_overlay.AFG.fdrs_displayed_at.slice(0,10)));
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
 const main=vm.createContext({map:{getPane(){return null;},createPane(name){assert.equal(name,'exploreLand');return pane;}},L:{geoJSON(data,options){drawn.push(options);return {addTo(){}};}},data:{},LAND_TONE:'#0c0c0e',BORDER_LINE:'#aaa',lookupCountry:f=>f.country,mapRiskColor:()=> '#band',_scnPaintOn:false});
 const begin=html.indexOf("        var landPane = map.getPane('exploreLand')"),finish=html.indexOf('        G.countryLayer =',begin);
 vm.runInContext(html.slice(begin,finish),main);
 assert.equal(pane.style.zIndex,390);assert.equal(drawn[0].style.fillOpacity,1);assert.equal(drawn[0].pane,'exploreLand');
 const styleBegin=html.indexOf('  function styleFeature(f)'),styleEnd=html.indexOf('  function onEachFeature',styleBegin);
 vm.runInContext(html.slice(styleBegin,styleEnd),main);
 const unscored=vm.runInContext('styleFeature({})',main);assert.equal(unscored.fillOpacity,1);assert.equal(unscored.fillColor,'#343b46');
 assert.equal(vm.runInContext('styleFeature({country:{fdrs:null}}).fillColor',main),unscored.fillColor);
 for(const score of [12,38,63,82,95]){const paint=vm.runInContext('styleFeature({country:{fdrs:'+score+'}})',main);assert.equal(paint.fillColor,'#band');assert.equal(paint.fillOpacity,.18+Math.pow(score/100,.85)*.78);}
 const legend=html.slice(html.indexOf('<div id="map-legend"'),html.indexOf('</div><!-- /#map-canvas -->'));
 for(const label of ['0–25','26–50','51–75','76–88','89–100','Unscored','map-legend-date'])assert(legend.includes(label));
});
test('hatch SVG strokes match visible ochre and green samples',()=>{
 const svg=new Element('svg'), query=ctx.document.querySelectorAll;
 ctx.document.querySelectorAll=s=>s==='#enso-map svg'?[svg]:[];
 api.buildDefs();ctx.document.querySelectorAll=query;
 assert.deepEqual(svg.querySelectorAll('line').map(n=>n.getAttribute('stroke')),['#c9773a','#6ba36b']);
 S.mode='impact';S.showRegions=true;S.showSST=false;S.showLanes=false;api.renderLegend();
 const key=node('enso-legend').querySelector('.enso-legend').innerHTML;
 for(const c of ['#c9773a','#6ba36b'])assert(key.includes('repeating-linear-gradient(45deg,'+c));
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
  assert.equal(buttons.length,3);assert(minus.disabled);plus.onclick();assert.equal(m.getZoom(),3);assert(!minus.disabled);
  minus.onclick();assert.equal(m.getZoom(),2);plus.onclick();reset.onclick();assert.equal(m.getZoom(),2);
  for(let i=0;i<4;i++)plus.onclick();assert(plus.disabled);reset.onclick();assert(!plus.disabled);
 });
 const expected={elnino:'sst',ensoharvest:'impact',ensowater:'none',ensomoney:'rtfp',ensolive:'asap'};
 for(let cycle=0;cycle<2;cycle++) for(const [view,mode] of Object.entries(expected)) {
  node('scroller').scrollTop=1400;
  await ctx.window.ensoInit(view);pending.splice(0).forEach(fn=>fn());
  test('cycle '+(cycle+1)+' '+view+' clears inactive overlays and keeps its visible key',()=>{
   assert.equal(S.mode,mode);
   for(const l of [S.nino34,S.ninoLabel,S.sstLayer])assert.equal(S.map.hasLayer(l),view==='elnino');
   assert.equal(S.map.hasLayer(S.layerRegions),view==='ensoharvest');
   for(const l of S.lanePins.concat(S.laneLines,S.corridorLines,S.corridorLabels,S.corridorArrows))assert.equal(S.map.hasLayer(l),view==='ensowater');
   for(const l of S.alertPins)assert.equal(S.map.hasLayer(l),view==='ensolive');
   assert.equal(S.annoLayers.length,view==='ensoharvest'?6:view==='ensowater'?3:0);
   assert.equal(country.options.color,['ensoharvest','ensomoney','ensolive'].includes(view)?'#ebe9e2':'#0b0b0d');
   if(view!=='ensolive')assert(!country.element.classList.contains('enso-hotspot')&&!country.element.classList.contains('enso-major-hotspot'));
   const legend=node('enso-legend'),visible=legend.querySelector('.enso-legend'),key=visible.textContent;
   assert.equal(legend.querySelectorAll('details').length,1);assert.equal(visible.querySelectorAll('details').length,0);
   assert.equal(key.includes('Niño 3.4 box'),view==='elnino');
   assert.equal(key.includes('El Niño reduces output here'),view==='ensoharvest');
   assert.equal(key.includes('El Niño raises output here'),view==='ensoharvest');
   assert.equal(key.includes('degraded on the La Niña side'),view==='ensowater');
   assert.equal(key.includes('corridor: schematic route through named ports and chokepoints'),view==='ensowater');
   if(view==='ensowater'){assert.equal(S.corridorLines.filter(l=>S.map.hasLayer(l)).length,9);assert.equal(S.corridorLabels.filter(l=>S.map.hasLayer(l)).length,9);for(const c of S.corridors.corridors){assert(legend.querySelector('details').textContent.includes(c.basis.replace(/'/g,'&#39;')));}}
   assert.equal(key.includes('GDACS drought'),view==='ensolive');
   if(view==='ensolive')for(const label of ['hotspot','major hotspot','ReliefWeb humanitarian event'])assert(key.includes(label));
   if(view==='ensowater')for(const l of S.lanes.lanes)assert(legend.querySelector('details').textContent.includes(l.name));
   if(view==='ensomoney')for(const label of ['−10%','0%','+30%','Cyan','warm grey','magenta','No data'])assert(key.includes(label));
   if(cycle||view!=='elnino')assert.equal(node('scroller').scrollTop,0);
  });
 }
 test('hotspot classes and price no-data preserve distinct palettes',()=>{
  S.mode='asap';const saved=S.asap;S.asap={ZWE:{hotspot_code:1}};api.paint();assert.equal(api.fillFor('ZWE'),'#8866ad');assert(country.element.classList.contains('enso-hotspot'));
  S.asap.ZWE.hotspot_code=2;api.paint();assert.equal(api.fillFor('ZWE'),'#51316f');assert(country.element.classList.contains('enso-major-hotspot'));assert(!country.element.classList.contains('enso-hotspot'));
  S.mode='rtfp';api.paint();assert(!country.element.classList.contains('enso-major-hotspot'));assert.notEqual(api.rtfpColor(0),'#1c1c22');assert.equal(api.rtfpColor(null),null);S.asap=saved;
 });
 test('diagram keys describe actual marks, windows and dated series',()=>{
  const analog=api.analogPlate();for(const label of ['five strongest past events','last published season','±0.5','five marked analog winters'])assert(analog.includes(label));
  api.renderCalendar();for(const label of ['Hatched: planting','Solid: harvest','El Niño slope falls','El Niño slope rises','Tinted band: DJF','Vertical rule: this month','grows through DJF'])assert(node('enso-calendar').textContent.includes(label));
  assert(node('enso-calendar').innerHTML.indexOf('cal-key')<node('enso-calendar').innerHTML.indexOf('class="enso-cal"'));
  api.renderCoeffs();assert(node('enso-coeffs').textContent.includes('La Niña slope × negative ONI'));
  api.renderMoney();assert(node('enso-money').querySelector('#enso-c-record'));for(const label of ['Green: prices fall','Ochre: prices rise','previous six months','same six months a year earlier'])assert(node('enso-money').textContent.includes(label));
  api.drawCharts('ensowater');assert(ctx.charts['enso-c-panama'].keyNotes[0].includes('pale diamonds'));
  const ais=ctx.charts['enso-c-panama-daily'].keyNotes[0],dates=S.pwhist.chokepoints.panama.dates;for(const t of ['Points: observed daily','7-day means','advisory effective dates',dates[0],dates[dates.length-1]])assert(ais.includes(t));
  api.drawCharts('ensomoney');const ffpi=ctx.charts['enso-c-ffpi'].keyNotes[0];for(const t of ['Circles','triangles','diamond',S.ffpi.latest.month])assert(ffpi.includes(t));
  const bars=ctx.charts['enso-c-rtfp'];assert(bars.keyNotes[0].includes('as of'));bars.data.datasets[0].data.forEach((v,i)=>assert.equal(bars.data.datasets[0].backgroundColor[i],api.rtfpColor(v)));
 });
 console.log(passed+'/'+passed+' Stage D non-browser checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});

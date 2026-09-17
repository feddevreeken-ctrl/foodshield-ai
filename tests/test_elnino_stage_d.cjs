// Non-browser Stage D gate. Runs the actual panel closure with a small DOM/Leaflet double.
// Browser layout and screenshots remain the owner's gate.
const fs = require('fs'), vm = require('vm'), assert = require('assert');
const html = fs.readFileSync('index.html', 'utf8');
class Element {
  constructor(tag = 'div') { this.tagName = tag; this.attrs = {}; this.childNodes = []; this.style = {}; this.dataset = {}; }
  get children() { return this.childNodes.filter(n => n instanceof Element); }
  set className(v) { this.attrs.class = v; } get className() { return this.attrs.class || ''; }
  get classList() { return { contains: c => this.className.split(/\s+/).includes(c), toggle: (c,on) => { const a=this.className.split(/\s+/).filter(x=>x&&x!==c); if(on)a.push(c); this.className=a.join(' '); } }; }
  setAttribute(k,v) { this.attrs[k] = String(v); } getAttribute(k) { return this.attrs[k]; } removeAttribute(k) { delete this.attrs[k]; }
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
  querySelectorAll(selector) { const out=[]; const match=n=>selector[0]==='.'?n.classList.contains(selector.slice(1)):selector[0]==='#'?n.attrs.id===selector.slice(1):n.tagName===selector; const visit=n=>n.children.forEach(c=>{if(match(c))out.push(c);visit(c);});visit(this);return out; }
  querySelector(s) { return this.querySelectorAll(s)[0]||null; }
  insertAdjacentHTML(_,value) { const n=new Element();n.innerHTML=value;n.childNodes.slice().forEach(c=>this.appendChild(c)); }
}
const nodes = {};
function node(id) { return nodes[id] || (nodes[id]=new Element()); }
class Layer {
  constructor(coords,opts={}) { this.coords=coords;this.options=opts;this.element=new Element('path'); }
  addTo(map) { map.layers.add(this);return this; }
  on(){return this;} bindTooltip(){return this;} bringToFront(){return this;}
  getElement(){return this.element;} setStyle(s){Object.assign(this.options,s);}
  getBounds(){return {getCenter(){return {lat:0,lng:0};}};}
}
const L={polyline:(c,o)=>new Layer(c,o),marker:(c,o)=>new Layer(c,o),circleMarker:(c,o)=>new Layer(c,o),divIcon:o=>o};
const pending=[];
const ctx=vm.createContext({console,Date,URL,URLSearchParams,Event,L,charts:{},RAMP:['#1','#2','#3','#4','#5'],setTimeout:fn=>pending.push(fn),clearTimeout(){},window:{location:{href:'http://localhost/index.html',search:''},matchMedia(){return {matches:true};}},document:{getElementById:node,querySelector:s=>s==='#tab-elnino .content-page'?node('scroller'):null,querySelectorAll(){return [];},createElement:t=>new Element(t),createElementNS:(_,t)=>new Element(t),addEventListener(){}}});
const start=html.indexOf('(function () {',html.indexOf('   THE MAP USES A DIVERGING')),end=html.indexOf('\n})();',start);
vm.runInContext(html.slice(start,end)+`
  globalThis.api={S,renderLegend,drawLanes,laneGeometry,fillFor,rtfpColor,renderWater,renderMoney,renderCoeffs,renderCalendar,analogPlate,drawCharts,paint,toggleSST,buildDefs};
  mk=function(id,cfg){ if(!S._chartFilter || S._chartFilter.indexOf(id)>=0) globalThis.charts[id]=cfg; };
  syncInstruments=renderMapTag=renderControls=renderDetail=renderFailures=wireTabKeys=syncTabRoving=wireRasterPlates=finishPlates=renderSubviewMeta=function(){};
})();`,ctx);
const api=ctx.api,S=api.S;
for(const [key,file] of Object.entries({model:'enso_model',calendars:'crop_calendars',enso:'enso',lanes:'enso_lanes',econ:'enso_econ',exp:'enso_exposure',portwatch:'portwatch',pwhist:'portwatch_history',rtfp:'rtfp',ffpi:'fao_ffpi',asap:'asap',gdacs:'gdacs',relief:'reliefweb_alerts',sst:'sst_anomaly'})){
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
 const expected={elnino:'sst',ensoharvest:'impact',ensowater:'none',ensomoney:'rtfp',ensolive:'asap'};
 for(let cycle=0;cycle<2;cycle++) for(const [view,mode] of Object.entries(expected)) {
  node('scroller').scrollTop=1400;
  await ctx.window.ensoInit(view);pending.splice(0).forEach(fn=>fn());
  test('cycle '+(cycle+1)+' '+view+' clears inactive overlays and keeps its visible key',()=>{
   assert.equal(S.mode,mode);
   for(const l of [S.nino34,S.ninoLabel,S.sstLayer])assert.equal(S.map.hasLayer(l),view==='elnino');
   assert.equal(S.map.hasLayer(S.layerRegions),view==='ensoharvest');
   for(const l of S.lanePins.concat(S.laneLines))assert.equal(S.map.hasLayer(l),view==='ensowater');
   for(const l of S.alertPins)assert.equal(S.map.hasLayer(l),view==='ensolive');
   assert.equal(S.annoLayers.length,view==='ensoharvest'?6:view==='ensowater'?3:0);
   assert.equal(country.options.color,view==='ensoharvest'?'#ebe9e2':'#0b0b0d');
   if(view!=='ensolive')assert(!country.element.classList.contains('enso-hotspot')&&!country.element.classList.contains('enso-major-hotspot'));
   const legend=node('enso-legend'),visible=legend.querySelector('.enso-legend'),key=visible.textContent;
   assert.equal(legend.querySelectorAll('details').length,1);assert.equal(visible.querySelectorAll('details').length,0);
   assert.equal(key.includes('Niño 3.4 box'),view==='elnino');
   assert.equal(key.includes('El Niño reduces output here'),view==='ensoharvest');
   assert.equal(key.includes('El Niño raises output here'),view==='ensoharvest');
   assert.equal(key.includes('degraded on the La Niña side'),view==='ensowater');
   assert.equal(key.includes('GDACS drought'),view==='ensolive');
   if(view==='ensolive')for(const label of ['hotspot','major hotspot','ReliefWeb humanitarian event'])assert(key.includes(label));
   if(view==='ensowater')for(const l of S.lanes.lanes)assert(legend.querySelector('details').textContent.includes(l.name));
   if(view==='ensomoney')for(const label of ['−10%','0%','+30%','Cyan','warm grey','magenta','No data'])assert(key.includes(label));
   if(cycle||view!=='elnino')assert.equal(node('scroller').scrollTop,0);
  });
 }
 test('hotspot classes and price no-data preserve distinct palettes',()=>{
  S.mode='asap';const saved=S.asap;S.asap={ZWE:{hotspot_code:1}};api.paint();assert.equal(api.fillFor('ZWE'),'#9b83c9');assert(country.element.classList.contains('enso-hotspot'));
  S.asap.ZWE.hotspot_code=2;api.paint();assert.equal(api.fillFor('ZWE'),'#69439b');assert(country.element.classList.contains('enso-major-hotspot'));assert(!country.element.classList.contains('enso-hotspot'));
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
  const bars=ctx.charts['enso-c-rtfp'];assert(bars.keyNotes[0].includes('as_of'));bars.data.datasets[0].data.forEach((v,i)=>assert.equal(bars.data.datasets[0].backgroundColor[i],api.rtfpColor(v)));
 });
 console.log(passed+'/'+passed+' Stage D non-browser checks passed');
})().catch(e=>{console.error(e);process.exitCode=1;});

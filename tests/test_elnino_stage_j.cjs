// Stage J: actual panel functions against source feeds and measured-rectangle fixtures.
// Browser geometry and screenshots remain the owner's separate gate.
const fs=require('fs'),vm=require('vm'),assert=require('assert');
const html=fs.readFileSync('index.html','utf8');
const start=html.indexOf('(function () {',html.indexOf('   THE MAP USES A DIVERGING')),end=html.indexOf('\n})();',start);
const nodes={};
const node=id=>nodes[id]||(nodes[id]={innerHTML:'',style:{},setAttribute(){},querySelectorAll(){return [];}});
let plot=null;
const ctx=vm.createContext({console,Date,URL,URLSearchParams,charts:{},window:{location:{href:'http://localhost',search:''}},document:{addEventListener(){},getElementById:node,querySelector(){return plot;}}});
vm.runInContext(html.slice(start,end)+`
 globalThis.api={S,renderIndices,renderLegend,coverageColor,fillFor,placeChokepointLabels,analogPlate,alignAnalogLeaders,drawCharts,renderMoney,rtfpColor,oceanHeading};
 mk=function(id,cfg){if(!S._chartFilter||S._chartFilter.indexOf(id)>=0)globalThis.charts[id]=cfg;};
 syncInstruments=renderMapRanking=renderMapTag=compactLegend=function(){};
})();`,ctx);
const api=ctx.api,S=api.S;
for(const [key,file] of Object.entries({indices:'enso_indices',enso:'enso',regions:'enso_regions',lanes:'enso_lanes',rtfp:'rtfp',exp:'enso_exposure',econ:'enso_econ',mech:'enso_mechanism',bulletins:'enso_bulletins'})){
 const feed=JSON.parse(fs.readFileSync('data/'+file+'.json'));S[key]=feed.data;S.meta[key]=feed._meta;
}
const tele=new Set(S.regions.regions.flatMap(r=>r.iso3));S.isoIndex={};tele.forEach(iso=>S.isoIndex[iso]=[{}]);
S.oniLive=S.enso.latest.anom;S.showSST=S.showRegions=S.showLanes=S.showAlerts=false;
let passed=0;function test(name,fn){fn();passed++;console.log('ok',name);}
test('hero leads with the sourced CPC outlook and separates RONI from ONI',()=>{
 const out=api.oceanHeading(),cpc=S.bulletins.bulletins.find(b=>b.agency==='NOAA CPC');
 assert(cpc.summary.includes('very strong'));assert(out.includes('El Niño strengthening toward a very strong event'));
 for(const text of ['RONI','ONI','more than 90%'])assert(out.includes(text));
 assert(!out.includes('ONI band is'));assert(out.includes('the strongest warming in the east'));
});
test('indices use published thresholds, retain source windows, and omit the weekly bar',()=>{
 api.renderIndices();const out=node('enso-indices').innerHTML, widths={};
 for(const r of S.indices.indices){
  const section=out.split('data-index="'+r.key+'"')[1].split('class="enso-idx-note"')[0];
  assert(section.includes(r.window.replace(/&/g,'&amp;')));
  if(r.key==='wk34'){assert(!section.includes('enso-idx-bar'));assert(section.includes('value only'));continue;}
  assert(section.includes('data-threshold="'+({oni:.5,roni:.5,bom_rel:.8,soi:-7}[r.key])+'"'));
  assert.equal((section.match(/class="enso-idx-tick"/g)||[]).length,2);
  widths[r.key]=Number(section.match(/width:([\d.]+)%/)[1]);
  assert(Math.abs(widths[r.key]-Math.abs(r.value/r.threshold)*25)<.01);
 }
 assert(widths.soi<widths.oni);assert(widths.soi<widths.bom_rel);
 assert(out.includes('not against the other indices'));
});
test('coverage ramp has three anchors and different USA and ZAF fills',()=>{
 S.mode='coverage';api.renderLegend();const out=node('enso-legend').innerHTML;
 assert(out.includes('enso-ramp enso-coverage-ramp'));
 for(const n of ['0%','50%','100%'])assert(out.includes('<span>'+n+'</span>'));
 assert(out.includes('Dark: no fitted signal.'));
 assert.notEqual(api.fillFor('USA'),api.fillFor('ZAF'));
 assert(out.includes(api.coverageColor(.5)));assert(out.includes(api.coverageColor(1)));
});
const rect=(x,y,w,h)=>({left:x,top:y,right:x+w,bottom:y+h,width:w,height:h});
function choke(x,y,w,h){
 return {style:{},getAttribute(){return '25';},getBoundingClientRect(){
  const side=this.style.right==='25px',shift=(this.style.transform||'').match(/translate\(([-\d.]+)px,([-\d.]+)px\)/);
  return rect(30+x+(side?24-25-w:25)+(shift?+shift[1]:0),40+y+2+(shift?+shift[2]:0),w,h);
 }};
}
test('measured labels stay inside a phone plate and clear graticule text on repeated placement',()=>{
 const labels=[choke(280,70,124,38),choke(85,140,130,38)],grat={getBoundingClientRect:()=>rect(140,182,140,12)};
 const host={getBoundingClientRect:()=>rect(30,40,350,330),querySelectorAll:s=>s==='.enso-choke-label'?labels:[grat]};
 S.map={getContainer:()=>host,latLngToContainerPoint(){}};S.showLanes=true;S.corridorLabels=[];
 for(let pass=0;pass<2;pass++){
  api.placeChokepointLabels();
  for(const l of labels){const r=l.getBoundingClientRect(),g=grat.getBoundingClientRect();
   assert(r.left>=34&&r.right<=376&&r.top>=44&&r.bottom<=366);
   assert(r.right<=g.left||r.left>=g.right||r.bottom<=g.top||r.top>=g.bottom);
  }
  assert.equal(labels[0].style.right,'25px','right-edge label flips to the left of its pin');
 }
 S.showLanes=false;
});
test('Panama plots every dated advisory and no empty normal category',()=>{
 api.drawCharts('ensowater');const c=ctx.charts['enso-c-panama'],p=S.lanes.lanes.find(l=>l.id==='panama');
 assert.equal(p.precedent_2023.normal_transits_per_day,undefined);
 assert.equal(c.data.labels.length,p.precedent_2023.steps.length+p.live_2026.steps.length);
 assert(c.data.labels.every((l,i)=>!l.includes('normal')&&c.data.datasets.some(ds=>ds.data[i]!=null)));
 assert.deepEqual(Array.from(c.data.datasets[0].data.slice(0,p.precedent_2023.steps.length)),p.precedent_2023.steps.map(s=>s.total));
});
test('all five analog connectors end exactly at measured label edges after resize',()=>{
 const out=api.analogPlate();assert.equal((out.match(/class="enso-analog-past"/g)||[]).length,5);
 const lines=Array.from({length:5},()=>({attrs:{},setAttribute(k,v){this.attrs[k]=v;}}));let width=1200;
 const labels=lines.map((_,i)=>({getBoundingClientRect:()=>rect(50+width-150-i*4,60+i*20,150+i*4,16)}));
 plot={querySelector:()=>({getBoundingClientRect:()=>rect(50,30,width,300)}),querySelectorAll:s=>s.includes('endlabels')?labels:lines};
 for(width of [1200,1000]){
  api.alignAnalogLeaders();lines.forEach((l,i)=>{
   const r=labels[i].getBoundingClientRect();assert.equal(l.attrs.x2,(r.left-50)/width*1000);assert.equal(l.attrs.y2,r.top+8-30);
  });
 }
 plot=null;
});
test('prices include every valued teleconnection country in descending order with exact source values',()=>{
 api.drawCharts('ensomoney');const c=ctx.charts['enso-c-rtfp'],ds=c.data.datasets[0];
 const expected=Object.keys(S.rtfp).filter(iso=>tele.has(iso)&&Number.isFinite(S.rtfp[iso].food_inflation_pct));
 assert.deepEqual([...ds.iso3].sort(),expected.sort());assert(ds.data.some(v=>v<0));
 ds.iso3.forEach((iso,i)=>{const r=S.rtfp[iso];assert.equal(ds.data[i],r.food_inflation_pct);if(i)assert(ds.data[i-1]>=ds.data[i]);
  assert.equal(ds.backgroundColor[i],api.rtfpColor(r.food_inflation_pct));
  const tip=c.options.plugins.tooltip.callbacks;assert(tip.label({dataIndex:i}).includes(r.markets+' markets · as of '+r.as_of));assert(tip.afterLabel({dataIndex:i}).includes(r.source_url));
 });
 api.renderMoney();const out=node('enso-money').innerHTML;assert(out.includes('Bars: '+expected.length+' teleconnection countries'));assert(!out.includes('highest eighteen'));
 for(const iso of expected){const r=S.rtfp[iso];assert(out.includes('data-price-iso="'+iso+'"'));assert(out.includes(r.as_of));}
});
test('price key never names a hue absent from positive-only, negative-only or zero-only plots',()=>{
 const saved=S.rtfp,iso=Object.keys(S.isoIndex)[0];
 for(const value of [2,-2,0]){
  S.rtfp={[iso]:{food_inflation_pct:value,markets:1,as_of:'2026-08-01'}};api.drawCharts('ensomoney');
  const key=ctx.charts['enso-c-rtfp'].keyNotes[0];assert.equal(key.includes('Blue-grey'),value<0);assert.equal(key.includes('Ochre to orange'),value>0);assert.equal(key.includes('Ground grey'),value===0);
 }
 S.rtfp=saved;
});
console.log(passed+'/'+passed+' Stage J non-browser checks passed');

// Node adapter for the same scorer shipped to the browser. No network access.
import fs from 'node:fs';
import path from 'node:path';
import vm from 'node:vm';
import crypto from 'node:crypto';
import {fileURLToPath} from 'node:url';
import scorer from '../js/fdrs.js';
const root=path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const hash=s=>crypto.createHash('sha256').update(s).digest('hex');
export function calculate({htmlPath=path.join(root,'index.html'), countriesPath=path.join(root,'data/countries.json'), nowcastPath=path.join(root,'data/nowcast.json')}={}) {
  const html=fs.readFileSync(htmlPath,'utf8');
  const context=vm.createContext({window:{},console:{info(){}},FoodShieldScore:scorer});
  const seedStart=html.indexOf('const COUNTRIES = [');
  const seedEnd=html.indexOf('\n];',seedStart)+3;
  vm.runInContext(html.slice(seedStart,seedEnd)+'\nwindow.COUNTRIES=COUNTRIES;',context);
  // Replay the existing food-menu initialization. Its curated fallback lists are
  // inputs to SCE and remain in the page; the score arithmetic lives in fdrs.js.
  vm.runInContext(html.slice(html.indexOf('const FOOD_TRADE_BLOCKLIST ='),html.indexOf('// ═',html.indexOf('function displayExportList'))),context);
  const menuStart=html.indexOf('let _scrubDone =');
  const menuEnd=html.indexOf('\n// ═',html.indexOf('function scrubFoodData()',menuStart));
  vm.runInContext('var _companyIndex=null;'+html.slice(menuStart,menuEnd)+'\nscrubFoodData();',context);
  const countries=JSON.parse(fs.readFileSync(countriesPath)).data.countries;
  const inputs={};const live={};
  const files={psd:'usda_psd',ndgain:'ndgain',aqueduct:'aqueduct',cckp:'cckp',asap:'asap',inform:'inform_risk',wgi:'wgi',lpi:'lpi',hapi_conflict:'hapi_conflict',feeding_america:'feeding_america_states',nowcast:'nowcast'};
  for (const [key,name] of Object.entries(files)) {
    const raw=fs.readFileSync(key==='nowcast'?nowcastPath:path.join(root,'data',name+'.json'),'utf8');
    const j=JSON.parse(raw);live[key]=j.data;inputs[name]={sha256:hash(raw),version:j._meta?.version||j._meta?.schema_version||null,generated_at:j._meta?.generated_at||null};
  }
  const canonical={};
  for (const c of context.window.COUNTRIES) {
    const row=countries[c.iso];if(!row)continue;
    canonical[c.iso]={};
    for(const [key,meta] of Object.entries(row)) {
      if(key.startsWith('fdrs_displayed')||key==='fdrs_nowcast_delta')continue;
      const value=meta&&typeof meta==='object'&&!Array.isArray(meta)&&'value' in meta?meta.value:meta;
      c[key]=structuredClone(value);canonical[c.iso][key]=value;
    }
  }
  inputs.countries={sha256:hash(JSON.stringify(canonical)),basis:'canonical fields excluding additive displayed snapshot'};
  inputs.page={sha256:hash(html),basis:'embedded profiles and food-menu inputs'};
  inputs.scorer={sha256:hash(fs.readFileSync(path.join(root,'js/fdrs.js'))),version:'stage-e-v1'};
  context.LIVE=live;
  const out={};
  for(const c of context.window.COUNTRIES) {
    context.current=c;
    const menus=vm.runInContext('({imports:displayImportList(current),exports:displayExportList(current)})',context);
    out[c.iso]=scorer.displayed(c,live,menus);
  }
  return {scores:out,inputs};
}
if(process.argv[1]===fileURLToPath(import.meta.url))process.stdout.write(JSON.stringify(calculate()));

#!/usr/bin/env node
/*
 * FDRS v2 formula regression test — REAL in-browser JS.
 *
 * Closes the gap left by scripts/test_fdrs_v2.py: that test validates a Python
 * RE-IMPLEMENTATION of the formula, but nothing exercised the actual JavaScript
 * `fdrsV2` that the live app ships. This imports the shared js/fdrs.js scorer,
 * verifies that index.html loads and uses it, and runs the same pinned
 * tests/fdrs_cases.json fixtures that the Python test uses.
 *
 * No DOM, no jsdom, no npm deps — fdrsV2 is a pure function of the component vector.
 *
 * Run:  node scripts/test_fdrs_v2_js.mjs
 * Exit code 0 = all pass; 1 = a mismatch (formula-parity regression or bad extraction).
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = path.dirname(path.dirname(fileURLToPath(import.meta.url)));
const HTML = path.join(ROOT, 'index.html');
const FIX = path.join(ROOT, 'tests', 'fdrs_cases.json');

import scorer from '../js/fdrs.js';
import assert from 'node:assert/strict';
function extractShippedFdrs(html) {
  if (!html.includes('<script src="js/fdrs.js"></script>') || !html.includes('window.fdrsV2 = FoodShieldScore.score')) throw new Error('page must use shared scorer');
  return {fdrsV2:scorer.score, weightsSrc:JSON.stringify(scorer.weights)};
}

function main() {
  const html = fs.readFileSync(HTML, 'utf8');
  assert(JSON.parse(fs.readFileSync(path.join(ROOT,'vercel.json'),'utf8')).builds.some(b=>b.src==='js/**'), 'deployment must ship shared scorer');
  const { fdrsV2, weightsSrc } = extractShippedFdrs(html);
  const fx = JSON.parse(fs.readFileSync(FIX, 'utf8'));
  const fixtureWeights = fx._meta.weights;

  // Sanity: the weights extracted from the shipped JS must match the fixture pins.
  const jsWeights = JSON.parse(weightsSrc);
  const weightsMatch = jsWeights.length === fixtureWeights.length &&
    jsWeights.every((w, i) => Math.abs(w - fixtureWeights[i]) < 1e-12);

  let failed = 0;
  console.log('FDRS v2 — REAL shipped JS (index.html::fdrsV2) vs pinned fixtures\n');
  if (!weightsMatch) {
    console.log(`  [FAIL] weights: JS ${weightsSrc} != fixture ${JSON.stringify(fixtureWeights)}`);
    failed++;
  } else {
    console.log(`  [ok  ] weights: shipped JS matches fixture pins ${weightsSrc}`);
  }

  for (const c of fx.cases) {
    const got = fdrsV2(c.c);
    const exp = c.expected_fdrs;
    const ok = got === exp;
    if (!ok) failed++;
    console.log(`  [${ok ? 'ok  ' : 'FAIL'}] ${c.name.padEnd(38)} expected ${String(exp).padStart(3)}  got ${String(got).padStart(3)}   ${c.assert || ''}`);
  }

  // Same extra invariants the Python test asserts.
  const ampHi = fdrsV2([90, 80, 70, 60, 60, 50, 40, 90, 40]);
  const ampLo = fdrsV2([90, 80, 70, 60, 60, 50, 40, 10, 40]);
  if (ampHi > ampLo) {
    console.log(`  [ok  ] amplifier interaction: ${ampHi} > ${ampLo} (econ_access raises an import-dependent score)`);
  } else {
    console.log(`  [FAIL] amplifier interaction: high-fragility (${ampHi}) should exceed low-fragility (${ampLo})`);
    failed++;
  }
  const allMax = fdrsV2([100, 100, 100, 100, 100, 100, 100, 100, 100]);
  if (allMax === 100) {
    console.log('  [ok  ] ceiling: all-max clips to 100 (raw 106)');
  } else {
    console.log(`  [FAIL] ceiling: all-max must clip to 100 (got ${allMax})`);
    failed++;
  }

  for (const c of fx.cases) {
    const d=scorer.decomposition(c.c);
    assert(Math.abs(d.contributions.reduce((sum,v)=>sum+(v||0),0)-d.base)<1e-10);
    assert.equal(d.score,scorer.score(c.c));
    d.components.forEach((v,i)=>assert.equal(d.contributions[i]===null,v===null));
  }
  const c={iso:'US-XX',c:[50,50,50,50,50,50,50,50,50]};
  const live={feeding_america:{'US-XX':{food_insecurity_pct:14}},nowcast:{'US-XX':{adjustment:.4}}};
  const r=scorer.displayed(c,live,{imports:[],exports:[]});
  assert.equal(r.structural,52);assert(Math.abs(r.base-54.1)<1e-10);
  assert.equal(r.displayed,55);assert.equal(r.delta,r.displayed-r.base);
  live.nowcast['US-XX'].adjustment=100;
  assert.equal(scorer.displayed(c,live,{imports:[],exports:[]}).displayed,100);
  assert.equal(scorer.decomposition([null,null,null]).observedWeight,0);
  console.log('  [ok  ] decomposition sum/missingness, US blend rounding and clipping');

  console.log(`\n${fx.cases.length + 3} checks, ${failed} failed.`);
  if (failed) {
    console.log('MISMATCH — the shipped JS fdrsV2 disagrees with the pinned fixtures.');
    return 1;
  }
  console.log('PASS — shipped JS fdrsV2 matches the pinned fixtures exactly.');
  return 0;
}

process.exit(main());

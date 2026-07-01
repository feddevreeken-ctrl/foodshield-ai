#!/usr/bin/env node
/*
 * FDRS v2 formula regression test — REAL in-browser JS.
 *
 * Closes the gap left by scripts/test_fdrs_v2.py: that test validates a Python
 * RE-IMPLEMENTATION of the formula, but nothing exercised the actual JavaScript
 * `fdrsV2` that the live app ships. This script slices the ACTUAL `FDRS_V2_W`
 * weights and `fdrsV2` function text out of index.html and evaluates them, then
 * runs them against the same tests/fdrs_cases.json fixtures the Python test uses.
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

// Slice a brace-balanced block out of `src` starting at the first `{` at/after `from`.
function sliceBalanced(src, from) {
  const start = src.indexOf('{', from);
  if (start < 0) throw new Error('no opening brace found');
  let depth = 0;
  for (let i = start; i < src.length; i++) {
    const ch = src[i];
    if (ch === '{') depth++;
    else if (ch === '}') { depth--; if (depth === 0) return src.slice(start, i + 1); }
  }
  throw new Error('unbalanced braces');
}

function extractShippedFdrs(html) {
  // FDRS_V2_W = [ ... ];
  const wMatch = html.match(/const\s+FDRS_V2_W\s*=\s*(\[[^\]]*\])\s*;/);
  if (!wMatch) throw new Error('could not extract FDRS_V2_W from index.html');
  const weightsSrc = wMatch[1];

  // window.fdrsV2 = function fdrsV2(cv, sceFallback) { ... };
  const fnIdx = html.indexOf('window.fdrsV2 = function fdrsV2');
  if (fnIdx < 0) throw new Error('could not locate fdrsV2 in index.html');
  const paramsIdx = html.indexOf('(', fnIdx);
  const params = html.slice(paramsIdx + 1, html.indexOf(')', paramsIdx));
  const body = sliceBalanced(html, html.indexOf(')', paramsIdx) + 1);

  // Build the real function from the shipped source. FDRS_V2_W is referenced by
  // the body, so it must be in scope — inject its exact shipped literal.
  const factory = new Function(
    `const FDRS_V2_W = ${weightsSrc};` +
    `return function fdrsV2(${params}) ${body};`
  );
  return { fdrsV2: factory(), weightsSrc };
}

function main() {
  const html = fs.readFileSync(HTML, 'utf8');
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

  console.log(`\n${fx.cases.length + 3} checks, ${failed} failed.`);
  if (failed) {
    console.log('MISMATCH — the shipped JS fdrsV2 disagrees with the pinned fixtures.');
    return 1;
  }
  console.log('PASS — shipped JS fdrsV2 matches the pinned fixtures exactly.');
  return 0;
}

process.exit(main());

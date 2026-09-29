/* Per-path <head> for every page path of foodshield.nl.

   The site is one file (index.html) served for every page path, and updatePageTitle()
   sets the title, description and canonical in the browser. Link previews (LinkedIn,
   Slack) and most AI crawlers do not run JS, so without this they all saw the home
   page's head. This Vercel Routing Middleware streams index.html and rewrites only the
   head tags before the JSON-LD block, from seo.json (the same table TAB_SEO is generated
   from; see scripts/build_seo.py). Every user agent gets the same response.

   The path rules mirror pathState()/buildPath()/countrySlug() in index.html. If the
   fetch of index.html fails, the middleware steps aside and vercel.json serves the
   plain file as before. */
import seo from './seo.json';

export const config = {
  // Page paths only: no data/, js/, img/, api/, Vercel internals or anything with a dot.
  matcher: ['/((?!api/|data/|js/|img/|_vercel/|\\.well-known/)[^.]*)'],
};

const SITE = seo.site;
const TAB_SLUG = { country: 'countryflows', score: 'rankings', forecast: 'outlook' };
const ENSO_SLUG = { ensoharvest: 'harvests', ensowater: 'shipping', ensomoney: 'prices', ensolive: 'reported', ensomech: 'mechanism' };
const SLUG_TAB = Object.fromEntries(Object.entries(TAB_SLUG).map(([k, v]) => [v, k]));
const SLUG_ENSO = Object.fromEntries(Object.entries(ENSO_SLUG).map(([k, v]) => [v, k]));
const ENSO_TABS = new Set(['elnino', ...Object.keys(ENSO_SLUG)]);
const TAB_HOST_ALIAS = { trade: 'country' };

function slugify(s) {
  return String(s || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}
const SLUG_INDEX = {};
for (const [iso, name] of Object.entries(seo.countries)) {
  const k = slugify(name);
  if (k) SLUG_INDEX[k] = k in SLUG_INDEX ? null : iso;
}
const countrySlug = (iso) => {
  const k = slugify(seo.countries[iso]);
  return k && SLUG_INDEX[k] === iso ? k : iso.toLowerCase();
};
const countryFromSlug = (seg) => SLUG_INDEX[seg] || (/^(us-)?[a-z]{2,3}$/.test(seg) ? seg.toUpperCase() : null);

function readState(url) {
  const segs = url.pathname.split('/').filter(Boolean);
  let a = '', b = '';
  try { a = decodeURIComponent(segs[0] || '').toLowerCase(); b = decodeURIComponent(segs[1] || '').toLowerCase(); } catch (e) { /* bad escape: no path state */ }
  const ps = {};
  if (a && /^[a-z0-9-]+$/.test(a)) {
    ps.tab = SLUG_TAB[a] || a;
    if (b) {
      if (ps.tab === 'elnino') ps.tab = SLUG_ENSO[b] || 'elnino';
      else if (ps.tab === 'country') ps.country = countryFromSlug(b);
      else if (ps.tab === 'commodities' && /^[a-z0-9-]+$/.test(b)) ps.commodity = b;
    }
  }
  const q = url.searchParams;
  const country = (q.get('country') || ps.country || '').toUpperCase();
  return {
    tab: (q.get('tab') || '').toLowerCase() || ps.tab || null,
    country: /^(US-)?[A-Z]{2,3}$/.test(country) ? country : null,
    commodity: (q.get('commodity') || ps.commodity || '').toLowerCase() || null,
  };
}

function buildPath(st) {
  const tab = TAB_HOST_ALIAS[st.tab] || st.tab;
  const q = new URLSearchParams();
  let path = '/';
  if (tab && tab !== 'global') {
    path = ENSO_TABS.has(tab) ? '/elnino' + (tab !== 'elnino' ? '/' + ENSO_SLUG[tab] : '') : '/' + (TAB_SLUG[tab] || tab);
  }
  if (st.country) { if (tab === 'country') path += '/' + countrySlug(st.country); else q.set('country', st.country); }
  if (st.commodity) { if (tab === 'commodities' && !st.country) path += '/' + st.commodity; else q.set('commodity', st.commodity); }
  const qs = q.toString();
  return path + (qs ? '?' + qs : '');
}

function tabEntry(tab) {
  const t = seo.tabs[tab];
  return t && t.same_as ? seo.tabs[t.same_as] : t;
}

export function headFor(url) {
  const s = readState(url);
  if (s.tab && !seo.tabs[s.tab]) s.tab = null;           // unknown path: the home page
  const t = tabEntry(s.tab || 'global');
  let title = t.title, description = t.description;
  const image = seo.images[t.image || 'default'];
  const name = s.country && seo.countries[s.country];
  if (s.country && !name) s.country = null;
  if (name) {
    title = seo.country.title.replace('{name}', name);
    description = seo.country.description.replace('{name}', name);
  }
  if (s.commodity) {
    const key = s.commodity.replace(/[\s_-]+/g, '');
    const hit = Object.entries(seo.commodities).find(([dk, nm]) => dk === key || nm.toLowerCase().replace(/[\s_-]+/g, '') === key);
    if (!hit) s.commodity = null;
    else if (name) title = hit[1] + ' · ' + title;
    else {
      title = seo.commodity.title.replace('{name}', hit[1]);
      description = seo.commodity.description.replace('{name}', hit[1]);
    }
  }
  return { title, description, url: SITE + buildPath(s), image: { ...image, url: SITE + image.url } };
}

const esc = (v) => String(v).replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;').replace(/>/g, '&gt;');

export function rewriteHead(html, h) {
  const meta = (attr, key, val) => {
    const re = new RegExp(`(<meta ${attr}="${key.replace(/[:]/g, '\\:')}" content=")[^"]*(")`);
    html = html.replace(re, `$1${esc(val).replace(/\$/g, '$$$$')}$2`);
  };
  html = html.replace(/<title>[^<]*<\/title>/, `<title>${esc(h.title).replace(/\$/g, '$$$$')}</title>`);
  meta('name', 'description', h.description);
  meta('property', 'og:title', h.title);
  meta('property', 'og:description', h.description);
  meta('property', 'og:url', h.url);
  meta('property', 'og:image', h.image.url);
  meta('property', 'og:image:width', h.image.width);
  meta('property', 'og:image:height', h.image.height);
  meta('property', 'og:image:alt', h.image.alt);
  meta('name', 'twitter:title', h.title);
  meta('name', 'twitter:description', h.description);
  meta('name', 'twitter:image', h.image.url);
  meta('name', 'twitter:image:alt', h.image.alt);
  if (!/<link rel="canonical"/.test(html)) {
    html = html.replace(/(<meta name="description" content="[^"]*">)/, `$1\n<link rel="canonical" href="${esc(h.url).replace(/\$/g, '$$$$')}">`);
  }
  return html;
}

const MARK = '<script type="application/ld+json">';

export default async function middleware(request) {
  if (request.method !== 'GET' && request.method !== 'HEAD') return;
  const url = new URL(request.url);
  let up;
  try {
    const fwd = {};
    for (const k of ['cookie', 'x-vercel-protection-bypass']) { const v = request.headers.get(k); if (v) fwd[k] = v; }
    up = await fetch(new URL('/index.html', url), { headers: fwd });
  } catch (e) { return; }
  if (!up.ok || !up.body) return;
  const h = headFor(url);

  const dec = new TextDecoder(), enc = new TextEncoder();
  let buf = '', done = false;
  const body = up.body.pipeThrough(new TransformStream({
    transform(chunk, ctl) {
      const text = dec.decode(chunk, { stream: true });
      if (done) { if (text) ctl.enqueue(enc.encode(text)); return; }
      buf += text;
      const i = buf.indexOf(MARK);
      if (i >= 0 || buf.length > 262144) {
        const cut = i >= 0 ? i : buf.length;
        ctl.enqueue(enc.encode(rewriteHead(buf.slice(0, cut), h) + buf.slice(cut)));
        buf = ''; done = true;
      }
    },
    flush(ctl) {
      const tail = dec.decode();
      if (!done) ctl.enqueue(enc.encode(rewriteHead(buf + tail, h)));
      else if (tail) ctl.enqueue(enc.encode(tail));
    },
  }));

  const headers = new Headers(up.headers);
  for (const k of ['content-length', 'content-encoding', 'etag', 'age', 'x-vercel-cache', 'content-disposition']) headers.delete(k);
  headers.set('content-type', 'text/html; charset=utf-8');
  return new Response(request.method === 'HEAD' ? null : body, { status: 200, headers });
}

// Compare the editable replica (rendered exactly the way the app renders it: shadow root
// inside a size container) against the live page, and prove text is editable.
// Usage: node verify.mjs <harness.json> <outDir> [width=1440]
import { chromium } from 'playwright-core';
import fs from 'fs';

const [harnessPath, outDir, w] = process.argv.slice(2);
const width = Number(w || 1440);
const H = JSON.parse(fs.readFileSync(harnessPath, 'utf8'));
fs.mkdirSync(outDir, { recursive: true });
const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || '/usr/bin/chromium', args: ['--no-sandbox'] });

async function shoot(page, getScroller, prefix) {
  const total = await page.evaluate(getScroller);
  let i = 0;
  for (let y = 0; y < total && i < 14; y += 900) {
    await page.evaluate(async y => { window.__s.scrollTop = y; await new Promise(r => setTimeout(r, 600)); }, y);
    await page.screenshot({ path: `${outDir}/${prefix}-${String(i++).padStart(2, '0')}.png`, clip: { x: 0, y: 0, width, height: 900 } });
  }
  return total;
}

const findScroller = root => {
  const scope = root || document;
  const els = [...scope.querySelectorAll('*')].filter(e => e.scrollHeight > e.clientHeight + 50 && ['auto', 'scroll'].includes(getComputedStyle(e).overflowY));
  const s = els.sort((a, b) => b.scrollHeight - a.scrollHeight)[0];
  const docH = document.scrollingElement.scrollHeight;
  window.__s = s && s.scrollHeight >= docH ? s : document.scrollingElement;
  return window.__s.scrollHeight;
};

// Live
const live = await browser.newPage({ viewport: { width, height: 900 } });
await live.goto(H.url, { waitUntil: 'networkidle', timeout: 90000 });
await live.evaluate(() => document.querySelectorAll('[class*=cky-],#onetrust-consent-sdk,#CybotCookiebotDialog').forEach(e => { e.style.setProperty('display', 'none', 'important'); })).catch(() => {});
await live.waitForTimeout(2500);
const liveH = await shoot(live, `(${findScroller})()`, 'live');

// Replica
const rep = await browser.newPage({ viewport: { width, height: 900 } });
await rep.setContent(`<!DOCTYPE html><html><head><style>html,body{margin:0}</style></head><body><div id="host" style="width:${width}px;height:900px;container-type:size;container-name:tppage;overflow:hidden"></div></body></html>`);
await rep.evaluate(H => {
  const g = document.createElement('style'); g.textContent = H.globalCss; document.head.appendChild(g);
  const root = document.getElementById('host').attachShadow({ mode: 'open' });
  const attrs = Object.entries(H.meta.htmlAttrs).filter(([k]) => k !== 'class').map(([k, v]) => `${k}="${String(v).replace(/"/g, '&quot;')}"`).join(' ');
  root.innerHTML = `<style>:host{all:initial;display:block}.tp-html{height:100%;overflow-y:auto}.tp-body{height:100%}.tp-html.tp-doc-scroll .tp-body{height:auto;min-height:100%}</style><style>${H.css}</style><div class="tp-html${H.meta.documentScroll ? ' tp-doc-scroll' : ''} ${H.meta.htmlAttrs.class || ''}" ${attrs}><div class="tp-body">${H.body}</div></div>`;
  window.__root = root;
}, H);
await rep.waitForTimeout(3000);
const repH = await shoot(rep, `(${findScroller})(window.__root)`, 'replica');

// Editability: click the first heading's copy span and type.
await rep.evaluate(() => { window.__s.scrollTop = 0; });
const target = await rep.evaluate(() => {
  const el = window.__root.querySelector('h1 [data-copy]') || window.__root.querySelector('[data-copy]');
  el.setAttribute('contenteditable', 'plaintext-only');
  const r = el.getBoundingClientRect();
  return { key: el.getAttribute('data-copy'), x: r.x + Math.min(20, r.width / 2), y: r.y + r.height / 2, before: el.textContent };
});
await rep.mouse.click(target.x, target.y);
await rep.keyboard.type('QA');
const after = await rep.evaluate(k => window.__root.querySelector(`[data-copy="${k}"]`).textContent, target.key);

console.log(JSON.stringify({ width, liveHeight: liveH, replicaHeight: repH, heightDelta: repH - liveH, editable: after !== target.before, editSample: after.slice(0, 60) }));
await browser.close();

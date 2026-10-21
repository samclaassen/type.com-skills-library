// Capture the fully rendered (post-JavaScript) DOM of a landing page.
// Usage: node capture.mjs <url> <out.json> [waitMs]
// Requires: playwright-core (bun add playwright-core) and /usr/bin/chromium.
import { chromium } from 'playwright-core';
import fs from 'fs';

const [url, out, waitArg] = process.argv.slice(2);
if (!url || !out) { console.error('usage: node capture.mjs <url> <out.json> [waitMs]'); process.exit(1); }
const wait = Number(waitArg || 7000);

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM || '/usr/bin/chromium', args: ['--no-sandbox'] });
const page = await browser.newPage({ viewport: { width: 1440, height: 900 } });
await page.goto(url, { waitUntil: 'networkidle', timeout: 90000 });

// Scroll the real scroll container top-to-bottom so lazy / in-view content renders.
const scrollInfo = await page.evaluate(async () => {
  const candidates = [...document.querySelectorAll('body *')].filter(e =>
    e.scrollHeight > e.clientHeight + 50 && ['auto', 'scroll'].includes(getComputedStyle(e).overflowY));
  const inner = candidates.sort((a, b) => b.scrollHeight - a.scrollHeight)[0];
  const docH = document.scrollingElement.scrollHeight;
  const useInner = inner && inner.scrollHeight > docH;
  const s = useInner ? inner : document.scrollingElement;
  for (let y = 0; y < s.scrollHeight; y += 400) { s.scrollTop = y; await new Promise(r => setTimeout(r, 250)); }
  s.scrollTop = 0;
  return { documentScroll: !useInner, height: s.scrollHeight };
});
await page.waitForTimeout(wait);
await page.screenshot({ path: out.replace(/\.json$/, '') + '-live.png' });

const cap = await page.evaluate(() => {
  const b = document.body.cloneNode(true);
  // Drop scripts, embeds and common cookie / chat widgets.
  b.querySelectorAll([
    'script', 'noscript', 'iframe', 'template',
    '[class*=cky-]', '[id^=cky]', '#onetrust-consent-sdk', '#CybotCookiebotDialog',
    '#plain-container', '#intercom-container', '.intercom-lightweight-app', '#hubspot-messages-iframe-container',
  ].join(',')).forEach(e => e.remove());
  return {
    url: location.href,
    title: document.title,
    html: [...document.documentElement.attributes].map(a => [a.name, a.value]),
    bodyAttrs: [...document.body.attributes].map(a => [a.name, a.value]),
    styles: [...document.querySelectorAll('style')].map(s => s.textContent || ''),
    links: [...document.querySelectorAll('link[rel=stylesheet]')].map(l => l.href),
    body: b.innerHTML,
  };
});
cap.documentScroll = scrollInfo.documentScroll;
cap.liveHeight = scrollInfo.height;
cap.capturedAt = new Date().toISOString();
fs.writeFileSync(out, JSON.stringify(cap));
console.log(JSON.stringify({ url: cap.url, bodyChars: cap.body.length, stylesheets: cap.links.length, inlineStyles: cap.styles.length, ...scrollInfo }));
await browser.close();

"""Turn a capture.json into editable-replica assets for the Type app template.

Usage: python3 build_page.py <capture.json> <app_dir> <slug> [label]
Writes <app_dir>/src/page/{body-N.html.txt,page-N.css.txt,global-N.css.txt,assets.ts,meta.ts}
and <capture_dir>/harness.json for verify.mjs.
Requires: beautifulsoup4.
"""
import base64, datetime, json, os, re, sys, urllib.request
from urllib.parse import urljoin, urlparse
from bs4 import BeautifulSoup, Comment, NavigableString

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from shadowcss import split_top, transform  # noqa: E402

UA = {'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 Chrome/126 Safari/537.36'}
CHUNK = 320_000          # stay well under Type's 500KB per-source-file limit
INLINE_MAX = 300_000     # max raw bytes for an inlined asset (base64 grows ~1.34x; must stay < 500KB/file)


def fetch(url, origin_header=None):
    headers = dict(UA)
    if origin_header:
        headers['Origin'] = origin_header
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read(), dict(r.headers)


def main():
    cap_path, app_dir, slug = sys.argv[1:4]
    cap = json.load(open(cap_path))
    page_url = cap['url']
    label = sys.argv[4] if len(sys.argv) > 4 else (urlparse(page_url).netloc + urlparse(page_url).path).rstrip('/')

    soup = BeautifulSoup('<body>' + cap['body'] + '</body>', 'html.parser')
    body = soup.find('body')
    for c in body.find_all(string=lambda s: isinstance(s, Comment)):
        c.extract()

    # --- type.com-style animated rotating words: collapse to one static word -----------
    for role in body.select('[data-marketing-rotating-role]'):
        word = role.get('data-role', '')
        for m in role.select('[data-marketing-role-measure]'):
            m.decompose()
        for el in role.find_all(True):
            if el.has_attr('style') and 'width' in el['style']:
                del el['style']
        chars = role.select('[data-marketing-role-character]')
        if chars and word:
            holder = chars[0].parent
            holder.clear()
            sp = soup.new_tag('span')
            sp['class'] = 'block flex-none whitespace-pre'
            sp.string = word
            holder.append(sp)

    # --- absolutize every URL so assets load from the live site ------------------------
    def absu(v):
        return urljoin(page_url, v) if isinstance(v, str) and v and not v.startswith(('data:', '#', 'javascript:', 'mailto:', 'tel:')) else v

    for el in body.find_all(True):
        for attr in ('src', 'href', 'poster', 'data-src', 'xlink:href'):
            if el.has_attr(attr):
                el[attr] = absu(el[attr])
        if el.has_attr('srcset'):
            el['srcset'] = ', '.join(
                ' '.join([absu(p.strip().split(' ')[0])] + p.strip().split(' ')[1:])
                for p in el['srcset'].split(',') if p.strip())
        if el.has_attr('style') and 'url(' in el['style']:
            el['style'] = re.sub(r'url\((["\']?)([^)"\']+)\1\)', lambda m: 'url(' + m.group(1) + absu(m.group(2)) + m.group(1) + ')', el['style'])
        if el.name == 'video':
            el['autoplay'] = ''
            el['muted'] = ''
            el['loop'] = ''
            el['playsinline'] = ''

    # --- wrap every visible text node in an editable span --------------------------------
    SKIP = {'svg', 'script', 'style', 'title', 'textarea', 'option', 'code', 'pre'}
    n = 0
    for s in list(body.find_all(string=True)):
        if isinstance(s, Comment) or not s.strip():
            continue
        if any(p.name in SKIP for p in s.parents if p is not None and p.name):
            continue
        n += 1
        text = str(s)
        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()):]
        span = soup.new_tag('span')
        span['data-copy'] = f'c{n:04d}'
        span.string = text.strip()
        frag = ([NavigableString(lead)] if lead else []) + [span] + ([NavigableString(trail)] if trail else [])
        s.replace_with(*frag)
    body_html = re.sub(r'\n\s*\n+', '\n', body.decode_contents())

    # --- collect CSS: linked sheets (urls resolved per sheet) + inline <style> ----------
    css_parts = []
    for href in cap['links']:
        try:
            data, _ = fetch(href)
        except Exception as e:  # noqa: BLE001
            print('WARN stylesheet failed', href, e)
            continue
        text = data.decode('utf-8', 'replace')
        text = re.sub(r'url\((["\']?)(?!data:|#)([^)"\']+)\1\)', lambda m, h=href: 'url(' + m.group(1) + urljoin(h, m.group(2).strip()) + m.group(1) + ')', text)
        css_parts.append(text)
    for st in cap['styles']:
        css_parts.append(re.sub(r'url\((["\']?)(?!data:|#)([^)"\']+)\1\)', lambda m: 'url(' + m.group(1) + urljoin(page_url, m.group(2).strip()) + m.group(1) + ')', st))
    css = '\n'.join(css_parts)

    # Drop KaTeX font faces (never used by landing pages, large).
    css = re.sub(r'@font-face\{[^}]*KaTeX[^}]*\}', '', css)

    # Inline assets that can't be loaded cross-origin (masks / fonts need CORS).
    origin_probe = 'https://example.com'
    inlined, warned = 0, []
    for u in sorted(set(re.findall(r'url\(["\']?(https?://[^)"\']+)["\']?\)', css))):
        path = urlparse(u).path.lower()
        is_svg = path.endswith('.svg')
        is_font = path.endswith(('.woff2', '.woff', '.ttf', '.otf'))
        if not (is_svg or is_font):
            continue
        try:
            data, headers = fetch(u, origin_probe)
        except Exception as e:  # noqa: BLE001
            warned.append(f'{u} ({e})')
            continue
        cors_ok = any(k.lower() == 'access-control-allow-origin' for k in headers)
        if cors_ok and not is_svg:
            continue
        if len(data) > INLINE_MAX:
            warned.append(f'{u} has no CORS and is too large to inline ({len(data)} bytes)')
            continue
        mime = 'image/svg+xml' if is_svg else {'woff2': 'font/woff2', 'woff': 'font/woff', 'ttf': 'font/ttf', 'otf': 'font/otf'}[path.rsplit('.', 1)[1]]
        uri = f'data:{mime};base64,' + base64.b64encode(data).decode()
        css = re.sub(r'url\((["\']?)' + re.escape(u) + r'\1\)', 'url("' + uri + '")', css)
        inlined += 1

    # --- shadow-root transform: media queries -> container queries, vw/vh -> cqw/cqh ----
    globals_out = []
    shadow_css = transform(css, globals_out)

    # --- write app assets ----------------------------------------------------------------
    page_dir = os.path.join(app_dir, 'src', 'page')
    os.makedirs(page_dir, exist_ok=True)
    for f in os.listdir(page_dir):
        if f.startswith('page-') and f.endswith('.css.txt'):
            os.remove(os.path.join(page_dir, f))
    def pieces(css_text):
        """Top-level pieces; oversized grouping at-rules are split into several copies."""
        for p, b in split_top(css_text):
            piece = p if b is None else p + '{' + b + '}'
            head = p.strip()
            if b is not None and len(piece) > CHUNK and head.startswith(('@layer', '@media', '@supports', '@container')):
                for inner in pieces(b):
                    yield p + '{' + inner + '}'
            else:
                yield piece
    chunks, cur = [], ''
    for piece in pieces(shadow_css):
        if len(cur) + len(piece) > CHUNK and cur:
            chunks.append(cur)
            cur = ''
        cur += piece
    if cur:
        chunks.append(cur)
    for i, c in enumerate(chunks, 1):
        if len(c.encode()) > 490_000:
            print(f'WARN css chunk {i} is {len(c.encode())} bytes (limit 500KB) - a single rule block is too large')
        open(os.path.join(page_dir, f'page-{i}.css.txt'), 'w').write(c)
    for f in os.listdir(page_dir):
        if f.startswith('body') and f.endswith('.html.txt'):
            os.remove(os.path.join(page_dir, f))
    body_parts = [body_html[i:i + CHUNK] for i in range(0, len(body_html), CHUNK)] or ['']
    for i, part in enumerate(body_parts, 1):
        open(os.path.join(page_dir, f'body-{i}.html.txt'), 'w').write(part)
    for f in os.listdir(page_dir):
        if f.startswith('global') and f.endswith('.css.txt'):
            os.remove(os.path.join(page_dir, f))
    global_chunks, cur = [], ''
    for rule in globals_out:
        if len(cur) + len(rule) > CHUNK and cur:
            global_chunks.append(cur)
            cur = ''
        cur += rule + '\n'
    global_chunks.append(cur)
    for i, c in enumerate(global_chunks, 1):
        open(os.path.join(page_dir, f'global-{i}.css.txt'), 'w').write(c)
    global_css = ''.join(global_chunks)

    html_attrs = {k: v for k, v in cap['html'] if k not in ('lang',)}
    captured = datetime.datetime.fromisoformat(cap['capturedAt'].replace('Z', '+00:00')).strftime('%b %-d, %Y')
    meta = {'slug': slug, 'label': label, 'url': page_url, 'captured': captured,
            'documentScroll': bool(cap.get('documentScroll')), 'htmlAttrs': html_attrs}
    raw = lambda f: f'import("./{f}?raw").then((m) => m.default)'
    body_imports = ', '.join(raw(f'body-{i}.html.txt') for i in range(1, len(body_parts) + 1))
    global_imports = ', '.join(raw(f'global-{i}.css.txt') for i in range(1, len(global_chunks) + 1))
    css_imports = ', '.join(raw(f'page-{i}.css.txt') for i in range(1, len(chunks) + 1))
    assets = '\n'.join([
        '// Generated by build_page.py - do not edit by hand.',
        '// Each file is a separate lazy chunk so large pages stay under bundle limits.',
        'export type PageAssets = { bodyHtml: string; globalCss: string; pageCss: string[] };',
        'export async function loadPageAssets(): Promise<PageAssets> {',
        '  const [body, globalCss, pageCss] = await Promise.all([',
        f'    Promise.all([{body_imports}]).then((parts) => parts.join("")),',
        f'    Promise.all([{global_imports}]).then((parts) => parts.join("\\n")),',
        f'    Promise.all([{css_imports}]),',
        '  ]);',
        '  return { bodyHtml: body, globalCss, pageCss };',
        '}',
        '',
    ])
    meta_ts = '\n'.join([
        '// Generated by build_page.py - do not edit by hand.',
        'export const pageMeta: {',
        '  slug: string; label: string; url: string; captured: string;',
        '  documentScroll: boolean; htmlAttrs: Record<string, string>;',
        f'}} = {json.dumps(meta, indent=2)};',
        '',
    ])
    open(os.path.join(page_dir, 'meta.ts'), 'w').write(meta_ts)
    open(os.path.join(page_dir, 'assets.ts'), 'w').write(assets)

    too_big = [f for f in os.listdir(page_dir) if os.path.getsize(os.path.join(page_dir, f)) > 490_000]
    if too_big:
        print('ERROR files over the 500KB source limit:', too_big)
    total = sum(os.path.getsize(os.path.join(page_dir, f)) for f in os.listdir(page_dir))
    if total > 7_000_000:
        print(f'WARN page assets total {total} bytes; Type apps allow ~8MB of source in total')

    # Harness input for verify.mjs
    harness = {'url': page_url, 'meta': meta, 'css': shadow_css, 'globalCss': global_css, 'body': body_html}
    json.dump(harness, open(os.path.join(os.path.dirname(os.path.abspath(cap_path)), 'harness.json'), 'w'))

    print(json.dumps({'copyBlocks': n, 'bodyBytes': len(body_html.encode()), 'cssChunks': [len(c) for c in chunks],
                      'globalRules': len(globals_out), 'inlinedAssets': inlined, 'warnings': warned[:10]}))


if __name__ == '__main__':
    main()

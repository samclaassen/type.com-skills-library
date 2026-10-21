"""Rewrite captured page CSS so it works inside a shadow root sized like a viewport."""
import re

def split_top(css):
    """Yield (prelude, body) for top-level blocks plus bare statements."""
    out = []
    i = 0; n = len(css)
    while i < n:
        # find next '{' or ';' at depth 0 respecting strings
        j = i; q = None
        while j < n:
            c = css[j]
            if c == '\\': j += 2; continue
            if q:
                if c == q: q = None
            elif c in '"\'': q = c
            elif c in '{;': break
            j += 1
        if j >= n:
            if css[i:].strip(): out.append((css[i:], None))
            break
        if css[j] == ';':
            out.append((css[i:j + 1], None)); i = j + 1; continue
        # match braces
        depth = 0; k = j; q = None
        while k < n:
            c = css[k]
            if c == '\\': k += 2; continue
            if q:
                if c == q: q = None
            elif c in '"\'': q = c
            elif c == '{': depth += 1
            elif c == '}':
                depth -= 1
                if depth == 0: break
            k += 1
        out.append((css[i:j], css[j + 1:k]))
        i = k + 1
    return out

WIDTH_ONLY = re.compile(r'^\s*(\(\s*(min-width|max-width|width)\s*[:<>=][^()]*\)\s*(and\s*)?)+$')

def rewrite_selector(sel):
    sel = re.sub(r':root\b', '.tp-html', sel)
    sel = re.sub(r'(^|[\s,>+~(])html\b', r'\1.tp-html', sel)
    sel = re.sub(r'(^|[\s,>+~(])body\b', r'\1.tp-body', sel)
    return sel

def rewrite_units(decls):
    return re.sub(r'(?<![\w-])(-?\d*\.?\d+)(?:d|s|l)?v([wh])\b', r'\1cq\2', decls)

def transform(css, globals_out):
    parts = []
    for prelude, body in split_top(css):
        p = prelude.strip()
        if body is None:
            parts.append(prelude)
            continue
        if p.startswith('@font-face') or p.startswith('@property'):
            globals_out.append(prelude + '{' + body + '}')
            continue
        if p.startswith('@media'):
            q = p[len('@media'):].strip()
            mnot = re.match(r'^not all and (\(\s*(min-width|max-width|width)[^()]*\))$', q)
            if mnot:
                parts.append('@container tppage not ' + mnot.group(1) + '{' + transform(body, globals_out) + '}')
            elif WIDTH_ONLY.match(q):
                parts.append('@container tppage ' + q + '{' + transform(body, globals_out) + '}')
            else:
                parts.append(prelude + '{' + transform(body, globals_out) + '}')
            continue
        if p.startswith('@keyframes') or p.startswith('@-webkit-keyframes'):
            parts.append(prelude + '{' + body + '}')
            continue
        if p.startswith('@'):
            # @supports, @layer, @container, @starting-style ... contain nested rules
            parts.append(prelude + '{' + transform(body, globals_out) + '}')
            continue
        # style rule; may contain nested rules (css nesting) -> handle simply
        if '{' in body:
            parts.append(rewrite_selector(prelude) + '{' + transform(body, globals_out) + '}')
        else:
            parts.append(rewrite_selector(prelude) + '{' + rewrite_units(body) + '}')
    return ''.join(parts)

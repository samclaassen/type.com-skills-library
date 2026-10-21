"""Extract GEO-relevant signals from HTML using only the standard library.

Replaces the BeautifulSoup/lxml parsing in upstream fetch_page.py and
citability_scorer.py with html.parser so the skill has no third-party
dependencies. It collects: title, meta tags, canonical, html lang, headings,
JSON-LD blocks, microdata/RDFa markers, links, images, visible word count,
JS-app-root emptiness (client-side rendering hint) and heading-delimited
content blocks for citability scoring.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from urllib.parse import urljoin, urlparse

VOID = {
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta",
    "param", "source", "track", "wbr",
}
# Content inside these is not visible prose (upstream decomposed the same set).
NON_CONTENT = {"script", "style", "noscript", "template", "svg", "nav", "footer", "header", "aside", "form"}
# Never visible text; links inside these are ignored too.
HIDDEN = {"script", "style", "noscript", "template", "svg"}
HEADINGS = {"h1", "h2", "h3", "h4", "h5", "h6"}
BLOCK_CAPTURE = {"p", "ul", "ol", "table", "blockquote", "dl"}
APP_ROOT_ID = re.compile(r"^(app|root|__next|__nuxt|svelte|gatsby.*)$", re.I)


@dataclass
class PageData:
    url: str
    title: str | None = None
    lang: str | None = None
    meta: dict[str, str] = field(default_factory=dict)
    canonical: str | None = None
    headings: list[tuple[int, str]] = field(default_factory=list)
    json_ld_raw: list[str] = field(default_factory=list)
    has_microdata: bool = False
    has_rdfa: bool = False
    links: list[tuple[str, str]] = field(default_factory=list)
    images: list[dict[str, str | None]] = field(default_factory=list)
    word_count: int = 0
    app_roots: list[tuple[str, int]] = field(default_factory=list)
    blocks: list[dict[str, str]] = field(default_factory=list)

    @property
    def h1s(self) -> list[str]:
        return [text for level, text in self.headings if level == 1]

    def internal_links(self) -> list[tuple[str, str]]:
        host = urlparse(self.url).netloc.lower()
        seen: set[str] = set()
        out: list[tuple[str, str]] = []
        for href, text in self.links:
            absolute = urljoin(self.url, href).split("#")[0]
            parsed = urlparse(absolute)
            if parsed.scheme not in ("http", "https") or parsed.netloc.lower() != host:
                continue
            if absolute in seen:
                continue
            seen.add(absolute)
            out.append((absolute, text))
        return out

    def json_ld(self) -> tuple[list[dict], int]:
        """Return flattened JSON-LD entities and the count of invalid blocks."""
        entities: list[dict] = []
        invalid = 0
        for raw in self.json_ld_raw:
            try:
                data = json.loads(raw)
            except (json.JSONDecodeError, TypeError):
                invalid += 1
                continue
            for node in data if isinstance(data, list) else [data]:
                if isinstance(node, dict) and isinstance(node.get("@graph"), list):
                    entities.extend(n for n in node["@graph"] if isinstance(n, dict))
                elif isinstance(node, dict):
                    entities.append(node)
        return entities, invalid


def _clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


class _Extractor(HTMLParser):
    def __init__(self, page: PageData):
        super().__init__(convert_charrefs=True)
        self.page = page
        self.stack: list[str] = []
        self.skip_depth = 0  # >0 while inside a NON_CONTENT element
        self.hidden_depth = 0  # >0 while inside a HIDDEN element
        self.in_title = False
        self.title_buf: list[str] = []
        self.in_jsonld = False
        self.jsonld_buf: list[str] = []
        self.heading_level: int | None = None
        self.heading_in_chrome = False
        self.heading_buf: list[str] = []
        self.link_href: str | None = None
        self.link_buf: list[str] = []
        self.capture_tag: str | None = None
        self.capture_depth = 0
        self.capture_buf: list[str] = []
        self.visible: list[str] = []
        self.current_heading = "Introduction"
        self.section_parts: list[str] = []
        self.root_trackers: list[list] = []  # [id, depth, chars]

    # -- helpers -------------------------------------------------------
    def _flush_section(self) -> None:
        if self.section_parts:
            self.page.blocks.append(
                {"heading": self.current_heading, "content": " ".join(self.section_parts)}
            )
        self.section_parts = []

    # -- parser callbacks ---------------------------------------------
    def handle_starttag(self, tag, attrs):
        attr = {k.lower(): (v or "") for k, v in attrs}
        if "itemscope" in attr or "itemtype" in attr:
            self.page.has_microdata = True
        if "typeof" in attr or "vocab" in attr:
            self.page.has_rdfa = True

        if tag == "html" and attr.get("lang"):
            self.page.lang = attr["lang"]
        elif tag == "meta":
            name = (attr.get("name") or attr.get("property") or attr.get("http-equiv") or "").lower()
            if name and "content" in attr:
                self.page.meta[name] = attr["content"]
        elif tag == "link" and "canonical" in attr.get("rel", "").lower().split():
            self.page.canonical = attr.get("href")
        elif tag == "img":
            self.page.images.append({"src": attr.get("src"), "alt": attr.get("alt") if "alt" in attr else None})

        if tag in VOID:
            return

        self.stack.append(tag)
        if tag == "script" and attr.get("type", "").lower() == "application/ld+json":
            self.in_jsonld = True
            self.jsonld_buf = []
        if tag == "title" and not self.skip_depth:
            self.in_title = True
        if tag in NON_CONTENT:
            self.skip_depth += 1
        if tag in HIDDEN:
            self.hidden_depth += 1
        if attr.get("id") and APP_ROOT_ID.match(attr["id"]):
            self.root_trackers.append([attr["id"], len(self.stack), 0])
        if tag == "a" and not self.hidden_depth:
            # Navigation and footer links are kept: they drive page sampling and llms.txt drafts.
            self.link_href = attr.get("href")
            self.link_buf = []
        if tag in HEADINGS and not self.hidden_depth:
            # Headings count even inside <header>/<nav> (site H1s often live there).
            self.heading_level = int(tag[1])
            self.heading_in_chrome = bool(self.skip_depth)
            self.heading_buf = []
        if self.skip_depth:
            return
        if tag in BLOCK_CAPTURE and self.capture_tag is None and self.heading_level is None:
            self.capture_tag = tag
            self.capture_depth = len(self.stack)
            self.capture_buf = []
        elif tag in ("li", "td", "th", "br", "tr", "dt", "dd") and self.capture_tag:
            self.capture_buf.append(" ")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in VOID:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in VOID or tag not in self.stack:
            return
        while self.stack:
            open_tag = self.stack.pop()
            self._close(open_tag)
            if open_tag == tag:
                break

    def _close(self, tag: str) -> None:
        depth_after = len(self.stack)
        for tracker in list(self.root_trackers):
            if tracker[1] == depth_after + 1:
                self.page.app_roots.append((tracker[0], tracker[2]))
                self.root_trackers.remove(tracker)
        if tag == "script" and self.in_jsonld:
            self.page.json_ld_raw.append("".join(self.jsonld_buf))
            self.in_jsonld = False
        if tag == "title" and self.in_title:
            self.page.title = _clean("".join(self.title_buf)) or None
            self.in_title = False
        if tag == "a" and self.link_href is not None:
            self.page.links.append((self.link_href, _clean("".join(self.link_buf))))
            self.link_href = None
        if tag in HIDDEN:
            self.hidden_depth = max(0, self.hidden_depth - 1)
        if tag in HEADINGS and self.heading_level is not None:
            text = _clean("".join(self.heading_buf))
            self.page.headings.append((self.heading_level, text))
            if self.heading_level <= 4 and not self.heading_in_chrome:
                self._flush_section()
                self.current_heading = text
            self.heading_level = None
        if tag in NON_CONTENT:
            self.skip_depth = max(0, self.skip_depth - 1)
            return
        if self.skip_depth:
            return
        if self.capture_tag and tag == self.capture_tag and depth_after + 1 == self.capture_depth:
            text = _clean("".join(self.capture_buf))
            if len(text.split()) >= 5:
                self.section_parts.append(text)
            self.capture_tag = None

    def handle_data(self, data):
        if self.in_jsonld:
            self.jsonld_buf.append(data)
            return
        if self.in_title:
            self.title_buf.append(data)
            return
        if self.link_href is not None and not self.hidden_depth:
            self.link_buf.append(data)
        if self.heading_level is not None and not self.hidden_depth:
            self.heading_buf.append(data)
        if self.skip_depth:
            return
        for tracker in self.root_trackers:
            tracker[2] += len(data.strip())
        self.visible.append(data)
        if self.capture_tag:
            self.capture_buf.append(data)

    def finish(self) -> None:
        self.close()
        while self.stack:
            self._close(self.stack.pop())
        self._flush_section()
        self.page.word_count = len(" ".join(self.visible).split())


def extract(html: str, url: str) -> PageData:
    page = PageData(url=url)
    parser = _Extractor(page)
    parser.feed(html)
    parser.finish()
    return page

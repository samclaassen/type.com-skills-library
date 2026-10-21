"""Page fetching for the GEO audit.

Two fetchers share one interface, ``get(url) -> FetchResult``:

* ``LiveFetcher`` uses urllib (stdlib) with a timeout and a response-size cap.
* ``FixtureFetcher`` serves saved files from disk so audits can run offline
  and deterministically (used by examples/run.sh and --html-file mode).

Adapted from fetch_page.py in zubair-trabzada/geo-seo-claude (MIT).
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; GEO-AI-Search-Audit/1.0; agency site review)"
)
MAX_BYTES = 5 * 1024 * 1024


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int | None
    text: str = ""
    headers: dict[str, str] = field(default_factory=dict)
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status == 200 and self.error is None


def _is_http(url: str) -> bool:
    return urlparse(url).scheme in ("http", "https")


def _decode(raw: bytes, content_type: str) -> str:
    match = re.search(r"charset=([\w-]+)", content_type or "", re.I)
    encoding = match.group(1) if match else "utf-8"
    try:
        return raw.decode(encoding, errors="replace")
    except LookupError:
        return raw.decode("utf-8", errors="replace")


class LiveFetcher:
    """Fetch over the network. Only GET requests to http(s) URLs."""

    mode = "live"

    def __init__(self, timeout: int = 20, user_agent: str = DEFAULT_USER_AGENT):
        self.timeout = timeout
        self.user_agent = user_agent
        self._cache: dict[str, FetchResult] = {}

    def get(self, url: str) -> FetchResult:
        if url in self._cache:
            return self._cache[url]
        result = self._get(url)
        self._cache[url] = result
        return result

    def _get(self, url: str) -> FetchResult:
        if not _is_http(url):
            return FetchResult(url, url, None, error="Only http and https URLs are supported")
        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                raw = response.read(MAX_BYTES + 1)[:MAX_BYTES]
                headers = {k.lower(): v for k, v in response.headers.items()}
                return FetchResult(
                    url,
                    response.geturl(),
                    response.status,
                    _decode(raw, headers.get("content-type", "")),
                    headers,
                )
        except urllib.error.HTTPError as exc:
            headers = {k.lower(): v for k, v in (exc.headers or {}).items()}
            return FetchResult(url, url, exc.code, "", headers)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            reason = getattr(exc, "reason", exc)
            return FetchResult(url, url, None, error=f"Network error: {reason}")


class FixtureFetcher:
    """Serve saved files instead of the network.

    ``root/<host>/<path>`` is looked up for each URL: ``/`` maps to
    ``index.html``; ``/about`` maps to ``about.html`` or ``about/index.html``;
    ``/robots.txt`` and ``/llms.txt`` map to files of the same name. A missing
    file is reported as HTTP 404. ``overrides`` maps exact URLs to files and
    wins over ``root`` (used by --html-file / --robots-file / --llms-file).
    """

    mode = "offline"

    def __init__(self, root: Path | None = None, overrides: dict[str, Path] | None = None,
                 not_checked: set[str] | None = None):
        self.root = root
        self.overrides = overrides or {}
        self.not_checked = not_checked or set()

    def _candidates(self, url: str) -> list[Path]:
        if self.root is None:
            return []
        parsed = urlparse(url)
        base = self.root / parsed.netloc.lower()
        path = parsed.path.strip("/")
        if not path:
            return [base / "index.html"]
        return [base / path, base / f"{path}.html", base / path / "index.html"]

    def get(self, url: str) -> FetchResult:
        if not _is_http(url):
            return FetchResult(url, url, None, error="Only http and https URLs are supported")
        if url in self.overrides:
            file = self.overrides[url]
            return FetchResult(url, url, 200, file.read_text(encoding="utf-8"),
                               {"content-type": "text/html; charset=utf-8"})
        suffix = "/" + urlparse(url).path.strip("/")
        if suffix in self.not_checked:
            return FetchResult(url, url, None, error="not checked (offline: no file supplied)")
        for candidate in self._candidates(url):
            if candidate.is_file():
                return FetchResult(url, url, 200, candidate.read_text(encoding="utf-8"),
                                   {"content-type": "text/html; charset=utf-8"})
        return FetchResult(url, url, 404, "")

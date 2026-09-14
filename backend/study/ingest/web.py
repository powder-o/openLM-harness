"""Fetch a URL and save it as original.html for docling to parse (SPEC §6 step 1, §13)."""
from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlparse

import httpx

MAX_BYTES = 15 * 1024 * 1024
_TIMEOUT = 30.0


class FetchError(RuntimeError):
    pass


def _extract_title(html: bytes) -> str | None:
    m = re.search(rb"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    if not m:
        return None
    raw = m.group(1).decode("utf-8", errors="replace")
    title = re.sub(r"\s+", " ", raw).strip()
    return title or None


def fetch_and_save(url: str, dest_dir: Path) -> tuple[Path, str]:
    """Download `url` (http/https only, ~15MB cap) and save it as dest_dir/original.html.

    Returns (path, title) where title comes from <title> or falls back to the URL.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise FetchError(f"unsupported URL scheme: {parsed.scheme!r}")

    data = bytearray()
    with httpx.Client(follow_redirects=True, timeout=_TIMEOUT) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            for part in resp.iter_bytes():
                data.extend(part)
                if len(data) > MAX_BYTES:
                    raise FetchError("response exceeded the 15 MB limit")

    html = bytes(data)
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "original.html"
    dest.write_bytes(html)
    title = _extract_title(html) or url
    return dest, title

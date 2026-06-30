"""
scraper.py

Fetches and extracts readable text content from an API documentation URL.
Optionally follows a small number of same-domain links that look relevant
(endpoints, auth, quickstart, reference) so multi-page docs sites still
yield enough context for the extractor step.
"""

import time
from typing import List, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; APIForge/1.0; +https://github.com/)"
}

# Keywords that suggest a linked page is relevant to API reference docs.
RELEVANT_LINK_KEYWORDS = [
    "endpoint", "api", "reference", "auth", "authentication",
    "quickstart", "getting-started", "docs", "guide",
]

NOISE_TAGS = ["script", "style", "nav", "footer", "header", "noscript", "svg"]


def _clean_text(soup: BeautifulSoup) -> str:
    """Strip non-content tags and collapse whitespace into readable lines."""
    for tag in soup(NOISE_TAGS):
        tag.decompose()
    text = soup.get_text(separator="\n")
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    return "\n".join(lines)


def _fetch(url: str, timeout: int = 10) -> Optional[BeautifulSoup]:
    try:
        resp = requests.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
        resp.raise_for_status()
    except requests.RequestException as exc:
        print(f"[scraper] Failed to fetch {url}: {exc}")
        return None
    return BeautifulSoup(resp.text, "html.parser")


def _find_relevant_links(soup: BeautifulSoup, base_url: str, limit: int = 4) -> List[str]:
    """Find same-domain links whose href or anchor text hints at API docs content."""
    domain = urlparse(base_url).netloc
    found = []
    seen = set()
    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = (a.get_text() or "").lower()
        full_url = urljoin(base_url, href)
        parsed = urlparse(full_url)

        if parsed.netloc != domain:
            continue
        if full_url in seen or full_url == base_url:
            continue
        if any(kw in href.lower() or kw in text for kw in RELEVANT_LINK_KEYWORDS):
            found.append(full_url)
            seen.add(full_url)
        if len(found) >= limit:
            break
    return found


def scrape_docs(start_url: str, max_pages: int = 4, follow_links: bool = True) -> dict:
    """
    Scrape an API documentation site starting from `start_url`.

    Args:
        start_url: the documentation page to start from.
        max_pages: hard cap on how many pages to fetch (keeps things fast
            and avoids accidentally crawling an entire site).
        follow_links: whether to follow relevant same-domain links at all.

    Returns:
        {
            "pages": [{"url": ..., "text": ...}, ...],
            "combined_text": "..."
        }
    """
    pages = []
    visited = set()
    queue = [start_url]

    while queue and len(pages) < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)

        soup = _fetch(url)
        if soup is None:
            continue

        text = _clean_text(soup)
        if text:
            pages.append({"url": url, "text": text})

        if follow_links and len(pages) < max_pages:
            for link in _find_relevant_links(soup, url, limit=max_pages - len(pages)):
                if link not in visited:
                    queue.append(link)

        time.sleep(0.3)  # be polite to the server

    combined_text = "\n\n---\n\n".join(p["text"] for p in pages)
    return {"pages": pages, "combined_text": combined_text}


if __name__ == "__main__":
    import sys

    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://jsonplaceholder.typicode.com/guide/"
    result = scrape_docs(test_url, max_pages=3)
    print(f"Scraped {len(result['pages'])} page(s) from {test_url}")
    print(result["combined_text"][:1000])

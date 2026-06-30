"""
Unit tests for scraper/doc_scraper.py.

These tests do NOT hit the network. They exercise the text-cleaning and
link-discovery helpers directly against small, hand-built HTML snippets,
so they run instantly and reliably (e.g. in CI, or on a grader's machine
with no internet access).

Run with:
    python tests/test_scraper.py
or, if you have pytest installed:
    pytest tests/
"""

import sys
from pathlib import Path

# Make the `scraper` package importable when running this file directly
# from anywhere (e.g. `python tests/test_scraper.py` from repo root).
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from bs4 import BeautifulSoup

from scraper.doc_scraper import _clean_text, _find_relevant_links


def test_clean_text_strips_noise_tags():
    html = """
    <html>
      <head><script>var x = 1;</script></head>
      <body>
        <nav>Site nav</nav>
        <main>
          <h1>API Reference</h1>
          <p>GET /users returns a list of users.</p>
        </main>
        <footer>Copyright 2026</footer>
      </body>
    </html>
    """
    soup = BeautifulSoup(html, "html.parser")
    text = _clean_text(soup)

    assert "Site nav" not in text
    assert "Copyright 2026" not in text
    assert "API Reference" in text
    assert "GET /users returns a list of users." in text


def test_find_relevant_links_filters_by_keyword_and_domain():
    html = """
    <html><body>
      <a href="/api/endpoints">Endpoints</a>
      <a href="/about">About us</a>
      <a href="https://external-site.com/api/reference">External</a>
      <a href="/auth/quickstart">Authentication Quickstart</a>
    </body></html>
    """
    soup = BeautifulSoup(html, "html.parser")
    links = _find_relevant_links(soup, base_url="https://docs.example.com/start", limit=5)

    assert any("api/endpoints" in link for link in links)
    assert any("auth/quickstart" in link for link in links)
    assert not any("about" in link for link in links)
    assert not any("external-site.com" in link for link in links)


if __name__ == "__main__":
    test_clean_text_strips_noise_tags()
    test_find_relevant_links_filters_by_keyword_and_domain()
    print("All scraper tests passed.")

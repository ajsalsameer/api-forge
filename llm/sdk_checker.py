"""
sdk_checker.py

Before generating a custom wrapper, check whether an official SDK already
exists on PyPI (Python) or npm (JavaScript) for the API the user is
integrating with.

This satisfies the brief requirement:
  "suggest SDKs or REST-based integration paths"

How it works:
  1. Extract a short name from the base_url (e.g. "stripe" from "api.stripe.com")
  2. Check KNOWN_SDKS/KNOWN_JS_SDKS for a curated match first
  3. Fall back to querying PyPI or npm by the raw extracted name
  4. Apply a relevance filter so random unrelated packages don't show up as hits
"""

import re
from typing import Optional
from urllib.parse import urlparse

import requests

PYPI_URL = "https://pypi.org/pypi/{package}/json"
NPM_URL  = "https://registry.npmjs.org/{package}"

# Keywords that suggest a package is actually an API client/SDK.
# Prevents false positives like "placeholder" matching a random PyPI utility.
SDK_KEYWORDS = {
    "api", "client", "sdk", "wrapper", "http", "rest",
    "integration", "connector", "library", "python client",
}

# Curated Python SDK names keyed by domain keyword.
KNOWN_PYTHON_SDKS = {
    "openai":      "openai",
    "stripe":      "stripe",
    "github":      "PyGithub",
    "twilio":      "twilio",
    "sendgrid":    "sendgrid",
    "anthropic":   "anthropic",
    "google":      "google-api-python-client",
    "twitter":     "tweepy",
    "slack":       "slack-sdk",
    "shopify":     "ShopifyAPI",
    "plaid":       "plaid-python",
    "braintree":   "braintree",
    "mailchimp":   "mailchimp-marketing",
    "hubspot":     "hubspot-api-client",
    "zendesk":     "zenpy",
    "spotify":     "spotipy",
    "discord":     "discord.py",
    "dropbox":     "dropbox",
    "notion":      "notion-client",
    "airtable":    "pyairtable",
    "cloudflare":  "cloudflare",
    "aws":         "boto3",
    "azure":       "azure-mgmt",
    "salesforce":  "simple-salesforce",
    "paypal":      "paypalrestsdk",
}

# Curated JavaScript/npm SDK names keyed by domain keyword.
KNOWN_JS_SDKS = {
    "openai":      "openai",
    "stripe":      "stripe",
    "github":      "@octokit/rest",
    "twilio":      "twilio",
    "sendgrid":    "@sendgrid/mail",
    "anthropic":   "@anthropic-ai/sdk",
    "google":      "googleapis",
    "twitter":     "twitter-api-v2",
    "slack":       "@slack/web-api",
    "shopify":     "@shopify/shopify-api",
    "plaid":       "plaid",
    "spotify":     "spotify-web-api-node",
    "discord":     "discord.js",
    "dropbox":     "dropbox",
    "notion":      "@notionhq/client",
    "cloudflare":  "cloudflare",
    "aws":         "aws-sdk",
    "azure":       "@azure/ms-rest-js",
    "salesforce":  "jsforce",
    "paypal":      "@paypal/checkout-server-sdk",
    "hubspot":     "@hubspot/api-client",
    "mailchimp":   "@mailchimp/mailchimp_marketing",
    "zendesk":     "node-zendesk",
}


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _extract_name(base_url: str) -> str:
    """
    Pull a short API name from a URL.
      "https://api.stripe.com/v1"          -> "stripe"
      "https://jsonplaceholder.typicode.com" -> "jsonplaceholder"
      "api.github.com"                      -> "github"  (no scheme)
    """
    try:
        parsed = urlparse(base_url)
        host = parsed.netloc or parsed.path  # handles missing scheme
        host = host.split("/")[0]            # drop any path that crept in
        # Strip common subdomain prefixes
        host = re.sub(r"^(api|docs|developer|dev|rest|sandbox)\.", "", host)
        name = host.split(".")[0]
        return name.lower().strip()
    except Exception:
        return ""


def _is_relevant(summary: str) -> bool:
    """Return True if the package summary looks like an API client/SDK."""
    lower = summary.lower()
    return any(kw in lower for kw in SDK_KEYWORDS)


def _check_pypi(package_name: str, timeout: int = 5) -> Optional[dict]:
    """Query PyPI for a package. Returns normalized info dict or None."""
    try:
        resp = requests.get(
            PYPI_URL.format(package=package_name),
            timeout=timeout,
        )
        if resp.status_code == 200:
            info = resp.json().get("info", {})
            summary = info.get("summary", "")
            if not _is_relevant(summary):
                return None
            return {
                "name":        info.get("name", package_name),
                "version":     info.get("version", ""),
                "summary":     summary,
                "url":         info.get("project_url") or f"https://pypi.org/project/{package_name}/",
                "install_cmd": f"pip install {info.get('name', package_name)}",
                "registry":    "PyPI",
            }
    except requests.RequestException:
        pass
    return None


def _check_npm(package_name: str, timeout: int = 5) -> Optional[dict]:
    """Query npm registry for a package. Returns normalized info dict or None."""
    try:
        resp = requests.get(
            NPM_URL.format(package=package_name),
            timeout=timeout,
        )
        if resp.status_code == 200:
            data = resp.json()
            # npm returns 404 JSON for missing packages but sometimes 200 with error
            if data.get("error"):
                return None
            summary = data.get("description", "")
            if not _is_relevant(summary):
                return None
            latest = data.get("dist-tags", {}).get("latest", "")
            return {
                "name":        data.get("name", package_name),
                "version":     latest,
                "summary":     summary,
                "url":         f"https://www.npmjs.com/package/{package_name}",
                "install_cmd": f"npm install {data.get('name', package_name)}",
                "registry":    "npm",
            }
    except requests.RequestException:
        pass
    return None


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #

def check(base_url: str, language: str = "python") -> Optional[dict]:
    """
    Check whether an official SDK exists for the API at base_url.

    Args:
        base_url: the API's base URL (e.g. "https://api.stripe.com")
        language: "python" (checks PyPI) or "javascript" (checks npm)

    Returns:
        dict with keys: name, version, summary, url, install_cmd, registry
        or None if no SDK found.
    """
    if not base_url:
        return None

    name = _extract_name(base_url)
    if not name:
        return None

    language = language.lower()
    known_sdks  = KNOWN_JS_SDKS if language == "javascript" else KNOWN_PYTHON_SDKS
    check_fn    = _check_npm    if language == "javascript" else _check_pypi

    # Build candidate list: curated matches first, then raw extracted name
    candidates = []
    for keyword, package in known_sdks.items():
        if keyword in name:
            candidates.append(package)
    if name not in candidates:
        candidates.append(name)

    for package in candidates:
        result = check_fn(package)
        if result:
            return result

    return None


if __name__ == "__main__":
    import sys

    test_url = sys.argv[1] if len(sys.argv) > 1 else "https://api.stripe.com"
    lang     = sys.argv[2] if len(sys.argv) > 2 else "python"

    print(f"Checking for {lang} SDK for: {test_url}")
    result = check(test_url, language=lang)
    if result:
        print(f"Found SDK  : {result['name']} v{result['version']} ({result['registry']})")
        print(f"Summary    : {result['summary']}")
        print(f"Install    : {result['install_cmd']}")
        print(f"URL        : {result['url']}")
    else:
        print("No official SDK found — a custom wrapper will be generated.")
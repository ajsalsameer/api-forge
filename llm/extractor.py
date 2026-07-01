"""
extractor.py

Turns API documentation into a structured spec the code generator can use.

Two paths, tried in order:
  Path A: look for a machine-readable OpenAPI/Swagger spec (fast, exact)
  Path B: fall back to sending scraped doc text to a Groq LLM, which reads
          the prose and extracts the same structure (slower, approximate)

Either path returns the same shape of dict, so the rest of the pipeline
(code generator) never needs to know which path was used.

Optionally, if a `use_case` description is provided, a final pass filters
the extracted endpoints down to the ones relevant to what the user is
actually building — works for BOTH the OpenAPI and LLM paths.
"""

import json
import os
from typing import Optional
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

COMMON_SPEC_PATHS = [
    "/openapi.json",
    "/swagger.json",
    "/api-docs",
    "/v1/openapi.json",
    "/.well-known/openapi.json",
]

GROQ_MODEL = "openai/gpt-oss-120b"

EXTRACTION_PROMPT = """You are an expert at reading API documentation and extracting \
structured information from it.

Read the documentation text below and extract:
1. base_url: the root URL requests are made against (best guess if not explicit)
2. auth_type: one of "api_key", "bearer_token", "oauth2", "none", or "unknown"
3. auth_details: a short note on how to send the credential (e.g. header name)
4. endpoints: a list of objects, each with:
   - method: GET, POST, PUT, DELETE, etc.
   - path: the endpoint path (e.g. /users/{{id}})
   - description: one short sentence
   - parameters: list of {{name, type, required, description}}

Only include endpoints you are reasonably confident about.

Respond with ONLY valid JSON, no markdown formatting, no explanation:
{{
  "base_url": "...",
  "auth_type": "...",
  "auth_details": "...",
  "endpoints": [
    {{"method": "...", "path": "...", "description": "...", "parameters": []}}
  ]
}}

Documentation text:
---
{docs_text}
---
"""

# Appended on retry when the LLM returns invalid JSON
JSON_RETRY_SUFFIX = """

Your previous response was not valid JSON. Respond again with ONLY the JSON
object described above — no markdown fences, no commentary, just the JSON."""

# Index-based filter: LLM returns positions only, not full objects.
# Safer than asking for full objects back (LLMs silently modify them).
FILTER_PROMPT = """A developer is building: "{use_case}"

Here are API endpoints (numbered):
{endpoint_summaries}

Return ONLY a JSON array of the 0-based index numbers of the endpoints
relevant to what this developer is building. Be reasonably generous.
Example response: [0, 3, 4, 9]"""


# --------------------------------------------------------------------------- #
# Internal helpers                                                             #
# --------------------------------------------------------------------------- #

def _try_fetch_spec(url: str, timeout: int = 5) -> Optional[dict]:
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 200 and "json" in resp.headers.get("Content-Type", ""):
            return resp.json()
    except (requests.RequestException, ValueError):
        pass
    return None


def _extract_request_body_params(details: dict) -> list:
    """Pull parameters from an OpenAPI 3.x requestBody block (POST/PUT/PATCH)."""
    json_schema = (
        details.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema", {})
    )
    required = set(json_schema.get("required", []))
    return [
        {
            "name": name,
            "type": prop.get("type", "string"),
            "required": name in required,
            "description": prop.get("description", ""),
        }
        for name, prop in json_schema.get("properties", {}).items()
    ]


def _normalize_result(result: dict) -> dict:
    """Fill in missing keys so downstream code never KeyErrors."""
    result.setdefault("base_url", "")
    result.setdefault("auth_type", "unknown")
    result.setdefault("auth_details", "")
    result.setdefault("endpoints", [])
    return result


# --------------------------------------------------------------------------- #
# Path A — OpenAPI / Swagger spec                                              #
# --------------------------------------------------------------------------- #

def find_openapi_spec(base_url: str) -> Optional[dict]:
    for path in COMMON_SPEC_PATHS:
        candidate = urljoin(base_url, path)
        spec = _try_fetch_spec(candidate)
        if spec and ("openapi" in spec or "swagger" in spec):
            print(f"[extractor] Found OpenAPI/Swagger spec at {candidate}")
            return spec
    return None


def parse_openapi_spec(spec: dict) -> dict:
    """Convert a raw OpenAPI/Swagger dict into our normalized internal shape."""
    servers = spec.get("servers", [])
    if servers:
        base_url = servers[0]["url"]
    elif spec.get("host"):
        # Swagger 2.0: reconstruct from scheme + host + basePath
        scheme   = (spec.get("schemes") or ["https"])[0]
        base_url = f"{scheme}://{spec['host']}{spec.get('basePath', '')}"
    else:
        base_url = ""

    # Support OpenAPI 3.x (components.securitySchemes) and Swagger 2.0 (securityDefinitions)
    security_schemes = (
        spec.get("components", {}).get("securitySchemes")
        or spec.get("securityDefinitions")
        or {}
    )
    if security_schemes:
        scheme_info  = next(iter(security_schemes.values()))
        auth_type    = scheme_info.get("type", "unknown")
        auth_details = scheme_info.get("name", scheme_info.get("scheme", ""))
    else:
        auth_type, auth_details = "none", ""

    endpoints = []
    for path, methods in spec.get("paths", {}).items():
        for method, details in methods.items():
            if method.lower() not in ("get", "post", "put", "delete", "patch"):
                continue
            params = [
                {
                    "name":        p.get("name", ""),
                    "type":        p.get("schema", {}).get("type", "string"),
                    "required":    p.get("required", False),
                    "description": p.get("description", ""),
                }
                for p in details.get("parameters", [])
            ]
            params.extend(_extract_request_body_params(details))
            endpoints.append({
                "method":      method.upper(),
                "path":        path,
                "description": details.get("summary", details.get("description", "")),
                "parameters":  params,
            })

    return {
        "base_url":    base_url,
        "auth_type":   auth_type,
        "auth_details": auth_details,
        "endpoints":   endpoints,
        "source":      "openapi_spec",
    }


# --------------------------------------------------------------------------- #
# Path B — LLM extraction                                                      #
# --------------------------------------------------------------------------- #

def extract_with_llm(docs_text: str, max_chars: int = 12000) -> dict:
    """
    Send scraped docs text to Groq and extract a structured API spec.
    Retries once with a stricter prompt if the first response is invalid JSON.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY not found. Add it to your .env file."
        )

    client   = Groq(api_key=api_key)
    prompt   = EXTRACTION_PROMPT.format(docs_text=docs_text[:max_chars])
    last_err = None

    for attempt in range(2):
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

        try:
            result          = json.loads(raw)
            result["source"] = "llm_extraction"
            return _normalize_result(result)
        except json.JSONDecodeError as exc:
            last_err = exc
            print(f"[extractor] Attempt {attempt + 1}: invalid JSON, retrying...")
            prompt = EXTRACTION_PROMPT.format(docs_text=docs_text[:max_chars]) + JSON_RETRY_SUFFIX

    raise ValueError(f"LLM did not return valid JSON after retrying. Last error: {last_err}")


# --------------------------------------------------------------------------- #
# Optional use-case filtering (works for BOTH paths)                          #
# --------------------------------------------------------------------------- #

def filter_relevant_endpoints(
    endpoints: list,
    use_case: Optional[str],
    max_endpoints: int = 20,
) -> list:
    """
    Ask the LLM which endpoints are relevant to the user's use_case.
    Only runs when use_case is given AND there are more than max_endpoints.
    Uses index-based selection so the LLM can't accidentally modify endpoint objects.
    Falls back to the first max_endpoints on any failure.
    """
    if not use_case or len(endpoints) <= max_endpoints:
        return endpoints

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        return endpoints[:max_endpoints]

    summaries = "\n".join(
        f"{i}: {e['method']} {e['path']} — {e.get('description', '')}"
        for i, e in enumerate(endpoints)
    )

    try:
        client   = Groq(api_key=api_key)
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{
                "role": "user",
                "content": FILTER_PROMPT.format(
                    use_case=use_case,
                    endpoint_summaries=summaries,
                ),
            }],
            temperature=0.1,
        )
        raw      = response.choices[0].message.content.strip()
        raw      = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        indices  = json.loads(raw)
        filtered = [endpoints[i] for i in indices if 0 <= i < len(endpoints)]
        if filtered:
            print(f"[extractor] Filtered to {len(filtered)} relevant endpoints.")
            return filtered
    except Exception as exc:
        print(f"[extractor] Endpoint filtering failed ({exc}), keeping first {max_endpoints}.")

    return endpoints[:max_endpoints]


# --------------------------------------------------------------------------- #
# Main entry point                                                             #
# --------------------------------------------------------------------------- #

def extract(
    start_url: str,
    scraped_text: str,
    use_case: Optional[str] = None,
) -> dict:
    """
    Try OpenAPI spec first, fall back to LLM extraction, then optionally
    filter endpoints by use_case. Filtering applies to BOTH paths.

    Args:
        start_url:    original docs URL (used to probe for an OpenAPI spec).
        scraped_text: combined text from the scraper.
        use_case:     optional one-liner about what the user is building.

    Returns:
        {base_url, auth_type, auth_details, endpoints, source}
    """
    spec = find_openapi_spec(start_url)
    if spec:
        result = parse_openapi_spec(spec)
    else:
        if not scraped_text.strip():
            raise ValueError(
                "No OpenAPI spec found and no scraped text available to fall back on."
            )
        print("[extractor] No OpenAPI spec found, falling back to LLM extraction")
        result = extract_with_llm(scraped_text)

    # Apply use-case filtering after BOTH paths (not just LLM path)
    result["endpoints"] = filter_relevant_endpoints(result["endpoints"], use_case)
    return result


if __name__ == "__main__":
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scraper.doc_scraper import scrape_docs

    test_url = (
        sys.argv[1] if len(sys.argv) > 1
        else "https://jsonplaceholder.typicode.com/guide/"
    )
    scraped = scrape_docs(test_url, max_pages=2)
    result  = extract(test_url, scraped["combined_text"])
    print(json.dumps(result, indent=2))
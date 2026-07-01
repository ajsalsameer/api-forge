"""
extractor.py

Turns API documentation into a structured spec the code generator can use.

Two paths, tried in order:
  Path A: look for a machine-readable OpenAPI/Swagger spec (fast, exact)
  Path B: fall back to sending scraped doc text to a Groq LLM, which reads
          the prose and extracts the same structure (slower, approximate)

Either path returns the same shape of dict, so the rest of the pipeline
(code generator) never needs to know which path was used.
"""

import json
import os
from typing import Optional
from urllib.parse import urljoin

import requests
from dotenv import load_dotenv
from groq import Groq

load_dotenv()

# Common locations where APIs publish their OpenAPI/Swagger spec.
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

Only include endpoints you are reasonably confident about. If something is
unclear, leave it out rather than guessing wildly.

Respond with ONLY valid JSON, no markdown formatting, no explanation, in this exact shape:
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

FILTER_PROMPT = """You are an expert developer. Given the following list of API endpoints
and a use-case description, return ONLY the endpoints that are relevant to the use case.

Use case: {use_case}

Endpoints (JSON):
{endpoints_json}

Respond with ONLY a valid JSON array of the relevant endpoint objects, no explanation.
Keep the exact same structure for each endpoint object.
"""


def _try_fetch_spec(url: str, timeout: int = 5) -> Optional[dict]:
    """Try to fetch and parse a JSON spec from a candidate URL."""
    try:
        resp = requests.get(url, timeout=timeout)
        if resp.status_code == 200 and "json" in resp.headers.get("Content-Type", ""):
            return resp.json()
    except (requests.RequestException, ValueError):
        return None
    return None


def find_openapi_spec(base_url: str) -> Optional[dict]:
    """
    Path A: probe common OpenAPI/Swagger locations relative to base_url.
    Returns the raw spec dict if found, otherwise None.
    """
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
    base_url = servers[0]["url"] if servers else spec.get("host", "")

    # Support both OpenAPI 3.x (components.securitySchemes)
    # and Swagger 2.0 (securityDefinitions)
    security_schemes = (
        spec.get("components", {}).get("securitySchemes", {})
        or spec.get("securityDefinitions", {})
    )
    if security_schemes:
        scheme = next(iter(security_schemes.values()))
        auth_type = scheme.get("type", "unknown")
        auth_details = scheme.get("name", scheme.get("scheme", ""))
    else:
        auth_type, auth_details = "none", ""

    endpoints = []
    for path, methods in spec.get("paths", {}).items():
        for method, details in methods.items():
            if method.lower() not in ("get", "post", "put", "delete", "patch"):
                continue

            # Query/path parameters
            params = [
                {
                    "name": p.get("name", ""),
                    "type": p.get("schema", {}).get("type", "string"),
                    "required": p.get("required", False),
                    "description": p.get("description", ""),
                }
                for p in details.get("parameters", [])
            ]

            # Request body parameters (POST/PUT/PATCH) — Swagger 2.0 misses these
            request_body = details.get("requestBody", {})
            content = request_body.get("content", {})
            json_schema = content.get("application/json", {}).get("schema", {})
            for prop_name, prop_info in json_schema.get("properties", {}).items():
                params.append({
                    "name": prop_name,
                    "type": prop_info.get("type", "string"),
                    "required": prop_name in json_schema.get("required", []),
                    "description": prop_info.get("description", ""),
                })

            endpoints.append({
                "method": method.upper(),
                "path": path,
                "description": details.get("summary", details.get("description", "")),
                "parameters": params,
            })

    return {
        "base_url": base_url,
        "auth_type": auth_type,
        "auth_details": auth_details,
        "endpoints": endpoints,
        "source": "openapi_spec",
    }


def extract_with_llm(
    docs_text: str,
    max_chars: int = 12000,
    use_case: Optional[str] = None,
) -> dict:
    """
    Path B: send scraped documentation text to Groq and ask it to extract
    the same structure an OpenAPI spec would give us.
    Retries once if the LLM returns invalid JSON.
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY not found. Add it to your .env file before running the extractor."
        )

    client = Groq(api_key=api_key)
    truncated = docs_text[:max_chars]

    last_error = None
    for attempt in range(2):
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {"role": "user", "content": EXTRACTION_PROMPT.format(docs_text=truncated)}
            ],
            temperature=0.1,
        )

        raw = response.choices[0].message.content.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()

        try:
            result = json.loads(raw)
            result["source"] = "llm_extraction"

            # Filter endpoints by use case if provided and there are many endpoints
            if use_case and len(result.get("endpoints", [])) > 10:
                result["endpoints"] = filter_relevant_endpoints(
                    result["endpoints"], use_case, client
                )

            return result
        except json.JSONDecodeError as exc:
            last_error = exc
            print(f"[extractor] Attempt {attempt + 1}: invalid JSON, retrying...")

    raise ValueError(
        f"LLM did not return valid JSON after retrying. Last error: {last_error}"
    )


def filter_relevant_endpoints(
    endpoints: list,
    use_case: str,
    client: Groq,
) -> list:
    """
    Ask the LLM to filter the endpoint list down to only those relevant
    to the user's use case. Only runs when there are more than 10 endpoints.
    Falls back to the full list if something goes wrong.
    """
    try:
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[
                {
                    "role": "user",
                    "content": FILTER_PROMPT.format(
                        use_case=use_case,
                        endpoints_json=json.dumps(endpoints, indent=2),
                    ),
                }
            ],
            temperature=0.1,
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
        filtered = json.loads(raw)
        if isinstance(filtered, list) and len(filtered) > 0:
            print(f"[extractor] Filtered to {len(filtered)} relevant endpoints for use case.")
            return filtered
    except Exception as exc:
        print(f"[extractor] Endpoint filtering failed, using full list: {exc}")
    return endpoints


def extract(
    start_url: str,
    scraped_text: str,
    use_case: Optional[str] = None,
) -> dict:
    """
    Main entry point: try the OpenAPI path first, fall back to the LLM path.

    Args:
        start_url:    the original docs URL the user provided.
        scraped_text: combined text already scraped from the docs site.
        use_case:     optional description of what the user is building,
                      used to filter endpoints on large APIs.

    Returns:
        Normalized dict: {base_url, auth_type, auth_details, endpoints, source}
    """
    # If the user pasted the spec URL directly, try to fetch it as-is first
    if start_url.endswith((".json", ".yaml", ".yml")):
        direct = _try_fetch_spec(start_url)
        if direct and ("openapi" in direct or "swagger" in direct):
            result = parse_openapi_spec(direct)
            result["endpoints"] = filter_relevant_endpoints(result["endpoints"], use_case)
            return result

    spec = find_openapi_spec(start_url)
    if spec:
        return parse_openapi_spec(spec)

    if not scraped_text.strip():
        raise ValueError(
            "No OpenAPI spec found and no scraped text available to fall back on."
        )

    print("[extractor] No OpenAPI spec found, falling back to LLM extraction")
    return extract_with_llm(scraped_text, use_case=use_case)


if __name__ == "__main__":
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scraper.doc_scraper import scrape_docs

    test_url = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "https://jsonplaceholder.typicode.com/guide/"
    )
    scraped = scrape_docs(test_url, max_pages=2)
    result = extract(test_url, scraped["combined_text"])
    print(json.dumps(result, indent=2))
"""
code_generator.py

Takes the structured API spec produced by llm/extractor.py and generates
a ready-to-use wrapper class in Python or JavaScript using Groq.

The generated class:
  - Has one method per endpoint
  - Handles authentication automatically (API key header, Bearer token, etc.)
  - Includes type hints / JSDoc and docstrings
  - Is ready to copy-paste and use immediately
"""

import ast
import json
import os
import re

from dotenv import load_dotenv
from groq import Groq

load_dotenv()

GROQ_MODEL = "openai/gpt-oss-120b"

# --------------------------------------------------------------------------- #
# Prompt templates — one per language                                          #
# --------------------------------------------------------------------------- #

PYTHON_PROMPT = """You are an expert Python developer. Generate a clean,
production-ready Python wrapper class for the following API specification.

API Specification:
{spec_json}

Requirements:
1. Class name derived from the base_url domain (e.g. "StripeClient", "GitHubClient")
2. Use the `requests` library for all HTTP calls
3. Handle authentication in __init__ based on auth_type:
   - "api_key": accept api_key parameter, send as a header or query param
   - "bearer_token" / "oauth2": accept token parameter, send as Authorization: Bearer
   - "none": no auth needed
4. One method per endpoint with:
   - Descriptive name (e.g. get_user, create_post, delete_comment)
   - Type hints for all parameters
   - Docstring with the endpoint description
   - Path parameters substituted correctly in the URL
   - Query params passed as params={{}}
   - Body params passed as json={{}} for POST/PUT/PATCH
   - Return response.json()
5. A shared _request() helper that raises on HTTP status >= 400
6. Short usage example in a comment at the bottom

Respond with ONLY the Python code, no markdown fences, no explanation.
"""

JAVASCRIPT_PROMPT = """You are an expert JavaScript developer. Generate a clean,
production-ready JavaScript class (ES2020, Node.js compatible) for the following
API specification. Use the built-in `fetch` API — no extra dependencies.

API Specification:
{spec_json}

Requirements:
1. Class name derived from the base_url domain (e.g. "StripeClient", "GitHubClient")
2. Use fetch() for all HTTP calls
3. Handle authentication in the constructor based on auth_type:
   - "api_key": accept apiKey, send as a header or query param
   - "bearer_token" / "oauth2": accept token, send as Authorization: Bearer
   - "none": no auth needed
4. One async method per endpoint with:
   - Descriptive camelCase name (e.g. getUser, createPost, deleteComment)
   - JSDoc comment with @param and @returns
   - Path parameters substituted correctly in the URL using template literals
   - Query params built with URLSearchParams
   - Body params sent as JSON for POST/PUT/PATCH
   - Await response, throw on status >= 400, return response.json()
5. A shared _request() async helper that throws on HTTP errors
6. Short usage example in a comment at the bottom

Respond with ONLY the JavaScript code, no markdown fences, no explanation.
"""

RETRY_SUFFIX = """

IMPORTANT: Your previous response contained a syntax error. Respond again with
ONLY valid, runnable code — no markdown fences, no commentary, nothing but code."""

PROMPTS = {
    "python": PYTHON_PROMPT,
    "javascript": JAVASCRIPT_PROMPT,
}

FILE_EXTENSIONS = {
    "python": ".py",
    "javascript": ".js",
}


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def _strip_fences(code: str) -> str:
    """Remove markdown code fences the model adds despite instructions."""
    code = re.sub(r"^```(?:python|javascript|js)?\n?", "", code)
    code = re.sub(r"\n?```$", "", code)
    return code.strip()


def _is_valid_syntax(code: str, language: str) -> tuple:
    """
    Check syntax validity.
    Python: use ast.parse() for exact checking.
    JavaScript: check for class keyword + balanced braces outside strings/templates.
    Returns (is_valid, error_message).
    """
    if language == "python":
        try:
            ast.parse(code)
            return True, ""
        except SyntaxError as exc:
            return False, str(exc)

    # JavaScript — strip string/template literal contents before counting braces
    # so that `Hello ${name}` doesn't throw off the brace count
    stripped = re.sub(r"`[^`]*`", "``", code)      # remove template literals
    stripped = re.sub(r'"[^"]*"', '""', stripped)   # remove double-quoted strings
    stripped = re.sub(r"'[^']*'", "''", stripped)   # remove single-quoted strings

    open_braces = stripped.count("{")
    close_braces = stripped.count("}")

    if open_braces != close_braces:
        return False, f"Unbalanced braces ({open_braces} open, {close_braces} close)"
    if "class " not in code:
        return False, "No class definition found in generated JavaScript"
    return True, ""


# --------------------------------------------------------------------------- #
# Public API                                                                   #
# --------------------------------------------------------------------------- #

def generate(spec: dict, language: str = "python") -> str:
    """
    Generate a wrapper class for the given API spec.

    Args:
        spec:     Normalized dict from extractor.extract()
                  (keys: base_url, auth_type, auth_details, endpoints)
        language: "python" or "javascript"

    Returns:
        String of source code for the wrapper class.
    """
    language = language.lower()
    if language not in PROMPTS:
        raise ValueError(f"Unsupported language '{language}'. Choose 'python' or 'javascript'.")

    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError("GROQ_API_KEY not found. Add it to your .env file.")

    client = Groq(api_key=api_key)

    clean_spec = {
        "base_url":     spec.get("base_url", ""),
        "auth_type":    spec.get("auth_type", "none"),
        "auth_details": spec.get("auth_details", ""),
        "endpoints":    spec.get("endpoints", []),
    }

    prompt_template = PROMPTS[language]
    prompt = prompt_template.format(spec_json=json.dumps(clean_spec, indent=2))

    last_error = ""
    for attempt in range(2):
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )
        code = _strip_fences(response.choices[0].message.content)

        valid, last_error = _is_valid_syntax(code, language)
        if valid:
            return code

        print(f"[generator] Attempt {attempt + 1}: syntax issue ({last_error}), retrying...")
        prompt = prompt_template.format(
            spec_json=json.dumps(clean_spec, indent=2)
        ) + RETRY_SUFFIX

    raise ValueError(
        f"Generated {language} code has syntax issues after retrying. Last error: {last_error}"
    )


def save(code: str, output_path: str) -> None:
    """Save generated code to a file."""
    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"[generator] Saved wrapper to {output_path}")


# --------------------------------------------------------------------------- #
# Quick test — run this file directly                                          #
# --------------------------------------------------------------------------- #

if __name__ == "__main__":
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scraper.doc_scraper import scrape_docs
    from llm.extractor import extract

    test_url = (
        sys.argv[1] if len(sys.argv) > 1
        else "https://jsonplaceholder.typicode.com/guide/"
    )
    lang = sys.argv[2] if len(sys.argv) > 2 else "python"

    print(f"[generator] Scraping {test_url} ...")
    scraped = scrape_docs(test_url, max_pages=2)

    print("[generator] Extracting API spec ...")
    spec = extract(test_url, scraped["combined_text"])
    print(f"[generator] Found {len(spec['endpoints'])} endpoints via {spec['source']}")

    print(f"[generator] Generating {lang} wrapper ...")
    code = generate(spec, language=lang)

    ext = FILE_EXTENSIONS.get(lang, ".txt")
    output_file = f"generated_wrapper{ext}"
    save(code, output_file)

    print(f"\n--- Preview (first 40 lines) ---")
    lines = code.split("\n")
    print("\n".join(lines[:40]))
    if len(lines) > 40:
        print(f"... ({len(lines) - 40} more lines) ...")
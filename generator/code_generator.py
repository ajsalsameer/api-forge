"""
code_generator.py

Takes the structured API spec produced by llm/extractor.py and generates
a ready-to-use Python wrapper class using Groq.

The generated class:
  - Has one method per endpoint
  - Handles authentication automatically (API key header, Bearer token, etc.)
  - Uses the requests library for HTTP calls
  - Includes type hints and docstrings
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

CODE_GENERATION_PROMPT = """You are an expert Python developer. Generate a clean,
production-ready Python wrapper class for the following API specification.

API Specification:
{spec_json}

Requirements for the generated class:
1. Class name should be derived from the base_url domain (e.g. "StripeClient", "GitHubClient")
2. Use the `requests` library for all HTTP calls
3. Handle authentication in __init__ based on auth_type:
   - "api_key": accept api_key parameter, add as header or query param
   - "bearer_token": accept token parameter, add as Authorization: Bearer header
   - "oauth2": accept token parameter, add as Authorization: Bearer header
   - "none": no auth needed
4. Create one method per endpoint with:
   - Descriptive method name (e.g. get_user, create_post, delete_comment)
   - Type hints for all parameters
   - A docstring with the endpoint description
   - Path parameters replaced correctly in the URL
   - Query parameters passed as params={{}}
   - Body parameters passed as json={{}} for POST/PUT/PATCH
   - Return response.json()
5. Add a __init__.py style imports comment at the top
6. Include error handling: raise an exception if response status >= 400
7. Add a short usage example in a comment at the bottom

Respond with ONLY the Python code, no markdown backticks, no explanation.
The code must be immediately runnable after pip install requests.
"""

# Appended on retry when the generated code fails ast.parse()
RETRY_SUFFIX = """

IMPORTANT: Your previous response contained invalid Python syntax. Respond
again with ONLY valid, runnable Python code — no markdown fences, no
commentary, nothing but the code itself."""


def _strip_fences(code: str) -> str:
    """Remove markdown code fences the model adds despite instructions."""
    code = re.sub(r"^```(?:python)?\n?", "", code)
    code = re.sub(r"\n?```$", "", code)
    return code.strip()


def generate(spec: dict) -> str:
    """
    Takes a normalized API spec dict (output of extractor.extract())
    and returns a string containing a complete Python wrapper class.
    Retries once if the model returns syntactically invalid Python.

    Args:
        spec: dict with keys: base_url, auth_type, auth_details, endpoints

    Returns:
        String of Python source code for the wrapper class
    """
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise RuntimeError(
            "GROQ_API_KEY not found. Add it to your .env file."
        )

    client = Groq(api_key=api_key)

    # Only send the fields the prompt needs — strip internal keys like "source"
    clean_spec = {
        "base_url": spec.get("base_url", ""),
        "auth_type": spec.get("auth_type", "none"),
        "auth_details": spec.get("auth_details", ""),
        "endpoints": spec.get("endpoints", []),
    }

    prompt = CODE_GENERATION_PROMPT.format(
        spec_json=json.dumps(clean_spec, indent=2)
    )

    last_error = None
    for attempt in range(2):  # one real attempt + one retry
        response = client.chat.completions.create(
            model=GROQ_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
        )

        code = _strip_fences(response.choices[0].message.content)

        # Validate syntax before returning — catch broken output early
        try:
            ast.parse(code)
            return code
        except SyntaxError as exc:
            last_error = exc
            print(f"[generator] Attempt {attempt + 1}: syntax error in generated code, retrying... ({exc})")
            prompt = CODE_GENERATION_PROMPT.format(
                spec_json=json.dumps(clean_spec, indent=2)
            ) + RETRY_SUFFIX

    raise ValueError(
        f"Generated code has invalid Python syntax after retrying. Last error: {last_error}"
    )


def save(code: str, output_path: str) -> None:
    """Save generated code to a .py file."""
    # os.path.dirname returns "" for a plain filename like "wrapper.py"
    # os.makedirs("") raises FileNotFoundError, so only call it when
    # there is actually a directory component in the path.
    directory = os.path.dirname(output_path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(code)
    print(f"[generator] Saved wrapper to {output_path}")


if __name__ == "__main__":
    import sys

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from scraper.doc_scraper import scrape_docs
    from llm.extractor import extract

    test_url = (
        sys.argv[1]
        if len(sys.argv) > 1
        else "https://jsonplaceholder.typicode.com/guide/"
    )

    print(f"[generator] Scraping {test_url} ...")
    scraped = scrape_docs(test_url, max_pages=2)

    print("[generator] Extracting API spec ...")
    spec = extract(test_url, scraped["combined_text"])
    print(f"[generator] Found {len(spec['endpoints'])} endpoints via {spec['source']}")

    print("[generator] Generating wrapper class ...")
    code = generate(spec)

    output_file = "generated_wrapper.py"
    save(code, output_file)

    print("\n--- Generated Code Preview (first 40 lines) ---")
    lines = code.split("\n")
    print("\n".join(lines[:40]))
    if len(lines) > 40:
        print(f"... ({len(lines) - 40} more lines) ...")
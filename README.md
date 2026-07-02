# ⚙️ api-forge

Every new API integration starts the same way. Open the docs, find the base URL, figure out how auth works, write a thin `requests` wrapper you'll copy-paste into every project. **api-forge automates that first hour.**

Paste a docs URL. Get a working Python or JavaScript client back in under 30 seconds.

```
$ streamlit run app.py
# paste https://jsonplaceholder.typicode.com/guide/
# → 12 endpoints extracted, 180-line Python wrapper generated, live test passed
```

---

## How it actually works

There are two ways API docs can exist in the world, and the tool handles both:

**When a machine-readable spec exists** (OpenAPI/Swagger) — the tool probes common locations (`/openapi.json`, `/swagger.json`, `/.well-known/openapi.json`) and parses it directly. No LLM involved. Fast and exact. This is Path A.

**When docs are just prose** (the more common case) — the scraper fetches the page, strips nav/scripts/footer noise, and sends the clean text to a Groq LLM that extracts the same structured spec as JSON. If the first response isn't valid JSON, it retries once with a stricter prompt. This is Path B.

Both paths return the same dict: `{base_url, auth_type, auth_details, endpoints, source}`. The code generator doesn't know or care which path ran.

Before generating a custom wrapper, the tool also checks PyPI (Python) or npm (JavaScript) for an official SDK. If one exists, it surfaces it with install instructions — no point generating 200 lines of `requests` boilerplate when `pip install stripe` exists.

---

## Project structure

```
api-forge/
├── app.py                   ← Streamlit UI (sidebar key input, pipeline, live test)
├── scraper/
│   └── doc_scraper.py       ← fetches URL, strips noise, follows relevant links
├── llm/
│   ├── extractor.py         ← Path A (OpenAPI) + Path B (LLM) + endpoint filtering
│   └── sdk_checker.py       ← PyPI / npm lookup with relevance check
├── generator/
│   └── code_generator.py    ← prompt → code, syntax validation, retry on failure
├── notebook/
│   └── demo.ipynb           ← full pipeline walkthrough, runs on Colab
├── tests/
│   └── test_scraper.py      ← unit tests, no internet needed
├── .env.example
└── requirements.txt
```

---

## Quick start

```bash
git clone https://github.com/ajsalsameer/api-forge.git
cd api-forge
pip install -r requirements.txt
```

Get a free Groq key at [console.groq.com](https://console.groq.com) — no card needed.

```bash
cp .env.example .env
# add GROQ_API_KEY=your_key to .env
streamlit run app.py
```

Or paste the key directly into the sidebar when the app opens — no `.env` file required.

---

## What the output looks like

For `https://jsonplaceholder.typicode.com/guide/` with no use-case filter:

```
Extraction path : llm_extraction
Base URL        : https://jsonplaceholder.typicode.com
Auth type       : none
Endpoints found : 12

METHOD   PATH                                     PARAMETERS
-------- ---------------------------------------- ----------
GET      /posts                                   —
GET      /posts/{id}                              id
POST     /posts                                   title, body, userId
PUT      /posts/{id}                              id, title, body, userId
PATCH    /posts/{id}                              id
DELETE   /posts/{id}                              id
GET      /posts/{id}/comments                     id
...
```

Generated wrapper (truncated):

```python
class JsonPlaceholderClient:
    def __init__(self, *, timeout: float = 10) -> None:
        self.base_url = "https://jsonplaceholder.typicode.com"
        self.session  = requests.Session()

    def list_posts(self) -> List[Dict[str, Any]]:
        """List all posts."""
        return self._request("GET", "/posts")

    def create_post(self, title: str, body: str, userId: int) -> Dict[str, Any]:
        """Create a new post."""
        return self._request("POST", "/posts", json_body={
            "title": title, "body": body, "userId": userId
        })
    # ... 10 more methods
```

---

## URLs to test with

| URL | What it exercises |
|-----|-------------------|
| `https://jsonplaceholder.typicode.com/guide/` | LLM path, no auth, live test available |
| `https://petstore3.swagger.io/api/v3/openapi.json` | Direct spec URL, API key auth |
| `https://petstore.swagger.io/` | OpenAPI auto-detection from docs page |
| `https://openweathermap.org/api` | LLM path, API key auth |

---

## Use-case filtering

For large APIs (Stripe, GitHub, etc.) with dozens or hundreds of endpoints, describe what you're building in the optional field. The tool asks the LLM to pick the relevant endpoints before generating the wrapper — so you get `get_pet`, `upload_photo`, `find_by_status` instead of 80 methods you'll never call.

Only fires when the endpoint count exceeds 20. Skipped entirely for smaller APIs to avoid the extra round-trip.

---

## Tech stack

| | |
|--|--|
| UI | Streamlit |
| Scraping | requests + BeautifulSoup4 |
| LLM | Groq — `openai/gpt-oss-120b` |
| SDK lookup | PyPI JSON API + npm registry |
| Validation | `ast.parse()` for Python, brace-balance heuristic for JS |
| Notebook | Jupyter / Google Colab |

> **On the model choice:** Groq is free, has no usage cap for this scale, and returns responses fast enough that the full pipeline (scrape → extract → generate) finishes in under 10 seconds on a fresh docs page. The model is `openai/gpt-oss-120b` — Groq deprecated `llama-3.3-70b-versatile` in June 2026.

---

## Running the tests

```bash
python tests/test_scraper.py
```

Tests run offline — they use hand-built HTML strings, not live HTTP calls. The scraper's noise-removal and link-discovery logic is tested directly.

---

## Known limitations

- JavaScript wrappers aren't executed in the live test (Python only)
- The brace-balance check for JS syntax is a heuristic, not a real parser
- Very large OpenAPI specs (500+ endpoints) may hit Groq's context window on the filtering step — the tool falls back to the first 20 endpoints
- Docs pages that require JavaScript to render won't scrape well (BeautifulSoup is static-only)

---

## Colab notebook

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/ajsalsameer/api-forge/blob/main/notebook/demo.ipynb)

Walks through the full pipeline step by step — scrape, extract (both paths), SDK check, generate, and a live import of the generated file to prove it actually runs.

---

**Claysys Hackathon — Smart DevTool for API Integration**  
https://github.com/ajsalsameer/api-forge
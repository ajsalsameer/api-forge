# API Forge — Smart DevTool for API Integration

Turn any API's documentation page into a ready-to-use Python client. Give API
Forge a docs URL and a short description of what you're building, and it
scrapes the docs, asks an LLM to identify the endpoints and auth method, and
generates a working wrapper class you can drop straight into your project.

## Why

Every new API integration starts the same way: read the docs, figure out the
base URL and auth header, write a thin wrapper around `requests`. API Forge
automates that first hour of boilerplate so you can get straight to using the
API instead of reading about it.

## How it works

1. **Scrape** — `scraper/doc_scraper.py` fetches the documentation URL and
   crawls a few same-domain pages that look relevant (endpoints, auth,
   quickstart).
2. **Extract** — `llm/extractor.py` sends the scraped text to an LLM (Groq)
   with a structured prompt and gets back a JSON spec: base URL, auth type,
   and a list of endpoints with methods, parameters, and descriptions.
3. **Generate** — `generator/code_generator.py` turns that JSON spec into a
   working Python wrapper class, complete with auth handling and one method
   per endpoint.
4. **UI** — `app.py` is a Streamlit app that ties the three steps together
   with a simple form and a code preview/download.
5. **Notebook** — `notebook/demo.ipynb` is a Colab-ready version of the demo
   for anyone reviewing the project without cloning it locally.

## Setup

```bash
git clone <your-repo-url>
cd api-forge
python -m venv venv
source venv/bin/activate   # on Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env       # then add your free Groq API key
```

Get a free Groq API key at https://console.groq.com.

## Usage

```bash
streamlit run app.py
```

Paste a documentation URL (e.g. a public API's reference page) and a
one-line description of your use case, then click "Generate Wrapper".

## Project structure

```
api-forge/
├── app.py                  # Streamlit UI
├── scraper/
│   └── doc_scraper.py        # Step 1: fetch + clean docs content
├── llm/
│   └── extractor.py            # Step 2: LLM extracts structured API spec
├── generator/
│   └── code_generator.py         # Step 3: spec -> Python wrapper class
├── notebook/
│   └── demo.ipynb                  # Colab-ready demo version
├── tests/
│   └── test_scraper.py               # unit tests, no network needed
└── requirements.txt
```

## Tech stack

Python, Streamlit, BeautifulSoup + Requests for scraping, Groq API for LLM
extraction and code generation.

## Status

Work in progress — see commit history for the build progression from the
initial scraper through to the finished tool.

## License

MIT

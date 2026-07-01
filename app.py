"""
app.py

Streamlit UI for api-forge.

Flow:
  1. User pastes a docs URL + optional use-case description
  2. User picks language (Python / JavaScript)
  3. App scrapes the docs, checks for OpenAPI spec, extracts endpoints,
     checks for an official SDK, generates a wrapper class
  4. Shows: SDK suggestion (if any) + summary card + generated code
  5. User can download the file or run a live test on no-auth APIs
"""

import os
import sys

import requests
import streamlit as st
from dotenv import load_dotenv

load_dotenv()

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from scraper.doc_scraper import scrape_docs
from llm.extractor import extract
from llm.sdk_checker import check as check_sdk
from generator.code_generator import generate, FILE_EXTENSIONS

# --------------------------------------------------------------------------- #
# Page config                                                                  #
# --------------------------------------------------------------------------- #

st.set_page_config(
    page_title="api-forge",
    page_icon="⚙️",
    layout="wide",
)

# --------------------------------------------------------------------------- #
# Sidebar — API key + instructions                                             #
# --------------------------------------------------------------------------- #

with st.sidebar:
    st.header("⚙️ api-forge")
    st.caption("Turn any API docs into a working wrapper class.")

    st.divider()

    # Allow the user to paste their Groq key in the UI if .env isn't set up.
    # This makes the Colab demo and video recording much smoother.
    env_key = os.getenv("GROQ_API_KEY", "")
    groq_key_input = st.text_input(
        "Groq API Key",
        value=env_key,
        type="password",
        placeholder="gsk_...",
        help="Free key at https://console.groq.com — never stored, only used for this session.",
    )
    if groq_key_input:
        os.environ["GROQ_API_KEY"] = groq_key_input

    st.divider()
    st.markdown(
        "**How it works**\n"
        "1. Scrapes the docs URL you provide\n"
        "2. Detects an OpenAPI/Swagger spec if one exists\n"
        "3. Falls back to LLM extraction if not\n"
        "4. Checks PyPI / npm for an official SDK\n"
        "5. Generates a ready-to-use wrapper class\n"
    )
    st.divider()
    st.markdown(
        "Try these URLs:\n"
        "- `https://jsonplaceholder.typicode.com/guide/` _(no auth)_\n"
        "- `https://petstore.swagger.io/` _(OpenAPI spec)_\n"
        "- `https://openweathermap.org/api` _(API key auth)_\n"
    )

# --------------------------------------------------------------------------- #
# Main header                                                                  #
# --------------------------------------------------------------------------- #

st.title("⚙️ api-forge")
st.caption("Paste any API documentation URL and get a ready-to-use wrapper class instantly.")
st.divider()

# --------------------------------------------------------------------------- #
# Inputs                                                                       #
# --------------------------------------------------------------------------- #

col1, col2 = st.columns([3, 1])

with col1:
    docs_url = st.text_input(
        "API Documentation URL",
        placeholder="https://docs.example.com/api",
        help="Paste the URL of the API documentation page.",
    )

with col2:
    language = st.selectbox(
        "Output Language",
        options=["Python", "JavaScript"],
        index=0,
    )

use_case = st.text_input(
    "What are you building? (optional)",
    placeholder="e.g. fetch user profiles and post activity from a social API",
    help="Helps the tool focus on the most relevant endpoints for large APIs.",
)

generate_btn = st.button("⚡ Generate Wrapper", type="primary", use_container_width=True)

st.divider()

# --------------------------------------------------------------------------- #
# Main pipeline                                                                #
# --------------------------------------------------------------------------- #

if generate_btn:
    if not docs_url.strip():
        st.error("Please enter a documentation URL before clicking Generate.")
        st.stop()

    if not os.getenv("GROQ_API_KEY"):
        st.error("Groq API key is missing. Paste it in the sidebar to continue.")
        st.stop()

    lang_key = language.lower()
    ext      = FILE_EXTENSIONS.get(lang_key, ".py")
    spec     = {}
    code     = ""
    sdk_info = None

    with st.status("Running api-forge pipeline...", expanded=True) as status:

        # Step 1: Scrape
        st.write("🔍 Scraping documentation...")
        try:
            scraped      = scrape_docs(docs_url, max_pages=5)
            pages_found  = len(scraped.get("pages", []))           # FIXED: was "pages_scraped"
            scraped_text = scraped.get("combined_text", "")

            # Fix common encoding artifacts (â, â, etc.) from Windows-1252 mis-decoding
            scraped_text = scraped_text.encode("utf-8", "ignore").decode("utf-8")

            st.write(f"✅ Scraped {pages_found} page(s) — {len(scraped_text):,} characters.")
        except Exception as exc:
            status.update(label="Pipeline failed", state="error")
            st.error(f"Scraping failed: {exc}")
            st.stop()

        # Step 2: Extract
        st.write("🧠 Extracting API spec...")
        try:
            spec           = extract(docs_url, scraped_text, use_case=use_case.strip() or None)
            endpoint_count = len(spec.get("endpoints", []))
            source_label   = "OpenAPI spec ✨" if spec.get("source") == "openapi_spec" else "LLM extraction"
            st.write(f"✅ Found {endpoint_count} endpoint(s) via {source_label}.")
        except Exception as exc:
            status.update(label="Pipeline failed", state="error")
            st.error(f"Extraction failed: {exc}")
            st.stop()

        # Step 3: SDK check
        st.write(f"📦 Checking for an official {language} SDK...")
        try:
            sdk_info = check_sdk(docs_url, language=lang_key)
            if sdk_info:
                st.write(f"✅ Found official SDK: `{sdk_info['name']}`")
            else:
                st.write("ℹ️ No official SDK found — generating custom wrapper.")
        except Exception:
            st.write("ℹ️ SDK check skipped.")
            sdk_info = None

        # Step 4: Generate
        st.write(f"⚙️ Generating {language} wrapper class...")
        try:
            code = generate(spec, language=lang_key)
            st.write(f"✅ Wrapper generated ({len(code.splitlines())} lines).")
        except Exception as exc:
            status.update(label="Pipeline failed", state="error")
            st.error(f"Code generation failed: {exc}")
            st.stop()

        status.update(label="Done! ✅", state="complete", expanded=False)

    st.toast("Wrapper ready!", icon="✅")

    # ------------------------------------------------------------------ #
    # SDK suggestion banner                                               #
    # ------------------------------------------------------------------ #

    if sdk_info:
        st.warning(
            f"💡 **Official SDK available:** `{sdk_info['name']}` v{sdk_info['version']} "
            f"on {sdk_info['registry']}\n\n"
            f"{sdk_info['summary']}\n\n"
            f"**Install:** `{sdk_info['install_cmd']}`  •  "
            f"[View on {sdk_info['registry']}]({sdk_info['url']})\n\n"
            f"_The custom wrapper below is still useful if you prefer zero extra "
            f"dependencies or need tailored behaviour._",
        )

    # ------------------------------------------------------------------ #
    # Summary card                                                        #
    # ------------------------------------------------------------------ #

    st.subheader("📋 API Summary")

    c1, c2, c3, c4 = st.columns(4)
    # FIXED: base_url can be long — st.metric clips it badly, use caption instead
    c2.metric("Auth Type",  spec.get("auth_type", "—").replace("_", " ").title())
    c3.metric("Endpoints",  len(spec.get("endpoints", [])))
    c4.metric("Source",     "OpenAPI Spec" if spec.get("source") == "openapi_spec" else "LLM")
    c1.metric("Language",   language)

    base_url = spec.get("base_url", "")
    if base_url:
        st.caption(f"🌐 **Base URL:** `{base_url}`")
    if spec.get("auth_details"):
        st.caption(f"🔑 **Auth details:** {spec['auth_details']}")

    with st.expander("View extracted endpoints"):
        for ep in spec.get("endpoints", []):
            params = ", ".join(f"`{p['name']}`" for p in ep.get("parameters", []))
            st.markdown(
                f"**{ep['method']}** `{ep['path']}` — {ep.get('description', '')}  \n"
                f"Parameters: {params if params else '_none_'}"
            )

    st.divider()

    # ------------------------------------------------------------------ #
    # Generated code + download                                           #
    # ------------------------------------------------------------------ #

    st.subheader(f"Generated {language} Wrapper")
    st.code(code, language=lang_key)

    filename = f"api_wrapper{ext}"
    st.download_button(
        label=f"⬇️ Download {filename}",
        data=code,
        file_name=filename,
        mime="text/plain",
        use_container_width=True,
    )

    st.divider()

    # ------------------------------------------------------------------ #
    # Live test (Python + no-auth only)                                   #
    # ------------------------------------------------------------------ #

    if lang_key == "python" and spec.get("auth_type") == "none":
        st.subheader("🧪 Live Test")
        st.caption(
            "This API requires no authentication — we can call a real endpoint "
            "right now to prove the generated wrapper works."
        )

        get_endpoints = [
            ep for ep in spec.get("endpoints", [])
            if ep["method"] == "GET" and "{" not in ep["path"]
        ]

        if get_endpoints:
            ep_options    = {f"{ep['method']} {ep['path']}": ep for ep in get_endpoints}
            chosen_label  = st.selectbox("Pick an endpoint to test", list(ep_options.keys()))
            chosen_ep     = ep_options[chosen_label]

            if st.button("▶️ Run Live Test", use_container_width=True):
                test_url = base_url.rstrip("/") + chosen_ep["path"]
                try:
                    resp = requests.get(test_url, timeout=10)
                    resp.raise_for_status()
                    data    = resp.json()
                    preview = data[0] if isinstance(data, list) else data
                    st.success(f"✅ {resp.status_code} OK — live response from `{test_url}`:")
                    st.json(preview)
                except requests.HTTPError as exc:
                    st.error(f"HTTP error: {exc}")
                except Exception as exc:
                    st.error(f"Request failed: {exc}")
        else:
            st.info("No parameter-free GET endpoints found for live testing.")

    elif lang_key == "python" and spec.get("auth_type") != "none":
        st.info(
            "ℹ️ Live test skipped — this API requires authentication. "
            "Download the wrapper and add your credentials to test locally."
        )

    elif lang_key == "javascript":
        st.info(
            "ℹ️ Live test is available for Python only. "
            "Download the generated `.js` file and run it with Node.js to test."
        )
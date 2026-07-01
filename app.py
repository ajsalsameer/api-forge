"""
app.py - Streamlit UI for api-forge
Uses session_state to persist pipeline results across button clicks.
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

st.set_page_config(page_title="api-forge", page_icon="⚙️", layout="wide")

# --------------------------------------------------------------------------- #
# Session state initialisation                                                 #
# --------------------------------------------------------------------------- #

for key in ["spec", "code", "sdk_info", "lang_key", "done"]:
    if key not in st.session_state:
        st.session_state[key] = None

if "done" not in st.session_state:
    st.session_state.done = False

# --------------------------------------------------------------------------- #
# Sidebar                                                                      #
# --------------------------------------------------------------------------- #

with st.sidebar:
    st.header("⚙️ api-forge")
    st.caption("Turn any API docs into a working wrapper class.")
    st.divider()

    env_key = os.getenv("GROQ_API_KEY", "")
    groq_key_input = st.text_input(
        "Groq API Key",
        value=env_key,
        type="password",
        placeholder="gsk_...",
        help="Free key at https://console.groq.com",
    )
    if groq_key_input:
        os.environ["GROQ_API_KEY"] = groq_key_input

    st.divider()
    st.markdown(
        "**How it works**\n"
        "1. Scrapes the docs URL\n"
        "2. Detects OpenAPI/Swagger spec if available\n"
        "3. Falls back to LLM extraction\n"
        "4. Checks PyPI / npm for official SDK\n"
        "5. Generates a ready-to-use wrapper class\n"
    )
    st.divider()
    st.markdown(
        "**Try these URLs:**\n"
        "- `https://jsonplaceholder.typicode.com/guide/`\n"
        "- `https://petstore.swagger.io/`\n"
        "- `https://openweathermap.org/api`\n"
    )

# --------------------------------------------------------------------------- #
# Header + inputs                                                              #
# --------------------------------------------------------------------------- #

st.title("⚙️ api-forge")
st.caption("Paste any API documentation URL and get a ready-to-use wrapper class instantly.")
st.divider()

col1, col2 = st.columns([3, 1])
with col1:
    docs_url = st.text_input(
        "API Documentation URL",
        placeholder="https://docs.example.com/api",
    )
with col2:
    language = st.selectbox("Output Language", ["Python", "JavaScript"])

use_case = st.text_input(
    "What are you building? (optional)",
    placeholder="e.g. fetch user profiles and post activity from a social API",
)

generate_btn = st.button("⚡ Generate Wrapper", type="primary", use_container_width=True)
st.divider()

# --------------------------------------------------------------------------- #
# Pipeline — only runs when button clicked                                     #
# --------------------------------------------------------------------------- #

if generate_btn:
    if not docs_url.strip():
        st.error("Please enter a documentation URL.")
        st.stop()
    if not os.getenv("GROQ_API_KEY"):
        st.error("Groq API key missing. Paste it in the sidebar.")
        st.stop()

    lang_key = language.lower()
    st.session_state.lang_key = lang_key
    st.session_state.done = False

    with st.status("Running api-forge pipeline...", expanded=True) as status:

        # Step 1
        st.write("🔍 Scraping documentation...")
        try:
            scraped      = scrape_docs(docs_url, max_pages=5)
            scraped_text = scraped.get("combined_text", "")
            # Fix encoding artifacts (â etc.) from Windows-1252 mis-decoding
            scraped_text = scraped_text.encode("utf-8", "ignore").decode("utf-8")
            pages        = len(scraped.get("pages", []))
            st.write(f"✅ Scraped {pages} page(s) — {len(scraped_text):,} characters.")
        except Exception as exc:
            status.update(label="Failed", state="error")
            st.error(f"Scraping failed: {exc}")
            st.stop()

        # Step 2
        st.write("🧠 Extracting API spec...")
        try:
            spec  = extract(docs_url, scraped_text, use_case=use_case.strip() or None)
            count = len(spec.get("endpoints", []))
            src   = "OpenAPI spec ✨" if spec.get("source") == "openapi_spec" else "LLM extraction"
            st.write(f"✅ Found {count} endpoint(s) via {src}.")
            st.session_state.spec = spec
        except Exception as exc:
            status.update(label="Failed", state="error")
            st.error(f"Extraction failed: {exc}")
            st.stop()

        # Step 3
        st.write(f"📦 Checking for official {language} SDK...")
        try:
            sdk_info = check_sdk(docs_url, language=lang_key)
            st.session_state.sdk_info = sdk_info
            if sdk_info:
                st.write(f"✅ Found: `{sdk_info['name']}`")
            else:
                st.write("ℹ️ No official SDK found — generating custom wrapper.")
        except Exception:
            st.session_state.sdk_info = None
            st.write("ℹ️ SDK check skipped.")

        # Step 4
        st.write(f"⚙️ Generating {language} wrapper...")
        try:
            code = generate(spec, language=lang_key)
            st.session_state.code = code
            st.write(f"✅ Done ({len(code.splitlines())} lines).")
        except Exception as exc:
            status.update(label="Failed", state="error")
            st.error(f"Generation failed: {exc}")
            st.stop()

        status.update(label="Done! ✅", state="complete", expanded=False)
        st.session_state.done = True
        st.toast("Wrapper ready!", icon="✅")

# --------------------------------------------------------------------------- #
# Results — shown from session_state, survives any button click               #
# --------------------------------------------------------------------------- #

if st.session_state.done and st.session_state.spec:
    spec     = st.session_state.spec
    code     = st.session_state.code
    sdk_info = st.session_state.sdk_info
    lang_key = st.session_state.lang_key
    language = lang_key.capitalize()
    ext      = FILE_EXTENSIONS.get(lang_key, ".py")
    base_url = spec.get("base_url", "")

    # SDK banner
    if sdk_info:
        st.warning(
            f"💡 **Official SDK:** `{sdk_info['name']}` v{sdk_info['version']} "
            f"on {sdk_info['registry']}\n\n"
            f"{sdk_info['summary']}\n\n"
            f"**Install:** `{sdk_info['install_cmd']}`  •  "
            f"[View]({sdk_info['url']})\n\n"
            f"_The generated wrapper below is still useful for a zero-dependency approach._"
        )

    # Summary card
    st.subheader("📋 API Summary")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Language",  language)
    c2.metric("Auth Type", spec.get("auth_type", "—").replace("_", " ").title())
    c3.metric("Endpoints", len(spec.get("endpoints", [])))
    c4.metric("Source",    "OpenAPI Spec" if spec.get("source") == "openapi_spec" else "LLM")
    if base_url:
        st.caption(f"🌐 **Base URL:** `{base_url}`")
    if spec.get("auth_details"):
        st.caption(f"🔑 **Auth:** {spec['auth_details']}")

    with st.expander("View extracted endpoints"):
        for ep in spec.get("endpoints", []):
            params = ", ".join(f"`{p['name']}`" for p in ep.get("parameters", []))
            st.markdown(
                f"**{ep['method']}** `{ep['path']}` — {ep.get('description', '')}  \n"
                f"Parameters: {params or '_none_'}"
            )

    st.divider()

    # Generated code
    st.subheader(f"Generated {language} Wrapper")
    st.code(code, language=lang_key)
    st.download_button(
        label=f"⬇️ Download api_wrapper{ext}",
        data=code,
        file_name=f"api_wrapper{ext}",
        mime="text/plain",
        use_container_width=True,
    )

    st.divider()

    # Live test
    if lang_key == "python" and spec.get("auth_type") == "none":
        st.subheader("🧪 Live Test")
        st.caption("This API needs no auth — let's call it live to prove the wrapper works.")

        get_endpoints = [
            ep for ep in spec.get("endpoints", [])
            if ep["method"] == "GET" and "{" not in ep["path"]
        ]

        if get_endpoints:
            ep_options   = {f"{ep['method']} {ep['path']}": ep for ep in get_endpoints}
            chosen_label = st.selectbox("Pick an endpoint", list(ep_options.keys()))
            chosen_ep    = ep_options[chosen_label]

            if st.button("▶️ Run Live Test", use_container_width=True):
                test_url = base_url.rstrip("/") + chosen_ep["path"]
                try:
                    resp = requests.get(test_url, timeout=10)
                    resp.raise_for_status()
                    data    = resp.json()
                    preview = data[0] if isinstance(data, list) else data
                    st.success(f"✅ {resp.status_code} OK — `{test_url}`")
                    st.json(preview)
                except Exception as exc:
                    st.error(f"Request failed: {exc}")
        else:
            st.info("No parameter-free GET endpoints available for live testing.")

    elif lang_key == "python" and spec.get("auth_type") != "none":
        st.info("ℹ️ This API requires authentication — download the wrapper and test locally.")

    elif lang_key == "javascript":
        st.info("ℹ️ Download the `.js` file and run it with Node.js to test.")
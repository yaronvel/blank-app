import os
import base64
import json
from pathlib import Path
from datetime import datetime
from inspect import signature as _sig

import openai
import streamlit as st

# Optional git integration (GitPython)
try:
    import git  # type: ignore
except ImportError:
    git = None  # will warn later if token provided but lib missing

# ------------------------------------------------------------
# 🧹  Room Inspector — Streamlit Web App (v6)
# ------------------------------------------------------------
# • Loads reference photo from disk (hidden)
# • Captures new photo via camera (rear‑facing when supported)
# • Uses OpenAI Vision to check cleanliness
# • If room is clean → writes timestamp to last_clean.txt AND pushes it to GitHub
# ------------------------------------------------------------
#   requirements.txt should now include: gitpython
# ------------------------------------------------------------

st.set_page_config(page_title="Room Inspector", page_icon="🧹", layout="centered")
st.title("🧹 Room Inspector")

# ---------- Secrets / ENV -----------------------------------
def _get_secret(path: str, default: str = ""):
    """Helper to read nested keys from st.secrets or env."""
    keys = path.split(".")
    node = st.secrets
    for k in keys:
        if k in node:
            node = node[k]
        else:
            return os.getenv(path.upper().replace(".", "_"), default)
    return node

openai_api_key = st.text_input(
    "🔑 OpenAI API Key", type="password", value=_get_secret("openai.api_key")
)

github_token = _get_secret("github.token")  # personal access token with repo scope
github_branch = _get_secret("github.branch", "main")

github_enabled = bool(github_token)

# ---------- Load reference image ----------------------------
ref_path = "reference_room.jpg"
if not os.path.exists(ref_path):
    st.error(f"Reference photo missing: {ref_path}")
    st.stop()
ref_bytes = Path(ref_path).read_bytes()
ref_mime = "image/jpeg" if ref_path.lower().endswith(".jpg") else "image/png"
# (hidden from UI)

# ---------- Capture new photo -------------------------------
_camera_kwargs = {}
if "mirror_image" in _sig(st.camera_input).parameters:
    _camera_kwargs["mirror_image"] = False  # prefer rear camera when supported

latest_file = st.camera_input(
    "📷 צלם תמונה חדשה של החדר (בחר מצלמה אחורית במכשיר נייד)",
    **_camera_kwargs,
)

# ------------------------------------------------------------

def file_to_b64(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"


def push_last_clean_to_github(timestamp: str):
    """Commit & push last_clean.txt if Git token is available."""
    if not github_enabled:
        return
    if git is None:
        st.warning("⚠️ GitPython not installed – cannot push to GitHub.")
        return
    try:
        repo = git.Repo(".")
    except git.exc.InvalidGitRepositoryError:
        st.warning("⚠️ Current directory is not a git repository – skipping push.")
        return

    # write timestamp file (already done by caller) and commit
    repo.index.add(["last_clean.txt"])
    repo.index.commit(f"Update last clean timestamp {timestamp}")

    # embed token in remote URL temporarily
    origin = repo.remote("origin")
    old_url = origin.url
    if github_token and old_url.startswith("https://"):
        protocol, rest = old_url.split("://", 1)
        if not rest.endswith(".git"):
            rest = rest.rstrip("/") + ".git"
        if "@" in rest:
            rest = rest.split("@", 1)[1]
        new_url = f"https://{github_token}:x-oauth-basic@{rest}"
        origin.set_url(new_url)

    try:
        origin.push(f"HEAD:{github_branch}")
        st.info("📤 last_clean.txt שודרג והועלה ל‑GitHub בהצלחה")
    except Exception as e:
        st.warning(f"⚠️ שגיאה בעת הדחיפה ל‑GitHub: {e}")
    finally:
        # restore original URL to avoid token leakage in .git/config
        if github_token and old_url:
            origin.set_url(old_url)

# ---------- Analyse button ----------------------------------
if st.button("🧐 נתח את החדר", type="primary"):

    if not openai_api_key:
        st.error("יש להזין מפתח API של OpenAI.")
        st.stop()
    if latest_file is None:
        st.error("צלם תמונה חדשה של החדר תחילה.")
        st.stop()

    client = openai.OpenAI(api_key=openai_api_key)

    ref_b64 = file_to_b64(ref_bytes, ref_mime)
    latest_b64 = file_to_b64(latest_file.getvalue(), latest_file.type)

    system_prompt = (
        "You are an expert interior organiser.\n"
        "Compare the two images.\n"
        "A room is not clean if there is a blanket on the floor\n",
        "When checking if the same room, make sure the picture shows the same furnitures\n",
        "Respond ONLY with valid JSON: \n"
        "{\n"
        "  \"same_room\": true|false,\n"
        "  \"is_clean\": true|false,\n"
        "  \"suggestions\": [\"tip 1\", \"tip 2\"]\n"
        "}\n"
        "If is_clean is true, suggestions may be an empty array.\n"
        "If is_clean is false, suggestions MUST be written in HEBREW."
    )

    messages = [
        {"role": "system", "content": system_prompt},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Reference room photo:"},
                {"type": "image_url", "image_url": {"url": ref_b64}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Latest room photo:"},
                {"type": "image_url", "image_url": {"url": latest_b64}},
            ],
        },
    ]

    with st.spinner("שולח בקשה ל‑OpenAI ..."):
        try:
            resp = client.chat.completions.create(
                model="gpt-4o",
                messages=messages,
                temperature=0,
            )
            content = resp.choices[0].message.content.strip()
            if content.startswith("```json"):
                content = content.removeprefix("```json").removesuffix("```")
            data = json.loads(content)
        except json.JSONDecodeError:
            st.error("❌ לא הצלחתי לנתח את תגובת OpenAI (JSON שגוי).")
            st.text(content)
            st.stop()
        except Exception as e:
            st.error(f"שגיאת OpenAI: {e}")
            st.stop()

    # ---------- Present results & Git push -------------------
    if not data.get("same_room", False):
        st.error("❗ נראה כי אלו אינם אותו חדר.")

        timestamp = datetime.now().isoformat()
    else:
        if data.get("is_clean", False):
            st.success("✅ החדר נראה מסודר ונקי — כל הכבוד!")
            timestamp = datetime.now().isoformat()
            try:
                Path("last_clean.txt").write_text(timestamp)
                push_last_clean_to_github(timestamp)
            except Exception as e:
                st.warning(f"⚠️ לא הצלחתי לעדכן last_clean.txt: {e}")
        else:
            st.warning("🧹 החדר אינו מסודר. הצעות לשיפור:")
            for tip in data.get("suggestions", []):
                st.markdown(f"- {tip}")

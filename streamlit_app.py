import os
import base64
import json
from pathlib import Path

import openai
import streamlit as st
from inspect import signature as _sig

# ------------------------------------------------------------
# 🧹  Room Inspector — Streamlit Web App (v5)
# ------------------------------------------------------------
# • Loads a reference photo from disk (not shown to the user)
# • Captures a new photo via rear‑facing camera (non‑selfie)
# • Asks an OpenAI Vision model to compare cleanliness
# • Shows Hebrew suggestions if the room is messy
# ------------------------------------------------------------

st.set_page_config(page_title="Room Inspector", page_icon="🧹", layout="centered")
st.title("🧹 Room Inspector")

# ---------- API key (hybrid) --------------------------------
_default_api_key = (
    st.secrets.get("openai", {}).get("api_key")
    if "openai" in st.secrets
    else os.getenv("OPENAI_API_KEY", "")
)
openai_api_key = st.text_input(
    "🔑 OpenAI API Key", type="password", value=_default_api_key
)

# ---------- Load reference image ----------------------------
ref_path = "reference_room.jpg"
if not os.path.exists(ref_path):
    st.error(f"Reference photo missing: {ref_path}")
    st.stop()
ref_bytes = Path(ref_path).read_bytes()
ref_mime = "image/jpeg" if ref_path.lower().endswith(".jpg") else "image/png"
# (No display — hidden from UI)

# ---------- Capture new photo -------------------------------
_camera_kwargs = {}
if "mirror_image" in _sig(st.camera_input).parameters:
    _camera_kwargs["mirror_image"] = False  # מונע מצב סלפי בדפדפן שתומך

latest_file = st.camera_input(
    "📷 צלם תמונה חדשה של החדר (בחר מצלמה אחורית במכשיר נייד)",
    **_camera_kwargs,
)

# ------------------------------------------------------------

def file_to_b64(data: bytes, mime: str) -> str:
    return f"data:{mime};base64,{base64.b64encode(data).decode()}"

# ---------- Analyse button ----------------------------------
if st.button("🧐 נתח את החדר", type="primary"):

    if not openai_api_key:
        st.error("יש להזין מפתח API של OpenAI (בשדה למעלה או כ‑ENV/secrets).")
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

    # ---------- Present results ------------------------------
    if not data.get("same_room", False):
        st.error("❗ נראה כי אלו אינם אותו חדר.")
    else:
        if data.get("is_clean", False):
            st.success("✅ החדר נראה מסודר ונקי — כל הכבוד!")
            # Write timestamp when room is clean
            try:
                Path("last_clean.txt").write_text(datetime.now().isoformat())
            except Exception as e:
                st.warning(f"⚠️ לא הצלחתי לכתוב לקובץ last_clean.txt: {e}")            
        else:
            st.warning("🧹 החדר אינו מסודר. הצעות לשיפור:")
            for tip in data.get("suggestions", []):
                st.markdown(f"- {tip}")

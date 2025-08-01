import os
import base64
import json
from pathlib import Path
from datetime import datetime
from inspect import signature as _sig
import PIL.Image
import base64
from io import BytesIO
import google.generativeai as genai

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
st.title("🧹 Room Inspector v0.0.9")

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

gemini_api_key = st.text_input(
    "🔑 Gemini API Key", type="password", value=_get_secret("gemini.api_key")
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


def push_last_clean_to_github(files):
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
    repo.index.add(files)
    repo.index.commit(f"Update")

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
        st.info("📤 הניסיון הועלה לגיטהאב")
    except Exception as e:
        st.warning(f"⚠️ שגיאה בעת הדחיפה ל‑GitHub: {e}")
    finally:
        # restore original URL to avoid token leakage in .git/config
        if github_token and old_url:
            origin.set_url(old_url)

def compare_rooms_in_with_gemini(api_key: str, reference_image_b64, new_image_b64) -> dict:
    """
    Compares two images using the Gemini API and returns a structured JSON response.
    This version is adapted to take image data from Streamlit widgets.

    Args:
        api_key: The user's Google AI API key.
        reference_image_data: The image data for the reference image.
        new_image_data: The image data for the new image.

    Returns:
        A dictionary containing the analysis from the Gemini API.
    """
    try:
        # Configure the generative AI client with the API key
        genai.configure(api_key=api_key)

        # Load the images from the image data
        decoded_bytes = base64.b64decode(reference_image_b64)
        reference_img = PIL.Image.open(BytesIO(decoded_bytes))

        decoded_bytes = base64.b64decode(new_image_b64)
        new_img = PIL.Image.open(BytesIO(decoded_bytes))

        new_img = PIL.Image.open(new_image_data)

        # Initialize the generative model
        model = genai.GenerativeModel('gemini-2.5-flash-latest')

        # The prompt asking for a JSON response.
        prompt = """
        Analyze the two images. The first is a reference, the second is a new picture.
        Respond ONLY with a JSON object in the following format. Do not include any other text or markdown formatting.

        {
          "is_the_same_room": <boolean>,
          "is_clean": <boolean>,
          "is_picture_wide_enough": <boolean>,
          "differences": "<A string describing the differences between the two images.>",
          "suggestions_hebrew": "<A string with suggestions in Hebrew. If the room is messy, suggest how to clean it. If the photo is cropped, suggest how to take a wider photo. If no suggestions are needed, leave this as an empty string.>"
        }

        Based on the images, determine the values for the JSON fields.
        - `is_the_same_room`: true if they depict the same room, otherwise false.
        - `is_clean`: true if the new picture shows a tidy room, otherwise false.
        - `is_picture_wide_enough`: true if the new picture captures the room well, false if it seems too cropped.
        """

        # Send the prompt and the images to the model
        response = model.generate_content([prompt, reference_img, new_img])

        # Clean up the response to ensure it's valid JSON.
        cleaned_response_text = response.text.strip().replace("```json", "").replace("```", "")
        
        # Parse the JSON string into a Python dictionary
        json_response = json.loads(cleaned_response_text)
        return json_response

    except json.JSONDecodeError:
        return {"error": "Failed to decode JSON from the API response.", "raw_response": response.text}
    except Exception as e:
        return {"error": f"An unexpected error occurred: {e}"}

# ---------- Analyse button ----------------------------------
if st.button("🧐 נתח את החדר", type="primary"):

    if not openai_api_key:
        st.error("יש להזין מפתח API של OpenAI.")
        st.stop()

    if not gemini_api_key:
        st.error("יש להזין מפתח API של Gemini.")
        st.stop()

    if latest_file is None:
        st.error("צלם תמונה חדשה של החדר תחילה.")
        st.stop()

    client = openai.OpenAI(api_key=openai_api_key)

    ref_b64 = file_to_b64(ref_bytes, ref_mime)
    latest_b64 = file_to_b64(latest_file.getvalue(), latest_file.type)

    system_prompt = (
        "You are an expert interior organiser.\n"
        "You check if a kid's room is clean or not by comparing two images.\n"
        "The kid will try to fool you by taking a photo of a different room or by hiding the mess.\n"
        "It will also try to take a very narrow photo of the room, so you must check that the photo shows big part of the room.\n"
        "Compare the two images.\n"
        "A room is not clean if there is a blanket on the floor\n",
        "When checking if the same room, make sure the picture shows the same furnitures\n",
        "If the picutre is too narrow, you must comment about it, and return is_too_narrow_photo as true.\n",
        "A picture is too narrow if it covers less area than the reference picutre. It must include at least two corners of the room.\n",
        "Respond ONLY with valid JSON: \n"
        "{\n"
        "  \"same_room\": true|false,\n"
        "  \"is_clean\": true|false,\n"
        "  \"is_too_narrow_photo\": true|false,  # if the latest photo is too narrow\n"
        "  \"suggestions\": [\"tip 1\", \"tip 2\"]\n"
        "}\n"
        "If the picture is too narrow, you must return is_too_narrow_photo as true and is_clean as false.\n",
        "If the picture covers less area than than the reference picture, you must return is_too_narrow_photo as true.\n",        
        "If is_clean is true, suggestions may be an empty array.\n",
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
    timestamp = datetime.now().isoformat()
    file_name = timestamp
    clean = False
    if not data.get("same_room", False):
        st.error("❗ נראה כי אלו אינם אותו חדר.")
        file_name += "_diff_room"
    elif not data.get("is_too_narrow_photo", True):
        st.error("❗ התמונה צרה מדי.")
        file_name += "_to_narrow_pic"

        
    else:
        if data.get("is_clean", False):
            st.success("✅ החדר נראה מסודר ונקי — כל הכבוד!")
            file_name += "_clean"
            try:
                Path("last_clean.txt").write_text(timestamp)
                clean = True
                #push_last_clean_to_github(timestamp, ["last_clean.txt"])
            except Exception as e:
                st.warning(f"⚠️ לא הצלחתי לעדכן last_clean.txt: {e}")
        else:
            st.warning("🧹 החדר אינו מסודר. הצעות לשיפור:")
            file_name += "not_clean"
            for tip in data.get("suggestions", []):
                st.markdown(f"- {tip}")

    file_name += ".jpg"
    # Save to disk
    with open(file_name, "wb") as f:
        f.write(latest_file.getvalue())
    files_to_push = [file_name]
    if clean:
        files_to_push.append("last_clean.txt")
    push_last_clean_to_github(files_to_push)

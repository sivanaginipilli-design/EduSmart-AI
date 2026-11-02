import json
import re
from io import BytesIO
from pathlib import Path

import streamlit as st
from groq import Groq
from pypdf import PdfReader
from docx import Document
from PIL import Image, ImageOps, ImageEnhance
import fitz  # PyMuPDF
import requests


# =========================================================
# PAGE CONFIGURATION
# =========================================================
st.set_page_config(
    page_title="EduSmart AI",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded",
)


# =========================================================
# OCR.SPACE CONFIGURATION
# =========================================================
OCR_SPACE_ENDPOINT = "https://api.ocr.space/parse/image"
OCR_MAX_FILE_BYTES = 950 * 1024  # Keep uploads below the free API's 1 MB limit.
OCR_MAX_PAGES_PER_IMPORT = 12   # Protect the shared public API quota.


# =========================================================
# CUSTOM DESIGN
# =========================================================
st.markdown(
    """
    <style>
    .stApp { background: #f4f7fc; color: #1e293b; }
    [data-testid="stHeader"] { background: #f4f7fc; }
    [data-testid="stSidebar"] {
        background: #e8f0fc;
        border-right: 1px solid #d5e1f2;
    }
    [data-testid="stSidebar"] * { color: #172554 !important; }
    h1, h2, h3, h4 {
        color: #173b70 !important;
        font-weight: 750 !important;
    }
    p, label, .stMarkdown { color: #26364b; }
    .stCaption { color: #64748b !important; }
    div.stButton > button {
        background: #2563eb;
        color: white !important;
        border: none;
        border-radius: 10px;
        font-weight: 700;
        padding: 10px 18px;
    }
    div.stButton > button:hover {
        background: #1d4ed8;
        color: white !important;
    }
    div[data-testid="stMetric"] {
        background: white;
        border: 1px solid #dbe5f1;
        border-radius: 14px;
        padding: 18px;
        box-shadow: 0 2px 8px rgba(30, 64, 120, 0.06);
    }
    div[data-testid="stTabs"] button {
        color: #173b70 !important;
        font-weight: 700;
    }
    div[data-testid="stTextArea"] textarea,
    div[data-testid="stTextInput"] input {
        background: white;
        color: #172554;
        border: 1px solid #cbd5e1;
        border-radius: 8px;
    }
    .stAlert { border-radius: 10px; }
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# SESSION STATE
# =========================================================
DEFAULTS = {
    "syllabus_text": "",
    "syllabus_editor": "",
    "topics": [],
    "quiz_questions": [],
    "quiz_checked": {},
    "uploaded_file_names": [],
}
for key, value in DEFAULTS.items():
    if key not in st.session_state:
        st.session_state[key] = value


# =========================================================
# AI CONNECTION
# =========================================================
def get_client(api_key: str) -> Groq:
    if not api_key or not api_key.strip():
        raise ValueError("Please enter your Groq API key.")
    return Groq(api_key=api_key.strip(), timeout=90.0, max_retries=2)


def ask_ai(client: Groq, prompt: str, model: str) -> str:
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are EduSmart AI, an expert educational teaching "
                    "assistant for university students. Give accurate, clear, "
                    "well-structured and syllabus-relevant answers. Do not "
                    "invent syllabus information. Explain difficult concepts "
                    "in simple academic language."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        max_completion_tokens=6000,
    )
    result = response.choices[0].message.content
    if not result:
        raise ValueError("AI returned an empty response.")
    return result.strip()


# =========================================================
# JSON EXTRACTION
# =========================================================
def extract_json(text: str):
    text = text.strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"\s*```$", "", text)

    start_obj = text.find("{")
    start_arr = text.find("[")
    starts = [pos for pos in (start_obj, start_arr) if pos >= 0]
    if not starts:
        raise ValueError("AI response did not contain valid JSON.")

    text = text[min(starts):]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Repair invalid backslash escapes occasionally returned by models.
        repaired = re.sub(r'\\(?!["\\/bfnrtu])', r"\\\\", text)
        return json.loads(repaired)


# =========================================================
# DOCUMENT EXTRACTION HELPERS - OCR.SPACE
# =========================================================
def get_secret(name: str) -> str:
    """Read a secret without ever displaying its value."""
    try:
        value = st.secrets.get(name, "")
        return str(value).strip() if value else ""
    except Exception:
        return ""


def prepare_image_for_ocr(image: Image.Image) -> Image.Image:
    """Light image cleanup before sending it to the OCR service."""
    image = ImageOps.exif_transpose(image).convert("RGB")
    image = ImageOps.grayscale(image)
    image = ImageOps.autocontrast(image)
    image = ImageEnhance.Sharpness(image).enhance(1.35)
    return image.convert("RGB")


def image_to_jpeg_under_limit(image: Image.Image) -> bytes:
    """Compress an image to fit the OCR.space free API's 1 MB file limit."""
    image = prepare_image_for_ocr(image)
    # Gradually reduce dimensions and JPEG quality until under the limit.
    for scale in (1.0, 0.85, 0.70, 0.55, 0.40):
        candidate = image
        if scale < 1.0:
            width = max(500, int(image.width * scale))
            height = max(500, int(image.height * scale))
            candidate = image.resize((width, height), Image.Resampling.LANCZOS)
        for quality in (88, 78, 68, 58):
            output = BytesIO()
            candidate.save(output, format="JPEG", quality=quality, optimize=True)
            data = output.getvalue()
            if len(data) <= OCR_MAX_FILE_BYTES:
                return data
    raise ValueError(
        "Image could not be compressed below 1 MB. Please upload a smaller or clearer image."
    )


def ocr_space_image(image_bytes: bytes, filename: str, api_key: str) -> str:
    """Send one image to OCR.space and return the recognized text."""
    if not api_key:
        raise RuntimeError(
            "OCR.space API key is not configured. Add OCR_SPACE_API_KEY in Streamlit Cloud Secrets."
        )
    if len(image_bytes) > OCR_MAX_FILE_BYTES:
        with Image.open(BytesIO(image_bytes)) as image:
            image_bytes = image_to_jpeg_under_limit(image)
        filename = Path(filename).stem + ".jpg"

    headers = {"apikey": api_key}
    files = {"file": (filename, image_bytes, "image/jpeg")}
    data = {
        "language": "eng",
        "isOverlayRequired": "false",
        "OCREngine": "2",
        "detectOrientation": "true",
        "scale": "true",
    }
    try:
        response = requests.post(
            OCR_SPACE_ENDPOINT,
            headers=headers,
            files=files,
            data=data,
            timeout=(15, 90),
        )
        response.raise_for_status()
        result = response.json()
    except requests.RequestException as exc:
        raise RuntimeError(f"OCR.space connection failed: {exc}") from exc
    except ValueError as exc:
        raise RuntimeError("OCR.space returned an unreadable response.") from exc

    if result.get("IsErroredOnProcessing"):
        message = result.get("ErrorMessage") or result.get("ErrorDetails") or "Unknown OCR error"
        if isinstance(message, list):
            message = "; ".join(str(item) for item in message)
        raise RuntimeError(f"OCR.space processing error: {message}")

    parsed_results = result.get("ParsedResults") or []
    text_parts = [
        item.get("ParsedText", "").strip()
        for item in parsed_results
        if item.get("ParsedText", "").strip()
    ]
    return "\n".join(text_parts).strip()


def image_to_text(image_bytes: bytes, api_key: str) -> str:
    try:
        with Image.open(BytesIO(image_bytes)) as image:
            jpeg_bytes = image_to_jpeg_under_limit(image)
        return ocr_space_image(jpeg_bytes, "uploaded_image.jpg", api_key)
    except Exception as exc:
        raise RuntimeError(f"Could not read image: {exc}") from exc


def pdf_to_text(file_bytes: bytes, ocr_api_key: str) -> tuple[str, str]:
    """Extract embedded PDF text; use OCR.space for pages that appear scanned."""
    extracted_pages = []
    ocr_pages = []
    reader = PdfReader(BytesIO(file_bytes))
    pdf_document = fitz.open(stream=file_bytes, filetype="pdf")
    ocr_page_count = 0

    try:
        for page_index, page in enumerate(reader.pages):
            page_text = (page.extract_text() or "").strip()
            if len(page_text) >= 30:
                extracted_pages.append(f"\n--- Page {page_index + 1} ---\n{page_text}")
                continue

            if page_index >= len(pdf_document):
                continue
            if ocr_page_count >= OCR_MAX_PAGES_PER_IMPORT:
                extracted_pages.append(
                    f"\n--- Page {page_index + 1} ---\n"
                    "[OCR page limit reached for this import. Upload remaining pages separately.]"
                )
                continue

            pix = pdf_document[page_index].get_pixmap(
                matrix=fitz.Matrix(1.8, 1.8), alpha=False
            )
            page_image = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            jpeg_bytes = image_to_jpeg_under_limit(page_image)
            ocr_text = ocr_space_image(
                jpeg_bytes, f"pdf_page_{page_index + 1}.jpg", ocr_api_key
            )
            ocr_page_count += 1
            if ocr_text:
                ocr_pages.append(page_index + 1)
                extracted_pages.append(
                    f"\n--- Page {page_index + 1} (OCR.space OCR) ---\n{ocr_text}"
                )
            else:
                extracted_pages.append(
                    f"\n--- Page {page_index + 1} ---\n[No readable text detected.]"
                )
    finally:
        pdf_document.close()

    note = (
        "OCR.space used on page(s): " + ", ".join(map(str, ocr_pages))
        if ocr_pages else "Embedded PDF text extracted; OCR was not needed."
    )
    return "\n".join(extracted_pages).strip(), note


def read_uploaded_file(uploaded_file, ocr_api_key: str) -> tuple[str, str]:
    """Read PDF, DOCX, TXT or image files. Returns (text, status_note)."""
    if uploaded_file is None:
        return "", "No file selected."

    filename = uploaded_file.name
    extension = Path(filename).suffix.lower()
    file_bytes = uploaded_file.getvalue()

    if len(file_bytes) > 15 * 1024 * 1024:
        raise ValueError("File is larger than the 15 MB per-file app limit.")

    if extension == ".pdf":
        return pdf_to_text(file_bytes, ocr_api_key)

    if extension == ".docx":
        document = Document(BytesIO(file_bytes))
        parts = [p.text for p in document.paragraphs if p.text.strip()]
        for table in document.tables:
            for row in table.rows:
                cells = [cell.text.strip() for cell in row.cells]
                if any(cells):
                    parts.append(" | ".join(cells))
        return "\n".join(parts).strip(), "Word document text extracted."

    if extension == ".txt":
        text = file_bytes.decode("utf-8-sig", errors="replace").strip()
        return text, "Text file imported."

    if extension in {".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp"}:
        return image_to_text(file_bytes, ocr_api_key), "Image text extracted using OCR.space."

    if extension == ".doc":
        raise ValueError(
            "The older .doc format is not supported directly. Open it in "
            "Microsoft Word or LibreOffice and save it as .docx, then upload."
        )
    raise ValueError(f"Unsupported file type: {extension}")


def import_multiple_files(uploaded_files, ocr_api_key: str) -> tuple[str, list[str], list[str]]:
    all_text, successful_files, errors = [], [], []
    for uploaded_file in uploaded_files:
        try:
            text, note = read_uploaded_file(uploaded_file, ocr_api_key)
            if text.strip():
                all_text.append(
                    f"\n\n========== FILE: {uploaded_file.name} ==========\n{text}"
                )
                successful_files.append(f"{uploaded_file.name} — {note}")
            else:
                errors.append(f"{uploaded_file.name}: No readable text was detected.")
        except Exception as exc:
            errors.append(f"{uploaded_file.name}: {exc}")
    return "\n".join(all_text).strip(), successful_files, errors


# =========================================================
# SIDEBAR
# =========================================================
with st.sidebar:
    st.markdown("## 🎓 EduSmart AI")
    st.caption("Smart Teaching & Learning Assistant")
    st.divider()

    st.markdown("### 🔐 AI Configuration")

    api_key = get_secret("GROQ_API_KEY")
    ocr_api_key = get_secret("OCR_SPACE_API_KEY")

    if api_key:
        st.success("Groq AI key configured")
    else:
        st.warning("GROQ_API_KEY is missing in Streamlit Secrets.")

    if ocr_api_key:
        st.success("OCR.space key configured")
    else:
        st.warning("OCR_SPACE_API_KEY is missing. Scanned PDFs/images need this key.")

    model = st.selectbox(
        "Select AI Model",
        [
            "openai/gpt-oss-20b",
            "openai/gpt-oss-120b",
        ],
        index=0,
    )

    st.divider()
    st.markdown("### 📂 Upload Syllabus")
    st.caption(
        "You can select multiple files together. Scanned PDFs and images "
        "are processed with OCR.space. Keep each file under 15 MB; OCR requests "
        "are subject to your OCR.space plan limits."
    )

    uploaded_files = st.file_uploader(
        "Choose syllabus files",
        type=["pdf", "docx", "doc", "txt", "png", "jpg", "jpeg", "tif", "tiff", "bmp", "webp"],
        accept_multiple_files=True,
        help="Supported: PDF, DOCX, TXT, PNG, JPG, JPEG, TIFF, BMP and WEBP. "
             "Old .doc files should be converted to .docx first.",
    )

    if st.button(
        "📥 Import Uploaded Syllabus",
        use_container_width=True,
        disabled=not uploaded_files,
    ):
        with st.spinner("Reading files and extracting syllabus text..."):
            combined_text, successful, errors = import_multiple_files(
                uploaded_files, ocr_api_key
            )

        if combined_text:
            st.session_state.syllabus_text = combined_text
            st.session_state.syllabus_editor = combined_text
            st.session_state.uploaded_file_names = [
                item.split(" — ")[0] for item in successful
            ]
            st.success(
                f"Imported {len(successful)} file(s) successfully."
            )
            st.rerun()
        else:
            st.error("No text could be extracted from the selected files.")

        for item in successful:
            st.caption(f"✓ {item}")
        for error in errors:
            st.warning(error)

    if st.session_state.uploaded_file_names:
        st.caption("Last imported files:")
        for filename in st.session_state.uploaded_file_names:
            st.write(f"• {filename}")

    st.divider()
    st.caption("EduSmart AI | Python + Streamlit + Groq + OCR.space")


# =========================================================
# HEADER
# =========================================================
st.markdown("# 🎓 EduSmart AI")
st.markdown("### Your Personal AI-Powered Study Assistant")
st.write("Upload your syllabus, select topics, and prepare for examinations.")
st.divider()


# =========================================================
# DASHBOARD METRICS
# =========================================================
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("Study Assistant", "Active")
with col2:
    st.metric("Available Answer Types", "4")
with col3:
    st.metric("Quiz Options", "4 per question")
st.divider()


# =========================================================
# SYLLABUS INPUT
# =========================================================
st.subheader("📚 Your Subject Syllabus")
st.caption(
    "Paste your syllabus below or upload multiple PDF, DOCX, TXT or image "
    "files from the sidebar."
)

syllabus = st.text_area(
    "Enter or review your syllabus",
    key="syllabus_editor",
    height=220,
    placeholder=(
        "Example:\n\n"
        "Subject: Machine Learning\n"
        "Unit 1: Introduction to Machine Learning\n"
        "Unit 2: Supervised Learning\n"
        "Unit 3: Unsupervised Learning\n"
        "Unit 4: Neural Networks\n"
        "Unit 5: Deep Learning"
    ),
)

if st.button("💾 Save Syllabus"):
    if syllabus.strip():
        st.session_state.syllabus_text = syllabus.strip()
        st.success("Syllabus saved successfully!")
    else:
        st.warning("Please enter or upload your syllabus.")

if st.session_state.syllabus_text:
    st.caption(
        f"Current syllabus length: {len(st.session_state.syllabus_text):,} characters"
    )

# =========================================================
# TOPIC SELECTION
# =========================================================
st.divider()
st.subheader("🔍 Topic Selection")

if st.button("✨ Automatically Extract Topics"):
    if not api_key:
        st.warning("Enter your Groq API key in the sidebar.")
    elif not st.session_state.syllabus_text.strip():
        st.warning("Please save or import your syllabus first.")
    else:
        try:
            client = get_client(api_key)
            prompt = f"""
Analyze the syllabus and extract its units and important topics.
Return ONLY valid JSON in this format:
{{
  "topics": ["Topic 1", "Topic 2", "Topic 3"]
}}

SYLLABUS:
{st.session_state.syllabus_text[:18000]}
"""
            with st.spinner("Analyzing syllabus and extracting topics..."):
                result = ask_ai(client, prompt, model)
                data = extract_json(result)

            topics = data.get("topics", [])
            cleaned_topics = [
                str(topic).strip()
                for topic in topics
                if str(topic).strip()
            ]

            if cleaned_topics:
                st.session_state.topics = list(dict.fromkeys(cleaned_topics))
                st.success(
                    f"{len(st.session_state.topics)} topics extracted!"
                )
            else:
                st.warning("No topics were found in the syllabus.")
        except Exception as exc:
            st.error(f"AI Error: {exc}")


manual_topics = st.text_input(
    "Add topics manually (separate multiple topics with commas)",
    placeholder="Example: Linear Regression, Decision Trees, Neural Networks",
)

if st.button("➕ Add Topics"):
    if manual_topics.strip():
        new_topics = [
            item.strip()
            for item in manual_topics.split(",")
            if item.strip()
        ]
        for topic in new_topics:
            if topic not in st.session_state.topics:
                st.session_state.topics.append(topic)
        st.success("Topics added successfully!")
    else:
        st.warning("Enter at least one topic.")

selected_topics = st.multiselect(
    "Select one or multiple topics",
    options=st.session_state.topics,
    placeholder="Choose your topics",
)

if selected_topics:
    st.info("Selected topics: " + ", ".join(selected_topics))


# =========================================================
# MAIN LEARNING TABS
# =========================================================
st.divider()
answer_tab, mcq_tab = st.tabs(
    ["📝 Generate Answers", "🎯 MCQ Practice Quiz"]
)


# =========================================================
# ANSWER GENERATION
# =========================================================
with answer_tab:
    st.subheader("📝 AI Answer Generator")

    answer_type = st.selectbox(
        "Select Answer Type",
        [
            "2 Marks Answer",
            "5 Marks Answer",
            "10 Marks Answer",
            "Detailed Answer",
        ],
    )

    if st.button("🚀 Generate Answer", use_container_width=True):
        if not api_key:
            st.warning("Enter your Groq API key in the sidebar.")
        elif not st.session_state.syllabus_text.strip():
            st.warning("Please provide your syllabus.")
        elif not selected_topics:
            st.warning("Please select at least one topic.")
        else:
            try:
                client = get_client(api_key)
                prompt = f"""
Act as an experienced university professor. Generate an accurate
examination answer based on the supplied syllabus.

SUBJECT SYLLABUS:
{st.session_state.syllabus_text[:18000]}

SELECTED TOPICS:
{", ".join(selected_topics)}

ANSWER TYPE:
{answer_type}

INSTRUCTIONS:
1. Keep the answer relevant to the supplied syllabus.
2. Explain every selected topic.
3. Use clear headings and subheadings.
4. Include important definitions.
5. Include examples wherever relevant.
6. Use bullet points when appropriate.
7. For 2 marks, keep the answer concise.
8. For 5 marks, provide a structured explanation.
9. For 10 marks, provide a detailed examination answer.
10. For Detailed Answer, explain concepts thoroughly.
11. Do not add unrelated topics.
12. Use simple academic English.
"""
                with st.spinner("Generating your answer..."):
                    answer = ask_ai(client, prompt, model)

                st.success("Answer generated successfully!")
                st.markdown("### 📖 Your Answer")
                st.markdown(answer)
                st.download_button(
                    "⬇️ Download Answer",
                    data=answer,
                    file_name="edusmart_answer.txt",
                    mime="text/plain",
                )
            except Exception as exc:
                st.error(f"AI Error: {exc}")
                st.info(
                    "Check your Groq API key, selected model availability, "
                    "internet connection and Groq usage limits."
                )


# =========================================================
# MCQ GENERATION AND QUIZ
# =========================================================
with mcq_tab:
    st.subheader("🎯 AI MCQ Practice")
    st.write(
        "Generate multiple-choice questions from your selected topics."
    )

    number_of_questions = st.slider(
        "Number of Questions",
        min_value=5,
        max_value=20,
        value=5,
    )

    if st.button("🎯 Generate MCQ Quiz", use_container_width=True):
        if not api_key:
            st.warning("Enter your Groq API key in the sidebar.")
        elif not st.session_state.syllabus_text.strip():
            st.warning("Please provide your syllabus.")
        elif not selected_topics:
            st.warning("Please select at least one topic.")
        else:
            try:
                client = get_client(api_key)
                prompt = f"""
You are an expert university examination question setter.
Generate exactly {number_of_questions} MCQs.

SUBJECT SYLLABUS:
{st.session_state.syllabus_text[:18000]}

SELECTED TOPICS:
{", ".join(selected_topics)}

REQUIREMENTS:
1. Every question must be relevant to the syllabus.
2. Each question must have exactly FOUR options.
3. Each question must have exactly ONE correct answer.
4. Avoid duplicate questions.
5. Include a clear explanation.
6. Use a mixture of easy, medium and difficult questions.
7. Correct answer must exactly match one of the four options.

Return ONLY valid JSON:
{{
  "questions": [
    {{
      "question": "Question text?",
      "options": ["Option A", "Option B", "Option C", "Option D"],
      "correct_answer": "Option A",
      "explanation": "Explanation of the correct answer."
    }}
  ]
}}
"""
                with st.spinner("Generating MCQ questions..."):
                    result = ask_ai(client, prompt, model)
                    data = extract_json(result)

                questions = data.get("questions", [])
                valid_questions = []

                for question in questions:
                    options = question.get("options", [])
                    correct_answer = str(
                        question.get("correct_answer", "")
                    ).strip()

                    if (
                        isinstance(options, list)
                        and len(options) == 4
                        and correct_answer in [str(x).strip() for x in options]
                        and question.get("question")
                    ):
                        valid_questions.append(
                            {
                                "question": str(question["question"]),
                                "options": [str(x).strip() for x in options],
                                "correct_answer": correct_answer,
                                "explanation": str(
                                    question.get("explanation", "")
                                ),
                            }
                        )

                if not valid_questions:
                    st.error(
                        "AI did not generate valid MCQs. Please try again."
                    )
                else:
                    st.session_state.quiz_questions = valid_questions
                    st.session_state.quiz_checked = {}
                    st.success(
                        f"{len(valid_questions)} MCQs generated!"
                    )
            except Exception as exc:
                st.error(f"MCQ Generation Error: {exc}")

    if st.session_state.quiz_questions:
        st.divider()
        st.markdown("### 📋 Your Practice Questions")

        for index, question in enumerate(
            st.session_state.quiz_questions
        ):
            st.markdown(f"#### Question {index + 1}")
            st.write(question["question"])

            selected_option = st.radio(
                "Choose your answer:",
                question["options"],
                key=f"answer_{index}",
                index=None,
            )

            if st.button("Check Answer", key=f"check_{index}"):
                if selected_option is None:
                    st.warning("Please select an option.")
                else:
                    st.session_state.quiz_checked[index] = (
                        selected_option == question["correct_answer"]
                    )

            if index in st.session_state.quiz_checked:
                if st.session_state.quiz_checked[index]:
                    st.success("✅ Correct Answer!")
                else:
                    st.error("❌ Wrong Answer!")
                    st.info(
                        "Correct Answer: " + question["correct_answer"]
                    )

                st.markdown(
                    "**Explanation:** " + question["explanation"]
                )
            st.divider()

        total_questions = len(st.session_state.quiz_questions)
        attempted = len(st.session_state.quiz_checked)
        score = sum(
            1
            for value in st.session_state.quiz_checked.values()
            if value
        )

        col1, col2, col3 = st.columns(3)
        col1.metric("Total Questions", total_questions)
        col2.metric("Attempted", attempted)
        col3.metric("Correct Answers", score)

        if attempted == total_questions:
            st.success(
                f"Quiz completed! Your score: {score}/{total_questions}"
            )


# =========================================================
# FOOTER
# =========================================================
st.divider()
st.markdown(
    """
    <center>
      <p style="color:#94a3b8;">
        🎓 EduSmart AI | Smart Teaching and Learning Assistant
      </p>
      <p style="color:#64748b;">
        Built with Python, Streamlit, Groq AI and OCR
      </p>
    </center>
    """,
    unsafe_allow_html=True,
)

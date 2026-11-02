# EduSmart AI

AI-powered syllabus study assistant built with Python, Streamlit, Groq and OCR.space.

## Features
- Paste or upload a syllabus.
- Multiple PDF, DOCX, TXT and image uploads.
- Extract embedded text from regular PDFs; use OCR.space for scanned PDF pages and images.
- Automatically extract topics and add topics manually.
- Generate 2-mark, 5-mark, 10-mark and detailed answers.
- Generate MCQ quizzes with four options, answer checking and explanations.
- Download generated answers.

## Files
- `edusmart_ai_app.py` — Streamlit application.
- `requirements.txt` — Python dependencies.

## Run locally
1. Install Python 3.10 or newer.
2. In the project folder, create `.streamlit/secrets.toml`.
3. Add the following (replace the placeholders; never commit real keys):

   ```toml
   GROQ_API_KEY = "your_groq_api_key"
   OCR_SPACE_API_KEY = "your_ocr_space_api_key"
   ```

4. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

5. Run:

   ```bash
   streamlit run edusmart_ai_app.py
   ```

## Deploy on GitHub + Streamlit Community Cloud
1. Push `edusmart_ai_app.py`, `requirements.txt`, and this README to a GitHub repository.
2. Do **not** upload `.streamlit/secrets.toml` or real API keys.
3. In Streamlit Community Cloud, create/deploy the app from the repository and select `edusmart_ai_app.py` as the main file.
4. Open the app's **Settings / Secrets** area and add:

   ```toml
   GROQ_API_KEY = "your_groq_api_key"
   OCR_SPACE_API_KEY = "your_ocr_space_api_key"
   ```

5. Save/reboot the app. The keys are read server-side from `st.secrets`; visitors do not need to enter their own keys.

## Upload and OCR notes
- Supported uploads: PDF, DOCX, TXT, PNG, JPG/JPEG, TIFF, BMP and WEBP.
- Legacy `.doc` files must be saved as `.docx` first.
- App upload limit: 15 MB per file.
- Scanned PDF pages are rendered and sent one page at a time to OCR.space. The app limits OCR to 12 scanned pages per import to help protect the shared API quota.
- OCR.space free API currently documents a 1 MB per-file limit, 25,000 requests/month and a 500-request/day/IP rate limit. Images are compressed before sending. Every scanned page/image uses an OCR request; ordinary text PDFs and DOCX/TXT files do not need OCR.
- The app currently requests English OCR (`eng`). OCR accuracy depends on image clarity, layout and language.
- Since the deployed app uses your API keys for all visitors, public usage can consume your Groq and OCR.space quotas. Monitor usage and disable/restrict the public app if needed.

## Security
- Keep API keys only in Streamlit Secrets or a local untracked `.streamlit/secrets.toml` file.
- Never paste API keys into source code, screenshots, public issues, or GitHub commits.
- If a key is accidentally published, revoke/rotate it in its provider dashboard.

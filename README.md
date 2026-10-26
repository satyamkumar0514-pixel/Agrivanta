# AgriVanta — From Soil to Sale

A runnable bilingual (English/Hindi) college mini-project for farmer support. It includes a responsive web UI, FastAPI backend, SQLite persistence, crop recommendations, soil/water report storage, weather lookup, and an optional Gemini-powered farming assistant.

## Requirements
- Python 3.10+
- Internet access for installing dependencies and live weather / AI integrations (the website itself runs locally)
- A Gemini API key is optional; add it to `.env` to enable live AI responses. SMTP settings are optional for emailing password-reset tokens.

## Run locally

### Windows (PowerShell)
```powershell
cd agrivanta
py -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000

### macOS / Linux
```bash
cd agrivanta
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --reload
```
Open http://127.0.0.1:8000

API docs: http://127.0.0.1:8000/docs

## AI setup (optional)
1. Obtain a Gemini API key from Google AI Studio.
2. Put it in `.env` as `GEMINI_API_KEY=your_key_here`.
3. Set `GEMINI_MODEL=gemini-2.5-flash` or another currently available model that supports text generation.
4. Restart the server.

Without a key, the assistant uses a clearly identified local demo response. It does not pretend that the response came from a live AI model.

## Account pages and password reset
- New sign-in and registration screens with server-side validation and salted scrypt password hashes.
- Forgot-password flow creates a single-use reset token that expires after 30 minutes.
- Configure `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, and `SMTP_FROM` in `.env` to email reset tokens.
- In local development (`APP_ENV=development`), if SMTP is not configured, the reset token is shown in the app for testing. Set `APP_ENV=production` before any public deployment.
- The account screen is a college-project starter, not production-ready access control. Protect all data APIs with server-side sessions/authorization before deploying publicly.

## What works
- Bilingual English/Hindi UI toggle.
- Soil test entry and SQLite persistence.
- Water test entry and SQLite persistence.
- Rule-based crop suggestions from farm details and soil pH.
- Farming assistant with optional Gemini REST API integration.
- Current weather lookup through Open-Meteo using a searched city/place name.
- Recent reports shown in the dashboard.
- API documentation and health endpoint.

## Project limitations
- Soil and water values are farmer-entered/lab-reported; this app does not physically test samples.
- Crop suggestions are educational decision support, not a guarantee of yield or profit.
- Crop-health photo analysis, live market prices, and verified buyer discovery are not implemented as live integrations in this first runnable version. They are presented as future modules rather than fake live services.
- Weather geocoding and forecast require internet access.
- Do not deploy publicly without adding production-grade authentication, HTTPS, CSRF/session hardening, rate limiting, backups, and review of data privacy/security.

## Project structure
```text
agrivanta/
├── app/
│   ├── main.py
│   └── static/
│       ├── index.html
│       ├── styles.css
│       └── app.js
├── tests/
│   └── test_app.py
├── .env.example
├── requirements.txt
└── README.md
```

## Gemini API
1. Create an API key in Google AI Studio.
2. Add `GEMINI_API_KEY=your_key_here` to your private `.env` file (never commit this file).
3. Optionally set `GEMINI_MODEL=gemini-2.5-flash`.
4. Restart Uvicorn. The Farming Assistant sends requests through the FastAPI backend, so the key is not exposed in browser JavaScript.

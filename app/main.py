from __future__ import annotations

import os
import re
import sqlite3
import hashlib
import hmac
import secrets
import smtplib
from email.message import EmailMessage
from datetime import timedelta
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
DB_PATH = Path(os.getenv("AGRIVANTA_DB", str(BASE_DIR / "agrivanta.db")))
if not DB_PATH.is_absolute():
    DB_PATH = BASE_DIR / DB_PATH

app = FastAPI(
    title="AgriVanta API",
    description="Bilingual agricultural decision-support mini-project API",
    version="1.0.0",
)
STATIC_DIR = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect_db()) as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS soil_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            farmer_name TEXT NOT NULL,
            location TEXT NOT NULL,
            crop TEXT NOT NULL,
            ph REAL,
            nitrogen REAL,
            phosphorus REAL,
            potassium REAL,
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS water_tests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            farmer_name TEXT NOT NULL,
            location TEXT NOT NULL,
            source TEXT NOT NULL,
            ph REAL,
            ec REAL,
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS recommendations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            location TEXT NOT NULL,
            season TEXT NOT NULL,
            soil_type TEXT NOT NULL,
            water TEXT NOT NULL,
            farm_size REAL,
            budget REAL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS password_resets (
            email TEXT PRIMARY KEY COLLATE NOCASE,
            token_hash TEXT NOT NULL,
            expires_at TEXT NOT NULL
        );
        """)
        conn.commit()


@app.on_event("startup")
def startup() -> None:
    init_db()


class SoilTestIn(BaseModel):
    farmer_name: str = Field(min_length=1, max_length=100)
    location: str = Field(min_length=1, max_length=160)
    crop: str = Field(min_length=1, max_length=100)
    ph: Optional[float] = Field(default=None, ge=0, le=14)
    nitrogen: Optional[float] = Field(default=None, ge=0, le=10000)
    phosphorus: Optional[float] = Field(default=None, ge=0, le=10000)
    potassium: Optional[float] = Field(default=None, ge=0, le=10000)
    notes: str = Field(default="", max_length=1000)


class WaterTestIn(BaseModel):
    farmer_name: str = Field(min_length=1, max_length=100)
    location: str = Field(min_length=1, max_length=160)
    source: str = Field(min_length=1, max_length=100)
    ph: Optional[float] = Field(default=None, ge=0, le=14)
    ec: Optional[float] = Field(default=None, ge=0, le=100000)
    notes: str = Field(default="", max_length=1000)


class CropAdvisorIn(BaseModel):
    location: str = Field(min_length=1, max_length=160)
    season: str = Field(min_length=1, max_length=40)
    soil_type: str = Field(min_length=1, max_length=80)
    water: str = Field(min_length=1, max_length=80)
    ph: Optional[float] = Field(default=None, ge=0, le=14)
    farm_size: Optional[float] = Field(default=None, ge=0)
    budget: Optional[float] = Field(default=None, ge=0)


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    language: str = Field(default="en", pattern="^(en|hi)$")


class RegisterIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=8, max_length=128)


class LoginIn(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    password: str = Field(min_length=1, max_length=128)


class ForgotPasswordIn(BaseModel):
    email: str = Field(min_length=5, max_length=254)


class ResetPasswordIn(BaseModel):
    email: str = Field(min_length=5, max_length=254)
    token: str = Field(min_length=20, max_length=200)
    new_password: str = Field(min_length=8, max_length=128)


def normalize_email(email: str) -> str:
    email = email.strip().lower()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise HTTPException(status_code=422, detail="Enter a valid email address.")
    return email


def password_hash(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=2**14, r=8, p=1)
    return salt.hex() + "$" + digest.hex()


def verify_password(password: str, stored: str) -> bool:
    try:
        salt_hex, digest_hex = stored.split("$", 1)
        candidate = password_hash(password, bytes.fromhex(salt_hex)).split("$", 1)[1]
        return hmac.compare_digest(candidate, digest_hex)
    except (ValueError, TypeError):
        return False


def send_reset_email(email: str, token: str) -> bool:
    host = os.getenv("SMTP_HOST", "").strip()
    if not host:
        return False
    msg = EmailMessage()
    msg["Subject"] = "AgriVanta password reset"
    msg["From"] = os.getenv("SMTP_FROM", os.getenv("SMTP_USER", ""))
    msg["To"] = email
    base_url = os.getenv("APP_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
    msg.set_content(
        "A password reset was requested for your AgriVanta account.\\n\\n"
        f"Reset token: {token}\\n"
        f"Open {base_url} and choose Forgot password to enter this token.\\n"
        "This token expires in 30 minutes. If you did not request this, ignore this email."
    )
    port = int(os.getenv("SMTP_PORT", "587"))
    with smtplib.SMTP(host, port, timeout=15) as server:
        server.starttls()
        user = os.getenv("SMTP_USER", "")
        password = os.getenv("SMTP_PASSWORD", "")
        if user:
            server.login(user, password)
        server.send_message(msg)
    return True


@app.post("/api/auth/register", status_code=201)
def register(data: RegisterIn):
    email = normalize_email(data.email)
    if not re.search(r"[A-Za-z]", data.password) or not re.search(r"\d", data.password):
        raise HTTPException(status_code=422, detail="Password must include at least one letter and one number.")
    with closing(connect_db()) as conn:
        try:
            cur = conn.execute(
                "INSERT INTO users (name, email, password_hash, created_at) VALUES (?, ?, ?, ?)",
                (data.name.strip(), email, password_hash(data.password), now_iso()),
            )
            conn.commit()
        except sqlite3.IntegrityError:
            raise HTTPException(status_code=409, detail="An account with this email already exists.")
    return {"message": "Account created successfully.", "user": {"id": cur.lastrowid, "name": data.name.strip(), "email": email}}


@app.post("/api/auth/login")
def login(data: LoginIn):
    email = normalize_email(data.email)
    with closing(connect_db()) as conn:
        user = conn.execute("SELECT id, name, email, password_hash FROM users WHERE email=?", (email,)).fetchone()
    if not user or not verify_password(data.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid email or password.")
    return {"message": "Login successful.", "user": {"id": user["id"], "name": user["name"], "email": user["email"]}}


@app.post("/api/auth/forgot-password")
def forgot_password(data: ForgotPasswordIn):
    email = normalize_email(data.email)
    # Always return a generic response so account existence is not disclosed.
    with closing(connect_db()) as conn:
        user = conn.execute("SELECT email FROM users WHERE email=?", (email,)).fetchone()
        if user:
            token = secrets.token_urlsafe(32)
            expires = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat(timespec="seconds")
            conn.execute(
                "INSERT INTO password_resets(email, token_hash, expires_at) VALUES (?, ?, ?) "
                "ON CONFLICT(email) DO UPDATE SET token_hash=excluded.token_hash, expires_at=excluded.expires_at",
                (email, hashlib.sha256(token.encode()).hexdigest(), expires),
            )
            conn.commit()
            try:
                sent = send_reset_email(email, token)
            except (OSError, smtplib.SMTPException, ValueError):
                sent = False
            # Development-only convenience; never expose reset tokens in production.
            if not sent and os.getenv("APP_ENV", "development").lower() == "development":
                return {"message": "If the account exists, reset instructions are ready. Development reset token shown below.", "dev_token": token}
    return {"message": "If an account exists for that email, password reset instructions will be sent."}


@app.post("/api/auth/reset-password")
def reset_password(data: ResetPasswordIn):
    email = normalize_email(data.email)
    token_hash = hashlib.sha256(data.token.encode()).hexdigest()
    with closing(connect_db()) as conn:
        record = conn.execute("SELECT token_hash, expires_at FROM password_resets WHERE email=?", (email,)).fetchone()
        if not record or not hmac.compare_digest(token_hash, record["token_hash"]):
            raise HTTPException(status_code=400, detail="Invalid or expired reset token.")
        expires = datetime.fromisoformat(record["expires_at"])
        if expires < datetime.now(timezone.utc):
            conn.execute("DELETE FROM password_resets WHERE email=?", (email,))
            conn.commit()
            raise HTTPException(status_code=400, detail="Reset token has expired. Request a new one.")
        conn.execute("UPDATE users SET password_hash=? WHERE email=?", (password_hash(data.new_password), email))
        conn.execute("DELETE FROM password_resets WHERE email=?", (email,))
        conn.commit()
    return {"message": "Password updated. You can now sign in."}


def row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


@app.get("/", include_in_schema=False)
def home():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/api/health")
def health():
    return {"status": "ok", "service": "AgriVanta API", "ai_configured": bool(os.getenv("GEMINI_API_KEY"))}


@app.get("/api/dashboard")
def dashboard():
    with closing(connect_db()) as conn:
        soil_count = conn.execute("SELECT COUNT(*) FROM soil_tests").fetchone()[0]
        water_count = conn.execute("SELECT COUNT(*) FROM water_tests").fetchone()[0]
        rec_count = conn.execute("SELECT COUNT(*) FROM recommendations").fetchone()[0]
        recent_soil = conn.execute("SELECT id, farmer_name, location, crop, ph, created_at FROM soil_tests ORDER BY id DESC LIMIT 3").fetchall()
        recent_water = conn.execute("SELECT id, farmer_name, location, source, ph, ec, created_at FROM water_tests ORDER BY id DESC LIMIT 3").fetchall()
    return {
        "counts": {"soil_tests": soil_count, "water_tests": water_count, "recommendations": rec_count},
        "recent_soil": [row_to_dict(r) for r in recent_soil],
        "recent_water": [row_to_dict(r) for r in recent_water],
    }


@app.post("/api/soil-tests", status_code=201)
def create_soil_test(data: SoilTestIn):
    created = now_iso()
    with closing(connect_db()) as conn:
        cur = conn.execute(
            """INSERT INTO soil_tests
            (farmer_name, location, crop, ph, nitrogen, phosphorus, potassium, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (data.farmer_name, data.location, data.crop, data.ph, data.nitrogen,
             data.phosphorus, data.potassium, data.notes, created),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM soil_tests WHERE id=?", (cur.lastrowid,)).fetchone()
    return {"message": "Soil report saved", "report": row_to_dict(row)}


@app.get("/api/soil-tests")
def list_soil_tests():
    with closing(connect_db()) as conn:
        rows = conn.execute("SELECT * FROM soil_tests ORDER BY id DESC LIMIT 100").fetchall()
    return {"items": [row_to_dict(r) for r in rows]}


@app.post("/api/water-tests", status_code=201)
def create_water_test(data: WaterTestIn):
    created = now_iso()
    with closing(connect_db()) as conn:
        cur = conn.execute(
            """INSERT INTO water_tests
            (farmer_name, location, source, ph, ec, notes, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (data.farmer_name, data.location, data.source, data.ph, data.ec, data.notes, created),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM water_tests WHERE id=?", (cur.lastrowid,)).fetchone()
    return {"message": "Water report saved", "report": row_to_dict(row)}


@app.get("/api/water-tests")
def list_water_tests():
    with closing(connect_db()) as conn:
        rows = conn.execute("SELECT * FROM water_tests ORDER BY id DESC LIMIT 100").fetchall()
    return {"items": [row_to_dict(r) for r in rows]}


def crop_suggestions(data: CropAdvisorIn) -> list[dict]:
    # Transparent starter rules for educational use, not a validated agronomic model.
    season = data.season.lower()
    soil = data.soil_type.lower()
    water = data.water.lower()
    ph = data.ph
    suggestions = []
    if season in ("kharif", "monsoon", "खरीफ", "मानसून"):
        suggestions.extend([
            {"crop": "Rice (धान)", "reason": "Often grown in the monsoon season where water is sufficient.", "duration": "Usually around 100–150 days, depending on variety", "caution": "Needs suitable water management and locally appropriate variety."},
            {"crop": "Maize (मक्का)", "reason": "Can suit many monsoon farming systems with suitable drainage.", "duration": "Often around 80–110 days", "caution": "Variety, rainfall and local soil conditions matter."},
        ])
    elif season in ("rabi", "winter", "रबी", "सर्दी"):
        suggestions.extend([
            {"crop": "Wheat (गेहूँ)", "reason": "A common winter crop in many northern Indian farming regions.", "duration": "Often around 110–150 days", "caution": "Needs region-appropriate variety and irrigation planning."},
            {"crop": "Mustard (सरसों)", "reason": "A common winter oilseed option in suitable areas.", "duration": "Often around 100–140 days", "caution": "Check local climate and soil recommendations."},
        ])
    else:
        suggestions.extend([
            {"crop": "Green gram / Moong (मूंग)", "reason": "A short-duration pulse option in suitable local seasons.", "duration": "Often around 60–75 days", "caution": "Confirm the locally recommended sowing window."},
            {"crop": "Vegetables (सब्ज़ियाँ)", "reason": "A broad category that may fit small farms with a reliable market.", "duration": "Varies by crop", "caution": "Select a specific crop after checking local demand, water and season."},
        ])
    if "sandy" in soil or "बलुई" in soil:
        suggestions.append({"crop": "Pearl millet / Bajra (बाजरा)", "reason": "Can tolerate relatively dry conditions in suitable regions.", "duration": "Often around 75–100 days", "caution": "Confirm local suitability and seed variety."})
    if ph is not None and (ph < 5.5 or ph > 8.5):
        for item in suggestions:
            item["caution"] += " The entered soil pH is outside a commonly preferred range for many crops; obtain a lab interpretation before planting."
    if water in ("low", "limited", "कम", "सीमित"):
        suggestions = [s for s in suggestions if "Rice" not in s["crop"]]
        suggestions.append({"crop": "Millets / मोटे अनाज", "reason": "Some millet crops can be options where water is limited.", "duration": "Depends on crop and variety", "caution": "Choose only after checking local agronomy guidance."})
    return suggestions[:4]


@app.post("/api/crop-advisor")
def crop_advisor(data: CropAdvisorIn):
    suggestions = crop_suggestions(data)
    result = {
        "location": data.location,
        "season": data.season,
        "notice": "Educational starter recommendations based on simple rules. Confirm with local Krishi Vigyan Kendra/agriculture officer before investing.",
        "suggestions": suggestions,
        "inputs": data.model_dump(),
    }
    with closing(connect_db()) as conn:
        conn.execute(
            """INSERT INTO recommendations
            (location, season, soil_type, water, farm_size, budget, result_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (data.location, data.season, data.soil_type, data.water, data.farm_size, data.budget,
             __import__("json").dumps(result, ensure_ascii=False), now_iso()),
        )
        conn.commit()
    return result


@app.get("/api/weather")
async def weather(location: str):
    location = location.strip()
    if not location or len(location) > 160:
        raise HTTPException(status_code=422, detail="Enter a valid city or village name.")
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            geo = await client.get(
                "https://geocoding-api.open-meteo.com/v1/search",
                params={"name": location, "count": 1, "language": "en", "format": "json"},
            )
            geo.raise_for_status()
            places = geo.json().get("results") or []
            if not places:
                raise HTTPException(status_code=404, detail="Location not found. Try a nearby city or district.")
            place = places[0]
            forecast = await client.get(
                "https://api.open-meteo.com/v1/forecast",
                params={
                    "latitude": place["latitude"], "longitude": place["longitude"],
                    "current": "temperature_2m,relative_humidity_2m,apparent_temperature,is_day,precipitation,weather_code,wind_speed_10m",
                    "daily": "temperature_2m_max,temperature_2m_min,precipitation_probability_max",
                    "forecast_days": 3, "timezone": "auto",
                },
            )
            forecast.raise_for_status()
            data = forecast.json()
        return {
            "source": "Open-Meteo",
            "place": f'{place.get("name", location)}, {place.get("admin1", "")}, {place.get("country", "")}'.strip(", "),
            "current": data.get("current", {}),
            "daily": data.get("daily", {}),
            "timezone": data.get("timezone"),
        }
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        raise HTTPException(status_code=502, detail="Weather service is temporarily unavailable. Please try again later.") from exc


@app.post("/api/assistant")
async def assistant(data: ChatIn):
    key = os.getenv("GEMINI_API_KEY", "").strip()
    model = os.getenv("GEMINI_MODEL", "gemini-2.5-flash").strip()
    if not key:
        answer = (
            "AI is not configured yet. Add GEMINI_API_KEY to the .env file and restart the server. "
            "For now, please share your crop, location, season, soil report and water availability with a local agriculture officer."
            if data.language == "en" else
            "AI अभी कॉन्फ़िगर नहीं है। .env फ़ाइल में GEMINI_API_KEY जोड़ें और सर्वर दोबारा शुरू करें। "
            "फिलहाल अपनी फसल, स्थान, मौसम, मिट्टी की रिपोर्ट और पानी की उपलब्धता की जानकारी स्थानीय कृषि अधिकारी से साझा करें।"
        )
        return {"answer": answer, "mode": "setup_required", "provider": None}
    prompt = (
        "You are AgriVanta, a cautious agricultural information assistant for Indian farmers. "
        "Answer in " + ("Hindi" if data.language == "hi" else "English") + ". "
        "Be practical, clear, and concise. Do not guarantee yield or profit. Do not invent local weather, market prices, "
        "laboratory results, official recommendations, or citations. Ask for missing context when needed. "
        "For pesticide, fertilizer dosage, disease diagnosis, or high-risk decisions, advise following product labels "
        "and consulting a qualified local agricultural expert/KVK. State uncertainty clearly.\n\nFarmer question: " + data.message
    )
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    try:
        async with httpx.AsyncClient(timeout=25) as client:
            response = await client.post(
                url,
                params={"key": key},
                json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"temperature": 0.35, "maxOutputTokens": 700}},
            )
        if response.status_code >= 400:
            raise HTTPException(status_code=502, detail="AI provider rejected the request. Check the model name, API key and quota.")
        payload = response.json()
        answer = payload["candidates"][0]["content"]["parts"][0]["text"]
        return {"answer": answer, "mode": "live_ai", "provider": "Gemini"}
    except HTTPException:
        raise
    except (httpx.HTTPError, ValueError, KeyError, IndexError) as exc:
        raise HTTPException(status_code=502, detail="AI service is temporarily unavailable. Please try again later.") from exc


@app.get("/api/recommendations")
def recommendations():
    with closing(connect_db()) as conn:
        rows = conn.execute("SELECT id, location, season, soil_type, water, created_at FROM recommendations ORDER BY id DESC LIMIT 10").fetchall()
    return {"items": [row_to_dict(r) for r in rows]}


@app.get("/api/reports")
def reports():
    with closing(connect_db()) as conn:
        soil = conn.execute("SELECT id, 'Soil' AS type, farmer_name AS name, location, crop AS detail, created_at FROM soil_tests ORDER BY id DESC LIMIT 10").fetchall()
        water = conn.execute("SELECT id, 'Water' AS type, farmer_name AS name, location, source AS detail, created_at FROM water_tests ORDER BY id DESC LIMIT 10").fetchall()
    combined = [row_to_dict(r) for r in soil] + [row_to_dict(r) for r in water]
    combined.sort(key=lambda x: x["created_at"], reverse=True)
    return {"items": combined[:15]}


# Simple local app: do not expose this database-backed demo publicly without authentication/authorization.

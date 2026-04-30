from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
from pymongo import MongoClient
from datetime import datetime, timezone
import os
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── Env Variables (set these in Render Dashboard → Environment) ──────────────
MONGO_URI    = os.getenv("MONGO_URI")
CHANNEL_ID   = os.getenv("CHANNEL_ID")
READ_API_KEY = os.getenv("READ_API_KEY")

if not MONGO_URI or not CHANNEL_ID or not READ_API_KEY:
    raise ValueError("Missing one or more environment variables: MONGO_URI, CHANNEL_ID, READ_API_KEY")

# ── MongoDB ───────────────────────────────────────────────────────────────────
try:
    client     = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db         = client["ai_stethoscope"]
    collection = db["patients"]
    client.admin.command("ping")   # fail fast if credentials are wrong
    print("✅ MongoDB connected")
except Exception as e:
    print(f"❌ MongoDB connection failed: {e}")
    collection = None

# ── Valid Ranges ──────────────────────────────────────────────────────────────
VALID_RANGES = {
    "heart_rate": (20, 300),
    "temp":       (30.0, 45.0),
    "spo2":       (50, 100),
    "sys":        (50, 300),
    "dia":        (20, 200),
}

def validate_vitals(hr, temp, spo2, sys, dia):
    checks = {
        "heart_rate": hr,
        "temp":       temp,
        "spo2":       spo2,
        "sys":        sys,
        "dia":        dia,
    }
    for field, value in checks.items():
        lo, hi = VALID_RANGES[field]
        if not (lo <= value <= hi):
            return False, f"Invalid {field}: {value} (expected {lo}–{hi})"
    return True, None

def calculate_risk(hr, temp, spo2, sys, dia):
    score = 0
    if hr > 140:   score += 3
    elif hr > 120: score += 2
    elif hr > 100: score += 1

    if temp > 39.5:   score += 3
    elif temp > 38.5: score += 2
    elif temp > 37.5: score += 1

    if spo2 < 85:   score += 3
    elif spo2 < 90: score += 2
    elif spo2 < 95: score += 1

    if sys > 180 or dia > 120:  score += 3
    elif sys > 140 or dia > 90: score += 2
    elif sys > 120 or dia > 80: score += 1

    if score >= 8:   return "CRITICAL RISK"
    elif score >= 5: return "HIGH RISK"
    elif score >= 2: return "MEDIUM RISK"
    else:            return "LOW RISK"

# ── FIX: Safe parser — returns None instead of crashing ──────────────────────
def safe_float(value):
    try:
        if value is None or str(value).strip() == "":
            return None
        return float(value)
    except (ValueError, TypeError):
        return None

# ── Health check (also prevents Render spin-down if pinged regularly) ─────────
@app.get("/")
def home():
    return {"status": "Backend running"}

@app.get("/live")
def live():
    url = (
        f"https://api.thingspeak.com/channels/{CHANNEL_ID}"
        f"/feeds/last.json?api_key={READ_API_KEY}"
    )

    # ── Step 1: Fetch from ThingSpeak ─────────────────────────────────────────
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        data = res.json()
    except requests.exceptions.Timeout:
        return {"error": "ThingSpeak request timed out — retrying soon"}
    except requests.exceptions.HTTPError as e:
        return {"error": f"ThingSpeak HTTP error: {e.response.status_code}"}
    except Exception as e:
        return {"error": f"Failed to reach ThingSpeak: {str(e)}"}

    # ── Step 2: Parse all 5 fields safely ─────────────────────────────────────
    fields = {
        "heart_rate": safe_float(data.get("field1")),
        "temp":       safe_float(data.get("field2")),
        "spo2":       safe_float(data.get("field3")),
        "sys":        safe_float(data.get("field4")),
        "dia":        safe_float(data.get("field5")),
    }

    # ── Step 3: Report exactly which fields are missing ───────────────────────
    missing = [k for k, v in fields.items() if v is None]
    if missing:
        return {
            "error":          "Sensor not ready yet — waiting for data",
            "missing_fields": missing,
            "raw_from_thingspeak": {
                f"field{i+1}": data.get(f"field{i+1}")
                for i in range(5)
            }
        }

    hr   = fields["heart_rate"]
    temp = fields["temp"]
    spo2 = fields["spo2"]
    sys  = fields["sys"]
    dia  = fields["dia"]

    # ── Step 4: Validate ranges ───────────────────────────────────────────────
    ok, err = validate_vitals(hr, temp, spo2, sys, dia)
    if not ok:
        return {"error": f"Sensor data out of range: {err}"}

    # ── Step 5: Score risk ────────────────────────────────────────────────────
    risk = calculate_risk(hr, temp, spo2, sys, dia)

    if risk == "CRITICAL RISK":
        print("🚨 CRITICAL ALERT — immediate attention required!")

    record = {
        "heart_rate": hr,
        "temp":       temp,
        "spo2":       spo2,
        "sys":        sys,
        "dia":        dia,
        "risk":       risk,
        "timestamp":  datetime.now(timezone.utc),
    }

    # ── Step 6: Save to MongoDB (non-blocking on failure) ─────────────────────
    if collection is not None:
        try:
            collection.insert_one(record.copy())
        except Exception as e:
            print(f"⚠️ DB insert error: {e}")
    else:
        print("⚠️ Skipping DB insert — collection unavailable")

    return {
        "heart_rate": hr,
        "temp":       temp,
        "spo2":       spo2,
        "sys":        sys,
        "dia":        dia,
        "risk":       risk,
    }
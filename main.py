from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
from pymongo import MongoClient
from datetime import datetime, timezone

app = FastAPI()

# ─────────────────────────────────────────────
# CORS (allow frontend access)
# ─────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
# MongoDB
# ─────────────────────────────────────────────
MONGO_URI = "mongodb+srv://aistethoscope:qYjlS5RuXheG2Izt@cluster0.8i7grhm.mongodb.net/?appName=Cluster0"

try:
    client = MongoClient(MONGO_URI)
    db = client["ai_stethoscope"]
    collection = db["patients"]
    print("MongoDB connected")
except Exception as e:
    print("MongoDB connection failed:", e)
    collection = None

# ─────────────────────────────────────────────
# ThingSpeak Config
# ─────────────────────────────────────────────
CHANNEL_ID = "3307509"
READ_API_KEY = "PWRZ5YE4XVFELF9N"

# ─────────────────────────────────────────────
# Valid ranges
# ─────────────────────────────────────────────
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
            return False, f"{field}: {value} (expected {lo}–{hi})"
    return True, None

# ─────────────────────────────────────────────
# Risk Logic
# ─────────────────────────────────────────────
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

    if sys > 180 or dia > 120:   score += 3
    elif sys > 140 or dia > 90:  score += 2
    elif sys > 120 or dia > 80:  score += 1

    if score >= 8:
        return "CRITICAL RISK"
    elif score >= 5:
        return "HIGH RISK"
    elif score >= 2:
        return "MEDIUM RISK"
    else:
        return "LOW RISK"

# ─────────────────────────────────────────────
# Root route (for testing deployment)
# ─────────────────────────────────────────────
@app.get("/")
def home():
    return {"status": "Backend running successfully"}

# ─────────────────────────────────────────────
# Main API
# ─────────────────────────────────────────────
@app.get("/live")
def live():
    url = (
        f"https://api.thingspeak.com/channels/{CHANNEL_ID}"
        f"/feeds/last.json?api_key={READ_API_KEY}"
    )

    # ── Fetch from ThingSpeak ──
    try:
        res = requests.get(url, timeout=10)
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        return {
            "error": "ThingSpeak connection failed",
            "details": str(e)
        }

    # ── Parse data ──
    try:
        hr   = float(data.get("field1", 0))
        temp = float(data.get("field2", 0))
        spo2 = float(data.get("field3", 0))
        sys  = float(data.get("field4", 0))
        dia  = float(data.get("field5", 0))
    except Exception:
        return {"error": "Invalid sensor data format"}

    # ── Validate ──
    ok, err = validate_vitals(hr, temp, spo2, sys, dia)
    if not ok:
        return {"error": f"Invalid data: {err}"}

    # ── Risk ──
    risk = calculate_risk(hr, temp, spo2, sys, dia)

    if risk == "CRITICAL RISK":
        print("🚨 CRITICAL ALERT")

    # ── Save to DB (safe) ──
    record = {
        "heart_rate": hr,
        "temp": temp,
        "spo2": spo2,
        "sys": sys,
        "dia": dia,
        "risk": risk,
        "timestamp": datetime.now(timezone.utc)
    }

    if collection:
        try:
            collection.insert_one(record)
        except Exception as e:
            print("⚠️ Mongo insert failed:", e)

    # ── Return to frontend ──
    return {
        "heart_rate": hr,
        "temp": temp,
        "spo2": spo2,
        "sys": sys,
        "dia": dia,
        "risk": risk
    }
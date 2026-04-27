from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import requests
from pymongo import MongoClient
from datetime import datetime, timezone

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

MONGO_URI = "mongodb+srv://aistethoscope:qYjlS5RuXheG2Izt@cluster0.8i7grhm.mongodb.net/?appName=Cluster0"
client = MongoClient(MONGO_URI)
db = client["ai_stethoscope"]
collection = db["patients"]

CHANNEL_ID = "3307509"
READ_API_KEY = "PWRZ5YE4XVFELF9N"

# ── Physiologically valid ranges ──────────────────────────────────────────────
VALID_RANGES = {
    "heart_rate": (20, 300),     # bpm
    "temp":       (30.0, 45.0),  # °C  (86–113 °F)
    "spo2":       (50, 100),     # %
    "sys":        (50, 300),     # mmHg
    "dia":        (20, 200),     # mmHg
}

def validate_vitals(hr, temp, spo2, sys, dia):
    """Return (ok, error_message). Rejects readings outside plausible ranges."""
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

    if sys > 180 or dia > 120:   score += 3
    elif sys > 140 or dia > 90:  score += 2
    elif sys > 120 or dia > 80:  score += 1

    if score >= 8:   return "CRITICAL RISK"
    elif score >= 5: return "HIGH RISK"
    elif score >= 2: return "MEDIUM RISK"
    else:            return "LOW RISK"

@app.get("/live")
def live():
    url = (
        f"https://api.thingspeak.com/channels/{CHANNEL_ID}"
        f"/feeds/last.json?api_key={READ_API_KEY}"
    )

    try:
        res  = requests.get(url, timeout=10)
        res.raise_for_status()
        data = res.json()
    except Exception as e:
        return {"error": f"Failed to reach ThingSpeak: {e}"}

    # ── Parse fields ──────────────────────────────────────────────────────────
    try:
        hr   = float(data["field1"])
        temp = float(data["field2"])   
        spo2 = float(data["field3"])
        sys  = float(data["field4"])
        dia  = float(data["field5"])
    except (KeyError, TypeError, ValueError):
        return {"error": "Missing or non-numeric sensor fields"}

    # ── Validate ranges — reject junk readings ────────────────────────────────
    ok, err = validate_vitals(hr, temp, spo2, sys, dia)
    if not ok:
        return {"error": f"Sensor data out of range: {err}"}

    # ── Risk scoring (uses °C internally — thresholds are in °C) ─────────────
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

    collection.insert_one(record.copy())

    return {
        "heart_rate": hr,
        "temp":       temp,
        "spo2":       spo2,
        "sys":        sys,
        "dia":        dia,
        "risk":       risk,
    }
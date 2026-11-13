try:
    from fastapi import FastAPI, HTTPException  # type: ignore[import-not-found]
    from fastapi.middleware.cors import CORSMiddleware  # type: ignore[import-not-found]
    from fastapi.staticfiles import StaticFiles  # type: ignore[import-not-found]
    from fastapi.responses import FileResponse  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - fallback for environments without FastAPI installed
    class HTTPException(Exception):
        def __init__(self, status_code: int, detail: str = None):
            super().__init__(detail or "")
            self.status_code = status_code
            self.detail = detail

    class CORSMiddleware:
        def __init__(self, app=None, **kwargs):
            self.app = app

    class StaticFiles:
        def __init__(self, directory=None, **kwargs):
            self.directory = directory

    class FileResponse:
        def __init__(self, path, **kwargs):
            self.path = path

    class FastAPI:
        def __init__(self, *_args, **_kwargs):
            pass

        def add_middleware(self, *_args, **_kwargs):
            return None

        def post(self, *_args, **_kwargs):
            def decorator(func):
                return func
            return decorator

        def get(self, *_args, **_kwargs):
            def decorator(func):
                return func
            return decorator

        def mount(self, *_args, **_kwargs):
            return None

try:
    from pydantic import BaseModel
except ImportError:  # pragma: no cover - fallback for environments without pydantic installed
    class BaseModel:
        pass

from typing import Optional
from datetime import datetime, timezone
from pathlib import Path
import csv
import math
import random

app = FastAPI(title="NASA Astrovita Live Telemetry Backend", version="2.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ==========================================================
# 1. AUTO-DETECT & PARSE DOWNLOADED NASA OSDR CSV FILES
# ==========================================================
serum_rows = []
urine_rows = []

for file_path in sorted(Path(__file__).parent.glob("*.csv")):
    with open(file_path, mode="r", encoding="utf-8-sig") as f:
        reader = list(csv.DictReader(f))
        if not reader:
            continue
        headers = list(reader[0].keys())
        if "glucose_value_milligram_per_deciliter" in headers and not serum_rows:
            serum_rows = reader
            print(f"[OK] Loaded Serum CMP Dataset: {file_path.name} ({len(serum_rows)} rows)")
        elif "il6_concentration_npq" in headers and not urine_rows:
            urine_rows = reader
            print(f"[OK] Loaded Urine Cytokine Dataset: {file_path.name} ({len(urine_rows)} rows)")

if not serum_rows:
    print("[WARN] Serum CSV not found - using clinical fallback ranges")
if not urine_rows:
    print("[WARN] Urine CSV not found - using clinical fallback ranges")


def get_csv_stats(rows, col_name, crew_prefix_list, fallback_min, fallback_max):
    """Calculates real min, max, and mean directly from NASA Inspiration4 CSV rows."""
    vals = []
    for r in rows:
        sample = r.get("Sample Name", "")
        if any(sample.startswith(c) for c in crew_prefix_list):
            raw = r.get(col_name, "")
            try:
                v = float(raw)
                if math.isfinite(v) and v > 0:
                    vals.append(v)
            except (ValueError, TypeError):
                continue
    if not vals:
        return fallback_min, fallback_max, round((fallback_min + fallback_max) / 2, 2)
    return round(min(vals), 2), round(max(vals), 2), round(sum(vals) / len(vals), 2)


def clamp(v, lo, hi, margin=0.04):
    """Keeps simulated nominal readings cleanly inside ideal bounds."""
    span = hi - lo
    return min(max(v, lo + span * margin), hi - span * margin)


MALE_CREW = ["C001", "C004"]
FEMALE_CREW = ["C002", "C003"]

BASELINES = {
    "male": {
        "glucose": get_csv_stats(serum_rows, "glucose_value_milligram_per_deciliter", MALE_CREW, 65.0, 99.0),
        "calcium": get_csv_stats(serum_rows, "calcium_value_milligram_per_deciliter", MALE_CREW, 8.6, 10.3),
        "creatinine": get_csv_stats(serum_rows, "creatinine_value_milligram_per_deciliter", MALE_CREW, 0.6, 1.35),
        "ast_liver": get_csv_stats(serum_rows, "ast_value_units_per_liter", MALE_CREW, 10.0, 40.0),
        "il6_inflam": get_csv_stats(urine_rows, "il6_concentration_npq", MALE_CREW, 7.0, 12.0),
    },
    "female": {
        "glucose": get_csv_stats(serum_rows, "glucose_value_milligram_per_deciliter", FEMALE_CREW, 65.0, 99.0),
        "calcium": get_csv_stats(serum_rows, "calcium_value_milligram_per_deciliter", FEMALE_CREW, 8.6, 10.4),
        "creatinine": get_csv_stats(serum_rows, "creatinine_value_milligram_per_deciliter", FEMALE_CREW, 0.5, 1.05),
        "ast_liver": get_csv_stats(serum_rows, "ast_value_units_per_liter", FEMALE_CREW, 10.0, 35.0),
        "il6_inflam": get_csv_stats(urine_rows, "il6_concentration_npq", FEMALE_CREW, 7.0, 12.0),
    },
}

# ==========================================================
# 2. NASA HRP EDUCATIONAL GUIDANCE (5 SPACEFLIGHT HAZARDS)
# ==========================================================
ALL_ORGANS = ["heart", "immune", "bones", "pancreas", "kidneys", "liver", "brain"]

ORGAN_GUIDE = {
    "heart": {
        "nasa_hazard": "Gravity Fields (Microgravity Cephalad Fluid Shift) & Space Radiation",
        "why_needed": "Microgravity shifts ~2L of fluid toward the upper body, shrinking plasma volume and increasing arrhythmia and orthostatic intolerance risk.",
        "how_to_measure": "Wearable ECG chest strap / pulse oximeter + automated upper-arm blood pressure cuff after 5 minutes of seated rest.",
        "flight_surgeon_escalation": "Escalate immediately if resting HR > 115 bpm, BP > 140/90 mmHg, or chest tightness/palpitations occur.",
    },
    "immune": {
        "nasa_hazard": "Space Radiation, Isolation & Hostile Closed Environment",
        "why_needed": "Spaceflight stress and radiation dysregulate T-cells, reactivate latent herpesviruses, and elevate inflammatory cytokines (IL-6).",
        "how_to_measure": "Onboard multiplex urine/saliva lateral-flow cartridge reader (15-min optical scan) + oral thermometer.",
        "flight_surgeon_escalation": "Escalate if Urine IL-6 > 16.0 NPQ accompanied by fever (> 38.0°C) or persistent skin rash.",
    },
    "bones": {
        "nasa_hazard": "Gravity Fields (Mechanical Unloading of Skeleton)",
        "why_needed": "Astronauts lose 1.0–1.5% bone mineral density per month in microgravity, releasing excess calcium into serum and urine.",
        "how_to_measure": "Fingerstick capillary sample analyzed in onboard i-STAT portable blood chemistry cartridge.",
        "flight_surgeon_escalation": "Escalate if Serum Calcium > 10.7 mg/dL or acute flank/back pain suggests a renal stone.",
    },
    "pancreas": {
        "nasa_hazard": "Inadequate Nutrition, Microgravity & Altered Energy Metabolism",
        "why_needed": "Reduced physical loading and circadian disruption trigger spaceflight-induced insulin resistance and glycemic spikes.",
        "how_to_measure": "Fasting capillary blood glucose test strip prior to 06:00 UTC morning meal.",
        "flight_surgeon_escalation": "Escalate if fasting blood glucose exceeds 126 mg/dL on two consecutive checks.",
    },
    "kidneys": {
        "nasa_hazard": "Gravity Fields (Fluid Shift Dehydration) & Hypercalciuria",
        "why_needed": "Blunted thirst drive and bone calcium resorption elevate serum creatinine and increase kidney stone formation risk.",
        "how_to_measure": "i-STAT Chem8+ blood cartridge + urine specific gravity and hydration color strip.",
        "flight_surgeon_escalation": "Escalate if Serum Creatinine rises > 0.3 mg/dL above pre-flight baseline or urine output drops.",
    },
    "liver": {
        "nasa_hazard": "Space Radiation & Closed-Loop Cabin Trace Contaminants",
        "why_needed": "Monitors hepatic oxidative stress from cosmic radiation, onboard medications, and closed-cabin volatile organic compounds.",
        "how_to_measure": "Portable dry-chemistry blood analyzer panel (AST/ALT hepatic enzymes).",
        "flight_surgeon_escalation": "Escalate if AST exceeds 55 U/L (or > 2x pre-flight baseline).",
    },
    "brain": {
        "nasa_hazard": "Isolation, Confinement & 90-Minute Orbital Light/Dark Cycles",
        "why_needed": "Circadian misalignment and confinement degrade sleep architecture, mood, and reaction speed during critical EVA/docking tasks.",
        "how_to_measure": "Wrist actigraphy sleep tracker + daily 3-minute Psychomotor Vigilance Task (PVT) and mood self-log.",
        "flight_surgeon_escalation": "Escalate if sleep < 5.0 hrs for 3 consecutive nights or mood rating drops <= 2/5.",
    },
}

ENVIRONMENT_CONFIG = {
    "ISS (LEO)": {
        "comms_delay": "Real-time (< 1 sec) — Direct Houston Flight Surgeon voice & telemetry link",
        "radiation_factor": 1.0,
    },
    "Lunar Surface Base": {
        "comms_delay": "2.6 sec round-trip — Near real-time telemetry with Lunar dust & 1/6g load",
        "radiation_factor": 1.4,
    },
    "Mars Transit": {
        "comms_delay": "14 min one-way — Autonomous crew diagnosis & treatment required before ground reply",
        "radiation_factor": 2.0,
    },
    "Mars Surface": {
        "comms_delay": "22 min one-way — Autonomous medical protocol required (3/8g gravity)",
        "radiation_factor": 1.8,
    },
}

# ==========================================================
# 3. ASTRONAUT DATABASE (PRE-SEEDED FOR INSTANT DEMO)
# ==========================================================
def create_astronaut_record(username: str, password: str, sex: str, sim_day: int = 1):
    return {
        "username": username,
        "password": password,  # Demo prototype only; hash passwords in production
        "sex": sex,
        "mission_start_time": datetime.now(timezone.utc),
        "environment": "ISS (LEO)",
        "simulated_day": sim_day,
        "comms_delay": ENVIRONMENT_CONFIG["ISS (LEO)"]["comms_delay"],
        "readiness_xp": 100,
        "completed_checks": [],
        "anomalies": {k: False for k in ALL_ORGANS},
        "manual_overrides": {},
        "history": {k: [] for k in ALL_ORGANS},
    }


astronauts_db = {
    "artemis": create_astronaut_record("artemis", "123", "female", sim_day=45),
    "orion": create_astronaut_record("orion", "123", "male", sim_day=45),
}


# ==========================================================
# 4. PYDANTIC REQUEST SCHEMAS
# ==========================================================
class AuthRequest(BaseModel):
    username: str
    password: str
    sex: str = "female"


class ManualVitalInput(BaseModel):
    value: float
    secondary_value: Optional[float] = None  # e.g., Diastolic BP, Body Temp (°C), or Mood (1-5)
    notes: str = ""


class MissionContextInput(BaseModel):
    environment: str = "ISS (LEO)"
    simulated_mission_day: int = 1


# ==========================================================
# 5. AUTHENTICATION ENDPOINTS
# ==========================================================
@app.post("/api/register")
def register_astronaut(user: AuthRequest):
    if user.username in astronauts_db:
        raise HTTPException(status_code=400, detail="Astronaut already exists")
    sex = user.sex.lower()
    if sex not in ["male", "female"]:
        raise HTTPException(status_code=400, detail="Sex must be 'male' or 'female'")

    astronauts_db[user.username] = create_astronaut_record(user.username, user.password, sex, sim_day=1)
    astro = astronauts_db[user.username]
    return {
        "message": "Mission clock locked & started!",
        "username": astro["username"],
        "sex": astro["sex"],
        "mission_start_time": astro["mission_start_time"].isoformat(),
        "datasets_loaded": {
            "serum_cmp_rows": len(serum_rows),
            "urine_cytokine_rows": len(urine_rows),
        },
    }


@app.post("/api/login")
def login_astronaut(user: AuthRequest):
    astro = astronauts_db.get(user.username)
    if not astro or astro["password"] != user.password:
        raise HTTPException(status_code=401, detail="Invalid credentials")
    return {
        "message": "Welcome aboard",
        "username": astro["username"],
        "sex": astro["sex"],
        "environment": astro["environment"],
        "simulated_day": astro["simulated_day"],
        "mission_start_time": astro["mission_start_time"].isoformat(),
    }


# ==========================================================
# 6. USER SELF-ASSESSMENT INPUT & MISSION CONTEXT ENDPOINTS
# ==========================================================
@app.post("/api/vitals/{username}/{organ}")
def submit_manual_vital(username: str, organ: str, payload: ManualVitalInput):
    """Allows the astronaut to type/input real measurements (HR, BP, Glucose, Temp, Sleep, Mood)."""
    if username not in astronauts_db:
        raise HTTPException(status_code=404, detail="Astronaut not found")
    organ = organ.lower()
    if organ not in ALL_ORGANS:
        raise HTTPException(status_code=400, detail=f"Unsupported organ. Choose from: {ALL_ORGANS}")

    astro = astronauts_db[username]
    astro["manual_overrides"][organ] = {
        "value": payload.value,
        "secondary_value": payload.secondary_value,
        "notes": payload.notes,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

    # Gamification reward for completing routine self-checks
    if organ not in astro["completed_checks"]:
        astro["completed_checks"].append(organ)
        astro["readiness_xp"] += 50
    else:
        astro["readiness_xp"] += 15

    return {
        "message": f"Logged manual {organ} measurement: {payload.value}",
        "organ": organ,
        "recorded_value": payload.value,
        "secondary_value": payload.secondary_value,
        "gamification": {
            "readiness_xp": astro["readiness_xp"],
            "completed_checks": astro["completed_checks"],
            "routine_progress": f"{len(astro['completed_checks'])}/{len(ALL_ORGANS)} organs verified",
        },
    }


@app.post("/api/mission-context/{username}")
def set_mission_context(username: str, ctx: MissionContextInput):
    """Switches environment (ISS, Lunar, Mars) & mission day for deep-space progression testing."""
    if username not in astronauts_db:
        raise HTTPException(status_code=404, detail="Astronaut not found")
    if ctx.environment not in ENVIRONMENT_CONFIG:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid environment. Choose from: {list(ENVIRONMENT_CONFIG.keys())}",
        )

    astro = astronauts_db[username]
    astro["environment"] = ctx.environment
    astro["simulated_day"] = max(1, ctx.simulated_mission_day)
    astro["comms_delay"] = ENVIRONMENT_CONFIG[ctx.environment]["comms_delay"]

    return {
        "message": "Mission environment updated",
        "username": username,
        "environment": astro["environment"],
        "simulated_day": astro["simulated_day"],
        "comms_delay": astro["comms_delay"],
    }


# ==========================================================
# 7. CORE ORGAN TELEMETRY ENGINE (SINGLE & FULL-BODY)
# ==========================================================
def evaluate_organ_telemetry(astro: dict, organ: str, record_history: bool = True):
    now = datetime.now(timezone.utc)
    elapsed = now - astro["mission_start_time"]
    total_seconds = int(elapsed.total_seconds())
    effective_days = astro["simulated_day"] + (elapsed.total_seconds() / 86400.0)
    sex = astro["sex"]
    sex_stats = BASELINES[sex]
    is_emergency = astro["anomalies"].get(organ, False)
    manual = astro["manual_overrides"].get(organ)

    secondary_metrics = {}

    if organ == "heart":
        if sex == "female":
            min_val, max_val = 65.0, round(85.0 + min(effective_days * 0.03, 7.0), 1)
            crit_min, crit_max = 45.0, 115.0
        else:
            min_val, max_val = 60.0, round(80.0 + min(effective_days * 0.03, 7.0), 1)
            crit_min, crit_max = 45.0, 110.0
        unit = "bpm"
        base = (min_val + max_val) / 2
        live_val = round(clamp(base + math.sin(total_seconds / 3) * 4 + random.uniform(-1.2, 1.2), min_val, max_val), 1)
        bp_sys, bp_dia = (116, 75) if sex == "female" else (120, 78)

        if is_emergency:
            live_val = round(crit_max + 18 + random.uniform(-2, 3), 1)
            bp_sys, bp_dia = 152, 96
        if manual:
            live_val = manual["value"]
            if manual["secondary_value"]:
                bp_dia = int(manual["secondary_value"])

        secondary_metrics = {
            "blood_pressure": f"{bp_sys}/{bp_dia} mmHg",
            "body_temperature": "36.8 °C",
        }
        extra_metric = f"Sex-Specific Baseline ({sex.capitalize()}) | Day {int(effective_days)} Microgravity Drift"
        yellow_alert = "CAUTION: Borderline heart rate elevation detected. Monitor hydration & exertion."
        red_alert = "CRITICAL: Tachycardia & microgravity fluid-shift cardiovascular stress detected!"
        advice_text = (
            "1. Drink 500mL electrolyte solution immediately. "
            "2. Assume supine recovery posture. "
            "3. Run 12-lead ECG & alert Flight Surgeon."
        )
        normal_advice = "Cardiovascular rhythm & blood pressure nominal. Continue daily 45-min ARED/cycle exercise."

    elif organ == "immune":
        _, _, csv_mean = sex_stats["il6_inflam"]
        min_val, max_val = 7.0, 12.0
        crit_min, crit_max = 2.0, 16.0
        unit = "NPQ (Urine IL-6)"
        live_val = round(clamp(csv_mean + math.sin(total_seconds / 5) * 0.6 + random.uniform(-0.2, 0.2), min_val, max_val), 2)
        body_temp = 36.7

        if is_emergency:
            live_val = 18.4
            body_temp = 38.4
        if manual:
            live_val = manual["value"]
            if manual["secondary_value"]:
                body_temp = manual["secondary_value"]

        secondary_metrics = {
            "body_temperature": f"{body_temp} °C",
            "viral_reactivation_risk": "Elevated" if live_val > max_val else "Low",
        }
        extra_metric = f"NASA OSD-656 {sex.capitalize()} Mean Urine IL-6: {csv_mean} NPQ"
        yellow_alert = "CAUTION: Mild inflammatory cytokine elevation (IL-6). Check body temperature."
        red_alert = "CRITICAL: Acute immune inflammation & febrile response detected in closed cabin!"
        advice_text = (
            "1. Log oral temperature every 2 hours & isolate if febrile (>38.0°C). "
            "2. Run viral PCR swab & HEPA cabin air check. "
            "3. Increase hydration & antioxidant intake."
        )
        normal_advice = "Urine IL-6 cytokine & core temperature nominal. Immune defense stable."

    elif organ == "pancreas":
        _, _, csv_mean = sex_stats["glucose"]
        min_val, max_val = 65.0, 99.0
        crit_min, crit_max = 55.0, 126.0
        unit = "mg/dL Glucose"
        live_val = round(clamp(csv_mean + math.cos(total_seconds / 5) * 4 + random.uniform(-1.2, 1.2), min_val, max_val), 1)

        if is_emergency:
            live_val = 142.0
        if manual:
            live_val = manual["value"]

        secondary_metrics = {"glycemic_trend": "Elevated" if live_val > max_val else "Balanced"}
        extra_metric = f"Inspiration4 {sex.capitalize()} Mean Glucose: {csv_mean} mg/dL (LSDS-8)"
        yellow_alert = "CAUTION: Elevated post-prandial or stress blood glucose (100–126 mg/dL)."
        red_alert = "CRITICAL: Spaceflight-induced insulin resistance / hyperglycemia spike (>126 mg/dL)!"
        advice_text = (
            "1. Hydrate with 400mL water. "
            "2. Perform 20 mins moderate cycle ergometer exercise. "
            "3. Re-test capillary glucose in 30 mins."
        )
        normal_advice = "Blood glucose within Inspiration4 flight range (65–99 mg/dL)."

    elif organ == "bones":
        _, _, csv_mean = sex_stats["calcium"]
        min_val, max_val = (8.6, 10.4) if sex == "female" else (8.6, 10.3)
        crit_min, crit_max = 8.0, 10.7
        unit = "mg/dL Serum Calcium"
        live_val = round(clamp(csv_mean + math.sin(total_seconds / 6) * 0.18 + random.uniform(-0.04, 0.04), min_val, max_val), 2)

        if is_emergency:
            live_val = 10.95
        if manual:
            live_val = manual["value"]

        bmd_loss = round(min(effective_days * 0.04, 18.0), 1)
        secondary_metrics = {"estimated_bmd_change": f"-{bmd_loss}% since launch"}
        extra_metric = f"Inspiration4 {sex.capitalize()} Mean Calcium: {csv_mean} mg/dL (LSDS-8)"
        yellow_alert = "CAUTION: Borderline serum calcium elevation from microgravity bone unloading."
        red_alert = "ALERT: Hypercalcemia indicating accelerated microgravity bone resorption!"
        advice_text = (
            "1. Increase ARED resistive squat/deadlift load by 10%. "
            "2. Administer 800 IU Vitamin D3 + 500mL water to prevent renal calculi."
        )
        normal_advice = f"Serum calcium nominal ({min_val}-{max_val} mg/dL). Continue daily ARED + Vitamin D3 protocol."

    elif organ == "kidneys":
        _, _, csv_mean = sex_stats["creatinine"]
        min_val, max_val = (0.5, 1.05) if sex == "female" else (0.6, 1.35)
        crit_min, crit_max = 0.4, round(max_val + 0.25, 2)
        unit = "mg/dL Serum Creatinine"
        live_val = round(clamp(csv_mean + math.sin(total_seconds / 7) * 0.04 + random.uniform(-0.02, 0.02), min_val, max_val), 2)

        if is_emergency:
            live_val = round(crit_max + 0.22, 2)
        if manual:
            live_val = manual["value"]

        secondary_metrics = {"hydration_status": "Dehydrated" if live_val > max_val else "Optimal"}
        extra_metric = f"Inspiration4 {sex.capitalize()} Mean Creatinine: {csv_mean} mg/dL (LSDS-8)"
        yellow_alert = "CAUTION: Mild creatinine rise suggesting subclinical spaceflight dehydration."
        red_alert = "WARNING: Elevated Serum Creatinine & acute renal dehydration stress!"
        advice_text = (
            "1. Drink 750mL water + electrolytes over next 2 hours. "
            "2. Log urine volume & specific gravity."
        )
        normal_advice = f"Renal filtration nominal for {sex} astronaut ({min_val}-{max_val} mg/dL)."

    elif organ == "liver":
        _, _, csv_mean = sex_stats["ast_liver"]
        min_val, max_val = (10.0, 35.0) if sex == "female" else (10.0, 40.0)
        crit_min, crit_max = 5.0, 55.0
        unit = "U/L AST Enzyme"
        live_val = round(clamp(csv_mean + math.cos(total_seconds / 8) * 2.0 + random.uniform(-0.8, 0.8), min_val, max_val), 1)

        if is_emergency:
            live_val = 64.0
        if manual:
            live_val = manual["value"]

        secondary_metrics = {"cabin_voc_status": "Nominal (0.12 ppm)"}
        extra_metric = f"Inspiration4 {sex.capitalize()} Mean AST: {csv_mean} U/L (LSDS-8)"
        yellow_alert = "CAUTION: Borderline hepatic AST enzyme elevation."
        red_alert = "WARNING: Elevated hepatic AST enzyme stress detected!"
        advice_text = (
            "1. Review onboard medication/acetaminophen log. "
            "2. Increase antioxidant nutrition & hydration. "
            "3. Re-test hepatic panel in 24h."
        )
        normal_advice = f"Hepatic enzyme levels nominal for {sex} profile ({min_val}-{max_val} U/L)."

    elif organ == "brain":
        min_val, max_val = (7.0, 9.0) if sex == "female" else (6.5, 8.5)
        crit_min, crit_max = 5.0, 11.0
        unit = "hrs sleep/24h"
        base = (min_val + max_val) / 2
        live_val = round(base + math.sin(total_seconds / 10) * 0.25, 1)
        mood_score = 4.5

        if is_emergency:
            live_val = 4.2
            mood_score = 2.0
        if manual:
            live_val = manual["value"]
            if manual["secondary_value"]:
                mood_score = manual["secondary_value"]

        secondary_metrics = {
            "mood_behavioral_score": f"{mood_score}/5.0",
            "pvt_reaction_time": "340 ms (Delayed)" if live_val < min_val else "242 ms (Sharp)",
        }
        extra_metric = "NASA UTMB Standard Schedule: 06:00 Wake / 22:00 Lights-Out"
        yellow_alert = "CAUTION: Sub-optimal sleep duration (< ideal range). Cognitive fatigue risk rising."
        red_alert = "WARNING: Severe circadian disruption & sleep deficit from 90-min orbital light cycles!"
        advice_text = (
            "1. Switch cabin LEDs to warm red-spectrum mode. "
            "2. Avoid caffeine for 8 hours. "
            "3. Complete 15-min mindfulness & cognitive fatigue check."
        )
        normal_advice = "Circadian rhythm & behavioral mood aligned with NASA 16h/8h schedule."

    else:
        raise HTTPException(status_code=400, detail=f"Unsupported organ. Choose from: {ALL_ORGANS}")

    # 3-Tier Status Evaluation (GREEN / YELLOW / RED)
    if live_val < crit_min or live_val > crit_max:
        status = "RED"
        alert = red_alert
        advice = advice_text
    elif live_val < min_val or live_val > max_val:
        status = "YELLOW"
        alert = yellow_alert
        advice = advice_text
    else:
        status = "GREEN"
        alert = f"Nominal {organ} telemetry."
        advice = normal_advice

    if record_history:
        astro["history"][organ].append({"time": now.strftime("%H:%M:%S"), "value": live_val})
        astro["history"][organ] = astro["history"][organ][-20:]

    return {
        "organ": organ,
        "sex": sex,
        "status": status,
        "current_value": live_val,
        "unit": unit,
        "ideal_range": f"{min_val}-{max_val} {unit}",
        "secondary_metrics": secondary_metrics,
        "input_source": "Manual Astronaut Entry" if manual else ("Simulated Emergency" if is_emergency else "Live Biotelemetry"),
        "dataset_calibration": extra_metric,
        "alert_message": alert,
        "advice": advice,
        "spaceflight_guide": ORGAN_GUIDE[organ],
        "mission_context": {
            "environment": astro["environment"],
            "simulated_day": int(effective_days),
            "comms_delay": astro["comms_delay"],
            "live_mission_clock": {
                "days": elapsed.days,
                "hours": (total_seconds // 3600) % 24,
                "minutes": (total_seconds // 60) % 60,
                "seconds": total_seconds % 60,
            },
        },
        "gamification": {
            "readiness_xp": astro["readiness_xp"],
            "completed_checks": astro["completed_checks"],
            "routine_progress": f"{len(astro['completed_checks'])}/{len(ALL_ORGANS)} organs verified",
        },
        "graph_history": astro["history"][organ],
    }


@app.get("/api/live/{username}/{organ}")
def get_live_organ_data(username: str, organ: str):
    """Polled by the 3D UI bubble when an astronaut clicks an organ."""
    if username not in astronauts_db:
        raise HTTPException(status_code=404, detail="Astronaut not found. Register or use 'artemis' / 'orion'.")
    return evaluate_organ_telemetry(astronauts_db[username], organ.lower(), record_history=True)


@app.get("/api/dashboard/{username}")
def get_full_body_status(username: str):
    """Returns status colors & values for all 7 organs at once so the 3D body can glow GREEN/YELLOW/RED."""
    if username not in astronauts_db:
        raise HTTPException(status_code=404, detail="Astronaut not found.")
    astro = astronauts_db[username]
    organs_summary = {}
    for org in ALL_ORGANS:
        data = evaluate_organ_telemetry(astro, org, record_history=False)
        organs_summary[org] = {
            "status": data["status"],
            "current_value": data["current_value"],
            "unit": data["unit"],
            "ideal_range": data["ideal_range"],
            "alert_message": data["alert_message"],
        }
    return {
        "username": username,
        "sex": astro["sex"],
        "environment": astro["environment"],
        "simulated_day": astro["simulated_day"],
        "comms_delay": astro["comms_delay"],
        "gamification": {
            "readiness_xp": astro["readiness_xp"],
            "completed_checks": astro["completed_checks"],
            "routine_progress": f"{len(astro['completed_checks'])}/{len(ALL_ORGANS)} organs verified",
        },
        "organs": organs_summary,
    }


# ==========================================================
# 8. REAL INSPIRATION4 CREW DATA INSPECTOR
# ==========================================================
@app.get("/api/nasa-crew/{crew_id}")
def get_crew_timeline(crew_id: str):
    crew_id = crew_id.upper()
    s_matches = [r for r in serum_rows if r.get("Sample Name", "").startswith(crew_id)]
    if not s_matches:
        raise HTTPException(
            status_code=404,
            detail="Crew ID not found (try C001, C002, C003, C004) or CSV not in folder.",
        )
    return {
        "crew_id": crew_id,
        "sex": "Female" if crew_id in FEMALE_CREW else "Male",
        "records": [
            {
                "timepoint": r["Sample Name"].split("_")[-1],
                "glucose_mg_dL": r.get("glucose_value_milligram_per_deciliter"),
                "calcium_mg_dL": r.get("calcium_value_milligram_per_deciliter"),
                "creatinine_mg_dL": r.get("creatinine_value_milligram_per_deciliter"),
                "ast_U_L": r.get("ast_value_units_per_liter"),
            }
            for r in s_matches
        ],
    }


# ==========================================================
# 9. PITCH DEMO CONTROLS (EMERGENCY TOGGLE & RESET)
# ==========================================================
@app.post("/api/demo/trigger-emergency/{username}/{organ}")
def toggle_user_emergency(username: str, organ: str):
    if username not in astronauts_db:
        raise HTTPException(status_code=404, detail="Astronaut not found")
    organ = organ.lower()
    if organ not in ALL_ORGANS:
        raise HTTPException(status_code=404, detail="Organ not found")
    astro = astronauts_db[username]
    astro["anomalies"][organ] = not astro["anomalies"][organ]
    return {"username": username, "organ": organ, "emergency_active": astro["anomalies"][organ]}


@app.post("/api/demo/reset/{username}")
def reset_astronaut_telemetry(username: str):
    """Clears all simulated emergencies and manual overrides back to nominal state."""
    if username not in astronauts_db:
        raise HTTPException(status_code=404, detail="Astronaut not found")
    astro = astronauts_db[username]
    astro["anomalies"] = {k: False for k in ALL_ORGANS}
    astro["manual_overrides"] = {}
    return {"message": f"All telemetry and alerts reset to nominal for {username}"}


# ==========================================================
# 9. SERVE THE WEBSITE (same origin as the API, no CORS issues)
#    The modern app loads its CSS, JS, and public assets from these folders.
# ==========================================================
SITE_DIR = Path(__file__).parent
app.mount("/public", StaticFiles(directory=SITE_DIR / "public"), name="public")
app.mount("/css", StaticFiles(directory=SITE_DIR / "css"), name="css")
app.mount("/js", StaticFiles(directory=SITE_DIR / "js"), name="js")


@app.get("/classic", include_in_schema=False)
def classic_website():
    return FileResponse(SITE_DIR / "index.html")


@app.get("/", include_in_schema=False)
def website():
    return FileResponse(SITE_DIR / "index2.html")

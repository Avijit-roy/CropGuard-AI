# ============================================================
# FILE: backend/fusion_engine.py
# PURPOSE: Cross-references AI disease prediction with live
#          IoT sensor readings and API weather data to generate
#          a combined risk score and recommendations.
# ============================================================

import database as db
import json
import logging
import requests
import os
from datetime import datetime
from dataclasses import dataclass, asdict

log = logging.getLogger(__name__)

# ============================================================
# DATA CLASSES
# ============================================================

@dataclass
class SoilState:
    temperature:     float
    humidity:        float
    soil_moisture:   float
    soil_dry:        bool    = False
    soil_wet:        bool    = False
    risk_score:      int     = 0

@dataclass
class WeatherState:
    temp:            float   = 0.0
    humidity:        float   = 0.0
    rain_mm:         float   = 0.0
    precip_prob:     float   = 0.0
    pressure_hpa:    float   = 1013.0
    weather_code:    int     = 0

@dataclass
class FusionResult:
    disease:           str
    crop:              str
    confidence:        float
    alert_level:       str
    alert_emoji:       str
    disease_advice:    str
    soil_advice:       str
    combined_insight:  str
    immediate_actions: list
    treatment:         list
    prevention:        list
    fertiliser_fix:    str
    irrigation_fix:    str
    risk_score:        int
    timestamp:         str
    llm_insight:       dict = None # CAUSES, SUGGESTIONS, INFORMATION

# ============================================================
# KNOWLEDGE BASE
# ============================================================

CROP_SOIL_OPTIMAL = {
    "Tomato":     {"moisture": (50, 70), "temperature": (18, 27), "humidity": (55, 75)},
    "Potato":     {"moisture": (60, 75), "temperature": (15, 20), "humidity": (60, 80)},
    "Apple":      {"moisture": (40, 65), "temperature": (15, 24), "humidity": (50, 70)},
    "Pepper":     {"moisture": (50, 70), "temperature": (20, 30), "humidity": (55, 75)},
    "Strawberry": {"moisture": (50, 70), "temperature": (15, 22), "humidity": (55, 70)},
    "Default":    {"moisture": (40, 70), "temperature": (15, 30), "humidity": (50, 75)},
}

DISEASE_KB = {
    "tomato___late_blight": {
        "alert": "critical",
        "triggers": ["high_moisture", "high_humidity", "cool_temp", "rain_incoming"],
        "disease_advice": "Late blight spreads rapidly in wet, cool conditions. Immediate action required.",
        "treatment": [
            "Apply Mancozeb 75 WP at 2.5g/litre immediately",
            "Apply Metalaxyl + Mancozeb (Ridomil Gold) every 7 days",
            "Remove and destroy all infected plant material",
        ],
        "prevention": [
            "Avoid overhead irrigation — use drip irrigation",
            "Improve field drainage to reduce moisture",
            "Plant resistant varieties next season",
            "Maintain plant spacing for airflow",
        ],
    },
    "tomato___early_blight": {
        "alert": "high",
        "triggers": ["high_moisture", "high_humidity"],
        "disease_advice": "Early blight often indicates nutrient stress. Treat both disease and soil.",
        "treatment": [
            "Apply Chlorothalonil 75 WP at 2g/litre",
            "Spray copper oxychloride as protective fungicide",
            "Remove lower infected leaves immediately",
        ],
        "prevention": [
            "Improve soil nutrient balance",
            "Mulch around plants to prevent soil splash",
            "Avoid working in wet fields",
        ],
    },
    "tomato___bacterial_spot": {
        "alert": "high",
        "triggers": ["high_moisture", "high_humidity"],
        "disease_advice": "Bacterial spot thrives in humid conditions. Copper sprays most effective.",
        "treatment": [
            "Apply copper hydroxide spray every 5-7 days",
            "Use streptomycin spray in severe cases",
            "Avoid wetting foliage during irrigation",
        ],
        "prevention": [
            "Use disease-free seeds and transplants",
            "Maintain field hygiene — remove crop debris",
            "Reduce irrigation frequency",
        ],
    },
    "tomato___healthy": {
        "alert": "low",
        "triggers": [],
        "disease_advice": "Plant appears healthy. Focus on soil maintenance.",
        "treatment": ["No treatment required"],
        "prevention": [
            "Continue current management practices",
            "Monitor weekly for early disease signs",
            "Maintain balanced fertilisation schedule",
        ],
    },
    "potato___late_blight": {
        "alert": "critical",
        "triggers": ["high_moisture", "cool_temp", "high_humidity", "rain_incoming"],
        "disease_advice": "Potato late blight — act immediately.",
        "treatment": [
            "Apply Cymoxanil + Mancozeb immediately",
            "Spray Propamocarb every 5 days in wet weather",
            "Destroy infected tubers and plant debris",
        ],
        "prevention": [
            "Reduce soil moisture — late blight loves wet soil",
            "Hill soil around plants to protect tubers",
            "Use certified disease-free seed potatoes",
        ],
    },
    "potato___early_blight": {
        "alert": "medium",
        "triggers": ["high_moisture"],
        "disease_advice": "Early blight indicates nutritional stress.",
        "treatment": [
            "Apply Chlorothalonil at 2g/litre weekly",
            "Supplement with potassium-rich fertiliser",
        ],
        "prevention": [
            "Improve soil nitrogen balance",
            "Avoid excessive irrigation",
            "Maintain 3-year crop rotation",
        ],
    },
    "apple___apple_scab": {
        "alert": "high",
        "triggers": ["high_moisture", "cool_temp", "rain_incoming"],
        "disease_advice": "Apple scab spreads during wet spring weather.",
        "treatment": [
            "Apply Captan 50 WP at 2g/litre every 10 days",
            "Use Myclobutanil for curative action",
            "Prune infected twigs and branches",
        ],
        "prevention": [
            "Apply lime sulfur during dormant season",
            "Rake and destroy fallen leaves",
            "Improve orchard air circulation by pruning",
        ],
    },
    "apple___black_rot": {
        "alert": "high",
        "triggers": ["high_moisture", "high_temp"],
        "disease_advice": "Black rot affects fruits and branches. Remove infected wood promptly.",
        "treatment": [
            "Prune infected branches 15cm below visible infection",
            "Apply Thiophanate-methyl every 14 days",
            "Remove mummified fruits from tree and ground",
        ],
        "prevention": [
            "Maintain proper orchard sanitation",
            "Avoid bark injuries that allow entry",
            "Inspect trees monthly during growing season",
        ],
    },
    "pepper___bacterial_spot": {
        "alert": "high",
        "triggers": ["high_moisture", "high_humidity", "high_temp"],
        "disease_advice": "Bacterial spot in peppers spreads rapidly in warm, wet conditions.",
        "treatment": [
            "Apply fixed copper + mancozeb spray",
            "Use streptomycin if available",
            "Reduce irrigation — avoid leaf wetness",
        ],
        "prevention": [
            "Use resistant varieties for next crop",
            "Sanitize tools between plants",
            "Avoid working in fields when plants are wet",
        ],
    },
    "default": {
        "alert": "medium",
        "triggers": [],
        "disease_advice": "Disease detected. Follow standard crop protection protocol.",
        "treatment": [
            "Consult local agriculture extension officer",
            "Apply broad-spectrum fungicide as precaution",
            "Remove and destroy infected plant material",
        ],
        "prevention": [
            "Maintain field hygiene",
            "Monitor plants weekly",
            "Ensure balanced soil nutrition",
        ],
    },
}

# ============================================================
# LLM INSIGHT (Groq Integration)
# ============================================================

def get_llm_insight(disease, crop, confidence, soil: SoilState, weather: WeatherState):
    """Calls Groq API to get expert agronomy insights."""
    api_key = os.environ.get("GROQ_API_KEY")
    model = os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile")
    
    if not api_key:
        return None

    prompt = f"""
    You are an expert plant pathologist and agronomist. 
    Analyze the following data and provide detailed insights.
    
    DATA:
    - Crop: {crop}
    - Detected Condition/Disease: {disease}
    - AI Confidence: {confidence:.1f}%
    - 7-Day Avg Temperature: {soil.temperature:.1f}°C
    - 7-Day Avg Humidity: {soil.humidity:.1f}%
    - 7-Day Avg Soil Moisture: {soil.soil_moisture:.1f}%
    - Current Weather: {weather.temp}°C, {weather.precip_prob}% rain probability
    
    Your response MUST be a JSON object with exactly three keys:
    1. "causes": A string explaining why this disease occurred given the environment.
    2. "suggestions": A string providing specific, scientific suggestions for treatment and recovery.
    3. "information": A string providing interesting botanical or pathological facts about this specific disease/crop interaction.
    
    Keep explanations professional, concise, and actionable.
    """

    try:
        url = "https://api.groq.com/openai/v1/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        payload = {
            "messages": [{"role": "user", "content": prompt}],
            "model": model,
            "response_format": {"type": "json_object"}
        }
        
        res = requests.post(url, headers=headers, json=payload, timeout=10)
        if res.ok:
            content = res.json()['choices'][0]['message']['content']
            return json.loads(content)
    except Exception as e:
        log.error(f"Groq API Error: {e}")
    
    return {
        "causes": "Environmental data analysis suggests high stress conditions favoring pathogen activity.",
        "suggestions": "Maintain optimal irrigation and apply recommended fungicides. Monitor humidity levels.",
        "information": f"{disease} is a common challenge for {crop} growers, often influenced by micro-climate variations."
    }

# ============================================================
# CONDITION CLASSIFIER
# ============================================================

def classify_conditions(soil: SoilState, weather: WeatherState, crop: str) -> list:
    conditions = []
    optimal    = CROP_SOIL_OPTIMAL.get(crop, CROP_SOIL_OPTIMAL["Default"])

    m_low, m_high = optimal["moisture"]
    if soil.soil_moisture > m_high + 10 or soil.soil_wet:
        conditions.append("high_moisture")
    elif soil.soil_moisture < m_low - 10 or soil.soil_dry:
        conditions.append("low_moisture")

    t_low, t_high = optimal["temperature"]
    if soil.temperature > t_high + 5:
        conditions.append("high_temp")
    elif soil.temperature < t_low - 3:
        conditions.append("cool_temp")

    h_low, h_high = optimal["humidity"]
    if soil.humidity > h_high + 5:
        conditions.append("high_humidity")

    if weather.rain_mm > 0.5 or weather.precip_prob > 60:
        conditions.append("rain_incoming")
    
    if weather.pressure_hpa < 1005:
        conditions.append("low_pressure")

    return conditions

# ============================================================
# RECOMMENDATIONS
# ============================================================

def generate_recommendations(soil: SoilState, weather: WeatherState, crop: str, conditions: list) -> tuple:
    fertiliser_parts = ["Maintain regular balanced NPK schedule."]
    irrigation_parts = []
    optimal       = CROP_SOIL_OPTIMAL.get(crop, CROP_SOIL_OPTIMAL["Default"])
    m_low, m_high = optimal["moisture"]

    if "high_moisture" in conditions or soil.soil_wet:
        irrigation_parts.append(f"Soil is wet ({soil.soil_moisture:.0f}%). Stop irrigation. Target: {m_low}-{m_high}%.")
    elif "rain_incoming" in conditions:
        irrigation_parts.append(f"Rain expected ({weather.precip_prob:.0f}% prob). Hold irrigation and check soil after rain.")
    elif "low_moisture" in conditions or soil.soil_dry:
        irrigation_parts.append(f"Soil is dry ({soil.soil_moisture:.0f}%). Increase irrigation. Target: {m_low}-{m_high}%.")
    else:
        irrigation_parts.append(f"Soil moisture normal ({soil.soil_moisture:.0f}%). Maintain schedule.")

    return (" ".join(fertiliser_parts), " ".join(irrigation_parts))

# ============================================================
# RISK SCORE CALCULATOR
# ============================================================

def calculate_risk_score(disease_confidence: float, alert_level: str, conditions: list, soil: SoilState, disease_triggers: list) -> int:
    base = disease_confidence * 0.4
    alert_scores = {"critical": 35, "high": 25, "medium": 15, "low": 5, "healthy": 0}
    base += alert_scores.get(alert_level, 10)
    matching = set(conditions) & set(disease_triggers)
    base += len(matching) * 8
    if soil.risk_score > 70: base += 10
    return min(100, int(base))

# ============================================================
# MAIN FUSION FUNCTION
# ============================================================

def fuse(
    disease_label: str,
    crop:          str,
    confidence:    float,
    soil:          SoilState,
    weather:       WeatherState = None,
) -> FusionResult:

    if weather is None:
        weather = WeatherState()

    key = disease_label.lower().replace(" ", "_")
    kb  = DISEASE_KB.get(key, DISEASE_KB["default"])

    conditions        = classify_conditions(soil, weather, crop)
    matching_triggers = set(conditions) & set(kb["triggers"])

    if matching_triggers:
        combined = (f"⚠️ ALERT: Sensor & Weather data actively favor this disease! Detected: {', '.join(matching_triggers).replace('_', ' ')}. Address both disease and environment immediately.")
    elif not kb["triggers"]:
        combined = "Sensor readings are within range. Focus on preventive maintenance."
    else:
        combined = ("Environment is not currently favoring this disease. Continue monitoring and treat existing symptoms.")

    fertiliser_fix, irrigation_fix = generate_recommendations(soil, weather, crop, conditions)

    immediate = []
    if kb["alert"] in ("critical", "high"):
        immediate.append("Remove and destroy infected plant parts immediately.")
    if "high_moisture" in conditions or soil.soil_wet:
        immediate.append("Stop all irrigation — soil is oversaturated.")
    if "low_moisture" in conditions or soil.soil_dry:
        immediate.append("Irrigate immediately — soil is critically dry.")
    if "rain_incoming" in conditions:
        immediate.append("Rain expected — apply fungicide BEFORE it starts to rain.")
    
    if not immediate:
        immediate.append("No urgent environmental actions — monitor daily.")

    emoji_map = {"critical": "🚨", "high": "⚠️", "medium": "🟡", "low": "🟢", "healthy": "✅"}

    risk_score = calculate_risk_score(confidence, kb["alert"], conditions, soil, kb["triggers"])

    # FETCH LLM INSIGHT
    llm_insight = get_llm_insight(disease_label, crop, confidence, soil, weather)

    result = FusionResult(
        disease           = disease_label,
        crop              = crop,
        confidence        = confidence,
        alert_level       = kb["alert"],
        alert_emoji       = emoji_map.get(kb["alert"], "ℹ️"),
        disease_advice    = kb["disease_advice"],
        soil_advice       = (f"7d Avg Temp: {soil.temperature:.1f}°C | Hum: {soil.humidity:.1f}% | Soil: {soil.soil_moisture:.0f}% | Weather: {weather.temp}°C, {weather.precip_prob}% rain"),
        combined_insight  = combined,
        immediate_actions = immediate,
        treatment         = kb["treatment"],
        prevention        = kb["prevention"],
        fertiliser_fix    = fertiliser_fix,
        irrigation_fix    = irrigation_fix,
        risk_score        = risk_score,
        timestamp         = datetime.utcnow().isoformat() + "Z",
        llm_insight       = llm_insight
    )

    _save_recommendation(result)
    return result

def _save_recommendation(result: FusionResult):
    try:
        db.save_recommendation({
            "device_id": "cropguard_01",
            "disease": result.disease,
            "soil_score": result.risk_score,
            "alert_level": result.alert_level,
            "recommendation": json.dumps(result.treatment),
            "soil_fix": result.irrigation_fix,
            "timestamp": result.timestamp
        })
    except Exception as e:
        log.error(f"Failed to save recommendation: {e}")
